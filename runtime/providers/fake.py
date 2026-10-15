"""Fake providers with realistic latencies — for load tests and offline development (PROVIDER_OVERRIDE=fake).

They exercise the whole server path (VAD, turns, player pacing, resampling, logging) without external calls.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from .audio import to_encoding
from .base import (AudioChunk, AudioEncoding, AudioInput, LLMDone, LLMEvent, LLMProvider, Message, STTProvider,
                   TextDelta, ToolSpec, Transcript, TTSProvider)
from .registry import register


@register("fake")
class FakeSTT(STTProvider):
    class Settings(BaseModel):
        latency_ms: int = Field(500, description="Simulated transcription time")
        text: str = "ابي احجز موعد"

    async def transcribe(self, audio: AudioInput, *, language: str | None = None,
                         prompt: str | None = None, model: str | None = None) -> Transcript:
        await asyncio.sleep(self.settings.latency_ms / 1000)
        return Transcript(self.settings.text, language or "ar", audio.duration_ms)


@register("fake")
class FakeLLM(LLMProvider):
    class Settings(BaseModel):
        first_token_ms: int = 250
        token_ms: int = 15
        reply: str = "أبشر، عشان أحجز لك أحتاج رقم جوالك المسجل لو سمحت؟"

    async def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                     **options: Any) -> AsyncIterator[LLMEvent]:
        await asyncio.sleep(self.settings.first_token_ms / 1000)
        for word in self.settings.reply.split(" "):
            yield TextDelta(word + " ")
            await asyncio.sleep(self.settings.token_ms / 1000)
        yield LLMDone("stop")


@register("fake")
class FakeTTS(TTSProvider):
    class Settings(BaseModel):
        first_audio_ms: int = 350
        speed: float = Field(4.0, description="Synthesis speed vs real time")
        ms_per_char: int = 60

    async def synthesize(self, text: str, *, language: str = "ar", voice: str | None = None,
                         encoding: AudioEncoding = "pcm16", sample_rate: int | None = None) -> AsyncIterator[AudioChunk]:
        s = self.settings
        await asyncio.sleep(s.first_audio_ms / 1000)
        total_ms = max(300, len(text) * s.ms_per_char)
        rate = 24000
        for start in range(0, total_ms, 200):            # 200 ms chunks
            n = int(rate * min(200, total_ms - start) / 1000)
            t = np.arange(n) / rate
            pcm = (np.sin(2 * np.pi * 180 * t) * 3000).astype(np.int16).tobytes()
            yield to_encoding(pcm, rate, encoding, sample_rate)
            await asyncio.sleep(0.2 / s.speed)
