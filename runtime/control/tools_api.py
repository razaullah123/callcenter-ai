"""Console API for the tool library and MCP servers (Phase 12.4).

    GET    /api/tool-library                      servers (+ live status), library tools (+ which agents use them),
                                                  discovered tools not in the library, and the choices a policy has
    PUT    /api/tool-library/{name}               add / update a tool's policy  {group, policy, agents?, note?}
                                                  → new release of every agent that has it (+ `agents`)
    DELETE /api/tool-library/{name}?agent=ID      remove it from one agent (new release) — refused while a skill uses it
    DELETE /api/tool-library/{name}               drop it from the library (only when no agent has it)
    POST   /api/tool-library/{name}/test          run a READ tool with sample args (never write / send tools)

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

from runtime.control.api import _rt, auth, changed
from runtime.harness.prompts import PHRASE_NAMES
from runtime.platform import WORKSPACE, toollib
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


@router.get("/tool-library", dependencies=auth)
async def library() -> dict:
    rt, store = _platform()
    load_packs()
    used = await toollib.usage(store)
    status = rt.mcp_status() if hasattr(rt, "mcp_status") else {}
    server_of = {t: name for name, st in status.items() for t in st.get("tools", [])}
    mcp_schemas, local = _schemas(rt), local_schemas()
    rows = await store.tools(WORKSPACE)
    names = {r["name"] for r in rows}
    tools = []
    for r in rows:
        schema = (r["policy"] if r["source"] == "http" else mcp_schemas.get(r["name"]) or local.get(r["name"])) or {}
        tools.append({"name": r["name"], "group": r["grp"], "source": r["source"], "policy": r["policy"],
                      "description": (schema.get("description") or "")[:600],
                      "input_schema": schema.get("input_schema"), "server": server_of.get(r["name"]),
                      "available": r["source"] == "http" or r["name"] in mcp_schemas or r["name"] in local,
                      "used_by": used.get(r["name"], [])})
    discovered = [{"name": n, "server": server_of.get(n), "description": (s.get("description") or "")[:300],
                   "input_schema": s.get("input_schema")}
                  for n, s in sorted(mcp_schemas.items()) if n not in names]
    servers = [{**{k: m.get(k) for k in ("id", "name", "url", "transport", "auth", "enabled")},
                "status": status.get(m["name"], {"connected": False, "tools": [], "error": None})}
               for m in await store.mcp_servers(WORKSPACE)]
    return {"servers": servers, "tools": tools, "discovered": discovered,
            "choices": {"kinds": toollib.KINDS, "confirm": toollib.CONFIRM, "roles": toollib.ROLES,
                        "claims": toollib.CLAIMS, "sources": toollib.SOURCES, "methods": toollib.METHODS,
                        "phrases": PHRASE_NAMES,
                        "hooks": {n: sorted(ph) for n, ph in sorted(TOOL_HOOKS.items())},
                        "groups": sorted({r["grp"] for r in rows})},
            "agents": [{"id": a["id"], "name": a["name"]} for a in await store.agents(WORKSPACE)]}


class ToolBody(BaseModel):
    group: str
    policy: dict[str, Any]
    agents: list[str] = []
    note: str = ""
    author: str = "console"


@router.put("/tool-library/{name}", dependencies=auth)
async def put_tool(name: str, body: ToolBody) -> dict:
    rt, store = _platform()
    policy = {k: v for k, v in body.policy.items() if v not in (None, "", [], {})}
    if errors := toollib.validate_policy(name, policy, mcp_tools=set(_schemas(rt)), local_tools=set(local_schemas())):
        raise HTTPException(422, {"errors": errors})
    if not re.fullmatch(r"[a-z][a-z0-9_]*", body.group):
        raise HTTPException(422, {"errors": ["group: lower-case letters, digits and _ (usually the skill name)"]})
    known = {r["name"] for r in await store.tools(WORKSPACE)}
    await store.put_tools(WORKSPACE, [{"name": name, "grp": body.group, "source": policy.get("source", "mcp"),
                                       "policy": policy}])
    agents = body.agents or ([] if name in known else [rt.agent.agent_id])     # a new tool joins the default agent
    releases = await toollib.publish_tool(store, name, body.group, policy, agents=agents, author=body.author,
                                          note=body.note)
    await changed(rt, "tool", name=name)
    return {"name": name, "releases": releases}


@router.delete("/tool-library/{name}", dependencies=auth)
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
    await store.delete_tool(WORKSPACE, name)
    return {"deleted": name}


class TestBody(BaseModel):
    args: dict[str, Any] = {}


@router.post("/tool-library/{name}/test", dependencies=auth)
async def test_tool(name: str, body: TestBody) -> dict:
    """Runs READ tools only: a test must never book, cancel or send anything."""
    rt, store = _platform()
    row = next((r for r in await store.tools(WORKSPACE) if r["name"] == name), None)
    if row is None:
        raise HTTPException(404, "tool not in the library")
    policy = row["policy"]
    if policy.get("kind", "read") != "read":
        raise HTTPException(400, "only read tools can be tested here (write / send tools would really act)")
    args = dict(body.args)
    catalog = rt.agent.executor.catalog
    if (t := catalog.get(name)) is not None:          # parameters the platform injects in a call (language)
        for param in (t.input_schema.get("properties") or {}):
            if catalog.injected_param(param) == "language_id":
                args.setdefault(param, 1)
    body.args = args
    t0 = time.perf_counter()
    try:
        timeout = float(policy.get("timeout_s", 10))
        if row["source"] == "http":
            from runtime.tools.http_tool import call_http
            spec = await rt.secrets.resolve(policy.get("http") or {}) if rt.secrets else policy.get("http") or {}
            ok, data = await call_http(spec, body.args, timeout)
        elif row["source"] == "local":
            ok, data = True, await call_local(name, body.args, ToolContext(call_id="console-test"))
        else:
            ok, data = await rt.backend.call(name, body.args, timeout)
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
async def add_server(body: ServerBody) -> dict:
    rt, store = _platform()
    _check_server(body)
    if any(m["name"] == body.name for m in await store.mcp_servers(WORKSPACE)):
        raise HTTPException(409, "a server with this name exists")
    await _store_token(rt, body)
    await store.put_mcp_server({"id": body.name, "workspace_id": WORKSPACE, **body.model_dump()})
    await changed(rt, "mcp", server=body.name)
    return {"id": body.name, "status": rt.mcp_status().get(body.name)}


@router.put("/mcp-servers/{sid}", dependencies=auth)
async def update_server(sid: str, body: ServerBody) -> dict:
    rt, store = _platform()
    _check_server(body)
    if not any(m["id"] == sid for m in await store.mcp_servers(WORKSPACE)):
        raise HTTPException(404, "server not found")
    await _store_token(rt, body)
    await store.put_mcp_server({"id": sid, "workspace_id": WORKSPACE, **body.model_dump()})
    await changed(rt, "mcp", server=sid)
    return {"id": sid, "status": rt.mcp_status().get(body.name)}


@router.delete("/mcp-servers/{sid}", dependencies=auth)
async def delete_server(sid: str) -> dict:
    rt, store = _platform()
    server = next((m for m in await store.mcp_servers(WORKSPACE) if m["id"] == sid), None)
    if server is None:
        raise HTTPException(404, "server not found")
    its_tools = set(rt.mcp_status().get(server["name"], {}).get("tools", []))
    used = await toollib.usage(store)
    if busy := sorted(t for t in its_tools if used.get(t)):
        raise HTTPException(409, f"agents use its tools ({', '.join(busy[:5])}…) — remove them first")
    await store.delete_mcp_server(sid)
    await changed(rt, "mcp", server=sid)
    return {"deleted": sid}
