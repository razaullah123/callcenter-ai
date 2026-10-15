"""Phase 12.6: Agent Studio API — agents, drafts, canvas graphs, publish / rollback, routes."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.config import get_settings
from runtime.platform.loader import AgentLoader
from runtime.platform.seed import seed
from runtime.platform.store import MemoryStore
from runtime.tools import MockMCP


class _Rt:
    def __init__(self, store, backend, loader, agent):
        self.platform, self.backend, self.loader, self.agent = store, backend, loader, agent
        self.settings, self.secrets, self.changes = get_settings(), None, []

    async def config_changed(self, kind, **detail):
        self.changes.append(kind)
        self.loader.invalidate()
        self.agent = await self.loader.for_call()


@pytest.fixture
def studio(monkeypatch):
    from runtime.control import agents_api
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
    app.include_router(agents_api.router)
    with TestClient(app) as c:
        yield c, rt, store, loop
    loop.close()


def test_new_blank_agent_loads_without_verification(studio):
    c, rt, store, loop = studio
    r = c.post("/api/agents", json={"name": "Clinic FAQ", "description": "Opening hours and directions"})
    assert r.status_code == 200, r.text and r.json()["id"] == "clinic-faq"
    agents = {a["id"]: a for a in c.get("/api/agents").json()}
    assert agents["clinic-faq"]["version"] == 1 and agents["hmg-care"]["default"]
    agent = loop.run_until_complete(rt.loader.for_call(agent_id="clinic-faq"))
    assert agent.settings.require_verification is False and agent.settings.entry_skill == "clinic_faq_main"
    assert agent.phrases.GREETING["en"] == "Hello, how can I help you today?"
    assert agent.skills.persona("en").startswith("You are a helpful assistant that will answer users questions")   # its own persona
    detail = c.get("/api/agents/clinic-faq").json()
    assert detail["skills"]["clinic_faq_main"]["flow"]["graph"]["start"] == "start"
    assert detail["skills"]["_persona"]["library"] == "clinic_faq_persona"
    assert c.post("/api/agents", json={"name": "Clinic FAQ"}).status_code == 409


def test_canvas_edit_is_a_draft_until_published(studio):
    c, rt, store, loop = studio
    c.post("/api/agents", json={"name": "Clinic FAQ"})
    graph = c.get("/api/agents/clinic-faq").json()["skills"]["clinic_faq_main"]["flow"]["graph"]
    graph["nodes"].append({"id": "bye", "type": "end", "say": {"en": "Goodbye!", "ar": "مع السلامة"},
                           "position": {"x": 300, "y": 0}})
    graph["edges"].append({"from": "start", "to": "bye", "when": {"llm": "the caller says goodbye"}})
    r = c.put("/api/agents/clinic-faq/draft/skills/clinic_faq_main", json={"graph": graph})
    assert r.status_code == 200, r.text and r.json()["version"] == 2
    detail = c.get("/api/agents/clinic-faq").json()
    assert detail["has_draft"] and detail["bundle"]["skills"]["clinic_faq_main"] == 2
    live = loop.run_until_complete(rt.loader.for_call(agent_id="clinic-faq"))
    assert "bye" not in live.skills.graph("clinic_faq_main").nodes                 # callers still get v1
    draft = loop.run_until_complete(rt.loader.load_draft("clinic-faq"))
    assert "bye" in draft.skills.graph("clinic_faq_main").nodes                    # test calls see the draft
    # a broken graph never becomes a version
    bad = dict(graph, edges=graph["edges"] + [{"from": "start", "to": "nowhere"}])
    r = c.put("/api/agents/clinic-faq/draft/skills/clinic_faq_main", json={"graph": bad})
    assert r.status_code == 422 and "nowhere" in r.text
    # publish → v2 for callers, draft cleared
    r = c.post("/api/agents/clinic-faq/publish", json={"note": "goodbye node"})
    assert r.status_code == 200 and r.json()["version"] == 2 and rt.changes == ["release", "release"]
    assert not c.get("/api/agents/clinic-faq").json()["has_draft"]
    rt.loader.invalidate()
    live = loop.run_until_complete(rt.loader.for_call(agent_id="clinic-faq"))
    assert "bye" in live.skills.graph("clinic_faq_main").nodes
    assert c.post("/api/agents/clinic-faq/publish", json={}).status_code == 400    # nothing to publish
    # rollback to v1
    v1 = next(r for r in c.get("/api/agents/clinic-faq").json()["releases"] if r["version"] == 1)
    assert c.post(f"/api/agents/clinic-faq/activate/{v1['id']}").json()["version"] == 1
    assert c.get(f"/api/agents/clinic-faq/releases/{v1['id']}").json()["bundle"]["skills"]["clinic_faq_main"] == 1


def test_draft_settings_are_checked(studio):
    c, rt, store, loop = studio
    c.post("/api/agents", json={"name": "Clinic FAQ"})
    bundle = c.get("/api/agents/clinic-faq").json()["bundle"]
    bundle["knobs"]["voice_end_silence_ms"] = 700
    bundle["phrases"]["GREETING"] = {"ar": "هلا", "en": "Hi there!"}
    assert c.put("/api/agents/clinic-faq/draft", json={"bundle": bundle}).status_code == 200
    draft = loop.run_until_complete(rt.loader.load_draft("clinic-faq"))
    assert draft.settings.voice_end_silence_ms == 700 and draft.phrases.GREETING["en"] == "Hi there!"
    bad = dict(bundle, knobs={**bundle["knobs"], "teleport": 1})
    assert c.put("/api/agents/clinic-faq/draft", json={"bundle": bad}).status_code == 422
    bad = dict(bundle, skills={**bundle["skills"], "ghost": 7})
    r = c.put("/api/agents/clinic-faq/draft", json={"bundle": bad})
    assert r.status_code == 422 and "ghost" in r.text
    assert c.post("/api/agents/clinic-faq/draft/discard").status_code == 200
    assert not c.get("/api/agents/clinic-faq").json()["has_draft"]


def test_copy_an_agent_and_route_calls_to_it(studio):
    c, rt, store, loop = studio
    r = c.post("/api/agents", json={"name": "HMG Jeddah", "copy_from": "hmg-care"})
    assert r.status_code == 200
    assert c.put("/api/routes", json={"pattern": "9200*", "agent_id": "hmg-jeddah", "priority": 5}).status_code == 200
    assert c.put("/api/routes", json={"pattern": "call me", "agent_id": "hmg-jeddah"}).status_code == 422
    agent = loop.run_until_complete(rt.loader.for_call(number="9200123"))
    assert agent.agent_id == "hmg-jeddah" and agent.executor.catalog.by_role("identity.lookup")
    assert c.delete("/api/agents/hmg-jeddah").status_code == 409                     # a route points to it
    assert c.delete("/api/routes?pattern=9200*").status_code == 200
    assert c.delete("/api/agents/hmg-jeddah").status_code == 200
    assert c.delete("/api/agents/hmg-care").status_code == 409                       # the default agent
    assert c.delete("/api/routes?pattern=*").status_code == 409


async def test_most_specific_route_wins():
    store = MemoryStore()
    await seed(store, get_settings())
    for pattern, agent in (("88*", "a"), ("8880*", "b"), ("88801234", "c")):
        await store.put_agent({"id": agent, "workspace_id": "hmg", "name": agent})
        await store.put_route("hmg", pattern, agent)
    loader = AgentLoader(store, None, get_settings(), warm_phrases=False)
    assert await loader.route(number="88801234") == "c"
    assert await loader.route(number="88809999") == "b"
    assert await loader.route(number="8812") == "a"
    assert await loader.route(number="0550000000") == "hmg-care"


def test_every_tool_of_the_project_is_usable_by_every_agent(studio):
    """An MCP / library tool belongs to the project: a new agent has it, and a flow may use a tool added after the agent was made."""
    c, rt, store, loop = studio
    library = {r["name"] for r in loop.run_until_complete(store.tools("hmg"))}
    assert library
    c.post("/api/agents", json={"name": "Clinic FAQ"})
    assert library <= set(c.get("/api/agents/clinic-faq").json()["tools"])               # a new agent starts with the project's tools
    loop.run_until_complete(store.put_tools("hmg", [{"name": "late_tool", "grp": "misc", "source": "http", "policy": {
        "kind": "read", "source": "http", "description": "added later", "input_schema": {"type": "object", "properties": {}},
        "http": {"method": "GET", "url": "https://example.test/x"}}}]))
    assert "late_tool" not in c.get("/api/agents/clinic-faq").json()["tools"]
    graph = c.get("/api/agents/clinic-faq").json()["skills"]["clinic_faq_main"]["flow"]["graph"]
    graph["nodes"].append({"id": "look", "type": "tool", "tool": "late_tool", "position": {"x": 300, "y": 0}})
    graph["edges"].append({"from": "start", "to": "look", "when": {"llm": "the caller asks"}})
    r = c.put("/api/agents/clinic-faq/draft/skills/clinic_faq_main", json={"graph": graph})
    assert r.status_code == 200, r.text
    assert "late_tool" in c.get("/api/agents/clinic-faq").json()["tools"]                # joined the agent when the flow was saved
