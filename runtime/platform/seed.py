"""First start: move today's HMG agent into the platform tables. Every start (development): repo sync.

Seeding (once, when the workspace is new):
  • providers   — from the active Phase-9 provider config (agent_config) if there is one, else .env defaults
  • MCP server  — from .env (MCP_SERVER_URL; the token stays in .env until the secret store, 12.2)
  • tools       — config/tools.yaml
  • skills      — skills/ (continuing the existing skill_versions history)
  • agent       — "HMG Customer Care" with release v1, and the default phone route '*'

Repo sync (PLATFORM_REPO_SYNC, on by default in development): when a skill folder, config/tools.yaml or the default
phrases in harness/prompts.py changed since they were last imported, the change is imported as a new skill version
/ tool policy and a new release of every agent that uses it is published. Edits made in the console are not
overwritten unless the same repo file changes afterwards.
"""

import logging
from typing import Any

from runtime.config import Settings
from runtime.harness.prompts import Phrases

from .bundle import (DEFAULT_AGENT, DEFAULT_STT_HINT, KINDS, KNOBS, SCHEMA, content_hash, repo_skill_files,
                     repo_tools_config, split_settings)
from .store import WORKSPACE

log = logging.getLogger(__name__)

PROVIDER_NAMES = {"groq": "Groq", "openai_compatible": "OpenAI-compatible", "custom_http": "Custom HTTP",
                  "fake": "Fake (load tests)"}
KIND_NAMES = {"stt": "STT", "llm": "LLM", "tts": "TTS", "embedding": "Embeddings"}
DEFAULT_TYPES = {"stt": "groq", "llm": "groq", "tts": "groq", "embedding": "custom_http"}


def provider_id(kind: str, type_: str) -> str:
    return f"{type_.replace('_', '-')}-{kind}"


def tool_rows(cfg: dict[str, Any]) -> list[dict]:
    return [{"name": name, "grp": grp, "source": (policy or {}).get("source", "mcp"), "policy": policy or {}}
            for grp, entries in (cfg.get("skills") or {}).items() for name, policy in (entries or {}).items()]


async def seed(store, settings: Settings, *, repo_sync: bool = True, secrets=None) -> dict[str, Any]:
    """Idempotent. Returns what was created / synced / migrated."""
    report: dict[str, Any] = {}
    new_workspace = await store.ensure_workspace(WORKSPACE, "Dr. Sulaiman Al Habib Group")
    report["workspace_created"] = new_workspace

    # ---- providers (once)
    models: dict[str, dict] = {}
    if not await store.providers(WORKSPACE):
        legacy = await store.legacy_agent_config() if hasattr(store, "legacy_agent_config") else None
        for kind in KINDS:
            spec = (legacy or {}).get(kind) or {}
            type_ = spec.get("provider") or DEFAULT_TYPES[kind]
            conn, choice = split_settings(spec.get("settings") or {})
            pid = provider_id(kind, type_)
            await store.put_provider({"id": pid, "workspace_id": WORKSPACE, "kind": kind, "type": type_,
                                      "name": f"{PROVIDER_NAMES.get(type_, type_)} {KIND_NAMES[kind]}",
                                      "settings": conn, "updated_by": "seed"})
            models[kind] = {"provider": pid, "settings": choice}
        report["providers"] = sorted(m["provider"] for m in models.values())
        report["legacy_knobs"] = (legacy or {}).get("runtime") or {}

    # ---- MCP server (once)
    if not await store.mcp_servers(WORKSPACE) and settings.mcp_server_url:
        await store.put_mcp_server({"id": "hmg_tools", "workspace_id": WORKSPACE, "name": "hmg_tools",
                                    "transport": settings.mcp_transport, "url": settings.mcp_server_url,
                                    "auth": {"header": settings.mcp_auth_header, "scheme": settings.mcp_auth_scheme,
                                             "secret": "MCP_AUTH_TOKEN"}})
        report["mcp_server"] = "hmg_tools"

    # ---- repo → library (tools, skills, phrases)
    changed: dict[str, Any] = {}
    agent = await store.agent(DEFAULT_AGENT["id"])
    if repo_sync or agent is None:
        changed = await _sync_library(store)

    # ---- the HMG agent (once)
    if agent is None:
        tools_cfg = repo_tools_config()
        skills = {s["name"]: s["latest"] for s in await store.skills(WORKSPACE) if s["latest"]}
        knobs = {k: getattr(settings, k) for k in KNOBS}
        knobs.update({k: v for k, v in (report.get("legacy_knobs") or {}).items() if k in KNOBS})
        if not models:              # providers existed already: use one of each kind
            for p in await store.providers(WORKSPACE):
                models.setdefault(p["kind"], {"provider": p["id"], "settings": {}})
        bundle = {"schema": SCHEMA,
                  "agent": {"name": DEFAULT_AGENT["name"], "languages": ["ar", "en"],
                            "default_language": settings.default_language},
                  "models": models, "knobs": knobs, "voice": {"stt_hint": dict(DEFAULT_STT_HINT)},
                  "phrases": Phrases.defaults(), "skills": skills, "tools": tools_cfg}
        await _fill_model_defaults(store, bundle)
        await store.put_agent({"id": DEFAULT_AGENT["id"], "workspace_id": WORKSPACE, "name": DEFAULT_AGENT["name"],
                               "description": DEFAULT_AGENT["description"]})
        rel = await store.add_release(DEFAULT_AGENT["id"], bundle, "seed", "initial (migrated from the repo / .env)")
        await store.put_route(WORKSPACE, "*", DEFAULT_AGENT["id"])
        report["agent_created"] = {"agent": DEFAULT_AGENT["id"], "release_id": rel["id"], "version": rel["version"]}
    elif changed:
        report["releases"] = await _release_changes(store, changed)
    report["synced"] = sorted(changed)
    if secrets is not None:
        report["secrets"] = await migrate_secrets(store, settings, secrets)
    report["materialized"] = await materialize_defaults(store)
    return report


