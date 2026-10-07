"""Hamsa parity (Flow Agent): keypad (DTMF) in flows, transfer node options, tool node behaviour, global node options."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from runtime.config import get_settings
from runtime.data.reference import set_reference
from runtime.events import EventBus
from runtime.harness.engine import Agent
from runtime.harness.session import Session
from runtime.skills import Graph, SkillSet
from runtime.tools import MockMCP, ToolResult
from runtime.tools.factory import executor_for

from .test_harness import ScriptedLLM
from .test_local_tools import REF


class Out:
    """Records what the agent says and how it transfers."""

    def __init__(self):
        self.said: list[str] = []
        self.transfers: list[tuple[str, dict]] = []
        self.hung_up = False

    async def say(self, text, *, language, interruptible=True):
        self.said.append(text)

    async def transfer(self, reason, **options):
        self.transfers.append((reason, options))

    async def hangup(self):
        self.hung_up = True


async def make_agent(flow: str, language: str = "en", slots: dict | None = None):
    set_reference(REF)
    mcp = MockMCP()
    await mcp.start()
    executor = executor_for(mcp)
    skills = SkillSet(executor.catalog, {"line": {"SKILL.md": "---\ndescription: Line\n---\nBe brief.", "flow.yaml": flow}})
    settings = get_settings().model_copy(update={"require_verification": False, "entry_skill": "line"})
    llm, out = ScriptedLLM(), Out()
    s = Session(call_id="opt-1", ani="+966548802968", agent_name="Line")
    s.language.language = language
    s.slots.update(slots or {})
    agent = Agent(s, executor, llm, skills, out, EventBus().bind(), filler_after_s=5, settings=settings)
    await agent.start()
    out.said.clear()
    return agent, s, llm, out


def answers(**qs):
    return json.dumps({"answers": qs, "values": {}})


# ---------------------------------------------------------------- M: keypad in flows

MENU = """
start: menu
nodes:
  - {id: menu, type: conversation, say: {en: "Press 1 for sales, 2 for support.", ar: "اضغط 1 للمبيعات"}}
  - {id: sales, type: conversation, say: {en: "Sales line.", ar: "المبيعات"}}
  - {id: support, type: conversation, say: {en: "Support line.", ar: "الدعم"}}
  - {id: human, type: transfer, reason: "operator"}
edges:
  - {from: menu, to: sales, when: {dtmf: "1"}}
  - {from: menu, to: support, when: {dtmf: "2"}}
  - {from: "*", to: human, when: {dtmf: "0"}}
"""


def test_dtmf_keys_and_validation():
    g = Graph.parse(MENU)
    assert g.errors() == [] and g.dtmf_keys("menu") == {"1", "2", "0"} and g.dtmf_keys("sales") == {"0"}
    bad = Graph.from_dict({"nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"from": "a", "to": "b", "when": {"dtmf": "12"}}]})
    assert any("dtmf key" in e for e in bad.errors())
    cap = lambda **c: Graph.from_dict({"nodes": [{"id": "a", "dtmf_capture": c}]}).errors()          # noqa: E731
    assert cap(variable="account_number", max_digits=6, end_keys=["#"], timeout_s=5) == []
    assert any("snake_case" in e for e in cap(variable="Account Number"))
    assert any("max_digits" in e for e in cap(variable="x", max_digits=25))
    assert any("timeout_s" in e for e in cap(variable="x", timeout_s=60))
    assert any("end_keys" in e for e in cap(variable="x", end_keys=["5"]))


async def test_keypad_menu_choice_moves_the_flow():
    agent, s, llm, out = await make_agent(MENU)
    plan = agent.dtmf_plan()
    assert plan["keys"] == {"1", "2", "0"} and plan["capture"] is None and plan["node"] == "menu"
    await agent.handle("hello")                                 # a spoken word is not a menu choice: the menu is said
    assert out.said[-1] == "Press 1 for sales, 2 for support."
    s.slots["_dtmf"] = "2"
    await agent.handle("[the caller pressed 2 on the keypad]")
    assert out.said[-1] == "Support line." and "_dtmf" not in s.slots          # the key counted for one turn only


async def test_global_keypad_trigger_reaches_a_transfer():
    agent, s, llm, out = await make_agent(MENU)
    s.slots["_dtmf"] = "0"
    await agent.handle("[the caller pressed 0 on the keypad]")
    assert out.transfers and out.transfers[0][0] == "operator"


CAPTURE = """
start: ask
variables: {}
nodes:
  - {id: ask, type: conversation, instructions: "Ask for the account number.", dtmf_capture: {variable: account_number, max_digits: 6, end_keys: ["#"], timeout_s: 3}}
  - {id: done, type: conversation, say: {en: "Got it.", ar: "تمام"}}
