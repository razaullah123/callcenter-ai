"""Eval jobs started from the console, in the database so that any worker can report progress (Phase 12.3).
Without a database they are kept in this process only."""

import asyncio
import json
import time
from typing import Any

STALE_S = 30 * 60          # a "running" job not updated for this long died with its worker


class EvalJobs:
    def __init__(self) -> None:
        self._mem: dict[str, dict[str, Any]] = {}

    async def _pool(self):
        try:
            from runtime.data.db import get_pool
            return await get_pool()
        except Exception:
            return None

    async def running(self) -> bool:
        pool = await self._pool()
        if pool is None:
            return any(j["status"] == "running" and time.time() - j["updated"] < STALE_S for j in self._mem.values())
        async with pool.acquire() as c:
            return bool(await c.fetchval("SELECT 1 FROM eval_jobs WHERE status = 'running' "
                                         "AND updated_at > now() - make_interval(secs => $1)", STALE_S))

    async def create(self, job: str, total: int, worker: str) -> None:
        pool = await self._pool()
        if pool is None:
            self._mem[job] = {"status": "running", "total": total, "done": [], "run_id": None, "error": None,
                              "updated": time.time()}
            return
        async with pool.acquire() as c:
            await c.execute("INSERT INTO eval_jobs (id, status, total, worker) VALUES ($1, 'running', $2, $3)",
                            job, total, worker)

    async def progress(self, job: str, item: dict[str, Any]) -> None:
        pool = await self._pool()
        if pool is None:
            self._mem[job]["done"].append(item)
            self._mem[job]["updated"] = time.time()
            return
        async with pool.acquire() as c:
            await c.execute("UPDATE eval_jobs SET done = done || $2::jsonb, updated_at = now() WHERE id = $1",
                            job, json.dumps([item]))

    async def finish(self, job: str, *, run_id: str | None = None, error: str | None = None) -> None:
        pool = await self._pool()
        status = "error" if error else "done"
        if pool is None:
            self._mem[job].update(status=status, run_id=run_id, error=error, updated=time.time())
            return
        async with pool.acquire() as c:
            await c.execute("UPDATE eval_jobs SET status = $2, run_id = $3, error = $4, updated_at = now() "
                            "WHERE id = $1", job, status, run_id, error)

    async def get(self, job: str) -> dict[str, Any] | None:
        pool = await self._pool()
        if pool is None:
            j = self._mem.get(job)
            return {k: v for k, v in j.items() if k != "updated"} if j else None
        async with pool.acquire() as c:
            r = await c.fetchrow("SELECT status, total, done, run_id, error FROM eval_jobs WHERE id = $1", job)
        if r is None:
            return None
        d = dict(r)
        d["done"] = json.loads(d["done"]) if isinstance(d["done"], str) else d["done"]
        return d


JOBS = EvalJobs()


def spawn(coro) -> asyncio.Task:
    """Run a job in the background, keeping a reference so it isn't garbage-collected mid-run."""
    task = asyncio.create_task(coro)
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task


_running: set[asyncio.Task] = set()
