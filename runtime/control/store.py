"""Event store: persists every (already PII-redacted) event to Postgres and maintains the `calls` summary.

Registered as an EventBus subscriber. Inserts are batched (every 250 ms or 200 events) so logging never
adds latency to a call.
"""

import asyncio
import json
import re
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.data.db import get_pool
from runtime.events import Event, EventType

log = logging.getLogger(__name__)

BATCH_SECONDS = 0.25
BATCH_MAX = 200


def channel_of(call_id: str | None) -> str | None:
    if not call_id:
        return None
    return "ivr" if call_id.startswith("ivr-") else "chat" if call_id.startswith("chat-") else "web"


class EventStore:
    def __init__(self) -> None:
        self._buf: list[Event] = []
        self._task: asyncio.Task | None = None
        self._latencies: dict[str, list[float]] = defaultdict(list)

    async def __call__(self, e: Event) -> None:
        self._buf.append(e)
        if self._task is None:
            self._task = asyncio.create_task(self._flush_loop(), name="event-store")
        if len(self._buf) >= BATCH_MAX:
            await self.flush()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(BATCH_SECONDS)
            try:
                await self.flush()
            except Exception as e:   # the DB being down must never affect calls
                log.warning("event store flush failed: %r", e)

    async def flush(self) -> None:
        if not self._buf:
            return
        batch, self._buf = self._buf, []
        pool = await get_pool()
        rows = [(e.id, e.call_id, e.ts, e.type.value, e.level.value, e.turn_id, e.skill, e.step, e.latency_ms,
                 json.dumps(e.data, ensure_ascii=False, default=str)) for e in batch]
        async with pool.acquire() as conn, conn.transaction():
            await conn.executemany(
                """INSERT INTO call_events (event_id, call_id, ts, type, level, turn_id, skill, step, latency_ms, data)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)""", rows)
            for e in batch:
                await self._summarize(conn, e)

    async def _summarize(self, conn, e: Event) -> None:
        cid, d = e.call_id, e.data
        if not cid:
            return
        if e.type == EventType.CALL_START:
            await conn.execute(
                """INSERT INTO calls (call_id, channel, started_at, language, config_version)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (call_id) DO UPDATE SET channel = EXCLUDED.channel, started_at = EXCLUDED.started_at,
                       language = COALESCE(EXCLUDED.language, calls.language),
                       config_version = EXCLUDED.config_version""",
                cid, channel_of(cid), e.ts, d.get("language"), d.get("config_version"))
            return
        await conn.execute("INSERT INTO calls (call_id, channel, started_at) VALUES ($1, $2, $3) "
                           "ON CONFLICT (call_id) DO NOTHING", cid, channel_of(cid), e.ts)
        if e.type == EventType.TURN_START:
            await conn.execute("UPDATE calls SET turns = GREATEST(turns, $2), last_skill = COALESCE($3, last_skill) "
                               "WHERE call_id = $1", cid, e.turn_id or 0, e.skill)
        elif e.type == EventType.SLOT_SET:
            f = d.get("field")
            if f == "language":
                await conn.execute("UPDATE calls SET language = $2 WHERE call_id = $1", cid, d.get("value"))
            elif f in ("booked", "appointment_no") and d.get("value"):
                await conn.execute("UPDATE calls SET booked = true WHERE call_id = $1", cid)
            elif f == "ivr_connect":
                await conn.execute("UPDATE calls SET extension_call = $2 WHERE call_id = $1", cid,
                                   bool(d.get("extension_call")))
        elif e.type == EventType.SKILL_EXIT and d.get("reason") == "verified":
            await conn.execute("UPDATE calls SET verified = true WHERE call_id = $1", cid)
        elif e.type == EventType.HANDOFF:
            await conn.execute("UPDATE calls SET handoff = COALESCE($2, handoff, 'transfer') WHERE call_id = $1",
                               cid, d.get("reason"))
        elif e.type == EventType.TTS_FIRST_BYTE and d.get("metric") and e.latency_ms is not None:
            lat = self._latencies[cid]
            lat.append(e.latency_ms)
            await conn.execute("UPDATE calls SET latency_p50_ms = $2 WHERE call_id = $1", cid,
                               sorted(lat)[len(lat) // 2])
        elif e.type == EventType.CALL_END:
            await conn.execute(
                """UPDATE calls SET ended_at = $2, end_reason = $3, verified = verified OR COALESCE($4, false)
                   WHERE call_id = $1""", cid, e.ts, d.get("reason"), d.get("verified"))
            self._latencies.pop(cid, None)

    async def set_mobile(self, call_id: str, mobile: str) -> None:
        """The caller's number goes only into the call row — never through the (masked) event stream."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO calls (call_id, channel, mobile) VALUES ($1, $2, $3) "
                               "ON CONFLICT (call_id) DO UPDATE SET mobile = EXCLUDED.mobile",
                               call_id, channel_of(call_id), mobile)

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        try:
            await self.flush()
        except Exception:
            pass


# ---------------------------------------------------------------- queries


async def stats(hours: int = 24) -> dict[str, Any]:
    pool = await get_pool()
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with pool.acquire() as c:
        totals = await c.fetchrow(
            """SELECT count(*) AS calls,
                      count(*) FILTER (WHERE verified) AS verified,
                      count(*) FILTER (WHERE booked) AS booked,
                      count(*) FILTER (WHERE handoff IS NOT NULL) AS handoffs,
                      count(*) FILTER (WHERE ended_at IS NULL AND started_at > now() - interval '30 minutes') AS active,
                      avg(turns)::float AS avg_turns
               FROM calls WHERE started_at >= $1""", since)
        lat = await c.fetchrow(
            """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95, count(*) AS n
               FROM call_events WHERE type = 'tts.first_byte' AND data->>'metric' IS NOT NULL AND ts >= $1""", since)
        per_hour = await c.fetch(
            """SELECT date_trunc('hour', started_at) AS hour, count(*) AS calls,
                      count(*) FILTER (WHERE booked) AS booked, count(*) FILTER (WHERE handoff IS NOT NULL) AS handoffs
               FROM calls WHERE started_at >= $1 GROUP BY 1 ORDER BY 1""", since)
        stages = await c.fetch(
            """SELECT type, percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50
               FROM call_events WHERE ts >= $1 AND latency_ms IS NOT NULL
                 AND type IN ('stt.result', 'llm.first_token', 'tts.first_byte', 'tool.end')
               GROUP BY type""", since)
        skills = await c.fetch(
            """SELECT coalesce(last_skill, 'none') AS skill, count(*) AS calls FROM calls
               WHERE started_at >= $1 GROUP BY 1 ORDER BY 2 DESC""", since)
    return {"window_hours": hours, "totals": dict(totals),
            "latency": {"p50": lat["p50"], "p95": lat["p95"], "samples": lat["n"]},
            "stages": {r["type"]: r["p50"] for r in stages},
            "per_hour": [dict(r) for r in per_hour], "skills": [dict(r) for r in skills]}


DURATION = "EXTRACT(EPOCH FROM (ended_at - started_at))::int AS duration_s"


async def list_calls(limit: int = 50, offset: int = 0, q: str | None = None, outcome: str | None = None,
                     channel: str | None = None) -> dict[str, Any]:
    pool = await get_pool()
    where, args = ["true"], []
    if q:
        # call id, or the mobile number however it's typed: 0548802968, 548802968, +966 54 880 2968, 2968
        digits = re.sub(r"\D", "", q)
        if digits.startswith("966"):
            digits = "0" + digits[3:]
        args.append(f"%{q.strip()}%")
        cond = f"call_id ILIKE ${len(args)}"
        if len(digits) >= 3:
            args.append(f"%{digits.lstrip('0') or digits}%")
            cond += f" OR mobile LIKE ${len(args)}"
        where.append(f"({cond})")
    if channel:
        args.append(channel)
        where.append(f"channel = ${len(args)}")
    if outcome == "booked":
        where.append("booked")
    elif outcome == "handoff":
        where.append("handoff IS NOT NULL")
    elif outcome == "unverified":
        where.append("NOT verified")
    sql_where = " AND ".join(where)
    async with pool.acquire() as c:
        total = await c.fetchval(f"SELECT count(*) FROM calls WHERE {sql_where}", *args)
        rows = await c.fetch(f"SELECT *, {DURATION} FROM calls WHERE {sql_where} ORDER BY started_at DESC "
                             f"LIMIT {int(limit)} OFFSET {int(offset)}", *args)
    return {"total": total, "items": [dict(r) for r in rows]}


async def get_call(call_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as c:
        call = await c.fetchrow(f"SELECT *, {DURATION} FROM calls WHERE call_id = $1", call_id)
        if call is None:
            return None
        rows = await c.fetch("SELECT * FROM call_events WHERE call_id = $1 ORDER BY ts, id", call_id)
    events = [_event_row(r) for r in rows]
    return {"call": dict(call), "events": events, "transcript": transcript(events), "turns": turn_latency(events)}


async def query_events(*, call_id: str | None = None, types: list[str] | None = None, level: str | None = None,
                       text: str | None = None, before_id: int | None = None, limit: int = 200) -> list[dict]:
    pool = await get_pool()
    where, args = ["true"], []
    for cond, val in (("call_id = ${}", call_id), ("type = ANY(${})", types), ("level = ${}", level),
                      ("data::text ILIKE ${}", f"%{text}%" if text else None), ("id < ${}", before_id)):
        if val:
            args.append(val)
            where.append(cond.format(len(args)))
    async with pool.acquire() as c:
        rows = await c.fetch(f"SELECT * FROM call_events WHERE {' AND '.join(where)} ORDER BY id DESC "
                             f"LIMIT {min(int(limit), 1000)}", *args)
    return [_event_row(r) for r in rows]


def _event_row(r) -> dict[str, Any]:
    d = dict(r)
    if isinstance(d.get("data"), str):
        d["data"] = json.loads(d["data"])
    return d


def transcript(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        d = e.get("data") or {}
        if e["type"] == "turn.start" and d.get("text"):
            out.append({"role": "user", "text": d["text"], "ts": e["ts"], "turn": e.get("turn_id")})
        elif e["type"] == "agent.say" and d.get("text"):
            if out and out[-1]["role"] == "agent" and out[-1].get("turn") == e.get("turn_id"):
                out[-1]["text"] += " " + d["text"]
            else:
                out.append({"role": "agent", "text": d["text"], "ts": e["ts"], "turn": e.get("turn_id")})
        elif e["type"] in ("handoff", "interrupt"):
            out.append({"role": "system", "text": e["type"] + (f": {d.get('reason')}" if d.get("reason") else ""),
                        "ts": e["ts"], "turn": e.get("turn_id")})
    return out


def turn_latency(events: list[dict]) -> list[dict]:
    """Per turn: STT, LLM first token, tools, turn end → first audio (for the waterfall view)."""
    turns: dict[int, dict] = {}
    pending_stt = None
    for e in events:
        t, d, lat = e["type"], e.get("data") or {}, e.get("latency_ms")
        if t == "stt.result":
            pending_stt = lat
            continue
        tid = e.get("turn_id")
        if t == "turn.start" and tid is not None:
            turns[tid] = {"turn": tid, "text": d.get("text"), "stt_ms": pending_stt, "llm_first_token_ms": None,
                          "tools": [], "first_audio_ms": None, "total_ms": None}
            pending_stt = None
        row = turns.get(tid) if tid is not None else (turns[max(turns)] if turns else None)
        if row is None:
            continue
        if t == "llm.first_token" and row["llm_first_token_ms"] is None:
            row["llm_first_token_ms"] = lat
        elif t in ("tool.end", "tool.error") and not d.get("prefetch"):
            row["tools"].append({"tool": d.get("tool"), "ms": lat, "ok": t == "tool.end", "cached": d.get("cached")})
        elif t == "tts.first_byte" and d.get("metric") and row["first_audio_ms"] is None:
            row["first_audio_ms"] = lat
        elif t == "turn.end":
            row["total_ms"] = lat
    return list(turns.values())
