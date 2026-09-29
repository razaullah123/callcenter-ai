"""Acoustic echo cancellation for telephony (WebRTC AEC3 via LiveKit's AudioProcessingModule, used as a
local DSP library — no LiveKit room or network).

On phone lines the caller's handset / speakerphone feeds our own speech back into the uplink; without
AEC the VAD hears the agent and "barges in" on itself.

    aec = EchoCanceller(8000)
    aec.feed_farend(pcm_we_send)          # every chunk sent to the caller
    clean = aec.process_nearend(pcm_in)   # every chunk received, before VAD / STT
    aec.suppression_ratio()               # ≈1.0 real caller speech · ≪1.0 mostly echo

The far-end reference is drained into the APM at a strict 10 ms cadence (silence when idle) rather than in
bursts when chunks are sent: AEC3's delay estimator assumes near- and far-end frames arrive in real-time
lockstep, and bursty references can make it suppress genuine caller speech.
"""

import asyncio
import logging
import time
from collections import deque

import numpy as np

log = logging.getLogger(__name__)

FRAME_MS = 10


class EchoCanceller:
    def __init__(self, sample_rate: int = 8000, window_ms: int = 300) -> None:
        from livekit import rtc  # optional dependency, only for telephony
        self._rtc = rtc
        self.rate = sample_rate
        self.frame_samples = sample_rate * FRAME_MS // 1000
        self.frame_bytes = self.frame_samples * 2
        self._apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=False,
                                              high_pass_filter=False, auto_gain_control=False)
        self._near = bytearray()
        self._far: asyncio.Queue[bytes] = asyncio.Queue()
        self._silence = bytes(self.frame_bytes)
        self._energy: deque[tuple[float, float]] = deque(maxlen=max(1, window_ms // FRAME_MS))
        self._pump: asyncio.Task | None = None

    def _frame(self, data: bytes):
        return self._rtc.AudioFrame(bytearray(data), sample_rate=self.rate, num_channels=1,
                                    samples_per_channel=self.frame_samples)

    def feed_farend(self, pcm: bytes) -> None:
        if not pcm:
            return
        if len(pcm) % self.frame_bytes:
            pcm += bytes(self.frame_bytes - len(pcm) % self.frame_bytes)
        for i in range(0, len(pcm), self.frame_bytes):
            self._far.put_nowait(pcm[i:i + self.frame_bytes])
        if self._pump is None:
            self._pump = asyncio.create_task(self._pump_far(), name="aec-far")

    async def _pump_far(self) -> None:
        tick = time.monotonic()
        while True:
            try:
                data = self._far.get_nowait()
            except asyncio.QueueEmpty:
                data = self._silence
            try:
                self._apm.process_reverse_stream(self._frame(data))
            except Exception as e:  # reference is best-effort
                log.debug("aec reverse stream failed: %r", e)
            tick += FRAME_MS / 1000
            await asyncio.sleep(max(0.0, tick - time.monotonic()))

    def process_nearend(self, pcm: bytes) -> bytes:
        self._near.extend(pcm)
        out = bytearray()
        n = len(self._near) // self.frame_bytes
        for i in range(n):
            raw = bytes(self._near[i * self.frame_bytes:(i + 1) * self.frame_bytes])
            frame = self._frame(raw)
            try:
                self._apm.process_stream(frame)
                clean = bytes(frame.data)
            except Exception as e:
                log.debug("aec process_stream failed, passing raw: %r", e)
                clean = raw
            out.extend(clean)
            self._energy.append((_energy(raw), _energy(clean)))
        del self._near[:n * self.frame_bytes]
        return bytes(out)

    def suppression_ratio(self) -> float:
        """RMS(after)/RMS(before) over the last ~300 ms; 1.0 when there's no data (never blocks barge-in)."""
        before = sum(b for b, _ in self._energy)
        return 1.0 if before < 1e-9 else float(np.sqrt(sum(a for _, a in self._energy) / before))

    async def close(self) -> None:
        if self._pump:
            self._pump.cancel()
            await asyncio.gather(self._pump, return_exceptions=True)


def _energy(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.mean(x * x)) if len(x) else 0.0
