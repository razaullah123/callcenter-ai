"""Load an agent for a call: route → published release → LoadedAgent (everything the call needs, frozen).

A LoadedAgent is built once per release and shared by all calls on it: provider clients, the compiled skills /
flows, the agent's phrases (pre-synthesized in its own voice), its tool policies and its knobs. A call keeps the
LoadedAgent it started with, so publishing or editing a provider never changes a call in progress — the next call
loads the new version.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from runtime.config import Settings
from runtime.control.config_store import ProviderSet, _drop_empty
from runtime.harness.prompts import Phrases
from runtime.providers import create
from runtime.skills import SkillSet
from runtime.tools import ToolExecutor
from runtime.tools.factory import executor_for

from .bundle import DEFAULT_AGENT, DEFAULT_STT_HINT, agent_settings, phrases_of, repo_bundle, upgrade
from .store import WORKSPACE

log = logging.getLogger(__name__)


@dataclass
class LoadedAgent:
    agent_id: str
    name: str
    release_id: int | None          # None: the repo copy (no database)
    version: int | None
    bundle: dict[str, Any]
    settings: Settings              # process settings + this agent's knobs
    providers: ProviderSet
    skills: SkillSet
    executor: ToolExecutor
    phrases: Phrases
    stt_hint: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_STT_HINT))
    languages: tuple[str, ...] = ("ar", "en")
    skill_files: dict[str, dict[str, str]] = field(default_factory=dict)

    def inline_bundle(self) -> dict[str, Any] | None:
        """This release with the skill files inline (for evals, which run on a fixture backend)."""
        if not self.bundle:
            return None
        return {**self.bundle, "skill_files": self.skill_files}

    @property
    def llm(self):
        return self.providers.llm

    @property
    def stt(self):
        return self.providers.stt

    @property
    def tts(self):
        return self.providers.tts

    @classmethod
    def from_parts(cls, *, settings: Settings, providers: ProviderSet, skills: SkillSet,
                   executor: ToolExecutor) -> "LoadedAgent":
        """For callers that assemble the pieces themselves (tests, offline scripts)."""
        return cls(agent_id=DEFAULT_AGENT["id"], name=DEFAULT_AGENT["name"], release_id=None,
                   version=providers.version, bundle={}, settings=settings, providers=providers, skills=skills,
                   executor=executor, phrases=getattr(skills, "phrases", None) or Phrases())


def agent_of(rt) -> LoadedAgent:
    """The runtime's default agent (or one assembled from an older-style runtime object)."""
    agent = getattr(rt, "agent", None)
    if isinstance(agent, LoadedAgent):
        return agent
    return LoadedAgent.from_parts(settings=rt.settings, providers=rt.providers, skills=rt.skills,
                                  executor=rt.executor)


async def mcp_config(servers: list[dict], settings: Settings, secrets=None) -> dict[str, dict]:
    """MCP pool config from mcp_servers rows; the auth token is resolved from the secret store by name."""
    from .secrets import Secrets, Cipher
    secrets = secrets or Secrets(None, Cipher(None), settings)       # no store: .env / environment only
    out = {}
    for m in servers:
        if not m.get("enabled", True):
            continue
        auth = m.get("auth") or {}
        headers = {}
        name = auth.get("secret") or auth.get("secret_env")
        if name and (token := await secrets.get(name)):
            headers[auth.get("header", "Authorization")] = f"{auth.get('scheme', 'Bearer')} {token}".strip()
        out[m["name"]] = {"transport": m.get("transport", "streamable_http"), "url": m["url"], "headers": headers}
    return out


def skill_ref(key: str, ref: Any) -> tuple[str, int]:
    """A bundle's skill entry: `name: version`, or `key: {skill: library_name, version: n}` (e.g. an agent's own
    persona used as its "_persona")."""
    if isinstance(ref, dict):
        return str(ref.get("skill") or key), int(ref["version"])
    return key, int(ref)


