"""Shrink tool results before they reach the LLM.

Raw HIS responses carry many fields the voice agent never says. Less context = faster
first token and fewer mistakes. The full result is still kept in `ToolResult.data`.
Tool-specific summarizers are registered with `@summarizer("tool_name")`.
"""

import json
from collections.abc import Callable
from typing import Any

MAX_CHARS = 4000
MAX_LIST = 15

Summarizer = Callable[[Any], Any]
_SUMMARIZERS: dict[str, Summarizer] = {}


def summarizer(*tool_names: str):
    def deco(fn: Summarizer) -> Summarizer:
        for name in tool_names:
            _SUMMARIZERS[name] = fn
        return fn
    return deco


def prune(value: Any, max_list: int = MAX_LIST) -> Any:
    """Drop null / empty values recursively and cap list length (noting how many were cut)."""
    if isinstance(value, dict):
        out = {k: prune(v, max_list) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        items = [prune(v, max_list) for v in value[:max_list]]
        if len(value) > max_list:
            items.append({"_more": len(value) - max_list})
        return items
    return value


def to_llm_content(tool_name: str, data: Any) -> str:
    fn = _SUMMARIZERS.get(tool_name)
    compact = fn(data) if fn else prune(data)
    text = compact if isinstance(compact, str) else json.dumps(compact, ensure_ascii=False, separators=(",", ":"),
                                                               default=str)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "…(truncated)"
    return text
