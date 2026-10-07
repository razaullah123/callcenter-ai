"""Import a Hamsa agent: the agent JSON Hamsa's flow builder loads (GET api.tryhamsa.com/v2/voice-agents/<id>, the
`data` object) → the parts of one of our agents: a persona, one flow skill, HTTP tools, greeting, knobs.

Hamsa's own export files (`.hamsa`) are AES-GCM encrypted with Hamsa's key — they can't be read here; `is_encrypted`
recognises them so the console can say so.

Mapping (Hamsa → ours):
  start / conversation      conversation (message → instructions; "static" messages are said word for word;
                            extractVariables → node `extract` + graph `variables`)
  set_local_variables       set (values may be Jinja templates; plain values become "=literal")
  router                    router
  tool                      tool (the web tool → an HTTP tool; params → args; extractVariables "result.x" → outputs)
  end_call                  end
  isGlobal node             a global ("*") edge to it, when its globalCondition holds (llm)
  conditions                natural_language → llm (on tool nodes "On Success" / "On Failure" → success / failure),
                            structured_equation → all / any of equals, auto / always → no condition (a
                            conversation node's `auto` waits for the caller's reply), after_user_reply → replied
  customVariables + params  an "init" set node in front of the start node
Not imported (listed in the report): the LLM / voice (the agent uses this project's models — never the API key in
the file), knowledge base, voice dictionaries, call settings other than the silence threshold, the outcome schema
(kept in the bundle for the post-call analysis, 12.9).
"""

import json
import re
from typing import Any

from runtime.platform import analysis
from runtime.skills.flow import jsonpath_to_path

ENCRYPTED = re.compile(r"^[0-9a-f]{24}:[0-9a-f]{32}:[0-9a-f]+$")
PATH = re.compile(r"^result(\.[A-Za-z_][A-Za-z0-9_]*|\.\d+|\[\d+\])*$")

# Hamsa router / equation operators (as the docs and exports name them) → our condition keys.
_OPERATORS = {
    "equals": "equals", "==": "equals", "eq": "equals", "not_equals": "ne", "notEquals": "ne", "!=": "ne", "ne": "ne",
    "greater_than": "gt", "greaterThan": "gt", ">": "gt", "gt": "gt",
    "greater_than_or_equal": "gte", "greater_than_or_equals": "gte", "greaterThanOrEqual": "gte", ">=": "gte", "gte": "gte",
    "less_than": "lt", "lessThan": "lt", "<": "lt", "lt": "lt",
    "less_than_or_equal": "lte", "less_than_or_equals": "lte", "lessThanOrEqual": "lte", "<=": "lte", "lte": "lte",
    "contains": "contains", "not_contains": "not_contains", "notContains": "not_contains",
    "regex": "regex", "matches": "regex",
}
_EXISTS = ("exists", "is_not_empty", "isNotEmpty")
_NOT_EXISTS = ("not_exists", "notExists", "is_empty", "isEmpty")


def _number(v: Any) -> Any:
    """A condition's number as a number (Hamsa stores values as text); anything else stays as it is."""
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v
SAFE_HEADERS = {"content-type", "accept"}
JSON_TYPES = {"string", "number", "integer", "boolean", "object", "array"}


def is_encrypted(text: str) -> bool:
    return bool(ENCRYPTED.match(text.strip()[:200000]))


def parse(text: str) -> dict[str, Any]:
    """The agent object from a file's text: Hamsa's API reply ({"data": {...}}) or the agent itself."""
    if is_encrypted(text):
        raise ValueError("this is an encrypted Hamsa export (.hamsa) — only Hamsa can open it. Import the agent's "
                         "JSON instead (the agent as Hamsa's flow builder loads it)")
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise ValueError(f"not JSON: {e}") from None
    if isinstance(doc, dict) and isinstance(doc.get("data"), dict) and "workflow" in doc["data"]:
        doc = doc["data"]
    if not isinstance(doc, dict) or not isinstance(doc.get("workflow"), dict):
        raise ValueError("not a Hamsa agent: no `workflow` (nodes and edges) in it")
    return doc


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:40].strip("_") or fallback


