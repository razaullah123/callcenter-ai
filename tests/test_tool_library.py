"""Phase 12.4: tool library — generic harness (roles / hooks / claims), policies, API tools, console API."""

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.config import get_settings
from runtime.harness.policy import Policy, unbacked_claim
from runtime.harness.session import Session
from runtime.platform import toollib
from runtime.platform.loader import AgentLoader
from runtime.platform.seed import seed
from runtime.platform.store import WORKSPACE, MemoryStore
from runtime.tools import MockMCP, ToolContext, ToolDef, ToolError
from runtime.tools.catalog import load_tools_config
from runtime.tools.http_tool import build_request

HARNESS = Path(__file__).resolve().parent.parent / "runtime" / "harness"


def test_harness_code_names_no_tool():
    """Tool-specific behaviour lives in tool policies + packs, never in the harness (prompts.py is default data)."""
    names = {n for entries in load_tools_config()["skills"].values() for n in entries}
    offenders = []
    for f in HARNESS.rglob("*.py"):
        if f.name == "prompts.py":
            continue
        text = f.read_text(encoding="utf-8")
        offenders += [f"{f.name}: {n}" for n in names if re.search(rf"\b{re.escape(n)}\b", text)]
    assert offenders == []


def tool(name, **kw):
    base = dict(skill="authenticate", kind="read", source="mcp", description="", input_schema={})
    return ToolDef(name=name, **{**base, **kw})


async def test_verification_follows_roles_not_names():
    """A CRM with its own tool / argument names verifies callers the same way."""
    s = Session(call_id="c")
    lookup = tool("crm_find_customer", role="identity.lookup", role_args={"mobile": "phone"})
    send = tool("crm_send_pin", kind="send", role="identity.send_code",
                role_args={"channel": "via", "default_channel": "SMS"})
    verify = tool("crm_check_pin", role="identity.verify_code", role_args={"code": "pin"})

    class Cat:
        def by_role(self, role):
            return {t.role: t for t in (lookup, send, verify)}.get(role)
    p = Policy(s, Cat())
    s.last_user_text = "رقمي صفر خمسة خمسة واحد اثنين ثلاثة أربعة خمسة ستة سبعة"
    assert (await p.pre(lookup, {}, ToolContext(call_id="c")))["phone"] == "0551234567"
    assert (await p.pre(send, {}, ToolContext(call_id="c")))["via"] == "SMS"
    s.last_user_text = "the code is 4 6 7 3 1 5"
    assert (await p.pre(verify, {}, ToolContext(call_id="c")))["pin"] == "467315"
    with pytest.raises(ToolError):                      # not verified: other groups are closed
        await p.pre(tool("crm_orders", skill="orders"), {}, ToolContext(call_id="c"))


def test_claims_come_from_what_tools_back():
    assert unbacked_claim("تم حجز موعدك بنجاح", done=set(), pending=None) == "booked"
    assert unbacked_claim("تم حجز موعدك بنجاح", done={"booked"}, pending=None) is None
    assert unbacked_claim("Your appointment is booked.", done={"booked"}, pending="booked") == "booked"   # still waiting
    assert unbacked_claim("I've sent you the details", done={"booked"}, pending=None) == "sent"
    assert unbacked_claim("Which day suits you?", done=set(), pending=None) is None


def test_hmg_policies_declare_roles_hooks_and_claims():
    cfg = {n: p for entries in load_tools_config()["skills"].values() for n, p in entries.items()}
    assert cfg["mssql_get_patient_info"]["role"] == "identity.lookup"
    assert cfg["api_book_Appointment"]["backs"] == "booked" and cfg["api_book_Appointment"]["success_line"] == "BOOKED_LINE"
    assert "hmg.slot_offered" in cfg["api_book_Appointment"]["hooks"]
    for name, policy in cfg.items():                     # every policy in the repo is valid
        errors = toollib.validate_policy(name, policy, mcp_tools=set(cfg), local_tools=set(cfg))
        assert errors == [], (name, errors)


