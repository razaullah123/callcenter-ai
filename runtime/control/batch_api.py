"""Batch (outbound) calls API (Hamsa parity) — see runtime/platform/batch.py for how calls are placed.

    GET / PUT / DELETE /api/outbound-numbers[/{number}]   numbers an agent may dial from (+ their dial URL)
    POST   /api/batch-calls/validate      {csv_base64 | rows}    accepted / rejected recipients (rejected ones can be fixed)
    POST   /api/batch-calls               {name, agent_id, from_number, rows, config}
    GET    /api/batch-calls               list with progress
    GET    /api/batch-calls/{id}          one batch call (+ counts)
    PATCH  /api/batch-calls/{id}          {name}
    DELETE /api/batch-calls/{id}
    POST   /api/batch-calls/{id}/actions/{pause|resume|cancel|retry}
    GET    /api/batch-calls/{id}/recipients?status=&q=&limit=&offset=
    POST   /api/batch-calls/{id}/recipients      {rows | csv_base64}  more recipients while it isn't finished
    DELETE /api/batch-calls/{id}/recipients/{rid}
    POST   /api/outbound/status           {outbound_token, status: busy | no_answer | failed, reason?}   (the PBX; no sign-in)
"""

import base64
import binascii
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from runtime.control.accounts import principal
from runtime.control.api import _rt, audited, auth
from runtime.platform import batch, current_project

router = APIRouter(prefix="/api")


def _store():
    rt = _rt()
    if rt.platform is None:
        raise HTTPException(503, "the platform database is not available")
    return rt, rt.platform


async def _batch(store, batch_id: str) -> dict:
    b = await store.batch(batch_id)
    if b is None or b["workspace_id"] != current_project():
        raise HTTPException(404, "batch call not found")
    return b


# ---------------------------------------------------------------- outbound numbers

def _secret_name(number: str) -> str:
    return "DIAL_TOKEN_" + (re.sub(r"\D", "", number) or "X")


@router.get("/outbound-numbers", dependencies=auth)
async def outbound_numbers() -> list[dict]:
    _, store = _store()
    return [{"number": n["number"], "label": n.get("label"), "dial_url": n["dial_url"], "has_token": bool(n.get("dial_auth")),
             "created_at": n.get("created_at")}
            for n in await store.outbound_numbers(current_project())]


class NumberBody(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    label: str | None = Field(default=None, max_length=100)
    dial_url: str = Field(min_length=1, max_length=2048)
    dial_token: str | None = Field(default=None, max_length=500)       # kept as an encrypted secret


@router.put("/outbound-numbers", dependencies=auth)
@audited("outbound.number")
async def put_outbound_number(body: NumberBody) -> dict:
    rt, store = _store()
    ws = current_project()
    number, errors = batch.check_phone(body.number, ignore_e164=True)
    if errors:
        raise HTTPException(422, "number: " + errors[0])
    if not body.dial_url.startswith(("http://", "https://")):
        raise HTTPException(422, "dial_url must start with http:// or https://")
    old = next((n for n in await store.outbound_numbers(ws) if n["number"] == number), None)
    auth_ref = (old or {}).get("dial_auth")
    if body.dial_token:
        if rt.secrets is None:
            raise HTTPException(503, "secrets are not available")
        try:
            await rt.secrets.put(_secret_name(number), body.dial_token, principal().actor)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        auth_ref = {"secret": _secret_name(number)}
    await store.put_outbound_number({"workspace_id": ws, "number": number, "label": (body.label or "").strip() or None,
                                     "dial_url": body.dial_url.strip(), "dial_auth": auth_ref})
    return {"number": number}


@router.delete("/outbound-numbers/{number}", dependencies=auth)
@audited("outbound.number_removed")
async def delete_outbound_number(number: str) -> dict:
    _, store = _store()
    ws = current_project()
    if any(b["from_number"] == number and b["status"] in ("scheduled", "running", "paused") for b in await store.batches(ws)):
        raise HTTPException(409, "a batch call that isn't finished uses this number")
    await store.delete_outbound_number(ws, number)
    return {"number": number}


# ---------------------------------------------------------------- recipients

class RowsBody(BaseModel):
    csv_base64: str | None = None
    rows: list[dict[str, Any]] | None = Field(default=None, max_length=batch.MAX_RECIPIENTS)


def _rows_of(body: RowsBody) -> list[dict]:
    if body.csv_base64:
        try:
            raw = base64.b64decode(body.csv_base64, validate=False)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "the file data isn't valid base64") from None
        try:
            return batch.parse_csv(raw)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
    if body.rows:
        return body.rows
    raise HTTPException(422, "send a CSV file or rows")


