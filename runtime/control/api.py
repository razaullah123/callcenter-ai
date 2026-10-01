"""Control-plane API for the console (mounted at /api on the voice server).

Auth: if CONSOLE_TOKEN is set, every request needs `Authorization: Bearer <token>` (WebSocket: ?token=).
"""

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from runtime.control import config_store, skills_store, store
from runtime.platform import WORKSPACE
from runtime.providers import AudioInput, TextDelta, available, create, schemas

router = APIRouter(prefix="/api")
ACTIVE_REFRESH_S = 5.0     # "Active now" refresh on the live console socket


def _rt():
    from runtime.server.app import state
    rt = state.get("rt")
    if rt is None:
        raise HTTPException(503, "runtime not ready")
    return rt


async def changed(rt, kind: str, **detail) -> None:
    """Reload here and tell the other worker processes (new calls everywhere use the change)."""
    notify = getattr(rt, "config_changed", None)
    await (notify(kind, **detail) if notify else rt.reload())


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
    """End a live call (browser playground or IVR) from the console — on whichever worker runs it."""
    rt = _rt()
    ender = getattr(rt, "end_call", None)
    if ender is not None:
        ok = await ender(call_id)
    elif (call := rt.calls.get(call_id)) is not None:
        await call.end_from_console()
        ok = True
    else:
        ok = False
    if not ok:
        raise HTTPException(404, "this call is not running (it may have just ended)")
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
    """The default agent's providers + knobs (its published release), and its release history."""
    rt = _rt()
    ag = rt.agent
    history = []
    if rt.platform is not None and ag.release_id:
        history = [{**r, "active": r["id"] == ag.release_id} for r in await rt.platform.releases(ag.agent_id)]
    return {"available": available(), "schemas": schemas(), "active_version": ag.version,
            "agent": {"id": ag.agent_id, "name": ag.name, "release_id": ag.release_id, "version": ag.version},
            "config": config_store.masked(rt.providers.config), "history": history,
            "effective": config_store.effective(rt.providers),
            "runtime_knobs": {k: t.__name__ for k, t in config_store.RUNTIME_KNOBS.items()}}


class ConfigBody(BaseModel):
    config: dict[str, Any]
    note: str = ""
    author: str = "console"


def _platform(rt):
    if rt.platform is None or not rt.agent.release_id:
        raise HTTPException(503, "the platform database is not available — changes can't be saved")
    return rt.platform


@router.put("/providers", dependencies=auth)
async def save_providers(body: ConfigBody) -> dict:
    """Connection settings (keys, URLs) update the shared provider record; the agent's choices (model, voice …) and
    knobs go into a new published release of the agent."""
    from runtime.platform.bundle import KINDS, split_settings
    from runtime.platform.seed import KIND_NAMES, PROVIDER_NAMES, provider_id
    rt = _rt()
    store = _platform(rt)
    config = config_store.merge_secrets(body.config, rt.providers.config)
    if errors := config_store.validate(config):
        raise HTTPException(422, {"errors": errors})
    ag = rt.agent
    bundle = (await store.release(ag.release_id))["bundle"]
    for kind in KINDS:
        spec = config.get(kind)
        if not spec:
            continue
        type_ = spec.get("provider") or "groq"
        conn, choice = split_settings(spec.get("settings") or {})
        pid = ((bundle.get("models") or {}).get(kind) or {}).get("provider")
        row = await store.provider(pid) if pid else None
        if row is None or row["type"] != type_:
            pid = provider_id(kind, type_)
            row = await store.provider(pid) or {"id": pid, "workspace_id": WORKSPACE, "kind": kind, "type": type_,
                                                "name": f"{PROVIDER_NAMES.get(type_, type_)} {KIND_NAMES[kind]}",
                                                "settings": {}}
        if rt.secrets is not None and rt.secrets.cipher.available:     # keys → encrypted secrets, references here
            conn = await rt.secrets.externalize(pid, conn, body.author, row.get("settings") or {})
        if row.get("settings") != conn or row.get("updated_by") is None:
            await store.put_provider({**row, "settings": conn, "updated_by": body.author})
        bundle.setdefault("models", {})[kind] = {"provider": pid, "settings": choice}
    bundle["knobs"] = {**(bundle.get("knobs") or {}), **(config.get("runtime") or {})}
    rel = await store.add_release(ag.agent_id, bundle, body.author, body.note or "providers / knobs")
    await changed(rt, "release", agent=ag.agent_id)
    return {"version": rel["version"], "release_id": rel["id"]}


