"""Provider-config helpers for the console: build providers from a config, mask / merge secrets, validate, and
report what each running provider actually uses. (Where the config lives — agents, releases, provider records —
is runtime.platform; each call runs on the release its agent had when the call started.)

    {"stt": {"provider": "groq", "settings": {"model": "whisper-large-v3"}},
     "llm": {"provider": "groq", "settings": {...}}, "tts": {...}, "embedding": {...},
     "runtime": {"red_flag_mode": "advise_and_continue", "voice_end_silence_ms": 550, ...}}
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from runtime.platform.bundle import KNOBS, knob_errors
from runtime.providers import LLMProvider, STTProvider, TTSProvider, create, schemas

log = logging.getLogger(__name__)

KINDS = ("stt", "llm", "tts", "embedding")
RUNTIME_KNOBS = KNOBS      # per-agent knobs: name → type
MASK = "••••••"


@dataclass
class ProviderSet:
    """Providers built from one config version (cached; shared by all calls on that version)."""
    version: int
    config: dict[str, Any]
    llm: LLMProvider
    stt: STTProvider
    tts: TTSProvider
    phrases: Any = None


def build(version: int, config: dict[str, Any]) -> ProviderSet:
    def make(kind: str):
        spec = config.get(kind) or {}
        return create(kind, spec.get("provider", "groq"), _drop_empty(spec.get("settings") or {}))
    return ProviderSet(version, config, make("llm"), make("stt"), make("tts"))


def effective(ps: "ProviderSet") -> dict[str, dict[str, Any]]:
    """What each running provider actually uses (stored overrides + .env defaults) — secrets only as "set"."""
    out: dict[str, dict[str, Any]] = {}
    providers = {"llm": ps.llm, "stt": ps.stt, "tts": ps.tts}
    emb = ps.config.get("embedding") or {}
    try:
        providers["embedding"] = create("embedding", emb.get("provider", "custom_http"),
                                        _drop_empty(emb.get("settings") or {}))
    except Exception:
        pass
    for kind, provider in providers.items():
        settings = getattr(provider, "settings", None)
        if settings is None:
            continue
        values: dict[str, Any] = {}
        for name in type(settings).model_fields:
            v = getattr(settings, name, None)
            if isinstance(v, SecretStr) or name in ("api_key", "token", "secret", "password")                     or name.endswith(("_key", "_token", "_secret", "_password")):     # not "max_tokens"
                values[name] = "set" if (v.get_secret_value() if isinstance(v, SecretStr) else v) else None
            elif name == "headers" and isinstance(v, dict):
                values[name] = {k: "••••" for k in v} or None      # header values are usually credentials
            else:
                values[name] = v
        out[kind] = values
    return out


def _drop_empty(s: dict[str, Any]) -> dict[str, Any]:
    """Blank / masked fields fall back to provider defaults (e.g. API keys from .env)."""
    return {k: v for k, v in s.items() if v not in (None, "", MASK)}


def masked(config: dict[str, Any]) -> dict[str, Any]:
    """Config safe to send to the browser: secret fields masked."""
    out = json.loads(json.dumps(config))
    sch = schemas()
    for kind in KINDS:
        spec = out.get(kind) or {}
        props = (sch.get(kind, {}).get(spec.get("provider"), {}) or {}).get("properties", {})
        for name, value in (spec.get("settings") or {}).items():
            if value and (_is_secret(props.get(name, {})) or name in ("api_key", "headers")):
                spec["settings"][name] = MASK
    return out


def _is_secret(prop: dict) -> bool:
    return prop.get("format") == "password" or prop.get("writeOnly") is True


def validate(config: dict[str, Any]) -> list[str]:
    """Errors if a provider name is unknown or its settings don't validate."""
    errors = []
    for kind in KINDS:
        spec = config.get(kind)
        if not spec:
            continue
        try:
            create(kind, spec.get("provider", ""), _drop_empty(spec.get("settings") or {}))
        except Exception as e:
            errors.append(f"{kind}: {e}")
    errors += [e.replace("knobs:", "runtime:") for e in knob_errors(config.get("runtime") or {})]
    return errors


def merge_secrets(new: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """Masked values coming back from the console keep the previously stored secret."""
    out = json.loads(json.dumps(new))
    for kind in KINDS:
        n = (out.get(kind) or {}).get("settings") or {}
        o = (old.get(kind) or {}).get("settings") or {}
        same_provider = (out.get(kind) or {}).get("provider") == (old.get(kind) or {}).get("provider")
        for k, v in list(n.items()):
            if v == MASK:
                if same_provider and k in o:
                    n[k] = o[k]
                else:
                    n.pop(k)
    return out
