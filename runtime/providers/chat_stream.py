"""Parser for OpenAI-format streaming chat completions (Groq, OpenAI, vLLM, Together, …)."""

import json
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from .base import LLMDone, LLMEvent, TextDelta, ToolCall, ToolCallsReady


async def parse_chat_stream(stream: AsyncIterable[Any]) -> AsyncIterator[LLMEvent]:
    """Yields text deltas as they arrive; tool calls once, fully assembled, at the end."""
    partial: dict[int, dict[str, str]] = {}
    finish_reason = None
    usage: dict[str, Any] = {}
    async for chunk in stream:
        # Groq reports usage in `x_groq.usage`; OpenAI-style servers in `usage` (with include_usage).
        for holder in (getattr(chunk, "x_groq", None), chunk):
            u = getattr(holder, "usage", None) if holder is not None else None
            if u:
                usage = u.model_dump() if hasattr(u, "model_dump") else dict(u)
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if delta.content:
            yield TextDelta(delta.content)
        for tc in delta.tool_calls or []:
            slot = partial.setdefault(tc.index, {"id": "", "name": "", "args": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function and tc.function.name:
                slot["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                slot["args"] += tc.function.arguments
        if choice.finish_reason:
            finish_reason = choice.finish_reason

    if partial:
        yield ToolCallsReady([_assemble(p) for _, p in sorted(partial.items())])
    yield LLMDone(finish_reason=finish_reason, usage=usage)


def _assemble(p: dict[str, str]) -> ToolCall:
    try:
        args = json.loads(p["args"]) if p["args"].strip() else {}
    except json.JSONDecodeError:
        args = {"__invalid_json__": p["args"]}   # executor reports this back to the LLM
    return ToolCall(id=p["id"], name=p["name"], arguments=args, raw_arguments=p["args"])
