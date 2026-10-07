"""Public page and embed widget of an agent (console side): publish a link anyone can talk to, change how it looks and what
visitors may spend, unpublish it.

    GET    /api/agents/{id}/share     the link and its settings (published: false when there is none)
    PUT    /api/agents/{id}/share     publish / update   {settings}   (the agent needs a published release)
    DELETE /api/agents/{id}/share     unpublish: the link stops working at once, calls on it are ended

The public side (page, embed script, voice socket) is runtime/server/public.py.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from runtime.control.agents_api import _agent, _platform
from runtime.control.api import audited, auth
from runtime.platform import current_project
from runtime.platform.share import LIMITER, ShareSettings, new_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class ShareBody(BaseModel):
    settings: dict = {}


def _view(row: dict | None) -> dict:
    defaults = ShareSettings().model_dump()
    if not row:
        return {"published": False, "token": None, "path": None, "settings": defaults, "defaults": defaults}
    settings = ShareSettings.model_validate(row.get("settings") or {}).model_dump()
    return {"published": True, "token": row["token"], "path": f"/p/{row['token']}", "settings": settings, "defaults": defaults,
            "updated_at": row.get("updated_at")}


@router.get("/agents/{agent_id}/share", dependencies=auth)
async def get_share(agent_id: str) -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    return _view(await store.share(agent_id))


@router.put("/agents/{agent_id}/share", dependencies=auth)
@audited("share.published")
async def put_share(agent_id: str, body: ShareBody) -> dict:
    rt, store = _platform()
    agent = await _agent(store, agent_id)
    if not agent.get("published_release_id"):
        raise HTTPException(409, "publish a release of this agent first — the public link starts the published version")
    try:
        settings = ShareSettings.model_validate(body.settings or {})
    except ValidationError as e:
        raise HTTPException(422, "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())) from None
    row = await store.share(agent_id) or {"agent_id": agent_id, "workspace_id": current_project(), "token": new_token()}
    await store.put_share({**row, "settings": settings.model_dump()})
    return _view(await store.share(agent_id))


@router.delete("/agents/{agent_id}/share", dependencies=auth)
@audited("share.unpublished")
async def delete_share(agent_id: str) -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    row = await store.share(agent_id)
    await store.delete_share(agent_id)
    ended = 0
    for call in LIMITER.calls_of(row["token"]) if row else []:           # takes effect immediately
        try:
            await call.end_from_console()
            ended += 1
        except Exception as e:                                          # noqa: BLE001
            log.warning("could not end a public call: %r", e)
    return {"published": False, "ended_calls": ended}
