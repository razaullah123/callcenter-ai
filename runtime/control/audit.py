"""Patient-data access audit trail (PDPL): one row per tool call that reads or changes a patient's data.

Kept in its own table (not the general event log) so patient identifiers don't spread into logs and can have
their own access control and retention (AUDIT_RETENTION_DAYS).
"""

import asyncio
import logging
from datetime import datetime, timezone

from runtime.data.db import get_pool

log = logging.getLogger(__name__)


class AuditLog:
    def __init__(self) -> None:
        self._buf: list[tuple] = []
        self._task: asyncio.Task | None = None

    def record(self, *, call_id: str, tool: str, kind: str, patient_id: int | None, ok: bool,
               confirmed: bool, verified: bool, detail: str | None = None) -> None:
        self._buf.append((datetime.now(timezone.utc), call_id, _channel(call_id), tool, kind,
                          str(patient_id) if patient_id is not None else None, ok, confirmed, verified, detail))
        if self._task is None or self._task.done():
            self._task = asyncio.get_event_loop().create_task(self._flush_soon())

    async def _flush_soon(self) -> None:
        await asyncio.sleep(0.5)
        await self.flush()

    async def flush(self) -> None:
        if not self._buf:
            return
        rows, self._buf = self._buf, []
        try:
            pool = await get_pool()
            async with pool.acquire() as c:
                await c.executemany(
                    """INSERT INTO patient_access_audit (ts, call_id, channel, tool, kind, patient_id, ok, confirmed,
                                                         verified, detail) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    rows)
        except Exception as e:     # never break a call; keep the rows for the next attempt
            log.warning("audit flush failed (%d rows kept): %r", len(rows), e)
            self._buf = rows + self._buf


def _channel(call_id: str) -> str:
    return "ivr" if call_id.startswith("ivr-") else "chat" if call_id.startswith("chat-") else \
        "eval" if call_id.startswith("eval-") else "web"


AUDIT = AuditLog()
