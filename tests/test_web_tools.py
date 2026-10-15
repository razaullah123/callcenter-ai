"""Web tools (Hamsa parity): functions defined in the dashboard, implemented by the embedding website, run in the visitor's browser."""

import asyncio
import json

import pytest

from runtime.events import EventBus
from runtime.platform import toollib
from runtime.providers import ToolCall
from runtime.tools import ToolContext, ToolDef
from runtime.tools.catalog import Catalog
from runtime.tools.web import MAX_RESULT_CHARS, WebToolBridge, WebToolError, clean_names


SCHEMA = {"type": "object", "properties": {"path": {"type": "string", "description": "the page"}}, "required": ["path"]}
POLICY = {"kind": "read", "source": "web", "description": "Open a page of the website", "input_schema": SCHEMA}


def validate(policy, name="navigate_to_page"):
    return toollib.validate_policy(name, policy, mcp_tools=set(), local_tools=set())


# ---------------------------------------------------------------- the definition (dashboard)

def test_a_web_tool_policy_is_validated():
    assert validate(POLICY) == []
    assert validate({**POLICY, "async": True}) == []                                  # unlike read tools of other sources
    assert any("description is required" in e for e in validate({**POLICY, "description": ""}))
    assert any("JSON schema object" in e for e in validate({**POLICY, "input_schema": {"type": "array"}}))
    assert any("no http settings" in e for e in validate({**POLICY, "http": {"url": "https://x"}}))
    assert any("function name" in e for e in validate(POLICY, name="1bad"))
    assert any("async" in e for e in validate({"kind": "read", "source": "http", "description": "x", "async": True,
                                               "http": {"url": "https://x.example/a"}}))      # other read tools still can't


def test_the_catalog_builds_a_web_tool_from_its_own_definition():
    cat = Catalog.from_config({"skills": {"site": {"navigate_to_page": POLICY}}}, {}, {})
    t = cat.get("navigate_to_page")
    assert t.source == "web" and t.input_schema == SCHEMA and t.description.startswith("Open a page") and t.http is None


# ---------------------------------------------------------------- the bridge

def test_which_names_a_page_may_offer():
    assert clean_names(["navigate_to_page", "a-b", "1x", "", "has space", 5, None, "x" * 101]) == {"navigate_to_page", "a-b"}
    assert clean_names("navigate") == set() and clean_names(None) == set() and clean_names({}) == set()
    assert len(clean_names([f"t{i}" for i in range(200)])) == 50


async def test_the_bridge_asks_the_page_and_waits_for_its_answer():
    sent = []
    bridge = WebToolBridge(lambda m: _record(sent, bridge, m, result={"title": "Pricing"}))
    assert await bridge.call("navigate_to_page", {"path": "/pricing"}, 2) == {"title": "Pricing"}
    assert sent[0]["event"] == "web_tool" and sent[0]["name"] == "navigate_to_page" and sent[0]["args"] == {"path": "/pricing"}
    assert bridge._pending == {}


async def _record(sent, bridge, msg, **answer):
    sent.append(msg)
    asyncio.get_running_loop().call_soon(bridge.resolve, msg["id"], answer.get("result"), answer.get("error"))


async def test_the_bridge_reports_what_went_wrong():
    sent = []
    bridge = WebToolBridge(lambda m: _record(sent, bridge, m, error="boom"))
    with pytest.raises(WebToolError, match="boom"):
        await bridge.call("t", {}, 2)
    silent = WebToolBridge(lambda m: asyncio.sleep(0))
    with pytest.raises(WebToolError, match="did not answer within 0.05 s"):
        await silent.call("t", {}, 0.05)
    big = WebToolBridge(lambda m: _record([], big, m, result="x" * (MAX_RESULT_CHARS + 1)))
    with pytest.raises(WebToolError, match="larger than"):
        await big.call("t", {}, 2)
    odd = WebToolBridge(lambda m: _record([], odd, m, result={"a": {1, 2}}))                # sets aren't JSON, default=str makes them text
    assert await odd.call("t", {}, 2) == {"a": {1, 2}}


async def test_an_answer_nobody_waits_for_is_ignored_and_the_end_of_the_call_cancels_waiting():
    bridge = WebToolBridge(lambda m: asyncio.sleep(0))
    assert not bridge.resolve("nope", "x") and not bridge.resolve(None, "x") and not bridge.resolve(5, "x")
    waiting = asyncio.create_task(bridge.call("t", {}, 5))
    await asyncio.sleep(0.01)
    bridge.close()
    with pytest.raises(WebToolError, match="call ended"):
        await waiting


