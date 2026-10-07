"""Hamsa parity (Flow Agent): the "change agent settings" node, a step's own model, and the "transfer agent" node."""

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.config import get_settings
from runtime.control.config_store import ProviderSet
from runtime.data.reference import set_reference
from runtime.events import EventBus, EventType
from runtime.harness.engine import Agent
from runtime.harness.handoff import MAX_AGENT_TRANSFERS, AgentTransfer, carry_over
from runtime.harness.session import Session
from runtime.platform.loader import LoadedAgent
from runtime.skills import Graph, SkillSet
from runtime.tools import MockMCP
from runtime.tools.factory import build_tooling, executor_for

from .test_flow_options import Out
from .test_harness import ScriptedLLM
from .test_local_tools import REF


class RecordingLLM(ScriptedLLM):
    """A scripted model that also remembers the per-call options (model, temperature) each request carried."""

    def __init__(self):
        super().__init__()
        self.options: list[dict] = []

    async def stream(self, messages, *, tools=None, **options):
        self.options.append(options)
        async for event in super().stream(messages, tools=tools):
            yield event


async def build(flow: str, *, language="en", slots=None, llm=None):
    set_reference(REF)
    mcp = MockMCP()
    await mcp.start()
    executor = executor_for(mcp)
    skills = SkillSet(executor.catalog, {"line": {"SKILL.md": "---\ndescription: Line\n---\nBe brief.", "flow.yaml": flow}})
    settings = get_settings().model_copy(update={"require_verification": False, "entry_skill": "line"})
    llm, out, events = llm or RecordingLLM(), Out(), []
    bus = EventBus()

    async def collect(e):
        events.append(e)

    bus.subscribe(collect)
    await bus.start()
    s = Session(call_id="fa-1", ani="+966548802968", agent_name="Line")
    s.language.language = language
    s.slots.update(slots or {})
    agent = Agent(s, executor, llm, skills, out, bus.bind(), filler_after_s=5, settings=settings)
    return agent, s, llm, out, events, bus


# ---------------------------------------------------------------- P: settings node, a step's own model

TUNE = """
start: a
nodes:
  - {id: a, type: conversation, instructions: "Ask something.", llm: {model: node-model, temperature: 0.7}}
  - id: tune
    type: settings
    overrides:
      system_prompt: "You are the VIP agent."
      llm: {model: big-model, temperature: 0.1}
      voice: {en: voice-x}
      stt_model: stt-2
      call: {response_delay_ms: 900, interrupt: false}
  - {id: b, type: conversation, instructions: "Help them."}
  - {id: reset, type: settings, overrides: {llm: {model: ""}, voice: {en: ""}}}
  - {id: c, type: conversation, instructions: "Wrap up."}
edges:
  - {from: a, to: tune, when: {replied: true}}
  - {from: tune, to: b}
  - {from: b, to: reset, when: {replied: true}}
  - {from: reset, to: c}
"""


def test_settings_node_validation_and_round_trip():
    g = Graph.parse(TUNE)
    assert g.errors() == []
    assert Graph.from_dict(g.to_dict()).to_dict() == g.to_dict()
    node = lambda ov: Graph.from_dict({"nodes": [{"id": "t", "type": "settings", "overrides": ov}]}).errors()          # noqa: E731
    assert node({"call": {"response_delay_ms": 900, "inactivity_s": 15, "vad_threshold": 0.6, "interrupt": True}}) == []
    assert any("response_delay_ms" in e for e in node({"call": {"response_delay_ms": 50}}))
    assert any("inactivity_s" in e for e in node({"call": {"inactivity_s": 90}}))
    assert any("vad_threshold" in e for e in node({"call": {"vad_threshold": 0.95}}))
    assert any("unknown setting" in e for e in node({"colour": "red"}))
    assert any("unknown call setting" in e for e in node({"call": {"volume": 3}}))
    assert any("temperature" in e for e in node({"llm": {"temperature": 5}}))
    assert any("per language" in e for e in node({"voice": {"fr": "x"}}))
    conv = Graph.from_dict({"nodes": [{"id": "c", "llm": {"temperature": 9}}]})
    assert any("temperature" in e for e in conv.errors())