def _plain(v) -> str | None:
    return v.get_secret_value() if hasattr(v, "get_secret_value") else v


async def migrate_secrets(store, settings: Settings, secrets) -> dict[str, Any]:
    """Keys out of .env / provider records into the encrypted store; records keep only {"secret": NAME}.
    Idempotent — and a no-op (with a warning) without MASTER_KEY."""
    if not secrets.cipher.available:
        log.warning("MASTER_KEY is not set: keys stay in .env / provider records (plain). See .env.example.")
        return {"skipped": "MASTER_KEY not set"}
    out: dict[str, list] = {"imported": [], "providers": [], "mcp_servers": []}
    existing = {r["name"] for r in await store.secrets(WORKSPACE)}

    async def ensure(name: str, value: str | None) -> bool:
        if name in existing:
            return True
        if not value:
            return False
        await secrets.put(name, value, "migration")
        existing.add(name)
        out["imported"].append(name)
        return True

    have_groq = await ensure("GROQ_API_KEY", _plain(settings.groq_api_key))
    await ensure("MCP_AUTH_TOKEN", _plain(settings.mcp_auth_token))
    for m in await store.mcp_servers(WORKSPACE):
        auth = dict(m.get("auth") or {})
        if "secret_env" in auth:                 # 12.1 records: a name to look up in .env
            name = auth.pop("secret_env")
            await ensure(name, await secrets.get(name))
            auth["secret"] = name
            await store.put_mcp_server({**m, "auth": auth})
            out["mcp_servers"].append(m["id"])
    for p in await store.providers(WORKSPACE):
        new = await secrets.externalize(p["id"], dict(p.get("settings") or {}), "migration")
        if p["type"] == "groq" and not new.get("api_key") and have_groq:
            new["api_key"] = {"secret": "GROQ_API_KEY"}
        if p["kind"] == "embedding" and p["type"] == "custom_http" and not new.get("url") and settings.embedding_url:
            new["url"] = settings.embedding_url
        if new != (p.get("settings") or {}):
            await store.put_provider({**p, "settings": new, "updated_by": "migration"})
            out["providers"].append(p["id"])
    return {k: v for k, v in out.items() if v}


