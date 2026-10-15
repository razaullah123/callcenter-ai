"""Step machine for a skill (skills/<name>/flow.yaml).

The current step is *derived* from session state, never stored: the first step whose conditions
hold and whose `until` slots are not all filled. Slots are filled by `infer` rules from the tools
the LLM calls (e.g. calling mssql_get_clinics_for_project(projectId=12) means hospital 12 was chosen),
so moving through the flow costs no extra LLM round trips.

flow.yaml:
  common_tools: [...]                 # available in every step
  reset: {slot: [dependent slots]}    # a changed slot clears the ones that depended on it
  infer:
    <tool>:
      on: {"result.status": "match"}  # optional condition
      set: {slot: <expr>}             # args.X | result.a.b | result.*key (deep, case-insensitive) | =literal
  steps:
    - id: hospital
      stage: awaiting_mobile          # optional: auth stage must equal
      requires: [slot]                # optional: slots that must be set
      until: [slot]                   # step is done when all are set
      tools: [...]
      auto_call:                      # run by the harness when the step is active (no LLM hop)
        - {tool: mssql_get_clinics_for_project, args: {projectId: slots.project_id}}
      instructions: |
        ...
"""

import functools
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


log = logging.getLogger(__name__)


@dataclass
class Step:
    id: str
    instructions: str = ""
    tools: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    until: list[str] = field(default_factory=list)
    stage: str | None = None
    auto_call: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Flow:
    steps: list[Step]
    common_tools: list[str] = field(default_factory=list)
    infer: dict[str, dict[str, Any]] = field(default_factory=dict)
    reset: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Flow":
        return cls.parse(path.read_text(encoding="utf-8"))

    @classmethod
    def parse(cls, text: str) -> "Flow":
        cfg = yaml.safe_load(text) or {}
        steps = [Step(id=s["id"], instructions=(s.get("instructions") or "").strip(), tools=s.get("tools", []),
                      requires=s.get("requires", []), until=s.get("until", []), stage=s.get("stage"),
                      auto_call=s.get("auto_call", []))
                 for s in cfg.get("steps", [])]
        return cls(steps, cfg.get("common_tools", []), cfg.get("infer", {}) or {}, cfg.get("reset", {}) or {})

    def all_tools(self) -> set[str]:
        tools = set(self.common_tools)
        for s in self.steps:
            tools |= set(s.tools) | {a["tool"] for a in s.auto_call}
        return tools

    def auto_calls(self, slots: dict[str, Any], stage: str | None = None,
                   parsed: dict[str, Any] | None = None) -> list[tuple[str, dict[str, Any], bool]]:
        """Tool calls the harness makes itself for the current step: (tool, args, blocking). Args are evaluated
        against slots and this turn's deterministic parses (parsed.mobile, parsed.code, …)."""
        step = self.current(slots, stage)
        out = []
        for spec in step.auto_call if step else []:
            ctx = {"slots": slots, "parsed": parsed or {}}
            args = {k: _eval(v, ctx) for k, v in (spec.get("args") or {}).items()}
            if all(v is not None for v in args.values()):
                out.append((spec["tool"], args, bool(spec.get("blocking", False))))
        return out

    def current(self, slots: dict[str, Any], stage: str | None = None) -> Step | None:
        for step in self.steps:
            if step.stage and step.stage != stage:
                continue
            if any(not _filled(slots.get(r)) for r in step.requires):
                continue
            if step.until and all(_filled(slots.get(u)) for u in step.until):
                continue
            return step
        return None

    def apply(self, tool: str, args: dict[str, Any], result: Any, ok: bool, slots: dict[str, Any]) -> dict[str, Any]:
        """Update slots from a tool call; returns the slots that changed."""
        rule = self.infer.get(tool)
        if not rule or not ok:
            return {}
        ctx = {"args": args or {}, "result": result}
        for path, expected in (rule.get("on") or {}).items():
            if _get(ctx, path) != expected:
                return {}
        changed = {}
        for slot, expr in (rule.get("set") or {}).items():
            value = _eval(expr, ctx)
            if value is None or slots.get(slot) == value:
                continue
            for dependent in self.reset.get(slot, []):
                slots.pop(dependent, None)
            slots[slot] = changed[slot] = value
        return changed


def _filled(v: Any) -> bool:
    return v not in (None, "", [], {}, False)


def _eval(expr: Any, ctx: dict[str, Any]) -> Any:
    if not isinstance(expr, str):
        return expr
    if is_template(expr):
        return render(expr, ctx)
    if _JSONPATH.match(expr):                                     # $.data.items[0].name → result.data.items.0.name
        return _get(ctx, jsonpath_to_path(expr))
    if expr.startswith("="):
        literal = expr[1:]
        return {"true": True, "false": False}.get(literal, literal)
    return _get(ctx, expr)


_JSONPATH = re.compile(r"^\$(\.|\[|$)")


def jsonpath_to_path(expr: str) -> str:
    """A JSONPath ($.a.b[0].c, $['a'], or $ alone) as one of our dotted paths on the tool result (result.a.b.0.c)."""
    p = re.sub(r"\[\s*['\"]?([^\]'\"]+?)['\"]?\s*\]", r".\1", expr[1:])
    return "result" + (p if p.startswith(".") or not p else "." + p)


def _get(ctx: Any, path: str) -> Any:
    cur = ctx
    for part in path.split("."):
        if part.startswith("*"):
            return _deep_find(cur, part[1:].lower())
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _deep_find(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == key and v not in (None, ""):
                return v
        for v in obj.values():
            if (found := _deep_find(v, key)) is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            if (found := _deep_find(v, key)) is not None:
                return found
    return None


# ---------------------------------------------------------------- templates

# Values and node instructions may be Jinja templates over the call's variables — "{{ patient_name }}",
# "{{ '2' if lang == 'en' else '1' }}", "{% if symptom %}…{% endif %}" (flows imported from Hamsa use them a lot).
# Sandboxed; an unknown name is empty (and `x|default(…)` / `(a|default({})).b` work).

def is_template(text: str) -> bool:
    return "{{" in text or "{%" in text


_FALLBACK = re.compile(r"\{\{\s*([^{}|]+?)\s*\|\|\s*([^{}]+?)\s*\}\}")


def _fallback_sugar(text: str) -> str:
    """Hamsa's `{{ var || 'default' }}` as Jinja: the default applies when the value is missing or empty."""
    return _FALLBACK.sub(lambda m: "{{ (" + m.group(1) + ") | default(" + m.group(2) + ", true) }}", text)


@functools.lru_cache(maxsize=2048)
def _template(text: str):
    from jinja2 import ChainableUndefined
    from jinja2.sandbox import SandboxedEnvironment
    return SandboxedEnvironment(undefined=ChainableUndefined, autoescape=False).from_string(_fallback_sugar(text))


def render(text: str, ctx: dict[str, Any]) -> str:
    """`text` rendered with the slots as plain names (plus `slots`, `parsed` and any other ctx keys). A template
    error leaves the text as it was (logged) — a typo in a prompt must not break the call."""
    slots = ctx.get("slots") or {}
    try:
        return _template(text).render({**slots, **ctx})
    except Exception as e:                                    # noqa: BLE001 — any template error
        log.warning("template error %r in %.80r", e, text)
        return text
