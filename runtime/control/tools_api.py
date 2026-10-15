"""Console API for the tool library and MCP servers (Phase 12.4).

    GET    /api/tool-library                      servers (+ live status), library tools (+ which agents use them),
                                                  discovered tools not in the library, and the choices a policy has
    PUT    /api/tool-library/{name}               add / update a tool's policy  {group, policy, agents?, note?}
                                                  → new release of every agent that has it (+ `agents`)
    DELETE /api/tool-library/{name}?agent=ID      remove it from one agent (new release) — refused while a skill uses it
    DELETE /api/tool-library/{name}               drop it from the library (only when no agent has it)
    POST   /api/tool-library/{name}/test          run a READ tool with sample args (never write / send tools);
                                                  API tools take `override` {url, method, timeout_s, headers} for
                                                  this test only

    POST   /api/mcp-servers                       add a server        {name, url, transport?, auth?}
    PUT    /api/mcp-servers/{id}                  update it
    DELETE /api/mcp-servers/{id}                  remove it (refused while agents use its tools)
    POST   /api/mcp-servers/discover              list the tools of a server (saved or not) without saving
"""

import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from runtime.control.api import _rt, auth, changed, audited
from runtime.harness.prompts import PHRASE_NAMES
from runtime.platform import WORKSPACE, current_project, toollib
from runtime.tools.hooks import TOOL_HOOKS, load_packs
from runtime.tools.local import call_local, local_schemas
from runtime.tools.types import ToolContext

router = APIRouter(prefix="/api")


def _platform():
    rt = _rt()
    if rt.platform is None:
        raise HTTPException(503, "the platform database is not available")
    return rt, rt.platform


def _schemas(rt) -> dict[str, dict]:
    return rt.backend.schemas() if hasattr(rt.backend, "schemas") else {}


async def _project_mcp(rt, store):
    """(backend view, status, schemas) of the current project's MCP servers only: another project's servers and tools are
    not shown here and never answer this project's calls."""
    names = {m["name"] for m in await store.mcp_servers(current_project())}
    backend = rt.backend.scoped(names) if hasattr(rt.backend, "scoped") else rt.backend
    status = {n: v for n, v in (backend.status() if hasattr(backend, "status") else {}).items() if n in names}
    return backend, status, (backend.schemas() if hasattr(backend, "schemas") else {})


@router.get("/tool-library", dependencies=auth)
async def library() -> dict:
    rt, store = _platform()
    load_packs()
    used = await toollib.usage(store)
    _, status, mcp_schemas = await _project_mcp(rt, store)
    server_of = {t: name for name, st in status.items() for t in st.get("tools", [])}
    local = local_schemas()
    rows = await store.tools(current_project())
    names = {r["name"] for r in rows}
    tools = []
    for r in rows:
        schema = (r["policy"] if r["source"] in ("http", "web") else mcp_schemas.get(r["name"]) or local.get(r["name"])) or {}
        tools.append({"name": r["name"], "group": r["grp"], "source": r["source"], "policy": r["policy"],
                      "description": (schema.get("description") or "")[:600],
                      "input_schema": schema.get("input_schema"), "server": server_of.get(r["name"]),
                      "available": r["source"] in ("http", "web") or r["name"] in mcp_schemas or r["name"] in local,
                      "used_by": used.get(r["name"], [])})
    discovered = [{"name": n, "server": server_of.get(n), "description": (s.get("description") or "")[:300],
                   "input_schema": s.get("input_schema")}
                  for n, s in sorted(mcp_schemas.items()) if n not in names]
    servers = [{**{k: m.get(k) for k in ("id", "name", "url", "transport", "auth", "enabled")},
                "status": status.get(m["name"], {"connected": False, "tools": [], "error": None})}
               for m in await store.mcp_servers(current_project())]
    return {"servers": servers, "tools": tools, "discovered": discovered,
            "choices": {"kinds": toollib.KINDS, "confirm": toollib.CONFIRM, "roles": toollib.ROLES,
                        "claims": toollib.CLAIMS, "sources": toollib.SOURCES, "methods": toollib.METHODS,
                        "phrases": PHRASE_NAMES,
                        "hooks": {n: sorted(ph) for n, ph in sorted(TOOL_HOOKS.items())},
                        "groups": sorted({r["grp"] for r in rows})},
            "agents": [{"id": a["id"], "name": a["name"]} for a in await store.agents(current_project())]}


