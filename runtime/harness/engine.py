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
from runtime.skills.flow import _eval as flow_eval
from runtime.skills.graph import ACTION_TYPES
from runtime.tools.hooks import load_packs, run_tool_hooks, turn_hooks

from . import control
from .context import build_messages, tool_call_message
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
    async def transfer(self, reason: str) -> None: ...
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
        load_packs()                                       # named tool / turn hooks referenced by the config
        if _dispatch_pre not in executor.pre_hooks:        # hooks are shared; they route per call via ctx
            executor.pre_hooks.append(_dispatch_pre)
            executor.post_hooks.append(_dispatch_post)

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self.settings.main_flow:                          # one flow graph runs the whole call
            self.s.flow_skill = self.s.active_skill = self.settings.main_flow
        if not self.settings.require_verification:          # e.g. an information line: straight to its entry skill
            a = self.s.auth
            a.required, a.verified, a.identity_confirmed = False, True, True
            if not self.s.flow_skill:
                self.s.active_skill = self.settings.entry_skill
        lang = self.s.language.language
        self.ev.emit(EventType.CALL_START, ani_present=self.s.ani is not None, language=lang,
                     config_version=getattr(self, "config_version", None), **getattr(self, "agent_ref", {}))
        await self._say(self.ph.GREETING[lang])
        self.s.history.append({"role": "assistant", "content": self.ph.GREETING[lang]})

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
        node = self.skills.node(skill, self.s)
        state = self.skills.graph_state(skill, self.s)
        state["llm"] = {}
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
            if node is None or node.type not in ACTION_TYPES:
                return False
            state = self.skills.graph_state(skill, self.s)
            ev.emit(EventType.STEP_TRANSITION, previous=self._last_step, step=f"{skill}/{node.id}", action=node.type)
            self._last_step = f"{skill}/{node.id}"
            if node.type == "tool":
                ctx = {"slots": self.s.slots, "parsed": self.s.memory.get("parsed") or {}}
                args = {k: flow_eval(v, ctx) for k, v in node.args.items()}
                call = ToolCall(id=f"flow_{node.id}_{self.s.turn_id}", name=node.tool, arguments=args)
                state.setdefault("ran", {})[node.id] = self.s.turn_id   # at most once per turn (no retry loops)
                result = await self._execute_with_filler(call, self._ctx(), ev)
                self.s.history.append(tool_call_message("", [call]))
                self.s.history.append({"role": "tool", "tool_call_id": call.id, "content": result.content})
                self._after_tool(call, result, ev)
                state.setdefault("outcome", {})[node.id] = result.ok
                if self.s.handoff or self.s.ended:
                    return True
            elif node.type == "transfer":
                await self._handoff(node.reason or "flow: transfer to a person", ev)
                return True
            elif node.type == "end":
                if line := node.say.get(self.s.language.language):
                    await self._say(line)
                    self.s.history.append({"role": "assistant", "content": line})
                self.s.ended = True
                ev.emit(EventType.CALL_END, reason="completed", node=node.id)
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
        async for event in self.llm.stream(messages, tools=tools):
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

    async def _execute_with_filler(self, call: ToolCall, ctx: ToolContext, ev: BoundEmitter) -> ToolResult:
        tool = self.executor.catalog.get(call.name)
        task = asyncio.ensure_future(self.executor.execute(call, ctx, ev))
        critical = tool is not None and tool.kind in ("write", "send")
        try:
            lang = self.s.language.language
            done, _ = await asyncio.wait({task}, timeout=self.filler_after_s)
            if not done and not self._filler_said:
                self._filler_said = True
                if special := self.ph.SLOW_TOOL_FILLER.get(call.name):
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
            return await (asyncio.shield(task) if critical else task)
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
            ev.emit(EventType.CALL_END, reason="completed")
            await self.out.hangup()
            return json.dumps({"ok": True})
        return json.dumps({"error": "unknown control tool"})

    # ------------------------------------------------------------------ yes / no read by the model

    def _awaiting_answer(self) -> list[str]:
        s, a = self.s, self.s.auth
        waiting = []
        if a.verified and not a.identity_confirmed and s.memory.get("identity_asked"):
            waiting.append("identity")
        if s.pending_action is not None:
            waiting.append("booking_confirmation")
        return waiting

    async def _record_answer(self, question: str, answer: str, ev: BoundEmitter) -> str:
        s = self.s
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
        return set(self.skills.tools(skill, s))

    def _ctx(self, confirmed: str | None = None) -> ToolContext:
        s = self.s
        extra: dict[str, Any] = {"policy": self.policy}
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

    async def _handoff(self, reason: str, ev: BoundEmitter) -> None:
        s = self.s
        if s.handoff:
            return
        s.handoff = {"reason": reason, "turn": s.turn_id, "verified": s.auth.verified, "skill": s.active_skill}
        ev.emit(EventType.HANDOFF, reason=reason)
        announced = any(w in " ".join(self._spoken) for w in ("أحول", "بحول", "الزملاء", "زميل", "transfer",
                                                                 "connect you", "colleague"))
        msg = (self.ph.HANDOFF_SHORT if announced else self.ph.HANDOFF)[s.language.language]
        s.history.append({"role": "assistant", "content": msg})
        await self._say(msg)
        await self.out.transfer(reason)

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
