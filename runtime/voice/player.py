"""Speech output for a call: text → (phrase cache | TTS) → wire-format frames, paced in real time.

    • say() only queues text, so the LLM keeps streaming while earlier sentences are synthesized / played
    • synthesis of sentence N+1 overlaps playback of sentence N
    • frames are sent in real time (small lead), so the server knows exactly what the caller has heard;
      interrupt() stops immediately, tells the transport to drop buffered audio, and returns the heard text
Implements the harness AgentOutput protocol (say / transfer / hangup).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from runtime.events import BoundEmitter, EventType, Level
from runtime.providers import TTSProvider
from runtime.providers.audio import to_encoding

from .normalize import normalize_for_tts
from .phrases import PhraseCache

log = logging.getLogger(__name__)

SendAudio = Callable[[bytes], Awaitable[None]]
SendEvent = Callable[[dict], Awaitable[None]]


@dataclass(frozen=True)
class AudioFormat:
    encoding: str = "pcm16"     # pcm16 | mulaw
    sample_rate: int = 16000

    @property
    def bytes_per_ms(self) -> float:
        return self.sample_rate / 1000 * (1 if self.encoding == "mulaw" else 2)


@dataclass
class _Clip:
    """A sentence being synthesized: audio arrives on `chunks` (None = end), playback starts on the first chunk."""
    text: str
    cached: bool
    chunks: asyncio.Queue = field(default_factory=asyncio.Queue)
    received: int = 0
    done: bool = False


class SpeechPlayer:
    def __init__(self, tts: TTSProvider, fmt: AudioFormat, send_audio: SendAudio, send_event: SendEvent,
                 emitter: BoundEmitter, phrases: PhraseCache | None = None, *, frame_ms: int = 20,
                 lead_ms: int = 120) -> None:
        self.tts, self.fmt, self._send_audio, self._send_event = tts, fmt, send_audio, send_event
        self.ev, self.phrases, self.frame_ms, self.lead_ms = emitter, phrases, frame_ms, lead_ms
        self._texts: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        self._clips: asyncio.Queue[_Clip | None] = asyncio.Queue(maxsize=3)
        self._heard: list[str] = []
        self._current: _Clip | None = None
        self._current_ms = 0.0
        self._pending = 0                      # texts queued or clips not yet fully played
        self._idle = asyncio.Event()
        self._idle.set()
        self.turn_started_at: float | None = None   # set by the call when the caller stops speaking
        self.speaking_since: float | None = None    # start of the current uninterrupted speech burst
        self.last_audio_at: float = 0.0             # when the agent's audio last played (echo tail)
        self.voices: dict[str, str] = {}        # per-language voice chosen for this call (flow "change settings" node)
        self._tasks: list[asyncio.Task] = []
        self._start_workers()

    # ---------------- AgentOutput ----------------

    def talking_or_just_talked(self, tail_s: float = 1.0) -> bool:
        """The agent is speaking, or stopped less than `tail_s` ago (its echo can still be arriving)."""
        return self.active or (time.perf_counter() - self.last_audio_at) < tail_s

    def recent_text(self) -> str:
        """What the agent is saying / just said — to recognise its own voice echoed back as 'caller speech'."""
        current = [self._current.text] if self._current else []
        return " ".join(self._heard[-2:] + current)

    async def say(self, text: str, *, language: str, interruptible: bool = True) -> None:
        self._pending += 1
        self._idle.clear()
        await self._texts.put((text, language))
        await self._send_event({"event": "transcript", "role": "agent", "text": text})

    async def transfer(self, reason: str, **options) -> None:
        await self.drained()
        await self._send_event({"event": "transfer", "reason": reason, **options})

    async def hangup(self) -> None:
        await self.drained()
        await self._send_event({"event": "hangup"})

    # ---------------- control ----------------

    @property
    def active(self) -> bool:
        return not self._idle.is_set()

    async def drained(self, timeout: float = 30) -> None:
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def interrupt(self) -> str:
        """Barge-in: stop now, flush the transport's buffer, return what the caller actually heard."""
        heard = list(self._heard)
        if self._current and self._current.received:
            played = min(1.0, self._current_ms * self.fmt.bytes_per_ms / self._current.received)
            if not self._current.done:      # still streaming: total is longer than what arrived
                played *= 0.8
            words = self._current.text.split()
            if played > 0.15:
                heard.append(" ".join(words[: max(1, int(len(words) * played))]))
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._texts, self._clips = asyncio.Queue(), asyncio.Queue(maxsize=3)
        self._current, self._current_ms, self._pending = None, 0.0, 0
        self.speaking_since = None
        self._heard.clear()
        self._idle.set()
        await self._send_event({"event": "clear"})
        self._start_workers()
        return " ".join(heard)

    def take_heard(self) -> str:
        heard, self._heard = " ".join(self._heard), []
        return heard

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---------------- workers ----------------

    def _start_workers(self) -> None:
        self._tasks = [asyncio.create_task(self._synth_loop(), name="tts-synth"),
                       asyncio.create_task(self._play_loop(), name="tts-play")]

    async def _synth_loop(self) -> None:
        """Synthesize sentences in order; sentence N+1 starts as soon as N's audio has fully arrived,
        overlapping N's playback."""
        while True:
            text, language = await self._texts.get()
            clip = _Clip(text, cached=False)
            t0 = time.perf_counter()
            if self.phrases and not self.voices.get(language) and (hit := self.phrases.get(language, text)):
                pcm, rate = hit
                clip.cached = True
                await self._clips.put(clip)
                await self._push(clip, to_encoding(pcm, rate, self.fmt.encoding, self.fmt.sample_rate).data)
            else:
                await self._clips.put(clip)
                try:
                    async for chunk in self.tts.synthesize(normalize_for_tts(text, language), language=language,
                                                           voice=self.voices.get(language) or None,
                                                           encoding=self.fmt.encoding,
                                                           sample_rate=self.fmt.sample_rate):
                        await self._push(clip, chunk.data)
                except Exception as e:
                    self.ev.emit(EventType.ERROR, level=Level.ERROR, during="tts", error=repr(e))
            clip.done = True
            await clip.chunks.put(None)
            self.ev.emit(EventType.TTS_END, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                         chars=len(text), cached=clip.cached)

    async def _push(self, clip: _Clip, data: bytes) -> None:
        if data:
            clip.received += len(data)
            await clip.chunks.put(data)

    async def _play_loop(self) -> None:
        frame_bytes = int(self.fmt.bytes_per_ms * self.frame_ms)
        if self.fmt.encoding == "pcm16":
            frame_bytes -= frame_bytes % 2
        while True:
            clip = await self._clips.get()
            self._current, self._current_ms = clip, 0.0
            start: float | None = None
            sent = 0
            pending = b""
            while True:
                data = await clip.chunks.get()
                if data is None:
                    break
                if start is None:
                    start = time.perf_counter()
                    if self.speaking_since is None:
                        self.speaking_since = start
                    if self.turn_started_at is not None:
                        ms = round((start - self.turn_started_at) * 1000, 1)
                        self.ev.emit(EventType.TTS_FIRST_BYTE, latency_ms=ms, cached=clip.cached,
                                     metric="turn_end_to_first_audio")
                        await self._send_event({"event": "metric", "name": "turn_end_to_first_audio", "ms": ms})
                        self.turn_started_at = None
                pending += data
                while len(pending) >= frame_bytes:
                    frame, pending = pending[:frame_bytes], pending[frame_bytes:]
                    await self._pace(start, sent)
                    await self._send_audio(frame)
                    sent += 1
                    self._current_ms += self.frame_ms
            if pending and start is not None:
                await self._pace(start, sent)
                await self._send_audio(pending)
                sent += 1
            if start is not None:
                # let the lead drain so "heard" means heard
                if (tail := start + sent * self.frame_ms / 1000 - time.perf_counter()) > 0:
                    await asyncio.sleep(tail)
                self._heard.append(clip.text)
                self.last_audio_at = time.perf_counter()
            self._current = None
            self._pending = max(0, self._pending - 1)
            if self._pending == 0 and self._texts.empty() and self._clips.empty():
                self._idle.set()
                self.speaking_since = None

    async def _pace(self, start: float, frame_index: int) -> None:
        due = start + frame_index * self.frame_ms / 1000 - self.lead_ms / 1000
        if (delay := due - time.perf_counter()) > 0:
            await asyncio.sleep(delay)
