"""Control-plane API for the console (mounted at /api on the voice server).

Auth: if CONSOLE_TOKEN is set, every request needs `Authorization: Bearer <token>` (WebSocket: ?token=).
"""

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from runtime.control import config_store, skills_store, store
from runtime.providers import AudioInput, TextDelta, available, create, schemas

router = APIRouter(prefix="/api")
ACTIVE_REFRESH_S = 5.0     # "Active now" refresh on the live console socket


def _rt():
    from runtime.server.app import state
    rt = state.get("rt")
    if rt is None:
        raise HTTPException(503, "runtime not ready")
    return rt


def require_console(request: Request) -> None:
    token = _rt().settings.console_token
    if token is None:
        return
    if request.headers.get("authorization", "") != f"Bearer {token.get_secret_value()}":
        raise HTTPException(401, "console token required")


auth = [Depends(require_console)]


# ---------------------------------------------------------------- overview


@router.get("/stats", dependencies=auth)
async def get_stats(hours: int = 24) -> dict:
    data = await store.stats(hours)
    data["active_calls"] = len(_rt().live.active) if _rt().live else 0
    return data


@router.get("/calls", dependencies=auth)
async def get_calls(limit: int = 50, offset: int = 0, q: str | None = None, outcome: str | None = None,
                    channel: str | None = None) -> dict:
    return await store.list_calls(limit, offset, q, outcome, channel)


@router.post("/calls/{call_id}/end", dependencies=auth)
async def end_call(call_id: str) -> dict:
    """End a live call (browser playground or IVR) from the console."""
    call = _rt().calls.get(call_id)
    if call is None:
        raise HTTPException(404, "this call is not running on this server (already ended, or on another worker)")
    await call.end_from_console()
    return {"call_id": call_id, "ended": True}


@router.get("/calls/{call_id}", dependencies=auth)
async def get_call(call_id: str) -> dict:
    await _rt().store.flush()
    data = await store.get_call(call_id)
    if data is None:
        raise HTTPException(404, "call not found")
    return data


@router.get("/events", dependencies=auth)
async def get_events(call_id: str | None = None, type: list[str] | None = Query(None), level: str | None = None,
                     text: str | None = None, before_id: int | None = None, limit: int = 200) -> list[dict]:
    await _rt().store.flush()
    return await store.query_events(call_id=call_id, types=type, level=level, text=text, before_id=before_id,
                                    limit=limit)


@router.get("/live/calls", dependencies=auth)
async def live_calls() -> list[dict]:
    return _rt().live.active_calls()


@router.websocket("/live")
async def live_ws(ws: WebSocket, call_id: str | None = None, token: str | None = None) -> None:
    rt = _rt()
    if rt.settings.console_token is not None and token != rt.settings.console_token.get_secret_value():
        await ws.close(code=1008)
        return
    await ws.accept()
    q = rt.live.subscribe(call_id)
    try:
        await ws.send_text(rt.live.active_message())
        last_active = time.monotonic()
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=ACTIVE_REFRESH_S)
                await ws.send_text(msg)
            except asyncio.TimeoutError:
                pass
            # refresh "Active now" (durations, turns) even while events keep flowing — before, it only came after
            # 15 s of silence, so a busy call never showed up in the list
            if time.monotonic() - last_active >= ACTIVE_REFRESH_S:
                await ws.send_text(rt.live.active_message())
                last_active = time.monotonic()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        rt.live.unsubscribe(q)


# ---------------------------------------------------------------- providers / agent config


@router.get("/providers", dependencies=auth)
async def get_providers() -> dict:
    rt = _rt()
    return {"available": available(), "schemas": schemas(), "active_version": rt.providers.version,
            "config": config_store.masked(rt.providers.config), "history": await config_store.history(),
            "effective": config_store.effective(rt.providers),
            "runtime_knobs": {k: t.__name__ for k, t in config_store.RUNTIME_KNOBS.items()}}


class ConfigBody(BaseModel):
    config: dict[str, Any]
    note: str = ""
    author: str = "console"


