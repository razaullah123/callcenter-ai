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
    POST   /api/agents/{id}/publish               draft → new published release     {note, override_reason?}
                                                  (gate: the agent's gate test cases must have passed on this
                                                  draft — agent_evals_api — or a reason is given; audited)
    POST   /api/agents/{id}/activate/{release}    publish an older release (rollback)
    GET    /api/agents/{id}/releases/{release}    a release's bundle (for diffs)
    GET / PUT / DELETE /api/routes                which number / extension reaches which agent
"""

import copy
import re
import uuid
from typing import Any, Literal

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from runtime.control.accounts import principal
from runtime.control.api import _rt, auth, changed, audited
from runtime.control.config_store import schemas
from runtime.harness.prompts import PHRASE_NAMES, Phrases
from runtime.platform import WORKSPACE, current_project, gate, hamsa_import
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
# What a new agent starts with, like Hamsa's: one Start node (a conversation node, no transitions yet), a general
# "helpful assistant" prompt and a plain greeting.
BLANK_FLOW = {"start": "start", "nodes": [{"id": "start", "type": "conversation", "label": "Start Node",
                                            "description": "Initial start node for the workflow.",
                                            "instructions": "You are a helpful assistant.",
                                            "position": {"x": 0, "y": 0}}], "edges": []}
BLANK_PERSONA = """---
routable: false
---
## ar
أنت مساعد مفيد تجيب على أسئلة المستخدمين.
## en
You are a helpful assistant that will answer users questions.
"""


def _platform():
    rt = _rt()
    if rt.platform is None:
        raise HTTPException(503, "the platform database is not available")
    return rt, rt.platform


async def _agent(store, agent_id: str) -> dict:
    """The agent, if it belongs to the console's current project."""
    a = await store.agent(agent_id)
    if a is None or (a.get("workspace_id") or WORKSPACE) != current_project():
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


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def uuid_agent_id(name: str) -> str:
    """A new agent's id, like Hamsa's: a UUID (8f724d99-c291-4693-952c-51f098a8dc08). Agents made before keep theirs."""
    return str(uuid.uuid4())


new_agent_id = uuid_agent_id               # what create / import call (tests swap it for readable ids)


def skill_key(agent_id: str, name: str) -> str:
    """The prefix of an agent's own skills in the project's library ("<name>_<first 8 of the id>_main")."""
    if not _UUID.fullmatch(agent_id):
        return agent_id.replace("-", "_")
    return f"{_slug(name).replace('-', '_')[:24] or 'agent'}_{agent_id[:8]}"


# ---------------------------------------------------------------- agents


@router.get("/agents", dependencies=auth)
async def list_agents() -> list[dict]:
    rt, store = _platform()
    routes = await store.routes(current_project())
    out = []
    limit = principal().agents(current_project())
    for a in await store.agents(current_project()):
        if limit is not None and a["id"] not in limit:
            continue
        rel = await store.release(a["published_release_id"]) if a.get("published_release_id") else None
        bundle = (rel or {}).get("bundle") or a.get("draft") or {}
        meta = bundle.get("agent") or {}
        langs = meta.get("languages") or ["ar", "en"]
        lang = meta.get("default_language") or langs[0]
        tts = ((bundle.get("models") or {}).get("tts") or {})
        out.append({"id": a["id"], "name": a["name"], "description": a.get("description", ""),
                    "version": rel["version"] if rel else None, "published_at": rel["created_at"] if rel else None,
                    "has_draft": a.get("draft") is not None, "draft_updated_at": a.get("draft_updated_at"),
                    "routes": [r["pattern"] for r in routes if r["agent_id"] == a["id"]],
                    "default": any(r["pattern"] == "*" and r["agent_id"] == a["id"] for r in routes),
                    "skills": sorted((rel["bundle"].get("skills") or {}) if rel else []),
                    "type": await _agent_type(store, bundle), "languages": langs, "default_language": lang,
                    "voice": (tts.get("settings") or {}).get(f"voice_{lang}") or (tts.get("settings") or {}).get("voice"),
                    "voice_provider": tts.get("provider"),
                    "created_at": a.get("created_at"), "updated_at": a.get("updated_at")})
    return out