class AgentLoader:
    def __init__(self, store, backend, settings: Settings, *, warm_phrases: bool = True, secrets=None) -> None:
        self.store = store                  # PgStore / MemoryStore, or None: the repo copy only
        self.secrets = secrets              # resolves {"secret": NAME} in provider settings (None: plain values)
        self.backend = backend              # started MCP backend shared by all agents
        self.settings = settings
        self.warm_phrases = warm_phrases
        self._cache: dict[Any, LoadedAgent] = {}
        self._locks: dict[Any, asyncio.Lock] = {}

    def invalidate(self) -> None:
        """Drop cached agents (after a publish or a provider / tool change): the next call loads fresh."""
        self._cache.clear()

    # ---------------------------------------------------------------- routing

    async def route(self, *, agent_id: str | None = None, number: str | None = None) -> str | None:
        """Which agent answers: an explicit id, else the phone routes (exact number, 'prefix*', then '*')."""
        if agent_id or self.store is None:
            return agent_id
        routes = await self.store.routes(WORKSPACE)
        digits = (number or "").strip()
        best, rank = None, None
        for r in routes:                      # exact number > longest prefix > '*'; then priority
            p = r["pattern"]
            if p == "*":
                score = (0, 0)
            elif digits and p == digits:
                score = (2, len(p))
            elif digits and p.endswith("*") and digits.startswith(p[:-1]):
                score = (1, len(p))
            else:
                continue
            key = (*score, r.get("priority", 0))
            if rank is None or key > rank:
                best, rank = r["agent_id"], key
        return best

    async def for_call(self, *, agent_id: str | None = None, number: str | None = None) -> LoadedAgent:
        aid = await self.route(agent_id=agent_id, number=number)
        if self.store is None or aid is None:
            return await self.repo_agent()
        agent = await self.store.agent(aid)
        if agent is None:
            raise LookupError(f"unknown agent {aid!r}")
        if not agent.get("published_release_id"):
            raise LookupError(f"agent {aid!r} has no published release")
        return await self.load_release(agent["published_release_id"])

    # ---------------------------------------------------------------- loading

    async def load_release(self, release_id: int) -> LoadedAgent:
        if (hit := self._cache.get(release_id)) is not None:
            return hit
        async with self._locks.setdefault(release_id, asyncio.Lock()):
            if (hit := self._cache.get(release_id)) is not None:
                return hit
            rel = await self.store.release(release_id)
            if rel is None:
                raise LookupError(f"release {release_id} not found")
            agent = await self.store.agent(rel["agent_id"])
            loaded = await self.build(rel["bundle"], agent_id=rel["agent_id"],
                                      name=(agent or {}).get("name") or rel["agent_id"], release_id=release_id,
                                      version=rel["version"])
            self._cache[release_id] = loaded
            log.info("agent %s: release v%s loaded", rel["agent_id"], rel["version"])
            return loaded

    async def repo_agent(self) -> LoadedAgent:
        if (hit := self._cache.get("repo")) is None:
            hit = self._cache["repo"] = await self.build(repo_bundle(self.settings), agent_id=DEFAULT_AGENT["id"],
                                                         name=DEFAULT_AGENT["name"], release_id=None, version=None)
        return hit

    async def build(self, bundle: dict[str, Any], *, agent_id: str, name: str, release_id: int | None,
                    version: int | None, warm: bool | None = None) -> LoadedAgent:
        bundle = upgrade(bundle)                    # releases from before tool roles / hooks
        settings = agent_settings(self.settings, bundle.get("knobs") or {})
        phrases = phrases_of(bundle)
        providers = await self._providers(bundle, version or 0)
        tools_cfg = bundle.get("tools")
        if tools_cfg and self.secrets is not None:
            tools_cfg = await self.secrets.resolve(tools_cfg)     # e.g. an API tool's {"secret": NAME} header
        executor = executor_for(self.backend, tools_cfg)
        skill_files = await self._skill_files(bundle)
        skills = SkillSet(executor.catalog, skill_files, phrases)
        for skill_name, hooks in (bundle.get("legacy_turn_hooks") or {}).items():
            if (sk := skills.skills.get(skill_name)) is not None and not sk.turn_hooks:
                sk.turn_hooks = list(hooks)
        languages = tuple((bundle.get("agent") or {}).get("languages") or ("ar", "en"))
        if self.warm_phrases if warm is None else warm:
            from runtime.voice.phrases import PhraseCache, fixed_phrases
            providers.phrases = PhraseCache(providers.tts, phrases=phrases)
            await providers.phrases.warm(fixed_phrases(phrases, languages))
        return LoadedAgent(agent_id=agent_id, name=name, release_id=release_id, version=version, bundle=bundle,
                           settings=settings, providers=providers, skills=skills, executor=executor,
                           phrases=phrases, stt_hint=dict((bundle.get("voice") or {}).get("stt_hint")
                                                          or DEFAULT_STT_HINT),
                           languages=languages, skill_files=skill_files)

    async def _providers(self, bundle: dict[str, Any], version: int) -> ProviderSet:
        resolved: dict[str, dict] = {}
        for kind in ("stt", "llm", "tts", "embedding"):
            spec = (bundle.get("models") or {}).get(kind) or {}
            type_, conn = spec.get("type"), {}
            if spec.get("provider") and self.store is not None:
                row = await self.store.provider(spec["provider"])
                if row is None:
                    raise LookupError(f"{kind}: provider {spec['provider']!r} not found")
                type_, conn = row["type"], row.get("settings") or {}
                if self.secrets is not None:
                    conn = await self.secrets.resolve(conn)
            type_ = type_ or ("custom_http" if kind == "embedding" else "groq")
            if self.settings.provider_override and kind != "embedding":
                type_, conn = self.settings.provider_override, {}
            resolved[kind] = {"provider": type_, "settings": {**conn, **(spec.get("settings") or {})},
                              "provider_id": spec.get("provider")}

        def make(kind: str):
            return create(kind, resolved[kind]["provider"], _drop_empty(resolved[kind]["settings"]))
        config = {**resolved, "runtime": dict(bundle.get("knobs") or {})}
        return ProviderSet(version, config, make("llm"), make("stt"), make("tts"))

    async def _skill_files(self, bundle: dict[str, Any]) -> dict[str, dict[str, str]]:
        if bundle.get("skill_files"):
            return bundle["skill_files"]
        files = {}
        for key, ref in (bundle.get("skills") or {}).items():
            name, version = skill_ref(key, ref)
            got = await self.store.skill_version(WORKSPACE, name, version) if self.store else None
            if got is None:
                raise LookupError(f"skill {name} v{version} not found")
            files[key] = got
        return files

    async def load_draft(self, agent_id: str) -> LoadedAgent:
        """The agent's working copy (or its published release when there is no draft) — for test calls."""
        agent = await self.store.agent(agent_id) if self.store else None
        if agent is None:
            raise LookupError(f"unknown agent {agent_id!r}")
        if agent.get("draft") is None:
            return await self.for_call(agent_id=agent_id)
        from .bundle import content_hash
        key = ("draft", agent_id, content_hash(agent["draft"]))
        if (hit := self._cache.get(key)) is None:
            hit = self._cache[key] = await self.build(agent["draft"], agent_id=agent_id,
                                                      name=f"{agent['name']} (draft)", release_id=None, version=None)
        return hit
