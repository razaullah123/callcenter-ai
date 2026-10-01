"""Agent Studio API (Phase 12.6): agents, their drafts, flows, publishing, versions and phone routes.

An agent is edited in its **draft** (a working copy of its release bundle). Test calls can run on the draft; nothing
reaches callers until it is published as a new release. Skill / flow edits made in the studio are stored as new
skill versions and pinned by the draft only.

    GET    /api/agents                            list
    POST   /api/agents                            create   {name, description?, copy_from?}
    GET    /api/agents/{id}                       draft (or published) bundle, skills + graphs, releases, routes
    PUT    /api/agents/{id}                       name / description
    DELETE /api/agents/{id}                       (not the default agent, not while a route points to it)
    PUT    /api/agents/{id}/draft                 replace the draft bundle          {bundle}
    PUT    /api/agents/{id}/draft/skills/{key}    save a skill of the draft         {files} or {graph}
    POST   /api/agents/{id}/draft/discard
    POST   /api/agents/{id}/publish               draft → new published release     {note}
    POST   /api/agents/{id}/activate/{release}    publish an older release (rollback)
    GET    /api/agents/{id}/releases/{release}    a release's bundle (for diffs)
    GET / PUT / DELETE /api/routes                which number / extension reaches which agent
"""

import copy
import re
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from runtime.control.api import _rt, auth, changed
from runtime.control.config_store import schemas
from runtime.harness.prompts import PHRASE_NAMES, Phrases
from runtime.platform import WORKSPACE
from runtime.platform.bundle import KNOBS, SCHEMA, validate as validate_bundle
from runtime.platform.loader import skill_ref
from runtime.platform.toollib import tools_in
from runtime.skills import SKILL_FILES, Graph

router = APIRouter(prefix="/api")

BLANK_SKILL = """---
description: {description}
---
You are the voice agent for {name}. Keep replies short: one or two sentences and one question at a time.
Answer only from what you know for certain; if the caller needs something you can't do, offer to transfer them.
"""
BLANK_FLOW = {"start": "talk", "nodes": [{"id": "talk", "type": "conversation",
                                           "instructions": "Help the caller with their question.",
                                           "position": {"x": 0, "y": 0}}], "edges": []}
BLANK_PERSONA = """---
routable: false
---
## ar
أنت موظف خدمة عملاء صوتي. تكلم بجمل قصيرة ومحترمة، وسؤال واحد بس في كل رد.
## en
You are a voice customer care agent. Be warm and brief: one or two sentences, one question per reply.
"""


def _platform():
    rt = _rt()
    if rt.platform is None:
        raise HTTPException(503, "the platform database is not available")
    return rt, rt.platform


async def _agent(store, agent_id: str) -> dict:
    a = await store.agent(agent_id)
    if a is None:
        raise HTTPException(404, "agent not found")
    return a


async def _published_bundle(store, agent: dict) -> dict:
    if not agent.get("published_release_id"):
        return {}
    return (await store.release(agent["published_release_id"]))["bundle"]


async def _working(store, agent: dict) -> dict:
    return copy.deepcopy(agent.get("draft") or await _published_bundle(store, agent))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


# ---------------------------------------------------------------- agents


@router.get("/agents", dependencies=auth)
async def list_agents() -> list[dict]:
    rt, store = _platform()
    routes = await store.routes(WORKSPACE)
    out = []
    for a in await store.agents(WORKSPACE):
        rel = await store.release(a["published_release_id"]) if a.get("published_release_id") else None
        out.append({"id": a["id"], "name": a["name"], "description": a.get("description", ""),
                    "version": rel["version"] if rel else None, "published_at": rel["created_at"] if rel else None,
                    "has_draft": a.get("draft") is not None, "draft_updated_at": a.get("draft_updated_at"),
                    "routes": [r["pattern"] for r in routes if r["agent_id"] == a["id"]],
                    "default": any(r["pattern"] == "*" and r["agent_id"] == a["id"] for r in routes),
                    "skills": sorted((rel["bundle"].get("skills") or {}) if rel else [])})
    return out