# ---------------------------------------------------------------- Hamsa's shape of the same list
# GET /v2/voice-agents/web-tool/list?projectId=&skip=&take=  ->  {success, message, data: {items, total, filtered}}
# (skip is the page number, from 1). The Tools page of the console reads /api/tool-library for the policies; this is the
# list the way Hamsa returns it, for anything written against Hamsa's API.

_TYPES = {"http": "FUNCTION", "mcp": "MCP", "local": "LOCAL", "web": "WEB"}


def hamsa_tool(row: dict, project: str, schema: dict, server_url: str | None) -> dict:
    import uuid
    policy = row.get("policy") or {}
    http = policy.get("http") or {}
    source = row.get("source") or policy.get("source") or "mcp"
    key = f"{project}/{row['name']}"
    settings = {"httpHeaders": http.get("headers") or {}, "pathParameters": None,
                "serverUrl": http.get("url") if source == "http" else server_url,
                "timeout": policy.get("timeout_s"), "authToken": None,
                "methodType": http.get("method") if source == "http" else None, "onHoldMusic": False}
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)), "persistentId": uuid.uuid5(uuid.NAMESPACE_URL, "p/" + key).hex[:24],
            "version": 1, "name": row["name"], "type": _TYPES.get(source, "FUNCTION"), "userId": None, "projectId": project,
            "isActive": policy.get("enabled") is not False, "async": bool(policy.get("async")),
            "description": (policy.get("description") or schema.get("description") or "")[:1000],
            "collectionId": None, "toolSettings": settings,
            "params": policy.get("input_schema") or schema.get("input_schema") or {"type": "object", "required": [], "properties": {}},
            "messages": []}


@router.get("/voice-agents/web-tool/list", dependencies=auth)
async def hamsa_tool_list(skip: int = 1, take: int = 10, search: str | None = None, q: str | None = None,
                          returnUncategorized: bool = False, collection: str | None = None, status: str | None = None) -> dict:
    """`collection` (a group) and `status` (active | inactive) filter further; `matched` counts the tools that match
    across all pages (for the pager)."""
    rt, store = _platform()
    project = current_project()
    _, status, mcp_schemas = await _project_mcp(rt, store)
    server_of = {t: name for name, st in status.items() for t in st.get("tools", [])}
    urls = {m["name"]: m.get("url") for m in await store.mcp_servers(project)}
    local = local_schemas()
    needle = (search or q or "").strip().lower()
    rows = sorted(await store.tools(project), key=lambda r: r["name"].lower())
    items = []
    for r in rows:
        schema = mcp_schemas.get(r["name"]) or local.get(r["name"]) or {}
        t = hamsa_tool(r, project, schema, urls.get(server_of.get(r["name"], "")))
        if needle and needle not in f"{r['grp']} {t['name']} {t['description']}".lower():
            continue
        if (collection and r["grp"] != collection) or (status == "active" and not t["isActive"]) \
                or (status == "inactive" and t["isActive"]):
            continue
        items.append(t)
    take = max(1, min(take, 100))
    page = items[(max(skip, 1) - 1) * take:][:take]
    return {"success": True, "message": "success",
            "data": {"items": page, "total": len(rows), "filtered": len(page), "matched": len(items)}}


@router.get("/voice-agents/collections/list", dependencies=auth)
async def hamsa_collections(skip: int = 1, take: int = 100) -> dict:
    """Hamsa's tool collections: here, the groups the Tools page files tools under."""
    rt, store = _platform()
    groups: dict[str, int] = {}
    for r in await store.tools(current_project()):
        groups[r["grp"]] = groups.get(r["grp"], 0) + 1
    items = [{"id": g, "name": g, "projectId": current_project(), "toolsCount": n} for g, n in sorted(groups.items())]
    take = max(1, min(take, 100))
    page = items[(max(skip, 1) - 1) * take:][:take]
    return {"success": True, "message": "success", "data": {"items": page, "total": len(items), "filtered": len(page)}}


class ToolBody(BaseModel):
    group: str
    policy: dict[str, Any]
    agents: list[str] = []
    note: str = ""
    author: str = "console"


