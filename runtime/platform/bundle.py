"""Agent release bundle: everything one agent needs for a call, as plain JSON (stored in agent_releases.bundle).

    {"schema": 1,
     "agent":  {"name": "...", "languages": ["ar", "en"], "default_language": "ar"},
     "models": {"llm": {"provider": "<providers.id>", "settings": {"model": "..."}}, "stt": …, "tts": …,
                "embedding": …},                        # repo / offline bundles use {"type": "groq"} instead of an id
     "knobs":  {"red_flag_mode": "...", "voice_end_silence_ms": 550, …},
     "voice":  {"stt_hint": {"ar": "...", "en": "..."}},
     "phrases": {"GREETING": {"ar": "...", "en": "..."}, …},   # overrides of harness.prompts defaults
     "skills": {"book_appointment": 7, …},                      # pinned skill versions (DB bundles) …
     "skill_files": {"book_appointment": {"SKILL.md": "...", "flow.yaml": "..."}},   # … or inline (repo bundles)
     "tools":  {"server": "...", "inject": {...}, "skills": {group: {tool: policy}}}}   # frozen tool policies

Provider *connections* (keys, URLs, headers, timeouts) live on the provider record and are shared; the agent's
choices on top of a connection (model, voice …) are versioned in its release.
"""

import hashlib
import json
from typing import Any

from runtime.config import Settings
from runtime.harness.prompts import Phrases

SCHEMA = 2          # 2: tool policies carry role / hooks / backs / success_line, skills their turn hooks (12.4)
KINDS = ("stt", "llm", "tts", "embedding")

# Settings that describe the connection (kept on the provider record), not the agent's choice.
CONNECTION_FIELDS = {"api_key", "base_url", "url", "headers", "timeout_s", "token", "secret", "password"}

# Per-agent knobs: name → type. They overlay the process settings for that agent's calls.
KNOBS: dict[str, type] = {
    "red_flag_mode": str, "voice_end_silence_ms": int, "voice_barge_in_ms": int, "voice_barge_in_confirm": bool,
    "voice_level_gate_db": float, "voice_filler_after_s": float, "llm_hedge_after_s": float,
    "ivr_barge_in_grace_ms": int, "ivr_chunk_ms": int, "ivr_transfer_destination": str,
    "require_verification": bool, "entry_skill": str, "main_flow": str,
}

# Vocabulary hint for speech-to-text, per language (place names only: a longer hint came back verbatim on noise).
DEFAULT_STT_HINT = {
    "ar": "أقرب موعد متاح، عيادة، مستشفى العليا، الحمراء، الريان، السويدي، التخصصي، الخرج.",
    "en": "Appointment, clinic, Olaya, Al Hamra, Arryan, Suwaidi.",
}

DEFAULT_AGENT = {"id": "hmg-care", "name": "HMG Customer Care",
                 "description": "Dr. Sulaiman Al Habib call center: caller verification and appointment booking"}


def coerce(kind: type, value: Any) -> Any:
    if kind is bool and isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return kind(value)


def knob_errors(knobs: dict[str, Any]) -> list[str]:
    errors = []
    for k, v in knobs.items():
        if k not in KNOBS:
            errors.append(f"knobs: unknown setting {k}")
            continue
        try:
            coerce(KNOBS[k], v)
        except (TypeError, ValueError):
            errors.append(f"knobs: {k} must be {KNOBS[k].__name__}")
    return errors


def agent_settings(base: Settings, knobs: dict[str, Any]) -> Settings:
    """The process settings with this agent's knobs on top (a copy — the process settings never change)."""
    update = {k: coerce(KNOBS[k], v) for k, v in (knobs or {}).items() if k in KNOBS and v not in (None, "")}
    return base.model_copy(update=update)


def split_settings(settings: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(connection, choice): connection fields go on the provider record, the rest into the agent release."""
    conn = {k: v for k, v in settings.items() if k in CONNECTION_FIELDS}
    choice = {k: v for k, v in settings.items() if k not in CONNECTION_FIELDS}
    return conn, choice


def content_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def repo_skill_files() -> dict[str, dict[str, str]]:
    from runtime.skills import read_skill_dir
    return read_skill_dir()


def repo_tools_config() -> dict[str, Any]:
    from runtime.tools.catalog import load_tools_config
    return load_tools_config()


def repo_bundle(settings: Settings, *, provider_types: dict[str, str] | None = None) -> dict[str, Any]:
    """The HMG agent as the repo defines it (skills/, config/tools.yaml, harness.prompts): the seed for the DB and
    the bundle used when there is no database (offline tools, evals on the repo copy)."""
    types = {k: "groq" for k in ("stt", "llm", "tts")} | {"embedding": "custom_http"} | (provider_types or {})
    return {
        "schema": SCHEMA,
        "agent": {"name": DEFAULT_AGENT["name"], "languages": ["ar", "en"],
                  "default_language": settings.default_language},
        "models": {k: {"type": types[k], "settings": {}} for k in KINDS},
        "knobs": {k: getattr(settings, k) for k in KNOBS},
        "voice": {"stt_hint": dict(DEFAULT_STT_HINT)},
        "phrases": {},
        "skill_files": repo_skill_files(),
        "tools": repo_tools_config(),
    }


# Releases published before 12.4 (schema 1) predate tool roles / hooks and skill turn hooks: when one is loaded
# (e.g. rolled back to), the harness-facing policy fields are taken from the repo's tool config, and the booking skill
# gets its turn hook — otherwise caller verification and the booking checks would silently stop working.
_POLICY_FIELDS = ("role", "args", "hooks", "backs", "success_line")
LEGACY_TURN_HOOKS = {"book_appointment": ["hmg.booking"]}


def upgrade(bundle: dict[str, Any]) -> dict[str, Any]:
    if bundle.get("schema", 1) >= 2:
        return bundle
    import copy
    out = copy.deepcopy(bundle)
    repo = {n: p for entries in (repo_tools_config().get("skills") or {}).values() for n, p in (entries or {}).items()}
    for entries in ((out.get("tools") or {}).get("skills") or {}).values():
        for name, policy in (entries or {}).items():
            for f in _POLICY_FIELDS:
                if f in (repo.get(name) or {}) and f not in (policy or {}):
                    entries[name] = policy = {**(policy or {}), f: repo[name][f]}
    out["legacy_turn_hooks"] = LEGACY_TURN_HOOKS
    out["schema"] = 2
    return out


def phrases_of(bundle: dict[str, Any]) -> Phrases:
    return Phrases(bundle.get("phrases") or {})


def validate(bundle: dict[str, Any]) -> list[str]:
    errors = []
    if bundle.get("schema") not in (1, SCHEMA):
        errors.append(f"schema must be {SCHEMA}")
    for k in ("llm", "stt", "tts"):
        m = (bundle.get("models") or {}).get(k) or {}
        if not (m.get("provider") or m.get("type")):
            errors.append(f"models.{k}: a provider is required")
    errors += knob_errors(bundle.get("knobs") or {})
    if not (bundle.get("skills") or bundle.get("skill_files")):
        errors.append("skills: at least one skill is required")
    return errors


__all__ = ["CONNECTION_FIELDS", "DEFAULT_AGENT", "DEFAULT_STT_HINT", "KINDS", "KNOBS", "SCHEMA",
           "agent_settings", "coerce", "content_hash", "knob_errors", "phrases_of", "repo_bundle", "split_settings",
           "validate"]
