import type { FlowEdge, FlowNode } from "../api";

// Hamsa's Variable System on the console side: naming rules, what a step creates, and what a step can see — "a node sees
// variables only from nodes that run before it" (system and custom variables are available everywhere).

export const VAR_NAME = /^[a-z][a-z0-9_]{0,49}$/;
export type SysVar = { group: string; name: string; description: string };
export type VarGroup = { label: string; items: { name: string; hint?: string }[] };

/** Names flows already use for the system values (kept working, not offered as new). */
export const LEGACY_SYSTEM = ["userNumber", "callParams", "call_lang", "current_datetime"];

export function nameProblem(name: string, reserved: Iterable<string> = []): string | null {
  if (!VAR_NAME.test(name)) return "must be snake_case: a lowercase letter, then lowercase letters, digits or underscores (1-50 characters)";
  if (new Set(reserved).has(name)) return "is a system variable name";
  return null;
}

/** The variables a step creates: the values it collects, sets, saves from a tool result, or captures from the keypad. */
export function createdBy(n: FlowNode): string[] {
  return [...(n.extract ?? []), ...Object.keys(n.set ?? {}), ...Object.keys(n.outputs ?? {}),
    ...(n.dtmf_capture?.variable ? [n.dtmf_capture.variable] : [])];
}

/** Steps that can run before `id` (a path of normal transitions leads from them to it). */
export function ancestors(edges: FlowEdge[], id: string): Set<string> {
  const seen = new Set<string>();
  const queue = [id];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const e of edges) {
      if (e.to === cur && e.from !== "*" && !seen.has(e.from)) { seen.add(e.from); queue.push(e.from); }
    }
  }
  return seen;
}

/** What step `id` can use of what steps create: the variables of the steps before it. A step reached from "Anywhere" can't
 *  rely on any (it may be entered before they exist), unless a normal path also leads to it. */
export function availableAt(nodes: FlowNode[], edges: FlowEdge[], id: string): string[] {
  const before = ancestors(edges, id);
  const names = nodes.filter(n => before.has(n.id)).flatMap(createdBy);
  return [...new Set(names)];
}

/** Every `{{ name }}` (and `{% if name %}`) a text refers to. */
export function referencedNames(text: string): string[] {
  const out = new Set<string>();
  for (const m of text.matchAll(/\{\{-?\s*([A-Za-z_][A-Za-z0-9_]*)/g)) out.add(m[1]);
  for (const m of text.matchAll(/\{%-?\s*(?:el)?if\s+(?:not\s+)?([A-Za-z_][A-Za-z0-9_]*)/g)) out.add(m[1]);
  return [...out];
}

/** The texts of a step that may hold templates. */
export function templatesOf(n: FlowNode): string[] {
  const strings = (o: unknown): string[] => (typeof o === "string" ? [o] : o && typeof o === "object" ? Object.values(o).flatMap(strings) : []);
  return [n.instructions ?? "", ...Object.values(n.say ?? {}), ...Object.values(n.processing ?? {}), n.destination ?? "", n.agent ?? "",
    ...Object.values(n.headers ?? {}), ...strings(n.args), ...strings(n.set)].filter(t => t.includes("{{") || t.includes("{%"));
}