async def test_settings_node_changes_model_prompt_and_what_the_transport_listens_for():
    agent, s, llm, out, events, bus = await build(TUNE)
    applied = []
    agent.on_settings = lambda ov: applied.append(ov)
    await agent.start()
    llm.then("What do you need?")
    await agent.handle("hello")                                 # step a: its own model + temperature
    assert llm.options[-1] == {"model": "node-model", "temperature": 0.7} and applied == []
    llm.then("Sure, VIP help.")
    await agent.handle("I need help")                           # a → settings → b: the new model, prompt and call settings
    assert llm.options[-1] == {"model": "big-model", "temperature": 0.1}
    assert "You are the VIP agent." in llm.requests[-1]["messages"][0]["content"]
    assert applied[-1] == {"system_prompt": "You are the VIP agent.", "llm": {"model": "big-model", "temperature": 0.1},
                           "voice": {"en": "voice-x"}, "stt_model": "stt-2", "call": {"response_delay_ms": 900, "interrupt": False}}
    llm.then("Anything else?")
    await agent.handle("thanks")                                # b → reset → c: the model and voice are withdrawn
    assert llm.options[-1] == {"temperature": 0.1}              # only what is still overridden
    assert "voice" not in applied[-1] and applied[-1]["llm"] == {"temperature": 0.1} and applied[-1]["stt_model"] == "stt-2"
    assert "You are the VIP agent." in llm.requests[-1]["messages"][0]["content"]
    await bus.stop()


async def test_a_flow_without_settings_nodes_passes_no_model_options():
    agent, s, llm, out, events, bus = await build("start: a\nnodes:\n  - {id: a, instructions: Ask.}\n")
    await agent.start()
    llm.then("Hello.")
    await agent.handle("hi")
    assert llm.options[-1] == {} and "persona_override" not in {k for k, v in s.memory.items() if v}
    await bus.stop()


def voice_call(settings=None):
    """A VoiceCall with the listening settings in place, nothing else."""
    from runtime.voice.call import VoiceCall
    from runtime.voice.turn import TurnConfig
    call = VoiceCall.__new__(VoiceCall)
    base = settings or get_settings().model_copy(update={"voice_end_silence_ms": 550, "voice_barge_in_ms": 300, "voice_interrupt": True,
                                                         "voice_inactivity_s": 0.0, "voice_vad_threshold": 0.5, "call_max_minutes": 0.0})
    call.loaded = SimpleNamespace(settings=base, agent_id="a")
    call.player = SimpleNamespace(voices={}, tts=None)
    call.turns = SimpleNamespace(cfg=TurnConfig())
    call.max_call_s, call._stopped, call._watch = 0.0, False, None
    call.stt_model = None
    call._apply_call_settings(base)
    return call


async def test_voice_call_follows_a_settings_nodes_overrides():
    call = voice_call()
    started = []

    async def watch():
        started.append(True)
        await asyncio.sleep(0)

    call._watch_limits = watch
    call._on_settings({"voice": {"ar": "v-ar", "en": ""}, "stt_model": "stt-2",
                       "call": {"response_delay_ms": 900, "interrupt": False, "inactivity_s": 20, "min_interruption_ms": 400,
                                "vad_threshold": 0.7}})
    await asyncio.sleep(0.01)
    assert call.player.voices == {"ar": "v-ar"} and call.stt_model == "stt-2"
    assert call.interrupt is False and call.inactivity_s == 20 and call.barge_in_ms == 400
    assert call.turns.cfg.end_silence_ms == 900 and call.turns.cfg.start_threshold == 0.7
    assert call.turns.cfg.end_threshold == min(0.35, 0.7 * 0.7)
    assert started == [True]                                    # the inactivity limit was off: the watcher starts now
    call._on_settings({})                                       # everything withdrawn: the agent's own values are back
    assert call.player.voices == {} and call.stt_model is None and call.interrupt is True and call.inactivity_s == 0
    assert call.turns.cfg.end_silence_ms == 550 and call.turns.cfg.start_threshold == 0.5