async def _agent_type(store, bundle: dict) -> str:
    """Hamsa's agent types: "flow" (a flow graph drives the call) or "prompt" (the model follows a prompt)."""
    if (bundle.get("agent") or {}).get("type") in ("flow", "prompt"):
        return bundle["agent"]["type"]
    if (bundle.get("knobs") or {}).get("main_flow"):
        return "flow"
    for key, ref in (bundle.get("skills") or {}).items():       # any skill with a flow graph (e.g. multi-skill HMG)
        if key == "_persona":
            continue
        lib, version = skill_ref(key, ref)
        files = await store.skill_version(current_project(), lib, version) if version else None
        if files and files.get("flow.yaml"):
            return "flow"
    return "prompt"


class NewAgent(BaseModel):
    name: str = Field(max_length=150)
    description: str = ""
    copy_from: str | None = None
    type: Literal["flow", "prompt"] = "flow"     # a blank agent: one conversation node, or a prompt the model follows
    author: str = "console"


@router.post("/agents", dependencies=auth)
@audited("agent.created")
async def create_agent(body: NewAgent) -> dict:
    rt, store = _platform()
    agent_id = new_agent_id(body.name) if body.name.strip() else ""
    if not agent_id or await store.agent(agent_id):
        raise HTTPException(409, "an agent with this name exists (or the name is empty)")
    if body.copy_from:
        bundle = copy.deepcopy(await _published_bundle(store, await _agent(store, body.copy_from)))
        bundle["agent"] = {**bundle.get("agent", {}), "name": body.name}
    else:
        bundle = await _blank_bundle(rt, store, agent_id, body)
    await store.put_agent({"id": agent_id, "workspace_id": current_project(), "name": body.name,
                           "description": body.description})
    rel = await store.add_release(agent_id, bundle, body.author, "created" + (f" from {body.copy_from}"
                                                                               if body.copy_from else ""))
    await changed(rt, "release", agent=agent_id)
    return {**rel, "id": agent_id, "release_id": rel["id"]}       # the agent id (rel["id"] is the release)


async def _blank_bundle(rt, store, agent_id: str, body: NewAgent) -> dict:
    """A minimal agent: its own persona + one skill with a one-node flow, no caller verification, the default
    agent's model connections, no tools yet."""
    key = skill_key(agent_id, body.name)
    main, persona = f"{key}_main", f"{key}_persona"
    files = {"SKILL.md": BLANK_SKILL.format(name=body.name, description=body.description or body.name)}
    if body.type == "flow":
        files["flow.yaml"] = yaml.safe_dump(BLANK_FLOW, allow_unicode=True, sort_keys=False)
    v_main = await store.add_skill_version(current_project(), main, files, body.author, "new agent")
    v_persona = await store.add_skill_version(current_project(), persona, {"SKILL.md": BLANK_PERSONA}, body.author, "new agent")
    base = rt.agent.bundle or {}
    models = copy.deepcopy(base.get("models") or {})
    mine = await store.providers(current_project())
    for kind, spec in models.items():
        if spec.get("provider") and not any(p["id"] == spec["provider"] for p in mine):
            same = [p for p in mine if p["kind"] == kind]
            if same:
                spec["provider"] = same[0]["id"]
            else:
                models[kind] = {k: v for k, v in spec.items() if k != "provider"}
    servers = [m["name"] for m in await store.mcp_servers(current_project())]
    server = (base.get("tools") or {}).get("server", "")
    if servers and server not in servers:
        server = servers[0]
    phrases = Phrases.defaults()
    phrases["GREETING"] = {"ar": "مرحباً، كيف أقدر أساعدك اليوم؟", "en": "Hello, how can I help you today?"}
    return {"schema": SCHEMA, "agent": {"name": body.name, "languages": ["ar", "en"], "default_language": "ar",
                                        "type": body.type},
            "models": models,
            "knobs": {**{k: getattr(rt.settings, k) for k in KNOBS}, "require_verification": False, "entry_skill": main},
            "voice": {"stt_hint": {"ar": "", "en": ""}}, "phrases": phrases,
            "skills": {"_persona": {"skill": persona, "version": v_persona}, main: v_main},
            "tools": {"server": server,
                      "inject": copy.deepcopy((base.get("tools") or {}).get("inject") or {}), "skills": {}}}