@router.put("/providers", dependencies=auth)
async def save_providers(body: ConfigBody) -> dict:
    rt = _rt()
    config = config_store.merge_secrets(body.config, rt.providers.config)
    if errors := config_store.validate(config):
        raise HTTPException(422, {"errors": errors})
    version = await config_store.save(config, body.author, body.note)
    await rt.apply_config(version, config)
    return {"version": version}


@router.post("/providers/activate/{version}", dependencies=auth)
async def activate_version(version: int) -> dict:
    config = await config_store.activate(version)
    if config is None:
        raise HTTPException(404, "version not found")
    await _rt().apply_config(version, config)
    return {"version": version}


class TestBody(BaseModel):
    kind: str
    provider: str
    settings: dict[str, Any] = {}


@router.post("/providers/test", dependencies=auth)
async def test_provider(body: TestBody) -> dict:
    """Quick live check of a provider config (before saving it)."""
    rt = _rt()
    settings = config_store.merge_secrets({body.kind: {"provider": body.provider, "settings": body.settings}},
                                          rt.providers.config)[body.kind]["settings"]
    try:
        p = create(body.kind, body.provider, config_store._drop_empty(settings))
    except Exception as e:
        return {"ok": False, "error": f"invalid settings: {e}"}
    t0 = time.perf_counter()
    try:
        if body.kind == "llm":
            text, first = "", None
            async for ev in p.stream([{"role": "user", "content": "Reply with one short Najdi greeting."}]):
                if isinstance(ev, TextDelta):
                    first = first or (time.perf_counter() - t0) * 1000
                    text += ev.text
            return {"ok": True, "first_token_ms": round(first or 0), "total_ms": _ms(t0), "sample": text[:200]}
        if body.kind == "tts":
            audio, first, rate = b"", None, 24000
            async for c in p.synthesize("هلا والله، كيف أقدر أخدمك؟", language="ar"):
                first = first or (time.perf_counter() - t0) * 1000
                audio, rate = audio + c.data, c.sample_rate
            return {"ok": True, "first_audio_ms": round(first or 0), "total_ms": _ms(t0),
                    "audio_s": round(len(audio) / 2 / rate, 2)}
        if body.kind == "stt":
            tts_audio, rate = b"", 24000
            async for c in rt.tts.synthesize("أبي أحجز موعد", language="ar"):
                tts_audio, rate = tts_audio + c.data, c.sample_rate
            t0 = time.perf_counter()
            tr = await p.transcribe(AudioInput(tts_audio, rate))
            return {"ok": True, "total_ms": _ms(t0), "sample": tr.text, "language": tr.language}
        if body.kind == "embedding":
            [vec] = await p.embed(["حي النرجس"])
            return {"ok": True, "total_ms": _ms(t0), "dimension": len(vec)}
    except Exception as e:
        return {"ok": False, "error": repr(e)[:300], "total_ms": _ms(t0)}
    return {"ok": False, "error": "unknown kind"}


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------- skills / tools


@router.get("/skills", dependencies=auth)
async def list_skills() -> list[dict]:
    sk = _rt().skills
    return [{"name": s.name, "description": s.description, "status": s.status, "routable": s.routable,
             "has_flow": s.flow is not None, "steps": [st.id for st in s.flow.steps] if s.flow else [],
             "tools": sorted(sk.tools(s.name, _DummySession())) if not s.flow else sorted(s.flow.all_tools())}
            for s in sk.skills.values()]


@router.get("/skills/{name}", dependencies=auth)
async def get_skill(name: str) -> dict:
    files = skills_store.read_files(_rt().skills, name)
    if files is None:
        raise HTTPException(404, "skill not found")
    return {"name": name, "files": files, "versions": await skills_store.versions(name)}


class SkillBody(BaseModel):
    files: dict[str, str]
    note: str = ""
    author: str = "console"


@router.post("/skills/{name}/validate", dependencies=auth)
async def validate_skill(name: str, body: SkillBody) -> dict:
    return {"errors": skills_store.validate(_rt().skills, name, body.files)}