async def test_stt_uses_the_overridden_model():
    from runtime.providers import Transcript
    call = voice_call()
    seen = []

    class STT:
        async def transcribe(self, audio, *, language=None, prompt=None, model=None):
            seen.append(model)
            return Transcript("hi", language or "en")

    call.session = SimpleNamespace(language=SimpleNamespace(language="en", decided=True))
    call.providers, call.vad_rate, call.stt_hint = SimpleNamespace(stt=STT()), 16000, {}
    await call._transcribe(bytes(3200))
    call.stt_model = "stt-2"
    await call._transcribe(bytes(3200))
    assert seen == [None, "stt-2"]


async def test_player_uses_the_overridden_voice():
    from runtime.providers import AudioChunk
    from runtime.voice.player import AudioFormat, SpeechPlayer
    voices, sent = [], []

    class TTS:
        async def synthesize(self, text, *, language="ar", voice=None, encoding="pcm16", sample_rate=None):
            voices.append((language, voice))
            yield AudioChunk(bytes(640), 8000)

    async def send_audio(data):
        sent.append(data)

    async def send_event(msg):
        pass

    bus = EventBus()
    player = SpeechPlayer(TTS(), AudioFormat("pcm16", 8000), send_audio, send_event, bus.bind(call_id="v-1"))
    await player.say("hello there", language="en")
    await player.drained()
    player.voices = {"en": "voice-x"}
    await player.say("hello again", language="en")
    await player.drained()
    await player.close()
    assert voices == [("en", None), ("en", "voice-x")]


# ---------------------------------------------------------------- R: transfer agent node

HANDOFF = """
start: ask
nodes:
  - {id: ask, type: conversation, instructions: "Ask what they need."}
  - id: pass
    type: agent
    agent: "{{ team }}"
    handoff_history: true
    handoff_variables: true
    say: {en: "Passing you to our {{ team }} team.", ar: "x"}
  - {id: bad, type: conversation, instructions: "Apologise, the team is busy."}
edges:
  - {from: ask, to: pass, when: {replied: true}}
__FAILURE__
"""


def handoff_flow(failure=True):
    return HANDOFF.replace("__FAILURE__", "  - {from: pass, to: bad, on: failure}" if failure else "")


def test_agent_node_validation():
    assert Graph.parse(handoff_flow()).errors() == []
    assert any("needs `agent`" in e for e in Graph.from_dict({"nodes": [{"id": "p", "type": "agent"}]}).errors())
    g = Graph.parse(handoff_flow())
    assert g.nodes["pass"].handoff_history and g.nodes["pass"].handoff_variables
    assert Graph.from_dict(g.to_dict()).to_dict() == g.to_dict()


def test_carry_over_passes_only_what_was_asked_for():
    old = Session(call_id="c-1", ani="+966500000000", agent_name="A", agent_number="8001")
    old.language.language, old.turn_id = "en", 4
    old.history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    old.slots = {"who": "Sara", "_dtmf": "1", "team": "billing"}
    bare = carry_over(old, "B", AgentTransfer("b"))
    assert bare.call_id == "c-1" and bare.ani == old.ani and bare.agent_name == "B" and bare.language is old.language
    assert bare.history == [] and bare.slots == {} and bare.turn_id == 4 and bare.memory["agent_transfers"] == 1
    full = carry_over(old, "B", AgentTransfer("b", history=True, variables=True))
    assert full.history == old.history and full.history is not old.history
    assert full.slots == {"who": "Sara", "team": "billing"}                  # internal "_" values stay behind
    assert carry_over(full, "C", AgentTransfer("c")).memory["agent_transfers"] == 2


