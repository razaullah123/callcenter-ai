"""One live call: caller audio in → VAD / turn detection → STT → agent harness → speech player → audio out.

Barge-in: when the caller talks for `voice_barge_in_ms` while the agent is speaking, playback stops at
once, the transport is told to drop buffered audio, the running agent turn is cancelled (writes in flight
still complete), and the agent's history is corrected to what the caller actually heard.
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor

from runtime.app import Runtime
from runtime.events import EventType, Level
from runtime.harness.engine import Agent
from runtime.harness.handoff import carry_over
from runtime.harness.variables import build_custom, declared_variables
from runtime.harness.session import Session
from runtime.providers import AudioInput
from runtime.providers.audio import mulaw_to_pcm16, resample_pcm16

from runtime.platform.bundle import DEFAULT_STT_HINT
from runtime.platform.loader import LoadedAgent, agent_of

from .phrases import PhraseCache
from .player import AudioFormat, SendAudio, SendEvent, SpeechPlayer
from .stt_quality import LevelGate, noise_reason, speech_level_db, speech_stats
from runtime.harness.nlu.numbers import normalize_mobile
from .turn import MS_PER_WINDOW, TurnConfig, TurnDetector

# Voice activity detection runs off the event loop: onnxruntime releases the GIL, so VAD for many concurrent
# calls spreads across cores instead of saturating the loop thread (measured: ~0.25 ms CPU per 20 ms frame).
VAD_POOL = ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 1), thread_name_prefix="vad")


def vad_rate_for(line_rate: int) -> int:
    """Run VAD / STT at the line's own rate when Silero supports it (8 / 16 kHz): no resampling at all."""
    return line_rate if line_rate in (8000, 16000) else 16000

# Whisper "hallucinations" on noise / silence (subtitle credits etc.) — never real caller speech.



