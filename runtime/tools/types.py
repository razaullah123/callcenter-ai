from dataclasses import dataclass, field
from typing import Any, Literal

ToolKind = Literal["read", "write", "send"]
ToolSource = Literal["mcp", "local"]
ConfirmMode = Literal["none", "affirm", "readback"]


@dataclass(frozen=True)
class ToolDef:
    """A tool as the harness sees it: catalog policy + input schema."""
    name: str
    skill: str
    kind: ToolKind
    source: ToolSource
    description: str
    input_schema: dict[str, Any]
    cache_ttl: float = 0          # seconds; read tools only
    idempotent: bool = False      # write tools: never execute the same call twice per session
    timeout_s: float | None = None
    confirm: ConfirmMode = "none"


@dataclass
class ToolResult:
    ok: bool
    data: Any = None              # full parsed result (kept in session state / logs)
    content: str = ""             # compact text given back to the LLM
    error: str | None = None
    latency_ms: float = 0.0
    cached: bool = False


@dataclass
class ToolContext:
    """What the executor needs from the call session."""
    call_id: str
    language_id: int = 1          # 1 = Arabic, 2 = English
    patient_id: int | None = None
    allowed_tools: set[str] | None = None   # active skill's tools; None = no restriction
    extra: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    """Error that is reported back to the LLM as a tool result (not raised to the caller)."""
