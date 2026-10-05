"""Process-wide runtime: what all calls share (event bus, MCP backend, agent loader, logs).

Each call is routed to an agent and gets that agent's LoadedAgent (providers, skills, tools, phrases, knobs),
loaded from its published release and frozen for the call. Per-call objects (Session, Agent, VoiceCall) are cheap.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from runtime.config import Settings, get_settings
from runtime.data.reference import get_reference
from runtime.events import ConsoleSink, EventBus, EventType, JsonlSink, Level
from runtime.harness.redact import redact_event
from runtime.platform import AgentLoader, LoadedAgent, PgStore, in_project, mcp_config
from runtime.providers import LLMProvider, STTProvider, TTSProvider
from runtime.skills import SkillSet
from runtime.tools import MCPPool, MockMCP, ToolExecutor
from runtime.tools.hybrid import HybridMCP

log = logging.getLogger(__name__)


async def all_mcp_config(platform, settings, secrets) -> dict[str, dict]:
    """One MCP pool for every project's servers (names are unique across projects); each server's token comes from
    its own project's secrets."""
    cfg: dict[str, dict] = {}
    for w in await platform.list_workspaces():
        with in_project(w["id"]):
            cfg.update(await mcp_config(await platform.mcp_servers(w["id"]), settings, secrets))
    return cfg


@dataclass
class Runtime:
    settings: Settings
    bus: EventBus
    backend: Any                                  # started MCP backend, shared by every agent
    loader: AgentLoader
    agent: LoadedAgent                            # the default agent (route '*')
    store: "EventStore | None" = None
    live: "LiveHub | None" = None
    platform: Any = None                          # PgStore (None without a database: the repo copy only)
    secrets: Any = None                           # encrypted secret store (runtime.platform.secrets.Secrets)
    sync: Any = None                              # ClusterSync: keeps worker processes in step (None: no database)
    calls: dict = field(default_factory=dict)   # call_id → VoiceCall running in this process (console "End call")

    # The default agent's parts (console, health, evals). A call uses the agent it was routed to.
    @property
    def providers(self):
        return self.agent.providers

    @property
    def skills(self) -> SkillSet:
        return self.agent.skills

    @property
    def executor(self) -> ToolExecutor:
        return self.agent.executor

    @property
    def llm(self) -> LLMProvider:
        return self.agent.llm

    @property
    def stt(self) -> STTProvider:
        return self.agent.stt

    @property
    def tts(self) -> TTSProvider:
        return self.agent.tts

    async def agent_for_call(self, *, agent_id: str | None = None, number: str | None = None,
                             draft: bool = False) -> LoadedAgent:
        """The agent that answers this call — loaded complete (cached per release) and frozen for the call.
        `draft`: the agent's working copy (studio test calls)."""
        if draft and agent_id:
            return await self.loader.load_draft(agent_id)
        try:
            return await self.loader.for_call(agent_id=agent_id, number=number)
        except Exception as e:
            if agent_id:                        # an explicitly requested agent that can't load is an error …
                raise
            log.warning("agent routing failed (%r) — the default agent answers", e)
            return self.agent                   # … a routing problem never drops a call

    async def reload(self) -> None:
        """After a publish or a provider / tool / skill change: new calls load the new version."""
        self.loader.invalidate()
        self.agent = await self.loader.for_call()
        self.bus.bind().emit(EventType.SLOT_SET, field="agent_release", agent=self.agent.agent_id,
                             value=self.agent.version)

    async def reload_mcp(self) -> None:
        """MCP servers changed: connect the new set, then new calls use it. Calls in progress keep the old
        connections, which are closed a while later."""
        cfg = await all_mcp_config(self.platform, self.settings, self.secrets)
        backend = _make_backend(self.settings, cfg)
        await backend.start()
        old, self.backend = self.backend, backend
        self.loader.backend = backend
        await self.reload()

        async def close_later() -> None:
            await asyncio.sleep(15 * 60)
            if hasattr(old, "close"):
                await old.close()
        self._closing = asyncio.create_task(close_later())

    def mcp_status(self) -> dict:
        return self.backend.status() if hasattr(self.backend, "status") else {}

    async def config_changed(self, kind: str, **detail: Any) -> None:
        """A change made here (console): reload, and tell the other worker processes to reload too."""
        await (self.reload_mcp() if kind == "mcp" else self.reload())
        if self.sync is not None:
            try:
                await self.sync.config_changed(kind, **detail)
            except Exception as e:
                log.warning("other workers not told about the %s change: %r", kind, e)

    async def end_call(self, call_id: str) -> bool:
        """End a call wherever it runs: here, or ask the worker that has it. False if no worker has it."""
        if (call := self.calls.get(call_id)) is not None:
            await call.end_from_console()
            return True
        if self.sync is not None and self.live is not None and call_id in self.live.active:
            await self.sync.end_call(call_id)
            return True
        return False

    # ---- messages from the other workers

    async def _on_config(self, msg: dict) -> None:
        log.info("reloading agents: %s changed on another worker", msg.get("kind"))
        await (self.reload_mcp() if msg.get("kind") == "mcp" else self.reload())

    async def _on_control(self, msg: dict) -> None:
        if msg.get("action") == "end" and (call := self.calls.get(msg.get("call_id"))) is not None:
            await call.end_from_console()

    async def _on_live(self, msg: dict) -> None:
        if self.live is not None:
            from runtime.events import Event
            msg.pop("origin", None)
            await self.live(Event.model_validate(msg))

    @classmethod
    async def create(cls, settings: Settings | None = None, *, console_log: bool = False,
                     control_plane: bool = True) -> "Runtime":
        s = settings or get_settings()
        bus = EventBus()
        bus.add_redactor(redact_event)
        bus.subscribe(JsonlSink(s.log_dir))
        if console_log:
            bus.subscribe(ConsoleSink(min_level=Level(s.log_level)))
        store = live = platform = secrets = None
        if control_plane:
            from runtime.control.live import LiveHub
            from runtime.control.store import EventStore
            await ensure_schema()
            store, live = EventStore(), LiveHub()
            bus.subscribe(store)
            bus.subscribe(live)
            platform, secrets = await _platform_store(s)
        await bus.start()

        mcp_cfg = s.mcp_server_config()
        if platform is not None:
            try:
                mcp_cfg = await all_mcp_config(platform, s, secrets) or mcp_cfg
            except Exception as e:
                log.warning("MCP servers not read from the database (%r) — using .env", e)
        backend = _make_backend(s, mcp_cfg)
        await backend.start()
        try:
            await get_reference()           # hospitals / locations into memory
        except Exception as e:              # the agent still works (hospital search fails gracefully)
            log.warning("reference data not loaded: %r", e)

        loader = AgentLoader(platform, backend, s, secrets=secrets)
        try:
            agent = await loader.for_call()
        except Exception as e:
            log.error("default agent not loaded from the database (%r) — running the repo copy", e)
            loader.store = None
            agent = await loader.for_call()
        if s.provider_override:
            log.warning("PROVIDER_OVERRIDE=%s — every agent uses it instead of its providers", s.provider_override)
        log.info("default agent: %s (%s)", agent.name,
                 f"release v{agent.version}" if agent.version else "repo copy")
        runtime = cls(settings=s, bus=bus, backend=backend, loader=loader, agent=agent, store=store, live=live,
                      platform=platform, secrets=secrets)
        if platform is not None:
            await runtime._start_sync()
        await runtime.warm()
        return runtime

    async def _start_sync(self) -> None:
        from runtime.platform.sync import ClusterSync
        s = self.settings
        try:
            self.sync = ClusterSync(s.database_url.get_secret_value(), ssl=s.database_ssl,
                                    on_config=self._on_config, on_control=self._on_control, on_live=self._on_live,
                                    share_live=s.workers > 1)
            if self.sync.share_live:
                self.bus.subscribe(self.sync)          # this worker's (redacted) events → the other live hubs
            await self.sync.start()
        except Exception as e:
            log.warning("cluster sync not started (%r) — changes reach other workers only after a restart", e)
            self.sync = None

    async def warm(self) -> None:
        """Open provider connections before the first call (cold requests measured at 1–10 s)."""
        from runtime.providers.groq_provider import _client
        if not hasattr(self.llm.settings, "api_key"):
            return
        try:
            await _client(self.llm.settings.api_key, 10).models.list()
        except Exception as e:
            log.warning("Groq warm-up failed: %r", e)

    async def close(self) -> None:
        if self.sync is not None:
            await self.sync.stop()
        if hasattr(self.backend, "close"):
            await self.backend.close()
        await self.bus.stop()
        if self.store:
            await self.store.close()


