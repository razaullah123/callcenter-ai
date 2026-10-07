"""Keep several server processes in step, over Postgres LISTEN / NOTIFY (no extra infrastructure).

  hmg_config        a release was published / a provider, secret or skill changed → every worker reloads its agents
  hmg_call_control  "end this call" from the console → the worker that owns the call ends it
  hmg_live          call events (already redacted) → every worker's live hub, so the console sees all calls
                    (only with WORKERS > 1: a single process needs no fan-out)

Each message carries the sender's worker id; a worker ignores its own. The listener keeps one dedicated
connection and reconnects with backoff; after a reconnect it reloads once, in case a change was missed meanwhile.
"""

import asyncio
import json
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

CONFIG, CONTROL, LIVE = "hmg_config", "hmg_call_control", "hmg_live"
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
MAX_PAYLOAD = 7800            # Postgres NOTIFY payloads must stay under 8000 bytes
_LONG_TEXT = 600

Handler = Callable[[dict[str, Any]], Awaitable[None]]


def compact_event(event: dict[str, Any]) -> str | None:
    """An event as a NOTIFY payload: long texts cut, and the data dropped if it still doesn't fit."""
    data = {k: (v[:_LONG_TEXT] + "…" if isinstance(v, str) and len(v) > _LONG_TEXT else v)
            for k, v in (event.get("data") or {}).items()}
    msg = json.dumps({**event, "data": data}, ensure_ascii=False, default=str)
    if len(msg.encode()) > MAX_PAYLOAD:
        msg = json.dumps({**event, "data": {"truncated": True}}, ensure_ascii=False, default=str)
    return msg if len(msg.encode()) <= MAX_PAYLOAD else None


class ClusterSync:
    def __init__(self, dsn: str, *, ssl: bool = False, worker_id: str = WORKER_ID,
                 on_config: Handler | None = None, on_control: Handler | None = None,
                 on_live: Handler | None = None, share_live: bool = False) -> None:
        self.dsn, self.ssl, self.worker_id = dsn, ssl, worker_id
        self.handlers = {CONFIG: on_config, CONTROL: on_control, LIVE: on_live if share_live else None}
        self.share_live = share_live
        self._task: asyncio.Task | None = None
        self._conn = None
        self._pending: set[asyncio.Task] = set()
        self.connected = asyncio.Event()

    # ---------------------------------------------------------------- listening

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="cluster-sync")
        try:
            await asyncio.wait_for(self.connected.wait(), 5)
        except asyncio.TimeoutError:
            log.warning("cluster sync: not connected yet — retrying in the background")

    async def _run(self) -> None:
        import asyncpg
        delay, first = 1.0, True
        while True:
            try:
                self._conn = await asyncpg.connect(self.dsn, ssl=self.ssl, timeout=10)
                for channel, handler in self.handlers.items():
                    if handler is not None:
                        await self._conn.add_listener(channel, self._on_notify)
                self.connected.set()
                if not first and self.handlers[CONFIG]:        # changes may have been missed while disconnected
                    await self.handlers[CONFIG]({"kind": "resync"})
                first, delay = False, 1.0
                log.info("cluster sync: listening as %s", self.worker_id)
                while not self._conn.is_closed():
                    await asyncio.sleep(5)
                    await self._conn.execute("SELECT 1")        # notice a dead connection
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("cluster sync: connection lost (%r) — retrying in %.0f s", e, delay)
            finally:
                self.connected.clear()
                if self._conn is not None and not self._conn.is_closed():
                    try:
                        await self._conn.close(timeout=2)
                    except Exception:
                        pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    def _on_notify(self, _conn, _pid, channel: str, payload: str) -> None:
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        if msg.get("origin") == self.worker_id or not (handler := self.handlers.get(channel)):
            return
        task = asyncio.get_running_loop().create_task(self._handle(channel, handler, msg))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _handle(self, channel: str, handler: Handler, msg: dict[str, Any]) -> None:
        try:
            await handler(msg)
        except Exception as e:
            log.warning("cluster sync: %s handler failed: %r", channel, e)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ---------------------------------------------------------------- sending

    async def notify(self, channel: str, msg: dict[str, Any] | str) -> None:
        from runtime.data.db import get_pool
        payload = msg if isinstance(msg, str) else json.dumps({**msg, "origin": self.worker_id},
                                                              ensure_ascii=False, default=str)
        pool = await get_pool()
        async with pool.acquire() as c:
            await c.execute("SELECT pg_notify($1, $2)", channel, payload)

    async def config_changed(self, kind: str, **detail: Any) -> None:
        await self.notify(CONFIG, {"kind": kind, **detail})

    async def instruct_call(self, call_id: str, text: str) -> None:
        await self.notify(CONTROL, {"action": "instruct", "call_id": call_id, "text": text})

    async def end_call(self, call_id: str) -> None:
        await self.notify(CONTROL, {"action": "end", "call_id": call_id})

    async def __call__(self, event) -> None:
        """Event-bus sink: share this worker's call events with the other workers' live hubs."""
        if not self.share_live or not event.call_id:
            return
        payload = compact_event({**event.model_dump(mode="json"), "origin": self.worker_id})
        if payload is None:
            return
        try:
            await self.notify(LIVE, payload)
        except Exception as e:                     # live view is best effort; never slow a call down
            log.debug("cluster sync: live event not shared: %r", e)
