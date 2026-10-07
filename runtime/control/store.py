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
                """INSERT INTO calls (call_id, channel, started_at, language, config_version, agent_id, release_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (call_id) DO UPDATE SET channel = EXCLUDED.channel, started_at = EXCLUDED.started_at,
                       language = COALESCE(EXCLUDED.language, calls.language),
                       config_version = EXCLUDED.config_version, agent_id = EXCLUDED.agent_id,
                       release_id = EXCLUDED.release_id""",
                cid, channel_of(cid), e.ts, d.get("language"), d.get("config_version"), d.get("agent_id"),
                d.get("release_id"))
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
            elif f == "identity_confirmed" and d.get("value"):     # only asked after the code was verified
                await conn.execute("UPDATE calls SET verified = true WHERE call_id = $1", cid)
            elif f in ("booked", "appointment_no") and d.get("value"):
                await conn.execute("UPDATE calls SET booked = true WHERE call_id = $1", cid)
            elif f == "ivr_connect":
                await conn.execute("UPDATE calls SET extension_call = $2 WHERE call_id = $1", cid,
                                   bool(d.get("extension_call")))
        elif e.type == EventType.SKILL_EXIT and d.get("reason") == "verified":
            await conn.execute("UPDATE calls SET verified = true WHERE call_id = $1", cid)
        elif e.type == EventType.AGENT_TRANSFER:       # the call now belongs to the agent it moved to
            await conn.execute("UPDATE calls SET agent_id = COALESCE($2, agent_id), release_id = $3, "
                               "config_version = $4 WHERE call_id = $1", cid, d.get("agent_id"), d.get("release_id"),
                               d.get("config_version"))
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

# A project scope: (agent ids, include calls without an agent). Calls from before agents existed (no agent_id)
# belong to the default project. None = every call (scripts, tests).
Scope = tuple[list[str], bool] | None


def _scope_sql(scope: Scope, args: list, col: str = "agent_id") -> str:
    if scope is None:
        return "true"
    agents, unassigned = scope
    args.append(list(agents))
    cond = f"{col} = ANY(${len(args)}::text[])"
    return f"({cond} OR {col} IS NULL)" if unassigned else cond


def _calls_of(scope: Scope, args: list) -> str:
    """call_events filter: only events of calls in scope."""
    if scope is None:
        return "true"
    return f"call_id IN (SELECT call_id FROM calls WHERE {_scope_sql(scope, args)})"


def _flat(q: tuple[str, list]) -> list:
    return [q[0], *q[1]]


async def stats(hours: int = 24, scope: Scope = None) -> dict[str, Any]:
    pool = await get_pool()
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    def q(sql: str, events: bool = False) -> tuple[str, list]:
        args: list = [since]
        cond = _calls_of(scope, args) if events else _scope_sql(scope, args)
        return sql.replace("{scope}", cond), args

    async with pool.acquire() as c:
        totals = await c.fetchrow(*_flat(q(
            """SELECT count(*) AS calls,
                      count(*) FILTER (WHERE verified) AS verified,
                      count(*) FILTER (WHERE booked) AS booked,
                      count(*) FILTER (WHERE handoff IS NOT NULL) AS handoffs,
                      count(*) FILTER (WHERE ended_at IS NULL AND started_at > now() - interval '30 minutes') AS active,
                      avg(turns)::float AS avg_turns
               FROM calls WHERE started_at >= $1 AND {scope}""")))
        lat = await c.fetchrow(*_flat(q(
            """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95, count(*) AS n
               FROM call_events WHERE type = 'tts.first_byte' AND data->>'metric' IS NOT NULL AND ts >= $1
                 AND {scope}""", events=True)))
        per_hour = await c.fetch(*_flat(q(
            """SELECT date_trunc('hour', started_at) AS hour, count(*) AS calls,
                      count(*) FILTER (WHERE booked) AS booked, count(*) FILTER (WHERE handoff IS NOT NULL) AS handoffs
               FROM calls WHERE started_at >= $1 AND {scope} GROUP BY 1 ORDER BY 1""")))
        stages = await c.fetch(*_flat(q(
            """SELECT type, percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50
               FROM call_events WHERE ts >= $1 AND latency_ms IS NOT NULL
                 AND type IN ('stt.result', 'llm.first_token', 'tts.first_byte', 'tool.end') AND {scope}
               GROUP BY type""", events=True)))
        skills = await c.fetch(*_flat(q(
            """SELECT coalesce(last_skill, 'none') AS skill, count(*) AS calls FROM calls
               WHERE started_at >= $1 AND {scope} GROUP BY 1 ORDER BY 2 DESC""")))
    return {"window_hours": hours, "totals": dict(totals),
            "latency": {"p50": lat["p50"], "p95": lat["p95"], "samples": lat["n"]},
            "stages": {r["type"]: r["p50"] for r in stages},
            "per_hour": [dict(r) for r in per_hour], "skills": [dict(r) for r in skills]}


