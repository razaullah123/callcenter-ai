"""Structured event schema. Every harness/pipeline action is one Event.

Logging, metrics, the live console and evals all consume the same stream, so the
event types below are the contract between the runtime and everything that observes it.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(StrEnum):
    # call lifecycle
    CALL_START = "call.start"
    CALL_END = "call.end"
    # audio / speech
    VAD_SPEECH_START = "vad.speech_start"
    VAD_SPEECH_STOP = "vad.speech_stop"
    STT_RESULT = "stt.result"
    TTS_START = "tts.start"
    TTS_FIRST_BYTE = "tts.first_byte"
    TTS_END = "tts.end"
    INTERRUPT = "interrupt"
    # agent turn
    TURN_START = "turn.start"
    TURN_END = "turn.end"
    LLM_REQUEST = "llm.request"
    LLM_FIRST_TOKEN = "llm.first_token"
    LLM_END = "llm.end"
    LLM_HEDGE = "llm.hedge"            # a slow first response: a backup request was raced
    AGENT_SAY = "agent.say"
    # tools
    TOOL_START = "tool.start"
    TOOL_END = "tool.end"
    TOOL_ERROR = "tool.error"
    # skills
    SKILL_ENTER = "skill.enter"
    SKILL_EXIT = "skill.exit"
    STEP_TRANSITION = "step.transition"
    SLOT_SET = "slot.set"
    # policy / control
    POLICY_BLOCK = "policy.block"
    HANDOFF = "handoff"
    AGENT_TRANSFER = "agent.transfer"   # the call moved to another agent (flow "transfer agent" node)
    ERROR = "error"


class Level(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


LEVEL_ORDER = {Level.DEBUG: 10, Level.INFO: 20, Level.WARNING: 30, Level.ERROR: 40}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    ts: datetime = Field(default_factory=_now)
    type: EventType
    level: Level = Level.INFO
    call_id: str | None = None
    turn_id: int | None = None
    skill: str | None = None
    step: str | None = None
    latency_ms: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)