# ---------------------------------------------------------------- the executor

async def _executor():
    from runtime.tools import MockMCP
    from runtime.tools.factory import executor_for
    mcp = MockMCP()
    await mcp.start()
    cfg = toollib.put_in_config({}, "navigate_to_page", "site", dict(POLICY))
    cfg = toollib.put_in_config(cfg, "slow_report", "site", {**POLICY, "async": True, "description": "Build a report"})
    return executor_for(mcp, cfg)


def _run(ex, name, args, bridge=None):
    ctx = ToolContext(call_id="c", extra={"web_bridge": bridge} if bridge else {})
    return ex.execute(ToolCall(id="1", name=name, arguments=args), ctx, EventBus().bind())


async def test_the_executor_runs_a_web_tool_through_the_bridge():
    ex = await _executor()
    sent = []
    bridge = WebToolBridge(lambda m: _record(sent, bridge, m, result="Navigated to /pricing"))
    r = await _run(ex, "navigate_to_page", {"path": "/pricing"}, bridge)
    assert r.ok and r.data == {"result": "Navigated to /pricing"} and sent[0]["args"] == {"path": "/pricing"}
    bridge2 = WebToolBridge(lambda m: _record([], bridge2, m, result={"total": 42}))
    assert (await _run(ex, "navigate_to_page", {"path": "/cart"}, bridge2)).data == {"total": 42}      # JSON stays JSON


async def test_a_web_tool_fails_cleanly_without_a_page_or_when_the_page_fails():
    ex = await _executor()
    r = await _run(ex, "navigate_to_page", {"path": "/x"})                                          # a phone call: no bridge
    assert not r.ok and "visitor's browser" in r.content
    bridge = WebToolBridge(lambda m: _record([], bridge, m, error="the website has not registered navigate_to_page"))
    r = await _run(ex, "navigate_to_page", {"path": "/x"}, bridge)
    assert not r.ok and "not registered" in r.content
    assert not (await _run(ex, "navigate_to_page", {}, bridge)).ok                                  # arguments are still checked first


async def test_an_async_web_tool_answers_at_once():
    ex = await _executor()
    ran = []
    bridge = WebToolBridge(lambda m: _record(ran, bridge, m, result="ok"))
    r = await _run(ex, "slow_report", {"path": "/a"}, bridge)
    assert r.ok and r.data == {"success": True, "queued": True}
    await asyncio.gather(*ex._background)
    assert ran and ran[0]["name"] == "slow_report"                                                  # and it still ran in the page


# ---------------------------------------------------------------- the agent: offered only where the page registered it

FLOW = """
start: go
nodes:
  - id: go
    type: tool
    tool: navigate_to_page
    args: {path: "=/pricing"}
    say: {en: "Opened {{ title }}."}
    outputs: {title: result.title}
  - {id: ok, type: conversation, instructions: "Tell them."}
  - {id: bad, type: conversation, instructions: "Apologise."}
edges:
  - {from: go, to: ok, on: success}
  - {from: go, to: bad, on: failure}
"""


SKILL_MD = "---\ndescription: Line\n---\nBe brief."


async def _agent():
    """An agent whose catalog has the web tool (as a release's tools config would give it) and the flow above."""
    from runtime.config import get_settings
    from runtime.data.reference import set_reference
    from runtime.harness.engine import Agent
    from runtime.harness.session import Session
    from runtime.skills import SkillSet
    from runtime.tools import MockMCP
    from runtime.tools.factory import executor_for

    from .test_flow_options import Out
    from .test_harness import ScriptedLLM
    from .test_local_tools import REF
    set_reference(REF)
    mcp = MockMCP()
    await mcp.start()
    executor = executor_for(mcp, toollib.put_in_config({}, "navigate_to_page", "line", {**POLICY, "timeout_s": 3}))
    skills = SkillSet(executor.catalog, {"line": {"SKILL.md": SKILL_MD, "flow.yaml": FLOW}})
    settings = get_settings().model_copy(update={"require_verification": False, "entry_skill": "line"})
    llm, out = ScriptedLLM(), Out()
    s = Session(call_id="web-1", ani="+966548802968", agent_name="Line")
    s.language.language = "en"
    agent = Agent(s, executor, llm, skills, out, EventBus().bind(), filler_after_s=5, settings=settings)
    await agent.start()
    out.said.clear()
    return agent, s, llm, out