edges:
  - {from: ask, to: done, when: {filled: [account_number]}}
"""


async def test_capture_plan_and_variable_flow():
    agent, s, llm, out = await make_agent(CAPTURE)
    assert agent.dtmf_plan()["capture"]["variable"] == "account_number"
    s.slots["account_number"] = "123456"                       # what the voice call stores when the digits end
    await agent.handle("123456")
    assert out.said[-1] == "Got it."


def keypad_call(plan):
    """A VoiceCall with just enough around it to receive keypad presses."""
    from runtime.voice.call import VoiceCall
    call = VoiceCall.__new__(VoiceCall)
    turns, events = [], []

    async def send_event(msg):
        events.append(msg)

    async def run_agent(text, lang):
        turns.append(text)

    call.agent = SimpleNamespace(dtmf_plan=lambda: plan, on_interrupted=lambda heard: None)
    call.player = SimpleNamespace(active=False, turn_started_at=0)
    call.session = SimpleNamespace(slots={}, language=SimpleNamespace(language="en"), auth=SimpleNamespace(stage="awaiting_otp"))
    call.ev = SimpleNamespace(emit=lambda *a, **k: None)
    call._agent_task, call._send_event, call._run_agent = None, send_event, run_agent
    call._dtmf, call._dtmf_timer, call._stopped = "", None, False
    return call, turns, events


async def press(call, keys):
    for k in keys:
        await call.on_dtmf(k)
    await asyncio.sleep(0.01)


async def test_voice_call_captures_digits_until_the_end_key():
    call, turns, _ = keypad_call({"keys": set(), "capture": {"variable": "account_number", "max_digits": 8, "end_keys": ["#"], "timeout_s": 5}})
    await press(call, "123")
    assert turns == []                                          # still collecting
    await press(call, "#")
    assert turns == ["123"] and call.session.slots["account_number"] == "123"


async def test_voice_call_capture_ends_at_max_digits_or_after_a_pause():
    call, turns, _ = keypad_call({"keys": set(), "capture": {"variable": "pin", "max_digits": 4, "end_keys": ["#"], "timeout_s": 0.05}})
    await press(call, "12345")                                  # the 4th digit completes it; the 5th starts a new entry
    assert turns == ["1234"]
    await asyncio.sleep(0.15)                                   # ...which the pause then completes
    assert turns == ["1234", "5"] and call.session.slots["pin"] == "5"


async def test_voice_call_key_transition_and_unused_keys():
    call, turns, events = keypad_call({"keys": {"1", "0"}, "capture": None})
    await press(call, "5")
    assert turns == []                                          # no transition listens for 5
    await press(call, "1")
    assert turns == ["[the caller pressed 1 on the keypad]"] and call.session.slots["_dtmf"] == "1"
    assert events[-1] == {"event": "transcript", "role": "user", "text": "⌨ 1"}


async def test_voice_call_without_a_keypad_flow_keeps_the_old_entry():
    call, turns, _ = keypad_call(None)
    await press(call, "1234#")
    assert turns == ["1234"] and "_dtmf" not in call.session.slots


# ---------------------------------------------------------------- N: transfer node options

TRANSFER = """
start: ask
nodes:
  - {id: ask, type: conversation, instructions: "Ask."}
  - id: warm
    type: transfer
    reason: billing
    destination: "+966112345678"
    timeout_s: 20
    headers: {X-Customer: "{{ who }}"}
    say: {en: "One moment, I'm connecting you to billing, {{ who }}.", ar: "لحظة"}
  - {id: cold, type: transfer, reason: sales, destination: "8001", transfer_type: cold, say: {en: "never said"}}
  - {id: plain, type: transfer, reason: other}
edges:
  - {from: ask, to: warm, when: {equals: {team: billing}}}
  - {from: ask, to: cold, when: {equals: {team: sales}}}
  - {from: ask, to: plain, when: {equals: {team: other}}}
