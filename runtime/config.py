"""Typed settings loaded from environment / `.env`.

These are process-level defaults. Per-call provider choices come from the control plane's
config snapshot (Phase 9) and fall back to these values.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # Secret store: encrypts API keys / tokens kept in the database (Fernet key; see .env.example)
    master_key: SecretStr | None = None

    # Groq — defaults for providers that don't set their own (keys move to the secret store; optional here)
    groq_api_key: SecretStr | None = None
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_llm_model: str = "openai/gpt-oss-120b"
    groq_tts_model_ar: str = "canopylabs/orpheus-arabic-saudi"
    groq_tts_model_en: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice_ar: str = "aisha"
    groq_tts_voice_en: str = "hannah"

    # MCP
    mcp_server_url: str | None = None           # seeds the MCP server record (then: console / DB)
    mcp_auth_token: SecretStr | None = None     # seeds the MCP_AUTH_TOKEN secret
    mcp_transport: str = "streamable_http"
    mcp_auth_header: str = "Authorization"
    mcp_auth_scheme: str = "Bearer"

    # Local data (projects / locations tables used by local tools)
    database_url: SecretStr | None = None
    database_ssl: bool = False  # local Docker Postgres; enable for managed databases

    # Embedding service used to build `locations.embedding` (queries must use the same model)
    embedding_url: str | None = None
    embedding_dim: int = 1024

    # Emergency symptoms during a call (chest pain, can't breathe, ...):
    #   advise_and_continue — empathy + one short ER / 997 safety line, then continue booking
    #   empathy_only        — empathy, then continue booking (no safety line) — default (user decision)
    #   stop                — safety message only, no booking
    red_flag_mode: str = "empathy_only"   # user decision: no ER / 997 line; advise_and_continue | stop also exist

    # Tool backend: live (production) | hybrid (live lookups, simulated patient/auth/writes) | mock
    tools_mode: str = "hybrid"
    hybrid_live_auth: bool = False       # hybrid: real patient lookup + OTP
    hybrid_live_booking: bool = False    # hybrid: REAL bookings / confirmations

    # Voice
    voice_end_silence_ms: int = 550      # silence that ends the caller's turn
    voice_barge_in_ms: int = 300         # caller speech needed to interrupt the agent
    voice_barge_in_confirm: bool = True  # transcribe that speech first: interrupt only for real words, not echo / noise
    voice_level_gate_db: float = 12.0    # ignore speech this far below the caller's own level (background voices); 0 = off
    voice_filler_after_s: float = 0.7
    voice_wait_for_user: str = "never"   # who speaks first: never (agent greets) | always (agent waits for the caller) | outbound (waits only on outbound calls)
    voice_interrupt: bool = True         # the caller may interrupt the agent (barge-in); off: the agent always finishes
    voice_vad_threshold: float = 0.5     # speech probability that counts as speech (higher: less sensitive to noise)
    voice_inactivity_s: float = 0.0      # caller silent this long after the agent spoke → "are you still there?"; 0 = off
    call_max_minutes: float = 0.0        # the call ends (with a closing line) after this many minutes; 0 = no limit
    llm_hedge_after_s: float = 2.5
    api_key_rate_per_min: int = 120      # requests an API key may make per minute (per server process)
    public_base_url: str = ""            # the address the IVR / PBX reaches this server on (dial requests carry it), e.g. https://agent.example.com
    batch_live_dial: bool = False         # batch calls: False = nothing is dialed (every recipient is 'simulated'); True = POST to the number's dial URL
    batch_max_concurrent: int = 5         # batch calls in progress at once (all batches together)
    batch_ring_timeout_s: int = 90        # a dialed call that never connects is a no-answer after this
    public_trust_proxy: bool = False     # behind a reverse proxy: take the visitor address for the public link limits from X-Forwarded-For
    # Agents (per release knobs): does the caller have to be verified first, and where does the conversation start?
    require_verification: bool = True    # False: e.g. an information line — no mobile / OTP, starts in entry_skill
    entry_skill: str = "home"            # the skill after verification (or from the start when none is required)
    main_flow: str = ""                  # one skill runs the whole call (verification included), as one flow graph       # no first LLM output after this → race an identical backup request (0 = off)

    # IVR endpoint /ws/voice-pipeline (compatible with the existing IVR integration)
    auth_secret: SecretStr | None = None      # HS256 JWT secret shared with the IVR auth service (AUTH_SECRET)
    source_database_url: SecretStr | None = None  # ONLY for scripts/clone_reference_data.py — never read at run time
    ivr_inbound_rate: int = 8000              # caller audio: raw PCM16 mono
    ivr_outbound_rate: int = 8000             # agent audio WAV pieces (TTS resampled; 0 = keep TTS rate)
    ivr_chunk_ms: int = 300                   # outbound WAV piece size (real-time paced)
    ivr_aec: bool = True                      # WebRTC echo cancellation on the caller's audio
    ivr_barge_in_grace_ms: int = 700          # ignore barge-in right after the agent starts talking (echo onset)
    ivr_min_suppression_ratio: float = 0.5    # below this the "caller speech" is mostly our own echo
    ivr_whitelist: bool = False               # only allow numbers in white_listed_numbers (pilot gating)
    ivr_transfer_destination: str = ""        # default human-agent destination (extension / queue)

    # Process-level provider override (load tests / offline dev): e.g. PROVIDER_OVERRIDE=fake
    provider_override: str | None = None

    # Retention (PDPL): events / transcripts older than this are deleted by the maintenance task
    event_retention_days: int = 90
    audit_retention_days: int = 730           # patient-data access audit
    allow_insecure_live: bool = False         # start in TOOLS_MODE=live despite missing CONSOLE_TOKEN / AUTH_SECRET
    workers: int = 1                          # server processes (~50 concurrent calls each; see docs/deployment.md)

    # Platform (Phase 12): agents, providers, tools and skills live in the database. In development, repo edits to
    # skills/, config/tools.yaml and the default phrases are imported as new versions + releases at start-up.
    platform_repo_sync: bool = True

    # Console / control plane
    console_token: SecretStr | None = None    # if set, /api requires "Authorization: Bearer <token>"
    # invitation emails (optional — without them the console shows the invitation link to copy)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None

    # Runtime
    default_language: str = "ar"
    log_dir: Path = Field(default=ROOT_DIR / "logs")
    log_console: bool = True
    log_level: str = "info"

    def mcp_headers(self) -> dict[str, str]:
        if self.mcp_auth_token is None:
            return {}
        value = f"{self.mcp_auth_scheme} {self.mcp_auth_token.get_secret_value()}".strip()
        return {self.mcp_auth_header: value}

    def mcp_server_config(self) -> dict[str, dict]:
        """Named MCP servers. Tools are namespaced by server name, so more can be added later."""
        if not self.mcp_server_url:
            return {}
        return {
            "hmg_tools": {
                "transport": self.mcp_transport,
                "url": self.mcp_server_url,
                "headers": self.mcp_headers(),
            }
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
