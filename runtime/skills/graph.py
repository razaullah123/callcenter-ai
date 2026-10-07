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
    - {id: lookup, type: tool, tool: some_tool, args: {id: slots.order_id},    # the platform calls it; on success / failure
       outputs: {order_status: result.status}}                                   # result values → slots
    - {id: human, type: transfer, reason: "wants a person"}
    - {id: bye, type: end, say: {ar: "...", en: "..."}}
    - {id: other, type: skill, skill: send_info}                             # continue in another skill
    - {id: voice, type: settings, overrides: {call: {response_delay_ms: 900}, llm: {model: some-model}}}
    - {id: help, type: agent, agent: support-agent, handoff_history: true}   # hand the call to another agent
  edges:                                # first matching edge wins: a node's own edges, then the global ("*") ones
    - {from: hospital, to: symptoms, when: {filled: [project_id]}}
    - {from: route, to: book, when: {equals: {intent: book}}}
    - {from: route, to: other}          # no condition: always (put it last)
    - {from: lookup, to: found, result: success}           # (or on: success)
    - {from: "*", to: human, when: {llm: "the caller asks for a human agent"}}

Conditions: filled / empty (lists of slots; exists / not_exists are the same), equals / ne ({slot: value}),
gt / gte / lt / lte ({slot: number}), contains / not_contains / regex ({slot: text or pattern}), stage (the
caller-verification stage), llm (a short classifier call on the caller's latest words — no paragraph-long edge
texts), replied (true: the caller has spoken since this node was entered), all / any (lists).

  dtmf: "1"   the caller pressed that key (0-9, *, #) — a menu choice, or a global trigger on a `*` edge
Edge options: back (a global edge: return to the node the caller was at once this one is done), confirm (ask the caller
yes / no before taking it; a dict gives the question per language), silent (go there without saying anything).
Node options: conversation `dtmf_capture` {variable, max_digits, end_keys, timeout_s} collects keypad digits into a
variable; `skip_response` (static messages) moves on without waiting for the caller; transfer `destination`,
`transfer_type` warm / cold, `timeout_s`, `headers`; tool `on_error` continue / retry / fail, `retries`, `timeout_s`,
`processing` (said while it runs) and `say` (said after it succeeds).
Settings node: `overrides` {system_prompt, voice {ar, en}, stt_model, llm {model, temperature}, call {interrupt,
response_delay_ms, inactivity_s, min_interruption_ms, vad_threshold}} apply from that node on, until another settings
node changes them. Any conversation node may also set `llm` {model, temperature} for its own replies. Agent node:
`agent` (target agent id), `handoff_history`, `handoff_variables`, `say` (said before the hand-off).

Values (set nodes, tool args) and conversation instructions may be Jinja templates over the slots:
"{{ patient_name }}", "{% if symptom %}…{% endif %}" (see runtime.skills.flow.render).

Step flows (the older `steps:` form) are converted into graphs with one global edge per step ("the first step whose
conditions hold"), so they behave exactly as before.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from runtime.harness.variables import name_problem

from .flow import Flow, _eval, _filled

NODE_TYPES = ("conversation", "router", "set", "tool", "transfer", "end", "skill", "settings", "agent")
ACTION_TYPES = ("tool", "transfer", "end", "skill", "settings", "agent")     # the harness performs these when the node is reached
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
    outputs: dict[str, str] = field(default_factory=dict)     # tool nodes: slot ← path in {"result": data}
    position: dict[str, float] | None = None
    dtmf_capture: dict[str, Any] = field(default_factory=dict)   # conversation: keypad digits → a variable
    skip_response: bool = False         # static message: move on without waiting for the caller
    destination: str = ""               # transfer: number / extension (default: the agent's transfer destination)
    transfer_type: str = ""             # transfer: warm (announce first, default) | cold (connect at once)
    timeout_s: float | None = None      # transfer: ring timeout · tool: longest wait
    headers: dict[str, str] = field(default_factory=dict)        # transfer: SIP headers (values may be templates)
    on_error: str = ""                  # tool: continue (default) | retry | fail (hand the call to a person)
    retries: int = 0                    # tool, on_error retry: extra attempts
    processing: dict[str, str] = field(default_factory=dict)     # tool: said while it runs, per language
    overrides: dict[str, Any] = field(default_factory=dict)      # settings node: what changes from here on
    llm: dict[str, Any] = field(default_factory=dict)            # conversation: model / temperature for this step
    agent: str = ""                      # agent node: the agent that takes the call
    handoff_history: bool = False       # agent node: pass the conversation on
    handoff_variables: bool = False     # agent node: pass the collected values on


@dataclass
class Edge:
    source: str
    target: str
    when: dict[str, Any] | None = None
    on: str | None = None               # tool nodes: success | failure
    back: bool = False                  # global edge: afterwards return to where the caller was
    confirm: Any = None                 # ask yes / no first: True, or {lang: question}
    silent: bool = False                # arrive without saying anything


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
                        outputs=dict(n.get("outputs") or {}), position=n.get("position"),
                        dtmf_capture=dict(n.get("dtmf_capture") or {}), skip_response=bool(n.get("skip_response")),
                        destination=str(n.get("destination") or ""), transfer_type=str(n.get("transfer_type") or ""),
                        timeout_s=n.get("timeout_s"), headers=dict(n.get("headers") or {}),
                        on_error=str(n.get("on_error") or ""), retries=int(n.get("retries") or 0),
                        processing=dict(n.get("processing") or {}), overrides=dict(n.get("overrides") or {}),
                        llm=dict(n.get("llm") or {}), agent=str(n.get("agent") or ""),
                        handoff_history=bool(n.get("handoff_history")), handoff_variables=bool(n.get("handoff_variables")))
            nodes[node.id] = node
        # `on:` unquoted in YAML is the boolean true — accept that, and `result:` as a clearer name
        edges = [Edge(str(e["from"]), str(e["to"]), e.get("when"), e.get("result") or e.get("on") or e.get(True),
                     bool(e.get("back")), e.get("confirm") or None, bool(e.get("silent")))
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
                      "outputs", "position", "dtmf_capture", "skip_response", "destination", "transfer_type", "timeout_s",
                      "headers", "on_error", "retries", "processing", "overrides", "llm", "agent", "handoff_history",
                      "handoff_variables"):
                if (v := getattr(n, k)) not in (None, "", [], {}, False, 0):
                    d[k] = v
            return d
        out: dict[str, Any] = {"start": self.start, "nodes": [node(n) for n in self.nodes.values()],
                               "edges": [{"from": e.source, "to": e.target,
                                          **({"when": e.when} if e.when else {}), **({"on": e.on} if e.on else {}),
                                          **({"back": True} if e.back else {}), **({"confirm": e.confirm} if e.confirm else {}),
                                          **({"silent": True} if e.silent else {})}
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
            e += _node_option_errors(n)
        names = set(self.variables) | {k for n in self.nodes.values() for k in n.set} | {k for n in self.nodes.values() for k in n.outputs}
        names |= {n.dtmf_capture.get("variable") for n in self.nodes.values() if n.dtmf_capture.get("variable")}
        for name in sorted(names):
            if problem := name_problem(name):
                e.append(f"variable {name!r}: the name {problem}")
        for edge in self.edges:
            for key in _values(edge.when, "dtmf"):
                if str(key) not in DTMF_KEYS:
                    e.append(f"edge {edge.source}→{edge.target}: dtmf key {key!r} must be one of 0-9, * or #")
            for pattern in _regexes(edge.when):
                try:
                    re.compile(pattern)
                except re.error as err:
                    e.append(f"edge {edge.source}→{edge.target}: bad regex {pattern!r} ({err})")
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

    def dtmf_keys(self, node_id: str) -> set[str]:
        """Keypad keys that move the call on from `node_id`: its own transitions and the global (\"*\") ones."""
        out: set[str] = set()
        for e in self._edges_from(node_id):
            out |= {str(k) for k in _values(e.when, "dtmf")}
        return out

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
        if not state.get("node"):                                # the flow just started: the start node is entered now
            state["entered"] = (facts or {}).get("_turn")
        for _ in range(MAX_MOVES):
            node = self.nodes.get(node_id) or self.nodes[self.start]
            nxt = self._next(node, state, {**slots, **(facts or {})}, stage)
            if nxt is None or nxt == node.id:
                break
            node_id = nxt
            entered = self.nodes[node_id]
            (state.get("outcome") or {}).pop(node_id, None)      # a tool node entered again runs again
            state["entered"] = (facts or {}).get("_turn")         # for `replied`: the caller speaks after this
            if entered.type == "set":
                for slot, expr in entered.set.items():
                    if expr is None:
                        slots.pop(slot, None)                    # `slot: null` forgets it
                    else:
                        slots[slot] = _eval(expr, {"slots": slots, **(facts or {})})
        state["node"] = node_id
        return self.nodes[node_id]

    def _edges_from(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id] + [e for e in self.edges if e.source == "*"]

    def _next(self, node: Node, state: dict[str, Any], slots: dict[str, Any], stage: str | None) -> str | None:
        outcome = (state.get("outcome") or {}).get(node.id)
        if node.type in ("tool", "settings", "agent") and outcome is None:
            return None                              # waits for the harness to run it
        ran = state.get("ran") or {}
        turn = slots.get("_turn")
        replied = turn is not None and state.get("entered") != turn       # the caller spoke since we got here
        said = state.get("said")
        if state.get("silent") == node.id or (node.skip_response and said and said[0] == node.id):
            replied = True                               # arrived silently / a message that doesn't wait for the caller
        for e in self._edges_from(node.id):
            target = self.nodes.get(e.target)
            if target is not None and target.type == "tool" and target.id != node.id \
                    and ran.get(target.id) is not None and ran.get(target.id) == slots.get("_turn"):
                continue                                 # a tool node runs at most once per turn
            if e.on is not None:
                if e.source != node.id or outcome is None or (e.on == "success") != bool(outcome):
                    continue
            holds = _holds(e.when, slots, stage, state.get("llm") or {}, replied)
            if e.confirm:
                key = f"{e.source}>{e.target}"
                asked = state.get("confirm")
                if asked and asked.get("key") == key:      # we asked the caller: this turn's yes / no is the answer
                    if asked.get("turn") == turn:
                        continue
                    state.pop("confirm", None)
                    if slots.get("_reply") == "yes":
                        return self._fire(e, node, state)
                elif holds:
                    state["confirm"] = {"key": key, "turn": turn}
                    state["ask"] = {"text": e.confirm if isinstance(e.confirm, dict) else None}
                continue
            if holds:
                return self._fire(e, node, state)
        back = state.get("return_to")
        if back and state.get("return_via") == node.id and back in self.nodes and (node.type != "conversation" or replied):
            state.pop("return_to", None)
            state.pop("return_via", None)
            return back                                  # a global node is done: back to where the caller was
        return None

    def _fire(self, e: Edge, node: Node, state: dict[str, Any]) -> str:
        """Take edge `e` out of `node`, with its options (silent arrival, return to this node afterwards)."""
        if e.target == node.id:
            return e.target
        state.pop("silent", None)
        state.pop("return_to", None)
        state.pop("return_via", None)
        if e.silent:
            state["silent"] = e.target
        if e.back and e.source == "*":
            state["return_to"], state["return_via"] = node.id, e.target
        return e.target

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


def _holds(cond: dict[str, Any] | None, slots: dict[str, Any], stage: str | None, llm: dict[str, bool],
           replied: bool = False) -> bool:
    if not cond:
        return True
    for key, v in cond.items():
        if key == "all" and not all(_holds(c, slots, stage, llm, replied) for c in v or []):
            return False
        if key == "any" and not any(_holds(c, slots, stage, llm, replied) for c in v or []):
            return False
        if key == "replied" and bool(v) != replied:
            return False
        if key == "filled" and not all(_filled(slots.get(s)) for s in v):
            return False
        if key == "empty" and any(_filled(slots.get(s)) for s in v):
            return False
        if key == "not_all_filled" and all(_filled(slots.get(s)) for s in v):
            return False
        if key == "equals" and any(str(slots.get(k)) != str(val) for k, val in (v or {}).items()):
            return False
        if key == "ne" and any(str(slots.get(k)) == str(val) for k, val in (v or {}).items()):
            return False
        if key in _NUMERIC and not all(_compare(key, slots.get(k), val) for k, val in (v or {}).items()):
            return False
        if key == "contains" and any(str(val).lower() not in str(slots.get(k) or "").lower() for k, val in (v or {}).items()):
            return False
        if key == "not_contains" and any(str(val).lower() in str(slots.get(k) or "").lower() for k, val in (v or {}).items()):
            return False
        if key == "regex" and not all(_matches(val, slots.get(k)) for k, val in (v or {}).items()):
            return False
        if key == "exists" and not all(_filled(slots.get(s)) for s in v):
            return False
        if key == "not_exists" and any(_filled(slots.get(s)) for s in v):
            return False
        if key == "dtmf" and str(slots.get("_dtmf") or "") != str(v):
            return False
        if key == "stage" and stage != v:
            return False
        if key == "llm" and not llm.get(v, False):
            return False
    return True


_NUMERIC = ("gt", "gte", "lt", "lte")
DTMF_KEYS = set("0123456789*#")


def _number(v: Any) -> float | None:
    try:
        return None if isinstance(v, bool) or v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _compare(op: str, have: Any, want: Any) -> bool:
    """A numeric comparison; a value that isn't a number never satisfies it."""
    a, b = _number(have), _number(want)
    if a is None or b is None:
        return False
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


def _matches(pattern: Any, value: Any) -> bool:
    try:
        return re.search(str(pattern), "" if value is None else str(value)) is not None
    except re.error:
        return False


def _regexes(cond: Any) -> list[str]:
    """Every regex pattern inside a condition (for the graph check)."""
    out: list[str] = []
    if isinstance(cond, dict):
        if isinstance(cond.get("regex"), dict):
            out += [str(p) for p in cond["regex"].values()]
        for k in ("all", "any"):
            for c in cond.get(k) or []:
                out += _regexes(c)
    return out


def _values(cond: Any, key: str) -> list[Any]:
    """Every value of condition `key` inside a (nested) condition."""
    out: list[Any] = []
    if isinstance(cond, dict):
        if key in cond:
            out.append(cond[key])
        for k in ("all", "any"):
            for c in cond.get(k) or []:
                out += _values(c, key)
    return out


_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def _node_option_errors(n: Node) -> list[str]:
    out: list[str] = []
    if n.dtmf_capture:
        c = n.dtmf_capture
        if n.type != "conversation":
            out.append(f"node {n.id}: keypad capture only works on conversation nodes")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(c.get("variable") or "")):
            out.append(f"node {n.id}: keypad capture needs a snake_case variable name")
        if not 1 <= _num(c.get("max_digits", 10)) <= 20:
            out.append(f"node {n.id}: keypad capture max_digits must be 1-20")
        if not 1 <= _num(c.get("timeout_s", 5)) <= 30:
            out.append(f"node {n.id}: keypad capture timeout_s must be 1-30")
        if not set(c.get("end_keys") or ["#"]) <= {"#", "*"}:
            out.append(f"node {n.id}: keypad capture end_keys can only be # and *")
    if n.type == "transfer":
        d = n.destination.strip()
        if d and "{{" not in d and not (_E164.match(d) or (d.isdigit() and len(d) <= 8)):
            out.append(f"node {n.id}: transfer destination {d!r} must be a number like +966112345678 or an extension")
        if n.transfer_type not in ("", "warm", "cold"):
            out.append(f"node {n.id}: transfer_type must be warm or cold")
        if n.timeout_s is not None and not 1 <= _num(n.timeout_s) <= 60:
            out.append(f"node {n.id}: transfer timeout_s must be 1-60")
    if n.llm:
        out += _llm_errors(n)
    if n.type == "settings":
        out += _override_errors(n)
    if n.type == "agent" and not n.agent.strip():
        out.append(f"node {n.id}: an agent node needs `agent` (the agent that takes the call)")
    if n.type == "tool":
        if n.on_error not in ("", "continue", "retry", "fail"):
            out.append(f"node {n.id}: on_error must be continue, retry or fail")
        if not 0 <= n.retries <= 5:
            out.append(f"node {n.id}: retries must be 0-5")
        if n.timeout_s is not None and not 0 < _num(n.timeout_s) <= 120:
            out.append(f"node {n.id}: tool timeout_s must be above 0 and at most 120")
    return out


OVERRIDE_SECTIONS = ("system_prompt", "voice", "stt_model", "llm", "call")
CALL_OVERRIDES = {"interrupt": None, "response_delay_ms": (100, 1500), "inactivity_s": (5, 60),
                  "min_interruption_ms": (200, 1500), "vad_threshold": (0.2, 0.9)}


def _llm_errors(n: Node) -> list[str]:
    out = []
    if n.llm.get("temperature") is not None and not 0 <= _num(n.llm["temperature"]) <= 2:
        out.append(f"node {n.id}: llm temperature must be 0-2")
    return out


def _override_errors(n: Node) -> list[str]:
    out = [f"node {n.id}: unknown setting {k!r}" for k in n.overrides if k not in OVERRIDE_SECTIONS]
    if (llm := n.overrides.get("llm")) is not None:
        out += _llm_errors(Node(id=n.id, llm=llm))
    for key, bounds in CALL_OVERRIDES.items():
        v = (n.overrides.get("call") or {}).get(key)
        if v is not None and bounds and not bounds[0] <= _num(v) <= bounds[1]:
            out.append(f"node {n.id}: {key} must be {bounds[0]}-{bounds[1]}")
    if bad := [k for k in (n.overrides.get("call") or {}) if k not in CALL_OVERRIDES]:
        out.append(f"node {n.id}: unknown call setting {bad[0]!r}")
    if bad := [k for k in (n.overrides.get("voice") or {}) if k not in ("ar", "en")]:
        out.append(f"node {n.id}: voice is set per language (ar, en), not {bad[0]!r}")
    return out


def _num(v: Any) -> float:
    n = _number(v)
    return n if n is not None else -1


__all__ = ["ACTION_TYPES", "DONE", "NODE_TYPES", "Edge", "Graph", "Node"]
