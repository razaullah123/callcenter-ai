"""Tool library (Phase 12.4): the workspace's tools and their policies, edited from the console.

A tool is an MCP tool (discovered from a connected server), a local tool (Python, e.g. find_hospital_by_name), or an
API-request tool defined entirely by its policy (source: http). Agent releases freeze the policies of the tools they
use; saving a tool publishes a new release of every agent that has it (new calls use it, calls in progress keep
theirs).

Policy fields (all optional except kind):
  kind          read | write | send
  confirm       none | affirm | readback            (write / send default: affirm)
  timeout_s, cache_ttl, idempotent
  role, args    identity.lookup / identity.send_code / identity.verify_code — caller verification driven by the harness
  hooks         named hooks from a pack (runtime.tools.hooks)
  backs         booked | confirmed | cancelled | sent — the claim a success makes true
  success_line  phrase said when the confirmed call succeeds (e.g. BOOKED_LINE)
  source        mcp | local | http;  http tools also: description, input_schema, http {method, url, headers}
"""

import copy
from typing import Any

from runtime.harness.prompts import PHRASE_NAMES
from runtime.tools.hooks import TOOL_HOOKS, load_packs

from .store import WORKSPACE, current_project

KINDS = ("read", "write", "send")
CONFIRM = ("none", "affirm", "readback")
ROLES = ("identity.lookup", "identity.send_code", "identity.verify_code")
CLAIMS = ("booked", "confirmed", "cancelled", "sent")
SOURCES = ("mcp", "local", "http")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
FIELDS = {"kind", "confirm", "timeout_s", "cache_ttl", "idempotent", "role", "args", "hooks", "backs",
          "success_line", "source", "description", "input_schema", "http"}


def validate_policy(name: str, policy: dict[str, Any], *, mcp_tools: set[str], local_tools: set[str]) -> list[str]:
    load_packs()
    e = []
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        e.append("name: letters, digits, _ and - only")
    if unknown := set(policy) - FIELDS:
        e.append(f"unknown fields: {', '.join(sorted(unknown))}")
    kind = policy.get("kind")
    if kind not in KINDS:
        e.append(f"kind must be one of {', '.join(KINDS)}")
    if policy.get("confirm", "none") not in CONFIRM:
        e.append(f"confirm must be one of {', '.join(CONFIRM)}")
    if kind == "read" and policy.get("confirm") not in (None, "none"):
        e.append("read tools need no confirmation")
    for k in ("timeout_s", "cache_ttl"):
        if k in policy and not (isinstance(policy[k], (int, float)) and policy[k] >= 0):
            e.append(f"{k} must be a number ≥ 0")
    if policy.get("role") and policy["role"] not in ROLES:
        e.append(f"role must be one of {', '.join(ROLES)}")
    if policy.get("backs") and policy["backs"] not in CLAIMS:
        e.append(f"backs must be one of {', '.join(CLAIMS)}")
    if policy.get("backs") and kind == "read":
        e.append("only write / send tools can back a claim")
    if policy.get("success_line") and policy["success_line"] not in PHRASE_NAMES:
        e.append(f"success_line must be a phrase name ({', '.join(PHRASE_NAMES)})")
    if missing := [h for h in policy.get("hooks") or [] if h not in TOOL_HOOKS]:
        e.append(f"unknown hooks: {', '.join(missing)}")
    source = policy.get("source", "mcp")
    if source not in SOURCES:
        e.append(f"source must be one of {', '.join(SOURCES)}")
    elif source == "mcp" and name not in mcp_tools:
        e.append(f"no connected MCP server provides {name}")
    elif source == "local" and name not in local_tools:
        e.append(f"no local tool named {name}")
    elif source == "http":
        http = policy.get("http") or {}
        if not str(http.get("url", "")).startswith(("http://", "https://")):
            e.append("http.url must start with http:// or https://")
        if str(http.get("method", "GET")).upper() not in METHODS:
            e.append(f"http.method must be one of {', '.join(METHODS)}")
        from runtime.tools.http_tool import auth_errors
        e += auth_errors(http.get("auth"))
        schema = policy.get("input_schema") or {"type": "object", "properties": {}}
        if not isinstance(schema, dict) or schema.get("type") != "object":
            e.append("input_schema must be a JSON schema object ({\"type\": \"object\", \"properties\": {...}})")
        if not policy.get("description"):
            e.append("description is required (the model reads it to decide when to call the tool)")
    return e


def put_in_config(cfg: dict[str, Any], name: str, group: str, policy: dict[str, Any]) -> dict[str, Any]:
    """A copy of a tools config (tools.yaml shape) with `name` under `group`."""
    out = copy.deepcopy(cfg or {})
    groups = out.setdefault("skills", {})
    for entries in groups.values():
        (entries or {}).pop(name, None)
    groups.setdefault(group, {})[name] = policy
    out["skills"] = {g: t for g, t in groups.items() if t}
    return out


def remove_from_config(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    out = copy.deepcopy(cfg or {})
    for entries in (out.get("skills") or {}).values():
        (entries or {}).pop(name, None)
    out["skills"] = {g: t for g, t in (out.get("skills") or {}).items() if t}
    return out


def tools_in(cfg: dict[str, Any]) -> dict[str, tuple[str, dict]]:
    return {n: (g, p or {}) for g, entries in (cfg.get("skills") or {}).items() for n, p in (entries or {}).items()}


async def usage(store) -> dict[str, list[str]]:
    """tool name → agents whose published release has it."""
    used: dict[str, list[str]] = {}
    for agent in await store.agents(current_project()):
        if agent.get("published_release_id"):
            bundle = (await store.release(agent["published_release_id"]))["bundle"]
            for name in tools_in(bundle.get("tools") or {}):
                used.setdefault(name, []).append(agent["id"])
    return used


async def publish_tool(store, name: str, group: str, policy: dict[str, Any], *, agents: list[str], author: str,
                       note: str) -> list[dict]:
    """New release for each agent in `agents` (and every agent that already has the tool) with this policy."""
    out = []
    for agent in await store.agents(current_project()):
        if not agent.get("published_release_id"):
            continue
        bundle = (await store.release(agent["published_release_id"]))["bundle"]
        has = name in tools_in(bundle.get("tools") or {})
        if not (has or agent["id"] in agents):
            continue
        bundle["tools"] = put_in_config(bundle.get("tools") or {}, name, group, policy)
        r = await store.add_release(agent["id"], bundle, author, note or f"tool {name}")
        out.append({"agent": agent["id"], **r})
    return out


async def unpublish_tool(store, name: str, agent_id: str, author: str) -> dict | None:
    agent = await store.agent(agent_id)
    if not agent or not agent.get("published_release_id"):
        return None
    bundle = (await store.release(agent["published_release_id"]))["bundle"]
    if name not in tools_in(bundle.get("tools") or {}):
        return None
    bundle["tools"] = remove_from_config(bundle["tools"], name)
    return {"agent": agent_id, **await store.add_release(agent_id, bundle, author, f"removed tool {name}")}