async def test_a_web_tool_is_offered_only_when_the_page_registered_it():
    agent, s, llm, out = await _agent()
    skills_tools = lambda skill, session: ["navigate_to_page", "a_plain_tool"]                     # noqa: E731
    agent.skills.tools = skills_tools
    assert agent._allowed() == {"a_plain_tool"}                                                    # no page: never offered
    s.web_tools = {"something_else"}
    assert agent._allowed() == {"a_plain_tool"}
    s.web_tools = {"navigate_to_page"}
    assert agent._allowed() == {"navigate_to_page", "a_plain_tool"}
    assert "navigate_to_page" in {t["function"]["name"] for t in agent.executor.llm_tools(agent._allowed())}


async def test_a_flow_tool_node_uses_the_web_tool_and_maps_its_result():
    agent, s, llm, out = await _agent()
    s.web_tools = {"navigate_to_page"}
    bridge = WebToolBridge(lambda m: _record([], bridge, m, result={"title": "Pricing"}))
    agent.web_bridge = bridge
    llm.then("Here you go.")
    await agent.handle("go")
    assert out.said[0] == "Opened Pricing." and s.slots["title"] == "Pricing" and agent.skills.node("line", s).id == "ok"


async def test_a_flow_tool_node_takes_the_failure_edge_on_a_call_without_a_page():
    agent, s, llm, out = await _agent()                                                            # web_tools empty: a phone call
    llm.then("Sorry.")
    await agent.handle("go")
    assert agent.skills.node("line", s).id == "bad" and "Opened" not in " ".join(out.said)


# ---------------------------------------------------------------- the real socket

from .test_public import public  # noqa: E402, F401 — the fixture: a published agent behind /ws/public/{token}


def _speak(ws, loop_seconds=1.0):
    """One second of "speech" then silence, as 20 ms pcm16 frames @ 16 kHz — what the page's microphone would send."""
    import base64

    import numpy as np
    tone = (np.sin(np.arange(16000 * 1) / 3) * 16000).astype(np.int16).tobytes()
    for frame in [tone[i:i + 640] for i in range(0, len(tone), 640)] + [bytes(640)] * 40:
        ws.send_text(json.dumps({"event": "media", "payload": base64.b64encode(frame).decode()}))


def _web_agent(rt, loop, llm):
    from runtime.config import Settings
    from runtime.control.config_store import ProviderSet
    from runtime.platform.loader import LoadedAgent
    from runtime.skills import SkillSet
    from runtime.tools import MockMCP
    from runtime.tools.factory import build_tooling

    from runtime.providers import Transcript

    from .test_ivr import FakeTTS

    class SaysPricing:                                   # what the caller says (the shared fake asks for a human)
        async def transcribe(self, audio, *, language=None, prompt=None):
            return Transcript("show me the pricing page", "en", audio.duration_ms)
    executor = loop.run_until_complete(build_tooling(MockMCP()))
    executor.catalog.tools["navigate_to_page"] = ToolDef(name="navigate_to_page", skill="line", kind="read", source="web",
                                                         description=POLICY["description"], input_schema=SCHEMA, timeout_s=5)
    skills = SkillSet(executor.catalog, {"line": {"SKILL.md": SKILL_MD, "flow.yaml": FLOW}})
    settings = Settings(database_url=None, voice_end_silence_ms=300, require_verification=False, entry_skill="line")
    loaded = LoadedAgent.from_parts(settings=settings, providers=ProviderSet(0, {}, llm, SaysPricing(), FakeTTS()), skills=skills, executor=executor)
    loaded.agent_id, loaded.name = "line", "Line Agent"
    rt._loaded = loaded


