"""Provider interfaces. The harness and voice pipeline depend only on these, so any
STT / LLM / TTS / embedding backend can be swapped from config without code changes.

Messages and tool definitions use the OpenAI chat format (what Groq and most
OpenAI-compatible providers accept); adapters for other formats convert internally.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

Message = dict[str, Any]   # {"role": "system"|"user"|"assistant"|"tool", "content": ..., ...}
ToolSpec = dict[str, Any]  # {"type": "function", "function": {"name", "description", "parameters"}}


# ---------- STT ----------

@dataclass
class AudioInput:
    """Mono 16-bit PCM audio."""
    pcm: bytes
    sample_rate: int = 16_000

    @property
    def duration_ms(self) -> float:
        return len(self.pcm) / 2 / self.sample_rate * 1000


@dataclass
class Transcript:
    text: str
    language: str | None = None   # ISO-639-1 ("ar", "en") when the provider reports it
    duration_ms: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------- LLM ----------

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass
class TextDelta:
    text: str
    kind: Literal["text"] = "text"


@dataclass
class ToolCallsReady:
    """Emitted once, after the stream ends, with fully assembled tool calls."""
    calls: list[ToolCall]
    kind: Literal["tool_calls"] = "tool_calls"


@dataclass
class LLMDone:
    finish_reason: str | None
    usage: dict[str, Any] = field(default_factory=dict)
    kind: Literal["done"] = "done"


LLMEvent = TextDelta | ToolCallsReady | LLMDone


# ---------- TTS ----------

AudioEncoding = Literal["pcm16", "mulaw"]


@dataclass
class AudioChunk:
    data: bytes
    sample_rate: int
    encoding: AudioEncoding = "pcm16"


# ---------- Provider base classes ----------

class Provider(ABC):
    """`Settings` is a pydantic model; its JSON schema drives the console's config forms."""

    kind: ClassVar[str]
    Settings: ClassVar[type[BaseModel]]

    def __init__(self, settings: BaseModel) -> None:
        self.settings = settings

    async def aclose(self) -> None:  # noqa: B027 — optional hook
        pass


class STTProvider(Provider):
    kind = "stt"

    @abstractmethod
    async def transcribe(self, audio: AudioInput, *, language: str | None = None,
                         prompt: str | None = None) -> Transcript: ...


class LLMProvider(Provider):
    kind = "llm"

    @abstractmethod
    def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
               **options: Any) -> AsyncIterator[LLMEvent]: ...


class TTSProvider(Provider):
    kind = "tts"
    # The voices this provider offers (Voices page): {"voice", "language", "gender", "dialect", "model"?, "style"?}.
    # Empty when the provider doesn't publish a list (any voice name can still be tried).
    VOICES: list[dict] = []

    @abstractmethod
    def synthesize(self, text: str, *, language: str = "ar", voice: str | None = None,
                   encoding: AudioEncoding = "pcm16", sample_rate: int | None = None) -> AsyncIterator[AudioChunk]: ...


class EmbeddingProvider(Provider):
    kind = "embedding"

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