def _make_backend(s: Settings, mcp_cfg: dict):
    if s.tools_mode == "mock":
        return HybridMCP(None, echo=log.info)
    if s.tools_mode == "hybrid":
        return HybridMCP(MCPPool(mcp_cfg), live_auth=s.hybrid_live_auth, live_booking=s.hybrid_live_booking,
                         echo=log.info)
    return MCPPool(mcp_cfg)


async def _platform_store(s: Settings):
    """The platform tables + secret store, seeded on first start (and synced with the repo in development)."""
    from runtime.platform.secrets import Cipher, Secrets
    from runtime.platform.seed import seed
    try:
        platform = PgStore()
        secrets = Secrets(platform, Cipher(s.master_key.get_secret_value() if s.master_key else None), s)
        async with platform.advisory_lock("platform-seed"):    # N workers start together: one seeds at a time
            report = await seed(platform, s, repo_sync=s.platform_repo_sync, secrets=secrets)
        if any(report.get(k) for k in ("agent_created", "releases", "materialized")) or                 (report.get("secrets") or {}).get("imported"):
            log.info("platform: %s", {k: v for k, v in report.items() if v})
        return platform, secrets
    except Exception as e:
        log.warning("platform tables unavailable (%r) — running the repo copy of the agent", e)
        return None, None


async def ensure_schema() -> None:
    """Apply db/schema.sql (idempotent)."""
    from runtime.config import ROOT_DIR
    from runtime.data.db import get_pool
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute((ROOT_DIR / "db" / "schema.sql").read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("schema not applied: %r", e)


__all__ = ["MockMCP", "Runtime"]