@router.put("/skills/{name}", dependencies=auth)
async def save_skill(name: str, body: SkillBody) -> dict:
    rt = _rt()
    if errors := skills_store.validate(rt.skills, name, body.files):
        raise HTTPException(422, {"errors": errors})
    version = await skills_store.save(rt.skills, name, body.files, body.author, body.note)
    return {"version": version}


@router.get("/skills/{name}/versions/{version}", dependencies=auth)
async def get_skill_version(name: str, version: int) -> dict:
    files = await skills_store.get_version(name, version)
    if files is None:
        raise HTTPException(404, "version not found")
    return {"name": name, "version": version, "files": files}


@router.get("/tools", dependencies=auth)
async def list_tools() -> list[dict]:
    return [{"name": t.name, "skill": t.skill, "kind": t.kind, "source": t.source, "confirm": t.confirm,
             "cache_ttl": t.cache_ttl, "description": t.description[:300]}
            for t in _rt().executor.catalog.tools.values()]


class _DummySession:
    """Enough of a Session for listing a skill's tools outside a call."""
    class _Auth:
        stage = "verified"

    def __init__(self) -> None:
        self.slots: dict = {}
        self.auth = self._Auth()


# ---------------------------------------------------------------- evals (Phase 6)

_eval_jobs: dict[str, dict[str, Any]] = {}


@router.get("/evals/cases", dependencies=auth)
async def eval_cases() -> list[dict]:
    from evals.runner import load_cases
    return [{"id": c.id, "suite": c.suite, "title": c.title, "language": c.language,
             "mode": "script" if c.caller.get("script") else "llm"} for c in load_cases()]


class EvalRunBody(BaseModel):
    suite: str | None = None
    cases: list[str] | None = None
    mode: str = "auto"
    judge: bool = False


@router.post("/evals/run", dependencies=auth)
async def eval_run(body: EvalRunBody) -> dict:
    """Runs in the background against the active provider config; poll /api/evals/jobs/{job}."""
    from evals.runner import load_cases, run_suite, save_report, store_run
    cases = load_cases(body.suite, body.cases)
    if not cases:
        raise HTTPException(400, "no cases match")
    if any(j["status"] == "running" for j in _eval_jobs.values()):
        raise HTTPException(409, "an eval run is already in progress")
    job = f"job-{int(time.time())}"
    _eval_jobs[job] = {"status": "running", "total": len(cases), "done": [], "run_id": None, "error": None}
    rt = _rt()

    def progress(r: dict) -> None:
        _eval_jobs[job]["done"].append({"case": r["case"], "passed": r["passed"]})

    async def work() -> None:
        try:
            run = await run_suite(cases, mode=body.mode, use_judge=body.judge, agent_llm=rt.providers.llm,
                                  progress=progress)
            run["config_version"] = rt.providers.version
            save_report(run)
            await store_run(run)
            _eval_jobs[job].update(status="done", run_id=run["id"])
        except Exception as e:
            _eval_jobs[job].update(status="error", error=repr(e)[:300])

    asyncio.create_task(work())
    return {"job": job, "total": len(cases)}


@router.get("/evals/jobs/{job}", dependencies=auth)
async def eval_job(job: str) -> dict:
    if job not in _eval_jobs:
        raise HTTPException(404, "job not found")
    return _eval_jobs[job]


@router.get("/evals/runs", dependencies=auth)
async def eval_runs(limit: int = 30) -> list[dict]:
    from runtime.data.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT id, started_at, finished_at, model, mode, summary FROM eval_runs "
                             "ORDER BY started_at DESC LIMIT $1", limit)
    return [{**dict(r), "summary": _json(r["summary"])} for r in rows]


@router.get("/evals/runs/{run_id}", dependencies=auth)
async def eval_run_detail(run_id: str) -> dict:
    from runtime.data.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM eval_runs WHERE id = $1", run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return {**dict(r), "summary": _json(r["summary"]), "results": _json(r["results"])}


def _json(v: Any) -> Any:
    import json as _j
    return _j.loads(v) if isinstance(v, str) else v