def _value(v: Any) -> Any:
    """A Hamsa value → our set / arg expression: templates stay, plain values are literals ("=…")."""
    if not isinstance(v, str):
        return v
    return v if ("{{" in v or "{%" in v) else "=" + v


def _json(v: Any, default: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v if v is not None else default


def convert(h: dict[str, Any], *, taken_tools: set[str] = frozenset()) -> dict[str, Any]:
    """Our agent parts from a Hamsa agent; `taken_tools`: tool names already used in the project."""
    report: list[str] = []
    wf = h["workflow"]
    hnodes: list[dict] = list(wf.get("nodes") or [])
    by_handle = {e.get("sourceHandle"): e.get("target") for e in wf.get("edges") or []}

    # ---- ids: readable and unique
    ids: dict[str, str] = {}
    used: set[str] = {"init"}
    for i, n in enumerate(hnodes):
        base = _slug(n.get("label") or "", f"node_{i + 1}")
        nid, k = base, 2
        while nid in used:
            nid, k = f"{base}_{k}", k + 1
        used.add(nid)
        ids[n["id"]] = nid

    # ---- tools: each web tool used by a node → an HTTP tool
    web = {t["id"]: t for t in h.get("resolvedWebTools") or []}
    binding = {b.get("nodeId"): b for b in h.get("tools") or []}
    tools: dict[str, dict] = {}
    tool_names: dict[str, str] = {}

    def tool_name(t: dict) -> str:
        if t["id"] in tool_names:
            return tool_names[t["id"]]
        base = _slug(t.get("name") or "", "tool")
        name, k = base, 2
        while name in tools or name in taken_tools:
            name, k = f"{base}_{k}", k + 1
        tool_names[t["id"]] = name
        settings = t.get("toolSettings") or {}
        headers = {}
        for hk, hv in (settings.get("httpHeaders") or {}).items():
            if hk.lower() in SAFE_HEADERS:
                headers[hk] = str(hv)
            else:
                report.append(f"Tool {t.get('name')}: header {hk} not copied — add it as a secret on the tool")
        if settings.get("authToken"):
            report.append(f"Tool {t.get('name')}: its auth token was not copied — add it as a secret on the tool")
        props = {}
        for p, spec in (t.get("params") or {}).items():
            typ = (spec or {}).get("type") if (spec or {}).get("type") in JSON_TYPES else "string"
            props[p] = {"type": typ, "description": (spec or {}).get("description") or p}
        kind, backs = _kind(t.get("name") or "")
        policy = {"kind": kind, "source": "http", "description": t.get("description") or t.get("name") or name,
                  "input_schema": {"type": "object", "properties": props},
                  "http": {"method": str(settings.get("methodType") or "POST").upper(), "url": settings.get("serverUrl"),
                           "headers": headers or {"Content-Type": "application/json"}},
                  "timeout_s": float(settings.get("timeOut") or 30)}
        if kind != "read":
            policy["confirm"] = "none"        # the flow itself asks the caller (as it did in Hamsa)
        if backs:
            policy["backs"] = backs
        tools[name] = policy
        return name

    # ---- variables the caller's words fill (conversation nodes)
    variables: dict[str, dict] = {}

    def declare(v: dict) -> str:
        name = v["name"]
        if name not in variables:
            spec: dict[str, Any] = {"type": "string", "description": (v.get("extractionPrompt") or name)[:1500]}
            if v.get("isEnumEnabled") and v.get("enumValues"):
                spec["enum"] = list(v["enumValues"])
            variables[name] = spec
        return name

    nodes: list[dict] = []
    edges: list[dict] = []
    start = None
    counts = {"auto_reply": 0, "no_target": 0, "unsupported": 0, "outputs_skipped": 0}
    for n in hnodes:
        typ, nid = n.get("type"), ids[n["id"]]
        pos = n.get("position") or None
        node: dict[str, Any] = {"id": nid, "position": {"x": round(pos["x"]), "y": round(pos["y"])} if pos else None}
        if typ in ("start", "conversation"):
            text = (n.get("message") or "").strip()
            if n.get("messageType") == "static" and text:
                node.update(type="conversation", say={"ar": text, "en": text})        # said as is, no model
                if n.get("skipResponse") and not n.get("isGlobal"):
                    node["skip_response"] = True                                       # doesn't wait for the caller
            else:
                node.update(type="conversation", instructions=text)
            ex = (n.get("extractVariables") or {})
            if ex.get("enabled"):
                node["extract"] = [declare(v) for v in ex.get("variables") or [] if v.get("name")]
            if (n.get("globalExtractVariables") or {}).get("variables"):
                report.append(f"Node {n.get('label')}: its global variables are extracted only while it is active")
            if typ == "start" or n.get("isStart"):
                start = start or nid
        elif typ == "set_local_variables":
            node.update(type="set", set={v["name"]: _value(v.get("value", "")) for v in n.get("staticVariables") or []
                                         if v.get("name")})
        elif typ == "router":
            node["type"] = "router"
        elif typ == "tool":
            b = binding.get(n["id"]) or {}
            t = web.get(b.get("toolId"))
            if t is None:
                report.append(f"Tool node {n.get('label')}: its tool is missing from the file — left as a router")
                node["type"] = "router"
            else:
                node.update(type="tool", tool=tool_name(t))
                over = ((b.get("overrides") or {}).get("params") or {})
                args = {}
                for p, spec in (t.get("params") or {}).items():
                    o = over.get(p) or {}
                    if o.get("disabled"):
                        continue
                    v = o.get("value") if o.get("value") not in (None, "") else (spec or {}).get("overrideValue")
                    if v in (None, ""):
                        report.append(f"Tool node {n.get('label')}: parameter {p} has no value in the flow "
                                      "(Hamsa let the model fill it) — set it on the node")
                        continue
                    args[p] = _value(v)
                node["args"] = args
                oeb = str(n.get("onErrorBehavior") or b.get("onErrorBehavior") or "").lower()
                if oeb in ("retry", "fail"):
                    node["on_error"] = oeb
                wait = n.get("timeout") or b.get("timeout")
                if isinstance(wait, (int, float)) and wait > 0:
                    node["timeout_s"] = round(wait / 1000, 1) if wait >= 1000 else wait       # Hamsa: ms (or seconds)
                outs = {}
                for v in ((n.get("extractVariables") or {}).get("variables") or []):
                    path = (v.get("extractionPrompt") or "").strip()
                    if path.startswith("$"):                      # a JSONPath ($.data.items[0].name)
                        path = jsonpath_to_path(path)
                    if PATH.match(path):
                        outs[v["name"]] = path
                    else:
                        counts["outputs_skipped"] += 1
                if outs:
                    node["outputs"] = outs
        elif typ in ("transfer_call", "transferCall"):
            msg = (n.get("message") or n.get("transferMessage") or "").strip()
            node.update(type="transfer", reason=n.get("label") or "transfer",
                        destination=str(n.get("phoneNumber") or "").strip(),
                        transfer_type="cold" if str(n.get("transferType") or "").lower() == "cold" else "warm",
                        timeout_s=n.get("timeout") or None,
                        headers={h["name"]: str(h.get("value", "")) for h in n.get("sipHeaders") or [] if h.get("name")})
            if msg and n.get("messageType", "static") == "static":
                node["say"] = {"ar": msg, "en": msg}
            elif msg:
                report.append(f"Transfer node {n.get('label')}: its AI-written announcement became the standard hand-off line")
        elif typ in ("change_agent_settings", "changeAgentSettings"):
            src = n.get("agentSettings") or n.get("settings") or n
            ov: dict[str, Any] = {}
            if (text := (src.get("systemInstructions") or "").strip()):
                ov["system_prompt"] = text
            call: dict[str, Any] = {}
            if src.get("interrupt") is not None:
                call["interrupt"] = bool(src["interrupt"])
            for hamsa_key, key, scale in (("responseDelay", "response_delay_ms", 1), ("userInactivityTimeout", "inactivity_s", 1),
                                          ("minInterruptionDuration", "min_interruption_ms", 1000),
                                          ("vadActivationThreshold", "vad_threshold", 1)):
                if isinstance(src.get(hamsa_key), (int, float)):
                    call[key] = round(src[hamsa_key] * scale, 2)
            if call:
                ov["call"] = call
            skipped = [k for k in ("voiceId", "expressiveness", "preferredSttModel", "voiceDictionaryIds") if src.get(k)]
            if skipped:
                report.append(f"Settings node {n.get('label')}: {', '.join(skipped)} left out — they name Hamsa's voices / models; "
                              "set this project's voice and models on the node")
            node.update(type="settings", overrides=ov)
        elif typ in ("transfer_agent", "transferAgent"):
            msg = (n.get("transferMessage") or "").strip()
            node.update(type="agent", agent=str(n.get("agentId") or ""), handoff_history=bool(n.get("handoffConversation")),
                        handoff_variables=bool(n.get("handoffVariables")))
            if msg and n.get("transferMessageType", "static") == "static":
                node["say"] = {"ar": msg, "en": msg}
            report.append(f"Transfer agent node {n.get('label')}: points to Hamsa's agent {node['agent'] or '(none)'} — choose the "
                          "matching agent of this project on the node")
        elif typ == "end_call":
            node["type"] = "end"
        else:
            report.append(f"Node {n.get('label')}: type {typ!r} isn't supported — imported as a router")
            node["type"] = "router"
        nodes.append({k: v for k, v in node.items() if v not in (None, "", [], {})} | {"id": nid, "type": node["type"]})

        # ---- its transitions → edges (in Hamsa's priority order)
        trans = sorted(n.get("transitions") or [], key=lambda t: t.get("priority") or 0)
        for t in trans:
            if t.get("isEnabled") is False:
                continue
            target = by_handle.get(f"transition-{t.get('id')}") or t.get("targetNodeId")
            if not target or target not in ids:
                counts["no_target"] += 1
                continue
            edge: dict[str, Any] = {"from": nid, "to": ids[target]}
            c = t.get("condition") or {}
            ctype = c.get("type")
            if typ == "tool" and ctype == "natural_language" and \
                    (t.get("name") or c.get("prompt") or "").strip().lower() in ("on success", "on failure"):
                edge["on"] = "success" if "success" in (t.get("name") or c.get("prompt")).lower() else "failure"
            elif ctype == "natural_language":
                edge["when"] = {"llm": (c.get("prompt") or c.get("description") or "").strip()}
            elif ctype == "structured_equation":
                parts = []
                for x in c.get("conditions") or []:
                    op, var, val = x.get("operator"), x.get("variable"), x.get("value")
                    if op in _EXISTS:
                        parts.append({"filled": [var]})
                    elif op in _NOT_EXISTS:
                        parts.append({"empty": [var]})
                    elif kind := _OPERATORS.get(op):
                        parts.append({kind: {var: _number(val) if kind in ("gt", "gte", "lt", "lte") else val}})
                    else:
                        counts["unsupported"] += 1
                        report.append(f"Node {n.get('label')}: condition operator {x.get('operator')!r} isn't "
                                      "supported — that edge was skipped")
                        parts = None
                        break
                if parts is None:
                    continue
                edge["when"] = {"any" if c.get("logic") == "any" else "all": parts}
            elif ctype == "dtmf":
                key = c.get("key") or c.get("dtmfKey") or c.get("digit")
                if key in (None, ""):
                    counts["unsupported"] += 1
                    report.append(f"Node {n.get('label')}: a keypad transition has no key — skipped")
                    continue
                edge["when"] = {"dtmf": str(key)}
            elif ctype == "after_user_reply" or (ctype == "auto" and typ in ("start", "conversation")):
                edge["when"] = {"replied": True}
                counts["auto_reply"] += ctype == "auto"
            elif ctype in ("auto", "always", None):
                pass
            else:
                counts["unsupported"] += 1
                report.append(f"Node {n.get('label')}: condition {ctype!r} isn't supported — edge skipped")
                continue
            edges.append(edge)
        if n.get("isGlobal"):
            if str(n.get("globalConditionType") or "").lower() == "dtmf" and n.get("globalDtmfKey") not in (None, ""):
                when: dict[str, Any] = {"dtmf": str(n["globalDtmfKey"])}
            else:
                when = {"llm": (n.get("globalCondition") or n.get("description") or n.get("label") or "").strip()}
            glob: dict[str, Any] = {"from": "*", "to": nid, "when": when}
            if n.get("globalReturnToSource"):
                glob["back"] = True
            if n.get("requiresDoubleConfirm"):
                glob["confirm"] = True
            if n.get("skipResponse"):
                glob["silent"] = True
            edges.append(glob)

    if start is None and nodes:
        start = nodes[0]["id"]
        report.append("No start node in the file — the first node starts the flow")
    # ---- initial values: an init node in front of the start node
    init_vals = {}
    for v in _json(wf.get("customVariables"), []) or []:
        if v.get("name"):
            init_vals[v["name"]] = _value(v.get("defaultValue") or "")
    for k, v in ((h.get("conversation") or {}).get("params") or {}).items():
        init_vals[k] = _value(v)
    if init_vals:
        nodes.insert(0, {"id": "init", "type": "set", "set": init_vals,
                         "position": {"x": (nodes[0].get("position") or {}).get("x", 0) - 400, "y": 0} if nodes else None})
        edges.insert(0, {"from": "init", "to": start})
        start = "init"

    if counts["auto_reply"]:
        report.append(f"{counts['auto_reply']} conversation step(s) moved on automatically in Hamsa; here they move on "
                      "after the caller's next reply")
    if counts["no_target"]:
        report.append(f"{counts['no_target']} transition(s) had no target node and were skipped")
    if counts["outputs_skipped"]:
        report.append(f"{counts['outputs_skipped']} tool output(s) described in words (not a result path) were skipped")

    conv = h.get("conversation") or {}
    llm, voice, cs = h.get("llm") or {}, h.get("voice") or {}, h.get("callSettings") or {}
    report.append(f"LLM: Hamsa used {llm.get('provider')} {llm.get('model')}; this agent uses the project's LLM "
                  "(its API key in the file was not imported)")
    vr = voice.get("voiceRecord") or {}
    report.append(f"Voice: Hamsa's {vr.get('name') or voice.get('voiceId')} ({vr.get('provider')}) — this agent uses "
                  "the project's voice; pick one on the Voices page")
    if h.get("knowledgeBaseItemsIds") not in (None, "[]", []):
        report.append("Knowledge base items were not imported (knowledge base: Phase 12.9)")
    if h.get("voiceDictionaryIds"):
        report.append("Voice dictionaries (pronunciations) were not imported")
    knobs: dict[str, Any] = {}
    if isinstance(cs.get("silenceThreshold"), (int, float)):
        knobs["voice_end_silence_ms"] = int(cs["silenceThreshold"])
    lang = voice.get("lang") or "ar"
    langs = [lang] + [x for x in ("ar", "en") if x != lang] if cs.get("languageDialectSwitcher", True) else [lang]
    greeting = (conv.get("greetingMessage") or "").strip()
    return {
        "name": (h.get("name") or "Imported agent").strip()[:150],
        "persona": (conv.get("preamble") or "").strip(),
        "greeting": greeting,
        "languages": langs, "default_language": lang,
        "flow": {"start": start, "variables": variables, "nodes": nodes, "edges": edges},
        "tools": tools, "knobs": knobs,
        "analysis": analysis.settings_from_schema(h.get("outcomeResponseShape")),     # Hamsa's outcome schema → our outcome fields
        "temperature": llm.get("temperature"),
        "source": {"from": "hamsa", "id": h.get("id"), "name": h.get("name"), "type": h.get("type"),
                   "llm": {"provider": llm.get("provider"), "model": llm.get("model")},
                   "voice": {"name": vr.get("name"), "provider": vr.get("provider"), "lang": voice.get("lang")},
                   "call_settings": cs, "outcome_schema": h.get("outcomeResponseShape")},
        "stats": {"nodes": len(nodes), "edges": len(edges), "tools": len(tools), "variables": len(variables)},
        "report": report,
    }


def _kind(name: str) -> tuple[str, str | None]:
    """read / write / send and the claim a success backs, from the tool's name (Hamsa doesn't say)."""
    n = name.lower()
    if "send" in n or ("otp" in n and "verify" not in n):
        return "send", "sent"
    if "book" in n or "reschedule commit" in n:
        return "write", "booked"
    if "cancel" in n:
        return "write", "cancelled"
    if "confirm" in n:
        return "write", "confirmed"
    return "read", None


__all__ = ["convert", "is_encrypted", "parse"]
