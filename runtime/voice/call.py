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

    def __init__(self, rt: Runtime, *, call_id: str, in_fmt: AudioFormat, out_fmt: AudioFormat,
                 send_audio: SendAudio, send_event: SendEvent, ani: str | None = None,
                 phrases: PhraseCache | None = None, chunk_ms: int = 20, aec: bool = False,
                 barge_in_grace_ms: int = 0, min_suppression_ratio: float = 0.0,
                 agent: LoadedAgent | None = None) -> None:
        # the agent answering this call, loaded complete and frozen: a publish never changes a call in progress
        self.loaded = agent or agent_of(rt)
        s = self.loaded.settings
        self.rt, self.in_fmt, self._send_event = rt, in_fmt, send_event
        self.providers = self.loaded.providers
        self.stt_hint = self.loaded.stt_hint
        phrases = phrases if phrases is not None else self.providers.phrases
        self.session = Session(call_id=call_id, ani=ani)
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
        self.agent = Agent(self.session, self.loaded.executor, self.providers.llm, self.loaded.skills, self.player,
                           rt.bus.bind(), filler_after_s=s.voice_filler_after_s, settings=s)
        self.agent.config_version = self.loaded.version
        self.agent.agent_ref = {"agent_id": self.loaded.agent_id, "release_id": self.loaded.release_id}
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
        self._stopped = False
        self.end_reason = "in_progress"
        self._forced_reason: str | None = None
        self.close_transport = None      # set by the transport: closes the connection from the server side

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
        self.end_reason = self._forced_reason or ("transferred" if self.session.handoff else
                                                  "agent_ended" if self.session.ended else reason)
        # everything still running for this call — incl. the early STT started at a pause and the barge-in check,
        # which otherwise kept sending audio to the STT provider after the caller hung up
        for t in [self._agent_task, self._speculative, self._barge_check, getattr(self, "_watch", None), *self._stt_tasks]:
            if t and not t.done():
                t.cancel()
        await self.player.close()
        if self.aec:
            await self.aec.close()
        if not self.session.ended:
            self.ev.emit(EventType.CALL_END, reason=reason, turns=self.session.turn_id,
                         verified=self.session.auth.verified, handoff=self.session.handoff)

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
        if self.aec:
            pcm = mulaw_to_pcm16(data) if self._out_fmt.encoding == "mulaw" else data
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
        """Keypad entry — the reliable fallback for mobile numbers and codes on noisy / 8 kHz lines.
        '#' ends an entry; a mobile number is also accepted automatically after 10 digits; '*' clears."""
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
        if self.player.active:
            heard = await self.player.interrupt()
            self.agent.on_interrupted(heard)
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
            await asyncio.gather(self._agent_task, return_exceptions=True)
        await self._send_event({"event": "transcript", "role": "user", "text": f"⌨ {digits}"})
        self.player.turn_started_at = time.perf_counter()
        self._agent_task = asyncio.create_task(self._run_agent(digits, self.session.language.language))

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
        tr = await self.providers.stt.transcribe(audio, language=lang, prompt=self.stt_hint.get(lang or "ar"))
        if lang is None and tr.language not in ("ar", "en"):
            retry = await self.providers.stt.transcribe(audio, language=s.language.language,
                                                        prompt=self.stt_hint.get(s.language.language))
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
