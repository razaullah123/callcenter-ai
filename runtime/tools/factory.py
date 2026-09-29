"""Assemble the tool layer: MCP backend (live pool or mock) + local tools + catalog + executor."""

from runtime.config import get_settings

from . import local_tools, summarizers  # noqa: F401 — registers local tools + result summarizers
from .catalog import Catalog
from .executor import ToolExecutor
from .local import local_schemas
from .mcp_client import MCPBackend, MCPPool
from .mock_mcp import MockMCP


async def build_tooling(mcp: MCPBackend | None = None, *, mock: bool = False) -> ToolExecutor:
    if mcp is None:
        mcp = MockMCP() if mock else MCPPool(get_settings().mcp_server_config())
    await mcp.start()
    catalog = Catalog.load(mcp.schemas(), local_schemas())
    return ToolExecutor(catalog, mcp)
