"""OpenAI-compatible providers: any server exposing /v1/chat/completions, /v1/audio/transcriptions
or /v1/audio/speech (OpenAI, Azure OpenAI, vLLM, Together, Fireworks, local servers, …).
"""

from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, SecretStr

from .audio import prepare_for_stt, split_for_tts, to_encoding, wav_to_pcm16
from .base import (
    AudioChunk,
    AudioEncoding,
    AudioInput,
    EmbeddingProvider,
    LLMEvent,
    LLMProvider,
    Message,
    STTProvider,
    ToolSpec,
    Transcript,
    TTSProvider,
)
from .chat_stream import parse_chat_stream
from .registry import register

_clients: dict[tuple[str, str], AsyncOpenAI] = {}


class _CompatBase(BaseModel):
    base_url: str = Field(..., description="e.g. https://api.openai.com/v1 or http://localhost:8000/v1")
    api_key: SecretStr = Field(SecretStr("none"), description="API key (use 'none' for unauthenticated servers)")
    timeout_s: float = 10.0

    def client(self) -> AsyncOpenAI:
        key = (self.base_url, self.api_key.get_secret_value())
        if key not in _clients:
            _clients[key] = AsyncOpenAI(base_url=self.base_url, api_key=key[1], timeout=self.timeout_s, max_retries=1)
        return _clients[key]


_LANG_NAMES = {"arabic": "ar", "english": "en"}


@register("openai_compatible")
class OpenAICompatSTT(STTProvider):
    class Settings(_CompatBase):
        model: str = "whisper-1"
        default_prompt: str = ""

    settings: "OpenAICompatSTT.Settings"

    async def transcribe(self, audio: AudioInput, *, language: str | None = None,
                         prompt: str | None = None, model: str | None = None) -> Transcript:
        s = self.settings
        kwargs: dict[str, Any] = {}
        if language:
            kwargs["language"] = language
        if hint := (prompt if prompt is not None else s.default_prompt):
            kwargs["prompt"] = hint
        result = await s.client().audio.transcriptions.create(
            file=("audio.wav", prepare_for_stt(audio)),
            model=model or s.model, response_format="verbose_json", **kwargs)
        raw = result.model_dump()
        lang = raw.get("language")
        lang = _LANG_NAMES.get(str(lang).lower(), lang) if lang else language
        return Transcript(text=(result.text or "").strip(), language=lang, duration_ms=audio.duration_ms, raw=raw)


@register("openai_compatible")
class OpenAICompatLLM(LLMProvider):
    class Settings(_CompatBase):
        model: str = Field(..., description="Model id on the server")
        temperature: float = Field(0.3, ge=0, le=2)
        max_tokens: int = 400
        parallel_tool_calls: bool = False

    settings: "OpenAICompatLLM.Settings"

    async def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                     **options: Any) -> AsyncIterator[LLMEvent]:
        s = self.settings
        params: dict[str, Any] = {
            "model": s.model, "messages": messages, "temperature": s.temperature,
            "max_tokens": s.max_tokens, "stream": True, "stream_options": {"include_usage": True},
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = options.pop("tool_choice", "auto")
            params["parallel_tool_calls"] = s.parallel_tool_calls
        params.update(options)
        stream = await s.client().chat.completions.create(**params)
        async for event in parse_chat_stream(stream):
            yield event


@register("openai_compatible")
class OpenAICompatTTS(TTSProvider):
    class Settings(_CompatBase):
        model: str = "tts-1"
        voice_ar: str = "alloy"
        voice_en: str = "alloy"
        max_chars: int = 400

    settings: "OpenAICompatTTS.Settings"

    async def synthesize(self, text: str, *, language: str = "ar", voice: str | None = None,
                         encoding: AudioEncoding = "pcm16",
                         sample_rate: int | None = None) -> AsyncIterator[AudioChunk]:
        s = self.settings
        voice = voice or (s.voice_en if language.startswith("en") else s.voice_ar)
        for piece in split_for_tts(text, s.max_chars):
            resp = await s.client().audio.speech.create(model=s.model, voice=voice, input=piece,
                                                        response_format="wav")
            pcm, rate = wav_to_pcm16(await resp.aread())
            yield to_encoding(pcm, rate, encoding, sample_rate)


@register("openai_compatible")
class OpenAICompatEmbedding(EmbeddingProvider):
    class Settings(_CompatBase):
        model: str = Field(..., description="Must match the model used to build the stored embeddings")
        dimensions: int | None = None

    settings: "OpenAICompatEmbedding.Settings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        s = self.settings
        kwargs = {"dimensions": s.dimensions} if s.dimensions else {}
        result = await s.client().embeddings.create(model=s.model, input=texts, **kwargs)
        return [d.embedding for d in sorted(result.data, key=lambda d: d.index)]