def _checked(rows: list[dict]) -> dict:
    out = batch.check_rows(rows)
    out["variables"] = sorted({k for r in out["accepted"] + out["rejected"] for k in r["variables"]})
    return out


@router.post("/batch-calls/validate", dependencies=auth)
async def validate_rows(body: RowsBody) -> dict:
    _store()
    return _checked(_rows_of(body))


# ---------------------------------------------------------------- batch calls

class ConfigBody(BaseModel):
    send_type: str = "now"
    scheduled_at: str | None = None            # local date-time in `timezone` (2026-10-08T09:00) or with an offset
    timezone: str = "Asia/Riyadh"
    window_start: str = "09:00"
    window_end: str = "18:00"
    days: list[int] = [6, 0, 1, 2, 3]       # Sunday-Thursday (the Saudi working week); 0 = Monday … 6 = Sunday


class CreateBody(RowsBody):
    name: str = Field(min_length=1, max_length=batch.MAX_NAME)
    agent_id: str
    from_number: str
    config: ConfigBody = ConfigBody()


async def _summary(store, b: dict, agent_names: dict[str, str]) -> dict:
    counts = await store.recipient_counts(b["id"])
    total = sum(counts.values())
    done = sum(counts.get(s, 0) for s in ("completed", "failed", "no_answer"))
    return {**b, "agent_name": agent_names.get(b["agent_id"], b["agent_id"]), "counts": counts, "total": total,
            "progress": round(100 * done / total) if total else 0}


@router.post("/batch-calls", dependencies=auth)
@audited("batch.created")
async def create_batch(body: CreateBody) -> dict:
    rt, store = _store()
    ws = current_project()
    agent = await store.agent(body.agent_id)
    if agent is None or agent.get("workspace_id", ws) != ws:
        raise HTTPException(404, "agent not found")
    if not agent.get("published_release_id"):
        raise HTTPException(409, "publish the agent first — batch calls use its published version")
    if not any(n["number"] == body.from_number for n in await store.outbound_numbers(ws)):
        raise HTTPException(422, "from_number is not an outbound number — add it under Phone numbers → Outbound numbers")
    cfg = body.config.model_dump()
    if errors := batch.config_errors(cfg):
        raise HTTPException(422, "; ".join(errors))
    checked = batch.check_rows(_rows_of(body))
    if checked["rejected"]:
        raise HTTPException(422, f"{len(checked['rejected'])} recipient(s) are invalid — fix or remove them first")
    if not checked["accepted"]:
        raise HTTPException(422, "add at least one valid recipient")
    batch_id = batch.new_id()
    await store.put_batch({"id": batch_id, "workspace_id": ws, "name": body.name.strip(), "agent_id": body.agent_id,
                           "from_number": body.from_number, "status": "scheduled", "config": cfg,
                           "created_by": principal().actor})
    await store.add_recipients(batch_id, checked["accepted"])
    return {"id": batch_id, "recipients": len(checked["accepted"]), "duplicates": checked["duplicates"]}


@router.get("/batch-calls", dependencies=auth)
async def list_batches() -> list[dict]:
    _, store = _store()
    names = {a["id"]: a["name"] for a in await store.agents(current_project())}
    return [await _summary(store, b, names) for b in await store.batches(current_project())]


