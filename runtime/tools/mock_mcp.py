"""In-process mock of the HIS MCP server for offline development, tests and evals.

Schemas come from the discovery snapshot (mcp_tools.json) so the harness sees exactly the
production tool definitions; responses come from handlers you register per tool.

    mock = MockMCP()
    mock.on("mssql_get_clinics_for_project", lambda args: [{"ClinicID": 10, "ClinicName": "Dermatology"}])
"""

from collections.abc import Callable
from typing import Any

from .catalog import snapshot_schemas

Handler = Callable[[dict[str, Any]], Any]


class MockMCP:
    def __init__(self, schemas: dict[str, dict[str, Any]] | None = None) -> None:
        self._schemas = schemas if schemas is not None else snapshot_schemas()
        self._handlers: dict[str, Handler] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def on(self, name: str, handler: Handler | Any) -> "MockMCP":
        self._handlers[name] = handler if callable(handler) else (lambda _args, v=handler: v)
        return self

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def schemas(self) -> dict[str, dict[str, Any]]:
        return self._schemas

    async def call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> tuple[bool, Any]:
        self.calls.append((name, arguments))
        if name not in self._handlers:
            return False, f"mock: no handler for {name}"
        result = self._handlers[name](arguments)
        if isinstance(result, Exception):
            raise result
        return True, result
