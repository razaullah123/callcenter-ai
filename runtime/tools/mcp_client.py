"""Persistent MCP client pool.

One long-lived session per server (opened at startup, shared by all calls), so tool calls
don't pay connection + initialize cost. A background task owns each session's context
managers; on a transport failure it reconnects with backoff.
"""

import asyncio
import json
import logging
from typing import Any, Protocol

from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

log = logging.getLogger(__name__)


class MCPBackend(Protocol):
    """Interface shared by the real pool and the mock server."""

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def schemas(self) -> dict[str, dict[str, Any]]: ...
    async def call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> tuple[bool, Any]: ...


class _ServerConnection:
    def __init__(self, name: str, cfg: dict[str, Any]) -> None:
        if cfg.get("transport", "streamable_http") != "streamable_http":
            raise ValueError(f"{name}: unsupported MCP transport {cfg.get('transport')!r}")
        self.name, self.url, self.headers = name, cfg["url"], cfg.get("headers", {})
        self.session: ClientSession | None = None
        self.tools: list[types.Tool] = []
        self._ready = asyncio.Event()
        self._restart = asyncio.Event()
        self._closing = asyncio.Event()
        self._error: BaseException | None = None
        self._task: asyncio.Task | None = None

    async def start(self, timeout: float = 20) -> None:
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self.name}")
        await asyncio.wait_for(self._ready.wait(), timeout)
        if self.session is None:
            raise ConnectionError(f"MCP server {self.name} unavailable: {self._error!r}")

    async def _run(self) -> None:
        backoff = 0.5
        while not self._closing.is_set():
            try:
                async with create_mcp_http_client(headers=self.headers) as http:
                    async with streamable_http_client(self.url, http_client=http) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            self.tools = await self._list_tools(session)
                            self.session, self._error, backoff = session, None, 0.5
                            self._ready.set()
                            log.info("MCP %s connected: %d tools", self.name, len(self.tools))
                            waiters = [asyncio.create_task(e.wait()) for e in (self._restart, self._closing)]
                            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                            for w in waiters:
                                w.cancel()
            except Exception as e:  # connection / protocol failure → retry
                self._error = e
                log.warning("MCP %s connection error: %r", self.name, e)
            finally:
                self.session = None
                self._restart.clear()
            self._ready.set()  # unblock start() even on failure
            if not self._closing.is_set():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)
                self._ready.clear()

    @staticmethod
    async def _list_tools(session: ClientSession) -> list[types.Tool]:
        tools, cursor = [], None
        while True:
            page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
            tools.extend(page.tools)
            if not (cursor := page.next_cursor):
                return tools

    async def call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> types.CallToolResult:
        if self.session is None:
            await asyncio.wait_for(self._ready.wait(), timeout_s)
        if self.session is None:
            raise ConnectionError(f"MCP server {self.name} not connected: {self._error!r}")
        try:
            return await self.session.call_tool(name, arguments, read_timeout_seconds=timeout_s)
        except (ConnectionError, OSError):
            self._restart.set()   # reconnect in the background for the next call
            raise

    async def close(self) -> None:
        self._closing.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, 5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()


class MCPPool:
    def __init__(self, server_config: dict[str, dict[str, Any]]) -> None:
        self._servers = {name: _ServerConnection(name, cfg) for name, cfg in server_config.items()}
        self._route: dict[str, _ServerConnection] = {}

    async def start(self) -> None:
        results = await asyncio.gather(*(s.start() for s in self._servers.values()), return_exceptions=True)
        for server, r in zip(self._servers.values(), results):
            if isinstance(r, BaseException):      # keeps retrying in the background; its tools appear when it's up
                log.warning("MCP %s not connected at start: %r", server.name, r)
        self._build_routes()

    def _build_routes(self) -> None:
        for server in self._servers.values():
            for tool in server.tools:
                self._route.setdefault(tool.name, server)

    async def close(self) -> None:
        await asyncio.gather(*(s.close() for s in self._servers.values()))

    def schemas(self) -> dict[str, dict[str, Any]]:
        return {t.name: {"description": t.description or "", "input_schema": t.input_schema}
                for s in self._servers.values() for t in s.tools}

    def status(self) -> dict[str, dict[str, Any]]:
        """Per server: connected?, its tools, the last connection error."""
        return {name: {"connected": s.session is not None, "tools": [t.name for t in s.tools],
                       "error": repr(s._error)[:200] if s._error else None} for name, s in self._servers.items()}

    async def call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> tuple[bool, Any]:
        server = self._route.get(name)
        if server is None:
            self._build_routes()                   # a server that was down at start may be connected now
            server = self._route.get(name)
        if server is None:
            raise KeyError(f"no MCP server provides tool {name!r}")
        result = await server.call(name, arguments, timeout_s)
        return (not result.is_error), parse_result(result)


async def discover(name: str, cfg: dict[str, Any], timeout: float = 15) -> list[dict[str, Any]]:
    """Connect to a server once (without touching the running pool) and list its tools."""
    conn = _ServerConnection(name, cfg)
    try:
        await conn.start(timeout)
        return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                for t in conn.tools]
    finally:
        await conn.close()


def parse_result(result: types.CallToolResult) -> Any:
    """Structured content if present, else text content (JSON-decoded when possible)."""
    if result.structured_content is not None:
        return result.structured_content
    texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    text = "\n".join(texts)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
