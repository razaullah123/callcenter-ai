"""Flow graphs: a skill's conversation as nodes and edges (what the console canvas edits, Phase 12.5 / 12.6).

flow.yaml (graph form):

  start: hospital                       # first node (default: the first in the list)
  common_tools: [...]                   # available in every node
  infer: {...}                          # slots filled from the tools the model calls (see runtime.skills.flow)
  reset: {slot: [dependent slots]}      # a changed slot clears the ones that depend on it
  variables:                            # typed values the caller gives, extracted from their words by the LLM
    date_pref: {type: string, enum: [earliest, specific_day], description: "earliest or a specific day"}
  nodes:
    - {id: hospital, type: conversation, instructions: "...", tools: [...], auto_call: [...], extract: [date_pref],
       position: {x: 0, y: 0}}          # position: for the canvas only
    - {id: route, type: router}         # only chooses an edge
    - {id: remember, type: set, set: {tries: "=1"}}
    - {id: lookup, type: tool, tool: some_tool, args: {id: slots.order_id}}   # the platform calls it; on success / failure
    - {id: human, type: transfer, reason: "wants a person"}
    - {id: bye, type: end, say: {ar: "...", en: "..."}}
    - {id: other, type: skill, skill: send_info}                             # continue in another skill
  edges:                                # first matching edge wins: a node's own edges, then the global ("*") ones
    - {from: hospital, to: symptoms, when: {filled: [project_id]}}
    - {from: route, to: book, when: {equals: {intent: book}}}
    - {from: route, to: other}          # no condition: always (put it last)
    - {from: lookup, to: found, result: success}           # (or on: success)
    - {from: "*", to: human, when: {llm: "the caller asks for a human agent"}}

Conditions: filled / empty (lists of slots), equals ({slot: value}), stage (the caller-verification stage),
llm (a short classifier call on the caller's latest words — no paragraph-long edge texts), all / any (lists).

Step flows (the older `steps:` form) are converted into graphs with one global edge per step ("the first step whose
conditions hold"), so they behave exactly as before.
"""

from dataclasses import dataclass, field
from typing import Any

import yaml

from .flow import Flow, _eval, _filled

NODE_TYPES = ("conversation", "router", "set", "tool", "transfer", "end", "skill")
ACTION_TYPES = ("tool", "transfer", "end", "skill")     # the harness performs these when the node is reached
DONE = "__done__"
MAX_MOVES = 25


@dataclass
class Node:
    id: str
    type: str = "conversation"
    instructions: str = ""
    tools: list[str] = field(default_factory=list)
    auto_call: list[dict[str, Any]] = field(default_factory=list)
    extract: list[str] = field(default_factory=list)
    set: dict[str, Any] = field(default_factory=dict)
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    say: dict[str, str] = field(default_factory=dict)
    skill: str | None = None
    position: dict[str, float] | None = None


@dataclass
class Edge:
    source: str
    target: str
    when: dict[str, Any] | None = None
    on: str | None = None               # tool nodes: success | failure