class VoiceCall:
    stt_hint: dict[str, str] = DEFAULT_STT_HINT     # replaced by the call's agent's own vocabulary hint
    _listeners: set = frozenset()                    # replaced per call by a set of listener queues (nobody listens by default)

    def __init__(self, rt: Runtime, *, call_id: str, in_fmt: AudioFormat, out_fmt: AudioFormat,
                 send_audio: SendAudio, send_event: SendEvent, ani: str | None = None,
                 phrases: PhraseCache | None = None, chunk_ms: int = 20, aec: bool = False,
                 barge_in_grace_ms: int = 0, min_suppression_ratio: float = 0.0,
                 agent: LoadedAgent | None = None, params: dict | None = None) -> None:
        # the agent answering this call, loaded complete and frozen: a publish never changes a call in progress
        self.loaded = agent or agent_of(rt)
        s = self.loaded.settings
        self.rt, self.in_fmt, self._send_event = rt, in_fmt, send_event
        self.providers = self.loaded.providers
        self.stt_hint = self.loaded.stt_hint
        phrases = phrases if phrases is not None else self.providers.phrases
        # custom variables: the agent's defaults with the call start's `params` on top (ValueError: bad params)
        self.session = Session(call_id=call_id, ani=ani, agent_name=self.loaded.name, agent_id=self.loaded.agent_id,
                               params=dict(params or {}), custom=build_custom(declared_variables(self.loaded.bundle), params))
        self.ev = rt.bus.bind(call_id=call_id)
        self.aec = None
        if aec:
            try:
                from .aec import EchoCanceller
                self.aec = EchoCanceller(in_fmt.sample_rate)
            except Exception as e:           # livekit missing: run without AEC, never fail the call
                self.ev.emit(EventType.ERROR, level=Level.WARNING, during="aec_init", error=repr(e))
        self._out_fmt = out_fmt
        self._raw_send_audio = send_audio
        self.barge_in_grace_ms = barge_in_grace_ms
        self.min_suppression_ratio = min_suppression_ratio
        self.player = SpeechPlayer(self.providers.tts, out_fmt, self._send_and_reference, send_event, self.ev,
                                   phrases, frame_ms=chunk_ms)
        self.stt_model: str | None = None            # a flow's settings node may pick another speech-to-text model
        self.agent = self._make_agent()
        self.vad_rate = vad_rate_for(in_fmt.sample_rate)
        self._in_resampler = self._make_resampler(in_fmt.sample_rate)
        vad = min(0.95, max(0.05, s.voice_vad_threshold))
        self.turns = TurnDetector(TurnConfig(end_silence_ms=s.voice_end_silence_ms, start_threshold=vad,
                                             end_threshold=min(0.35, vad * 0.7)), sample_rate=self.vad_rate)
        self.interrupt = s.voice_interrupt
        self.inactivity_s = s.voice_inactivity_s
        self.max_call_s = s.call_max_minutes * 60
        self._watch: asyncio.Task | None = None
        self._last_activity = time.monotonic()
        self.barge_in_ms = s.voice_barge_in_ms
        self.barge_in_confirm = s.voice_barge_in_confirm
        self._next_barge_ms = self.barge_in_ms
        self._barge_check: asyncio.Task | None = None
        self._utt_ms = 0.0               # utterance in progress: length …
        self._utt_overlap_ms = 0.0       # … and how much of it was over the agent's voice (or its echo tail)
        self.levels = LevelGate(margin_db=s.voice_level_gate_db) if s.voice_level_gate_db > 0 else None
        self._agent_task: asyncio.Task | None = None
        self._stt_tasks: set[asyncio.Task] = set()
        self._barged = False
        self._speech_ms = 0
        self._speculative: asyncio.Task | None = None   # STT started at the first pause
        self._dtmf = ""
        self._dtmf_timer: asyncio.Task | None = None     # ends a keypad capture after a pause
        self._stopped = False
        self._listeners: set[asyncio.Queue] = set()      # console users listening in (listen-only)
        self.end_reason = "in_progress"
        self._forced_reason: str | None = None
        self.close_transport = None      # set by the transport: closes the connection from the server side

    def _make_agent(self) -> Agent:
        s = self.loaded.settings
        agent = Agent(self.session, self.loaded.executor, self.providers.llm, self.loaded.skills, self.player,
                      self.rt.bus.bind(), filler_after_s=s.voice_filler_after_s, settings=s)
        agent.config_version = self.loaded.version
        agent.agent_ref = {"agent_id": self.loaded.agent_id, "release_id": self.loaded.release_id}
        agent.on_settings = self._on_settings
        agent.transfer_agent = self.switch_agent
        return agent

    def _apply_call_settings(self, s) -> None:
        """The settings that shape listening and interruptions, read from `s` (the agent's own, or those with a flow's
        overrides on top)."""
        self.interrupt = s.voice_interrupt
        self.inactivity_s = s.voice_inactivity_s
        self.barge_in_ms = self._next_barge_ms = s.voice_barge_in_ms
        vad = min(0.95, max(0.05, s.voice_vad_threshold))
        cfg = self.turns.cfg
        cfg.end_silence_ms, cfg.start_threshold, cfg.end_threshold = s.voice_end_silence_ms, vad, min(0.35, vad * 0.7)

    def _on_settings(self, overrides: dict) -> None:
        """A flow's settings node changed what is in force: the voice, the speech-to-text model and the listening
        settings follow (the agent's own values return for anything not overridden)."""
        self.player.voices = {k: v for k, v in (overrides.get("voice") or {}).items() if v}
        self.stt_model = overrides.get("stt_model") or None
        call = overrides.get("call") or {}
        names = {"interrupt": "voice_interrupt", "response_delay_ms": "voice_end_silence_ms", "inactivity_s": "voice_inactivity_s",
                 "min_interruption_ms": "voice_barge_in_ms", "vad_threshold": "voice_vad_threshold"}
        self._apply_call_settings(self.loaded.settings.model_copy(update={names[k]: v for k, v in call.items() if k in names}))
        watch = getattr(self, "_watch", None)
        if (self.inactivity_s > 0 or self.max_call_s > 0) and (watch is None or watch.done()) and not self._stopped:
            self._watch = asyncio.create_task(self._watch_limits())        # a limit was switched on mid-call

    async def switch_agent(self, req) -> bool:
        """Hand the call to another agent of this project (a flow's \"transfer agent\" node): its models, voice, skills and
        tools take over; the conversation and the collected values come along only if the node says so. False: the
        agent couldn't be loaded."""
        try:
            loaded = await self.rt.agent_for_call(agent_id=req.agent_id)
        except Exception as e:                                                  # noqa: BLE001
            self.ev.emit(EventType.ERROR, level=Level.WARNING, during="agent_transfer", agent=req.agent_id, error=repr(e)[:160])
            return False
        previous = self.loaded.agent_id
        self.session = carry_over(self.session, loaded.name, req, agent_id=loaded.agent_id,
                                  declared=declared_variables(loaded.bundle))
        self.loaded, self.providers, self.stt_hint, self.stt_model = loaded, loaded.providers, loaded.stt_hint, None
        self.player.tts, self.player.voices = self.providers.tts, {}
        self.agent = self._make_agent()
        self._apply_call_settings(loaded.settings)
        self.max_call_s = loaded.settings.call_max_minutes * 60
        self.ev.emit(EventType.AGENT_TRANSFER, agent_id=loaded.agent_id, release_id=loaded.release_id,
                     config_version=loaded.version, previous=previous, history=req.history, variables=req.variables)
        await self._send_event({"event": "agent_transfer", "agent_id": loaded.agent_id, "name": loaded.name})
        await self.agent.start(resumed=True, greet=not req.history)
        return True

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        await self._record_mobile()                        # the IVR calling number, if any
        if self.inactivity_s > 0 or self.max_call_s > 0:
            self._watch = asyncio.create_task(self._watch_limits())
        await self.agent.start()

    async def _watch_limits(self) -> None:
        """The agent's call limits: ask "are you still there?" after `voice_inactivity_s` of caller silence (once per
        silence), and end the call with a closing line after `call_max_minutes`."""
        started, pinged = time.monotonic(), False
        try:
            while not self._stopped and not self.session.ended and not self.session.handoff:
                await asyncio.sleep(0.5)
                now = time.monotonic()
                busy = self.player.active or self.turns.in_speech or (self._agent_task and not self._agent_task.done())
                if busy:
                    self._last_activity, pinged = now, False
                if self.max_call_s and now - started >= self.max_call_s:
                    self.ev.emit(EventType.POLICY_BLOCK, reason="max_call_duration", minutes=self.max_call_s / 60)
                    # time is up even mid-answer: stop what the agent is saying / preparing, then say goodbye
                    if self.player.active:
                        self.agent.on_interrupted(await self.player.interrupt())
                    if self._agent_task and not self._agent_task.done():
                        self._agent_task.cancel()
                        await asyncio.gather(self._agent_task, return_exceptions=True)
                    await self._say_line("CALL_TIME_LIMIT")
                    self._forced_reason = "max_duration"
                    await self._send_event({"event": "hangup"})
                    if self.close_transport:
                        await self.close_transport()
                    return
                if self.inactivity_s and not busy and not pinged and now - self._last_activity >= self.inactivity_s:
                    pinged = True
                    self.ev.emit(EventType.POLICY_BLOCK, reason="caller_inactive", seconds=self.inactivity_s)
                    await self._say_line("STILL_THERE")
                    self._last_activity = time.monotonic()
        except asyncio.CancelledError:
            pass

    async def _say_line(self, name: str) -> None:
        """One of the agent's fixed lines, in the call's language, also kept in the conversation history."""
        lang = self.session.language.language
        text = getattr(self.agent.ph, name)[lang]
        await self.agent._say(text)
        self.session.history.append({"role": "assistant", "content": text})
        while self.player.active and not self._stopped:       # let it finish before anything else happens
            await asyncio.sleep(0.1)

    async def _record_mobile(self) -> None:
        """Keep the call row's mobile current: the number the caller gave (looked up in the HIS) wins over the
        calling number. Only the call row sees it; events and logs stay masked."""
        s = self.session
        mobile = s.auth.mobile_no or (normalize_mobile(s.ani) if s.ani else None)
        store = getattr(self.rt, "store", None)          # no store: text chat, tests
        if not mobile or mobile == getattr(self, "_mobile_recorded", None) or store is None:
            return
        self._mobile_recorded = mobile
        try:
            await store.set_mobile(s.call_id, mobile)
        except Exception as e:
            self.ev.emit(EventType.ERROR, level=Level.WARNING, during="record_mobile", error=repr(e)[:120])

    async def stop(self, reason: str = "caller_hangup") -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._listeners:
            self._listeners.clear()
        self.end_reason = self._forced_reason or ("transferred" if self.session.handoff else
                                                  "agent_ended" if self.session.ended else reason)
        # everything still running for this call — incl. the early STT started at a pause and the barge-in check,
        # which otherwise kept sending audio to the STT provider after the caller hung up
        for t in [self._agent_task, self._speculative, self._barge_check, getattr(self, "_watch", None), getattr(self, "_dtmf_timer", None),
                  *self._stt_tasks]:
            if t and not t.done():
                t.cancel()
        await self.player.close()
        if self.aec:
            await self.aec.close()
        if not self.session.ended:
            self.ev.emit(EventType.CALL_END, reason=reason, turns=self.session.turn_id,
                         verified=self.session.auth.verified, handoff=self.session.handoff)

    # ---------------- listening in (console, listen-only) ----------------

    MAX_LISTENERS = 3

    def add_listener(self) -> asyncio.Queue | None:
        """A queue of (source, sample_rate, pcm16) — source 0 = the caller, 1 = the agent — for someone listening in from the
        console. None when too many already listen or the call is over. The call never waits for a listener: when its queue is
        full, audio is dropped for that listener only."""
        if self._stopped or len(self._listeners) >= self.MAX_LISTENERS:
            return None
        q: asyncio.Queue = asyncio.Queue(maxsize=300)
        self._listeners.add(q)
        return q

    def remove_listener(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def _tap(self, source: int, pcm: bytes, rate: int) -> None:
        for q in self._listeners:
            try:
                q.put_nowait((source, rate, pcm))
            except asyncio.QueueFull:
                pass

    def add_supervisor_note(self, text: str) -> None:
        """A live instruction from the console: the agent's next reply follows it (kept for the rest of the call)."""
        text = " ".join(text.split())[:500]
        if text and not self._stopped:
            self.session.supervisor_notes.append(text)
            self.ev.emit(EventType.SLOT_SET, field="supervisor_instruction", value=text)

    async def end_from_console(self) -> None:
        """An operator pressed "End call" in the console: tell the client the call is over and close it."""
        if self._stopped:
            return
        self._forced_reason = "ended_from_console"
        self.ev.emit(EventType.POLICY_BLOCK, reason="ended_from_console")
        if self.player.active:
            await self.player.interrupt()
        await self._send_event({"event": "hangup"})       # playground: "call ended"; IVR: closes the call
        if self.close_transport:
            await self.close_transport()                   # don't rely on the client to hang up

    # ---------------- audio in ----------------

    async def _send_and_reference(self, data: bytes) -> None:
        """Send agent audio and give the echo canceller the same audio as its far-end reference."""
        await self._raw_send_audio(data)
        if self.aec or self._listeners:
            pcm = mulaw_to_pcm16(data) if self._out_fmt.encoding == "mulaw" else data
            if self._listeners:
                self._tap(1, pcm, self._out_fmt.sample_rate)
            if self.aec:
                self.aec.feed_farend(resample_pcm16(pcm, self._out_fmt.sample_rate, self.in_fmt.sample_rate))

    def _make_resampler(self, rate: int):
        if rate == self.vad_rate:
            return None
        from runtime.providers.audio import StreamConverter
        return StreamConverter(rate, "pcm16", self.vad_rate)   # stateful: no clicks at frame joins

    def set_input_rate(self, rate: int) -> None:
        """IVR 'init' message: the caller audio arrives at a different rate than configured."""
        if rate == self.in_fmt.sample_rate:
            return
        self.in_fmt = AudioFormat(self.in_fmt.encoding, rate)
        self.vad_rate = vad_rate_for(rate)
        self._in_resampler = self._make_resampler(rate)
        self.turns = TurnDetector(self.turns.cfg, sample_rate=self.vad_rate)
        if self.aec:
            from .aec import EchoCanceller
            old, self.aec = self.aec, EchoCanceller(rate)
            asyncio.create_task(old.close())

    def set_branch(self, projects: list) -> None:
        """Extension call: the caller reached a branch directly — use it as the hospital context."""
        self.session.memory["branch_projects"] = [p.reference_id for p in projects]
        self.session.memory.setdefault("offered_projects", set()).update(p.reference_id for p in projects)
        if len(projects) == 1:
            self.session.slots["branch"] = projects[0].project_name
        self.ev.emit(EventType.SLOT_SET, field="branch", value=[p.project_name for p in projects])

    async def on_audio(self, data: bytes) -> None:
        if self._stopped or self.session.handoff or self.session.ended:
            return
        pcm = mulaw_to_pcm16(data) if self.in_fmt.encoding == "mulaw" else data
        if self._listeners:
            self._tap(0, pcm, self.in_fmt.sample_rate)             # what the caller said, before echo cancellation
        if self.aec:
            pcm = self.aec.process_nearend(pcm)
        if self._in_resampler:
            pcm = self._in_resampler.feed(pcm)
        events = await asyncio.get_running_loop().run_in_executor(VAD_POOL, self.turns.feed, pcm)
        for event, payload in events:
            if event == "start":
                self._barged, self._speech_ms, self._next_barge_ms = False, 0, self.barge_in_ms
                self._utt_ms = self._utt_overlap_ms = 0.0
                self.ev.emit(EventType.VAD_SPEECH_START, agent_speaking=self.player.active)
            elif event == "pause":
                self._cancel_speculative()
                self._speculative = asyncio.create_task(self._transcribe(payload))
            elif event == "resume":
                self._cancel_speculative()
            elif event == "end":
                self.ev.emit(EventType.VAD_SPEECH_STOP, audio_ms=round(len(payload) / 2 / self.vad_rate * 1000),
                             speculative=self._speculative is not None)
                spec, self._speculative = self._speculative, None
                # Mostly over the agent's voice → probably its echo. (Continuous background noise can hold an
                # utterance open for 20–30 s: flagging the whole thing because it *started* during the agent's
                # voice dropped the caller's real answers.)
                overlap = self._utt_ms > 0 and self._utt_overlap_ms / self._utt_ms >= 0.5
                self._spawn_stt(payload, time.perf_counter(), spec, overlap=overlap)
            elif event == "noise":
                self.ev.emit(EventType.VAD_SPEECH_STOP, level=Level.DEBUG, noise=True)
        if self.turns.in_speech:
            ms = len(pcm) / 2 / self.vad_rate * 1000
            self._speech_ms += ms
            self._utt_ms += ms
            if self.player.talking_or_just_talked():
                self._utt_overlap_ms += ms
            if not self._barged and self._barge_check is None and self._speech_ms >= self._next_barge_ms \
                    and self.player.active and self._barge_in_allowed():
                if self.barge_in_confirm:
                    self._barge_check = asyncio.create_task(self._confirm_barge_in(self.turns.current_pcm()))
                else:
                    self._barged = True
                    await self._barge_in()

    async def on_dtmf(self, digit: str) -> None:
        """Keypad entry. A flow that uses the keypad (a node collecting digits, transitions or global triggers on
        keys) gets the keys; otherwise it is the reliable fallback for mobile numbers and codes on noisy / 8 kHz
        lines: '#' ends an entry; a mobile number is also accepted automatically after 10 digits; '*' clears."""
        plan_of = getattr(self.agent, "dtmf_plan", None)
        plan = plan_of() if plan_of else None
        if plan and (plan["capture"] or plan["keys"]):
            await self._flow_dtmf(digit, plan)
            return
        if digit == "*":
            self._dtmf = ""
            return
        if digit != "#":
            self._dtmf += digit
        stage = self.session.auth.stage
        complete = digit == "#" or (stage == "awaiting_mobile" and len(self._dtmf) == 10)
        if not complete or not self._dtmf:
            return
        digits, self._dtmf = self._dtmf, ""
        self.ev.emit(EventType.SLOT_SET, field="dtmf_entry", value=len(digits))
        await self._dtmf_turn(digits)

    async def _dtmf_turn(self, text: str, key: str | None = None) -> None:
        """The keypad said something: stop whatever the agent is saying / preparing and answer it as a turn. `key`: the
        key that was pressed, for `dtmf` transitions (it counts for this turn only)."""
        if self.player.active:
            heard = await self.player.interrupt()
            self.agent.on_interrupted(heard)
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
            await asyncio.gather(self._agent_task, return_exceptions=True)
        shown = key if key is not None else text
        await self._send_event({"event": "transcript", "role": "user", "text": f"⌨ {shown}"})
        if key is not None:
            self.session.slots["_dtmf"] = key
        self.player.turn_started_at = time.perf_counter()
        self._agent_task = asyncio.create_task(self._run_agent(text, self.session.language.language))

    async def _flow_dtmf(self, digit: str, plan: dict) -> None:
        """Keys for a flow: digits go to the node's capture (ended by an end key, the maximum length or a pause);
        any key a transition of this node, or a global one, listens for moves the flow on."""
        cap = plan["capture"]
        if cap:
            if digit in (cap.get("end_keys") or ["#"]):
                await self._dtmf_capture_done(cap)
                return
            if digit.isdigit():
                self._dtmf += digit
                if len(self._dtmf) >= int(cap.get("max_digits") or 10):
                    await self._dtmf_capture_done(cap)
                else:
                    self._arm_dtmf_timer(cap)
                return
        if digit in plan["keys"]:
            self._cancel_dtmf_timer()
            self._dtmf = ""
            self.ev.emit(EventType.SLOT_SET, field="dtmf_key", value=digit, source="keypad")
            await self._dtmf_turn(f"[the caller pressed {digit} on the keypad]", key=digit)

    def _cancel_dtmf_timer(self) -> None:
        t = self._dtmf_timer
        if t and t is not asyncio.current_task() and not t.done():
            t.cancel()
        self._dtmf_timer = None

    def _arm_dtmf_timer(self, cap: dict) -> None:
        self._cancel_dtmf_timer()
        self._dtmf_timer = asyncio.create_task(self._dtmf_timeout(cap))

    async def _dtmf_timeout(self, cap: dict) -> None:
        try:
            await asyncio.sleep(float(cap.get("timeout_s") or 5))
        except asyncio.CancelledError:
            return
        if self._dtmf and not self._stopped:
            await self._dtmf_capture_done(cap)

    async def _dtmf_capture_done(self, cap: dict) -> None:
        """The caller finished keying digits: store them in the node's variable and let the flow carry on."""
        self._cancel_dtmf_timer()
        digits, self._dtmf = self._dtmf, ""
        if not digits:
            return
        name = str(cap.get("variable") or "dtmf_input")
        self.session.slots[name] = digits
        self.ev.emit(EventType.SLOT_SET, field=name, value=f"{len(digits)} digits", source="keypad")
        await self._dtmf_turn(digits)

    # ---------------- turn handling ----------------

    def _barge_in_allowed(self) -> bool:
        """Telephony echo guards: no barge-in right after the agent starts talking (echo onset), and not when
        the echo canceller says the 'speech' is mostly our own voice coming back."""
        if not getattr(self, "interrupt", True):     # this agent always finishes what it says
            return False
        since = self.player.speaking_since
        if self.barge_in_grace_ms and since and (time.perf_counter() - since) * 1000 < self.barge_in_grace_ms:
            return False
        if self.aec and self.aec.suppression_ratio() < self.min_suppression_ratio:
            self.ev.emit(EventType.POLICY_BLOCK, level=Level.DEBUG, reason="barge_in_echo",
                         ratio=round(self.aec.suppression_ratio(), 2))
            return False
        return True

    async def _confirm_barge_in(self, pcm: bytes) -> None:
        """Interrupt only for real words from the caller — not background chatter, line noise or the agent's own
        voice coming back through the caller's speaker (live test: ~20 false interruptions in one call)."""
        try:
            if self.levels and self.levels.too_quiet(speech_level_db(pcm)):
                self.ev.emit(EventType.POLICY_BLOCK, reason="barge_in_rejected", why="quiet_background")
                self._next_barge_ms = self._speech_ms + 600
                return
            try:
                tr = await self._transcribe(pcm)
                text, raw = tr.text.strip(), tr.raw
            except Exception:
                text, raw = "", {}
            reason = noise_reason(text, raw, self._prompt(), overlap=True) or ("echo" if self._is_echo(text) else None)
            if reason is None and self.player.active and not self._barged:
                self._barged = True
                await self._barge_in()
            elif reason:
                self.ev.emit(EventType.POLICY_BLOCK, reason="barge_in_rejected", why=reason, text=text[:80])
                self._next_barge_ms = self._speech_ms + 600      # check again if they keep talking
        finally:
            self._barge_check = None

    def _prompt(self) -> str:
        s = self.session
        return self.stt_hint.get(s.language.language if s.language.decided else "ar", "")

    def _is_echo(self, text: str) -> bool:
        from rapidfuzz import fuzz
        from runtime.text.arabic import normalize
        said = self.player.recent_text()
        heard = normalize(text)
        return bool(said and len(heard) >= 3 and fuzz.partial_ratio(heard, normalize(said)) >= 80)

    async def _barge_in(self) -> None:
        heard = await self.player.interrupt()
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
            try:
                await self._agent_task
            except (asyncio.CancelledError, Exception):
                pass
        self.agent.on_interrupted(heard)
        self.ev.emit(EventType.INTERRUPT, heard=heard)

    def _cancel_speculative(self) -> None:
        if self._speculative and not self._speculative.done():
            self._speculative.cancel()
        self._speculative = None

    def _spawn_stt(self, pcm: bytes, stopped_at: float, speculative: asyncio.Task | None = None,
                   overlap: bool = False) -> None:
        task = asyncio.create_task(self._process(pcm, stopped_at, speculative, overlap))
        self._stt_tasks.add(task)
        task.add_done_callback(self._stt_tasks.discard)

    async def _transcribe(self, pcm: bytes):
        s = self.session
        lang = s.language.language if s.language.decided else None
        audio = AudioInput(pcm, self.vad_rate)
        model = {"model": self.stt_model} if getattr(self, "stt_model", None) else {}
        tr = await self.providers.stt.transcribe(audio, language=lang, prompt=self.stt_hint.get(lang or "ar"), **model)
        if lang is None and tr.language not in ("ar", "en"):
            retry = await self.providers.stt.transcribe(audio, language=s.language.language,
                                                        prompt=self.stt_hint.get(s.language.language), **model)
            retry.raw["first_guess"] = {"language": tr.language, "text": tr.text[:80]}
            tr = retry
        return tr

    async def _process(self, pcm: bytes, stopped_at: float, speculative: asyncio.Task | None = None,
                       overlap: bool = False) -> None:
        t0 = time.perf_counter()
        tr = None
        if speculative is not None:          # same speech, only trailing silence differs → reuse
            try:
                tr = await speculative
            except (asyncio.CancelledError, Exception):
                tr = None
        try:
            tr = tr or await self._transcribe(pcm)
        except Exception as e:
            self.ev.emit(EventType.ERROR, level=Level.ERROR, during="stt", error=repr(e))
            return
        text = tr.text.strip()
        level = speech_level_db(pcm)
        dropped = noise_reason(text, tr.raw, self._prompt(), overlap) or \
            ("quiet_background" if self.levels and self.levels.too_quiet(level) else None)
        no_speech, logprob = speech_stats(tr.raw)
        self.ev.emit(EventType.STT_RESULT, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                     text=text, language=tr.language, audio_ms=round(tr.duration_ms or 0),
                     speculative=speculative is not None, no_speech_prob=no_speech, avg_logprob=logprob,
                     level_db=level, caller_db=self.levels.caller_db if self.levels else None, overlap=overlap,
                     dropped=dropped, first_guess=tr.raw.get("first_guess"))
        if dropped:
            return
        if self.levels:
            self.levels.accept(level)
        if self.player.active and not self._barged:   # a short real answer that ended before barge-in confirmed
            self._barged = True
            await self._barge_in()
        await self._send_event({"event": "transcript", "role": "user", "text": text})
        if self._agent_task and not self._agent_task.done():     # caller spoke again: newest wins
            self._agent_task.cancel()
            await asyncio.gather(self._agent_task, return_exceptions=True)
        self.player.turn_started_at = stopped_at
        self._agent_task = asyncio.create_task(self._run_agent(text, tr.language))

    async def _run_agent(self, text: str, language: str | None) -> None:
        try:
            await self.agent.handle(text, language)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.ev.emit(EventType.ERROR, level=Level.ERROR, during="agent", error=repr(e))
        await self._record_mobile()


__all__ = ["AudioFormat", "MS_PER_WINDOW", "VoiceCall"]
