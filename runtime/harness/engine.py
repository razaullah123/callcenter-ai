"""Turn engine: one caller utterance in → streamed agent speech (and tool calls) out.

    agent = Agent(session, executor, llm, skills, output, emitter)
    await agent.start()                      # greeting
    task = asyncio.create_task(agent.handle("أبي أحجز موعد"))
    task.cancel()                            # barge-in: stops LLM / speech; writes in flight still complete

Latency choices: rule-based checks run before any LLM call; replies are spoken sentence by sentence
while the LLM is still generating; slow tools trigger a short filler; likely next lookups are prefetched.
"""

import asyncio
import json
import re
import time
from datetime import date
from typing import Any, Protocol

from runtime.config import Settings, get_settings
from runtime.events import BoundEmitter, EventType, Level
from runtime.providers import LLMProvider, TextDelta, ToolCall, ToolCallsReady
from runtime.providers.base import LLMDone
from runtime.providers.hedge import hedged
from runtime.tools import ToolContext, ToolExecutor, ToolResult
from runtime.skills.flow import _eval as flow_eval, is_template, render
from runtime.skills.graph import ACTION_TYPES
from runtime.skills.loader import template_vars
from runtime.tools.hooks import load_packs, run_tool_hooks, turn_hooks

from . import control
from .context import build_messages, tool_call_message
from .handoff import MAX_AGENT_TRANSFERS, AgentTransfer
from .nlu.dates import resolve_dob, today_riyadh
from .nlu.gender import gender_from_text
from .nlu.extract import classify_and_extract, dateish, extract, numberish
from .nlu.lang import detect
from .nlu.intents import (EMERGENCY_MESSAGE, describes_location, greeting_only, greeting_reply, loose_earliest, mentions_since, red_flag, red_flag_clinic,
                          wants_earliest, wants_human, yes_no)
from .nlu.numbers import extract_code, normalize_mobile
from .policy import (MAX_MATCH_ATTEMPTS, MAX_OTP_ATTEMPTS, Policy, clean_for_speech, normalize_datetime_args,
                     is_reasoning_leak, to_feminine, unbacked_claim)
from .prompts import Phrases
from .session import VERIFY_SKILL, Session
from .skills import SkillSet

MAX_HOPS = 6
STILL_WORKING_AFTER_S = 6.0
# The only sentence allowed after the reply's question: the note that more time slots exist (agreed wording).
_MORE_TIMES_NOTE = re.compile(r"(more|other) (time )?slots|another time|أوقات ثانية|وقت ثاني", re.IGNORECASE)


def _tool_call_error(e: Exception) -> str | None:
    text = str(e)
    if "tool call validation failed" in text:
        return "invalid tool name"
    if ("tool_use_failed" in text or "Failed to call a function" in text or "failed_generation" in text
            or "Parsing failed" in text):
        return "malformed tool call"
    return None
MAX_TOOL_FAILURES = 3


class AgentOutput(Protocol):
    async def say(self, text: str, *, language: str, interruptible: bool = True) -> None: ...
    async def transfer(self, reason: str, **options: Any) -> None: ...      # options: destination, timeout_s, headers
    async def hangup(self) -> None: ...


class SentenceChunker:
    """Splits streamed text into speakable chunks: sentence ends always, commas once the chunk is long
    enough (shorter threshold for the first chunk, so speech starts sooner)."""

    STRONG = ".!?؟\n…"
    WEAK = "،,؛;:"
    ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "st", "no", "prof", "د", "أ", "م"}

    def __init__(self, first_min: int = 25, min_len: int = 70) -> None:
        self.buf, self.first, self.first_min, self.min_len = "", True, first_min, min_len

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out = []
        while True:
            cut = self._boundary()
            if cut is None:
                return out
            chunk, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if chunk:
                out.append(chunk)
                self.first = False

    def _boundary(self) -> int | None:
        threshold = self.first_min if self.first else self.min_len
        for i, ch in enumerate(self.buf[:-1]):
            nxt = self.buf[i + 1]
            if not nxt.isspace():
                glued = ch in "?!؟" or (ch == "." and nxt.isalpha())
                if not glued or ch in self.WEAK:
                    continue
            if ch == "." and self._abbreviation_before(i):
                continue
            if ch in self.STRONG or (ch in self.WEAK and i + 1 >= threshold):
                return i + 1
        return None

    def _abbreviation_before(self, i: int) -> bool:
        word = self.buf[:i].rsplit(None, 1)[-1] if self.buf[:i].strip() else ""
        return word.lower() in self.ABBREVIATIONS

    def flush(self) -> str:
        chunk, self.buf = self.buf.strip(), ""
        return chunk


async def _dispatch_pre(tool, args, ctx: ToolContext):
    policy = ctx.extra.get("policy")
    return await policy.pre(tool, args, ctx) if policy else args


async def _dispatch_post(tool, args, result, ctx: ToolContext):
    policy = ctx.extra.get("policy")
    if policy:
        await policy.post(tool, args, result, ctx)