class NewAgent(BaseModel):
    name: str
    description: str = ""
    copy_from: str | None = None
    author: str = "console"


@router.post("/agents", dependencies=auth)
async def create_agent(body: NewAgent) -> dict:
    rt, store = _platform()
    agent_id = _slug(body.name)
    if not agent_id or await store.agent(agent_id):
        raise HTTPException(409, "an agent with this name exists (or the name is empty)")
    if body.copy_from:
        bundle = copy.deepcopy(await _published_bundle(store, await _agent(store, body.copy_from)))
        bundle["agent"] = {**bundle.get("agent", {}), "name": body.name}
    else:
        bundle = await _blank_bundle(rt, store, agent_id, body)
    await store.put_agent({"id": agent_id, "workspace_id": WORKSPACE, "name": body.name,
                           "description": body.description})
    rel = await store.add_release(agent_id, bundle, body.author, "created" + (f" from {body.copy_from}"
                                                                               if body.copy_from else ""))
    await changed(rt, "release", agent=agent_id)
    return {"id": agent_id, **rel}


async def _blank_bundle(rt, store, agent_id: str, body: NewAgent) -> dict:
    """A minimal agent: its own persona + one skill with a one-node flow, no caller verification, the default
    agent's model connections, no tools yet."""
    main, persona = f"{agent_id.replace('-', '_')}_main", f"{agent_id.replace('-', '_')}_persona"
    v_main = await store.add_skill_version(WORKSPACE, main, {
        "SKILL.md": BLANK_SKILL.format(name=body.name, description=body.description or body.name),
        "flow.yaml": yaml.safe_dump(BLANK_FLOW, allow_unicode=True, sort_keys=False)}, body.author, "new agent")
    v_persona = await store.add_skill_version(WORKSPACE, persona, {"SKILL.md": BLANK_PERSONA}, body.author, "new agent")
    base = rt.agent.bundle or {}
    phrases = Phrases.defaults()
    phrases["GREETING"] = {"ar": "مرحباً، كيف أقدر أخدمك؟", "en": "Hello, how can I help you?"}
    return {"schema": SCHEMA, "agent": {"name": body.name, "languages": ["ar", "en"], "default_language": "ar"},
            "models": copy.deepcopy(base.get("models") or {}),
            "knobs": {**{k: getattr(rt.settings, k) for k in KNOBS}, "require_verification": False, "entry_skill": main},
            "voice": {"stt_hint": {"ar": "", "en": ""}}, "phrases": phrases,
            "skills": {"_persona": {"skill": persona, "version": v_persona}, main: v_main},
            "tools": {"server": (base.get("tools") or {}).get("server", ""),
                      "inject": copy.deepcopy((base.get("tools") or {}).get("inject") or {}), "skills": {}}}


@router.get("/agents/{agent_id}", dependencies=auth)
async def get_agent(agent_id: str) -> dict:
    rt, store = _platform()
    a = await _agent(store, agent_id)
    bundle = await _working(store, a)
    skills: dict[str, Any] = {}
    for key, ref in (bundle.get("skills") or {}).items():
        lib, version = skill_ref(key, ref)
        files = await store.skill_version(WORKSPACE, lib, version) or {}
        graph = None
        if files.get("flow.yaml", "").strip():
            try:
                g = Graph.parse(files["flow.yaml"])
                graph = {"converted": g.converted, "graph": g.to_dict(), "errors": g.errors()}
            except Exception as e:
                graph = {"error": str(e)}
        skills[key] = {"library": lib, "version": version, "files": files, "flow": graph,
                       "versions": await store.skill_versions(WORKSPACE, lib)}
    connections = [{k: p[k] for k in ("id", "kind", "type", "name")} for p in await store.providers(WORKSPACE)]
    return {"agent": {k: a.get(k) for k in ("id", "name", "description", "published_release_id", "draft_updated_at",
                                            "draft_updated_by")},
            "has_draft": a.get("draft") is not None, "bundle": bundle, "skills": skills,
            "tools": sorted(tools_in(bundle.get("tools") or {})),
            "releases": await store.releases(agent_id, limit=100),
            "routes": [r for r in await store.routes(WORKSPACE) if r["agent_id"] == agent_id],
            "connections": connections, "schemas": schemas(),
            "choices": {"knobs": {k: t.__name__ for k, t in KNOBS.items()}, "phrases": list(PHRASE_NAMES),
                        "library_skills": [s["name"] for s in await store.skills(WORKSPACE)]}}


