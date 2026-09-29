"""Import JSONL event logs (logs/events-*.jsonl) into the control-plane tables (calls + call_events).

    python scripts/backfill_events.py            # all files, skips events already imported
"""

import asyncio
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.app import ensure_schema  # noqa: E402
from runtime.control.store import EventStore  # noqa: E402
from runtime.data.db import get_pool  # noqa: E402
from runtime.events import Event  # noqa: E402


async def main() -> None:
    await ensure_schema()
    pool = await get_pool()
    async with pool.acquire() as c:
        known = {r[0] for r in await c.fetch("SELECT event_id FROM call_events WHERE event_id IS NOT NULL")}
    store, added = EventStore(), 0
    for path in sorted(glob.glob(str(ROOT / "logs" / "events-*.jsonl"))):
        for line in open(path, encoding="utf-8"):
            try:
                e = Event.model_validate_json(line)
            except Exception:
                continue
            if e.id in known or not e.call_id:
                continue
            store._buf.append(e)
            added += 1
            if len(store._buf) >= 500:
                await store.flush()
    await store.flush()
    async with pool.acquire() as c:
        calls = await c.fetchval("SELECT count(*) FROM calls")
    print(f"imported {added} events · {calls} calls in the database")


if __name__ == "__main__":
    asyncio.run(main())
