"""Control-plane API for the console (mounted at /api on the voice server).

Auth: if CONSOLE_TOKEN is set, every request needs `Authorization: Bearer <token>` (WebSocket: ?token=).
"""

import asyncio
import logging
import functools
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from runtime.control import config_store, skills_store, store
from runtime.platform import WORKSPACE, current_project
from runtime.providers import AudioInput, TextDelta, available, create, schemas

log = logging.getLogger(__name__)
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


def bearer_of(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


async def require_console(request: Request) -> None:
    """A signed-in user, the CONSOLE_TOKEN, or (no accounts and no token yet) anyone — see control/accounts.py."""
    from runtime.control.accounts import resolve, set_principal
    rt = _rt()
    who = await resolve(rt.platform, rt.settings, bearer_of(request))
    if who is None:
        raise HTTPException(401, {"message": "sign in required", "login": True})
    set_principal(who)


async def use_project(request: Request) -> None:
    """The console's current project (X-Project header; else the user's default / first project, else the default
    project). A signed-in user only reaches projects they are a member of."""
    from runtime.control.accounts import principal
    rt = _rt()
    who = principal()
    ws = request.headers.get("x-project")
    if who.is_user:
        if not ws:
            ws = who.user.get("default_project") if who.role(who.user.get("default_project") or "") else None
            ws = ws or (WORKSPACE if who.role(WORKSPACE) else next(iter(sorted(who.memberships)), None))
        if ws is None or who.role(ws) is None:
            raise HTTPException(404, {"message": f"project {ws!r} not found", "project": True})
    else:
        ws = ws or WORKSPACE
        if ws != WORKSPACE and rt.platform is not None and not any(
                w["id"] == ws for w in await rt.platform.list_workspaces()):
            raise HTTPException(404, {"message": f"project {ws!r} not found", "project": True})
    from runtime.platform import set_project
    set_project(ws)


auth = [Depends(require_console), Depends(use_project)]

# what a project audit entry may show about a request (never secret values or settings)
AUDIT_FIELDS = ("name", "pattern", "agent_id", "kind", "type", "url", "group", "description")


def audited(action: str):
    """Record a successful console change in the project's audit log (Project settings → Audit log)."""
    def deco(fn):
        @functools.wraps(fn)
        async def inner(*args, **kw):
            result = await fn(*args, **kw)
            rt = _rt()
            if rt.platform is not None:
                detail: dict[str, Any] = {k: v for k, v in kw.items() if isinstance(v, (str, int)) and v != ""
                                          and k not in ("author", "token")}
                body = next((v for v in kw.values() if isinstance(v, BaseModel)), None)
                if body is not None:
                    detail.update({k: getattr(body, k) for k in AUDIT_FIELDS
                                   if isinstance(getattr(body, k, None), (str, int)) and getattr(body, k)})
                agent = str(kw.get("agent_id") or detail.get("agent_id") or
                            (result.get("id") if action.startswith("agent.") and isinstance(result, dict) else "")
                            or "")
                from runtime.control.accounts import principal
                try:
                    await rt.platform.audit(agent, action, principal().actor, detail)
                except Exception as e:                       # the change is done; a missing log line must not fail it
                    log.warning("audit %s not recorded: %r", action, e)
            return result
        return inner
    return deco


# ---------------------------------------------------------------- overview


async def project_scope(agent: str | None = None, ws: str | None = None) -> tuple[list[str], bool]:
    """The calls a console request may see: its project's agents (or one of them). Calls without an agent (from
    before agents existed) belong to the default project."""
    ws = ws or current_project()
    rt = _rt()
    agents = [a["id"] for a in await rt.platform.agents(ws)] if rt.platform is not None else [rt.agent.agent_id]
    if agent:
        if agent not in agents:
            raise HTTPException(404, "agent not found in this project")
        return [agent], False
    return agents, ws == WORKSPACE


async def primary_agent(agent: str | None = None):
    """The agent a project-wide page works on: the one asked for, else the project's default-route agent, else
    its first agent — loaded as callers get it (published release)."""
    rt = _rt()
    if rt.platform is None:
        return rt.agent
    if not agent:
        mine = [a["id"] for a in await rt.platform.agents(current_project())]
        if not mine:
            raise HTTPException(404, "this project has no agents yet")
        routed = [r["agent_id"] for r in await rt.platform.routes(current_project()) if r["agent_id"] in mine]
        agent = rt.agent.agent_id if rt.agent.agent_id in mine else (routed[0] if routed else sorted(mine)[0])
    scope, _ = await project_scope(agent)
    return rt.agent if agent == rt.agent.agent_id else await rt.loader.for_call(agent_id=scope[0])


@router.get("/stats", dependencies=auth)
async def get_stats(hours: int = 24, agent: str | None = None) -> dict:
    scope = await project_scope(agent)
    data = await store.stats(hours, scope)
    data["active_calls"] = len(_rt().live.active_calls(scope)) if _rt().live else 0
    return data


@router.get("/dashboard", dependencies=auth)
async def get_dashboard(start: datetime | None = None, end: datetime | None = None, agent: str | None = None,
                        tz: str = "UTC") -> dict:
    """Dashboard numbers for calls started in [start, end) (ISO times; default: the last 24 hours); the
    calls-over-time series is bucketed in the viewer's time zone `tz` (IANA name)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, f"unknown time zone {tz!r}") from None
    end = _aware(end) if end else datetime.now(timezone.utc)
    start = _aware(start) if start else end - timedelta(hours=24)
    if start >= end:
        raise HTTPException(422, "start must be before end")
    await _rt().store.flush()
    scope = await project_scope(agent)
    data = await store.dashboard(start, end, scope, tz)
    data["live_calls"] = len(_rt().live.active_calls(scope)) if _rt().live else 0
    return data


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


@router.get("/calls", dependencies=auth)
async def get_calls(limit: int = 50, offset: int = 0, q: str | None = None, outcome: str | None = None,
                    channel: str | None = None, agent: str | None = None, channels: str | None = None,
                    status: str | None = None, start: datetime | None = None, end: datetime | None = None,
                    sort: str = "time", desc: bool = True) -> dict:
    """Call history. `channels` / `status`: comma lists (status: in_progress, completed, failed, forwarded,
    terminated; an empty `status=` matches nothing); `start` / `end`: started in [start, end); sort: time | duration."""
    def split(v: str) -> list[str]:
        return [x for x in v.split(",") if x]
    return await store.list_calls(min(limit, 200), offset, q, outcome, channel, await project_scope(agent),
                                  channels=split(channels) if channels else None,
                                  statuses=split(status) if status is not None else None,
                                  start=_aware(start) if start else None, end=_aware(end) if end else None,
                                  sort=sort, desc=desc)


@router.post("/calls/{call_id}/end", dependencies=auth)
async def end_call(call_id: str) -> dict:
    """End a live call (browser playground or IVR) from the console — on whichever worker runs it."""
    rt = _rt()
    if rt.live is not None and call_id in rt.live.active and not rt.live.visible(call_id, await project_scope()):
        raise HTTPException(404, "this call is not running (it may have just ended)")
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
    data = await store.get_call(call_id, await project_scope())
    if data is None:
        raise HTTPException(404, "call not found")
    data["agent"] = await _call_agent(data["call"])
    return data


async def _call_agent(call: dict) -> dict | None:
    """The agent and the exact release a call ran on: name, version, LLM / STT / TTS (voice in the call's language)."""
    rt = _rt()
    if rt.platform is None or not call.get("agent_id"):
        return None
    a = await rt.platform.agent(call["agent_id"])
    rel = await rt.platform.release(call["release_id"]) if call.get("release_id") else None
    models = ((rel or {}).get("bundle") or {}).get("models") or {}
    spec = lambda kind: models.get(kind) or {}
    tts = spec("tts").get("settings") or {}
    lang = call.get("language") or "ar"
    return {"id": call["agent_id"], "name": (a or {}).get("name") or call["agent_id"],
            "version": (rel or {}).get("version"),
            "llm": {"provider": spec("llm").get("provider"), "model": (spec("llm").get("settings") or {}).get("model")},
            "stt": {"provider": spec("stt").get("provider"), "model": (spec("stt").get("settings") or {}).get("model")},
            "tts": {"provider": spec("tts").get("provider"), "voice": tts.get(f"voice_{lang}") or tts.get("voice"),
                    "model": tts.get(f"model_{lang}") or tts.get("model")}}


@router.get("/events", dependencies=auth)
async def get_events(call_id: str | None = None, type: list[str] | None = Query(None), level: str | None = None,
                     text: str | None = None, before_id: int | None = None, limit: int = 200,
                     agent: str | None = None) -> list[dict]:
    await _rt().store.flush()
    return await store.query_events(call_id=call_id, types=type, level=level, text=text, before_id=before_id,
                                    limit=limit, scope=await project_scope(agent))


@router.get("/live/calls", dependencies=auth)
async def live_calls(agent: str | None = None) -> list[dict]:
    return _rt().live.active_calls(await project_scope(agent))


@router.websocket("/live")
async def live_ws(ws: WebSocket, call_id: str | None = None, token: str | None = None,
                  project: str | None = None) -> None:
    from runtime.control.accounts import resolve
    rt = _rt()
    who = await resolve(rt.platform, rt.settings, token)
    if who is not None and who.is_user and not project:          # a user's own default / first project
        d = who.user.get("default_project")
        project = d if who.role(d or "") else (WORKSPACE if who.role(WORKSPACE) else next(iter(sorted(who.memberships)), None))
    if who is None or (who.is_user and project is None) or (project and who.role(project) is None):
        await ws.close(code=1008)
        return
    await ws.accept()
    # one call (a test panel) is shown as asked; the overview only shows the project's calls
    scope = None if call_id else await project_scope(ws=project or WORKSPACE)
    q = rt.live.subscribe(call_id, scope)
    try:
        await ws.send_text(rt.live.active_message(scope))
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
                await ws.send_text(rt.live.active_message(scope))
                last_active = time.monotonic()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        rt.live.unsubscribe(q)


# ---------------------------------------------------------------- providers / agent config


@router.get("/providers", dependencies=auth)
async def get_providers(agent: str | None = None) -> dict:
    """An agent's providers + knobs (its published release), and its release history — by default the project's
    main agent."""
    rt = _rt()
    ag = await primary_agent(agent)
    history = []
    if rt.platform is not None and ag.release_id:
        history = [{**r, "active": r["id"] == ag.release_id} for r in await rt.platform.releases(ag.agent_id)]
    return {"available": available(), "schemas": schemas(), "active_version": ag.version,
            "agent": {"id": ag.agent_id, "name": ag.name, "release_id": ag.release_id, "version": ag.version},
            "config": config_store.masked(ag.providers.config), "history": history,
            "effective": config_store.effective(ag.providers),
            "agents": [{"id": a["id"], "name": a["name"]} for a in await rt.platform.agents(current_project())]
            if rt.platform is not None else [],
            "runtime_knobs": {k: t.__name__ for k, t in config_store.RUNTIME_KNOBS.items()}}


class ConfigBody(BaseModel):
    config: dict[str, Any]
    note: str = ""
    author: str = "console"
    agent: str | None = None


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
    ag = await primary_agent(body.agent)
    config = config_store.merge_secrets(body.config, ag.providers.config)
    if errors := config_store.validate(config):
        raise HTTPException(422, {"errors": errors})
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
            row = await store.provider(pid) or {"id": pid, "workspace_id": current_project(), "kind": kind, "type": type_,
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
async def activate_version(version: int, agent: str | None = None) -> dict:
    """Roll an agent (default: the project's main agent) back / forward to one of its releases."""
    rt = _rt()
    store = _platform(rt)
    ag = await primary_agent(agent)
    match = [r for r in await store.releases(ag.agent_id, limit=1000) if r["version"] == version]
    if not match:
        raise HTTPException(404, "version not found")
    await store.publish(ag.agent_id, match[0]["id"])
    await store.audit(ag.agent_id, "activate", "console", {"release_id": match[0]["id"], "version": version})
    await changed(rt, "release", agent=ag.agent_id)
    return {"version": version}


class TestBody(BaseModel):
    kind: str
    provider: str
    settings: dict[str, Any] = {}
    agent: str | None = None


@router.post("/providers/test", dependencies=auth)
async def test_provider(body: TestBody) -> dict:
    """Quick live check of a provider config (before saving it)."""
    rt = _rt()
    ag = await primary_agent(body.agent)
    settings = config_store.merge_secrets({body.kind: {"provider": body.provider, "settings": body.settings}},
                                          ag.providers.config)[body.kind]["settings"]
    try:
        p = create(body.kind, body.provider, config_store._drop_empty(settings))
    except Exception as e:
        return {"ok": False, "error": f"invalid settings: {e}"}
    return await probe(body.kind, p, ag)


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
    agent: str | None = None


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
    ag = await primary_agent(body.agent)
    job = f"job-{int(time.time())}"
    await JOBS.create(job, len(cases), WORKER_ID)
    rt = _rt()
    pending: list[asyncio.Task] = []

    def progress(r: dict) -> None:
        pending.append(asyncio.create_task(JOBS.progress(job, {"case": r["case"], "passed": r["passed"]})))

    async def work() -> None:
        try:
            # the agent's published release (its skills, phrases, tool policies and knobs)
            run = await run_suite(cases, mode=body.mode, use_judge=body.judge, agent_llm=ag.providers.llm,
                                  progress=progress, bundle=ag.inline_bundle())
            run["config_version"] = ag.providers.version
            run["agent"] = {"id": ag.agent_id, "release": ag.version}
            run["agent_id"] = ag.agent_id
            run["target"] = {"draft": False, "release": ag.version}
            save_report(run)
            await store_run(run)
            await asyncio.gather(*pending, return_exceptions=True)
            await JOBS.finish(job, run_id=run["id"])
        except Exception as e:
            await JOBS.finish(job, error=repr(e)[:300])

    spawn(work())
    return {"job": job, "total": len(cases), "agent": ag.agent_id}


@router.get("/evals/jobs/{job}", dependencies=auth)
async def eval_job(job: str) -> dict:
    from runtime.control.eval_jobs import JOBS
    found = await JOBS.get(job)
    if found is None:
        raise HTTPException(404, "job not found")
    return found


@router.get("/evals/runs", dependencies=auth)
async def eval_runs(limit: int = 30, agent: str | None = None) -> list[dict]:
    """The project's runs (runs from before agents were recorded belong to the default project)."""
    from runtime.data.db import get_pool
    agents, unassigned = await project_scope(agent)
    pool = await get_pool()
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT id, started_at, finished_at, model, mode, summary, agent_id, target FROM eval_runs "
                             "WHERE agent_id = ANY($2::text[]) OR ($3 AND agent_id IS NULL) "
                             "ORDER BY started_at DESC LIMIT $1", limit, agents, unassigned)
    return [{**dict(r), "summary": _json(r["summary"]), "target": _json(r["target"])} for r in rows]


@router.get("/evals/runs/{run_id}", dependencies=auth)
async def eval_run_detail(run_id: str) -> dict:
    from runtime.data.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM eval_runs WHERE id = $1", run_id)
    agents, unassigned = await project_scope()
    if r is not None and not (r["agent_id"] in agents or (unassigned and r["agent_id"] is None)):
        r = None
    if r is None:
        raise HTTPException(404, "run not found")
    return {**dict(r), "summary": _json(r["summary"]), "results": _json(r["results"])}


def _json(v: Any) -> Any:
    import json as _j
    return _j.loads(v) if isinstance(v, str) else v