async def test_agent_node_hands_the_call_over_and_the_turn_ends():
    agent, s, llm, out, events, bus = await build(handoff_flow(), slots={"team": "billing"})
    asked = []

    async def handler(req):
        asked.append(req)
        return True

    agent.transfer_agent = handler
    await agent.start()
    llm.then("What do you need?")
    await agent.handle("hello")
    calls = len(llm.requests)
    await agent.handle("I need billing")
    assert asked == [AgentTransfer("billing", True, True)]                  # the {{ team }} in the node's agent was filled in
    assert out.said[-1] == "Passing you to our billing team." and len(llm.requests) == calls      # no model call for it
    assert s.history[-1] == {"role": "assistant", "content": "Passing you to our billing team."}   # goes along with the history
    assert not out.transfers
    await bus.stop()


async def test_agent_node_failure_follows_the_failure_edge():
    agent, s, llm, out, events, bus = await build(handoff_flow(), slots={"team": "billing"})

    async def handler(req):
        return False

    agent.transfer_agent = handler
    await agent.start()
    llm.then("What do you need?")
    await agent.handle("hello")
    llm.then("Sorry, they are busy.")
    await agent.handle("billing please")
    assert agent.skills.node("line", s).id == "bad" and out.said[-1] == "Sorry, they are busy." and not out.transfers
    await bus.stop()


async def test_agent_node_without_a_failure_edge_hands_to_a_person():
    agent, s, llm, out, events, bus = await build(handoff_flow(failure=False), slots={"team": "billing"})
    agent.transfer_agent = None                                              # a transport that can't swap agents
    await agent.start()
    llm.then("What do you need?")
    await agent.handle("hello")
    await agent.handle("billing please")
    assert s.handoff and "billing" in s.handoff["reason"] and out.transfers
    await bus.stop()


async def test_agent_transfers_are_capped():
    agent, s, llm, out, events, bus = await build(handoff_flow(), slots={"team": "billing"})
    asked = []

    async def handler(req):
        asked.append(req)
        return True

    agent.transfer_agent = handler
    s.memory["agent_transfers"] = MAX_AGENT_TRANSFERS                      # already passed around enough
    await agent.start()
    llm.then("What do you need?")
    await agent.handle("hello")
    llm.then("Sorry.")
    await agent.handle("billing")
    assert asked == [] and agent.skills.node("line", s).id == "bad"
    await bus.stop()


async def test_a_resumed_agent_does_not_open_a_new_call_record():
    agent, s, llm, out, events, bus = await build("start: a\nnodes:\n  - {id: a, instructions: Ask.}\n")
    await agent.start(resumed=True, greet=False)
    assert out.said == []                                                    # the conversation came with it: no greeting
    await agent.start(resumed=True, greet=True)
    await bus.stop()
    assert len(out.said) == 1 and not [e for e in events if e.type == EventType.CALL_START]
    fresh, s2, llm2, out2, events2, bus2 = await build("start: a\nnodes:\n  - {id: a, instructions: Ask.}\n")
    await fresh.start()
    await bus2.stop()
    assert [e.type for e in events2].count(EventType.CALL_START) == 1