class AgentMeta(BaseModel):
    name: str
    description: str = ""


@router.put("/agents/{agent_id}", dependencies=auth)
async def rename_agent(agent_id: str, body: AgentMeta) -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    await store.put_agent({"id": agent_id, "workspace_id": WORKSPACE, "name": body.name, "description": body.description})
    return {"id": agent_id}


@router.delete("/agents/{agent_id}", dependencies=auth)
async def delete_agent(agent_id: str) -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    if agent_id == rt.agent.agent_id or any(r["agent_id"] == agent_id for r in await store.routes(WORKSPACE)):
        raise HTTPException(409, "this agent answers calls (a phone route points to it) — change the routes first")
    await store.delete_agent(agent_id)
    return {"deleted": agent_id}


# ---------------------------------------------------------------- drafts


class DraftBody(BaseModel):
    bundle: dict[str, Any]
    author: str = "console"


async def _check(rt, store, agent_id: str, bundle: dict) -> list[str]:
    """Everything that would stop the agent from loading: bundle fields, skills, flows, tools, hooks."""
    errors = validate_bundle(bundle)
    if errors:
        return errors
    try:
        await rt.loader.build(bundle, agent_id=agent_id, name="check", release_id=None, version=None, warm=False)
    except Exception as e:
        errors.append(str(e)[:500])
    return errors


@router.put("/agents/{agent_id}/draft", dependencies=auth)
async def put_draft(agent_id: str, body: DraftBody) -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    if errors := await _check(rt, store, agent_id, body.bundle):
        raise HTTPException(422, {"errors": errors})
    await store.set_draft(agent_id, body.bundle, body.author)
    return {"ok": True}


class SkillBody(BaseModel):
    files: dict[str, str] | None = None
    graph: dict[str, Any] | None = None
    note: str = ""
    author: str = "console"


@router.put("/agents/{agent_id}/draft/skills/{key}", dependencies=auth)
async def put_draft_skill(agent_id: str, key: str, body: SkillBody) -> dict:
    """A skill edited in the studio (text files, or the canvas graph) → new skill version, pinned by the draft."""
    rt, store = _platform()
    a = await _agent(store, agent_id)
    bundle = await _working(store, a)
    refs = bundle.setdefault("skills", {})
    lib = skill_ref(key, refs[key])[0] if key in refs else key
    current = (await store.skill_version(WORKSPACE, lib, skill_ref(key, refs[key])[1])) if key in refs else {}
    files = dict(body.files or current or {})
    if body.graph is not None:
        graph = Graph.from_dict(body.graph)
        if errors := graph.errors():
            raise HTTPException(422, {"errors": [f"flow: {e}" for e in errors]})
        files["flow.yaml"] = yaml.safe_dump(graph.to_dict(), allow_unicode=True, sort_keys=False)
    files = {f: files[f] for f in SKILL_FILES if files.get(f, "").strip()}
    if "SKILL.md" not in files:
        files["SKILL.md"] = f"---\ndescription: {key}\n---\n"
    if errors := await _check_with_files(rt, agent_id, bundle, key, files):
        raise HTTPException(422, {"errors": errors})
    version = await store.add_skill_version(WORKSPACE, lib, files, body.author, body.note or "studio draft")
    refs[key] = {"skill": lib, "version": version} if isinstance(refs.get(key), dict) else version
    await store.set_draft(agent_id, bundle, body.author)
    return {"skill": lib, "version": version}


