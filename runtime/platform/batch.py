"""Batch (outbound) calls (Hamsa parity, Batch Calls): a CSV of recipients, one agent, one number, a schedule and a daily
calling window; the dialer places the calls as capacity allows.

How a call is placed: the platform has no telephony of its own. For every recipient it POSTs to the **dial URL** of the
from-number (Phone numbers → Outbound numbers); the IVR / PBX behind that URL places the call and, once the callee
answers, connects its audio to our IVR socket (`/ws/voice-pipeline`) with the `outbound_token` from the request. A call
that is never answered reports `no_answer` / `busy` / `failed` to `/api/outbound/status` (or just never connects: then
the ring timeout turns it into no_answer). See docs/ivr_protocol.md, "Outbound calls".

Batch: scheduled → running ⇄ paused → completed | failed | cancelled.
Recipient: pending → in_progress → completed | failed | no_answer   (retry puts failed / no_answer back to pending).

Safety: `BATCH_LIVE_DIAL=false` (the default) places no real call — the dial URL is not contacted and every recipient is
marked completed with the note "simulated". Set it true only when the dial URL is a real, tested one.
"""

import asyncio
import csv
import io
import json
import logging
import re
import secrets as _secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import jwt

log = logging.getLogger(__name__)

MAX_CSV_BYTES = 50 * 1024 * 1024
MAX_RECIPIENTS = 10_000            # per batch call (keeps the recipients table and the screens responsive)
MAX_NAME = 100
BATCH_STATUSES = ("scheduled", "running", "paused", "completed", "failed", "cancelled")
RECIPIENT_STATUSES = ("pending", "in_progress", "completed", "failed", "no_answer")
LIVE = ("pending", "in_progress")
RETRYABLE = ("failed", "no_answer")
PHONE_COLUMNS = ("phonenumber", "phone_number")
IGNORE_COLUMNS = ("ignoree164validation", "ignore_e164_validation")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TOKEN_KIND = "outbound"
MAX_CALL = timedelta(hours=2)          # a connected call that never reports back is failed after this
_secret_fallback = _secrets.token_hex(32)      # per process; only used when neither AUTH_SECRET nor MASTER_KEY is set


# ---------------------------------------------------------------- recipients: CSV + phone numbers

def check_phone(raw: str, ignore_e164: bool = False) -> tuple[str, list[str]]:
    """(the number as stored, errors). Digits only with an optional +, at least 7 digits, valid E.164 unless ignored."""
    s = (raw or "").strip()
    if not s:
        return "", ["the phone number is empty"]
    if not re.fullmatch(r"\+?\d+", s):
        return s, ["the phone number must be digits only (a + prefix is allowed)"]
    digits = s.lstrip("+")
    if len(digits) < 7:
        return s, ["the phone number needs at least 7 digits"]
    if ignore_e164:
        return s, []
    if not re.fullmatch(r"[1-9]\d{6,14}", digits):
        return s, ["not a valid E.164 number (country code first, at most 15 digits)"]
    return "+" + digits, []


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def check_rows(rows: list[dict]) -> dict[str, Any]:
    """Rows as {phone, name?, variables?, ignore_e164?} → {accepted, rejected}. Rejected rows keep their data and say why
    (the console lets the user fix them and check again)."""
    accepted, rejected = [], []
    for i, r in enumerate(rows, start=1):
        phone, errors = check_phone(str(r.get("phone") or ""), bool(r.get("ignore_e164")))
        row = {"row": r.get("row", i), "phone": phone, "name": (r.get("name") or "").strip() or None,
               "variables": {k: v for k, v in (r.get("variables") or {}).items()},
               "ignore_e164": bool(r.get("ignore_e164"))}
        (rejected if errors else accepted).append({**row, "errors": errors} if errors else row)
    seen: dict[str, int] = {}
    for r in accepted:
        seen[r["phone"]] = seen.get(r["phone"], 0) + 1
    duplicates = sum(n - 1 for n in seen.values() if n > 1)
    return {"accepted": accepted, "rejected": rejected, "duplicates": duplicates}