class Agent:
    def __init__(self, session: Session, executor: ToolExecutor, llm: LLMProvider, skills: SkillSet,
                 output: AgentOutput, emitter: BoundEmitter, *, filler_after_s: float = 0.7,
                 settings: Settings | None = None) -> None:
        self.s, self.executor, self.skills, self.out = session, executor, skills, output
        self.settings = settings or get_settings()       # the agent's knobs (per release), frozen for this call
        self.ph: Phrases = getattr(skills, "phrases", None) or Phrases()
        self.ev = emitter.bind(call_id=session.call_id)
        # Groq's first-token time spikes (0.6 s vs 8 s for similar requests): race a backup when it's slow to start
        self.llm = hedged(llm, self.settings.llm_hedge_after_s,
                          on_hedge=lambda d: self.ev.emit(EventType.LLM_HEDGE, **d))
        self.policy = Policy(session, executor.catalog)
        self.filler_after_s = filler_after_s
        self._spoken: list[str] = []
        self._done_tools: set[str] = set()   # write / send tools that succeeded in this call
        self._done_claims: set[str] = set()  # what those successes back: booked / confirmed / cancelled / sent
        self._turn_answered = False
        self._filler_said = False
        self._filler_idx = 0
        self._last_step: str | None = None
        self.on_settings = None          # the transport re-reads these when a settings node changes them: f(overrides)
        self.transfer_agent = None       # the transport swaps the call to another agent: async f(AgentTransfer) -> bool
        load_packs()                                       # named tool / turn hooks referenced by the config
        if _dispatch_pre not in executor.pre_hooks:        # hooks are shared; they route per call via ctx
            executor.pre_hooks.append(_dispatch_pre)
            executor.post_hooks.append(_dispatch_post)

    # ------------------------------------------------------------------ lifecycle

    async def start(self, *, resumed: bool = False, greet: bool = True) -> None:
        """Begin the call. `resumed`: this agent took over a call in progress (agent-to-agent hand-off) — no new call
        record; `greet`: say the opening line (not when the conversation was handed over with it)."""
        if self.settings.main_flow:                          # one flow graph runs the whole call
            self.s.flow_skill = self.s.active_skill = self.settings.main_flow
        if not self.settings.require_verification:          # e.g. an information line: straight to its entry skill
            a = self.s.auth
            a.required, a.verified, a.identity_confirmed = False, True, True
            if not self.s.flow_skill:
                self.s.active_skill = self.settings.entry_skill
        lang = self.s.language.language
        if not resumed:
            self.ev.emit(EventType.CALL_START, ani_present=self.s.ani is not None, language=lang,
                         config_version=getattr(self, "config_version", None), **getattr(self, "agent_ref", {}))
        if not greet:
            return
        wait = self.settings.voice_wait_for_user
        if wait == "always" or (wait == "outbound" and self.s.direction == "outbound"):
            return                                           # the caller speaks first; the agent answers that
        greeting = self.greeting(lang)
        await self._say(greeting)
        self.s.history.append({"role": "assistant", "content": greeting})

    def greeting(self, lang: str) -> str:
        """The opening line: the agent's greeting, rendered as a template when it has {{ }} (a dynamic greeting such
        as "Good {{ 'morning' if current_time < '12' else 'evening' }}, this is {{ agent_name }}")."""
        text = self.ph.GREETING[lang]
        if is_template(text):
            text = render(text, {"slots": self.s.slots, **template_vars(self.s)}).strip() or self.ph.GREETING[lang]
        return text

    async def handle(self, text: str, stt_language: str | None = None) -> None:
        s = self.s
        if s.ended or s.handoff:
            return
        s.turn_id += 1
        ev = self.ev.bind(turn_id=s.turn_id, skill=s.active_skill)
        ev.emit(EventType.TURN_START, text=text, stt_language=stt_language)
        t0 = time.perf_counter()
        self._spoken, self._filler_said = [], False
        self._turn_answered = False
        try:
            await self._turn(text.strip(), stt_language, ev)
        except asyncio.CancelledError:
            self._record_interrupted()
            ev.emit(EventType.INTERRUPT, spoken=" ".join(self._spoken))
            raise
        except Exception as e:
            ev.emit(EventType.ERROR, level=Level.ERROR, error=repr(e))
            s.tool_failures += 1
            await self._say(self.ph.FALLBACK[s.language.language])
        finally:
            s.slots.pop("_dtmf", None)                     # a keypad press counts for this turn only
            ev.emit(EventType.TURN_END, latency_ms=round((time.perf_counter() - t0) * 1000, 1))
        if s.tool_failures >= MAX_TOOL_FAILURES and not s.handoff:
            await self._handoff("repeated system errors", ev)

    def on_interrupted(self, played_text: str) -> None:
        """Voice pipeline reports what the caller actually heard before barging in."""
        for msg in reversed(self.s.history):
            if msg.get("role") == "assistant" and msg.get("content") and not msg.get("tool_calls"):
                msg["content"] = (played_text.strip() + " —") if played_text.strip() else "—"
                return

    # ------------------------------------------------------------------ turn

    async def _turn(self, text: str, stt_language: str | None, ev: BoundEmitter) -> None:
        s = self.s
        s.last_user_text = text
        if s.language.update(text, stt_language):
            ev.emit(EventType.SLOT_SET, field="language", value=s.language.language)
        if s.gender.observe(gender_from_text(text), "speech"):
            ev.emit(EventType.SLOT_SET, field="gender", value=s.gender.known, source="speech")
        s.last_reply = yes_no(text)
        # Greeting handling only at the start of the call (before the caller said what they need).
        greeting = s.pending_intent is None and not s.auth.verified and greeting_only(text)
        if greeting:
            s.last_reply = None   # "هلا" / "hi" is a greeting here, not a yes
        elif s.pending_intent is None and not s.auth.verified and s.auth.stage == "awaiting_mobile" \
                and not normalize_mobile(text):
            s.pending_intent = text   # the caller's first real request, resumed after verification

        if wants_human(text):
            s.history.append({"role": "user", "content": text})
            await self._handoff("caller asked for a human agent", ev)
            return
        note = ""
        if red_flag(text):
            mode = self.settings.red_flag_mode
            ev.emit(EventType.POLICY_BLOCK, reason="red_flag_symptoms", mode=mode)
            if mode == "stop":
                msg = EMERGENCY_MESSAGE[s.language.language]
                s.history += [{"role": "user", "content": text}, {"role": "assistant", "content": msg}]
                await self._say(msg)
                return
            note = self.ph.RED_FLAG_NOTE[mode]
            if clinic := red_flag_clinic(text):
                note += f"\n[system: the matching clinic for this symptom is {clinic}.]"

        if greeting and not note:
            # greet back in kind, word for word — no model round trip (it copied "وعليكم السلام" to a "مرحبا")
            reply = greeting_reply(text, s.language.language)
            s.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
            await self._say(reply)
            return
        if greeting:
            note += self.ph.GREETING_NOTE
        if s.pending_action and s.last_reply not in ("yes", "no"):
            note += self.ph.READBACK_UNCLEAR_NOTE
        if s.language.language == "en":
            note += self.ph.REPLY_IN_ENGLISH   # the opening greeting is Arabic: keep the model from drifting back
        extracted = await self._extract(text, ev)
        s.history.append({"role": "user", "content": text + self._hints(text, extracted) + note})
        if s.auth.verified and not s.flow_skill and s.active_skill == self.settings.entry_skill:
            if routed := getattr(self.skills, "classify", lambda _t: None)(text):
                s.active_skill = routed
                ev.emit(EventType.SKILL_ENTER, skill=routed, routed_by="keywords")
        a = s.auth
        if a.verified and not a.identity_confirmed and s.memory.get("identity_asked"):
            if s.last_reply == "yes":
                a.identity_confirmed = True
                s.last_reply = "consumed"          # this yes was for "Am I speaking to …?", nothing else
                ev.emit(EventType.SLOT_SET, field="identity_confirmed", value=True)
                if s.history and s.history[-1].get("role") == "user":
                    s.history[-1]["content"] += self.ph.IDENTITY_CONFIRMED_NOTE
            elif s.last_reply == "no":
                await self._handoff(self.ph.NOT_THE_PATIENT, ev)
                return
            else:
                # unclear (misheard / loose): the model reads it — record_answer, or it asks again (IDENTITY_STEP).
                # A second reply that still isn't "no" counts as yes: the OTP already proved the phone, and a caller
                # who just carries on with their request ("مستشفى العليا") must not be stuck on the name question.
                n = s.memory["identity_unclear"] = s.memory.get("identity_unclear", 0) + 1
                if n >= 2:
                    a.identity_confirmed = True
                    ev.emit(EventType.SLOT_SET, field="identity_confirmed", value=True, source="implicit")
                    if s.history and s.history[-1].get("role") == "user":
                        s.history[-1]["content"] += self.ph.IDENTITY_CONFIRMED_NOTE
        await self._graph_turn(text, ev)
        if s.pending_action and await self._resolve_pending(ev):
            return
        await self._llm_loop(ev)

    # ------------------------------------------------------------------ flow graphs

    def _flow_skill(self) -> str:
        return self.s.flow_skill or (VERIFY_SKILL if not self.s.auth.verified else self.s.active_skill)

    async def _graph_turn(self, text: str, ev: BoundEmitter) -> None:
        """The current node's edge questions ("the caller wants a different hospital") and variables, read from
        the caller's words in one short call — only when the node has any."""
        skill = self._flow_skill()
        graph = getattr(self.skills, "graph", lambda _s: None)(skill)
        if graph is None:
            return
        state = self.skills.graph_state(skill, self.s)
        state["llm"] = {}
        node = self.skills.node(skill, self.s)
        questions = graph.llm_conditions(node.id)
        variables = {n: graph.variables[n] for n in node.extract if n in graph.variables}
        if not questions and not variables:
            return
        last = next((m.get("content") or "" for m in reversed(self.s.history[:-1]) if m.get("role") == "assistant"), "")
        answers, values, ms = await classify_and_extract(
            self.llm, text, questions=questions, variables=variables, today=today_riyadh(),
            context=f"The agent had just said: {last}" if last else "")
        state["llm"] = answers
        for name, value in values.items():
            self.s.slots[name] = value
            ev.emit(EventType.SLOT_SET, field=name, value=value, source="flow_extract")
        ev.emit(EventType.SLOT_SET, field="flow:classified", value={q[:60]: a for q, a in answers.items()},
                node=node.id, latency_ms=ms)

    async def _run_graph_actions(self, ev: BoundEmitter) -> bool:
        """Nodes the platform performs itself when the flow reaches them: call a tool (then follow its success /
        failure edge), transfer, end the call, or continue in another skill. True if the turn is over."""
        node_of = getattr(self.skills, "node", None)
        if node_of is None:
            return False
        for _ in range(6):
            skill = self._flow_skill()
            node = node_of(skill, self.s)
            state = self.skills.graph_state(skill, self.s)
            if (ask := state.pop("ask", None)) is not None:            # an edge wants the caller's yes / no first
                lang = self.s.language.language
                line = (ask.get("text") or {}).get(lang) or self.ph.CONFIRM_GLOBAL[lang]
                self.ev.emit(EventType.POLICY_BLOCK, reason="confirm_transition", text=line[:120])
                await self._say(line)
                self.s.history.append({"role": "assistant", "content": line})
                return True
            silent = node is not None and state.get("silent") == node.id
            if node is not None and node.type == "conversation" and silent:
                return True                                             # arrived silently: nothing to say here
            if node is not None and node.type == "conversation" and node.say:
                step = await self._say_static(skill, node, ev)          # a fixed message, no model round trip
                if step == "again":
                    continue                                            # it doesn't wait for the caller: next node now
                if step == "over":
                    return True
                return False
            if node is None or node.type not in ACTION_TYPES:
                return False
            if node.type in ("settings", "agent") and (state.get("ran") or {}).get(node.id) == self.s.turn_id:
                return False                                            # already done this turn, nothing follows
            ev.emit(EventType.STEP_TRANSITION, previous=self._last_step, step=f"{skill}/{node.id}", action=node.type)
            self._last_step = f"{skill}/{node.id}"
            if node.type == "tool":
                ctx = {"slots": self.s.slots, "parsed": self.s.memory.get("parsed") or {}}
                args = {k: flow_eval(v, ctx) for k, v in node.args.items()}
                call = ToolCall(id=f"flow_{node.id}_{self.s.turn_id}", name=node.tool, arguments=args)
                state.setdefault("ran", {})[node.id] = self.s.turn_id   # at most once per turn (no retry loops)
                result = await self._run_tool_node(node, call, ev)
                self.s.history.append(tool_call_message("", [call]))
                self.s.history.append({"role": "tool", "tool_call_id": call.id, "content": result.content})
                self._after_tool(call, result, ev)
                for slot, path in node.outputs.items() if result.ok else ():
                    value = flow_eval(path, {"result": result.data})
                    if value is None and isinstance(result.data, dict) and "result" in result.data:
                        value = flow_eval(path, {"result": result.data["result"]})   # {"result": {...}} replies
                    self.s.slots[slot] = value
                    ev.emit(EventType.SLOT_SET, field=slot, value=value if not isinstance(value, (list, dict))
                            else f"{type(value).__name__}[{len(value)}]", source=f"tool:{node.id}")
                state.setdefault("outcome", {})[node.id] = result.ok
                if result.ok and node.say and (line := self._flow_line(node)):     # the node's own spoken result
                    await self._say(line)
                    self.s.history.append({"role": "assistant", "content": line})
                if not result.ok and node.error_say and (line := self._flow_line(node, "error_say")):   # Hamsa's errorMessage
                    await self._say(line)
                    self.s.history.append({"role": "assistant", "content": line})
                if not result.ok and node.on_error == "fail":
                    await self._handoff(f"flow: tool {node.tool} failed", ev)
                if self.s.handoff or self.s.ended:
                    return True
            elif node.type == "settings":
                state.setdefault("ran", {})[node.id] = self.s.turn_id
                self._apply_settings(node, ev)
                state.setdefault("outcome", {})[node.id] = True
            elif node.type == "agent":
                state.setdefault("ran", {})[node.id] = self.s.turn_id
                done = await self._transfer_agent(node, ev)
                state.setdefault("outcome", {})[node.id] = done
                if done:
                    return True                                         # the call belongs to the other agent now
                graph = self.skills.graph(skill)
                if not any(e.source == node.id and (e.on == "failure" or (e.on is None and not e.when)) for e in graph.edges):
                    await self._handoff(f"flow: could not transfer to agent {self._agent_target(node)}", ev)
                    return True
            elif node.type == "transfer":
                state.pop("silent", None)
                await self._handoff(node.reason or "flow: transfer to a person", ev, node=node, quiet=silent)
                return True
            elif node.type == "end":
                state.pop("silent", None)
                if (line := self._flow_line(node)) and not silent:
                    await self._say(line)
                    self.s.history.append({"role": "assistant", "content": line})
                self.s.ended = True
                ev.emit(EventType.CALL_END, reason="completed", node=node.id, verified=self.s.auth.verified)
                await self.out.hangup()
                return True
            elif node.type == "skill":
                target = node.skill
                state["node"] = None                    # coming back later starts this skill's flow again
                if target not in getattr(self.skills, "skills", {}):
                    ev.emit(EventType.ERROR, level=Level.WARNING, during="flow", error=f"unknown skill {target}")
                    return False
                ev.emit(EventType.SKILL_EXIT, skill=skill, reason=f"flow:{node.id}")
                self.s.active_skill = target
                ev.emit(EventType.SKILL_ENTER, skill=target, routed_by="flow")
        return False

    def _flow_line(self, node, field: str = "say") -> str:
        """A node's fixed line (`say`, or `error_say`) in the call language (any language when that one is missing), with {{ }} filled in."""
        lines = getattr(node, field)
        line = lines.get(self.s.language.language) or next(iter(lines.values()), "")
        if line and is_template(line):
            line = render(line, {"slots": self.s.slots, **template_vars(self.s)})
        return line.strip()

    async def _say_static(self, skill: str, node, ev: BoundEmitter) -> str:
        """A conversation node with a fixed message: say it once when the flow reaches the node. Returns \"over\" (the
        turn is finished), \"again\" (the node doesn't wait for the caller: look at the next node now) or \"pass\" (let
        the model handle this turn). If the caller replies and no transition fires, the node's prompt (when it has
        one) takes over; without a prompt the line is said again."""
        state = self.skills.graph_state(skill, self.s)
        mark = (node.id, state.get("entered"))
        if state.get("said") == mark:
            if node.instructions:
                return "pass"
            if node.skip_response:
                return "over"                                # said already and nothing follows: wait for the caller
        line = self._flow_line(node)
        if not line:
            return "pass"
        state["said"] = mark
        ev.emit(EventType.STEP_TRANSITION, previous=self._last_step, step=f"{skill}/{node.id}", action="say")
        self._last_step = f"{skill}/{node.id}"
        await self._say(line)
        self.s.history.append({"role": "assistant", "content": line})
        return "again" if node.skip_response else "over"

    async def _run_tool_node(self, node, call: ToolCall, ev: BoundEmitter) -> ToolResult:
        """A flow tool node's call with its own behaviour: a longest wait, retries (on_error: retry), and a
        \"one moment\" line of its own. (A write already under way is never abandoned, so the wait doesn't cut it short.)"""
        lang = self.s.language.language
        filler = (node.processing.get(lang) or next(iter(node.processing.values()), "")) if node.processing else None
        attempts = 1 + (node.retries if node.on_error == "retry" else 0)
        for attempt in range(attempts):
            run = self._execute_with_filler(call, self._ctx(), ev, filler=filler)
            try:
                result = await (asyncio.wait_for(run, node.timeout_s) if node.timeout_s else run)
            except asyncio.TimeoutError:
                result = ToolResult(ok=False, error="timeout", content=json.dumps(
                    {"error": f"{node.tool} did not answer within {node.timeout_s} s"}))
                ev.emit(EventType.ERROR, level=Level.WARNING, during="flow_tool", tool=node.tool, error="timeout")
            if result.ok:
                break
            if attempt + 1 < attempts:
                ev.emit(EventType.SLOT_SET, field="flow:retry", node=node.id, attempt=attempt + 1)
        return result

    def _apply_settings(self, node, ev: BoundEmitter) -> None:
        """A settings node: its overrides join those already in force for this call (a later node changes them again;
        an empty value withdraws one). The transport applies the voice / listening ones; the model ones are read at
        the next reply, the system prompt at the next message."""
        ov = self.s.memory.setdefault("overrides", {})
        for section, value in node.overrides.items():
            if isinstance(value, dict):
                cur = ov.setdefault(section, {})
                for k, v in value.items():
                    if v is None or v == "":
                        cur.pop(k, None)
                    else:
                        cur[k] = v
                if not cur:
                    ov.pop(section, None)
            elif value is None or value == "":
                ov.pop(section, None)
            else:
                ov[section] = value
        self.s.memory["persona_override"] = ov.get("system_prompt") or None
        ev.emit(EventType.SLOT_SET, field="flow:settings", node=node.id, value=sorted(node.overrides))
        if self.on_settings:
            self.on_settings({k: (dict(v) if isinstance(v, dict) else v) for k, v in ov.items()})

    def _llm_options(self) -> dict[str, Any]:
        """The model / temperature this reply uses when the flow chose them: the current step's own, else the ones a
        settings node set (else the agent's, which the provider already has)."""
        base = (self.s.memory.get("overrides") or {}).get("llm") or {}
        node_of = getattr(self.skills, "node", None)
        node = node_of(self._flow_skill(), self.s) if node_of else None
        own = getattr(node, "llm", None) or {}
        out: dict[str, Any] = {}
        if model := own.get("model") or base.get("model"):
            out["model"] = model
        temperature = own.get("temperature") if own.get("temperature") is not None else base.get("temperature")
        if temperature is not None:
            out["temperature"] = temperature
        return out

    def _agent_target(self, node) -> str:
        """The agent an agent node hands the call to ({{ }} filled in)."""
        target = node.agent
        if is_template(target):
            target = render(target, {"slots": self.s.slots, **template_vars(self.s)}).strip()
        return target

    async def _transfer_agent(self, node, ev: BoundEmitter) -> bool:
        """An agent node: say the node's line, then hand the call to the other agent. False: it couldn't (no transport
        support, unknown agent, too many hand-offs) — the flow's failure path, or a person, takes over."""
        target = self._agent_target(node)
        if self.transfer_agent is None or not target:
            ev.emit(EventType.ERROR, level=Level.WARNING, during="agent_transfer", error="not available here", agent=target)
            return False
        if self.s.memory.get("agent_transfers", 0) >= MAX_AGENT_TRANSFERS:
            ev.emit(EventType.ERROR, level=Level.WARNING, during="agent_transfer", error="too many hand-offs", agent=target)
            return False
        if line := self._flow_line(node):
            await self._say(line)
            self.s.history.append({"role": "assistant", "content": line})
        try:
            return bool(await self.transfer_agent(AgentTransfer(target, node.handoff_history, node.handoff_variables)))
        except Exception as e:                                          # noqa: BLE001 — the call carries on
            ev.emit(EventType.ERROR, level=Level.ERROR, during="agent_transfer", error=repr(e)[:160], agent=target)
            return False

    def dtmf_plan(self) -> dict[str, Any] | None:
        """What the keypad does right now, for the voice call: the keys that move the flow on (the node's own
        transitions and the global ones) and the node's digit capture, if it collects digits. None: this agent's
        flow doesn't use the keypad."""
        skill = self._flow_skill()
        graph = getattr(self.skills, "graph", lambda _s: None)(skill)
        node = self.skills.node(skill, self.s) if graph is not None else None
        if node is None:
            return None
        return {"node": node.id, "keys": graph.dtmf_keys(node.id),
                "capture": dict(node.dtmf_capture) if node.type == "conversation" and node.dtmf_capture else None}

    def _turn_hooks(self) -> list:
        """The active skill's turn hooks (e.g. hmg.booking: dates, times, "I'm at …")."""
        names = getattr(self.skills, "turn_hook_names", lambda _s: [])(self.s.active_skill)
        return turn_hooks(names) if self.s.auth.verified else []

    # ------------------------------------------------------------------ extraction (regex first, then the LLM)

    def _awaited(self, text: str) -> tuple[str, dict[str, Any]] | None:
        """Which detail the call is waiting for, if the fast parsers didn't find it but the words may hold it."""
        stage = self.s.auth.stage
        if stage == "awaiting_mobile":
            return ("mobile", {}) if not normalize_mobile(text) and numberish(text) else None
        if stage == "awaiting_otp":
            return ("otp", {}) if not extract_code(text) and numberish(text) else None
        if stage == "awaiting_dob_and_name":
            return ("dob_name", {}) if not resolve_dob(text) and (numberish(text) or dateish(text)) else None
        for hook in self._turn_hooks():
            if (awaits := getattr(hook, "awaits", None)) and (found := awaits(self, text)):
                return found
        return None

    async def _extract(self, text: str, ev: BoundEmitter) -> dict[str, Any]:
        awaited = self._awaited(text)
        if awaited is None:
            return {}
        field, options = awaited
        offered: set[str] = set(options.get("offered") or ())
        last = next((m.get("content") or "" for m in reversed(self.s.history) if m.get("role") == "assistant"), "")
        r = await extract(self.llm, field, text, today=today_riyadh(), offered=offered,
                          context=f"The agent had just said: {last}" if last else "")
        shown = "•••" if field in ("mobile", "otp") and r.value else (str(r.value) if r.value is not None else None)
        ev.emit(EventType.SLOT_SET, field=f"extracted:{field}", value=shown, found=r.value is not None,
                latency_ms=r.latency_ms, source="llm")
        return {field: r.value} if r.value is not None else {}

    def _hints(self, text: str, extracted: dict[str, Any] | None = None) -> str:
        """Parses appended for the LLM (it is bad at digits, dates and weekdays): the fast pattern parsers first,
        the tight LLM extraction when they found nothing (`extracted`)."""
        s, hints = self.s, []
        x = extracted or {}
        stage = s.auth.stage
        s.memory["parsed"] = parsed = {}
        if stage == "awaiting_mobile" and (m := normalize_mobile(text) or x.get("mobile")):
            hints.append(f"mobile: {m}")
            parsed["mobile"] = m
        elif stage == "awaiting_otp" and (c := extract_code(text) or x.get("otp")):
            hints.append(f"code: {c}")
            parsed["code"] = c
        elif stage == "awaiting_dob_and_name" and (d := resolve_dob(text) or (x.get("dob_name") or {}).get("date_of_birth")):
            hints.append(f"date_of_birth: {d.isoformat()}")
            if (x.get("dob_name") or {}).get("first_name"):
                hints.append(f"first_name: {x['dob_name']['first_name']}")
        for hook in self._turn_hooks():
            if hints_for := getattr(hook, "hints", None):
                hints += hints_for(self, text, x)
        if s.last_reply and s.pending_action:
            hints.append(f"caller answered: {s.last_reply}")
        return f"\n[parsed: {'; '.join(hints)}]" if hints else ""

    async def _resolve_pending(self, ev: BoundEmitter) -> bool:
        """Runs a parked write on "yes". True if the turn is complete (the harness already answered)."""
        s = self.s
        pending = s.pending_action
        if pending.turn_id >= s.turn_id:            # parked this turn: the read-back hasn't been heard yet
            return False
        if s.last_reply == "yes":
            s.pending_action = None
            call = ToolCall(id=f"confirmed_{s.turn_id}", name=pending.tool, arguments=dict(pending.args))
            ctx = self._ctx(confirmed=pending.tool)
            s.history.append(tool_call_message("", [call]))
            result = await self._execute_with_filler(call, ctx, ev)
            s.history.append({"role": "tool", "tool_call_id": call.id, "content": result.content})
            self._after_tool(call, result, ev)
            s.last_reply = "consumed"   # this "yes" was for the read-back; further actions need a new one
            tool = self.executor.catalog.get(pending.tool)
            if result.ok and tool is not None and tool.success_line and hasattr(self.ph, tool.success_line):
                line = getattr(self.ph, tool.success_line)[s.language.language]
                await self._say(line)
                s.history.append({"role": "assistant", "content": line})
                return True
        elif s.last_reply == "no":
            s.pending_action = None
            ev.emit(EventType.POLICY_BLOCK, reason="caller_declined", tool=pending.tool)
        return False

    async def _llm_loop(self, ev: BoundEmitter) -> None:
        s = self.s
        for hop in range(MAX_HOPS):
            self._track_step(ev)
            await self._run_auto_calls(ev)
            if await self._run_graph_actions(ev):
                return
            if await self._say_harness_lines():
                return
            messages = build_messages(s, self.skills)
            allowed = self._allowed()
            tools = self.executor.llm_tools(allowed) + control.control_specs(
                verified=s.auth.verified, routable={} if s.flow_skill else self.skills.routable(),
                disambiguating=s.auth.stage == "awaiting_dob_and_name", awaiting=self._awaiting_answer())
            spoken_before = len(self._spoken)
            try:
                text, calls = await self._stream(messages, tools, ev.bind(skill=s.active_skill), hop)
            except Exception as e:
                # Groq rejects the whole request when the model invents a tool name or writes a malformed tool
                # call (tool_use_failed); retry once with a correction.
                kind = _tool_call_error(e)
                if not kind:
                    raise
                ev.emit(EventType.ERROR, level=Level.WARNING, error=f"{kind} from model", detail=str(e)[:300])
                valid = ", ".join(t["function"]["name"] for t in tools)
                already = " ".join(self._spoken[spoken_before:])
                note = (f"You called a tool that is not available now. Only these tools exist: {valid}."
                        if kind == "invalid tool name" else
                        "Your last tool call was malformed. Call a tool with valid JSON arguments exactly as its "
                        "schema says, or just reply to the caller.")
                if already:
                    note += f" You already said to the caller: \"{already}\" — do not repeat it."
                messages = messages + [{"role": "system", "content": note}]
                text, calls = await self._stream(messages, tools, ev.bind(skill=s.active_skill), hop,
                                                 mute=bool(already))
                text = (already + " " + text).strip() if already else text
            if not calls:
                if text:
                    s.history.append({"role": "assistant", "content": text})
                    for hook in self._turn_hooks():
                        if after := getattr(hook, "after_text", None):
                            after(self, text)
                elif len(self._spoken) == spoken_before:
                    await self._say(self.ph.FALLBACK[s.language.language])
                return
            s.history.append(tool_call_message(text, calls))
            # a question anywhere in the reply (it may end with the "more time slots" note, not a "?")
            asked = bool(re.search(r"[?؟](\s|$)", text))
            for call in calls:
                tool = self.executor.catalog.get(call.name)
                acts = (tool is not None and tool.kind in ("write", "send")) or \
                    call.name in (control.END_CALL, control.TRANSFER)
                if asked and (acts or call.name == control.RECORD_ANSWER):
                    ev.emit(EventType.POLICY_BLOCK, reason="action_before_answer", tool=call.name)
                    content = json.dumps({"not_executed": "you asked the caller a question; wait for their answer"})
                else:
                    content = await self._run_call(call, ev)
                s.history.append({"role": "tool", "tool_call_id": call.id, "content": content})
            if s.handoff or s.ended or self._turn_answered:
                return
            self._route_if_verified(ev)
            if await self._say_harness_lines():
                return
            if asked and not self._needs_followup():
                # The caller was asked something: wait for their answer instead of talking over it.
                ev.emit(EventType.POLICY_BLOCK, reason="question_asked_stop_turn")
                return
        await self._say(self.ph.FALLBACK[s.language.language])

    async def _stream(self, messages, tools, ev: BoundEmitter, hop: int,
                      mute: bool = False) -> tuple[str, list[ToolCall]]:
        lang = self.s.language.language
        chunker = SentenceChunker()
        spoken: list[str] = []
        calls: list[ToolCall] = []
        t0 = time.perf_counter()
        first = True
        asked = False   # one question per reply: stop speaking after the first question …
        after_question = 0   # … except one short statement after it
        ev.emit(EventType.LLM_REQUEST, hop=hop, tools=len(tools), messages=len(messages))
        async for event in self.llm.stream(messages, tools=tools, **self._llm_options()):
            if isinstance(event, TextDelta):
                if first:
                    ev.emit(EventType.LLM_FIRST_TOKEN, latency_ms=round((time.perf_counter() - t0) * 1000, 1))
                    first = False
                for sentence in chunker.feed(event.text):
                    if mute or (asked and (after_question >= 1 or not _MORE_TIMES_NOTE.search(sentence))):
                        continue
                    if asked:
                        after_question += 1
                    if self._blocked_claim(sentence, ev):
                        sentence = self.ph.READBACK_REASK[lang] if self.s.pending_action else ""
                        asked = True
                        if not sentence:
                            continue
                    await self._say(sentence)
                    spoken.append(sentence)
                    asked = asked or sentence.rstrip().endswith(("?", "؟"))
            elif isinstance(event, ToolCallsReady):
                calls = event.calls
            elif isinstance(event, LLMDone):
                ev.emit(EventType.LLM_END, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                        finish=event.finish_reason, usage=event.usage, tool_calls=[c.name for c in calls])
        if (rest := chunker.flush()) and not mute and (not asked or (after_question == 0 and
                                                                     _MORE_TIMES_NOTE.search(rest))):
            if self._blocked_claim(rest, ev):
                rest = self.ph.READBACK_REASK[lang] if self.s.pending_action else ""
            if rest:
                await self._say(rest)
                spoken.append(rest)
        if asked:
            ev.emit(EventType.POLICY_BLOCK, reason="one_question_per_reply")
        return clean_for_speech(" ".join(spoken)), calls

    # ------------------------------------------------------------------ tools

    async def _run_call(self, call: ToolCall, ev: BoundEmitter) -> str:
        s = self.s
        if call.name in control.CONTROL_TOOLS:
            return await self._control(call, ev)
        result = await self._execute_with_filler(call, self._ctx(), ev)
        self._after_tool(call, result, ev)
        a = s.auth
        if a.otp_attempts >= MAX_OTP_ATTEMPTS and not a.verified:
            await self._handoff("OTP attempts exhausted", ev)
        return result.content

    def _tool_line(self, lines: dict[str, str], call: ToolCall) -> str:
        """A tool's start / done line in the caller's language (templated with the call's arguments)."""
        text = lines.get(self.s.language.language) or lines.get("en") or lines.get("ar") or ""
        if text and is_template(text):
            text = render(text, {"slots": self.s.slots, "args": call.arguments, **template_vars(self.s)})
        return text

    async def _execute_with_filler(self, call: ToolCall, ctx: ToolContext, ev: BoundEmitter,
                                   filler: str | None = None) -> ToolResult:
        tool = self.executor.catalog.get(call.name)
        task = asyncio.ensure_future(self.executor.execute(call, ctx, ev))
        critical = tool is not None and tool.kind in ("write", "send")
        try:
            lang = self.s.language.language
            if tool is not None and tool.enabled and tool.say_start and (line := self._tool_line(tool.say_start, call)):
                self._filler_said = True                    # the tool's own start line replaces the generic filler
                await self._say(line)
            done, _ = await asyncio.wait({task}, timeout=self.filler_after_s)
            if not done and not self._filler_said:
                self._filler_said = True
                if filler:                                  # the flow node's own "one moment" line
                    await self._say(render(filler, {"slots": self.s.slots, **template_vars(self.s)}) if is_template(filler) else filler)
                elif special := self.ph.SLOW_TOOL_FILLER.get(call.name):
                    await self._say(special[lang])
                else:
                    fillers = self.ph.FILLER[lang]
                    await self._say(fillers[self._filler_idx % len(fillers)])
                    self._filler_idx += 1
                for _ in range(2):
                    done, _ = await asyncio.wait({task}, timeout=STILL_WORKING_AFTER_S)
                    if done:
                        break
                    await self._say(self.ph.STILL_WORKING[lang])     # no long silences on the phone
            result = await (asyncio.shield(task) if critical else task)
            if result.ok and tool is not None and tool.say_done and (line := self._tool_line(tool.say_done, call)):
                await self._say(line)
            return result
        except asyncio.CancelledError:
            if critical:   # never abandon a booking / cancellation half-way: finish and record it
                result = await task
                self.s.history.append({"role": "tool", "tool_call_id": call.id, "content": result.content})
            else:
                task.cancel()
            raise

    async def _control(self, call: ToolCall, ev: BoundEmitter) -> str:
        s, args = self.s, call.arguments
        if call.name == control.SWITCH_SKILL:
            skill = args.get("skill")
            if not s.auth.verified or s.flow_skill or skill not in self.skills.routable():
                return json.dumps({"error": "not available"})
            ev.emit(EventType.SKILL_EXIT, skill=s.active_skill)
            s.active_skill = skill
            ev.emit(EventType.SKILL_ENTER, skill=skill)
            return json.dumps({"ok": True, "active": skill})
        if call.name == control.SELECT_PATIENT:
            result = self.policy.select_patient(str(args.get("first_name", "")), str(args.get("date_of_birth", "")),
                                                self._ctx())
            ev.emit(EventType.SLOT_SET, field="patient_selected", value=result["matched"])
            if not result["matched"] and s.auth.match_attempts >= MAX_MATCH_ATTEMPTS:
                await self._handoff("could not identify the caller", ev)
            return json.dumps(result, ensure_ascii=False)
        if call.name == control.RECORD_ANSWER:
            return await self._record_answer(str(args.get("question", "")), str(args.get("answer", "")), ev)
        if call.name == control.TRANSFER:
            await self._handoff(str(args.get("reason", "agent requested")), ev)
            return json.dumps({"ok": True})
        if call.name == control.END_CALL:
            s.ended = True
            ev.emit(EventType.CALL_END, reason="completed", verified=s.auth.verified)
            await self.out.hangup()
            return json.dumps({"ok": True})
        return json.dumps({"error": "unknown control tool"})

    # ------------------------------------------------------------------ yes / no read by the model

    def _awaiting_answer(self) -> list[str]:
        s, a = self.s, self.s.auth
        waiting = []
        if a.verified and not a.identity_confirmed and s.memory.get("identity_asked"):
            waiting.append("identity")
        # only once the read-back was said: in the turn that parked the booking nobody has heard it yet
        # (live 2026-10-04: booking parked, then record_answer "yes" in the same turn booked with no read-back)
        if s.pending_action is not None and s.pending_action.turn_id < s.turn_id:
            waiting.append("booking_confirmation")
        return waiting

    async def _record_answer(self, question: str, answer: str, ev: BoundEmitter) -> str:
        s = self.s
        if question == "booking_confirmation" and s.pending_action is not None \
                and s.pending_action.turn_id >= s.turn_id:
            ev.emit(EventType.POLICY_BLOCK, reason="answer_before_readback", tool=s.pending_action.tool)
            return json.dumps({"error": "NOTHING is booked: the caller hasn't heard the details yet. Read them back "
                                        "in one short sentence and ask whether to go ahead; then wait for their "
                                        "answer."})
        if question not in self._awaiting_answer():
            return json.dumps({"error": f"no {question or 'such'} question is waiting for an answer"})
        ev.emit(EventType.SLOT_SET, field=f"answer:{question}", value=answer, source="model")
        if answer == "unclear":
            return json.dumps({"ok": True, "next": "ask the question again, briefly"})
        if question == "identity":
            if answer == "no":
                await self._handoff(self.ph.NOT_THE_PATIENT, ev)
                return json.dumps({"ok": True, "transferred": True})
            s.auth.identity_confirmed = True
            ev.emit(EventType.SLOT_SET, field="identity_confirmed", value=True, source="model")
            return json.dumps({"ok": True, "next": "the caller is the patient — continue with the current task "
                                                  "(don't question their name again)"})
        # booking_confirmation — exactly the path of a clear "yes" / "no"
        s.last_reply = answer
        if await self._resolve_pending(ev):
            self._turn_answered = True              # the harness already said "booked successfully …"
            return json.dumps({"ok": True, "booked": True})
        if answer == "no":
            return json.dumps({"ok": True, "declined": True, "next": "nothing was booked — ask what they would "
                                                                    "like to change"})
        return json.dumps({"ok": True, "next": "the booking did not go through — tell the caller why (see the "
                                              "tool result above) and offer an alternative"})

    # ------------------------------------------------------------------ helpers

    async def _say_harness_lines(self) -> bool:
        """Lines the harness says word for word instead of the model: the HIS's own OTP confirmation ("…via WhatsApp
        that ends with 2968") and, once verified, "Am I speaking to <name>?". True if the turn is done."""
        s, lang = self.s, self.s.language.language
        lines: list[str] = []
        if msg := s.memory.pop("otp_message", None):
            lines = [msg, self.ph.OTP_CODE_QUESTION[lang]]
        elif s.memory.pop("identity_say", None):
            lines = [self.ph.IDENTITY_QUESTION[lang].format(name=await self._spoken_name())]
        if not lines:
            return False
        for line in lines:
            await self._say(line)
        s.history.append({"role": "assistant", "content": " ".join(lines)})
        return True

    async def _spoken_name(self) -> str:
        """The patient's full name as it should be said in the call language (the HIS often has only English)."""
        a, lang = self.s.auth, self.s.language.language
        name = a.full_name or a.first_name or ""
        if name.isupper():                      # "RAZA ASMATULLAH" → "Raza Asmatullah" (TTS may spell capitals out)
            name = name.title()
        if lang != "ar" or not name or detect(name) == "ar":
            return name
        if cached := self.s.memory.get("name_ar"):
            return cached
        task = self.s.memory.get("name_ar_task")
        if task is None or task.done() and not self.s.memory.get("name_ar"):
            task = self.s.memory["name_ar_task"] = asyncio.ensure_future(self._transliterate(name))
        return await asyncio.shield(task)

    async def _transliterate(self, name: str) -> str:
        text = ""
        try:
            async for ev in self.llm.stream([{"role": "user", "content": self.ph.TRANSLITERATE.format(name=name)}]):
                if isinstance(ev, TextDelta):
                    text += ev.text
        except Exception:
            text = ""
        arabic = " ".join(re.findall(r"[\u0621-\u064A]+", text))
        self.s.memory["name_ar"] = arabic or name
        return self.s.memory["name_ar"]

    def _blocked_claim(self, sentence: str, ev: BoundEmitter) -> bool:
        tool = self.executor.catalog.get(self.s.pending_action.tool) if self.s.pending_action else None
        if claim := unbacked_claim(sentence, self._done_claims, tool.backs if tool else None):
            ev.emit(EventType.POLICY_BLOCK, reason="unbacked_claim", claim=claim, text=sentence[:120])
            return True
        return False

    def _after_tool(self, call: ToolCall, result: ToolResult, ev: BoundEmitter) -> None:
        tool = self.executor.catalog.get(call.name)
        if result.ok and tool is not None and tool.kind in ("write", "send"):
            self._done_tools.add(call.name)
            if tool.backs:
                self._done_claims.add(tool.backs)
        role = tool.role if tool is not None else None
        if role == "identity.send_code" and result.ok and isinstance(result.data, dict):
            msg = str(result.data.get("message") or "").strip()
            if msg and detect(msg) == self.s.language.language and len(msg) > 20:   # the system's sentence, in our language
                self.s.memory["otp_message"] = msg
        if role == "identity.lookup" and self.s.auth.full_name and self.s.language.language == "ar" \
                and "name_ar_task" not in self.s.memory:
            # "هل أتحدث مع …؟" needs the name in Arabic letters: prepare it while the OTP is being sent
            self.s.memory["name_ar_task"] = asyncio.ensure_future(self._transliterate(self.s.auth.full_name))
        if role == "identity.verify_code" and self.s.auth.verified and not self.s.auth.identity_confirmed:
            self.s.memory["identity_say"] = True
        on_tool = getattr(self.skills, "on_tool", None)
        if on_tool:
            skill = self._flow_skill()
            args = normalize_datetime_args(call.arguments)
            for slot, value in on_tool(skill, call.name, args, result.data, result.ok, self.s).items():
                ev.emit(EventType.SLOT_SET, field=slot, value=value, tool=call.name)
        if tool is not None:
            run_tool_hooks("after", tool, call.arguments, self.s, result=result, agent=self)

    async def _run_auto_calls(self, ev: BoundEmitter) -> None:
        """Step-declared lookups the harness performs itself (e.g. the hospital's clinic list once a hospital
        is chosen), so the model always suggests from real data and no LLM hop is spent asking for it."""
        auto = getattr(self.skills, "auto_calls", None)
        if not auto:
            return
        done = self.s.memory.setdefault("auto_done", set())
        for _ in range(4):                       # chain: lookup → send OTP happen in the same turn
            skill = self._flow_skill()
            ran = False
            for tool, args, blocking in auto(skill, self.s):
                key = f"{tool}:{json.dumps(args, sort_keys=True)}"
                if key in done:
                    continue
                if not blocking and not self.executor.ready(tool, args, self._ctx()):
                    # Don't make the caller wait (clinics take ~2 s): fetch in the background; the result joins
                    # the conversation on the next hop / turn, before the caller's answer needs it.
                    self.executor.prefetch(tool, args, self._ctx(), ev)
                    continue
                done.add(key)
                call = ToolCall(id=f"auto_{len(done)}_{self.s.turn_id}", name=tool, arguments=args)
                result = await self._execute_with_filler(call, self._ctx(), ev)
                self.s.history.append(tool_call_message("", [call]))
                self.s.history.append({"role": "tool", "tool_call_id": call.id, "content": result.content})
                self._after_tool(call, result, ev)
                ran = True
                if self.s.handoff:
                    return
            self._route_if_verified(ev)
            if not ran:
                return

    def _route_if_verified(self, ev: BoundEmitter) -> None:
        s = self.s
        if s.auth.verified and not s.flow_skill and s.active_skill == VERIFY_SKILL:
            ev.emit(EventType.SKILL_EXIT, skill=VERIFY_SKILL, reason="verified")
            # Route the caller's first request directly (saves a switch_skill round trip).
            routed = getattr(self.skills, "classify", lambda _t: None)(s.pending_intent)
            s.active_skill = routed or self.settings.entry_skill
            ev.emit(EventType.SKILL_ENTER, skill=s.active_skill, routed_by="keywords" if routed else None)

    def _needs_followup(self) -> bool:
        """A pending read-back must still be spoken even if the model already asked something."""
        return self.s.pending_action is not None and self.s.pending_action.turn_id == self.s.turn_id

    def _track_step(self, ev: BoundEmitter) -> None:
        current_step = getattr(self.skills, "current_step", None)
        if not current_step:
            return
        skill = self._flow_skill()
        step = f"{skill}/{current_step(skill, self.s)}"
        if step != self._last_step:
            ev.emit(EventType.STEP_TRANSITION, previous=self._last_step, step=step)
            self._last_step = step

    def _allowed(self) -> set[str]:
        s = self.s
        skill = self._flow_skill()
        names = set(self.skills.tools(skill, s))
        catalog = self.executor.catalog
        # a web tool exists only where the visitor's page registered it (never on a phone call)
        return {n for n in names if (t := catalog.get(n)) is None or t.source != "web" or n in s.web_tools}

    def _ctx(self, confirmed: str | None = None) -> ToolContext:
        s = self.s
        extra: dict[str, Any] = {"policy": self.policy}
        if getattr(self, "web_bridge", None) is not None:
            extra["web_bridge"] = self.web_bridge
        if confirmed:
            extra["confirmed_action"] = confirmed
        return ToolContext(call_id=s.call_id, language_id=s.language_id, patient_id=s.auth.patient_id,
                           allowed_tools=self._allowed() | ({confirmed} if confirmed else set()), extra=extra)

    async def _say(self, text: str) -> None:
        text = clean_for_speech(text)
        if is_reasoning_leak(text):
            self.ev.emit(EventType.POLICY_BLOCK, reason="reasoning_leak", text=text[:120])
            return
        if self.s.language.language == "ar" and self.s.gender.known == "female":
            text = to_feminine(text)
        if not text:
            return
        self._spoken.append(text)
        for hook in self._turn_hooks():
            if on_say := getattr(hook, "on_say", None):
                on_say(self, text)
        self.ev.emit(EventType.AGENT_SAY, text=text)
        await self.out.say(text, language=self.s.language.language)

    def _transfer_options(self, node) -> dict[str, Any]:
        """A transfer node's own destination / ring timeout / SIP headers (templates filled in) for the transport."""
        if node is None:
            return {}
        ctx = {"slots": self.s.slots, **template_vars(self.s)}
        fill = lambda v: render(v, ctx).strip() if isinstance(v, str) and is_template(v) else v          # noqa: E731
        opts: dict[str, Any] = {}
        if dest := fill(node.destination):
            opts["destination"] = dest
        if node.timeout_s:
            opts["timeout_s"] = node.timeout_s
        if node.headers:
            opts["headers"] = {k: fill(v) for k, v in node.headers.items()}
        return opts

    async def _handoff(self, reason: str, ev: BoundEmitter, node=None, quiet: bool = False) -> None:
        s = self.s
        if s.handoff:
            return
        s.handoff = {"reason": reason, "turn": s.turn_id, "verified": s.auth.verified, "skill": s.active_skill}
        ev.emit(EventType.HANDOFF, reason=reason)
        if not (quiet or (node is not None and node.transfer_type == "cold")):     # cold / silent: connect at once
            announced = any(w in " ".join(self._spoken) for w in ("أحول", "بحول", "الزملاء", "زميل", "transfer",
                                                                     "connect you", "colleague"))
            msg = (node is not None and node.say and self._flow_line(node)) or \
                (self.ph.HANDOFF_SHORT if announced else self.ph.HANDOFF)[s.language.language]
            s.history.append({"role": "assistant", "content": msg})
            await self._say(msg)
        await self.out.transfer(reason, **self._transfer_options(node))

    def _record_interrupted(self) -> None:
        """Keep history consistent after barge-in: pair dangling tool calls, keep what was spoken."""
        h = self.s.history
        answered = {m.get("tool_call_id") for m in h if m.get("role") == "tool"}
        for msg in list(h):
            for tc in msg.get("tool_calls") or []:
                if tc["id"] not in answered:
                    h.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"interrupted": True})})
        if self._spoken and not (h and h[-1].get("role") == "assistant" and not h[-1].get("tool_calls")):
            h.append({"role": "assistant", "content": " ".join(self._spoken) + " —"})