@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge]
    start: str
    common_tools: list[str] = field(default_factory=list)
    infer: dict[str, dict[str, Any]] = field(default_factory=dict)
    reset: dict[str, list[str]] = field(default_factory=dict)
    variables: dict[str, dict[str, Any]] = field(default_factory=dict)
    converted: bool = False             # made from a step flow

    # ---------------------------------------------------------------- building

    @classmethod
    def parse(cls, text: str) -> "Graph":
        cfg = yaml.safe_load(text) or {}
        return cls.from_steps(Flow.parse(text)) if "steps" in cfg and "nodes" not in cfg else cls.from_dict(cfg)

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> "Graph":
        nodes = {}
        for n in cfg.get("nodes") or []:
            node = Node(id=str(n["id"]), type=n.get("type", "conversation"),
                        instructions=(n.get("instructions") or "").strip(), tools=list(n.get("tools") or []),
                        auto_call=list(n.get("auto_call") or []), extract=list(n.get("extract") or []),
                        set=dict(n.get("set") or {}), tool=n.get("tool"), args=dict(n.get("args") or {}),
                        reason=n.get("reason", ""), say=dict(n.get("say") or {}), skill=n.get("skill"),
                        position=n.get("position"))
            nodes[node.id] = node
        # `on:` unquoted in YAML is the boolean true — accept that, and `result:` as a clearer name
        edges = [Edge(str(e["from"]), str(e["to"]), e.get("when"), e.get("result") or e.get("on") or e.get(True))
                 for e in cfg.get("edges") or []]
        start = cfg.get("start") or (next(iter(nodes)) if nodes else "")
        return cls(nodes, edges, start, list(cfg.get("common_tools") or []), dict(cfg.get("infer") or {}),
                   dict(cfg.get("reset") or {}), dict(cfg.get("variables") or {}))

    @classmethod
    def from_steps(cls, flow: Flow) -> "Graph":
        """Step flow → graph: one global edge per step, in order ("the first step whose stage / requires hold and
        whose `until` isn't done yet"), then a quiet end node — exactly the old derived-step behaviour."""
        nodes = {s.id: Node(id=s.id, instructions=s.instructions, tools=list(s.tools), auto_call=list(s.auto_call))
                 for s in flow.steps}
        edges = []
        for s in flow.steps:
            cond: list[dict[str, Any]] = []
            if s.stage:
                cond.append({"stage": s.stage})
            if s.requires:
                cond.append({"filled": list(s.requires)})
            if s.until:
                cond.append({"not_all_filled": list(s.until)})
            edges.append(Edge("*", s.id, {"all": cond} if cond else None))
        nodes[DONE] = Node(id=DONE)
        edges.append(Edge("*", DONE))
        start = flow.steps[0].id if flow.steps else DONE
        return cls(nodes, edges, start, list(flow.common_tools), dict(flow.infer), dict(flow.reset), converted=True)

    def to_dict(self) -> dict[str, Any]:
        """The graph as flow.yaml / canvas JSON."""
        def node(n: Node) -> dict[str, Any]:
            d: dict[str, Any] = {"id": n.id, "type": n.type}
            for k in ("instructions", "tools", "auto_call", "extract", "set", "tool", "args", "reason", "say", "skill",
                      "position"):
                if (v := getattr(n, k)) not in (None, "", [], {}):
                    d[k] = v
            return d
        out: dict[str, Any] = {"start": self.start, "nodes": [node(n) for n in self.nodes.values()],
                               "edges": [{"from": e.source, "to": e.target,
                                          **({"when": e.when} if e.when else {}), **({"on": e.on} if e.on else {})}
                                         for e in self.edges]}
        for k in ("common_tools", "infer", "reset", "variables"):
            if v := getattr(self, k):
                out[k] = v
        return out

    # ---------------------------------------------------------------- checks

    def errors(self) -> list[str]:
        e = []
        if self.start not in self.nodes:
            e.append(f"start node {self.start!r} does not exist")
        for n in self.nodes.values():
            if n.type not in NODE_TYPES:
                e.append(f"node {n.id}: unknown type {n.type!r}")
            if n.type == "tool" and not n.tool:
                e.append(f"node {n.id}: a tool node needs `tool`")
            if n.type == "skill" and not n.skill:
                e.append(f"node {n.id}: a skill node needs `skill`")
            if unknown := [v for v in n.extract if v not in self.variables]:
                e.append(f"node {n.id}: extracts undeclared variables {unknown}")
        for edge in self.edges:
            if edge.source != "*" and edge.source not in self.nodes:
                e.append(f"edge from unknown node {edge.source!r}")
            if edge.target not in self.nodes:
                e.append(f"edge to unknown node {edge.target!r}")
            if edge.on and edge.on not in ("success", "failure"):
                e.append(f"edge {edge.source}→{edge.target}: `on` must be success or failure")
        return e

    def all_tools(self) -> set[str]:
        tools = set(self.common_tools)
        for n in self.nodes.values():
            tools |= set(n.tools) | {a["tool"] for a in n.auto_call} | ({n.tool} if n.tool else set())
        return tools

    def llm_conditions(self, node_id: str) -> list[str]:
        """Classifier questions on the edges that can leave `node_id` (its own and the global ones)."""
        out: list[str] = []
        for e in self._edges_from(node_id):
            _collect_llm(e.when, out)
        return list(dict.fromkeys(out))

    # ---------------------------------------------------------------- running

    def current(self, state: dict[str, Any], slots: dict[str, Any], stage: str | None = None,
                facts: dict[str, Any] | None = None) -> Node:
        """Follow matching edges from the current node until none applies (runs `set` nodes on the way); `state`
        is the skill's graph state in the session: {"node", "llm": {question: bool}, "outcome": {node: ok}}."""
        node_id = state.get("node") or self.start
        for _ in range(MAX_MOVES):
            node = self.nodes.get(node_id) or self.nodes[self.start]
            nxt = self._next(node, state, {**slots, **(facts or {})}, stage)
            if nxt is None or nxt == node.id:
                break
            node_id = nxt
            entered = self.nodes[node_id]
            (state.get("outcome") or {}).pop(node_id, None)      # a tool node entered again runs again
            if entered.type == "set":
                for slot, expr in entered.set.items():
                    if expr is None:
                        slots.pop(slot, None)                    # `slot: null` forgets it
                    else:
                        slots[slot] = _eval(expr, {"slots": slots})
        state["node"] = node_id
        return self.nodes[node_id]

    def _edges_from(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id] + [e for e in self.edges if e.source == "*"]

    def _next(self, node: Node, state: dict[str, Any], slots: dict[str, Any], stage: str | None) -> str | None:
        outcome = (state.get("outcome") or {}).get(node.id)
        if node.type == "tool" and outcome is None:
            return None                              # waits for the harness to run the tool
        ran = state.get("ran") or {}
        for e in self._edges_from(node.id):
            target = self.nodes.get(e.target)
            if target is not None and target.type == "tool" and target.id != node.id \
                    and ran.get(target.id) is not None and ran.get(target.id) == slots.get("_turn"):
                continue                                 # a tool node runs at most once per turn
            if e.on is not None:
                if e.source != node.id or outcome is None or (e.on == "success") != bool(outcome):
                    continue
            if _holds(e.when, slots, stage, state.get("llm") or {}):
                return e.target
        return None

    def auto_calls(self, node: Node, slots: dict[str, Any], parsed: dict[str, Any] | None
                   ) -> list[tuple[str, dict[str, Any], bool]]:
        out = []
        for spec in node.auto_call:
            ctx = {"slots": slots, "parsed": parsed or {}}
            args = {k: _eval(v, ctx) for k, v in (spec.get("args") or {}).items()}
            if all(v is not None for v in args.values()):
                out.append((spec["tool"], args, bool(spec.get("blocking", False))))
        return out

    def apply(self, tool: str, args: dict[str, Any], result: Any, ok: bool, slots: dict[str, Any]) -> dict[str, Any]:
        return Flow([], infer=self.infer, reset=self.reset).apply(tool, args, result, ok, slots)