async def dashboard(start: datetime, end: datetime, scope: Scope = None, tz: str = "UTC") -> dict[str, Any]:
    """Everything the console dashboard shows for calls started in [start, end): sessions and durations, outcomes,
    per-stage latency averages, words per agent reply, error rate and a calls-over-time series (hourly up to two
    days, else daily, in the viewer's time zone `tz`)."""
    if not re.fullmatch(r"[A-Za-z0-9_+\-/]{1,64}", tz):
        raise ValueError(f"bad time zone {tz!r}")
    pool = await get_pool()
    hourly = (end - start) <= timedelta(days=2)

    def q(sql: str, events: bool = False) -> tuple[str, list]:
        args: list = [start, end]
        cond = _calls_of(scope, args) if events else _scope_sql(scope, args)
        return sql.replace("{scope}", cond), args

    in_window = "started_at >= $1 AND started_at < $2 AND {scope}"
    ev_window = "call_id IN (SELECT call_id FROM calls WHERE started_at >= $1 AND started_at < $2) AND {scope}"
    dur = "EXTRACT(EPOCH FROM (ended_at - started_at))"
    async with pool.acquire() as c:
        t = await c.fetchrow(*_flat(q(
            f"""SELECT count(*) AS calls,
                      count(*) FILTER (WHERE ended_at IS NOT NULL) AS ended,
                      coalesce(sum({dur}), 0)::float AS total_duration_s,
                      avg({dur})::float AS avg_duration_s,
                      count(*) FILTER (WHERE {dur} < 30) AS lt30,
                      count(*) FILTER (WHERE {dur} >= 30 AND {dur} <= 120) AS s30_120,
                      count(*) FILTER (WHERE {dur} > 120) AS gt120,
                      count(*) FILTER (WHERE verified) AS verified,
                      count(*) FILTER (WHERE booked) AS booked,
                      count(*) FILTER (WHERE handoff IS NOT NULL) AS handoffs,
                      count(*) FILTER (WHERE verified AND NOT booked AND handoff IS NULL) AS verified_only,
                      count(*) FILTER (WHERE NOT verified AND NOT booked AND handoff IS NULL) AS unresolved,
                      avg(turns)::float AS avg_turns
               FROM calls WHERE {in_window}""")))
        series = await c.fetch(*_flat(q(
            f"""SELECT date_trunc('{"hour" if hourly else "day"}', started_at AT TIME ZONE '{tz}') AS t,
                      count(*) AS calls,
                      count(*) FILTER (WHERE booked) AS booked, count(*) FILTER (WHERE handoff IS NOT NULL) AS handoffs
               FROM calls WHERE {in_window} GROUP BY 1 ORDER BY 1""")))
        channels = await c.fetch(*_flat(q(
            f"SELECT coalesce(channel, 'web') AS key, count(*) AS calls FROM calls WHERE {in_window} "
            "GROUP BY 1 ORDER BY 2 DESC")))
        languages = await c.fetch(*_flat(q(
            f"SELECT coalesce(language, 'unknown') AS key, count(*) AS calls FROM calls WHERE {in_window} "
            "GROUP BY 1 ORDER BY 2 DESC")))
        skills = await c.fetch(*_flat(q(
            f"SELECT coalesce(last_skill, 'none') AS key, count(*) AS calls FROM calls WHERE {in_window} "
            "GROUP BY 1 ORDER BY 2 DESC")))
        stages = await c.fetch(*_flat(q(
            f"""SELECT type, avg(latency_ms)::float AS avg,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
               FROM call_events WHERE latency_ms IS NOT NULL AND {ev_window}
                 AND (type IN ('stt.result', 'llm.first_token', 'tool.end')
                      OR (type = 'tts.first_byte' AND data->>'metric' IS NOT NULL))
               GROUP BY type""", events=True)))
        # agent.say / tts.first_byte carry no turn id: each event belongs to the turn started last before it
        per_turn = f"""WITH ev AS (
                 SELECT call_id, type, latency_ms, data, data->>'metric' AS metric,
                        max(turn_id) OVER (PARTITION BY call_id ORDER BY id) AS turn
                 FROM call_events WHERE type IN ('turn.start', 'llm.first_token', 'tts.first_byte', 'agent.say')
                   AND {ev_window})"""
        # speech synthesis: per turn, first audio minus the LLM's first token (TTS + writing the first sentence)
        tts = await c.fetchval(*_flat(q(
            per_turn + """ SELECT avg(GREATEST(a.ms - l.ms, 0))::float FROM
                 (SELECT call_id, turn, min(latency_ms) AS ms FROM ev
                  WHERE type = 'tts.first_byte' AND metric IS NOT NULL AND turn IS NOT NULL GROUP BY 1, 2) a
               JOIN (SELECT call_id, turn, min(latency_ms) AS ms FROM ev WHERE type = 'llm.first_token'
                     GROUP BY 1, 2) l USING (call_id, turn)""", events=True)))
        words = await c.fetchval(*_flat(q(
            per_turn + r""" SELECT avg(n)::float FROM (
                 SELECT sum(array_length(regexp_split_to_array(btrim(data->>'text'), '\s+'), 1)) AS n
                 FROM ev WHERE type = 'agent.say' AND coalesce(btrim(data->>'text'), '') <> ''
                 GROUP BY call_id, turn) r""", events=True)))
        errors = await c.fetchval(*_flat(q(
            f"SELECT count(DISTINCT call_id) FROM call_events WHERE level = 'error' AND {ev_window}", events=True)))
        reasons = await c.fetch(*_flat(q(
            f"SELECT handoff AS key, count(*) AS calls FROM calls "
            f"WHERE handoff IS NOT NULL AND {in_window} GROUP BY 1 ORDER BY 2 DESC")))
    st = {r["type"]: {"avg": r["avg"], "p50": r["p50"], "p95": r["p95"]} for r in stages}
    lat = st.get("tts.first_byte") or {}
    return {"start": start.isoformat(), "end": end.isoformat(), "granularity": "hour" if hourly else "day",
            "totals": {**dict(t), "errors": errors or 0, "avg_words_per_reply": words},
            "performance": {"stt_ms": (st.get("stt.result") or {}).get("avg"),
                            "llm_ms": (st.get("llm.first_token") or {}).get("avg"),
                            "tool_ms": (st.get("tool.end") or {}).get("avg"),
                            "tts_ms": tts, "latency_ms": lat.get("avg"),
                            "latency_p50_ms": lat.get("p50"), "latency_p95_ms": lat.get("p95")},
            "series": [dict(r) for r in series],
            "channels": [dict(r) for r in channels], "languages": [dict(r) for r in languages],
            "skills": [dict(r) for r in skills], "handoff_reasons": [dict(r) for r in reasons]}


