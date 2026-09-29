"""Groq implementations of STT (Whisper), LLM (chat completions) and TTS (Orpheus).

One AsyncGroq client (HTTP connection pool) is shared per API key, so every call reuses
warm connections — important for latency.
"""

from collections.abc import AsyncIterator
from typing import Any, Literal

from groq import AsyncGroq
from pydantic import BaseModel, Field, SecretStr

from .audio import StreamConverter, WavStreamParser, prepare_for_stt, split_for_tts, to_encoding, wav_to_pcm16
from .chat_stream import parse_chat_stream
from .base import (
    AudioChunk,
    AudioEncoding,
    AudioInput,
    LLMEvent,
    LLMProvider,
    Message,
    STTProvider,
    ToolSpec,
    Transcript,
    TTSProvider,
)
from .registry import register

_clients: dict[str, AsyncGroq] = {}


def _env(attr: str):
    """Default factory: provider defaults come from `.env` unless the call config overrides them."""
    def factory():
        from runtime.config import get_settings
        return getattr(get_settings(), attr)
    return factory


_default_api_key = _env("groq_api_key")


def _client(api_key: SecretStr, timeout: float) -> AsyncGroq:
    key = api_key.get_secret_value()
    if key not in _clients:
        _clients[key] = AsyncGroq(api_key=key, timeout=timeout, max_retries=1)
    return _clients[key]


class _GroqBase(BaseModel):
    api_key: SecretStr = Field(default_factory=_default_api_key, description="Groq API key")
    timeout_s: float = Field(10.0, description="Request timeout (seconds)")


# Whisper's verbose_json reports the language as an English name.
_WHISPER_LANG = {"arabic": "ar", "english": "en"}


@register("groq")
class GroqSTT(STTProvider):
    class Settings(_GroqBase):
        model: str = Field(default_factory=_env("groq_stt_model"), description="whisper-large-v3 | whisper-large-v3-turbo")
        default_prompt: str = Field("", description="Vocabulary hint: hospital / clinic / doctor names")

    settings: "GroqSTT.Settings"

    async def transcribe(self, audio: AudioInput, *, language: str | None = None,
                         prompt: str | None = None) -> Transcript:
        s = self.settings
        kwargs: dict[str, Any] = {}
        if language:
            kwargs["language"] = language   # forcing skips detection; leave None to auto-detect
        hint = prompt if prompt is not None else s.default_prompt
        if hint:
            kwargs["prompt"] = hint
        result = await _client(s.api_key, s.timeout_s).audio.transcriptions.create(
            file=("audio.wav", prepare_for_stt(audio)),
            model=s.model,
            response_format="verbose_json",
            temperature=0.0,
            **kwargs,
        )
        raw = result.model_dump()
        lang = raw.get("language")
        lang = _WHISPER_LANG.get(str(lang).lower(), lang) if lang else language
        return Transcript(text=(result.text or "").strip(), language=lang,
                          duration_ms=audio.duration_ms, raw=raw)


@register("groq")
class GroqLLM(LLMProvider):
    class Settings(_GroqBase):
        model: str = Field(default_factory=_env("groq_llm_model"), description="Chat model id")
        temperature: float = Field(0.3, ge=0, le=2)
        max_tokens: int = Field(400, description="Voice replies are short; caps runaway output")
        reasoning_effort: Literal["none", "low", "medium", "high"] | None = Field(
            "low", description="For reasoning models (gpt-oss). Lower = faster first token")
        parallel_tool_calls: bool = False

    settings: "GroqLLM.Settings"

    async def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                     **options: Any) -> AsyncIterator[LLMEvent]:
        s = self.settings
        params: dict[str, Any] = {
            "model": s.model,
            "messages": messages,
            "temperature": s.temperature,
            "max_tokens": s.max_tokens,
            "stream": True,
        }
        if s.reasoning_effort and "gpt-oss" in s.model:
            params["reasoning_effort"] = s.reasoning_effort
            params["include_reasoning"] = False
        if tools:
            params["tools"] = tools
            params["tool_choice"] = options.pop("tool_choice", "auto")
            params["parallel_tool_calls"] = s.parallel_tool_calls
        params.update(options)

        stream = await _client(s.api_key, s.timeout_s).chat.completions.create(**params)
        async for event in parse_chat_stream(stream):
            yield event


@register("groq")
class GroqTTS(TTSProvider):
    class Settings(_GroqBase):
        model_ar: str = Field(default_factory=_env("groq_tts_model_ar"))
        model_en: str = Field(default_factory=_env("groq_tts_model_en"))
        voice_ar: str = Field(default_factory=_env("groq_tts_voice_ar"))
        voice_en: str = Field(default_factory=_env("groq_tts_voice_en"))
        max_chars: int = Field(200, description="Max characters per TTS request; longer text is split")
        stream: bool = Field(True, description="Stream audio as it is generated (first audio ~300 ms vs ~900 ms)")

    settings: "GroqTTS.Settings"

    async def synthesize(self, text: str, *, language: str = "ar", voice: str | None = None,
                         encoding: AudioEncoding = "pcm16",
                         sample_rate: int | None = None) -> AsyncIterator[AudioChunk]:
        s = self.settings
        english = language.startswith("en")
        model = s.model_en if english else s.model_ar
        voice = voice or (s.voice_en if english else s.voice_ar)
        client = _client(s.api_key, s.timeout_s)
        for piece in split_for_tts(text, s.max_chars):
            # Orpheus only returns WAV; telephony formats are produced locally (~1 ms).
            if not s.stream:
                resp = await client.audio.speech.create(model=model, voice=voice, input=piece,
                                                        response_format="wav")
                pcm, rate = wav_to_pcm16(await resp.read())
                yield to_encoding(pcm, rate, encoding, sample_rate)
                continue
            parser, conv = WavStreamParser(), None
            async with client.audio.speech.with_streaming_response.create(
                    model=model, voice=voice, input=piece, response_format="wav") as resp:
                async for raw in resp.iter_bytes(4096):
                    pcm = parser.feed(raw)
                    if not pcm:
                        continue
                    if conv is None:
                        conv = StreamConverter(parser.sample_rate or 24000, encoding, sample_rate)
                    if out := conv.feed(pcm):
                        yield AudioChunk(out, conv.dst_rate, encoding)
            if conv and (tail := conv.flush()):
                yield AudioChunk(tail, conv.dst_rate, encoding)