@router.post("/providers/activate/{version}", dependencies=auth)
async def activate_version(version: int) -> dict:
    """Roll the default agent back / forward to one of its releases."""
    rt = _rt()
    store = _platform(rt)
    match = [r for r in await store.releases(rt.agent.agent_id, limit=1000) if r["version"] == version]
    if not match:
        raise HTTPException(404, "version not found")
    await store.publish(rt.agent.agent_id, match[0]["id"])
    await changed(rt, "release", agent=rt.agent.agent_id)
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
    return await probe(body.kind, p, rt)


async def probe(kind: str, p, rt) -> dict:
    """A short live request through provider `p` (latency + a sample)."""
    t0 = time.perf_counter()
    try:
        if kind == "llm":
            text, first = "", None
            async for ev in p.stream([{"role": "user", "content": "Reply with one short Najdi greeting."}]):
                if isinstance(ev, TextDelta):
                    first = first or (time.perf_counter() - t0) * 1000
                    text += ev.text
            return {"ok": True, "first_token_ms": round(first or 0), "total_ms": _ms(t0), "sample": text[:200]}
        if kind == "tts":
            audio, first, rate = b"", None, 24000
            async for c in p.synthesize("هلا والله، كيف أقدر أخدمك؟", language="ar"):
                first = first or (time.perf_counter() - t0) * 1000
                audio, rate = audio + c.data, c.sample_rate
            return {"ok": True, "first_audio_ms": round(first or 0), "total_ms": _ms(t0),
                    "audio_s": round(len(audio) / 2 / rate, 2)}
        if kind == "stt":
            tts_audio, rate = b"", 24000
            async for c in rt.tts.synthesize("أبي أحجز موعد", language="ar"):
                tts_audio, rate = tts_audio + c.data, c.sample_rate
            t0 = time.perf_counter()
            tr = await p.transcribe(AudioInput(tts_audio, rate))
            return {"ok": True, "total_ms": _ms(t0), "sample": tr.text, "language": tr.language}
        if kind == "embedding":
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
             "has_flow": s.flow is not None, "steps": [n for n in s.flow.nodes if n != "__done__"] if s.flow else [],
             "tools": sorted(sk.tools(s.name, _DummySession())) if not s.flow else sorted(s.flow.all_tools())}
            for s in sk.skills.values()]


@router.get("/skills/{name}", dependencies=auth)
async def get_skill(name: str) -> dict:
    rt = _rt()
    files = await skills_store.read_files(rt, name)
    if files is None:
        raise HTTPException(404, "skill not found")
    return {"name": name, "files": files, "versions": await skills_store.versions(rt, name)}


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
    _platform(rt)
    if errors := skills_store.validate(rt.skills, name, body.files):
        raise HTTPException(422, {"errors": errors})
    return await skills_store.save(rt, name, body.files, body.author, body.note)


@router.get("/skills/{name}/graph", dependencies=auth)
async def get_skill_graph(name: str) -> dict:
    """The skill's flow as a graph (nodes, edges, variables) — step flows converted — for the canvas."""
    graph = _rt().skills.graph(name) if hasattr(_rt().skills, "graph") else None
    if graph is None:
        raise HTTPException(404, "this skill has no flow")
    return {"name": name, "converted": graph.converted, "graph": graph.to_dict(), "errors": graph.errors()}


@router.get("/skills/{name}/versions/{version}", dependencies=auth)
async def get_skill_version(name: str, version: int) -> dict:
    files = await skills_store.get_version(_rt(), name, version)
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
    from runtime.control.eval_jobs import JOBS, spawn
    from runtime.platform.sync import WORKER_ID
    if await JOBS.running():
        raise HTTPException(409, "an eval run is already in progress")
    job = f"job-{int(time.time())}"
    await JOBS.create(job, len(cases), WORKER_ID)
    rt = _rt()
    pending: list[asyncio.Task] = []

    def progress(r: dict) -> None:
        pending.append(asyncio.create_task(JOBS.progress(job, {"case": r["case"], "passed": r["passed"]})))

    async def work() -> None:
        try:
            # the default agent's published release (its skills, phrases, tool policies and knobs)
            run = await run_suite(cases, mode=body.mode, use_judge=body.judge, agent_llm=rt.providers.llm,
                                  progress=progress, bundle=rt.agent.inline_bundle())
            run["config_version"] = rt.providers.version
            run["agent"] = {"id": rt.agent.agent_id, "release": rt.agent.version}
            save_report(run)
            await store_run(run)
            await asyncio.gather(*pending, return_exceptions=True)
            await JOBS.finish(job, run_id=run["id"])
        except Exception as e:
            await JOBS.finish(job, error=repr(e)[:300])

    spawn(work())
    return {"job": job, "total": len(cases)}


@router.get("/evals/jobs/{job}", dependencies=auth)
async def eval_job(job: str) -> dict:
    from runtime.control.eval_jobs import JOBS
    found = await JOBS.get(job)
    if found is None:
        raise HTTPException(404, "job not found")
    return found


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
