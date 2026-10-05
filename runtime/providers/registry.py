"""Provider registry: name → implementation + settings schema.

    @register("groq")
    class GroqLLM(LLMProvider): ...

    llm = create("llm", "groq", {"model": "openai/gpt-oss-120b"})
    schemas()  # → {"llm": {"groq": {...json schema...}}, ...} for the console
"""

from typing import Any, TypeVar

from .base import Provider

P = TypeVar("P", bound=type[Provider])

_REGISTRY: dict[str, dict[str, type[Provider]]] = {}


def register(name: str):
    def deco(cls: P) -> P:
        _REGISTRY.setdefault(cls.kind, {})[name] = cls
        return cls
    return deco


def available(kind: str | None = None) -> dict[str, list[str]]:
    kinds = [kind] if kind else list(_REGISTRY)
    return {k: sorted(_REGISTRY.get(k, {})) for k in kinds}


def schemas() -> dict[str, dict[str, dict[str, Any]]]:
    return {kind: {name: cls.Settings.model_json_schema() for name, cls in impls.items()}
            for kind, impls in _REGISTRY.items()}


def provider_class(kind: str, name: str) -> type[Provider]:
    """The implementation registered as `name` (e.g. to read a TTS provider's VOICES)."""
    return _REGISTRY[kind][name]


def create(kind: str, name: str, config: dict[str, Any] | None = None) -> Provider:
    try:
        cls = _REGISTRY[kind][name]
    except KeyError:
        raise ValueError(f"unknown {kind} provider {name!r}; available: {available(kind)[kind]}") from None
    return cls(cls.Settings.model_validate(config or {}))
