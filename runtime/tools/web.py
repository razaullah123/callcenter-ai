"""Web tools (Hamsa parity: Tools → Web Tool): functions that run in the visitor's browser.

A web tool is *defined* in the tool library (name, description, parameter schema — source `web`); it is *implemented* by the website
that embeds the agent, which registers a function of the same name (`VoiceAgent.registerTools`, see docs/web_tools.md). When the model
calls the tool, the server asks the visitor's page through the call's socket, the widget runs the website's function and sends the
result back, which becomes the tool's result. Definitions only come from the dashboard: a page can supply implementations, it can't
add tools to the agent.

They exist only on web calls whose page registered them (public link / embed widget). On a phone call, or when the page did not
register a tool, the model is never offered it, and a flow's tool node for it takes its failure edge.

Wire protocol (public call socket): the page's `start` message carries `web_tools: [names it can run]`; the server sends
`{"event": "web_tool", "id", "name", "args"}`; the page answers `{"event": "web_tool_result", "id", "result"}` or `{"event": "web_tool_result", "id", "error"}`.
"""

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

MAX_RESULT_CHARS = 20_000          # what a page may send back
MAX_NAMES = 50
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,99}$")


class WebToolError(Exception):
    """The page could not run the tool (not registered, threw, answered too late, the call ended)."""


def clean_names(raw: Any) -> set[str]:
    """The tool names a page says it can run: at most MAX_NAMES valid names, whatever else it sent is dropped."""
    if not isinstance(raw, list):
        return set()
    return set([n for n in raw if isinstance(n, str) and _NAME.match(n)][:MAX_NAMES])


class WebToolBridge:
    """The call's link to the visitor's page: `call()` asks it to run a tool and waits for the answer."""

    def __init__(self, send: Callable[[dict], Awaitable[None]]) -> None:
        self._send = send
        self._pending: dict[str, asyncio.Future] = {}

    async def call(self, name: str, args: dict[str, Any], timeout_s: float) -> Any:
        call_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[call_id] = fut
        try:
            await self._send({"event": "web_tool", "id": call_id, "name": name, "args": args})
            return await asyncio.wait_for(fut, timeout_s)
        except asyncio.TimeoutError:
            raise WebToolError(f"the page did not answer within {timeout_s:g} s") from None
        finally:
            self._pending.pop(call_id, None)

    def resolve(self, call_id: Any, result: Any = None, error: Any = None) -> bool:
        """The page's answer to a request (False: nobody is waiting for it — late, repeated or invented)."""
        fut = self._pending.get(call_id) if isinstance(call_id, str) else None
        if fut is None or fut.done():
            return False
        if error is not None:
            fut.set_exception(WebToolError(str(error)[:300] or "the page's function failed"))
            return True
        try:
            if len(json.dumps(result, ensure_ascii=False, default=str)) > MAX_RESULT_CHARS:
                fut.set_exception(WebToolError(f"the result is larger than {MAX_RESULT_CHARS:,} characters"))
                return True
        except (TypeError, ValueError):
            fut.set_exception(WebToolError("the result is not plain data"))
            return True
        fut.set_result(result)
        return True

    def close(self) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(WebToolError("the call ended"))
        self._pending.clear()