class ImportBody(BaseModel):
    content: str = Field(max_length=20_000_000)       # the file's text
    name: str | None = Field(default=None, max_length=150)
    filename: str | None = None
    author: str = "console"


@router.post("/agents/import", dependencies=auth)
@audited("agent.imported")
async def import_agent(body: ImportBody) -> dict:
    """A Hamsa agent (the JSON Hamsa's flow builder loads) → a new agent of this project: persona, one flow skill
    (its graph), HTTP tools, greeting. It answers no number until a phone route points to it."""
    rt, store = _platform()
    try:
        hamsa = hamsa_import.parse(body.content)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    taken: set[str] = set()                            # tool names the project already has
    for status in (rt.mcp_status() if hasattr(rt, "mcp_status") else {}).values():
        taken |= set((status or {}).get("tools") or [])
    for a in await store.agents(current_project()):
        if a.get("published_release_id"):
            taken |= set(tools_in(((await store.release(a["published_release_id"]))["bundle"]).get("tools") or {}))
    parts = hamsa_import.convert(hamsa, taken_tools=taken)
    name = (body.name or parts["name"]).strip() or "Imported agent"
    base = new_agent_id(name) or "imported-agent"
    agent_id, k = base, 2
    while await store.agent(agent_id):
        agent_id, k = f"{base[:36]}-{k}", k + 1
    key = skill_key(agent_id, name)
    flow_skill, persona = f"{key}_flow", f"{key}_persona"
    ws = current_project()
    v_flow = await store.add_skill_version(ws, flow_skill, {
        "SKILL.md": "---\n" + yaml.safe_dump({"description": f"{name} — imported from Hamsa", "routable": False},
                                             allow_unicode=True, sort_keys=False)
                    + "---\nFollow the current step of the flow.\n",
        "flow.yaml": yaml.safe_dump(parts["flow"], allow_unicode=True, sort_keys=False, width=1000)},
        body.author, "imported from Hamsa")
    v_persona = await store.add_skill_version(ws, persona, {
        "SKILL.md": "---\nroutable: false\n---\n" + (parts["persona"] or BLANK_PERSONA.split("---", 2)[2])},
        body.author, "imported from Hamsa")
    blank = await _blank_bundle(rt, store, agent_id, NewAgent(name=name))     # the project's models and tool server
    models = blank["models"]
    if parts.get("temperature") is not None and models.get("llm"):
        models["llm"].setdefault("settings", {})["temperature"] = parts["temperature"]
    phrases = Phrases.defaults()
    if parts["greeting"]:
        phrases["GREETING"] = {"ar": parts["greeting"], "en": parts["greeting"]}
    bundle = {**blank,
              "agent": {"name": name, "languages": parts["languages"], "default_language": parts["default_language"],
                        "type": "flow"},
              "models": models, "phrases": phrases,
              "knobs": {**blank["knobs"], **parts["knobs"], "require_verification": False, "main_flow": flow_skill,
                        "entry_skill": flow_skill},
              "skills": {"_persona": {"skill": persona, "version": v_persona}, flow_skill: v_flow},
              "tools": {**blank["tools"], "skills": {"imported": parts["tools"]}} if parts["tools"] else blank["tools"],
              "imported": {**parts["source"], "filename": body.filename, "report": parts["report"]}}
    if parts.get("analysis"):
        bundle["analysis"] = parts["analysis"]
        parts["report"].append("The outcome schema became the Outcome fields (Global Settings → Outcome) and is switched on.")
    if errors := await _check(rt, store, agent_id, copy.deepcopy(bundle)):
        raise HTTPException(422, {"errors": errors, "report": parts["report"]})
    await store.put_agent({"id": agent_id, "workspace_id": ws, "name": name,
                           "description": f"Imported from Hamsa ({parts['source'].get('name')})"})
    rel = await store.add_release(agent_id, bundle, body.author, f"imported from Hamsa: {parts['source'].get('name')}")
    await changed(rt, "release", agent=agent_id)
    return {"id": agent_id, "release_id": rel["id"], "version": rel["version"], "name": name,
            "stats": parts["stats"], "tools": sorted(parts["tools"]), "report": parts["report"]}