DURATION = "EXTRACT(EPOCH FROM (ended_at - started_at))::int AS duration_s"


# Hamsa's call statuses, from what a call row and its events record
STATUSES = ("in_progress", "completed", "failed", "forwarded", "terminated")
STATUS = """CASE
    WHEN ended_at IS NULL AND started_at > now() - interval '30 minutes' THEN 'in_progress'
    WHEN handoff IS NOT NULL OR end_reason = 'transferred' THEN 'forwarded'
    WHEN EXISTS (SELECT 1 FROM call_events e WHERE e.call_id = calls.call_id AND e.level = 'error') THEN 'failed'
    WHEN ended_at IS NULL OR end_reason = 'ended_from_console' THEN 'terminated'
    ELSE 'completed' END"""
SORTS = {"time": "started_at", "duration": "duration_s"}


async def list_calls(limit: int = 50, offset: int = 0, q: str | None = None, outcome: str | None = None,
                     channel: str | None = None, scope: Scope = None, *, channels: list[str] | None = None,
                     statuses: list[str] | None = None, start: datetime | None = None, end: datetime | None = None,
                     sort: str = "time", desc: bool = True, duration: tuple[str, int, int | None] | None = None) -> dict[str, Any]:
    pool = await get_pool()
    args: list = []
    where = [_scope_sql(scope, args)]
    if q:
        # call id, agent id, or the mobile number however it's typed: 0548802968, 548802968, +966 54 880 2968, 2968
        digits = re.sub(r"\D", "", q)
        if digits.startswith("966"):
            digits = "0" + digits[3:]
        args.append(f"%{q.strip()}%")
        cond = f"call_id ILIKE ${len(args)} OR agent_id ILIKE ${len(args)}"
        if len(digits) >= 3:
            args.append(f"%{digits.lstrip('0') or digits}%")
            cond += f" OR mobile LIKE ${len(args)}"
        where.append(f"({cond})")
    if channel:
        channels = [*(channels or []), channel]
    if channels:
        args.append(channels)
        where.append(f"channel = ANY(${len(args)}::text[])")
    if start:
        args.append(start)
        where.append(f"started_at >= ${len(args)}")
    if end:
        args.append(end)
        where.append(f"started_at < ${len(args)}")
    if outcome == "booked":
        where.append("booked")
    elif outcome == "handoff":
        where.append("handoff IS NOT NULL")
    elif outcome == "unverified":
        where.append("NOT verified")
    rows_sql = f"SELECT *, {DURATION}, {STATUS} AS status FROM calls WHERE {' AND '.join(where)}"
    conds: list[str] = []
    if statuses is not None:
        args.append([x for x in statuses if x in STATUSES])
        conds.append(f"status = ANY(${len(args)}::text[])")
    if duration:                                  # seconds; a call without a duration (still running) never matches
        op, a, b = duration
        args.append(a)
        if op == "between":
            args.append(b)
            conds.append(f"duration_s BETWEEN ${len(args) - 1} AND ${len(args)}")
        else:
            conds.append(f"duration_s {dict(gt='>', lt='<', eq='=')[op]} ${len(args)}")
    outer = f"WHERE {' AND '.join(conds)}" if conds else ""
    order = f"{SORTS.get(sort, 'started_at')} {'DESC' if desc else 'ASC'} NULLS LAST, started_at DESC"
    async with pool.acquire() as c:
        total = await c.fetchval(f"SELECT count(*) FROM ({rows_sql}) x {outer}", *args)
        rows = await c.fetch(f"SELECT * FROM ({rows_sql}) x {outer} ORDER BY {order} "
                             f"LIMIT {int(limit)} OFFSET {int(offset)}", *args)
    return {"total": total, "items": [dict(r) for r in rows]}


async def get_call(call_id: str, scope: Scope = None) -> dict[str, Any] | None:
    pool = await get_pool()
    args: list = [call_id]
    in_scope = _scope_sql(scope, args)
    async with pool.acquire() as c:
        call = await c.fetchrow(f"SELECT *, {DURATION}, {STATUS} AS status FROM calls "
                                f"WHERE call_id = $1 AND {in_scope}", *args)
        if call is None:
            return None
        rows = await c.fetch("SELECT * FROM call_events WHERE call_id = $1 ORDER BY ts, id", call_id)
    events = [_event_row(r) for r in rows]
    return {"call": dict(call), "events": events, "transcript": transcript(events), "turns": turn_latency(events)}


async def query_events(*, call_id: str | None = None, types: list[str] | None = None, level: str | None = None,
                       text: str | None = None, before_id: int | None = None, limit: int = 200,
                       scope: Scope = None) -> list[dict]:
    pool = await get_pool()
    args: list = []
    where = [_calls_of(scope, args)]
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