def _collect_llm(cond: Any, out: list[str]) -> None:
    if not isinstance(cond, dict):
        return
    if isinstance(cond.get("llm"), str):
        out.append(cond["llm"])
    for k in ("all", "any"):
        for c in cond.get(k) or []:
            _collect_llm(c, out)


def _holds(cond: dict[str, Any] | None, slots: dict[str, Any], stage: str | None, llm: dict[str, bool]) -> bool:
    if not cond:
        return True
    for key, v in cond.items():
        if key == "all" and not all(_holds(c, slots, stage, llm) for c in v or []):
            return False
        if key == "any" and not any(_holds(c, slots, stage, llm) for c in v or []):
            return False
        if key == "filled" and not all(_filled(slots.get(s)) for s in v):
            return False
        if key == "empty" and any(_filled(slots.get(s)) for s in v):
            return False
        if key == "not_all_filled" and all(_filled(slots.get(s)) for s in v):
            return False
        if key == "equals" and any(str(slots.get(k)) != str(val) for k, val in (v or {}).items()):
            return False
        if key == "stage" and stage != v:
            return False
        if key == "llm" and not llm.get(v, False):
            return False
    return True


__all__ = ["ACTION_TYPES", "DONE", "NODE_TYPES", "Edge", "Graph", "Node"]
