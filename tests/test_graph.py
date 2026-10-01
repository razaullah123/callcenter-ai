"""Phase 12.5: flow graphs — step flows converted exactly, graph semantics, and a native graph agent end to end."""

import json
import random
from pathlib import Path

import pytest

from runtime.config import get_settings
from runtime.data.reference import set_reference
from runtime.events import EventBus
from runtime.harness.engine import Agent
from runtime.harness.session import Session
from runtime.skills import Flow, Graph, SkillSet
from runtime.skills.graph import DONE
from runtime.tools import MockMCP
from runtime.tools.factory import executor_for

from .test_harness import Output, ScriptedLLM
from .test_local_tools import REF

SKILLS = Path(__file__).resolve().parent.parent / "skills"
STAGES = ["awaiting_mobile", "awaiting_dob_and_name", "send_otp", "awaiting_otp", "verified"]


@pytest.mark.parametrize("skill", ["book_appointment", "authenticate"])
def test_converted_step_flows_pick_the_same_step(skill):
    text = (SKILLS / skill / "flow.yaml").read_text(encoding="utf-8")
    flow, graph = Flow.parse(text), Graph.parse(text)
    assert graph.converted and graph.errors() == []
    slots_used = sorted({x for st in flow.steps for x in st.requires + st.until})
    rnd = random.Random(7)
    state: dict = {}
    for _ in range(3000):                      # a random walk: the graph keeps state, the step machine derives it
        slots = {k: 1 for k in slots_used if rnd.random() < 0.5}
        stage = rnd.choice(STAGES)
        old = flow.current(slots, stage)
        new = graph.current(state, dict(slots), stage)
        assert (old.id if old else DONE) == new.id, (slots, stage)


GRAPH = """
start: ask
variables:
  topic: {type: string, enum: [hours, other], description: "what the caller wants to know"}
nodes:
  - {id: ask, instructions: "Ask what they would like to know.", extract: [topic]}
  - {id: route, type: router}
  - {id: note, type: set, set: {asked: "=yes"}}
  - {id: lookup, type: tool, tool: find_hospital_by_name, args: {query: "=olaya"}}
  - {id: answer, instructions: "Tell them Olaya Hospital's opening hours: open 24 hours."}
  - {id: human, type: transfer, reason: "not a question this line answers"}
  - {id: bye, type: end, say: {en: "Thanks for calling, goodbye.", ar: "شكراً لاتصالك، مع السلامة."}}
edges:
  - {from: ask, to: route, when: {filled: [topic]}}
  - {from: route, to: note, when: {equals: {topic: hours}}}
  - {from: route, to: human}
  - {from: note, to: lookup}
  - {from: lookup, to: answer, on: success}
  - {from: lookup, to: human, result: failure}
  - {from: answer, to: bye, when: {llm: "the caller says goodbye or needs nothing else"}}
  - {from: "*", to: human, when: {llm: "the caller wants to complain"}}
"""


def test_graph_semantics():
    g = Graph.parse(GRAPH)
    assert g.errors() == [] and not g.converted
    state, slots = {}, {}
    assert g.current(state, slots).id == "ask"
    slots["topic"] = "hours"
    assert g.current(state, slots).id == "lookup" and slots["asked"] == "yes"        # router → set → tool (waits)
    state["outcome"] = {"lookup": False}
    assert g.current(state, slots).id == "human"                                       # failure edge
    state2, slots2 = {"node": "lookup", "outcome": {"lookup": True}}, {"topic": "hours"}
    assert g.current(state2, slots2).id == "answer"
    assert g.llm_conditions("answer") == ["the caller says goodbye or needs nothing else", "the caller wants to complain"]
    state2["llm"] = {"the caller says goodbye or needs nothing else": True}
    assert g.current(state2, slots2).id == "bye"
    state3 = {"node": "ask", "llm": {"the caller wants to complain": True}}
    assert g.current(state3, {}).id == "human"                                          # global edge
    assert set(g.to_dict()) >= {"start", "nodes", "edges", "variables"}
    assert Graph.from_dict(g.to_dict()).to_dict() == g.to_dict()                        # canvas round trip