@router.get("/voice-agents/{agent_id}", dependencies=auth)        # Hamsa's address: /voice-agents/<agent id>?projectId=<project id>
@router.get("/agents/{agent_id}", dependencies=auth)
async def get_agent(agent_id: str) -> dict:
    rt, store = _platform()
    a = await _agent(store, agent_id)
    bundle = await _working(store, a)
    skills: dict[str, Any] = {}
    for key, ref in (bundle.get("skills") or {}).items():
        lib, version = skill_ref(key, ref)
        files = await store.skill_version(current_project(), lib, version) or {}
        graph = None
        if files.get("flow.yaml", "").strip():
            try:
                g = Graph.parse(files["flow.yaml"])
                graph = {"converted": g.converted, "graph": g.to_dict(), "errors": g.errors()}
            except Exception as e:
                graph = {"error": str(e)}
        skills[key] = {"library": lib, "version": version, "files": files, "flow": graph,
                       "versions": await store.skill_versions(current_project(), lib)}
    connections = [{k: p[k] for k in ("id", "kind", "type", "name")} for p in await store.providers(current_project())]
    return {"agent": {k: a.get(k) for k in ("id", "name", "description", "published_release_id", "draft_updated_at",
                                            "draft_updated_by")},
            "has_draft": a.get("draft") is not None, "bundle": bundle, "skills": skills,
            "tools": sorted(tools_in(bundle.get("tools") or {})),
            "releases": await store.releases(agent_id, limit=100),
            "routes": [r for r in await store.routes(current_project()) if r["agent_id"] == agent_id],
            "connections": connections, "schemas": schemas(),
            "choices": {"knobs": {k: t.__name__ for k, t in KNOBS.items()}, "phrases": list(PHRASE_NAMES),
                        "library_skills": [s["name"] for s in await store.skills(current_project())]}}


class AgentMeta(BaseModel):
    name: str
    description: str = ""


@router.put("/agents/{agent_id}", dependencies=auth)
@audited("agent.renamed")
async def rename_agent(agent_id: str, body: AgentMeta) -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    await store.put_agent({"id": agent_id, "workspace_id": current_project(), "name": body.name, "description": body.description})
    return {"id": agent_id}


@router.delete("/agents/{agent_id}", dependencies=auth)
@audited("agent.deleted")
async def delete_agent(agent_id: str) -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    if agent_id == rt.agent.agent_id or any(r["agent_id"] == agent_id for r in await store.routes(current_project())):
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
    current = (await store.skill_version(current_project(), lib, skill_ref(key, refs[key])[1])) if key in refs else {}
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
    version = await store.add_skill_version(current_project(), lib, files, body.author, body.note or "studio draft")
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
        resolved[k] = await rt.platform.skill_version(current_project(), lib, version) or {}
    resolved[key] = files
    trial.pop("skills", None)
    trial["skill_files"] = resolved
    return await _check(rt, rt.platform, agent_id, trial)


