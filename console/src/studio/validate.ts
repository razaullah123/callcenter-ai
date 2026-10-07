import type { FlowEdge, FlowNode } from "../api";
import { availableAt, createdBy, LEGACY_SYSTEM, nameProblem, referencedNames, templatesOf } from "./variables";

// Live checks of the open flow, like Hamsa's validation badge: errors block a working flow (the server refuses to save
// some of them), warnings are things that usually stall or confuse a call. Every issue may point at a node ("Focus").

export type Issue = { level: "error" | "warning"; msg: string; node?: string };
/** What the variable checks need from outside the graph: system and custom variable names, what tools fill in, the call facts. */
export type VarContext = { system: string[]; custom: string[]; infer: string[]; facts: string[] };
type G = { nodes: FlowNode[]; edges: FlowEdge[]; start: string; variables: Record<string, unknown> };

const CONDITION_KEYS = new Set(["dtmf", "all", "any", "filled", "empty", "not_all_filled", "equals", "ne", "gt", "gte", "lt", "lte",
  "contains", "not_contains", "regex", "exists", "not_exists", "stage", "llm", "replied"]);
const END_TYPES = new Set(["end", "transfer"]);

function walk(cond: unknown, visit: (key: string, value: unknown) => void) {
  if (!cond || typeof cond !== "object") return;
  for (const [k, v] of Object.entries(cond as Record<string, unknown>)) {
    visit(k, v);
    if ((k === "all" || k === "any") && Array.isArray(v)) v.forEach(c => walk(c, visit));
  }
}