async def test_voice_call_swaps_to_the_next_agent():
    from runtime.voice.call import VoiceCall
    started, sent, emitted = [], [], []
    old_tts, new_tts = object(), object()
    target = SimpleNamespace(agent_id="b", name="Agent B", release_id=7, version=3, settings=get_settings(),
                             providers=SimpleNamespace(tts=new_tts), stt_hint={"ar": "b-hint"}, bundle={})

    async def agent_for_call(*, agent_id=None, number=None, draft=False):
        if agent_id != "b":
            raise LookupError(agent_id)
        return target

    class NextAgent:
        async def start(self, *, resumed=False, greet=True):
            started.append((resumed, greet))

    async def send_event(msg):
        sent.append(msg)

    call = voice_call()
    call.rt = SimpleNamespace(agent_for_call=agent_for_call)
    call.loaded = SimpleNamespace(settings=get_settings(), agent_id="a")
    call.session = Session(call_id="v-9", ani="+966500000000", agent_name="A")
    call.session.history = [{"role": "user", "content": "hi"}]
    call.session.slots = {"who": "Sara"}
    call.providers, call.stt_hint, call.player = SimpleNamespace(tts=old_tts), {}, SimpleNamespace(voices={"en": "v"}, tts=old_tts)
    call.ev = SimpleNamespace(emit=lambda *a, **k: emitted.append((a, k)))
    call._send_event = send_event
    call._make_agent = lambda: NextAgent()
    assert await call.switch_agent(AgentTransfer("nobody")) is False        # unknown agent: the call stays where it is
    assert call.loaded.agent_id == "a" and started == []
    assert await call.switch_agent(AgentTransfer("b", history=True, variables=True)) is True
    assert call.loaded is target and call.player.tts is new_tts and call.player.voices == {} and call.stt_hint == {"ar": "b-hint"}
    assert call.session.agent_name == "Agent B" and call.session.slots == {"who": "Sara"} and call.session.history
    assert started == [(True, False)]                                       # the history went along: no new greeting
    assert sent == [{"event": "agent_transfer", "agent_id": "b", "name": "Agent B"}]
    kinds = [a[0] for a, k in emitted]
    assert EventType.AGENT_TRANSFER in kinds
    assert next(k for a, k in emitted if a[0] == EventType.AGENT_TRANSFER)["previous"] == "a"


# ---------------------------------------------------------------- end to end: a chat that moves between two agents


class TwoAgentRt:
    def __init__(self, agents, bus):
        self.agents, self.bus, self.calls = agents, bus, {}

    async def agent_for_call(self, *, agent_id=None, number=None, draft=False):
        if agent_id not in self.agents:
            raise LookupError(agent_id)
        return self.agents[agent_id]


def make_loaded(loop, agent_id, name, flow):
    executor = loop.run_until_complete(build_tooling(MockMCP()))
    skills = SkillSet(executor.catalog, {"line": {"SKILL.md": f"---\ndescription: {name}\n---\nBe brief.", "flow.yaml": flow}})
    settings = get_settings().model_copy(update={"require_verification": False, "entry_skill": "line"})
    a = LoadedAgent.from_parts(settings=settings, providers=ProviderSet(0, {}, ScriptedLLM(), None, None), skills=skills,
                               executor=executor)
    a.agent_id, a.name = agent_id, name
    return a


A_FLOW = """
start: hello
nodes:
  - {id: hello, type: conversation, say: {en: "This is A.", ar: "x"}}
  - {id: remember, type: set, set: {who: "=Sara"}}
  - {id: pass, type: agent, agent: __TARGET__, handoff_variables: true, say: {en: "Passing you to B.", ar: "x"}}
  - {id: busy, type: conversation, say: {en: "B is busy.", ar: "x"}}
edges:
  - {from: hello, to: remember, when: {replied: true}}
  - {from: remember, to: pass}
  - {from: pass, to: busy, on: failure}
"""
B_FLOW = """
start: hi
nodes:
  - {id: hi, type: conversation, say: {en: "This is B, {{ who }}.", ar: "x"}}
"""


def read_until(ws, kind, text=None):
    for _ in range(30):
        msg = json.loads(ws.receive_text())
        if msg["event"] == kind and (text is None or text in msg.get("text", "")):
            return msg
    raise AssertionError(f"never saw {kind} {text!r}")


