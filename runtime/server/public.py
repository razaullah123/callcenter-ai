"""Public side of Publishing: a page anyone can open and talk to (no sign-in), the embed script that puts it on a website, and
the voice socket behind it. Everything here is reached with the link's token; the token only ever starts that agent's
*published release*, with the limits the owner set (call length, calls at once, calls per visitor per hour, sites that may
embed it).

    GET /p/{token}               the page (token "preview": the page for the console's live preview)
    GET /embed.js                the widget script (<script src=".../embed.js" data-token="…">)
    GET /public/{token}/config   what the page and the widget may know (appearance, embed options)
    WS  /ws/public/{token}       the call (the same audio protocol as /ws)
"""

import asyncio
import base64
import json
import logging
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response

from runtime.events import EventType
from runtime.platform.share import LIMITER, REFUSALS, ShareSettings, public_config
from runtime.voice.call import VoiceCall
from runtime.voice.player import AudioFormat

from . import CLOSED_ERRORS
from .public_page import EMBED_JS, NOT_FOUND_HTML, PAGE_HTML

log = logging.getLogger("runtime.server")
router = APIRouter()


def _state() -> dict:
    from runtime.server.app import state
    return state


async def _share(token: str) -> dict | None:
    store = getattr(_state().get("rt"), "platform", None)
    return await store.share_by_token(token) if store is not None else None


def same_site(ws: WebSocket) -> bool:
    """A browser tells which page opened the socket (Origin): only this server's own pages (the public page, also when it is
    framed by another site) may — another site can't open the socket from its own page and skip the embed rules."""
    origin = ws.headers.get("origin")
    return not origin or urlparse(origin).netloc.lower() == (ws.headers.get("host") or "").lower()


def client_ip(ws: WebSocket) -> str:
    rt = _state().get("rt")
    if getattr(getattr(rt, "settings", None), "public_trust_proxy", False):
        if forwarded := ws.headers.get("x-forwarded-for"):
            return forwarded.split(",")[0].strip()
    return ws.client.host if ws.client else ""


@router.get("/p/{token}", include_in_schema=False)
async def page(token: str) -> Response:
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
    if token == "preview":                                        # the console's live preview: framed by the console only
        headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    else:
        row = await _share(token)
        if row is None:
            return HTMLResponse(NOT_FOUND_HTML, status_code=404, headers=headers)
        origins = ShareSettings.model_validate(row.get("settings") or {}).limits.allowed_origins
        if origins:                                               # only these sites may embed the page
            headers["Content-Security-Policy"] = "frame-ancestors 'self' " + " ".join(origins)
    return HTMLResponse(PAGE_HTML.replace("__TOKEN__", token), headers=headers)


@router.get("/embed.js", include_in_schema=False)
async def embed_script() -> Response:
    return Response(EMBED_JS, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"})


@router.get("/public/{token}/config", include_in_schema=False)
async def config(token: str) -> Response:
    row = await _share(token)
    cors = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}
    if row is None:
        return JSONResponse({"detail": "this link is no longer available"}, status_code=404, headers=cors)
    agent = await _state()["rt"].platform.agent(row["agent_id"]) or {}
    return JSONResponse(public_config(row.get("settings") or {}, agent), headers=cors)


@router.websocket("/ws/public/{token}")
async def public_call(ws: WebSocket, token: str) -> None:
    await ws.accept()
    rt = _state()["rt"]
    send_lock = asyncio.Lock()

    async def send_event(msg: dict) -> None:
        async with send_lock:
            try:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            except CLOSED_ERRORS:
                pass                                              # the visitor left while a line was on its way

    async def refuse(message: str, code: int = 1008) -> None:
        await send_event({"event": "error", "message": message})
        try:
            await ws.close(code=code, reason=message[:100])
        except Exception:                                         # noqa: BLE001
            pass

    if not same_site(ws):
        return await refuse("This page can't open a call from here.")
    row = await _share(token)
    if row is None:
        return await refuse("This link is no longer available.")
    settings = ShareSettings.model_validate(row.get("settings") or {})
    if reason := LIMITER.admit(token, client_ip(ws), settings.limits):
        return await refuse(REFUSALS[reason], 1013)

    call: VoiceCall | None = None
    handle: int | None = None
    starter: asyncio.Task | None = None
    try:
        try:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), 15))
        except (asyncio.TimeoutError, ValueError):
            return await refuse("The call did not start.")
        if first.get("event") != "start":
            return await refuse("The call did not start.")
        from runtime.server.app import _fmt
        in_fmt = _fmt(first.get("audio"), AudioFormat())
        out_fmt = _fmt(first.get("output_audio"), in_fmt)
        raw = first.get("params") if isinstance(first.get("params"), dict) else {}
        params = {k: str(v)[:200] for k, v in raw.items() if k in settings.limits.allowed_params}   # only what the owner allowed
        try:
            loaded = await rt.agent_for_call(agent_id=row["agent_id"])        # the published release, never the draft
        except Exception as e:                                    # noqa: BLE001
            log.warning("public link %s: agent not available: %r", token[:6], e)
            return await refuse("This agent isn't available right now.", 1011)

        async def send_audio(data: bytes) -> None:
            await send_event({"event": "media", "payload": base64.b64encode(data).decode()})

        call_id = f"pub-{uuid.uuid4().hex[:12]}"
        try:
            call = VoiceCall(rt, call_id=call_id, in_fmt=in_fmt, out_fmt=out_fmt, send_audio=send_audio, send_event=send_event,
                             phrases=_state().get("phrases"), agent=loaded, params=params)
        except ValueError as e:
            return await refuse(str(e))
        cap = settings.limits.max_minutes * 60                    # a visitor's call never outlasts the link's limit
        call.max_call_s = min(call.max_call_s, cap) if call.max_call_s else cap
        from runtime.tools.web import clean_names
        if names := clean_names(first.get("web_tools")):       # tools the embedding page registered (the dashboard defines them)
            call.attach_web_tools(names)
        handle = LIMITER.join(token, call)
        rt.calls[call_id] = call

        async def close_transport() -> None:
            try:
                await ws.close(code=1000, reason="ended")
            except Exception:                                     # noqa: BLE001
                pass
        call.close_transport = close_transport
        rt.bus.bind(call_id=call_id).emit(EventType.SLOT_SET, field="public_page", agent=row["agent_id"])
        await send_event({"event": "ready", "call_id": call_id})
        # The opening turn may already call a web tool, whose answer arrives on this socket: keep reading while the call starts.
        # The visitor's audio waits until the opening is done (as it did before), at most ~4 s of it.
        starter = asyncio.create_task(call.start(), name=f"start-{call_id}")
        held: list[bytes] = []
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("event")
            if kind == "media":
                data = base64.b64decode(msg["payload"])
                if not starter.done():
                    if len(held) < 200:
                        held.append(data)
                    continue
                if starter.exception():
                    raise starter.exception()
                while held:
                    await call.on_audio(held.pop(0))
                await call.on_audio(data)
            elif kind == "web_tool_result" and call.web_bridge is not None:
                call.web_bridge.resolve(msg.get("id"), msg.get("result"), msg.get("error"))
            elif kind == "stop":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:                                        # noqa: BLE001
        log.exception("public call failed: %r", e)
    finally:
        if starter is not None and not starter.done():
            starter.cancel()
        if handle is not None:
            LIMITER.leave(token, handle)
        if call:
            rt.calls.pop(call.session.call_id, None)
            await call.stop()
        try:
            await ws.close()
        except Exception:                                         # noqa: BLE001
            pass
