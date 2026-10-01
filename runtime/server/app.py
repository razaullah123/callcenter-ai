"""Voice agent server: WebSocket call endpoint (IVR + browser playground).

    python -m runtime.server            # http://localhost:8080  (playground)  ·  ws://localhost:8080/ws

WebSocket protocol (JSON text frames, one call per connection):

  client → agent
    {"event": "start", "call_id": "...", "ani": "+9665...", "agent": "<agent id, optional>",
     "audio": {"encoding": "pcm16"|"mulaw", "sample_rate": 16000|8000}}
    {"event": "media", "payload": "<base64 audio, 20 ms frames recommended>"}
    {"event": "dtmf",  "digit": "1"}
    {"event": "stop"}

  agent → client
    {"event": "ready", "call_id": "..."}
    {"event": "media", "payload": "<base64 audio, same format as the caller's unless start.output_audio is given>"}
    {"event": "clear"}                        # barge-in: drop any buffered agent audio immediately
    {"event": "transfer", "reason": "..."}    # hand the call to a human agent
    {"event": "hangup"}
    {"event": "transcript", "role": "user"|"agent", "text": "..."}   # informational (IVR may ignore)
"""

import asyncio
import base64
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from runtime.app import Runtime
from runtime.platform.sync import WORKER_ID
from runtime.voice.call import VoiceCall
from runtime.voice.player import AudioFormat

from . import CLOSED_ERRORS

log = logging.getLogger("runtime.server")
STATIC = Path(__file__).parent / "static"
state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    from runtime.config import get_settings
    from runtime.control.maintenance import maintenance_loop, production_problems
    s = get_settings()
    if problems := production_problems(s):
        for p in problems:
            log.error("UNSAFE FOR PRODUCTION: %s", p)
        if not s.allow_insecure_live:
            raise RuntimeError("refusing to start in TOOLS_MODE=live: " + "; ".join(problems) +
                               " (set ALLOW_INSECURE_LIVE=true only for a supervised test)")
    rt = await Runtime.create()                          # loads the default agent (phrases pre-synthesized)
    state.update(rt=rt, phrases=None, loop_lag_ms=0.0)   # calls take phrases from their agent
    lag_task = asyncio.create_task(_measure_loop_lag())
    maint_task = asyncio.create_task(maintenance_loop(s))
    log.info("voice agent ready (tools_mode=%s)", rt.settings.tools_mode)
    yield
    lag_task.cancel()
    maint_task.cancel()
    from runtime.control.audit import AUDIT
    await AUDIT.flush()
    await rt.close()


async def _measure_loop_lag() -> None:
    """Event-loop lag (ms) over the last second — the first sign of CPU saturation with many calls."""
    import time
    worst = 0.0
    last_reset = time.perf_counter()
    while True:
        t0 = time.perf_counter()
        await asyncio.sleep(0.05)
        worst = max(worst, (time.perf_counter() - t0 - 0.05) * 1000)
        if time.perf_counter() - last_reset > 1:
            state["loop_lag_ms"], worst, last_reset = round(worst, 1), 0.0, time.perf_counter()


app = FastAPI(title="HMG Voice Agent", lifespan=lifespan)

from runtime.control import api as control_api  # noqa: E402 — console API /api
from runtime.control import connections as control_connections  # noqa: E402 — /api/connections, /api/secrets
from runtime.control import tools_api as control_tools  # noqa: E402 — /api/tool-library, /api/mcp-servers
from runtime.control import agents_api as control_agents  # noqa: E402 — /api/agents, /api/routes (Agent Studio)
from runtime.server import chat as chat_ws  # noqa: E402 — text test channel /ws/chat (Agent Studio)
from runtime.server import ivr  # noqa: E402 — IVR endpoint /ws/voice-pipeline

app.include_router(ivr.router)
app.include_router(chat_ws.router)
app.include_router(control_api.router)
app.include_router(control_connections.router)
app.include_router(control_tools.router)
app.include_router(control_agents.router)

# Console (React build in console/dist, served at /console with SPA fallback)
from fastapi.responses import FileResponse, RedirectResponse  # noqa: E402

