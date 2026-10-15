from dataclasses import dataclass, field
from typing import Any, Literal

ToolKind = Literal["read", "write", "send"]
ToolSource = Literal["mcp", "local", "http", "web"]
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
    # --- what the harness knows about the tool (all from the tool policy; no tool names in harness code)
    role: str | None = None                   # a capability the harness drives itself, e.g. "identity.lookup"
    role_args: dict[str, str] = field(default_factory=dict)   # role parameter → this tool's argument name
    hooks: tuple[str, ...] = ()               # named hooks (runtime.tools.hooks) run before / after the call
    backs: str | None = None                  # the claim a success makes true: booked | confirmed | cancelled | sent
    success_line: str | None = None           # phrase the harness says when the confirmed call succeeds
    http: dict[str, Any] | None = None        # API-request tools: {url, method, headers, body}
    enabled: bool = True                      # inactive tools are not offered to the model and refuse to run
    run_async: bool = False                   # fire-and-forget: the agent gets {"queued": true} at once, the call runs on
    say_start: dict[str, str] | None = None   # {ar, en} line spoken when the call starts (templated: {{args.x}})
    say_done: dict[str, str] | None = None    # {ar, en} line spoken when it succeeds


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