@router.put("/tool-library/{name}", dependencies=auth)
@audited("tool.changed")
async def put_tool(name: str, body: ToolBody) -> dict:
    rt, store = _platform()
    policy = {k: v for k, v in body.policy.items() if v not in (None, "", [], {})}
    if errors := toollib.validate_policy(name, policy, mcp_tools=set((await _project_mcp(rt, store))[2]), local_tools=set(local_schemas())):
        raise HTTPException(422, {"errors": errors})
    if not re.fullmatch(r"[a-z][a-z0-9_]*", body.group):
        raise HTTPException(422, {"errors": ["group: lower-case letters, digits and _ (usually the skill name)"]})
    known = {r["name"] for r in await store.tools(current_project())}
    await store.put_tools(current_project(), [{"name": name, "grp": body.group, "source": policy.get("source", "mcp"),
                                       "policy": policy}])
    agents = body.agents or ([] if name in known else [rt.agent.agent_id])     # a new tool joins the default agent
    releases = await toollib.publish_tool(store, name, body.group, policy, agents=agents, author=body.author,
                                          note=body.note)
    await changed(rt, "tool", name=name)
    return {"name": name, "releases": releases}


@router.delete("/tool-library/{name}", dependencies=auth)
@audited("tool.removed")
async def delete_tool(name: str, agent: str | None = None) -> dict:
    rt, store = _platform()
    if agent:
        loaded = await rt.agent_for_call(agent_id=agent)
        users = [s.name for s in loaded.skills.skills.values()
                 if name in ((s.flow.all_tools() if s.flow else set()) | set(s.extra_tools) | set(s.hidden_tools))]
        if users:
            raise HTTPException(409, f"skills of {agent} use {name}: {', '.join(users)} — change them first")
        r = await toollib.unpublish_tool(store, name, agent, "console")
        if r is None:
            raise HTTPException(404, f"{agent} doesn't have {name}")
        await changed(rt, "tool", name=name)
        return {"removed_from": agent, **r}
    if used := (await toollib.usage(store)).get(name):
        raise HTTPException(409, f"in use by {', '.join(used)} — remove it from those agents first")
    await store.delete_tool(current_project(), name)
    return {"deleted": name}


class TestBody(BaseModel):
    args: dict[str, Any] = {}
    override: dict[str, Any] = {}      # API tools: url / method / timeout_s / headers for this test only


@router.post("/tool-library/{name}/test", dependencies=auth)
async def test_tool(name: str, body: TestBody) -> dict:
    """Runs READ tools only: a test must never book, cancel or send anything."""
    rt, store = _platform()
    row = next((r for r in await store.tools(current_project()) if r["name"] == name), None)
    if row is None:
        raise HTTPException(404, "tool not in the library")
    policy = row["policy"]
    if row["source"] == "web":
        raise HTTPException(400, "a web tool runs in the visitor's browser, so it can't be tested from here — open the Share page's "
                                 "test page (or your own site) and talk to the agent")
    if policy.get("kind", "read") != "read":
        raise HTTPException(400, "only read tools can be tested here (write / send tools would really act)")
    args = dict(body.args)
    catalog = rt.agent.executor.catalog
    if (t := catalog.get(name)) is not None:          # parameters the platform injects in a call (language)
        for param in (t.input_schema.get("properties") or {}):
            if catalog.injected_param(param) == "language_id":
                args.setdefault(param, 1)
    body.args = args
    spec = dict(policy.get("http") or {})
    if row["source"] == "http":
        for k in ("url", "method", "headers"):
            if body.override.get(k):
                spec[k] = body.override[k]
        if not str(spec.get("url", "")).startswith(("http://", "https://")):
            raise HTTPException(422, "url must start with http:// or https://")
    try:
        timeout = min(60.0, float(body.override.get("timeout_s") or policy.get("timeout_s", 10)))
    except (TypeError, ValueError):
        raise HTTPException(422, "timeout_s must be a number") from None
    t0 = time.perf_counter()
    try:
        if row["source"] == "http":
            from runtime.tools.http_tool import call_http
            spec = await rt.secrets.resolve(spec) if rt.secrets else spec
            ok, data = await call_http(spec, body.args, timeout)
        elif row["source"] == "local":
            ok, data = True, await call_local(name, body.args, ToolContext(call_id="console-test"))
        else:
            ok, data = await (await _project_mcp(rt, store))[0].call(name, body.args, timeout)
    except Exception as e:
        return {"ok": False, "error": repr(e)[:400], "ms": round((time.perf_counter() - t0) * 1000)}
    return {"ok": ok, "ms": round((time.perf_counter() - t0) * 1000), "data": _clip(data)}