def test_graph_errors():
    bad = Graph.from_dict({"nodes": [{"id": "a", "type": "teleport", "extract": ["x"]}, {"id": "t", "type": "tool"}],
                           "edges": [{"from": "a", "to": "nowhere"}, {"from": "t", "to": "a", "on": "maybe"}]})
    errors = " | ".join(bad.errors())
    for part in ("unknown type 'teleport'", "undeclared variables ['x']", "needs `tool`", "unknown node 'nowhere'",
                 "success or failure"):
        assert part in errors


async def test_a_graph_agent_without_caller_verification():
    """An information line built only from configuration: no mobile / OTP, one extraction call per turn,
    the platform runs the lookup itself, and the call ends on the flow's own goodbye."""
    set_reference(REF)
    mcp = MockMCP()
    await mcp.start()
    executor = executor_for(mcp)
    skills = SkillSet(executor.catalog, {
        "info": {"SKILL.md": "---\ndescription: Hospital information line\n---\nAnswer briefly.", "flow.yaml": GRAPH}})
    settings = get_settings().model_copy(update={"require_verification": False, "entry_skill": "info"})
    llm = ScriptedLLM()
    q_bye, q_complain = "the caller says goodbye or needs nothing else", "the caller wants to complain"
    llm.then(json.dumps({"answers": {q_complain: False}, "values": {"topic": "hours"}}))       # turn 1: extraction
    llm.then("Olaya Hospital is open 24 hours. Anything else?")                               # turn 1: reply
    llm.then(json.dumps({"answers": {q_bye: True, q_complain: False}, "values": {}}))          # turn 2: extraction
    out, bus = Output(), EventBus()
    s = Session(call_id="info-1")
    s.language.language = "en"
    agent = Agent(s, executor, llm, skills, out, bus.bind(), filler_after_s=5, settings=settings)
    await agent.start()
    assert s.auth.verified and not s.auth.required and s.active_skill == "info"
    await agent.handle("What time is Olaya hospital open?", "en")
    assert "open 24 hours" in " ".join(out.said)
    assert any(m.get("tool_calls") and m["tool_calls"][0]["function"]["name"] == "find_hospital_by_name"
               for m in s.history)                                                 # the tool node ran
    assert s.slots["topic"] == "hours" and s.slots["asked"] == "yes"
    system = llm.requests[1]["messages"][0]["content"]
    assert "Current step: answer" in system and "VERIFIED" not in system and "mobile" not in system.lower()
    await agent.handle("Great, that's all, bye", "en")
    assert out.said[-1] == "Thanks for calling, goodbye." and out.hung_up and s.ended
    assert len(llm.requests) == 3                                                     # no model reply after the end


def test_one_flow_lets_its_steps_decide_when_to_verify():
    """With main_flow, the prompt must not tell the model to verify before the greeting step found the request."""
    from runtime.harness.context import system_prompt
    from runtime.harness.session import Session
    from runtime.skills import SkillSet
    from runtime.tools import MockMCP

    async def build():
        mcp = MockMCP()
        await mcp.start()
        return executor_for(mcp)
    import asyncio
    ex = asyncio.new_event_loop().run_until_complete(build())
    skills = SkillSet(ex.catalog, {"info": {"SKILL.md": "---\ndescription: x\n---\nHelp.", "flow.yaml": GRAPH}})
    s = Session(call_id="c")
    s.flow_skill = s.active_skill = "info"
    s.pending_intent = "something"
    prompt = system_prompt(s, skills)
    assert "Verify before helping" not in prompt and "only when it is time" in prompt
    assert "handle it after verification" not in prompt and "Current step: ask" in prompt
    s.flow_skill, s.active_skill = None, "authenticate"
    assert "Verify before helping" in system_prompt(s, skills)          # step-based agents: unchanged
