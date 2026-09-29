"""Turn detection on 16 kHz PCM16: speech start (for barge-in) and end of utterance (for STT).

    det = TurnDetector()
    for event, payload in det.feed(pcm_bytes):
        if event == "start": ...            # caller started speaking
        if event == "end": stt(payload)     # full utterance (with pre-roll), caller stopped speaking
"""

from collections import deque
from dataclasses import dataclass

import numpy as np

from .vad import SAMPLE_RATE, WINDOW, SileroVAD

MS_PER_WINDOW = WINDOW * 1000 // SAMPLE_RATE   # 32 (same at 8 kHz: 256 samples)


@dataclass
class TurnConfig:
    start_threshold: float = 0.5      # speech probability to count a window as speech
    end_threshold: float = 0.35       # below this counts as silence (hysteresis)
    min_speech_ms: int = 160          # speech needed before "start" fires (ignores clicks / coughs)
    end_silence_ms: int = 550         # silence that ends the caller's turn
    pre_roll_ms: int = 320            # audio kept from before the start (don't clip the first syllable)
    max_utterance_ms: int = 30_000    # force an end for very long speech
    min_utterance_ms: int = 250       # shorter "utterances" are dropped as noise
    pause_ms: int = 250               # silence after which a speculative transcription starts


class TurnDetector:
    def __init__(self, config: TurnConfig | None = None, vad: SileroVAD | None = None,
                 sample_rate: int = SAMPLE_RATE) -> None:
        self.cfg = config or TurnConfig()
        self.vad = vad or SileroVAD(sample_rate=sample_rate)
        self.rate = sample_rate
        self.window = getattr(self.vad, "window", WINDOW * sample_rate // SAMPLE_RATE)
        self._buf = np.zeros(0, dtype=np.float32)
        self._pre = deque(maxlen=max(1, self.cfg.pre_roll_ms // MS_PER_WINDOW))
        self._utt: list[np.ndarray] = []
        self.in_speech = False
        self._speech_ms = 0
        self._silence_ms = 0
        self.last_prob = 0.0

    def reset(self) -> None:
        self.vad.reset()
        self._buf = np.zeros(0, dtype=np.float32)
        self._pre.clear()
        self._utt, self.in_speech, self._speech_ms, self._silence_ms = [], False, 0, 0

    def feed(self, pcm16: bytes) -> list[tuple[str, bytes | None]]:
        events: list[tuple[str, bytes | None]] = []
        self._buf = np.concatenate([self._buf, np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0])
        while len(self._buf) >= self.window:
            window, self._buf = self._buf[:self.window], self._buf[self.window:]
            p = self.last_prob = self.vad(window)
            events += self._step(window, p)
        return events

    def _step(self, window: np.ndarray, p: float) -> list[tuple[str, bytes | None]]:
        c = self.cfg
        if not self.in_speech:
            self._pre.append(window)
            self._speech_ms = self._speech_ms + MS_PER_WINDOW if p >= c.start_threshold else 0
            if self._speech_ms >= c.min_speech_ms:
                self.in_speech, self._silence_ms = True, 0
                self._utt = list(self._pre)
                self._pre.clear()
                return [("start", None)]
            return []
        self._utt.append(window)
        events: list[tuple[str, bytes | None]] = []
        if p < c.end_threshold:
            before = self._silence_ms
            self._silence_ms += MS_PER_WINDOW
            crossed_pause = before < c.pause_ms <= self._silence_ms
            if crossed_pause and len(self._utt) * MS_PER_WINDOW - self._silence_ms >= c.min_utterance_ms:
                events.append(("pause", self._pcm(self._utt)))
        else:
            if self._silence_ms >= c.pause_ms:
                events.append(("resume", None))
            self._silence_ms = 0
        duration = len(self._utt) * MS_PER_WINDOW
        if self._silence_ms >= c.end_silence_ms or duration >= c.max_utterance_ms:
            audio = np.concatenate(self._utt)
            speech_ms = duration - self._silence_ms
            self._utt, self.in_speech, self._speech_ms, self._silence_ms = [], False, 0, 0
            if speech_ms < c.min_utterance_ms:
                return events + [("noise", None)]
            pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
            return events + [("end", pcm)]
        return events

    def current_pcm(self) -> bytes:
        """The caller's speech so far in the utterance in progress (used to confirm a barge-in)."""
        return self._pcm(self._utt) if self._utt else b""

    @staticmethod
    def _pcm(windows: list[np.ndarray]) -> bytes:
        return (np.clip(np.concatenate(windows), -1, 1) * 32767).astype(np.int16).tobytes()
