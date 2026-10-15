"""Console API for provider connections and secrets (Phase 12.2).

    GET    /api/connections                 provider records (keys as {"secret": NAME}), schemas, which agents use each
    POST   /api/connections                 create        {kind, type, name, settings}
    PUT    /api/connections/{id}            update        {name?, settings}
    DELETE /api/connections/{id}            only when no agent's published release uses it
    POST   /api/connections/{id}/test       live check through the saved record

    GET    /api/secrets                     names, hints ("••••2f9a"), who uses them — never values
    PUT    /api/secrets/{NAME}              create / rotate   {value}
    DELETE /api/secrets/{NAME}              only when nothing references it

A plain key typed into a connection's settings is stored as a secret named <CONNECTION>_<FIELD> and replaced by a
reference. Every change reloads agents: new calls use it, calls in progress keep what they started with.
"""

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from runtime.control.api import _rt, auth, changed, probe, audited
from runtime.control.config_store import _drop_empty
from runtime.platform import WORKSPACE, current_project
from runtime.platform.bundle import CONNECTION_FIELDS, KINDS
from runtime.platform.secrets import SecretsUnavailable, refs_in
from runtime.providers import available, create, schemas

router = APIRouter(prefix="/api")


def _platform():
    rt = _rt()
    if rt.platform is None or rt.secrets is None:
        raise HTTPException(503, "the platform database is not available")
    return rt, rt.platform


async def _usage(store) -> dict[str, list[str]]:
    """provider id → agents whose published release uses it."""
    used: dict[str, list[str]] = {}
    for agent in await store.agents(current_project()):
        if not agent.get("published_release_id"):
            continue
        bundle = (await store.release(agent["published_release_id"]))["bundle"]
        for spec in (bundle.get("models") or {}).values():
            if spec.get("provider"):
                used.setdefault(spec["provider"], []).append(agent["id"])
    return used


def _view(row: dict, used: dict[str, list[str]]) -> dict:
    return {k: row.get(k) for k in ("id", "kind", "type", "name", "settings", "updated_at", "updated_by")} | \
        {"used_by": used.get(row["id"], [])}


@router.get("/connections", dependencies=auth)
async def list_connections() -> dict:
    rt, store = _platform()
    used = await _usage(store)
    return {"connections": [_view(r, used) for r in await store.providers(current_project())],
            "available": available(), "schemas": schemas(), "connection_fields": sorted(CONNECTION_FIELDS)}


class ConnectionBody(BaseModel):
    kind: str | None = None
    type: str | None = None
    name: str | None = None
    settings: dict[str, Any] = {}
    author: str = "console"


async def _checked_settings(rt, owner: str, kind: str, type_: str, settings: dict, current: dict) -> dict:
    """Plain keys → secrets; then make sure the provider accepts the settings (with secrets resolved)."""
    if choice := sorted(k for k in settings if k not in CONNECTION_FIELDS):
        raise HTTPException(422, {"errors": [f"{', '.join(choice)}: chosen per agent (Agent models), not on the "
                                             "connection"]})
    try:
        stored = await rt.secrets.externalize(owner, settings, "console", current)
        # required per-agent fields (e.g. an OpenAI-compatible model id) get a placeholder: only the account is checked
        required = (schemas().get(kind, {}).get(type_) or {}).get("required", [])
        placeholders = {r: "placeholder" for r in required if r not in CONNECTION_FIELDS}
        create(kind, type_, {**placeholders, **_drop_empty(await rt.secrets.resolve(stored))})
    except (SecretsUnavailable, LookupError, ValueError) as e:
        raise HTTPException(422, {"errors": [str(e)]}) from None
    except Exception as e:                      # pydantic validation of the provider settings
        raise HTTPException(422, {"errors": [f"invalid settings: {e}"]}) from None
    return stored