def run_chat(monkeypatch, target):
    from runtime.server import app as server_app
    from runtime.server import chat
    loop = asyncio.new_event_loop()
    bus = EventBus()
    rt = TwoAgentRt({"a": make_loaded(loop, "a", "Agent A", A_FLOW.replace("__TARGET__", target)),
                     "b": make_loaded(loop, "b", "Agent B", B_FLOW)}, bus)
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    app.include_router(chat.router)
    seen = []
    with TestClient(app) as c, c.websocket_connect("/ws/chat") as ws:
        ws.send_text(json.dumps({"event": "start", "agent": "a", "language": "en"}))
        ready = json.loads(ws.receive_text())
        assert ready["event"] == "ready"
        read_until(ws, "transcript")                                         # A's greeting
        ws.send_text(json.dumps({"event": "text", "text": "hello"}))
        assert read_until(ws, "transcript", "This is A.")["role"] == "agent"
        ws.send_text(json.dumps({"event": "text", "text": "go ahead"}))
        seen.append(read_until(ws, "transcript", "Passing you to B." if target == "b" else "B is busy."))
        if target == "b":
            seen.append(read_until(ws, "agent_transfer"))
            seen.append(read_until(ws, "transcript"))                         # B's own greeting
            ws.send_text(json.dumps({"event": "text", "text": "anyone there?"}))
            seen.append(read_until(ws, "transcript", "This is B, Sara."))
        ws.send_text(json.dumps({"event": "stop"}))
    loop.close()
    return seen


def test_chat_moves_to_the_next_agent(monkeypatch):
    seen = run_chat(monkeypatch, "b")
    assert seen[1] == {"event": "agent_transfer", "agent_id": "b", "name": "Agent B"}
    assert seen[3]["text"] == "This is B, Sara."                              # B's flow, with A's collected value


def test_chat_stays_when_the_next_agent_is_unknown(monkeypatch):
    seen = run_chat(monkeypatch, "ghost")
    assert seen[0]["text"] == "B is busy."                                    # the failure edge, still with agent A


# ---------------------------------------------------------------- the Hamsa importer maps both nodes


def test_importer_maps_settings_and_agent_nodes():
    import copy
    from runtime.platform.hamsa_import import convert, parse
    from .test_projects import HAMSA
    h = copy.deepcopy(HAMSA)
    h["workflow"]["nodes"] += [
        {"id": "n-set2", "type": "change_agent_settings", "label": "Calm down", "systemInstructions": "Be gentle.",
         "voiceId": "hamsa-voice-1", "expressiveness": 0.4, "preferredSttModel": "Hamsa-STT-S3-beta", "interrupt": False,
         "responseDelay": 900, "userInactivityTimeout": 20, "minInterruptionDuration": 0.4, "vadActivationThreshold": 0.7,
         "transitions": [{"id": "t10", "condition": {"type": "auto"}, "targetNodeId": "n-end"}]},
        {"id": "n-agent", "type": "transfer_agent", "label": "To billing", "agentId": "hamsa-billing", "handoffConversation": True,
         "handoffVariables": True, "transferMessage": "One moment.", "transferMessageType": "static", "transitions": []},
    ]
    r = convert(parse(json.dumps({"success": True, "data": h})))
    g = Graph.from_dict(r["flow"])
    assert g.errors() == []
    s = g.nodes["calm_down"]
    assert s.type == "settings" and s.overrides == {"system_prompt": "Be gentle.", "call": {
        "interrupt": False, "response_delay_ms": 900, "inactivity_s": 20, "min_interruption_ms": 400.0, "vad_threshold": 0.7}}
    a = g.nodes["to_billing"]
    assert a.type == "agent" and a.agent == "hamsa-billing" and a.handoff_history and a.handoff_variables
    assert a.say == {"ar": "One moment.", "en": "One moment."}
    report = " ".join(r["report"])
    assert "voiceId" in report and "hamsa-billing" in report                  # what couldn't come across is listed
