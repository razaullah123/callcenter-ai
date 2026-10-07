"""API keys (Hamsa parity: Create API keys / "Get project by API key").

A key belongs to one project. It is sent as `Authorization: Token hmg_…` (Hamsa's form), `Authorization: Bearer hmg_…` or `X-API-Key: hmg_…`
and then acts as an admin of that project through the same HTTP API the console uses — start calls, read call history, run batch
calls, manage agents — with these limits:

  scope `read`   GET requests only
  scope `full`   everything the console may do in the project …
  … except (any scope): manage keys, people, projects or secrets, listen to calls, and anything in another project.

Only the SHA-256 of a key is stored; the key itself is shown once, when it is made. Keys can expire, be revoked at any time, are
rate limited (`API_KEY_RATE_PER_MIN`) and every use shows in the project audit log under the key's name.

    GET    /api/api-keys               this project's keys (never the key itself)
    POST   /api/api-keys               {name, scope: read | full, expires_days?}  → the new key, once
    DELETE /api/api-keys/{id}          revoke
    GET    /api/whoami                 the project (and key) the credential in use belongs to
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from runtime.control import accounts as acc
from runtime.control.accounts import principal
from runtime.control.api import _rt, audited, auth
from runtime.platform import current_project

router = APIRouter(prefix="/api")
MAX_ACTIVE = 20
SCOPES = ("read", "full")


def _store():
    rt = _rt()
    if rt.platform is None:
        raise HTTPException(503, "the platform database is not available")
    return rt.platform


def _public(k: dict) -> dict:
    now = datetime.now(timezone.utc)
    exp, rev = k.get("expires_at"), k.get("revoked_at")
    return {"id": k["id"], "name": k["name"], "prefix": k["prefix"], "scope": k["scope"], "created_by": k.get("created_by"),
            "created_at": k.get("created_at"), "last_used_at": k.get("last_used_at"), "expires_at": exp, "revoked_at": rev,
            "status": "revoked" if rev else "expired" if exp and exp <= now else "active"}


@router.get("/api-keys", dependencies=auth)
async def list_keys() -> list[dict]:
    return [_public(k) for k in await _store().api_keys(current_project())]


class NewKey(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scope: str = "full"
    expires_days: int | None = Field(default=None, ge=1, le=3650)


@router.post("/api-keys", dependencies=auth)
@audited("apikey.created")
async def create_key(body: NewKey) -> dict:
    store = _store()
    ws = current_project()
    if body.scope not in SCOPES:
        raise HTTPException(422, f"scope must be one of: {', '.join(SCOPES)}")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "the name is empty")
    if sum(1 for k in await store.api_keys(ws) if _public(k)["status"] == "active") >= MAX_ACTIVE:
        raise HTTPException(409, f"a project may have {MAX_ACTIVE} active keys — revoke one first")
    raw = acc.new_api_key()
    row = {"id": uuid.uuid4().hex[:12], "workspace_id": ws, "name": name, "prefix": raw[:12], "key_hash": acc.token_hash(raw),
           "scope": body.scope, "created_by": principal().actor,
           "expires_at": datetime.now(timezone.utc) + timedelta(days=body.expires_days) if body.expires_days else None}
    await store.create_api_key(row)
    return {**_public(await store.api_key_by_hash(row["key_hash"])), "key": raw,
            "note": "Copy the key now — it is not shown again."}


@router.delete("/api-keys/{key_id}", dependencies=auth)
@audited("apikey.revoked")
async def revoke_key(key_id: str) -> dict:
    store = _store()
    name = next((k["name"] for k in await store.api_keys(current_project()) if k["id"] == key_id), None)
    if name is None or not await store.revoke_api_key(current_project(), key_id):
        raise HTTPException(404, "key not found (or already revoked)")
    return {"id": key_id, "name": name, "revoked": True}


@router.get("/whoami", dependencies=auth)
async def whoami() -> dict:
    """Hamsa's "get project by API key": which project the credential belongs to, and what it may do."""
    rt = _rt()
    who = principal()
    ws = current_project()
    name = next((w["name"] for w in await rt.platform.list_workspaces() if w["id"] == ws), ws) if rt.platform else ws
    out = {"kind": who.kind, "project": {"id": ws, "name": name}}
    if who.kind == "key":
        out["key"] = {"id": who.key["id"], "name": who.key["name"], "prefix": who.key["prefix"], "scope": who.key["scope"]}
    return out