@router.post("/agents/{agent_id}/draft/discard", dependencies=auth)
async def discard_draft(agent_id: str, author: str = "console") -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    await store.set_draft(agent_id, None)
    await store.audit(agent_id, "draft.discarded", author)
    return {"ok": True}


class PublishBody(BaseModel):
    note: str = ""
    author: str = "console"
    override_reason: str | None = None      # publish although the gate test cases did not all pass


@router.post("/agents/{agent_id}/publish", dependencies=auth)
async def publish(agent_id: str, body: PublishBody) -> dict:
    rt, store = _platform()
    a = await _agent(store, agent_id)
    if a.get("draft") is None:
        raise HTTPException(400, "nothing to publish — the draft is the same as the published release")
    if errors := await _check(rt, store, agent_id, a["draft"]):
        raise HTTPException(422, {"errors": errors})
    st = gate.status(a, await store.eval_cases(agent_id))
    reason = (body.override_reason or "").strip() or None
    if not st["ok"] and reason is None:
        raise HTTPException(409, {"gate": {k: st[k] for k in ("required", "passed", "failed", "not_run")},
                                  "message": "the agent's gate test cases have not all passed on this draft — run "
                                             "them, or publish with a reason"})
    if not st["ok"] and len(reason) < gate.MIN_OVERRIDE_REASON:
        raise HTTPException(422, {"errors": [f"override reason: at least {gate.MIN_OVERRIDE_REASON} characters"]})
    record = gate.release_gate(st, override=reason if not st["ok"] else None, by=body.author)
    rel = await store.add_release(agent_id, a["draft"], body.author, body.note or "published from the studio",
                                  gate=record)
    await store.set_draft(agent_id, None)
    await store.audit(agent_id, "publish.override" if "override" in record else "publish", body.author,
                      {"release_id": rel["id"], "version": rel["version"], "note": body.note, **record})
    await changed(rt, "release", agent=agent_id)
    return {**rel, "gate": record}


@router.post("/agents/{agent_id}/activate/{release_id}", dependencies=auth)
async def activate(agent_id: str, release_id: int, author: str = "console") -> dict:
    rt, store = _platform()
    rel = await store.release(release_id)
    if rel is None or rel["agent_id"] != agent_id:
        raise HTTPException(404, "release not found")
    before = (await _agent(store, agent_id)).get("published_release_id")
    await store.publish(agent_id, release_id)
    await store.audit(agent_id, "activate", author, {"release_id": release_id, "version": rel["version"],
                                                     "previous_release_id": before})
    await changed(rt, "release", agent=agent_id)
    return {"release_id": release_id, "version": rel["version"]}


@router.get("/agents/{agent_id}/export", dependencies=auth)
async def export_agent(agent_id: str, draft: bool = True) -> dict:
    """The agent as one JSON document: its bundle with every skill's files inline (secrets stay references)."""
    rt, store = _platform()
    a = await _agent(store, agent_id)
    bundle = await _working(store, a) if draft else await _published_bundle(store, a)
    files = {}
    for key, ref in (bundle.get("skills") or {}).items():
        lib, version = skill_ref(key, ref)
        files[key] = {"skill": lib, "version": version, "files": await store.skill_version(current_project(), lib, version)}
    out = {k: v for k, v in bundle.items() if k != "skills"}
    return {"format": "voice-agent/1", "agent": {"id": a["id"], "name": a["name"], "description": a.get("description", "")},
            "from": "draft" if draft and a.get("draft") is not None else "published", "bundle": out, "skills": files}


@router.get("/agents/{agent_id}/releases/{release_id}", dependencies=auth)
async def get_release(agent_id: str, release_id: int) -> dict:
    rt, store = _platform()
    rel = await store.release(release_id)
    if rel is None or rel["agent_id"] != agent_id:
        raise HTTPException(404, "release not found")
    return rel


class WebhookTest(BaseModel):
    webhook: dict[str, Any] | None = None       # the settings as typed in the console; default = the draft's
    token: str | None = None                    # a bearer token / signing secret just typed (not saved yet), used for this test only
    signing_secret: str | None = None


