"""List the tools exposed by the configured MCP servers and save their schemas to mcp_tools.json.

Usage (from the project root, venv active):
    python scripts/discover_mcp.py
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.config import get_settings  # noqa: E402


async def list_server_tools(cfg: dict) -> tuple[dict | None, list[types.Tool]]:
    if cfg["transport"] != "streamable_http":
        raise ValueError(f"unsupported transport: {cfg['transport']}")
    # MCP SDK 2.x: headers are configured on the httpx client, not on the transport.
    async with create_mcp_http_client(headers=cfg["headers"]) as http_client:
        async with streamable_http_client(cfg["url"], http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                server = session.server_info
                tools: list[types.Tool] = []
                cursor = None
                while True:
                    params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
                    page = await session.list_tools(params=params)
                    tools.extend(page.tools)
                    cursor = page.next_cursor
                    if not cursor:
                        break
    return (server.model_dump(mode="json") if server else None), tools


async def main() -> None:
    out: dict[str, dict] = {}
    for name, cfg in get_settings().mcp_server_config().items():
        server, tools = await list_server_tools(cfg)
        out[name] = {
            "server": server,
            "tools": [t.model_dump(mode="json", exclude_none=True, by_alias=True) for t in tools],
        }
        print(f"[{name}] {server['name'] if server else cfg['url']} — {len(tools)} tools:")
        for t in tools:
            first_line = (t.description or "").strip().splitlines()[0][:90] if t.description else ""
            print(f"  - {t.name}: {first_line}")

    (ROOT / "mcp_tools.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nFull schemas saved to mcp_tools.json (contains no credentials).")


if __name__ == "__main__":
    asyncio.run(main())
