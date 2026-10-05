"""Projects (Hamsa's project switcher): separate agents, connections, secrets, tools, MCP servers and routes per
project; the console's current project comes in the X-Project header."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.config import get_settings
from runtime.platform.loader import AgentLoader
from runtime.platform.seed import seed
from runtime.platform.store import MemoryStore
from runtime.tools import MockMCP

from .test_studio import _Rt

CLINIC = {"X-Project": "clinic"}


@pytest.fixture
def console(monkeypatch):
    from runtime.control import agents_api, api as control_api, connections, projects_api, tools_api
    from runtime.server import app as server_app
    loop = asyncio.new_event_loop()
    store = MemoryStore()

    async def setup():
        await seed(store, get_settings())
        await store.put_mcp_server({"id": "hmg_tools", "workspace_id": "hmg", "name": "hmg_tools",
                                    "url": "http://mcp.local/mcp", "auth": {"secret": "MCP_AUTH_TOKEN"}})
        await store.put_secret("hmg", "MCP_AUTH_TOKEN", "cipher-text", "…1234", "test")
        mcp = MockMCP()
        await mcp.start()
        loader = AgentLoader(store, mcp, get_settings(), warm_phrases=False)
        return _Rt(store, mcp, loader, await loader.for_call())
    rt = loop.run_until_complete(setup())
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    for r in (agents_api.router, projects_api.router, connections.router, tools_api.router, control_api.router):
        app.include_router(r)
    with TestClient(app) as c:
        yield c, rt, store, loop


def test_create_a_project_with_the_current_setup(console):
    c, rt, store, loop = console
    r = c.post("/api/projects", json={"name": "Clinic", "copy_setup": True})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "clinic" and r.json()["copied"]["mcp_servers"] == 1
    projects = {p["id"]: p for p in c.get("/api/projects").json()}
    assert projects["hmg"]["platform_default"] and projects["hmg"]["agents"] == 1
    assert projects["clinic"]["agents"] == 0 and projects["clinic"]["connections"] == projects["hmg"]["connections"]
    servers = loop.run_until_complete(store.mcp_servers("clinic"))
    assert [m["name"] for m in servers] == ["hmg_tools-clinic"]                 # unique across projects
    assert loop.run_until_complete(store.secret("clinic", "MCP_AUTH_TOKEN"))["ciphertext"] == "cipher-text"
    assert "mcp" in rt.changes
    # a second project with the same name gets its own id; empty when nothing is copied
    r2 = c.post("/api/projects", json={"name": "Clinic", "copy_setup": False}).json()
    assert r2["id"] == "clinic-2" and r2["copied"] == {}
    assert c.put("/api/projects/clinic", json={"name": "Clinic Riyadh"}).json()["name"] == "Clinic Riyadh"
    assert c.put("/api/projects/nope", json={"name": "x"}).status_code == 404


def test_agents_are_separate_per_project(console):
    c, rt, store, loop = console
    c.post("/api/projects", json={"name": "Clinic"})
    assert [a["id"] for a in c.get("/api/agents", headers=CLINIC).json()] == []
    r = c.post("/api/agents", json={"name": "Clinic FAQ"}, headers=CLINIC)
    assert r.status_code == 200, r.text
    assert [a["id"] for a in c.get("/api/agents", headers=CLINIC).json()] == ["clinic-faq"]
    assert "clinic-faq" not in [a["id"] for a in c.get("/api/agents").json()]           # default project
    assert c.get("/api/agents/clinic-faq").status_code == 404                           # wrong project
    assert c.get("/api/agents/hmg-care", headers=CLINIC).status_code == 404
    detail = c.get("/api/agents/clinic-faq", headers=CLINIC).json()
    # its models use the clinic's own copies of the connections, its tools the clinic's MCP server
    assert all(m["provider"].startswith("clinic-") for m in detail["bundle"]["models"].values() if m.get("provider"))
    assert detail["bundle"]["tools"]["server"] == "hmg_tools-clinic"
    assert {p["id"] for p in detail["connections"]} == {p["id"] for p in loop.run_until_complete(store.providers("clinic"))}
    agent = loop.run_until_complete(rt.loader.for_call(agent_id="clinic-faq"))   # builds in its own project
    assert agent.agent_id == "clinic-faq"
    assert c.get("/api/agents", headers={"X-Project": "nope"}).status_code == 404


def test_a_number_belongs_to_one_project(console):
    c, rt, store, loop = console
    c.post("/api/projects", json={"name": "Clinic"})
    c.post("/api/agents", json={"name": "Clinic FAQ"}, headers=CLINIC)
    assert c.put("/api/routes", json={"pattern": "8880*", "agent_id": "clinic-faq"}, headers=CLINIC).status_code == 200
    taken = c.put("/api/routes", json={"pattern": "8880*", "agent_id": "hmg-care"})
    assert taken.status_code == 409 and "clinic" in taken.text
    assert c.put("/api/routes", json={"pattern": "*", "agent_id": "clinic-faq"}, headers=CLINIC).status_code == 409
    assert [r["pattern"] for r in c.get("/api/routes", headers=CLINIC).json()] == ["8880*"]
    # calls are routed across projects
    assert loop.run_until_complete(rt.loader.route(number="88801234")) == "clinic-faq"
    assert loop.run_until_complete(rt.loader.route(number="0551234567")) == "hmg-care"


def test_mcp_server_names_are_unique_across_projects(console):
    c, rt, store, loop = console
    c.post("/api/projects", json={"name": "Clinic", "copy_setup": False})
    r = c.post("/api/mcp-servers", headers=CLINIC, json={"name": "hmg_tools", "url": "http://other/mcp"})
    assert r.status_code == 409 and "hmg" in r.text


def test_project_audit_log(console):
    c, rt, store, loop = console
    c.post("/api/projects", json={"name": "Clinic"})
    c.post("/api/agents", json={"name": "Clinic FAQ"}, headers=CLINIC)
    c.put("/api/routes", json={"pattern": "8880*", "agent_id": "clinic-faq"}, headers=CLINIC)
    c.put("/api/secrets/CLINIC_KEY", json={"value": "sk-very-secret-value"}, headers=CLINIC)
    c.put("/api/projects/clinic", json={"name": "Clinic Riyadh"})
    log = c.get("/api/projects/clinic/audit").json()
    actions = [a["action"] for a in log]
    assert actions[0] == "project.renamed" and log[0]["detail"] == {"from": "Clinic", "to": "Clinic Riyadh"}
    assert {"project.created", "agent.created", "route.set"} <= set(actions)
    created = next(a for a in log if a["action"] == "agent.created")
    assert created["agent_id"] == "clinic-faq" and created["detail"]["name"] == "Clinic FAQ"
    assert "sk-very-secret-value" not in str(log)                      # never secret values
    assert all(a["workspace_id"] == "clinic" for a in log)
    assert "agent.created" not in [a["action"] for a in c.get("/api/projects/hmg/audit").json()]


def test_agent_models_page_works_on_the_projects_agent(console):
    c, rt, store, loop = console
    c.post("/api/projects", json={"name": "Clinic"})
    assert c.get("/api/providers", headers=CLINIC).status_code == 404            # no agent yet
    c.post("/api/agents", json={"name": "Clinic FAQ"}, headers=CLINIC)
    r = c.get("/api/providers", headers=CLINIC)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"]["id"] == "clinic-faq" and [a["id"] for a in body["agents"]] == ["clinic-faq"]
    assert c.get("/api/providers").json()["agent"]["id"] == "hmg-care"
    assert c.get("/api/providers", params={"agent": "hmg-care"}, headers=CLINIC).status_code == 404


def test_live_view_shows_only_the_projects_calls():
    from runtime.control.live import LiveHub
    from runtime.events import Event, EventType
    hub = LiveHub()
    for cid, agent in (("c1", "hmg-care"), ("c2", "clinic-faq"), ("c3", None)):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(hub(Event(type=EventType.CALL_START, call_id=cid, data={"agent_id": agent})))
        loop.close()
    clinic, hmg = (["clinic-faq"], False), (["hmg-care"], True)
    assert [c["call_id"] for c in hub.active_calls(clinic)] == ["c2"]
    assert sorted(c["call_id"] for c in hub.active_calls(hmg)) == ["c1", "c3"]     # old calls: default project
    assert len(hub.active_calls()) == 3
    # a console subscribes with the (list, bool) scope project_scope() returns
    q = hub.subscribe(None, clinic)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(hub(Event(type=EventType.TURN_START, call_id="c1", turn_id=1, data={"text": "hi"})))
    loop.run_until_complete(hub(Event(type=EventType.TURN_START, call_id="c2", turn_id=1, data={"text": "hi"})))
    loop.close()
    msgs = [json.loads(m) for m in iter(lambda: q.get_nowait() if not q.empty() else None, None)]
    seen = {m["event"]["call_id"] for m in msgs if m["kind"] == "event"}
    assert "c2" in seen and "c1" not in seen
    hub.unsubscribe(q)


def test_call_queries_are_scoped():
    from runtime.control.store import _calls_of, _scope_sql
    args: list = []
    assert _scope_sql(None, args) == "true" and args == []
    assert _scope_sql((["a", "b"], False), args) == "agent_id = ANY($1::text[])" and args == [["a", "b"]]
    assert _scope_sql((["a"], True), args) == "(agent_id = ANY($2::text[]) OR agent_id IS NULL)"
    assert _calls_of((["a"], False), args).startswith("call_id IN (SELECT call_id FROM calls WHERE agent_id")


def test_voices_page(console):
    import base64
    c, rt, store, loop = console
    from runtime.control import voices_api
    c.app.include_router(voices_api.router)
    rows = c.get("/api/voices").json()
    assert [r["agent"] for r in rows] == ["hmg-care"] and set(rows[0]["voices"]) == {"ar", "en"}

    class FakeTTS:
        async def synthesize(self, text, *, language="ar", voice=None, **kw):
            from runtime.providers.base import AudioChunk
            assert voice == "lulwa" and language == "en"
            yield AudioChunk(data=b"\x00\x01" * 2400, sample_rate=24000)
    rt.agent.providers.tts = FakeTTS()
    r = c.post("/api/voices/preview", json={"language": "en", "voice": "lulwa"}).json()
    assert base64.b64decode(r["audio"])[:4] == b"RIFF" and r["seconds"] == 0.1


def test_voice_catalog(console):
    c, rt, store, loop = console
    from runtime.control import voices_api
    c.app.include_router(voices_api.router)
    cat = c.get("/api/voices/catalog").json()
    assert len(cat) == 12 and {v["language"] for v in cat} == {"ar", "en"}
    by = {(v["voice"], v["language"]): v for v in cat}
    assert by[("aisha", "ar")]["used_by"] == [{"agent": "hmg-care", "name": "HMG Customer Care"}]
    assert by[("hannah", "en")]["used_by"] and not by[("fahad", "ar")]["used_by"]
    assert by[("fahad", "ar")]["gender"] == "male" and by[("troy", "en")]["dialect"] == "US - EN"


def test_use_this_voice_sets_the_draft(console):
    c, rt, store, loop = console
    from runtime.control import voices_api
    c.app.include_router(voices_api.router)
    r = c.post("/api/voices/use", json={"agent": "hmg-care", "language": "ar", "voice": "lulwa"})
    assert r.status_code == 200, r.text
    assert r.json() == {"agent": "hmg-care", "language": "ar", "voice": "lulwa", "previous": "aisha", "draft": True}
    detail = c.get("/api/agents/hmg-care").json()
    assert detail["has_draft"] and detail["bundle"]["models"]["tts"]["settings"]["voice_ar"] == "lulwa"
    live = loop.run_until_complete(rt.loader.for_call(agent_id="hmg-care"))
    assert live.providers.config["tts"]["settings"]["voice_ar"] == "aisha"            # callers: unchanged until publish
    cat = {(v["voice"], v["language"]): v for v in c.get("/api/voices/catalog").json()}
    assert cat[("lulwa", "ar")]["draft_by"] == [{"agent": "hmg-care", "name": "HMG Customer Care"}]
    assert cat[("aisha", "ar")]["used_by"] and not cat[("aisha", "ar")]["draft_by"]
    assert c.post("/api/voices/use", json={"agent": "hmg-care", "language": "ar", "voice": "troy"}).status_code == 409
    assert c.post("/api/voices/use", json={"agent": "nope", "language": "ar", "voice": "lulwa"}).status_code == 404
    assert c.get("/api/agents/hmg-care/audit").status_code in (200, 404)
    assert any(a["action"] == "voice.changed" for a in c.get("/api/projects/hmg/audit").json())


def test_call_details_name_the_agent_release_and_models(console):
    """The Call History panel shows the agent, its release version and the LLM / STT / voice the call ran on."""
    from runtime.control.api import _call_agent
    c, rt, store, loop = console
    agent = loop.run_until_complete(store.agent("hmg-care"))
    rel = loop.run_until_complete(store.release(agent["published_release_id"]))
    info = loop.run_until_complete(_call_agent({"agent_id": "hmg-care", "release_id": rel["id"], "language": "en"}))
    tts = rel["bundle"]["models"]["tts"]["settings"]
    assert info["name"] == agent["name"] and info["version"] == rel["version"]
    assert info["llm"]["model"] == rel["bundle"]["models"]["llm"]["settings"]["model"]
    assert info["tts"]["voice"] == tts.get("voice_en", tts.get("voice"))
    assert loop.run_until_complete(_call_agent({"agent_id": None})) is None             # calls from before agents


def test_agent_list_like_hamsa(console):
    """Agents list: type (flow / prompt), languages, voice, created time; blank prompt and flow agents."""
    c, rt, store, loop = console
    rows = {a["id"]: a for a in c.get("/api/agents").json()}
    hmg = rows["hmg-care"]
    assert hmg["type"] == "flow" and hmg["languages"] == ["ar", "en"] and hmg["voice"] and hmg["created_at"]
    for kind in ("prompt", "flow"):
        r = c.post("/api/agents", json={"name": f"Info line {kind}", "type": kind})
        assert r.status_code == 200, r.text
    rows = {a["id"]: a for a in c.get("/api/agents").json()}
    assert rows["info-line-prompt"]["type"] == "prompt" and rows["info-line-flow"]["type"] == "flow"
    main = loop.run_until_complete(store.skill_versions("hmg", "info_line_prompt_main"))
    files = loop.run_until_complete(store.skill_version("hmg", "info_line_prompt_main", main[0]["version"]))
    assert "flow.yaml" not in files and "SKILL.md" in files
    loop.run_until_complete(rt.loader.for_call(agent_id="info-line-prompt"))      # a prompt agent loads
    # duplicate = a copy keeps the type
    r = c.post("/api/agents", json={"name": "Info line prompt (Copy)", "copy_from": "info-line-prompt"})
    assert r.status_code == 200 and {a["id"]: a for a in c.get("/api/agents").json()}[r.json()["id"]]["type"] == "prompt"
    assert c.post("/api/agents", json={"name": "x" * 151}).status_code == 422



HAMSA = {
    "id": "h-1", "type": "Flow Agent", "name": "Clinic line (hamsa)",
    "conversation": {"greetingMessage": "أهلاً بك", "preamble": "You are the clinic line. Language id: {{ lang_id }}.",
                     "params": {"lang_id": "1"}},
    "voice": {"lang": "ar", "voiceRecord": {"name": "Jawaher", "provider": "Hamsa"}},
    "llm": {"provider": "OpenAI", "model": "GPT-4.1", "temperature": 0.1, "apiKey": "sk-not-imported"},
    "callSettings": {"silenceThreshold": 800, "languageDialectSwitcher": True},
    "tools": [{"nodeId": "n-tool", "toolId": "t-1",
               "overrides": {"params": {"mobileNo": {"value": "{{search_mobile}}"}}}}],
    "resolvedWebTools": [{"id": "t-1", "name": "HMG — Patient Lookup", "description": "Look a patient up.",
                          "params": {"mobileNo": {"type": "string", "description": "mobile"}},
                          "toolSettings": {"serverUrl": "https://n8n.example/webhook?tool=x", "methodType": "POST",
                                           "httpHeaders": {"Content-Type": "application/json", "X-Key": "secret"},
                                           "timeOut": 30}}],
    "workflow": {
        "customVariables": '[{"name": "search_mobile", "defaultValue": ""}]',
        "nodes": [
            {"id": "n-start", "type": "start", "label": "Greeting & Intent", "message": "Ask what they need.",
             "extractVariables": {"enabled": True, "variables": [{"name": "intent", "extractionPrompt": "book or other"}]},
             "transitions": [{"id": "t1", "priority": 0, "targetNodeId": "n-set",
                              "condition": {"type": "natural_language", "prompt": "the caller wants to book"}}]},
            {"id": "n-set", "type": "set_local_variables", "label": "Init",
             "staticVariables": [{"name": "search_mobile", "value": "{{ userNumber }}"}, {"name": "tries", "value": "0"}],
             "transitions": [{"id": "t2", "condition": {"type": "auto"}, "targetNodeId": "n-tool"}]},
            {"id": "n-tool", "type": "tool", "label": "Lookup Patient",
             "extractVariables": {"enabled": True,
                                  "variables": [{"name": "patient_count", "extractionPrompt": "result.count"}]},
             "transitions": [
                 {"id": "t3", "name": "On Success", "priority": 0, "targetNodeId": "",
                  "condition": {"type": "natural_language", "prompt": "On Success"}},
                 {"id": "t4", "name": "On Failure", "priority": 1, "targetNodeId": "n-end",
                  "condition": {"type": "natural_language", "prompt": "On Failure"}}]},
            {"id": "n-router", "type": "router", "label": "Found",
             "transitions": [
                 {"id": "t5", "priority": 1, "targetNodeId": "n-done",
                  "condition": {"type": "structured_equation", "logic": "all",
                                "conditions": [{"variable": "patient_count", "operator": "equals", "value": "1"}]}},
                 {"id": "t6", "priority": 999, "condition": {"type": "always"}, "targetNodeId": "n-end"}]},
            {"id": "n-done", "type": "conversation", "label": "Found", "messageType": "static", "message": "وجدنا ملفك",
             "transitions": [{"id": "t7", "condition": {"type": "auto"}, "targetNodeId": "n-end"}]},
            {"id": "n-switch", "type": "conversation", "label": "Switch Task", "isGlobal": True,
             "message": "Ask which task.", "globalCondition": "the caller asks for a different task",
             "transitions": [{"id": "t8", "condition": {"type": "after_user_reply"}, "targetNodeId": "n-start"}]},
            {"id": "n-end", "type": "end_call", "label": "End Call", "transitions": []},
        ],
        # the tool's success edge only exists among the canvas edges (its transition has no target)
        "edges": [{"source": "n-tool", "target": "n-router", "sourceHandle": "transition-t3"}],
    },
}
ENCRYPTED = "c8a1a7a905c697e12b420c4a:85abef17328afb6e49cc36f281382f04:623c78f14aeaff57d2"


def test_hamsa_flow_converts():
    from runtime.platform.hamsa_import import convert, is_encrypted, parse
    from runtime.skills.graph import Graph
    assert is_encrypted(ENCRYPTED)
    with pytest.raises(ValueError, match="encrypted"):
        parse(ENCRYPTED)
    r = convert(parse(json.dumps({"success": True, "data": HAMSA})))
    g = Graph.from_dict(r["flow"])
    assert g.errors() == [] and g.start == "init"
    n = g.nodes
    assert n["init"].set == {"search_mobile": "=", "lang_id": "=1"}
    assert n["init_2"].set == {"search_mobile": "{{ userNumber }}", "tries": "=0"}
    tool = n["lookup_patient"]
    assert tool.type == "tool" and tool.tool == "hmg_patient_lookup"
    assert tool.args == {"mobileNo": "{{search_mobile}}"} and tool.outputs == {"patient_count": "result.count"}
    assert n["found_2"].instructions.startswith("Say exactly") and n["end_call"].type == "end"
    edges = {(e.source, e.target): e for e in g.edges}
    assert edges[("lookup_patient", "found")].on == "success" and edges[("lookup_patient", "end_call")].on == "failure"
    assert edges[("found", "found_2")].when == {"all": [{"equals": {"patient_count": "1"}}]}
    assert edges[("found_2", "end_call")].when == {"replied": True}              # Hamsa's "auto" after speaking
    assert edges[("*", "switch_task")].when == {"llm": "the caller asks for a different task"}
    assert edges[("greeting_intent", "init_2")].when == {"llm": "the caller wants to book"}
    assert r["tools"]["hmg_patient_lookup"]["http"]["headers"] == {"Content-Type": "application/json"}
    assert any("X-Key" in line for line in r["report"])
    assert "sk-not-imported" not in json.dumps(r, ensure_ascii=False)          # never the LLM key


def test_flow_templates_outputs_and_replied():
    from runtime.skills.flow import render
    from runtime.skills.graph import Graph
    assert render("{{ '2' if (call_lang|default('')) == 'en' else '1' }}", {"slots": {}, "call_lang": "en"}) == "2"
    assert render("{{ (sip|default({})).headers|default({}) }}", {"slots": {}}) == "{}"
    assert render("{% if x %}broken", {"slots": {}}) == "{% if x %}broken"          # a bad template stays as text
    g = Graph.from_dict({"start": "ask", "nodes": [
        {"id": "ask", "type": "conversation"},
        {"id": "calc", "type": "set", "set": {"n": "{{ (n|default('0')|int) + 1 }}"}},
        {"id": "next", "type": "conversation"}],
        "edges": [{"from": "ask", "to": "calc", "when": {"replied": True}}, {"from": "calc", "to": "next"}]})
    state = {"entered": 0}                                  # `ask` was entered on turn 0
    assert g.current(state, {"_turn": 0}, facts={"_turn": 0}).id == "ask"          # the caller hasn't spoken yet
    slots = {"_turn": 1}
    assert g.current(state, slots, facts={"_turn": 1}).id == "next" and slots["n"] == "1"


def test_import_a_hamsa_agent(console):
    c, rt, store, loop = console
    r = c.post("/api/agents/import", json={"content": json.dumps(HAMSA), "filename": "clinic.json"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == "clinic-line-hamsa" and out["tools"] == ["hmg_patient_lookup"] and out["stats"]["nodes"] == 8
    rows = {a["id"]: a for a in c.get("/api/agents").json()}
    assert rows["clinic-line-hamsa"]["type"] == "flow" and not rows["clinic-line-hamsa"]["routes"]
    agent = loop.run_until_complete(rt.loader.for_call(agent_id="clinic-line-hamsa"))      # it loads
    knobs = agent.bundle["knobs"]
    assert knobs["main_flow"] == "clinic_line_hamsa_flow" and knobs["voice_end_silence_ms"] == 800
    assert agent.bundle["phrases"]["GREETING"]["ar"] == "أهلاً بك"
    assert "sk-not-imported" not in json.dumps(agent.bundle, ensure_ascii=False)
    # a second import gets its own id; an encrypted .hamsa file is refused with an explanation
    assert c.post("/api/agents/import", json={"content": json.dumps(HAMSA)}).json()["id"] == "clinic-line-hamsa-2"
    bad = c.post("/api/agents/import", json={"content": ENCRYPTED})
    assert bad.status_code == 422 and "encrypted" in bad.json()["detail"]
    assert any(row["action"] == "agent.imported" for row in loop.run_until_complete(store.project_audit_log("hmg")))


def test_http_tools_follow_tools_mode(monkeypatch):
    """An imported agent's API tools must not book or send for real while testing (hybrid / mock)."""
    from runtime.config import get_settings
    from runtime.tools.http_tool import simulated
    s = get_settings()
    monkeypatch.setattr(s, "tools_mode", "hybrid")
    monkeypatch.setattr(s, "hybrid_live_booking", False)
    assert simulated("write")[0] and simulated("write")[1]["simulated"] and simulated("send")[1]["simulated"]
    assert simulated("read") is None                                    # lookups are real
    monkeypatch.setattr(s, "hybrid_live_booking", True)
    assert simulated("write") is None
    monkeypatch.setattr(s, "tools_mode", "mock")
    assert simulated("read")[0] is False and simulated("write")[0] is True
    monkeypatch.setattr(s, "tools_mode", "live")
    assert simulated("write") is None