@router.post("/connections", dependencies=auth)
@audited("connection.created")
async def create_connection(body: ConnectionBody) -> dict:
    rt, store = _platform()
    if body.kind not in KINDS or body.type not in available(body.kind).get(body.kind, []):
        raise HTTPException(422, {"errors": [f"kind must be one of {KINDS} with a known type"]})
    if not body.name:
        raise HTTPException(422, {"errors": ["name is required"]})
    pid = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")
    if not pid or await store.provider(pid):
        raise HTTPException(409, "a connection with this name already exists")
    stored = await _checked_settings(rt, pid, body.kind, body.type, body.settings, {})
    await store.put_provider({"id": pid, "workspace_id": current_project(), "kind": body.kind, "type": body.type,
                              "name": body.name, "settings": stored, "updated_by": body.author})
    return {"id": pid}


@router.put("/connections/{pid}", dependencies=auth)
@audited("connection.changed")
async def update_connection(pid: str, body: ConnectionBody) -> dict:
    rt, store = _platform()
    row = await store.provider(pid)
    if row is None:
        raise HTTPException(404, "connection not found")
    stored = await _checked_settings(rt, pid, row["kind"], row["type"], body.settings, row.get("settings") or {})
    await store.put_provider({**row, "name": body.name or row["name"], "settings": stored,
                              "updated_by": body.author})
    await changed(rt, "provider", id=pid)       # new calls use it (every worker); calls in progress keep theirs
    return {"id": pid, "used_by": (await _usage(store)).get(pid, [])}


@router.delete("/connections/{pid}", dependencies=auth)
@audited("connection.deleted")
async def delete_connection(pid: str) -> dict:
    rt, store = _platform()
    if used := (await _usage(store)).get(pid):
        raise HTTPException(409, f"in use by {', '.join(used)} — point those agents at another connection first")
    await store.delete_provider(pid)
    return {"deleted": pid}


@router.post("/connections/{pid}/test", dependencies=auth)
async def test_connection(pid: str) -> dict:
    rt, store = _platform()
    row = await store.provider(pid)
    if row is None:
        raise HTTPException(404, "connection not found")
    try:
        p = create(row["kind"], row["type"], _drop_empty(await rt.secrets.resolve(row.get("settings") or {})))
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    return await probe(row["kind"], p, rt)


# ---------------------------------------------------------------- secrets


async def _secret_usage(store) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for p in await store.providers(current_project()):
        for name in refs_in(p.get("settings")):
            used.setdefault(name, []).append(f"connection {p['id']}")
    for m in await store.mcp_servers(current_project()):
        auth_ = m.get("auth") or {}
        if name := auth_.get("secret") or auth_.get("secret_env"):
            used.setdefault(name, []).append(f"MCP server {m['name']}")
    return used


@router.get("/secrets", dependencies=auth)
async def list_secrets() -> dict:
    rt, store = _platform()
    used = await _secret_usage(store)
    rows = await store.secrets(current_project())
    names = {r["name"] for r in rows}
    missing = sorted(n for n in used if n not in names)      # referenced but not stored (still read from .env)
    return {"encryption": rt.secrets.cipher.available,
            "secrets": [{**{k: r.get(k) for k in ("name", "hint", "updated_by", "updated_at")},
                         "used_by": used.get(r["name"], [])} for r in rows],
            "missing": [{"name": n, "used_by": used[n]} for n in missing]}


class SecretBody(BaseModel):
    value: str
    author: str = "console"


@router.put("/secrets/{name}", dependencies=auth)
@audited("secret.set")
async def put_secret(name: str, body: SecretBody) -> dict:
    rt, store = _platform()
    try:
        await rt.secrets.put(name, body.value.strip(), body.author)
    except (SecretsUnavailable, ValueError) as e:
        raise HTTPException(422, {"errors": [str(e)]}) from None
    await changed(rt, "secret", name=name)
    used = (await _secret_usage(store)).get(name, [])
    note = "MCP servers pick up a new token on the next server restart" if any("MCP" in u for u in used) else None
    return {"name": name, "used_by": used, "note": note}


@router.delete("/secrets/{name}", dependencies=auth)
@audited("secret.deleted")
async def delete_secret(name: str) -> dict:
    rt, store = _platform()
    if used := (await _secret_usage(store)).get(name):
        raise HTTPException(409, f"in use by {', '.join(used)}")
    await store.delete_secret(current_project(), name)
    return {"deleted": name}
