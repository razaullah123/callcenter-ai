"""Background maintenance: data retention (PDPL) — transcripts / events, JSONL logs, eval runs, audit rows."""

import asyncio
import logging
import time

from runtime.config import Settings
from runtime.data.db import get_pool

log = logging.getLogger(__name__)


async def purge_once(s: Settings) -> dict[str, int]:
    """Delete data older than the retention windows. Call summaries (no transcript content) are kept."""
    out: dict[str, int] = {}
    pool = await get_pool()
    async with pool.acquire() as c:
        # call summaries are kept, but the caller's number isn't — cleared on the same window as transcripts
        r = await c.execute("UPDATE calls SET mobile = NULL WHERE mobile IS NOT NULL "
                            "AND started_at < now() - make_interval(days => $1)", s.event_retention_days)
        out["calls.mobile"] = int(r.split()[-1])
        for table, column, days in (("call_events", "ts", s.event_retention_days),
                                    ("eval_runs", "started_at", s.event_retention_days),
                                    ("patient_access_audit", "ts", s.audit_retention_days)):
            r = await c.execute(f"DELETE FROM {table} WHERE {column} < now() - make_interval(days => $1)", days)
            out[table] = int(r.split()[-1])
    cutoff = time.time() - s.event_retention_days * 86400
    removed = 0
    for f in s.log_dir.glob("events-*.jsonl"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    out["jsonl_files"] = removed
    return out


async def maintenance_loop(s: Settings, every_s: int = 6 * 3600) -> None:
    while True:
        try:
            result = await purge_once(s)
            if any(result.values()):
                log.info("retention purge: %s", result)
        except Exception as e:
            log.warning("retention purge failed: %r", e)
        await asyncio.sleep(every_s)


def production_problems(s: Settings) -> list[str]:
    """Settings that are unsafe with real patients (TOOLS_MODE=live)."""
    problems = []
    if s.tools_mode != "live":
        return problems
    if s.console_token is None:
        problems.append("CONSOLE_TOKEN is not set — the console (transcripts, configs) would be open")
    if s.auth_secret is None:
        problems.append("AUTH_SECRET is not set — IVR tokens would not be verified")
    if s.provider_override:
        problems.append(f"PROVIDER_OVERRIDE={s.provider_override} is set in live mode")
    return problems