def test_policy_validation():
    v = lambda p, **kw: toollib.validate_policy("t", p, mcp_tools={"t"}, local_tools=set(), **kw)   # noqa: E731
    assert v({"kind": "read"}) == []
    assert "kind must be one of read, write, send" in v({"kind": "delete"})
    assert "read tools need no confirmation" in v({"kind": "read", "confirm": "readback"})
    assert any("unknown hooks" in e for e in v({"kind": "read", "hooks": ["nope.hook"]}))
    assert any("backs" in e for e in v({"kind": "write", "backs": "teleported"}))
    assert any("success_line" in e for e in v({"kind": "write", "success_line": "NOPE"}))
    assert any("unknown fields" in e for e in v({"kind": "read", "model": "x"}))
    assert any("no connected MCP server" in e for e in toollib.validate_policy(
        "ghost", {"kind": "read"}, mcp_tools=set(), local_tools=set()))
    http = {"kind": "read", "source": "http", "description": "Track a parcel",
            "input_schema": {"type": "object", "properties": {"no": {"type": "string"}}},
            "http": {"method": "GET", "url": "https://api.example.com/track/{no}"}}
    assert v(http) == []
    assert any("http.url" in e for e in v({**http, "http": {"url": "ftp://x"}}))


def test_http_request_building():
    m, url, headers, query, body = build_request(
        {"method": "get", "url": "https://x/track/{no}", "headers": {"Authorization": "Bearer k"}},
        {"no": "A 1/2", "lang": "ar"})
    assert (m, url, query, body) == ("GET", "https://x/track/A%201%2F2", {"lang": "ar"}, None)
    m, url, _, query, body = build_request({"method": "POST", "url": "https://x/tickets"}, {"text": "hi"})
    assert (m, query, body) == ("POST", None, {"text": "hi"})


