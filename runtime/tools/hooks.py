"""Named hooks: tool- or skill-specific logic that can't be declared in a policy (parsing one hospital system's
result, grounding checks, prefetch, date / time extraction for a booking flow).

Hooks live in packs (runtime/packs/…), registered by name, and are referenced from configuration:

    tool policy     hooks: [hmg.offered_slots]        phases: pre (check / rewrite args), post (bookkeeping on the
                                                      result), after (engine side effects: prefetch)
    SKILL.md        turn_hooks: [hmg.booking]         per turn while the skill is active: what detail the caller's
                                                      reply may hold (awaits), parse hints, reactions to the agent's
                                                      own words (on_say / after_text)

The harness only knows the names in configuration, so an agent's behaviour is changed from the console; code
changes are needed only to add a new hook to a pack.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .types import ToolContext, ToolDef, ToolResult


@dataclass
class HookCall:
    phase: str                          # "pre" | "post" | "after"
    tool: ToolDef
    args: dict[str, Any]
    session: Any                        # harness Session
    result: ToolResult | None = None    # post / after
    ctx: ToolContext | None = None
    agent: Any = None                   # harness Agent (after: prefetch through agent.executor)


ToolHook = Callable[[HookCall], dict[str, Any] | None]

TOOL_HOOKS: dict[str, dict[str, ToolHook]] = {}      # name → {phase: fn}
TURN_HOOKS: dict[str, Any] = {}                     # name → object with optional awaits / hints / on_say / after_text


def tool_hook(name: str, *phases: str):
    """Register `fn` under `name` for the given phases (default: post)."""
    def deco(fn: ToolHook) -> ToolHook:
        for phase in phases or ("post",):
            TOOL_HOOKS.setdefault(name, {})[phase] = fn
        return fn
    return deco


def turn_hook(name: str):
    def deco(cls):
        TURN_HOOKS[name] = cls()
        return cls
    return deco


def run_tool_hooks(phase: str, tool: ToolDef, args: dict[str, Any], session: Any, **kw) -> dict[str, Any]:
    """Run the tool's hooks for `phase`; pre hooks may return replacement args (or raise ToolError)."""
    for name in tool.hooks:
        fn = TOOL_HOOKS.get(name, {}).get(phase)
        if fn is None:
            continue
        out = fn(HookCall(phase, tool, args, session, **kw))
        if phase == "pre" and isinstance(out, dict):
            args = out
    return args


def turn_hooks(names: list[str] | tuple[str, ...]) -> list[Any]:
    return [TURN_HOOKS[n] for n in names if n in TURN_HOOKS]


def unknown_hooks(tool_hooks: set[str] = frozenset(), turn: set[str] = frozenset()) -> list[str]:
    load_packs()
    return sorted(set(tool_hooks) - set(TOOL_HOOKS)) + sorted(set(turn) - set(TURN_HOOKS))


def load_packs() -> None:
    from runtime import packs  # noqa: F401 — registers the packs' hooks
