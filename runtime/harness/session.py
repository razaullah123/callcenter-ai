"""Per-call session state. Everything the harness knows about the call lives here.

PHI (patient records found during lookup) is kept in `auth.candidates` and never sent to the LLM.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from runtime.providers.base import Message

from .nlu.gender import GenderResolver
from .nlu.lang import LanguageTracker


@dataclass
class AuthState:
    mobile_no: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)   # patient records (PHI) — never to the LLM
    patient_id: int | None = None
    first_name: str | None = None
    full_name: str | None = None       # for "Am I speaking to …?" after verification
    verified: bool = False
    required: bool = True              # False: this agent doesn't verify callers (verified from the start)
    identity_confirmed: bool = False   # the caller said yes to "Am I speaking to <name>?"
    otp_sent: bool = False
    otp_channel: str | None = None
    otp_attempts: int = 0
    match_attempts: int = 0
    lookup_attempts: int = 0

    @property
    def stage(self) -> str:
        if self.verified:
            return "verified"
        if self.otp_sent:
            return "awaiting_otp"
        if self.patient_id is not None:
            return "send_otp"
        if len(self.candidates) > 1:
            return "awaiting_dob_and_name"
        return "awaiting_mobile"


@dataclass
class PendingAction:
    """A write the caller must confirm (readback mode). Executed by the harness on "yes"."""
    tool: str
    args: dict[str, Any]
    turn_id: int


# The skill that verifies the caller (the harness drives it by tool roles: identity.lookup / send_code / verify_code).
VERIFY_SKILL = "authenticate"


@dataclass
class Session:
    call_id: str
    ani: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    language: LanguageTracker = field(default_factory=LanguageTracker)
    gender: GenderResolver = field(default_factory=GenderResolver)
    auth: AuthState = field(default_factory=AuthState)
    active_skill: str = VERIFY_SKILL
    flow_skill: str | None = None        # set when one skill's flow runs the whole call (knob main_flow)
    pending_intent: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)          # booking choices (project_id, clinic_id, ...)
    memory: dict[str, Any] = field(default_factory=dict)         # skill scratch space (e.g. last clinic list)
    history: list[Message] = field(default_factory=list)
    turn_id: int = 0
    last_reply: str | None = None        # "yes" / "no" / None for the latest caller utterance
    last_user_text: str = ""
    pending_action: PendingAction | None = None
    tool_failures: int = 0
    handoff: dict[str, Any] | None = None
    ended: bool = False

    @property
    def language_id(self) -> int:
        return self.language.language_id