async def _check_with_files(rt, agent_id: str, bundle: dict, key: str, files: dict[str, str]) -> list[str]:
    """Would the draft load with these files for `key`? (checked before a version is stored)"""
    trial = copy.deepcopy(bundle)
    resolved = {}
    for k, ref in (trial.get("skills") or {}).items():
        if k == key:
            continue
        lib, version = skill_ref(k, ref)
        resolved[k] = await rt.platform.skill_version(WORKSPACE, lib, version) or {}
    resolved[key] = files
    trial.pop("skills", None)
    trial["skill_files"] = resolved
    return await _check(rt, rt.platform, agent_id, trial)


@router.post("/agents/{agent_id}/draft/discard", dependencies=auth)
async def discard_draft(agent_id: str) -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    await store.set_draft(agent_id, None)
    return {"ok": True}


class PublishBody(BaseModel):
    note: str = ""
    author: str = "console"


@router.post("/agents/{agent_id}/publish", dependencies=auth)
async def publish(agent_id: str, body: PublishBody) -> dict:
    rt, store = _platform()
    a = await _agent(store, agent_id)
    if a.get("draft") is None:
        raise HTTPException(400, "nothing to publish — the draft is the same as the published release")
    if errors := await _check(rt, store, agent_id, a["draft"]):
        raise HTTPException(422, {"errors": errors})
    rel = await store.add_release(agent_id, a["draft"], body.author, body.note or "published from the studio")
    await store.set_draft(agent_id, None)
    await changed(rt, "release", agent=agent_id)
    return rel


@router.post("/agents/{agent_id}/activate/{release_id}", dependencies=auth)
async def activate(agent_id: str, release_id: int) -> dict:
    rt, store = _platform()
    rel = await store.release(release_id)
    if rel is None or rel["agent_id"] != agent_id:
        raise HTTPException(404, "release not found")
    await store.publish(agent_id, release_id)
    await changed(rt, "release", agent=agent_id)
    return {"release_id": release_id, "version": rel["version"]}


@router.get("/agents/{agent_id}/releases/{release_id}", dependencies=auth)
async def get_release(agent_id: str, release_id: int) -> dict:
    rt, store = _platform()
    rel = await store.release(release_id)
    if rel is None or rel["agent_id"] != agent_id:
        raise HTTPException(404, "release not found")
    return rel


# ---------------------------------------------------------------- phone routes


@router.get("/routes", dependencies=auth)
async def list_routes() -> list[dict]:
    rt, store = _platform()
    return await store.routes(WORKSPACE)


class RouteBody(BaseModel):
    pattern: str
    agent_id: str
    priority: int = 0


@router.put("/routes", dependencies=auth)
async def put_route(body: RouteBody) -> dict:
    rt, store = _platform()
    await _agent(store, body.agent_id)
    if not re.fullmatch(r"\*|\+?[0-9]{2,15}\*?", body.pattern.strip()):
        raise HTTPException(422, {"errors": ["pattern: '*' (default), a number, or a prefix ending in * (e.g. 8880*)"]})
    await store.put_route(WORKSPACE, body.pattern.strip(), body.agent_id, body.priority)
    await changed(rt, "release", route=body.pattern)
    return {"ok": True}


@router.delete("/routes", dependencies=auth)
async def delete_route(pattern: str) -> dict:
    rt, store = _platform()
    if pattern == "*":
        raise HTTPException(409, "the default route can be pointed at another agent, not removed")
    await store.delete_route(WORKSPACE, pattern)
    await changed(rt, "release", route=pattern)
    return {"ok": True}