"""


def test_transfer_node_validation():
    ok = Graph.parse(TRANSFER)
    assert ok.errors() == []
    n = lambda **kw: Graph.from_dict({"nodes": [{"id": "t", "type": "transfer", **kw}]}).errors()          # noqa: E731
    assert n(destination="+14155551234") == [] and n(destination="1234") == [] and n(destination="{{ x }}") == []
    assert any("destination" in e for e in n(destination="0112345678900"))
    assert any("transfer_type" in e for e in n(transfer_type="hot"))
    assert any("timeout_s" in e for e in n(timeout_s=90))


async def test_warm_transfer_announces_and_passes_its_own_options():
    agent, s, llm, out = await make_agent(TRANSFER, slots={"who": "Sara", "team": "billing"})
    await agent.handle("billing please")
    assert out.said[-1] == "One moment, I'm connecting you to billing, Sara."
    reason, opts = out.transfers[0]
    assert reason == "billing"
    assert opts == {"destination": "+966112345678", "timeout_s": 20, "headers": {"X-Customer": "Sara"}}


async def test_cold_transfer_connects_without_announcing():
    agent, s, llm, out = await make_agent(TRANSFER, slots={"team": "sales"})
    await agent.handle("sales")
    assert out.said == [] and out.transfers == [("sales", {"destination": "8001"})]


async def test_plain_transfer_node_keeps_the_default_hand_off():
    agent, s, llm, out = await make_agent(TRANSFER, slots={"team": "other"})
    await agent.handle("other")
    assert out.said and out.transfers == [("other", {})]        # the standard hand-off line, the agent's own destination


# ---------------------------------------------------------------- O: tool node behaviour

TOOL = """
start: go
nodes:
  - id: go
    type: tool
    tool: find_hospital_by_name
    args: {query: "=olaya"}
    on_error: __ON_ERROR__
    retries: 2
    timeout_s: __TIMEOUT__
    processing: {en: "Checking, one moment.", ar: "لحظة"}
    say: {en: "Found it, {{ name }}.", ar: "لقيته"}
    outputs: {name: result.name}
  - {id: ok, type: conversation, instructions: "Tell them."}
  - {id: bad, type: conversation, instructions: "Apologise."}
edges:
  - {from: go, to: ok, on: success}
  - {from: go, to: bad, on: failure}
"""


def tool_flow(on_error="continue", timeout="null"):
    return TOOL.replace("__ON_ERROR__", on_error).replace("__TIMEOUT__", timeout)


def fake_tool(results, calls, fillers=None, delay=0.0):
    async def run(self, call, ctx, ev, filler=None):
        calls.append(call.name)
        if fillers is not None:
            fillers.append(filler)
        if delay:
            await asyncio.sleep(delay)
        return results.pop(0)
    return run


OK = lambda: ToolResult(ok=True, data={"name": "Olaya"}, content='{"name": "Olaya"}')          # noqa: E731
BAD = lambda: ToolResult(ok=False, error="down", content='{"error": "down"}')                      # noqa: E731


async def test_tool_node_says_its_result_and_uses_its_processing_line(monkeypatch):
    agent, s, llm, out = await make_agent(tool_flow())
    calls, fillers = [], []
    monkeypatch.setattr(Agent, "_execute_with_filler", fake_tool([OK()], calls, fillers))
    llm.then("Here you go.")
    await agent.handle("find it")
    assert calls == ["find_hospital_by_name"] and fillers == ["Checking, one moment."]
    assert out.said[0] == "Found it, Olaya." and s.slots["name"] == "Olaya"          # said after the outputs were saved


async def test_tool_node_retries_then_follows_the_failure_edge(monkeypatch):
    agent, s, llm, out = await make_agent(tool_flow("retry"))
    calls = []
    monkeypatch.setattr(Agent, "_execute_with_filler", fake_tool([BAD(), BAD(), BAD()], calls))
    llm.then("Sorry about that.")
    await agent.handle("find it")
    assert len(calls) == 3                                      # 1 try + 2 retries
    assert agent.skills.node("line", s).id == "bad" and "Found it" not in " ".join(out.said)


async def test_tool_node_retry_stops_at_the_first_success(monkeypatch):
    agent, s, llm, out = await make_agent(tool_flow("retry"))
    calls = []
    monkeypatch.setattr(Agent, "_execute_with_filler", fake_tool([BAD(), OK()], calls))
    llm.then("Done.")
    await agent.handle("find it")
    assert len(calls) == 2 and agent.skills.node("line", s).id == "ok"


async def test_tool_node_continue_does_not_retry(monkeypatch):
    agent, s, llm, out = await make_agent(tool_flow("continue"))
    calls = []
    monkeypatch.setattr(Agent, "_execute_with_filler", fake_tool([BAD(), OK()], calls))
    llm.then("Sorry.")
    await agent.handle("find it")
    assert len(calls) == 1 and agent.skills.node("line", s).id == "bad"


async def test_tool_node_fail_hands_the_call_to_a_person(monkeypatch):
    agent, s, llm, out = await make_agent(tool_flow("fail"))
    monkeypatch.setattr(Agent, "_execute_with_filler", fake_tool([BAD()], []))
    await agent.handle("find it")
    assert s.handoff and "find_hospital_by_name" in s.handoff["reason"] and out.transfers


async def test_tool_node_timeout_counts_as_a_failure(monkeypatch):
    agent, s, llm, out = await make_agent(tool_flow("continue", "0.05"))
    monkeypatch.setattr(Agent, "_execute_with_filler", fake_tool([OK()], [], delay=1.0))
    llm.then("Sorry.")
    await agent.handle("find it")
    assert agent.skills.node("line", s).id == "bad" and "Found it" not in " ".join(out.said)


def test_tool_node_option_validation():
    bad = lambda **kw: Graph.from_dict({"nodes": [{"id": "t", "type": "tool", "tool": "x", **kw}]}).errors()          # noqa: E731
    assert bad(on_error="retry", retries=2, timeout_s=30) == []
    assert any("on_error" in e for e in bad(on_error="explode"))
    assert any("retries" in e for e in bad(retries=9))
    assert any("timeout_s" in e for e in bad(timeout_s=500))


# ---------------------------------------------------------------- Q: global node options

GLOBALS = """
start: ask
nodes:
  - {id: ask, type: conversation, instructions: "Ask what they need."}
  - {id: hours, type: conversation, instructions: "Give the opening hours."}
  - {id: bye, type: end, say: {en: "Goodbye.", ar: "مع السلامة"}}
  - {id: quiet, type: end, say: {en: "never said", ar: "x"}}