async def test_http_tool_runs_through_the_executor(monkeypatch):
    from runtime.tools import http_tool
    from runtime.tools.factory import executor_for
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return httpx.Response(200, json={"status": "in transit", "eta": "tomorrow"})
    monkeypatch.setattr(http_tool, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    mcp = MockMCP()
    await mcp.start()
    cfg = toollib.put_in_config({}, "track_parcel", "shipping", {
        "kind": "read", "source": "http", "description": "Track a parcel",
        "input_schema": {"type": "object", "properties": {"no": {"type": "string"}}, "required": ["no"]},
        "http": {"method": "GET", "url": "https://api.example.com/track/{no}", "headers": {"Authorization": "Bearer k"}}})
    ex = executor_for(mcp, cfg)
    from runtime.events import EventBus
    from runtime.providers import ToolCall
    r = await ex.execute(ToolCall(id="1", name="track_parcel", arguments={"no": "SA123"}),
                         ToolContext(call_id="c"), EventBus().bind())
    assert r.ok and r.data["eta"] == "tomorrow"
    assert seen == {"url": "https://api.example.com/track/SA123", "auth": "Bearer k"}
    assert "track_parcel" in {t["function"]["name"] for t in ex.llm_tools()}


async def test_unknown_turn_hook_is_rejected():
    from runtime.skills import SkillSet
    mcp = MockMCP()
    await mcp.start()
    from runtime.tools.factory import executor_for
    with pytest.raises(ValueError, match="unknown turn hooks"):
        SkillSet(executor_for(mcp).catalog, {"x": {"SKILL.md": "---\nturn_hooks: [no.such]\n---\nhi"}})


# ---------------------------------------------------------------- library + console API


async def test_publish_and_unpublish_a_tool():
    store = MemoryStore()
    await seed(store, get_settings())
    policy = {"kind": "read", "source": "http", "description": "FAQ search",
              "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
              "http": {"method": "GET", "url": "https://faq.example.com/search"}}
    out = await toollib.publish_tool(store, "faq_search", "home", policy, agents=["hmg-care"], author="t", note="")
    assert out[0]["version"] == 2
    rel = await store.release((await store.agent("hmg-care"))["published_release_id"])
    assert toollib.tools_in(rel["bundle"]["tools"])["faq_search"] == ("home", policy)
    assert (await toollib.usage(store))["faq_search"] == ["hmg-care"]
    assert (await toollib.unpublish_tool(store, "faq_search", "hmg-care", "t"))["version"] == 3
    assert "faq_search" not in (await toollib.usage(store))


class _Rt:
    def __init__(self, store, backend, loader, agent, secrets=None):
        self.platform, self.backend, self.loader, self.agent, self.secrets = store, backend, loader, agent, secrets
        self.settings = get_settings()
        self.changes = []

    async def config_changed(self, kind, **detail):
        self.changes.append(kind)
        self.loader.invalidate()
        self.agent = await self.loader.for_call()

    async def agent_for_call(self, agent_id=None, number=None):
        return await self.loader.for_call(agent_id=agent_id, number=number)

    def mcp_status(self):
        return {"hmg_tools": {"connected": True, "tools": list(self.backend.schemas()), "error": None}}


@pytest.fixture
def api(monkeypatch):
    import asyncio

    from runtime.control import tools_api
    from runtime.server import app as server_app
    loop = asyncio.new_event_loop()
    store = MemoryStore()

    async def setup():
        await seed(store, get_settings())
        mcp = MockMCP()
        await mcp.start()
        loader = AgentLoader(store, mcp, get_settings(), warm_phrases=False)
        return _Rt(store, mcp, loader, await loader.for_call())
    rt = loop.run_until_complete(setup())
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    app.include_router(tools_api.router)
    with TestClient(app) as c:
        yield c, rt, store
    loop.close()


def test_library_listing(api):
    c, rt, store = api
    d = c.get("/api/tool-library").json()
    by = {t["name"]: t for t in d["tools"]}
    assert by["api_book_Appointment"]["used_by"] == ["hmg-care"] and by["api_book_Appointment"]["available"]
    assert by["find_hospital_by_name"]["source"] == "local"
    assert "hmg.slot_offered" in d["choices"]["hooks"] and "identity.lookup" in d["choices"]["roles"]
    assert {x["name"] for x in d["discovered"]} and not ({x["name"] for x in d["discovered"]} & set(by))


def test_edit_policy_publishes_a_release(api):
    c, rt, store = api
    before = rt.agent.version
    policy = {"kind": "read", "cache_ttl": 120, "hooks": ["hmg.pick_offered_project", "hmg.clinic_list"]}
    r = c.put("/api/tool-library/mssql_get_clinics_for_project", json={"group": "book_appointment", "policy": policy})
    assert r.status_code == 200, r.text
    assert rt.changes == ["tool"] and rt.agent.version == before + 1
    assert rt.agent.executor.catalog.get("mssql_get_clinics_for_project").cache_ttl == 120
    bad = c.put("/api/tool-library/mssql_get_clinics_for_project",
                json={"group": "book_appointment", "policy": {"kind": "read", "hooks": ["typo.hook"]}})
    assert bad.status_code == 422 and "unknown hooks" in bad.text


def test_add_an_api_tool_and_remove_it(api):
    c, rt, store = api
    policy = {"kind": "read", "source": "http", "description": "Opening hours of a branch",
              "input_schema": {"type": "object", "properties": {"branch": {"type": "string"}}},
              "http": {"method": "GET", "url": "https://hours.example.com/{branch}"}}
    assert c.put("/api/tool-library/branch_hours", json={"group": "home", "policy": policy}).status_code == 200
    assert "branch_hours" in rt.agent.executor.catalog                    # a new tool joins the default agent
    assert c.delete("/api/tool-library/branch_hours").status_code == 409  # still used
    assert c.delete("/api/tool-library/branch_hours?agent=hmg-care").status_code == 200
    assert c.delete("/api/tool-library/branch_hours").status_code == 200
    # a tool its skills use can't be removed from the agent
    r = c.delete("/api/tool-library/api_book_Appointment?agent=hmg-care")
    assert r.status_code == 409 and "book_appointment" in r.text


def test_only_read_tools_can_be_tested(api):
    c, rt, store = api
    assert c.post("/api/tool-library/api_book_Appointment/test", json={"args": {}}).status_code == 400
    r = c.post("/api/tool-library/find_hospital_by_name/test", json={"args": {"name": "العليا"}}).json()
    assert "ok" in r and "ms" in r


def test_http_tool_auth_types():
    from runtime.tools.http_tool import auth_errors, auth_headers
    assert auth_headers({"type": "bearer", "token": "k"}) == {"Authorization": "Bearer k"}
    assert auth_headers({"type": "basic", "username": "u", "password": "p"}) == {"Authorization": "Basic dTpw"}
    assert auth_headers({"type": "api_key", "header": "X-Key", "value": "v"}) == {"X-Key": "v"}
    assert auth_headers(None) == {} and auth_headers({"type": "none"}) == {}
    _, _, headers, _, _ = build_request({"url": "https://x/a", "headers": {"A": "1"},
                                         "auth": {"type": "bearer", "token": "t"}}, {})
    assert headers == {"A": "1", "Authorization": "Bearer t"}
    assert auth_errors({"type": "bearer"}) == ["http.auth.token is required for bearer"]
    assert auth_errors({"type": "oauth"}) and auth_errors(None) == []
    policy = {"kind": "read", "source": "http", "description": "x",
              "http": {"url": "https://x", "auth": {"type": "basic", "username": "u"}}}
    assert any("password" in e for e in toollib.validate_policy("t", policy, mcp_tools=set(), local_tools=set()))


def test_api_tool_test_run_with_overrides(api, monkeypatch):
    from runtime.tools import http_tool
    c, rt, store = api
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers.get("x-test"), request.headers.get("authorization")))
        return httpx.Response(200, json={"open": "9-5"})
    monkeypatch.setattr(http_tool, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    policy = {"kind": "read", "source": "http", "description": "Opening hours of a branch",
              "input_schema": {"type": "object", "properties": {"branch": {"type": "string"}}},
              "http": {"method": "GET", "url": "https://hours.example.com/{branch}",
                       "auth": {"type": "bearer", "token": "abc"}}}
    assert c.put("/api/tool-library/branch_hours", json={"group": "home", "policy": policy}).status_code == 200
    r = c.post("/api/tool-library/branch_hours/test", json={"args": {"branch": "olaya"}}).json()
    assert r["ok"] and r["data"] == {"open": "9-5"}
    r = c.post("/api/tool-library/branch_hours/test", json={
        "args": {"branch": "olaya"},
        "override": {"url": "https://staging.example.com/{branch}", "method": "POST", "headers": {"X-Test": "1"}}}).json()
    assert r["ok"]
    assert seen == [("GET", "https://hours.example.com/olaya", None, "Bearer abc"),
                    ("POST", "https://staging.example.com/olaya", "1", "Bearer abc")]     # auth kept, rest overridden
    bad = c.post("/api/tool-library/branch_hours/test", json={"args": {}, "override": {"url": "file:///etc"}})
    assert bad.status_code == 422


async def test_releases_from_before_roles_still_verify_callers():
    """A schema-1 release (12.1–12.3) loads with the roles / hooks / turn hooks it didn't have yet."""
    from runtime.platform.bundle import repo_bundle
    mcp = MockMCP()
    await mcp.start()
    old = repo_bundle(get_settings())
    old["schema"] = 1
    for entries in old["tools"]["skills"].values():
        for name, policy in entries.items():
            entries[name] = {k: v for k, v in policy.items() if k not in ("role", "args", "hooks", "backs", "success_line")}
    old["skill_files"]["book_appointment"]["SKILL.md"] = old["skill_files"]["book_appointment"]["SKILL.md"].replace(
        "turn_hooks: [hmg.booking]", "")
    agent = await AgentLoader(None, mcp, get_settings(), warm_phrases=False).build(
        old, agent_id="hmg-care", name="x", release_id=1, version=1)
    assert agent.executor.catalog.by_role("identity.verify_code").name == "api_verify_otp"
    assert "hmg.slot_offered" in agent.executor.catalog.get("api_book_Appointment").hooks
    assert agent.skills.turn_hook_names("book_appointment") == ["hmg.booking"]