@router.get("/batch-calls/{batch_id}", dependencies=auth)
async def get_batch(batch_id: str) -> dict:
    _, store = _store()
    b = await _batch(store, batch_id)
    names = {a["id"]: a["name"] for a in await store.agents(current_project())}
    return await _summary(store, b, names)


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=batch.MAX_NAME)


@router.patch("/batch-calls/{batch_id}", dependencies=auth)
@audited("batch.renamed")
async def rename_batch(batch_id: str, body: RenameBody) -> dict:
    _, store = _store()
    await _batch(store, batch_id)
    if not body.name.strip():
        raise HTTPException(422, "the name is empty")
    await store.put_batch({"id": batch_id, "name": body.name.strip()})
    return {"id": batch_id, "name": body.name.strip()}


@router.delete("/batch-calls/{batch_id}", dependencies=auth)
@audited("batch.deleted")
async def delete_batch(batch_id: str) -> dict:
    _, store = _store()
    b = await _batch(store, batch_id)
    await store.delete_batch(batch_id)
    return {"deleted": batch_id, "name": b["name"]}


ACTIONS = {                      # action → (statuses it applies to, new status)
    "pause": (("running",), "paused"),
    "resume": (("paused",), "running"),
    "cancel": (("scheduled", "running", "paused"), "cancelled"),
}


@router.post("/batch-calls/{batch_id}/actions/{action}", dependencies=auth)
@audited("batch.action")
async def batch_action(batch_id: str, action: str) -> dict:
    _, store = _store()
    b = await _batch(store, batch_id)
    if action == "retry":
        if b["status"] not in ("completed", "failed", "cancelled"):
            raise HTTPException(409, f"a {b['status']} batch call can't be retried")
        counts = await store.recipient_counts(batch_id)
        if not (counts.get("failed") or counts.get("no_answer") or counts.get("pending")):
            raise HTTPException(409, "nothing to retry — every call was completed")
        n = await store.reset_recipients(batch_id, list(batch.RETRYABLE))
        await store.put_batch({"id": batch_id, "status": "scheduled", "finished_at": None,
                               "config": {**b["config"], "send_type": "now"}})
        return {"id": batch_id, "status": "scheduled", "retrying": n}
    if action not in ACTIONS:
        raise HTTPException(404, "unknown action")
    allowed, new = ACTIONS[action]
    if b["status"] not in allowed:
        raise HTTPException(409, f"can't {action} a {b['status']} batch call")
    fields: dict[str, Any] = {"id": batch_id, "status": new}
    if new == "cancelled":
        fields["finished_at"] = datetime.now(timezone.utc)
    await store.put_batch(fields)
    return {"id": batch_id, "status": new}