def _clip(data: Any, limit: int = 6000) -> Any:
    import json
    text = json.dumps(data, ensure_ascii=False, default=str)
    return data if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------- MCP servers


class ServerBody(BaseModel):
    name: str
    url: str
    transport: str = "streamable_http"
    auth: dict[str, Any] = {}          # {header, scheme, secret}
    enabled: bool = True


def _check_server(body: ServerBody) -> None:
    errors = []
    if not re.fullmatch(r"[a-z][a-z0-9_]*", body.name):
        errors.append("name: lower-case letters, digits and _")
    if not body.url.startswith(("http://", "https://")):
        errors.append("url must start with http:// or https://")
    if body.transport != "streamable_http":
        errors.append("transport: only streamable_http is supported")
    if errors:
        raise HTTPException(422, {"errors": errors})


async def _store_token(rt, body: ServerBody) -> None:
    """A token pasted into the form is stored as an encrypted secret; the server keeps its name."""
    token = body.auth.pop("token", None)
    if token:
        if rt.secrets is None or not rt.secrets.cipher.available:
            raise HTTPException(422, {"errors": ["MASTER_KEY is not set — the token can't be stored"]})
        name = f"MCP_{body.name.upper()}_TOKEN"
        await rt.secrets.put(name, token, "console")
        body.auth["secret"] = name


async def _server_cfg(rt, body: ServerBody) -> dict:
    from runtime.platform.loader import mcp_config
    return (await mcp_config([{"name": body.name, **body.model_dump()}], rt.settings, rt.secrets))[body.name]


@router.post("/mcp-servers/discover", dependencies=auth)
async def discover(body: ServerBody) -> dict:
    rt, _ = _platform()
    _check_server(body)
    from runtime.tools.mcp_client import discover as list_tools
    cfg = await _server_cfg(rt, body)
    if token := body.auth.get("token"):          # not saved yet: use the pasted token as is
        cfg["headers"][body.auth.get("header", "Authorization")] = f"{body.auth.get('scheme', 'Bearer')} {token}".strip()
    try:
        tools = await list_tools(body.name, cfg)
    except Exception as e:
        return {"ok": False, "error": repr(e)[:300]}
    return {"ok": True, "tools": [{"name": t["name"], "description": t["description"][:300]} for t in tools]}


@router.post("/mcp-servers", dependencies=auth)
@audited("mcp.added")
async def add_server(body: ServerBody) -> dict:
    rt, store = _platform()
    _check_server(body)
    for w in await store.list_workspaces():              # names are unique across projects (one MCP pool)
        if any(m["name"] == body.name or m["id"] == body.name for m in await store.mcp_servers(w["id"])):
            raise HTTPException(409, "a server with this name exists" + ("" if w["id"] == current_project()
                                                                         else f" in project {w['id']!r}"))
    await _store_token(rt, body)
    await store.put_mcp_server({"id": body.name, "workspace_id": current_project(), **body.model_dump()})
    await changed(rt, "mcp", server=body.name)
    return {"id": body.name, "status": rt.mcp_status().get(body.name)}


@router.put("/mcp-servers/{sid}", dependencies=auth)
@audited("mcp.changed")
async def update_server(sid: str, body: ServerBody) -> dict:
    rt, store = _platform()
    _check_server(body)
    if not any(m["id"] == sid for m in await store.mcp_servers(current_project())):
        raise HTTPException(404, "server not found")
    await _store_token(rt, body)
    await store.put_mcp_server({"id": sid, "workspace_id": current_project(), **body.model_dump()})
    await changed(rt, "mcp", server=sid)
    return {"id": sid, "status": rt.mcp_status().get(body.name)}


@router.delete("/mcp-servers/{sid}", dependencies=auth)
@audited("mcp.deleted")
async def delete_server(sid: str) -> dict:
    rt, store = _platform()
    server = next((m for m in await store.mcp_servers(current_project()) if m["id"] == sid), None)
    if server is None:
        raise HTTPException(404, "server not found")
    its_tools = set(rt.mcp_status().get(server["name"], {}).get("tools", []))
    used = await toollib.usage(store)
    if busy := sorted(t for t in its_tools if used.get(t)):
        raise HTTPException(409, f"agents use its tools ({', '.join(busy[:5])}…) — remove them first")
    await store.delete_mcp_server(sid)
    await changed(rt, "mcp", server=sid)
    return {"deleted": sid}