export function validateGraph(g: G, ctx?: VarContext): Issue[] {
  const out: Issue[] = [];
  const err = (msg: string, node?: string) => out.push({ level: "error", msg, node });
  const warn = (msg: string, node?: string) => out.push({ level: "warning", msg, node });
  const ids = new Set(g.nodes.map(n => n.id));
  if (!g.nodes.length) { err("The flow has no nodes."); return out; }
  if (!ids.has(g.start)) err(`The start node “${g.start}” doesn't exist.`);

  const incoming = new Set<string>();
  for (const e of g.edges) {
    if (e.from !== "*" && !ids.has(e.from)) err(`A transition starts at “${e.from}”, which doesn't exist.`);
    if (!ids.has(e.to)) err(`A transition from “${e.from === "*" ? "Anywhere" : e.from}” points to “${e.to}”, which doesn't exist.`, ids.has(e.from) ? e.from : undefined);
    if (e.to !== e.from) incoming.add(e.to);
    walk(e.when, (k, v) => {
      const where = e.from === "*" ? "Anywhere" : e.from, node = ids.has(e.from) ? e.from : undefined;
      if (k === "llm" && !String(v ?? "").trim()) err(`A transition from “${where}” → “${e.to}” has an empty question.`, node);
      if (k === "regex" && v && typeof v === "object") {
        for (const p of Object.values(v as Record<string, unknown>)) {
          try { new RegExp(String(p)); } catch { err(`A transition from “${where}” has an invalid pattern ${JSON.stringify(p)}.`, node); }
        }
      }
      if (k === "dtmf" && !/^[0-9*#]$/.test(String(v))) err(`A transition from “${where}” listens for the key ${JSON.stringify(v)}, which isn't 0-9, * or #.`, node);
      if (!CONDITION_KEYS.has(k)) warn(`A transition from “${where}” uses an unknown condition “${k}” — it is ignored (always true).`, node);
    });
  }

  for (const n of g.nodes) {
    const own = g.edges.filter(e => e.from === n.id);
    if (n.type === "tool" && !n.tool) err("This tool node has no tool selected.", n.id);
    if (n.type === "skill" && !n.skill) err("This node has no skill selected.", n.id);
    if (n.type === "agent" && !n.agent?.trim()) err("This node has no agent selected.", n.id);
    if (n.type === "agent" && !own.some(e => e.on === "failure" || (e.on === undefined && !e.when))) warn("Nothing handles the transfer failing (unknown agent, too many hand-offs) — a person takes over.", n.id);
    if (n.llm?.temperature != null && !(n.llm.temperature >= 0 && n.llm.temperature <= 2)) err("The step's model temperature must be 0-2.", n.id);
    if (n.type === "settings") {
      const ov = n.overrides ?? {}, call = (ov.call ?? {}) as Record<string, number>;
      const range = (k: string, lo: number, hi: number, label: string) => { if (call[k] != null && !(call[k] >= lo && call[k] <= hi)) err(`${label} must be ${lo}-${hi}.`, n.id); };
      if (!Object.keys(ov).length) warn("It changes nothing yet — set at least one value.", n.id);
      range("response_delay_ms", 100, 1500, "Response delay (ms)"); range("inactivity_s", 5, 60, "Inactivity timeout (s)");
      range("min_interruption_ms", 200, 1500, "Minimum interruption (ms)"); range("vad_threshold", 0.2, 0.9, "VAD threshold");
      const t = (ov.llm as { temperature?: number } | undefined)?.temperature;
      if (t != null && !(t >= 0 && t <= 2)) err("Temperature must be 0-2.", n.id);
    }
    if (n.dtmf_capture) {
      const c = n.dtmf_capture;
      if (!/^[a-z][a-z0-9_]*$/.test(c.variable ?? "")) err("Keypad capture needs a snake_case variable name (for example account_number).", n.id);
      if (c.max_digits != null && !(c.max_digits >= 1 && c.max_digits <= 20)) err("Keypad capture: the maximum number of digits must be 1-20.", n.id);
      if (c.timeout_s != null && !(c.timeout_s >= 1 && c.timeout_s <= 30)) err("Keypad capture: the pause that ends it must be 1-30 seconds.", n.id);
    }
    if (n.type === "transfer" && n.destination?.trim() && !n.destination.includes("{{") && !/^\+[1-9]\d{6,14}$/.test(n.destination.trim()) && !/^\d{1,8}$/.test(n.destination.trim())) {
      err("The transfer destination must be a number like +966112345678 or an extension.", n.id);
    }
    if (n.type === "transfer" && n.timeout_s != null && !(n.timeout_s >= 1 && n.timeout_s <= 60)) err("The transfer timeout must be 1-60 seconds.", n.id);
    if (n.type === "tool" && n.timeout_s != null && !(n.timeout_s > 0 && n.timeout_s <= 120)) err("The tool timeout must be above 0 and at most 120 seconds.", n.id);
    const undeclared = (n.extract ?? []).filter(v => !(v in g.variables));
    if (undeclared.length) err(`It collects undeclared variables: ${undeclared.join(", ")}.`, n.id);

    if (n.type === "conversation") {
      const say = n.say ?? {};
      if (n.say && !Object.values(say).some(s => s.trim()) && !n.instructions?.trim()) warn("The static message is empty.", n.id);
      else if (!n.say && !n.instructions?.trim()) warn("It has no prompt — the agent doesn't know what to do here.", n.id);
      if (!own.length && n.id !== g.start) warn("It has no transitions out — the call stays here until the caller hangs up.", n.id);
    }
    if (n.type === "router" && own.length && !own.some(e => !e.when || !Object.keys(e.when).length)) {
      warn("A router needs a last transition without a condition (“always”), or the flow stalls when none matches.", n.id);
    }
    if (n.type === "router" && !own.length && g.edges.some(e => e.to === n.id)) warn("A router with no transitions leads nowhere.", n.id);
    if (n.type === "tool" && n.tool && !own.some(e => e.on === "failure" || e.on === undefined && !e.when)) {
      warn("Nothing handles this tool failing — add an “On failure” transition.", n.id);
    }
    if (n.type === "tool" && !own.some(e => e.on === "success" || e.on === undefined)) warn("It has no “On success” transition.", n.id);
    if (END_TYPES.has(n.type) && own.length) warn("Transitions out of an end / transfer node are never used.", n.id);
    if (n.id !== g.start && !incoming.has(n.id)) warn("Nothing leads to this node.", n.id);
  }

  // ---- variables (Hamsa's Variable System): names, and where each one can be used
  const reserved = new Set([...(ctx?.system ?? []), ...LEGACY_SYSTEM]);
  const created = new Map<string, string>();                       // variable → the first step that creates it
  for (const n of g.nodes) for (const v of createdBy(n)) if (!created.has(v)) created.set(v, n.id);
  for (const v of Object.keys(g.variables)) if (!created.has(v)) created.set(v, "");
  for (const [name, node] of created) {
    const problem = nameProblem(name, reserved);
    if (problem) err(`The variable “${name}” ${problem}.`, node || undefined);
  }
  if (ctx?.system.length) {
    const known = new Set([...ctx.system, ...LEGACY_SYSTEM, ...ctx.custom, ...ctx.infer, ...ctx.facts, "slots", "parsed", "result", "args"]);
    for (const n of g.nodes) {
      const here = new Set(availableAt(g.nodes, g.edges, n.id));      // what the steps before this one create
      for (const name of new Set(templatesOf(n).flatMap(referencedNames))) {
        if (known.has(name) || here.has(name)) continue;
        warn(created.has(name)
          ? `It uses {{ ${name} }}, which is only collected after this step or on another path.`
          : `It uses {{ ${name} }}, which isn't a system variable, a custom variable or collected by an earlier step.`, n.id);
      }
    }
    for (const e of g.edges.filter(x => x.from === "*")) {           // "Anywhere" can run before anything was collected
      const used: string[] = [];
      walk(e.when, (k, v) => {
        if (k === "all" || k === "any") return;
        if (Array.isArray(v)) used.push(...v.map(String));
        else if (v && typeof v === "object") used.push(...Object.keys(v as object));
      });
      for (const name of new Set(used)) {
        if (created.has(name) && !known.has(name)) warn(`A transition from Anywhere to “${e.to}” depends on “${name}”, which is collected during the call and may not exist yet.`, ids.has(e.to) ? e.to : undefined);
      }
    }
  }
  return out;
}