edges:
  - {from: "*", to: hours, when: {llm: "the caller asks for the opening hours"}, back: true}
  - {from: "*", to: bye, when: {llm: "the caller wants to end the call"}, confirm: true}
  - {from: "*", to: quiet, when: {llm: "the caller says stop silently"}, silent: true}
"""
Q_HOURS, Q_END, Q_QUIET = "the caller asks for the opening hours", "the caller wants to end the call", "the caller says stop silently"


def test_global_edge_options_round_trip():
    g = Graph.parse(GLOBALS)
    assert g.errors() == []
    out = {(e["to"]): e for e in g.to_dict()["edges"]}
    assert out["hours"]["back"] is True and out["bye"]["confirm"] is True and out["quiet"]["silent"] is True
    assert Graph.from_dict(g.to_dict()).to_dict() == g.to_dict()


def test_return_to_source_after_a_global_node():
    g = Graph.parse(GLOBALS)
    state = {"node": "ask", "llm": {Q_HOURS: True}}
    assert g.current(state, {"_turn": 1}, facts={"_turn": 1}).id == "hours"          # the caller asked: go there
    state["llm"] = {}
    assert g.current(state, {"_turn": 1}, facts={"_turn": 1}).id == "hours"          # same turn: stay (it just spoke)
    assert g.current(state, {"_turn": 2}, facts={"_turn": 2}).id == "ask"            # the caller replied: back to where they were


def test_confirm_edge_asks_first_and_follows_only_a_yes():
    g = Graph.parse(GLOBALS)
    state = {"node": "ask", "llm": {Q_END: True}}
    assert g.current(state, {"_turn": 1}, facts={"_turn": 1}).id == "ask"            # not yet: the caller must confirm
    assert state["ask"] == {"text": None} and state["confirm"]["turn"] == 1
    state.pop("ask")
    state["llm"] = {}
    assert g.current(state, {"_turn": 2, "_reply": "yes"}, facts={"_turn": 2, "_reply": "yes"}).id == "bye"
    state2 = {"node": "ask", "llm": {Q_END: True}}
    g.current(state2, {"_turn": 1}, facts={"_turn": 1})
    state2["llm"] = {}
    assert g.current(state2, {"_turn": 2, "_reply": "no"}, facts={"_turn": 2, "_reply": "no"}).id == "ask"
    assert "confirm" not in state2                                                    # the question is dropped


async def test_agent_asks_to_confirm_then_ends_the_call():
    agent, s, llm, out = await make_agent(GLOBALS)
    llm.then(answers(**{Q_HOURS: False, Q_END: True, Q_QUIET: False}))
    await agent.handle("that's all, bye")
    assert out.said == ["Just to confirm — would you like me to go ahead with that?"] and not out.hung_up
    llm.then(answers(**{Q_HOURS: False, Q_END: False, Q_QUIET: False}))
    await agent.handle("yes")
    assert out.said[-1] == "Goodbye." and out.hung_up


async def test_declining_the_confirmation_carries_on():
    agent, s, llm, out = await make_agent(GLOBALS)
    llm.then(answers(**{Q_HOURS: False, Q_END: True, Q_QUIET: False}))
    await agent.handle("bye")
    llm.then(answers(**{Q_HOURS: False, Q_END: False, Q_QUIET: False}))
    llm.then("Of course, what else do you need?")
    await agent.handle("no")
    assert not out.hung_up and out.said[-1].startswith("Of course")


async def test_silent_arrival_skips_the_goodbye():
    agent, s, llm, out = await make_agent(GLOBALS)
    llm.then(answers(**{Q_HOURS: False, Q_END: False, Q_QUIET: True}))
    await agent.handle("stop")
    assert out.hung_up and out.said == []


async def test_global_node_with_return_goes_back_to_the_question():
    agent, s, llm, out = await make_agent(GLOBALS)
    llm.then(answers(**{Q_HOURS: True, Q_END: False, Q_QUIET: False}))
    llm.then("We are open 24 hours.")
    await agent.handle("what are your hours?")
    assert out.said[-1] == "We are open 24 hours." and agent.skills.node("line", s).id == "hours"
    llm.then(answers(**{Q_HOURS: False, Q_END: False, Q_QUIET: False}))
    llm.then("Anything else?")
    await agent.handle("thanks")
    assert agent.skills.node("line", s).id == "ask"


@pytest.mark.parametrize("flag", ["back", "silent"])
def test_edge_flags_off_by_default(flag):
    g = Graph.from_dict({"nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"from": "a", "to": "b"}]})
    assert flag not in g.to_dict()["edges"][0]


# ---------------------------------------------------------------- the Hamsa importer maps these options


def test_importer_maps_keypad_transfer_global_and_tool_options():
    import copy
    from runtime.platform.hamsa_import import convert, parse
    from .test_projects import HAMSA
    h = copy.deepcopy(HAMSA)
    h["workflow"]["nodes"] += [
        {"id": "n-op", "type": "transfer_call", "label": "Operator", "phoneNumber": "+966112345678", "transferType": "cold",
         "timeout": 25, "sipHeaders": [{"name": "X-Id", "value": "{{ search_mobile }}"}], "transitions": [],
         "isGlobal": True, "globalConditionType": "dtmf", "globalDtmfKey": "0", "globalReturnToSource": True,
         "requiresDoubleConfirm": True, "skipResponse": True},
        {"id": "n-menu", "type": "conversation", "label": "Menu", "message": "Press 1.", "messageType": "static",
         "skipResponse": True, "transitions": [{"id": "t9", "condition": {"type": "dtmf", "key": "1"}, "targetNodeId": "n-end"}]},
    ]
    h["workflow"]["nodes"][2]["onErrorBehavior"] = "retry"      # the tool node
    r = convert(parse(json.dumps({"success": True, "data": h})))
    g = Graph.from_dict(r["flow"])
    assert g.errors() == []
    op = g.nodes["operator"]
    assert op.type == "transfer" and op.destination == "+966112345678" and op.transfer_type == "cold"
    assert op.timeout_s == 25 and op.headers == {"X-Id": "{{ search_mobile }}"}
    glob = next(e for e in g.edges if e.source == "*" and e.target == "operator")
    assert glob.when == {"dtmf": "0"} and glob.back and glob.confirm and glob.silent
    menu = g.nodes["menu"]
    assert menu.say == {"ar": "Press 1.", "en": "Press 1."} and menu.skip_response
    assert next(e for e in g.edges if e.source == "menu").when == {"dtmf": "1"}
    assert g.nodes["lookup_patient"].on_error == "retry"
