"""Audio helpers shared by providers and the voice pipeline."""

import io
import re
import wave

import numpy as np
import soxr


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate or not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16)
    return soxr.resample(x, src_rate, dst_rate, quality="HQ").astype(np.int16).tobytes()


# G.711 μ-law (bit-exact with the ITU reference / audioop), vectorized.
_BIAS = 0x84
_SEG_UEND = np.array([0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF], dtype=np.int32)


def pcm16_to_mulaw(pcm: bytes) -> bytes:
    # 14-bit reference algorithm (same rounding as the Sun / CPython audioop encoder).
    p = np.frombuffer(pcm, dtype=np.int16).astype(np.int32) >> 2
    mask = np.where(p < 0, 0x7F, 0xFF)
    p = np.minimum(np.abs(p), 8159) + 0x21
    seg = np.searchsorted(_SEG_UEND, p, side="left")
    uval = np.where(seg >= 8, 0x7F, (seg << 4) | ((p >> (seg + 1)) & 0x0F))
    return ((uval ^ mask) & 0xFF).astype(np.uint8).tobytes()


def mulaw_to_pcm16(data: bytes) -> bytes:
    u = ~np.frombuffer(data, dtype=np.uint8).astype(np.int32) & 0xFF
    exponent = (u >> 4) & 0x07
    mag = (((u & 0x0F) << 3) + _BIAS << exponent) - _BIAS
    return np.where(u & 0x80, -mag, mag).astype(np.int16).tobytes()


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm16(data: bytes) -> tuple[bytes, int]:
    """Returns (mono PCM16 frames, sample_rate). Raises on non-16-bit or multi-channel audio."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"expected mono 16-bit wav, got {w.getnchannels()}ch/{w.getsampwidth() * 8}bit")
        return w.readframes(w.getnframes()), w.getframerate()


# Sentence / clause boundaries for Arabic and English. Arabic comma "،" and question mark "؟" included.
_BOUNDARY = re.compile(r"(?<=[.!?؟…\n])\s+|(?<=[،,;؛:])\s+")


def split_for_tts(text: str, max_chars: int) -> list[str]:
    """Split text into chunks ≤ max_chars, preferring sentence then clause then word boundaries."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    for piece in _BOUNDARY.split(text):
        piece = piece.strip()
        if not piece:
            continue
        candidate = f"{current} {piece}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # A single piece longer than the limit: fall back to word boundaries.
        while len(piece) > max_chars:
            cut = piece.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            chunks.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        current = piece
    if current:
        chunks.append(current)
    return chunks


STT_RATE = 16_000


def prepare_for_stt(audio) -> bytes:
    """WAV for upload, downsampled to 16 kHz (Whisper's native rate) to cut upload size."""
    pcm, rate = audio.pcm, audio.sample_rate
    if rate > STT_RATE:
        pcm, rate = resample_pcm16(pcm, rate, STT_RATE), STT_RATE
    return pcm16_to_wav(pcm, rate)


class StreamConverter:
    """Chunk-by-chunk PCM16 → target rate / encoding, with a stateful resampler (no clicks at chunk joins)."""

    def __init__(self, src_rate: int, encoding: str = "pcm16", dst_rate: int | None = None) -> None:
        self.encoding = encoding
        self.dst_rate = dst_rate or (8000 if encoding == "mulaw" else src_rate)
        self._rs = soxr.ResampleStream(src_rate, self.dst_rate, 1, dtype="int16", quality="HQ") \
            if self.dst_rate != src_rate else None
        self._odd = b""

    def _out(self, pcm: bytes) -> bytes:
        return pcm16_to_mulaw(pcm) if self.encoding == "mulaw" else pcm

    def feed(self, pcm: bytes) -> bytes:
        pcm = self._odd + pcm
        cut = len(pcm) - len(pcm) % 2
        pcm, self._odd = pcm[:cut], pcm[cut:]
        if not pcm:
            return b""
        if self._rs:
            pcm = self._rs.resample_chunk(np.frombuffer(pcm, dtype=np.int16)).astype(np.int16).tobytes()
        return self._out(pcm)

    def flush(self) -> bytes:
        if not self._rs:
            return b""
        tail = self._rs.resample_chunk(np.zeros(0, dtype=np.int16), last=True).astype(np.int16).tobytes()
        return self._out(tail)


class WavStreamParser:
    """Incrementally parse a streamed WAV: returns (sample_rate, pcm bytes) as data arrives."""

    def __init__(self) -> None:
        self._head = b""
        self.sample_rate: int | None = None
        self._in_data = False

    def feed(self, chunk: bytes) -> bytes:
        if self._in_data:
            return chunk
        self._head += chunk
        buf = self._head
        if len(buf) < 12:
            return b""
        pos = 12
        while pos + 8 <= len(buf):
            cid, size = buf[pos:pos + 4], int.from_bytes(buf[pos + 4:pos + 8], "little")
            if cid == b"fmt ":
                if pos + 8 + 16 > len(buf):
                    return b""
                self.sample_rate = int.from_bytes(buf[pos + 12:pos + 16], "little")
            if cid == b"data":
                self._in_data = True
                data, self._head = buf[pos + 8:], b""
                return data
            if size in (0, 0xFFFFFFFF) or pos + 8 + size > len(buf):
                return b""
            pos += 8 + size + (size % 2)
        return b""


def to_encoding(pcm: bytes, rate: int, encoding: str, sample_rate: int | None):
    """Convert provider PCM16 output to the requested encoding / rate (e.g. 8 kHz μ-law for IVR)."""
    from .base import AudioChunk

    if encoding == "mulaw":
        target = sample_rate or 8000
        return AudioChunk(pcm16_to_mulaw(resample_pcm16(pcm, rate, target)), target, "mulaw")
    if sample_rate and sample_rate != rate:
        return AudioChunk(resample_pcm16(pcm, rate, sample_rate), sample_rate, "pcm16")
    return AudioChunk(pcm, rate, "pcm16")
