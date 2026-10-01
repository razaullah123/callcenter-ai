"""Assemble the tool layer: MCP backend (live pool or mock) + local tools + catalog + executor."""

from runtime.config import get_settings

from . import local_tools, summarizers  # noqa: F401 — registers local tools + result summarizers
from .catalog import Catalog, load_tools_config
from .executor import ToolExecutor
from .local import local_schemas
from .mcp_client import MCPBackend, MCPPool
from .mock_mcp import MockMCP


async def build_tooling(mcp: MCPBackend | None = None, *, mock: bool = False,
                        tools_config: dict | None = None) -> ToolExecutor:
    if mcp is None:
        mcp = MockMCP() if mock else MCPPool(get_settings().mcp_server_config())
    await mcp.start()
    return executor_for(mcp, tools_config)


def executor_for(mcp: MCPBackend, tools_config: dict | None = None) -> ToolExecutor:
    """An executor over an already-started backend with one agent's tool policies (default: config/tools.yaml)."""
    cfg = tools_config if tools_config is not None else load_tools_config()
    return ToolExecutor(Catalog.from_config(cfg, mcp.schemas(), local_schemas()), mcp)