def _talk(c, token, names, answer):
    """Open a call announcing `names`; answer every web_tool request with `answer(msg)`; return (asked, spoken lines, call)."""
    asked, spoken = [], []
    with c.websocket_connect(f"/ws/public/{token}", headers={"origin": "http://testserver"}) as ws:
        ws.send_text(json.dumps({"event": "start", "audio": {"encoding": "pcm16", "sample_rate": 16000}, "web_tools": names}))
        ready = json.loads(ws.receive_text())
        assert ready["event"] == "ready"
        _speak(ws)
        for _ in range(400):
            m = json.loads(ws.receive_text())
            if m["event"] == "web_tool":
                asked.append(m)
                ws.send_text(json.dumps({"event": "web_tool_result", "id": m["id"], **answer(m)}))
            elif m["event"] == "transcript" and m["role"] == "agent":
                spoken.append(m["text"])
                if any("Opened" in t or "Sorry" in t for t in spoken):
                    break
        ws.send_text(json.dumps({"event": "stop"}))
    return asked, spoken


def test_the_page_registers_a_tool_and_the_agent_runs_it_over_the_socket(public):
    c, rt, publish, loop = public
    _web_agent(rt, loop, ScriptedLLM().then("Anything else?"))
    asked, spoken = _talk(c, publish(), ["navigate_to_page", "bogus tool!", 5], lambda m: {"result": {"title": "Pricing"}})
    assert [(m["name"], m["args"]) for m in asked] == [("navigate_to_page", {"path": "/pricing"})]
    assert any("Opened Pricing." in t for t in spoken)


def test_a_page_that_says_it_cannot_run_the_tool_makes_the_flow_take_its_failure_edge(public):
    c, rt, publish, loop = public
    _web_agent(rt, loop, ScriptedLLM().then("Sorry, I could not open it."))
    asked, spoken = _talk(c, publish(), ["navigate_to_page"], lambda m: {"error": "the website has not registered navigate_to_page"})
    assert len(asked) == 1 and not any("Opened" in t for t in spoken) and any("Sorry" in t for t in spoken)


def test_a_page_that_registered_nothing_never_gets_asked(public):
    c, rt, publish, loop = public
    _web_agent(rt, loop, ScriptedLLM().then("Sorry, I could not open it."))
    asked, spoken = _talk(c, publish(), [], lambda m: {"result": "x"})
    assert asked == [] and any("Sorry" in t for t in spoken)                                      # no page tools: the tool node fails at once


import pytest as _pytest  # noqa: E402 — keep the helpers above readable
del _pytest

from .test_harness import ScriptedLLM  # noqa: E402


# ---------------------------------------------------------------- the browser scripts (need Node; skipped without it)

def _node():
    import shutil
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    return node


def test_the_widget_script_registers_runs_and_guards_the_sites_functions(tmp_path):
    import subprocess
    from pathlib import Path

    from runtime.server.public_page import EMBED_JS
    script = tmp_path / "embed.js"
    script.write_text(EMBED_JS, encoding="utf-8")
    run = subprocess.run([_node(), str(Path(__file__).parent / "js" / "embed_harness.js"), str(script)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    got = json.loads(run.stdout)["checks"]
    assert got["names"] == ["boom", "from_global", "navigate_to_page"]                      # functions only, from both ways to register
    assert got["names_origin"] == "https://agent.example"                                   # answered to the widget's own origin
    assert got["string_result"] == {"type": "hmg-web-tool-result", "id": "1", "result": "went to /pricing"}
    assert got["global_result"]["result"] == {"echo": {"a": 1}}
    assert got["throws"] == {"type": "hmg-web-tool-result", "id": "3", "error": "nope"}
    assert got["missing"]["error"] == "the website has not registered missing"
    assert got["ignored_foreign"] is True and got["frame_removed"] is True                  # other windows / origins / a closed widget: nothing


def test_both_browser_scripts_are_valid_javascript(tmp_path):
    import re
    import subprocess

    from runtime.server.public_page import EMBED_JS, PAGE_HTML
    page = "\n;\n".join(re.findall(r"<script[^>]*>(.*?)</script>", PAGE_HTML, re.S))
    for name, code in (("page.js", page), ("embed.js", EMBED_JS)):
        f = tmp_path / name
        f.write_text(code, encoding="utf-8")
        run = subprocess.run([_node(), "--check", str(f)], capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, f"{name}: {run.stderr}"
    assert "hmg-web-tool" in page and "web_tool_result" in page and "web_tools: webTools" in page


def test_the_page_script_relays_web_tool_requests():
    from runtime.server.public_page import PAGE_HTML
    assert 'm.event === "web_tool"' in PAGE_HTML and "runWebTool(m, mine)" in PAGE_HTML
