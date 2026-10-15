"""Custom HTTP providers — the contract for plugging in an in-house / third-party STT or TTS
service that is not OpenAI-compatible. (Custom LLMs should expose an OpenAI-compatible API.)

STT  POST {url}   multipart: file=<audio.wav>, fields: language?, prompt?
     200 → {"text": "...", "language": "ar" | "en" | null}

TTS  POST {url}   json: {"text", "language", "voice", "encoding": "pcm16"|"mulaw", "sample_rate"}
     200 → audio body; Content-Type audio/wav (decoded to pcm16) or audio/basic (raw μ-law)

Both send `headers` from settings (e.g. {"Authorization": "Bearer …"}).
"""

from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from .audio import prepare_for_stt, split_for_tts, to_encoding, wav_to_pcm16
from .base import (
    AudioChunk,
    AudioEncoding,
    AudioInput,
    EmbeddingProvider,
    STTProvider,
    Transcript,
    TTSProvider,
)
from .registry import register

_clients: dict[float, httpx.AsyncClient] = {}


def _http(timeout: float) -> httpx.AsyncClient:
    if timeout not in _clients:
        _clients[timeout] = httpx.AsyncClient(timeout=timeout, http2=False)
    return _clients[timeout]


class _HttpBase(BaseModel):
    url: str = Field(..., description="Endpoint URL")
    headers: dict[str, str] = Field(default_factory=dict, description="Extra headers, e.g. auth")
    timeout_s: float = 10.0


@register("custom_http")
class CustomHttpSTT(STTProvider):
    class Settings(_HttpBase):
        pass

    settings: "CustomHttpSTT.Settings"

    async def transcribe(self, audio: AudioInput, *, language: str | None = None,
                         prompt: str | None = None, model: str | None = None) -> Transcript:
        s = self.settings
        data = {k: v for k, v in (("language", language), ("prompt", prompt), ("model", model)) if v}
        resp = await _http(s.timeout_s).post(
            s.url, headers=s.headers, data=data,
            files={"file": ("audio.wav", prepare_for_stt(audio), "audio/wav")})
        resp.raise_for_status()
        body = resp.json()
        return Transcript(text=body.get("text", "").strip(), language=body.get("language") or language,
                          duration_ms=audio.duration_ms, raw=body)


@register("custom_http")
class CustomHttpTTS(TTSProvider):
    class Settings(_HttpBase):
        voice_ar: str = ""
        voice_en: str = ""
        max_chars: int = 300

    settings: "CustomHttpTTS.Settings"

    async def synthesize(self, text: str, *, language: str = "ar", voice: str | None = None,
                         encoding: AudioEncoding = "pcm16",
                         sample_rate: int | None = None) -> AsyncIterator[AudioChunk]:
        s = self.settings
        voice = voice or (s.voice_en if language.startswith("en") else s.voice_ar)
        for piece in split_for_tts(text, s.max_chars):
            resp = await _http(s.timeout_s).post(s.url, headers=s.headers, json={
                "text": piece, "language": language, "voice": voice,
                "encoding": encoding, "sample_rate": sample_rate})
            resp.raise_for_status()
            if resp.headers.get("content-type", "").startswith("audio/basic"):
                yield AudioChunk(resp.content, sample_rate or 8000, "mulaw")
            else:
                pcm, rate = wav_to_pcm16(resp.content)
                yield to_encoding(pcm, rate, encoding, sample_rate)


def _env(attr: str):
    def factory():
        from runtime.config import get_settings
        return getattr(get_settings(), attr)
    return factory


@register("custom_http")
class CustomHttpEmbedding(EmbeddingProvider):
    """In-house embedding service (default: EMBEDDING_URL from .env).

    Request:  POST {url} json {<text_field>: "..."}  (per text)  or  {<text_field>: ["...", ...]}  (batch)
    Response: any of  [..floats..] · [[..]] · {"embedding": [..]} · {"embeddings": [[..]]} · {"data": [{"embedding": [..]}]}
    """

    class Settings(BaseModel):
        url: str = Field(default_factory=_env("embedding_url"), description="Embedding endpoint")
        headers: dict[str, str] = Field(default_factory=dict)
        timeout_s: float = 5.0
        text_field: str = Field("text", description="JSON field carrying the text")
        mode: Literal["single", "batch"] = Field("single", description="One request per text, or all texts at once")
        dim: int = Field(default_factory=_env("embedding_dim"), description="Expected vector size (validated)")

    settings: "CustomHttpEmbedding.Settings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        s = self.settings
        client = _http(s.timeout_s)
        if s.mode == "batch":
            resp = await client.post(s.url, headers=s.headers, json={s.text_field: texts})
            resp.raise_for_status()
            vectors = _parse_vectors(resp.json())
        else:
            vectors = []
            for text in texts:
                resp = await client.post(s.url, headers=s.headers, json={s.text_field: text})
                resp.raise_for_status()
                vectors.extend(_parse_vectors(resp.json()))
        if len(vectors) != len(texts):
            raise ValueError(f"embedding service returned {len(vectors)} vectors for {len(texts)} texts")
        for v in vectors:
            if len(v) != s.dim:
                raise ValueError(f"embedding dim {len(v)} != expected {s.dim} — wrong model?")
        return vectors


def _parse_vectors(body: Any) -> list[list[float]]:
    if isinstance(body, dict):
        for key in ("embeddings", "vectors"):
            if key in body:
                return _parse_vectors(body[key])
        if "embedding" in body:
            return _parse_vectors(body["embedding"])
        if "data" in body:
            return [d["embedding"] for d in sorted(body["data"], key=lambda d: d.get("index", 0))]
        raise ValueError(f"unrecognized embedding response keys: {sorted(body)}")
    if isinstance(body, list) and body and isinstance(body[0], (int, float)):
        return [[float(x) for x in body]]
    if isinstance(body, list):
        return [[float(x) for x in v] for v in body]
    raise ValueError(f"unrecognized embedding response: {type(body).__name__}")


def location_embedding_text(row) -> str:
    """Text the `locations.embedding` vectors were built from (verified: cosine 1.0000 with stored vectors).
    Use it when (re)embedding locations so new rows stay consistent with existing ones."""
    return " ".join(str(row[k]) for k in ("city_name_en", "city_name_ar", "district_name_en", "district_name_ar") if row[k])