@router.post("/agents/{agent_id}/webhook/test", dependencies=auth)
async def test_webhook(agent_id: str, body: WebhookTest) -> dict:
    """Send a sample `call.ended` to the agent's webhook URL (one attempt) and report what it answered."""
    from runtime.platform.webhook import WebhookSink, build_payload, ended_data, webhook_errors
    rt, store = _platform()
    a = await _agent(store, agent_id)
    cfg = body.webhook if body.webhook is not None else (await _working(store, a)).get("webhook")
    if errors := webhook_errors(cfg):
        raise HTTPException(422, {"errors": errors})
    if not (cfg or {}).get("url"):
        raise HTTPException(422, {"errors": ["set the webhook URL first"]})
    sample = build_payload(
        "call.ended", "test-call",
        ended_data("test-call", {"channel": "web", "language": "en", "duration_s": 42, "status": "completed",
                                 "end_reason": "agent_ended"},
                   [{"role": "agent", "text": "Hello, how can I help?"}, {"role": "user", "text": "This is a test."}]),
        agent_id=agent_id, agent_name=a.get("name"), project_id=a.get("workspace_id"))
    sink = WebhookSink(rt, delays=())
    try:
        ok, detail, attempts = await sink._deliver(cfg, sample, token=body.token or None,
                                                   signing_secret=body.signing_secret or None)
        await sink.record(agent_id, a.get("workspace_id"), "test-call", "call.ended", ok, f"test: {detail}", attempts)
    finally:
        await sink.close()
    return {"ok": ok, "detail": detail}


@router.get("/agents/{agent_id}/webhook/deliveries", dependencies=auth)
async def webhook_deliveries(agent_id: str, limit: int = 50) -> list[dict]:
    """The agent's latest webhook deliveries (newest first): event, call, delivered or not, what the receiver answered."""
    rt, store = _platform()
    await _agent(store, agent_id)
    return await store.webhook_deliveries(agent_id, max(1, min(limit, 200)))


# ---------------------------------------------------------------- phone routes


@router.get("/routes", dependencies=auth)
async def list_routes() -> list[dict]:
    rt, store = _platform()
    return await store.routes(current_project())


class RouteBody(BaseModel):
    pattern: str
    agent_id: str
    priority: int = 0
    label: str | None = None          # None keeps the current label, "" clears it


@router.put("/routes", dependencies=auth)
@audited("route.set")
async def put_route(body: RouteBody) -> dict:
    rt, store = _platform()
    await _agent(store, body.agent_id)
    if not re.fullmatch(r"\*|\+?[0-9]{2,15}\*?", body.pattern.strip()):
        raise HTTPException(422, {"errors": ["pattern: '*' (default), a number, or a prefix ending in * (e.g. 8880*)"]})
    pattern = body.pattern.strip()
    other = next((r for r in await store.all_routes()
                  if r["pattern"] == pattern and r["workspace_id"] != current_project()), None)
    if other is not None:
        raise HTTPException(409, f"{pattern!r} already reaches an agent of project {other['workspace_id']!r} — a "
                                 "number / prefix / '*' can belong to one project only")
    if body.label is not None and len(body.label) > 100:
        raise HTTPException(422, {"errors": ["label: at most 100 characters"]})
    await store.put_route(current_project(), pattern, body.agent_id, body.priority,
                          None if body.label is None else body.label.strip())
    await changed(rt, "release", route=body.pattern)
    return {"ok": True}


@router.delete("/routes", dependencies=auth)
@audited("route.deleted")
async def delete_route(pattern: str) -> dict:
    rt, store = _platform()
    if pattern == "*":
        raise HTTPException(409, "the default route can be pointed at another agent, not removed")
    await store.delete_route(current_project(), pattern)
    await changed(rt, "release", route=pattern)
    return {"ok": True}