def parse_csv(raw: bytes) -> list[dict]:
    """CSV bytes → rows {row, phone, name, variables, ignore_e164}. ValueError when the file can't be used at all."""
    if len(raw) > MAX_CSV_BYTES:
        raise ValueError(f"the file is larger than {MAX_CSV_BYTES // (1024 * 1024)} MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1256", "replace")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        raise ValueError("the file is empty")
    keys = [h.strip() for h in header]
    low = [k.lower() for k in keys]
    phone_i = next((i for i, k in enumerate(low) if k in PHONE_COLUMNS), None)
    if phone_i is None:
        raise ValueError("the file needs a phoneNumber (or phone_number) column in its first row")
    name_i = next((i for i, k in enumerate(low) if k == "name"), None)
    ignore_i = next((i for i, k in enumerate(low) if k in IGNORE_COLUMNS), None)
    special = {phone_i, name_i, ignore_i}
    rows = []
    for n, rec in enumerate(reader, start=2):
        if not any(c.strip() for c in rec):
            continue
        cell = lambda i: rec[i].strip() if i is not None and i < len(rec) else ""      # noqa: E731
        rows.append({"row": n, "phone": cell(phone_i), "name": cell(name_i),
                     "ignore_e164": _truthy(cell(ignore_i)),
                     "variables": {keys[i]: cell(i) for i in range(len(keys)) if i not in special and keys[i]}})
        if len(rows) > MAX_RECIPIENTS:
            raise ValueError(f"more than {MAX_RECIPIENTS:,} recipients — split the file into several batch calls")
    if not rows:
        raise ValueError("the file has no recipients")
    return rows


# ---------------------------------------------------------------- schedule

def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValueError(f"unknown timezone {name!r}") from None


def _hm(v: Any) -> int | None:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(v or "").strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def config_errors(cfg: dict[str, Any]) -> list[str]:
    e = []
    if cfg.get("send_type") not in ("now", "schedule"):
        e.append("send_type must be now or schedule")
    try:
        tz = _tz(cfg.get("timezone") or "")
    except ValueError as err:
        tz = None
        e.append(str(err))
    if cfg.get("send_type") == "schedule":
        try:
            at = datetime.fromisoformat(str(cfg.get("scheduled_at")))
            if tz and at.tzinfo is None:
                at = at.replace(tzinfo=tz)
            if at.tzinfo is not None and at <= datetime.now(timezone.utc):
                e.append("the start time is in the past")
        except (TypeError, ValueError):
            e.append("scheduled_at must be a date and time")
    if cfg.get("always"):                       # a single outbound call: no daily window
        return e
    s, t = _hm(cfg.get("window_start")), _hm(cfg.get("window_end"))
    if s is None or t is None:
        e.append("the daily start and end times must be HH:MM")
    elif s == t:
        e.append("the daily start and end times must differ")
    days = cfg.get("days")
    if not isinstance(days, list) or not days or any(not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6 for d in days):
        e.append("choose at least one day of the week (0 = Monday … 6 = Sunday)")
    return e


def scheduled_utc(cfg: dict[str, Any]) -> datetime | None:
    """When a scheduled batch may start (None: send now)."""
    if cfg.get("send_type") != "schedule":
        return None
    at = datetime.fromisoformat(str(cfg["scheduled_at"]))
    if at.tzinfo is None:
        at = at.replace(tzinfo=_tz(cfg["timezone"]))
    return at.astimezone(timezone.utc)


def in_window(now: datetime, cfg: dict[str, Any]) -> bool:
    """May calls be placed right now? (the daily window in the batch's timezone, on its allowed days; a window that
    ends after midnight belongs to the day it started on)"""
    if cfg.get("always"):
        return True
    local = now.astimezone(_tz(cfg["timezone"]))
    s, t = _hm(cfg["window_start"]), _hm(cfg["window_end"])
    minute, today = local.hour * 60 + local.minute, local.weekday()
    days = set(cfg["days"])
    if s < t:
        return today in days and s <= minute < t
    return (today in days and minute >= s) or ((today - 1) % 7 in days and minute < t)


