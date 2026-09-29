"""Hedged LLM requests: when the first response is slow to start, race an identical second request.

Groq's first-token latency spikes (live: 0.6 s and 8 s for similar requests in one call). If nothing has arrived
after `after_s`, a duplicate request starts; whichever produces its first event first is streamed and the other
is cancelled. A failing attempt doesn't fail the call while the other is still running.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from .base import LLMEvent, LLMProvider, Message, ToolSpec

_END = object()


class HedgedLLM(LLMProvider):
    def __init__(self, inner: LLMProvider, after_s: float,
                 on_hedge: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.inner, self.after_s, self.on_hedge = inner, after_s, on_hedge
        self.settings = getattr(inner, "settings", None)

    async def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                     **options: Any) -> AsyncIterator[LLMEvent]:
        q: asyncio.Queue = asyncio.Queue()

        async def attempt(i: int) -> None:
            try:
                async for ev in self.inner.stream(messages, tools=tools, **options):
                    await q.put((i, ev))
                await q.put((i, _END))
            except asyncio.CancelledError:
                raise
            except Exception as e:            # reported through the queue; the other attempt may still win
                await q.put((i, e))

        t0 = time.perf_counter()
        tasks = [asyncio.create_task(attempt(0))]
        winner: int | None = None
        failed: set[int] = set()
        try:
            while True:
                wait = self.after_s if winner is None and len(tasks) == 1 else None
                try:
                    i, item = await asyncio.wait_for(q.get(), wait)
                except asyncio.TimeoutError:
                    tasks.append(asyncio.create_task(attempt(1)))
                    if self.on_hedge:
                        self.on_hedge({"after_ms": round((time.perf_counter() - t0) * 1000)})
                    continue
                if winner is None:
                    if isinstance(item, Exception):
                        failed.add(i)
                        if len(failed) < len(tasks):      # the other attempt is still running
                            continue
                        raise item
                    winner = i
                    for j, t in enumerate(tasks):
                        if j != i:
                            t.cancel()
                    if len(tasks) > 1 and self.on_hedge:
                        self.on_hedge({"winner": "backup" if i == 1 else "first",
                                       "first_event_ms": round((time.perf_counter() - t0) * 1000)})
                if i != winner:
                    continue
                if item is _END:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()


def hedged(llm: LLMProvider, after_s: float, on_hedge=None) -> LLMProvider:
    return HedgedLLM(llm, after_s, on_hedge) if after_s and after_s > 0 else llm