@router.get("/batch-calls/{batch_id}/recipients", dependencies=auth)
async def recipients(batch_id: str, status: str | None = None, q: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    _, store = _store()
    await _batch(store, batch_id)
    if status and status not in batch.RECIPIENT_STATUSES:
        raise HTTPException(422, "unknown status")
    rows, total = await store.recipients(batch_id, status, q, max(1, min(limit, 500)), max(0, offset))
    return {"items": rows, "total": total, "counts": await store.recipient_counts(batch_id)}


@router.post("/batch-calls/{batch_id}/recipients", dependencies=auth)
@audited("batch.recipients_added")
async def add_recipients(batch_id: str, body: RowsBody) -> dict:
    _, store = _store()
    b = await _batch(store, batch_id)
    if b["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(409, "this batch call is finished — create a new one")
    checked = batch.check_rows(_rows_of(body))
    if checked["rejected"]:
        raise HTTPException(422, f"{len(checked['rejected'])} recipient(s) are invalid — fix or remove them first")
    counts = await store.recipient_counts(batch_id)
    if sum(counts.values()) + len(checked["accepted"]) > batch.MAX_RECIPIENTS:
        raise HTTPException(422, f"a batch call holds at most {batch.MAX_RECIPIENTS:,} recipients")
    await store.add_recipients(batch_id, checked["accepted"])
    return {"id": batch_id, "added": len(checked["accepted"])}


@router.delete("/batch-calls/{batch_id}/recipients/{rid}", dependencies=auth)
async def remove_recipient(batch_id: str, rid: int) -> dict:
    _, store = _store()
    await _batch(store, batch_id)
    rec = await store.recipient(rid)
    if rec is None or rec["batch_id"] != batch_id:
        raise HTTPException(404, "recipient not found")
    if rec["status"] == "in_progress":
        raise HTTPException(409, "this call is in progress")
    await store.delete_recipient(batch_id, rid)
    return {"deleted": rid}


# ---------------------------------------------------------------- one outbound call (Hamsa: "Make Outbound Call")

class OutboundCallBody(BaseModel):
    agent_id: str
    from_number: str
    to_number: str
    params: dict[str, Any] = Field(default_factory=dict)      # values for the agent's variables; unused ones are ignored
    draft: bool = False        # a test call: the agent's working copy answers instead of the published version


@router.post("/outbound-calls", dependencies=auth)
@audited("outbound.call")
async def outbound_call(body: OutboundCallBody) -> dict:
    """Place one call: a batch call of one recipient that starts at once (it shows in Batch calls and Call history)."""
    import asyncio
    rt, store = _store()
    ws = current_project()
    agent = await store.agent(body.agent_id)
    if agent is None or (agent.get("workspace_id") or ws) != ws:
        raise HTTPException(404, "agent not found")
    if body.draft:
        if not agent.get("draft"):
            raise HTTPException(409, "the agent has no draft to test — change something first, or test the published version")
    elif not agent.get("published_release_id"):
        raise HTTPException(409, "publish the agent first — calls use its published version")
    if not any(n["number"] == body.from_number for n in await store.outbound_numbers(ws)):
        raise HTTPException(422, "from_number is not an outbound number — add it under Phone numbers → Outbound numbers")
    phone, errors = batch.check_phone(body.to_number)
    if errors:
        raise HTTPException(422, f"to_number: {errors[0]}")
    if len(body.params) > 50:
        raise HTTPException(422, "at most 50 params")
    batch_id = batch.new_id()
    cfg = {"send_type": "now", "timezone": "UTC", "window_start": "00:00", "window_end": "23:59", "days": [0, 1, 2, 3, 4, 5, 6],
           "always": True, "single": True, **({"draft": True} if body.draft else {})}
    await store.put_batch({"id": batch_id, "workspace_id": ws, "name": f"{'Test call' if body.draft else 'Call'} to {phone}", "agent_id": body.agent_id,
                           "from_number": body.from_number, "status": "scheduled", "config": cfg, "created_by": principal().actor})
    await store.add_recipients(batch_id, [{"phone": phone, "name": None, "variables": body.params}])
    dialer = getattr(rt, "batch", None)
    if dialer is not None:
        asyncio.create_task(dialer.tick())                  # start now instead of waiting for the next tick
    (rec,), _ = await store.recipients(batch_id)
    return {"batch_call_id": batch_id, "recipient_id": rec["id"], "to": phone}


# ---------------------------------------------------------------- the PBX reports a call that never connected

class StatusBody(BaseModel):
    outbound_token: str
    status: str = Field(pattern="^(busy|no_answer|failed)$")
    reason: str | None = Field(default=None, max_length=300)


@router.post("/outbound/status")
async def outbound_status(body: StatusBody) -> dict:
    rt = _rt()
    ref = batch.read_token(rt.settings, body.outbound_token)
    if ref is None or getattr(rt, "batch", None) is None:
        raise HTTPException(401, "invalid token")
    return {"recorded": await rt.batch.report(ref[1], body.status, body.reason)}
