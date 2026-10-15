"""One shared MCP connection pool, but a project only sees and calls its own servers."""

from types import SimpleNamespace

import pytest

from runtime.tools.hybrid import HybridMCP
from runtime.tools.mcp_client import MCPPool


class FakeServer:
    def __init__(self, name, tools):
        self.name = name
        self.tools = [SimpleNamespace(name=t, description=f"{t} of {name}", input_schema={"type": "object"}) for t in tools]
        self.session = object()
        self._error = None
        self.calls = []

    async def call(self, tool, arguments, timeout_s):
        self.calls.append(tool)
        return SimpleNamespace(is_error=False, structured_content={"from": self.name}, content=[])


def pool():
    p = MCPPool({})
    p._servers = {"clinic_a": FakeServer("clinic_a", ["lookup", "only_a"]), "clinic_b": FakeServer("clinic_b", ["lookup", "only_b"])}
    return p


def test_a_project_sees_only_its_servers_tools():
    p = pool()
    a, b = p.scoped({"clinic_a"}), p.scoped({"clinic_b"})
    assert set(a.schemas()) == {"lookup", "only_a"} and set(b.schemas()) == {"lookup", "only_b"}
    assert set(a.status()) == {"clinic_a"} and set(p.scoped(set()).schemas()) == set()


async def test_the_same_tool_name_goes_to_the_calling_projects_own_server():
    p = pool()
    assert (await p.scoped({"clinic_b"}).call("lookup", {}, 5)) == (True, {"from": "clinic_b"})
    assert (await p.scoped({"clinic_a"}).call("lookup", {}, 5)) == (True, {"from": "clinic_a"})
    with pytest.raises(KeyError):
        await p.scoped({"clinic_a"}).call("only_b", {}, 5)           # another project's tool is not reachable


def test_the_hybrid_backend_scopes_too():
    h = HybridMCP(pool(), echo=None).scoped({"clinic_a"})
    assert set(h.status()) == {"clinic_a"} and "only_b" not in h.schemas()