# ---------------------------------------------------------------- outbound call token

def _key(settings) -> str:
    for secret in (settings.auth_secret, settings.master_key):
        if secret is not None:
            return secret.get_secret_value()
    return _secret_fallback


def make_token(settings, batch_id: str, recipient_id: int, ttl_s: int) -> str:
    return jwt.encode({"kind": TOKEN_KIND, "b": batch_id, "r": recipient_id,
                       "exp": datetime.now(timezone.utc) + timedelta(seconds=ttl_s)}, _key(settings), algorithm="HS256")


def read_token(settings, token: str) -> tuple[str, int] | None:
    try:
        p = jwt.decode(token, _key(settings), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return (p["b"], int(p["r"])) if p.get("kind") == TOKEN_KIND else None


# ---------------------------------------------------------------- variables for the call

def call_params(declared: dict[str, Any], rec: dict) -> dict[str, Any]:
    """A recipient's CSV columns as the call's `params`. Columns the agent doesn't declare still go in (they are
    templates' extras); names that aren't valid variable names or values that don't fit a declared type are left out —
    never an error, Hamsa ignores mismatches silently."""
    from runtime.harness.variables import build_custom, name_problem
    values = {"name": rec.get("name"), **(rec.get("variables") or {})}
    out = {k: v for k, v in values.items() if v not in (None, "") and name_problem(k) is None}
    for _ in range(len(out) + 1):
        try:
            build_custom(declared, out)
            return out
        except ValueError as e:
            bad = re.search(r"param '([^']+)'", str(e))
            if not bad or bad.group(1) not in out:
                return {}
            out.pop(bad.group(1))
    return {}


# ---------------------------------------------------------------- the dialer

class BatchDialer:
    """Moves batch calls forward: starts due batches, places calls inside the window as capacity allows, times out calls
    nobody answered and completes batches. One instance per process (`rt.batch`); `tick()` is idempotent."""

    def __init__(self, rt, interval_s: float = 10.0) -> None:
        self.rt = rt
        self.interval_s = interval_s
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    @property
    def store(self):
        return self.rt.platform

    @property
    def settings(self):
        return self.rt.settings

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:                                        # noqa: BLE001 — the loop must survive
                log.exception("batch dialer tick failed")
            await asyncio.sleep(self.interval_s)

    async def tick(self, now: datetime | None = None) -> None:
        if self.store is None:
            return
        now = now or datetime.now(timezone.utc)
        async with self._lock, self.store.advisory_lock("batch-dialer"):       # one worker at a time, cluster-wide
            for b in await self.store.batches_in(["scheduled"]):
                due = scheduled_utc(b["config"])
                if due is None or due <= now:
                    await self.store.put_batch({"id": b["id"], "status": "running", "started_at": b["started_at"] or now})
            active = await self.store.recipients_with_status(["in_progress"])
            await self._time_out(active, now)
            capacity = max(0, self.settings.batch_max_concurrent - sum(1 for r in active if r["status"] == "in_progress"))
            for b in sorted(await self.store.batches_in(["running"]), key=lambda x: x["created_at"]):
                if capacity > 0 and in_window(now, b["config"]):
                    for rec in await self.store.pending_recipients(b["id"], capacity):
                        await self._dial(b, rec, now)
                        capacity -= 1
                await self._finish_if_done(b, now)

    async def _time_out(self, active: list[dict], now: datetime) -> None:
        limit = timedelta(seconds=self.settings.batch_ring_timeout_s)
        for r in active:
            if r["call_id"] is None and r["dialed_at"] and now - r["dialed_at"] > limit:
                await self.store.update_recipient(r["id"], {"status": "no_answer", "ended_at": now,
                                                            "error": "the call was not answered in time"})
            elif r["call_id"] and r["dialed_at"] and now - r["dialed_at"] > MAX_CALL:      # never reported its end
                await self.store.update_recipient(r["id"], {"status": "failed", "ended_at": now,
                                                            "error": "the call never reported its end"})

    async def _finish_if_done(self, b: dict, now: datetime) -> None:
        counts = await self.store.recipient_counts(b["id"])
        if any(counts.get(s) for s in LIVE):
            return
        status = "completed" if counts.get("completed") or not counts else "failed"
        await self.store.put_batch({"id": b["id"], "status": status, "finished_at": now})

    async def _dial(self, b: dict, rec: dict, now: datetime) -> None:
        s = self.settings
        await self.store.update_recipient(rec["id"], {"status": "in_progress", "attempts": rec["attempts"] + 1,
                                                      "dialed_at": now, "error": None})
        number = next((n for n in await self.store.outbound_numbers(b["workspace_id"]) if n["number"] == b["from_number"]), None)
        if number is None:
            return await self._fail(rec, now, "the from-number is not set up for outbound calls")
        if not s.batch_live_dial:
            return await self.store.update_recipient(rec["id"], {
                "status": "completed", "ended_at": now, "duration_s": 0.0,
                "error": "simulated: no call was placed (BATCH_LIVE_DIAL=false)"})
        token = make_token(s, b["id"], rec["id"], s.batch_ring_timeout_s + 4 * 3600)
        base = (s.public_base_url or "").rstrip("/")
        ws_base = base.replace("https://", "wss://").replace("http://", "ws://") if base else ""
        payload = {"to": rec["phone"], "from": b["from_number"], "name": rec.get("name"),
                   "variables": rec.get("variables") or {}, "batch_call_id": b["id"], "recipient_id": rec["id"],
                   "outbound_token": token, "ws_url": f"{ws_base}/ws/voice-pipeline", "status_url": f"{base}/api/outbound/status"}
        headers = {}
        if number.get("dial_auth"):
            try:
                value = await self.rt.secrets.resolve(number["dial_auth"]) if self.rt.secrets else None
            except LookupError as e:
                return await self._fail(rec, now, str(e))
            if value:
                headers["Authorization"] = f"Bearer {value}"
        try:
            r = await self._http().post(number["dial_url"], json=payload, headers=headers)
        except Exception as e:                                       # noqa: BLE001
            return await self._fail(rec, now, f"the dial URL could not be reached ({e.__class__.__name__})")
        if not r.is_success:
            return await self._fail(rec, now, f"the dial URL answered HTTP {r.status_code}")
        try:
            answer = r.json()
        except ValueError:
            answer = {}
        if isinstance(answer, dict) and answer.get("status") in ("busy", "no_answer", "failed"):
            await self.report(rec["id"], answer["status"], answer.get("reason"))

    async def _fail(self, rec: dict, now: datetime, why: str) -> None:
        await self.store.update_recipient(rec["id"], {"status": "failed", "ended_at": now, "error": why})

    # -- reports from the call itself

    async def report(self, rid: int, status: str, reason: str | None = None) -> bool:
        """The PBX's word on a call that never connected: busy / no_answer / failed."""
        rec = await self.store.recipient(rid)
        if rec is None or rec["status"] != "in_progress" or rec["call_id"]:
            return False
        mapped = "no_answer" if status in ("no_answer", "busy") else "failed"
        text = {"busy": "the line was busy", "no_answer": "nobody answered"}.get(status) or reason or "the call failed"
        await self.store.update_recipient(rid, {"status": mapped, "ended_at": datetime.now(timezone.utc), "error": text})
        return True

    async def on_connect(self, rid: int, call_id: str) -> None:
        await self.store.update_recipient(rid, {"call_id": call_id})

    async def on_end(self, rid: int, call_id: str, started: float, reason: str | None) -> None:
        rec = await self.store.recipient(rid)
        if rec is None or rec["status"] != "in_progress":
            return
        await self.store.update_recipient(rid, {"status": "completed", "ended_at": datetime.now(timezone.utc),
                                                "duration_s": round(time.monotonic() - started, 1), "call_id": call_id,
                                                "error": None})

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:60] or "batch"


def new_id() -> str:
    return "bc_" + _secrets.token_hex(6)


__all__ = ["BatchDialer", "call_params", "check_phone", "check_rows", "config_errors", "in_window", "make_token",
           "new_id", "parse_csv", "read_token", "scheduled_utc"]