CONSOLE_DIST = Path(__file__).resolve().parents[2] / "console" / "dist"


@app.get("/console", include_in_schema=False)
async def console_root():
    return RedirectResponse("/console/")


@app.get("/console/{path:path}", include_in_schema=False)
async def console(path: str):
    if not CONSOLE_DIST.exists():
        return HTMLResponse("Console not built — run: cd console && npm install && npm run build", status_code=503)
    target = (CONSOLE_DIST / path).resolve()
    if path and target.is_file() and CONSOLE_DIST in target.parents:
        return FileResponse(target)
    return FileResponse(CONSOLE_DIST / "index.html")


@app.get("/")
async def playground() -> HTMLResponse:
    return HTMLResponse((STATIC / "playground.html").read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict:
    rt: Runtime | None = state.get("rt")
    return {"ok": rt is not None, "tools_mode": rt.settings.tools_mode if rt else None,
            "active_calls": len(rt.live.active) if rt and rt.live else None,
            "loop_lag_ms": state.get("loop_lag_ms"),
            "config_version": rt.providers.version if rt else None,
            "agent": {"id": rt.agent.agent_id, "release": rt.agent.version} if rt else None,
            "worker": WORKER_ID, "cluster_sync": bool(rt and rt.sync and rt.sync.connected.is_set())}


def _fmt(spec: dict | None, default: AudioFormat) -> AudioFormat:
    if not spec:
        return default
    enc = spec.get("encoding", default.encoding)
    if enc not in ("pcm16", "mulaw"):
        raise ValueError(f"unsupported encoding {enc!r}")
    return AudioFormat(enc, int(spec.get("sample_rate", 8000 if enc == "mulaw" else 16000)))


@app.websocket("/ws")
async def call_ws(ws: WebSocket) -> None:
    await ws.accept()
    rt: Runtime = state["rt"]
    send_lock = asyncio.Lock()

    async def send_event(msg: dict) -> None:
        async with send_lock:
            try:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            except CLOSED_ERRORS:
                pass        # the caller hung up while a reply / transcript was on its way

    async def send_audio(data: bytes) -> None:
        await send_event({"event": "media", "payload": base64.b64encode(data).decode()})

    call: VoiceCall | None = None
    try:
        first = json.loads(await ws.receive_text())
        if first.get("event") != "start":
            await ws.close(code=1008, reason="first message must be start")
            return
        in_fmt = _fmt(first.get("audio"), AudioFormat())
        out_fmt = _fmt(first.get("output_audio"), in_fmt)
        call_id = first.get("call_id") or f"call-{uuid.uuid4().hex[:12]}"
        agent = None
        if hasattr(rt, "agent_for_call"):        # start.agent picks one explicitly (playground / console test call)
            try:
                agent = await rt.agent_for_call(agent_id=first.get("agent") or None, number=first.get("ani"),
                                                draft=bool(first.get("draft")))
            except LookupError as e:
                await send_event({"event": "error", "message": str(e)})
                await ws.close(code=1008, reason="unknown agent")
                return
        call = VoiceCall(rt, call_id=call_id, in_fmt=in_fmt, out_fmt=out_fmt, send_audio=send_audio,
                         send_event=send_event, ani=first.get("ani"), phrases=state.get("phrases"), agent=agent)
        rt.calls[call_id] = call

        async def close_transport() -> None:
            try:
                await ws.close(code=1000, reason="ended from console")
            except Exception:
                pass
        call.close_transport = close_transport
        await send_event({"event": "ready", "call_id": call_id})
        await call.start()
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("event")
            if kind == "media":
                await call.on_audio(base64.b64decode(msg["payload"]))
            elif kind == "dtmf":
                await call.on_dtmf(str(msg.get("digit", "")))
            elif kind == "stop":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.exception("call failed: %r", e)
    finally:
        if call:
            rt.calls.pop(call.session.call_id, None)
            await call.stop()
        try:
            await ws.close()
        except Exception:
            pass        # already closed by the browser
        # uvicorn prints "connection open" but nothing when the browser closes first — say it ourselves
        log.info("call %s ended (%s) — connection closed", call.session.call_id if call else "-",
                 call.end_reason if call else "no call started")