async def _fill_model_defaults(store, bundle: dict) -> list[str]:
    """Empty model choices ("whatever .env says") → the effective values, written into `bundle`."""
    from runtime.providers import create
    from .bundle import CONNECTION_FIELDS
    from .secrets import is_secret_field
    filled = []
    for kind, spec in (bundle.get("models") or {}).items():
        if spec.get("settings"):
            continue
        row = await store.provider(spec["provider"]) if spec.get("provider") else None
        type_ = (row or {}).get("type") or spec.get("type")
        placeholder = {"api_key": "x"} if type_ in ("groq", "openai_compatible") else             {"url": "http://x"} if type_ == "custom_http" else {}
        try:
            effective = create(kind, type_, placeholder)
        except Exception as e:
            log.warning("defaults for %s/%s not materialized: %r", kind, type_, e)
            continue
        choice = {k: v for k, v in effective.settings.model_dump().items()
                  if k not in CONNECTION_FIELDS and not is_secret_field(k) and v is not None}
        if choice:
            spec["settings"] = choice
            filled.append(kind)
    return filled


async def materialize_defaults(store) -> list[dict]:
    """Agents whose model choices are still empty get the effective values in a new release — so the agent no
    longer depends on .env and the console shows what it really uses."""
    out = []
    for agent in await store.agents(WORKSPACE):
        if not agent.get("published_release_id"):
            continue
        bundle = (await store.release(agent["published_release_id"]))["bundle"]
        if filled := await _fill_model_defaults(store, bundle):
            r = await store.add_release(agent["id"], bundle, "migration",
                                        "model / voice defaults from .env written into the release: " + ", ".join(filled))
            out.append({"agent": agent["id"], **r, "kinds": filled})
    return out


async def _sync_library(store) -> dict[str, Any]:
    """Import repo files that changed since the last import. Returns {"skills": {name: version}, "tools": cfg,
    "phrases": (old_defaults, new_defaults)} for whatever changed."""
    changed: dict[str, Any] = {}
    tools_cfg = repo_tools_config()
    h = content_hash(tools_cfg)
    if await store.meta_get("repo_hash:tools") != h:
        await store.put_tools(WORKSPACE, tool_rows(tools_cfg))
        await store.meta_set("repo_hash:tools", h)
        changed["tools"] = tools_cfg

    known = {s["name"]: s for s in await store.skills(WORKSPACE)}
    new_versions = {}
    for name, files in repo_skill_files().items():
        h = content_hash(files)
        row = known.get(name)
        if row and row.get("repo_hash") == h:
            continue
        versions = await store.skill_versions(WORKSPACE, name)     # history may predate the skills table
        latest = await store.skill_version(WORKSPACE, name, versions[0]["version"]) if versions else None
        if latest == files:              # already the latest version (e.g. saved from the console before 12.1)
            await store.set_skill_repo_hash(WORKSPACE, name, h)
            continue
        new_versions[name] = await store.add_skill_version(WORKSPACE, name, files, "repo", "imported from skills/",
                                                           repo_hash=h)
    if new_versions:
        changed["skills"] = new_versions

    defaults = Phrases.defaults()
    old = await store.meta_get("repo_phrases")
    if old != defaults:
        await store.meta_set("repo_phrases", defaults)
        if old is not None:
            changed["phrases"] = (old, defaults)
    return changed


async def _release_changes(store, changed: dict[str, Any]) -> list[dict]:
    """A new release for every agent affected by the imported changes."""
    out = []
    for agent in await store.agents(WORKSPACE):
        if not agent.get("published_release_id"):
            continue
        rel = await store.release(agent["published_release_id"])
        bundle = rel["bundle"]
        notes = []
        for name, version in (changed.get("skills") or {}).items():
            if name in bundle.get("skills", {}) or agent["id"] == DEFAULT_AGENT["id"]:
                bundle.setdefault("skills", {})[name] = version
                notes.append(f"{name} v{version}")
        if "tools" in changed and agent["id"] == DEFAULT_AGENT["id"]:
            bundle["tools"] = changed["tools"]
            bundle["schema"] = SCHEMA
            notes.append("tool policies")
        if "phrases" in changed:
            old, new = changed["phrases"]
            phrases = bundle.setdefault("phrases", {})
            updated = [k for k, v in new.items() if phrases.get(k, old.get(k)) == old.get(k) and old.get(k) != v]
            for k in updated:                 # only lines the agent didn't change itself
                phrases[k] = new[k]
            if updated:
                notes.append("phrases " + ", ".join(updated))
        if notes:
            r = await store.add_release(agent["id"], bundle, "repo-sync", "repo: " + "; ".join(notes))
            out.append({"agent": agent["id"], **r, "note": notes})
            log.info("agent %s: release v%s published from repo changes (%s)", agent["id"], r["version"],
                     "; ".join(notes))
    return out
