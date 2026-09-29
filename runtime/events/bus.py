"""Async event bus.

`emit()` never blocks the audio/LLM hot path: events are queued and a background task
fans them out to subscribers. A slow or failing subscriber cannot stall a call.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .schema import Event, EventType, Level

Subscriber = Callable[[Event], Awaitable[None]]
Redactor = Callable[[Event], Event]

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, max_queue: int = 10_000) -> None:
        self._subscribers: list[Subscriber] = []
        self._redactors: list[Redactor] = []
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None
        self.dropped = 0

    def subscribe(self, fn: Subscriber) -> None:
        self._subscribers.append(fn)

    def add_redactor(self, fn: Redactor) -> None:
        """Redactors run once per event before any subscriber sees it (PII masking)."""
        self._redactors.append(fn)

    def emit(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="event-bus")

    async def stop(self) -> None:
        """Drain pending events, then stop."""
        if self._task is None:
            return
        await self._queue.join()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                for redact in self._redactors:
                    event = redact(event)
                for sub in self._subscribers:
                    try:
                        await sub(event)
                    except Exception:
                        log.exception("event subscriber failed")
            finally:
                self._queue.task_done()

    def bind(self, **context: Any) -> "BoundEmitter":
        return BoundEmitter(self, context)


class BoundEmitter:
    """Emitter pre-filled with call context (call_id, skill, step, turn_id).

    Usage:
        ev = bus.bind(call_id="c_1")
        ev.emit(EventType.TOOL_END, tool="get_doctors", latency_ms=212)
        with ev.timed(EventType.LLM_END, model="gpt-oss-120b"):
            ...
    """

    _FIELDS = {"call_id", "turn_id", "skill", "step"}

    def __init__(self, bus: EventBus, context: dict[str, Any]) -> None:
        self.bus = bus
        self.context = dict(context)

    def update(self, **context: Any) -> None:
        self.context.update(context)

    def bind(self, **context: Any) -> "BoundEmitter":
        return BoundEmitter(self.bus, {**self.context, **context})

    def emit(
        self,
        type: EventType,
        *,
        level: Level = Level.INFO,
        latency_ms: float | None = None,
        **data: Any,
    ) -> Event:
        fields = {k: v for k, v in self.context.items() if k in self._FIELDS}
        extra = {k: v for k, v in self.context.items() if k not in self._FIELDS}
        event = Event(type=type, level=level, latency_ms=latency_ms, data={**extra, **data}, **fields)
        self.bus.emit(event)
        return event

    def timed(self, type: EventType, **data: Any) -> "_Timed":
        return _Timed(self, type, data)


class _Timed:
    def __init__(self, emitter: BoundEmitter, type: EventType, data: dict[str, Any]) -> None:
        self.emitter, self.type, self.data = emitter, type, data
        self.start = 0.0

    def __enter__(self) -> "_Timed":
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        ms = round((time.perf_counter() - self.start) * 1000, 1)
        if exc is not None:
            self.emitter.emit(EventType.ERROR, level=Level.ERROR, latency_ms=ms,
                              during=self.type.value, error=repr(exc), **self.data)
        else:
            self.emitter.emit(self.type, latency_ms=ms, **self.data)
