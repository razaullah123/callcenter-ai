"""Process-wide runtime: everything shared by all calls (connection pools, providers, skills, logs).

Per-call objects (Session, Agent, VoiceCall) are cheap and created on each call.
"""

import logging
from dataclasses import dataclass, field

from runtime.config import Settings, get_settings
from runtime.data.reference import get_reference
from runtime.events import ConsoleSink, EventBus, EventType, JsonlSink, Level
from runtime.harness.redact import redact_event
from runtime.providers import LLMProvider, STTProvider, TTSProvider
from runtime.skills import FileSkillSet
from runtime.tools import MCPPool, MockMCP, ToolExecutor
from runtime.tools.factory import build_tooling
from runtime.tools.hybrid import HybridMCP

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    bus: EventBus
    executor: ToolExecutor
    skills: FileSkillSet
    providers: "ProviderSet"
    store: "EventStore | None" = None
    live: "LiveHub | None" = None
    calls: dict = field(default_factory=dict)   # call_id → VoiceCall running in this process (console "End call")

    # Current providers (a call snapshots `providers` once at its start).
    @property
    def llm(self) -> LLMProvider:
        return self.providers.llm

    @property
    def stt(self) -> STTProvider:
        return self.providers.stt

    @property
    def tts(self) -> TTSProvider:
        return self.providers.tts

    async def apply_config(self, version: int, config: dict) -> None:
        """Activate a config version: new providers for new calls, runtime knobs applied immediately."""
        from runtime.control.config_store import build
        from runtime.voice.phrases import PhraseCache
        ps = build(version, config)
        ps.phrases = PhraseCache(ps.tts)
        await ps.phrases.warm()
        for k, v in (config.get("runtime") or {}).items():
            if hasattr(self.settings, k) and v not in (None, ""):
                setattr(self.settings, k, type(getattr(self.settings, k))(v)
                        if getattr(self.settings, k) is not None else v)
        self.providers = ps
        self.bus.bind().emit(EventType.SLOT_SET, field="config_version", value=version)

    @classmethod
    async def create(cls, settings: Settings | None = None, *, console_log: bool = False,
                     control_plane: bool = True) -> "Runtime":
        s = settings or get_settings()
        bus = EventBus()
        bus.add_redactor(redact_event)
        bus.subscribe(JsonlSink(s.log_dir))
        if console_log:
            bus.subscribe(ConsoleSink(min_level=Level(s.log_level)))
        store = live = None
        if control_plane:
            from runtime.control.live import LiveHub
            from runtime.control.store import EventStore
            await ensure_schema()
            store, live = EventStore(), LiveHub()
            bus.subscribe(store)
            bus.subscribe(live)
        await bus.start()

        if s.tools_mode == "mock":
            backend = HybridMCP(None, echo=log.info)
        elif s.tools_mode == "hybrid":
            backend = HybridMCP(MCPPool(s.mcp_server_config()), live_auth=s.hybrid_live_auth,
                                live_booking=s.hybrid_live_booking, echo=log.info)
        else:
            backend = MCPPool(s.mcp_server_config())
        executor = await build_tooling(backend)
        try:
            await get_reference()           # hospitals / locations into memory
        except Exception as e:              # the agent still works (hospital search fails gracefully)
            log.warning("reference data not loaded: %r", e)

        from runtime.control import config_store
        try:
            version, config = await config_store.load_active(s)
        except Exception as e:                  # no DB: run on .env defaults
            log.warning("agent config not loaded (%r) — using .env defaults", e)
            version, config = 0, config_store.default_config(s)
        if s.provider_override:                 # e.g. load tests: every kind uses the override provider
            config = {**config, **{k: {"provider": s.provider_override, "settings": {}}
                                   for k in ("stt", "llm", "tts")}}
            log.warning("PROVIDER_OVERRIDE=%s — not using the saved provider config", s.provider_override)
        runtime = cls(settings=s, bus=bus, executor=executor, skills=FileSkillSet(executor.catalog),
                      providers=config_store.build(version, config), store=store, live=live)
        await runtime.warm()
        return runtime

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
        mcp = self.executor.mcp
        if hasattr(mcp, "close"):
            await mcp.close()
        await self.bus.stop()
        if self.store:
            await self.store.close()


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
