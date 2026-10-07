import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider, useNodesState, useReactFlow,
  type Connection, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type FlowEdge, type FlowGraph, type FlowNode } from "../api";
import { Badge, Button, cx } from "../ui";
import { NodeIcon } from "./nodeIcons";
import { validateGraph, type Issue, type VarContext } from "./validate";
import VariablePicker from "./VariablePicker";
import { availableAt, nameProblem, type SysVar, type VarGroup } from "./variables";

// Flow canvas (Hamsa-style): a floating toolbar (+ add node · (x) variables · ⊞ auto-layout · ⚙ global settings),
// nodes drawn by type with their transitions as rows — each row has its own handle, in the order they are tried —
// a ⋯ menu per node (rename · view logs · duplicate · delete), and a tool picker for tool nodes.

const ANY = "__any__";            // the "Anywhere" node: edges from "*" (reachable from any step)
const DONE = "__done__";

export const TYPES: Record<string, { label: string; color: string; tint: string; hint: string; edge: string }> = {
  conversation: { label: "Conversation", color: "bg-blue-600", tint: "text-blue-600", hint: "Interactive dialog with the user", edge: "border-blue-300/60" },
  tool: { label: "Tool", color: "bg-purple-600", tint: "text-purple-600", hint: "Execute pre-configured tools", edge: "border-purple-300/60" },
  transfer: { label: "Transfer Call", color: "bg-orange-600", tint: "text-orange-600", hint: "Transfer call to another number", edge: "border-orange-300/60" },
  agent: { label: "Transfer to Agent", color: "bg-green-600", tint: "text-green-600", hint: "Transfer conversation to another agent", edge: "border-green-300/60" },
  router: { label: "Router", color: "bg-indigo-600", tint: "text-indigo-600", hint: "Logic splitting node with conditional routing", edge: "border-indigo-300/60" },
  set: { label: "Set Local Variables", color: "bg-green-600", tint: "text-green-600", hint: "Set local variables for use in subsequent nodes", edge: "border-green-300/60" },
  skill: { label: "Go to Skill", color: "bg-teal-600", tint: "text-teal-600", hint: "Continue in another skill of this agent", edge: "border-teal-300/60" },
  settings: { label: "Change Agent Settings", color: "bg-amber-600", tint: "text-amber-600", hint: "Override agent settings mid-flow", edge: "border-amber-300/60" },
  end: { label: "End Call", color: "bg-red-600", tint: "text-red-600", hint: "End the conversation", edge: "border-red-300/60" },
};

/** Values the platform knows about the call, usable in edge conditions next to the collected variables. */
const CALL_FACTS: [string, string][] = [
  ["verified", "the caller passed verification"], ["identity_confirmed", "they said yes to “Am I speaking to …?”"],
  ["mobile_heard", "a mobile number was heard this turn"], ["code_heard", "a code was heard this turn"],
  ["files_found", "patient files found for the number"], ["otp_exhausted", "3 wrong codes"],
  ["awaiting_confirmation", "a booking / change waits for the caller's yes"],
];

/** Comparison operators of a transition: key, label in the picker, short form on the canvas. */
const CMP: [string, string, string][] = [
  ["ne", "is not", "≠"], ["gt", "is greater than", ">"], ["gte", "is at least", "≥"], ["lt", "is less than", "<"],
  ["lte", "is at most", "≤"], ["contains", "contains", "contains"], ["not_contains", "does not contain", "does not contain"],
  ["regex", "matches the pattern", "matches"],
];

export function conditionLabel(e: FlowEdge): string {
  const flags = [e.back && "returns", e.confirm && "asks first", e.silent && "silent"].filter(Boolean).join(", ");
  const label = conditionText(e);
  return flags ? `${label} · ${flags}` : label;
}
function conditionText(e: FlowEdge): string {
  if (e.on) {
    const base = e.on === "success" ? "On success" : "On failure";
    return e.when && Object.keys(e.when).length ? `${base} · ${describe(e.when)}` : base;
  }
  return describe(e.when);
}
function describe(w: Record<string, unknown> | undefined): string {
  if (!w || !Object.keys(w).length) return "Always";
  return Object.entries(w).map(([k, v]) => {
    if (k === "all" || k === "any") return (v as Record<string, unknown>[]).map(describe).join(k === "all" ? " and " : " or ");
    if (k === "filled") return `has ${(v as string[]).join(", ")}`;
    if (k === "empty") return `no ${(v as string[]).join(", ")}`;
    if (k === "not_all_filled") return `not done: ${(v as string[]).join(", ")}`;
    if (k === "equals") return Object.entries(v as object).map(([a, b]) => `${a} = ${b}`).join(", ");
    if (k === "stage") return `stage ${v}`;
    if (k === "dtmf") return `key ${v}`;
    const cmp = CMP.find(([key]) => key === k);
    if (cmp) return Object.entries(v as object).map(([a, b]) => `${a} ${cmp[2]} ${b}`).join(", ");
    if (k === "exists") return `has ${(v as string[]).join(", ")}`;
    if (k === "not_exists") return `no ${(v as string[]).join(", ")}`;
    if (k === "llm") return `“${String(v)}”`;
    if (k === "replied") return v ? "after the caller replies" : "before the caller replies";
    return `${k}: ${JSON.stringify(v)}`;
  }).join(" and ");
}

/** How a transition row is drawn on a card: its kind (Hamsa's labels) and its text. */
export function rowInfo(e: FlowEdge, srcType: string): { kind: string; text: string } {
  const flags = [e.back && "returns", e.confirm && "asks first", e.silent && "silent"].filter(Boolean).join(", ");
  const tail = flags ? ` · ${flags}` : "";
  const w = e.when ?? {}, keys = Object.keys(w);
  if (e.on) return { kind: "", text: (e.on === "success" ? "On Success" : "On Failure") + (keys.length ? ` · ${describe(w)}` : "") + tail };
  if (!keys.length) {
    if (srcType === "router") return { kind: "else", text: `Else${tail}` };
    return srcType === "set" || srcType === "settings" ? { kind: "auto", text: `Auto-advance${tail}` } : { kind: "always", text: `Always${tail}` };
  }
  if (keys.length === 1 && keys[0] === "llm") return { kind: "prompt", text: `${String(w.llm || "…")}${tail}` };
  if (keys.length === 1 && keys[0] === "dtmf") return { kind: "keypad", text: `Press ${w.dtmf}${tail}` };
  if (keys.length === 1 && keys[0] === "replied") return { kind: "reply", text: `${describe(w)}${tail}` };
  return { kind: "equation", text: `${describe(w)}${tail}` };
}
const ROW_KIND: Record<string, { icon: string; label?: string }> = {
  prompt: { icon: "sparkles", label: "Prompt" }, equation: { icon: "sigma", label: "Equation" }, keypad: { icon: "hash", label: "Keypad" },
  reply: { icon: "skill", label: "Reply" }, auto: { icon: "zap" }, always: { icon: "zap" }, else: { icon: "router" },
  "": { icon: "sparkles" },
};

// ---------------------------------------------------------------- layout

function layout(nodes: FlowNode[], edges: FlowEdge[], start: string, dir: "horizontal" | "vertical") {
  const layer: Record<string, number> = { [start]: 0 };
  const queue = [start];
  while (queue.length) {
    const id = queue.shift()!;
    for (const e of edges.filter(x => x.from === id)) {
      if (layer[e.to] === undefined) { layer[e.to] = layer[id] + 1; queue.push(e.to); }
    }
  }
  let next = Math.max(0, ...Object.values(layer)) + 1;
  const rows: Record<number, number> = {};
  const pos: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const l = layer[n.id] ?? next++;
    const r = rows[l] = (rows[l] ?? 0) + 1;
    pos[n.id] = dir === "horizontal" ? { x: l * 320, y: 40 + (r - 1) * 230 } : { x: 40 + (r - 1) * 300, y: l * 260 };
  }
  return pos;
}

// ---------------------------------------------------------------- node view

type RowInfo = { gi: number; kind: string; text: string };
type NodeData = { node: FlowNode; start: boolean; active: boolean; rows: RowInfo[]; selectedEdge: number | null };

type CanvasActions = {
  open: (id: string) => void; menu: (id: string, action: "rename" | "logs" | "duplicate" | "delete") => void;
  pickTool: (id: string) => void; selectEdge: (gi: number) => void; moveEdge: (gi: number, dir: -1 | 1) => void;
  /** Conversation card: switch between a prompt the agent follows and a message it says as written. */
  setMode: (id: string, mode: "prompt" | "static") => void;
};
const Actions = createContext<CanvasActions | null>(null);

function FlowNodeView({ id, data, selected }: NodeProps<Node<NodeData>>) {
  const act = useContext(Actions)!;
  const [menu, setMenu] = useState(false);
  const n = data.node;
  const anywhere = id === ANY;
  const t = anywhere ? { label: "Anywhere", color: "bg-slate-500", hint: "", edge: "border-slate-300/60" } : TYPES[n.type] ?? TYPES.conversation;
  const chip = "rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent-text";
  const body = (() => {
    if (anywhere) return <div className="text-[11px] text-muted">Edges here are checked from every step.</div>;
    if (n.id === DONE) return <div className="text-[11px] text-muted">Quiet end of the old step flow.</div>;
    switch (n.type) {
      case "tool": return (
        <>
          <button className="w-full rounded-lg border border-line bg-soft/60 px-2 py-1.5 text-left" onClick={e => { e.stopPropagation(); act.pickTool(id); }}>
            <div className="flex items-center gap-1 text-[10px] text-muted"><NodeIcon type="tool" size={10} />Tool · click to change</div>
            <div className="mt-0.5 truncate text-xs font-semibold">{n.tool || "choose a tool…"}</div>
            {n.args && Object.keys(n.args).length > 0 && <div className="truncate font-mono text-[10px] text-muted">{JSON.stringify(n.args)}</div>}
          </button>
          {Object.keys(n.outputs ?? {}).length > 0 && <div className="flex flex-wrap items-center gap-1"><span className="text-[10px] text-muted">Extracting:</span>
            {Object.keys(n.outputs ?? {}).map(v => <span key={v} className={chip}>{v}</span>)}</div>}
        </>);
      case "router": return <div className="text-[11px] text-muted">Logic splitting node with conditional routing</div>;
      case "set": return (
        <>
          <div className="flex items-center gap-1.5 text-[11px] text-muted"><span className="text-[10px] font-semibold text-green-600">(x)</span>{Object.keys(n.set ?? {}).length} variables</div>
          <div className="flex flex-wrap gap-1">{Object.keys(n.set ?? {}).slice(0, 6).map(k => <span key={k} className="rounded-md bg-green-100 px-1.5 py-0.5 text-[10px] font-medium text-green-800">{k}</span>)}</div>
        </>);
      case "transfer": return <div className="line-clamp-2 text-[11px] text-muted">{n.reason || "Transfer to a person"}{n.destination ? ` · ${n.destination}` : ""}{n.transfer_type === "cold" ? " · cold" : ""}</div>;
      case "end": return <div className="line-clamp-2 text-[11px] text-muted" dir="auto">{n.say?.en || n.say?.ar || "Silent end"}</div>;
      case "skill": return <div className="text-[11px] text-muted">→ {n.skill || "choose a skill…"}</div>;
      case "settings": return <div className="flex flex-wrap gap-1">{Object.keys(n.overrides ?? {}).length ? Object.keys(n.overrides ?? {}).map(k =>
        <span key={k} className="rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">{k.replace("_", " ")}</span>) : <span className="text-[11px] text-muted">Nothing changed yet</span>}</div>;
      case "agent": return <div className="text-[11px] text-muted">→ {n.agent || "choose an agent…"}{n.handoff_history ? " · with the conversation" : ""}</div>;
      default: {
        const stat = !!n.say;
        return (
          <>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted">{stat ? "Message:" : "Prompt:"}</span>
              <div role="tablist" className="flex gap-0.5 rounded-lg bg-soft p-0.5 text-[11px]">
                {([["prompt", "code", "Prompt"], ["static", "chat", "Static"]] as const).map(([m, icon, label]) => (
                  <button key={m} role="tab" aria-selected={(m === "static") === stat} onClick={e => { e.stopPropagation(); act.setMode(id, m); }}
                    className={cx("flex items-center gap-1 rounded-md px-2 py-0.5 font-medium transition", (m === "static") === stat ? "bg-brand text-white shadow-sm" : "text-ink hover:bg-panel")}>
                    <NodeIcon type={icon} size={11} />{label}</button>))}
              </div>
            </div>
            <div className="rounded-lg border border-line bg-soft/60 px-2 py-1.5">
              <div className="mb-0.5 flex items-center gap-1 text-[10px] text-muted"><NodeIcon type={stat ? "chat" : "code"} size={10} />{stat ? "Static message" : "Prompt"}</div>
              <div className="line-clamp-4 whitespace-pre-line text-[11px]" dir="auto">{stat ? (n.say?.en || n.say?.ar || "Empty message") : (n.instructions || "No instructions yet")}</div>
            </div>
            {(n.extract?.length ?? 0) > 0 && <div className="flex flex-wrap items-center gap-1"><span className="text-[10px] text-muted">Extracting:</span>
              {n.extract!.map(v => <span key={v} className={chip}>{v}</span>)}</div>}
            {n.dtmf_capture && <div className="flex items-center gap-1 text-[10px] text-muted"><NodeIcon type="hash" size={10} />Keypad → <span className="font-mono">{n.dtmf_capture.variable || "?"}</span></div>}
            {(n.tools?.length ?? 0) > 0 && <div className="flex items-center gap-1 text-[10px] text-muted"><NodeIcon type="tool" size={10} />{n.tools!.length} tools</div>}
          </>);
      }
    }
  })();
  const auto = !anywhere && ["set", "router"].includes(n.type) === false && data.rows.length === 0 && n.type !== "end" && n.type !== "transfer";
  return (
    <div className={cx("w-64 rounded-2xl border bg-panel text-left shadow-sm", t.edge,
      selected && "!border-accent ring-2 ring-accent/30",
      data.active && "!border-brand shadow-[0_0_0_4px_rgba(230,58,64,0.3)]")}>
      {!anywhere && <Handle type="target" position={Position.Left} className="!h-3 !w-3 !bg-slate-400" />}
      <div className="flex items-center gap-2.5 px-3 pb-2 pt-3">
        <span className={cx("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white", t.color)}><NodeIcon type={anywhere ? "anywhere" : n.type} size={16} /></span>
        <button className="min-w-0 flex-1 truncate text-left text-[13px] font-semibold" onClick={e => { e.stopPropagation(); act.open(id); }}>
          {n.id === DONE ? "Done" : anywhere ? "Anywhere" : n.id}</button>
        {data.start && <span className="rounded bg-accent/15 px-1 text-[9px] font-semibold text-accent-text">START</span>}
        {!anywhere && <button title="Settings" aria-label="Settings" className="text-muted hover:text-ink" onClick={e => { e.stopPropagation(); act.open(id); }}><NodeIcon type="settings" size={14} stroke={1.8} /></button>}
        {!anywhere && <div className="relative">
          <button title="More" aria-label="More" className="px-1 text-muted hover:text-ink" onClick={e => { e.stopPropagation(); setMenu(m => !m); }}>⋯</button>
          {menu && <div className="absolute right-0 z-30 mt-1 w-36 rounded-lg border border-line bg-panel p-1 text-xs shadow-lg" onMouseLeave={() => setMenu(false)}>
            {([["rename", "✎ Rename"], ["logs", "📜 View logs"], ["duplicate", "⧉ Duplicate"], ["delete", "🗑 Delete"]] as const).map(([a, l]) => (
              <button key={a} className={cx("block w-full rounded px-2 py-1 text-left hover:bg-soft", a === "delete" && "text-bad")}
                onClick={e => { e.stopPropagation(); setMenu(false); act.menu(id, a); }}>{l}</button>))}
          </div>}
        </div>}
      </div>
      {data.active && <div className="mx-3 mb-1 rounded-md bg-red-50 px-2 py-0.5 text-[10px] font-semibold text-red-800">● active in the test call</div>}
      <div className="space-y-2 px-3 pb-2.5">{body}</div>
      {!(n.type === "end" || n.type === "transfer") && (
        <div className="px-3 pb-3">
          <div className="mb-1.5 text-xs font-semibold">Transitions</div>
          <div className="space-y-1.5">
            {data.rows.map(r => {
              const k = ROW_KIND[r.kind] ?? ROW_KIND[""];
              return (
                <div key={r.gi} className={cx("group relative flex items-start gap-2 rounded-lg border px-2 py-1.5",
                  data.selectedEdge === r.gi ? "border-accent bg-accent/5" : "border-line bg-soft/50")}
                  onClick={e => { e.stopPropagation(); act.selectEdge(r.gi); }}>
                  <span className="mt-0.5 text-ink"><NodeIcon type={k.icon} size={12} /></span>
                  <span className="min-w-0 flex-1">
                    {k.label && <span className="block text-[10px] font-semibold text-muted">{k.label}</span>}
                    <span className="line-clamp-2 block text-[11px]" dir="auto" title={r.text}>{r.text}</span>
                  </span>
                  <span className="hidden gap-0.5 group-hover:flex">
                    <button className="text-[10px] text-muted hover:text-ink" title="Try earlier" onClick={e => { e.stopPropagation(); act.moveEdge(r.gi, -1); }}>▲</button>
                    <button className="text-[10px] text-muted hover:text-ink" title="Try later" onClick={e => { e.stopPropagation(); act.moveEdge(r.gi, 1); }}>▼</button>
                  </span>
                  <Handle id={`h-${r.gi}`} type="source" position={Position.Right} className="!-right-[13px] !h-2.5 !w-2.5 !bg-accent" />
                </div>);
            })}
            {auto && <div className="text-[10px] text-muted">No transitions — the call stays here.</div>}
            <div className="relative flex items-center gap-1 rounded-lg border border-dashed border-line px-2 py-1.5 text-[11px] font-medium text-accent-text">
              <span className="text-sm leading-none">+</span> Add — drag from ● to connect
              <Handle id="new" type="source" position={Position.Right} className="!-right-[13px] !h-2.5 !w-2.5 !bg-slate-400" />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
const nodeTypes = { flow: FlowNodeView };

// ---------------------------------------------------------------- canvas

type CanvasProps = {
  graph: FlowGraph; converted: boolean; tools: string[]; skills: string[];
  onSave: (g: FlowGraph) => void; saving: boolean;
  /** Replaces the side panel (the test panel during a test). */
  aside?: ReactNode;
  /** Shown by the ⚙ button (agent-wide settings). */
  globalPanel?: ReactNode;
  /** Tool name → what the tool picker shows. */
  toolInfo?: Record<string, { description: string; kind: string; source: string }>;
  /** Node the test call is in: highlighted, and centred when `follow`. */
  activeNode?: string | null; follow?: boolean;
  /** Centre this node once (the log's ⌖ button); `n` changes on every request. */
  locate?: { id: string; n: number } | null;
  onDirty?: (dirty: boolean) => void;
  /** ⋯ → View logs: open the test log filtered to this node. */
  onViewLogs?: (nodeId: string) => void;
  /** A node or transition was opened: the side panel must show the inspector (closes the test panel). */
  onInspect?: () => void;
  /** The agent's custom variables (Global Settings → Variables). */
  customVars?: Record<string, { type?: string; default?: unknown; description?: string }>;
};

export default function FlowCanvas(props: CanvasProps) {
  return <ReactFlowProvider><Canvas {...props} /></ReactFlowProvider>;
}

type Panel = "inspector" | "variables" | "global";

function Canvas({ graph, converted, tools, skills, onSave, saving, aside, globalPanel, toolInfo, activeNode, follow, locate,
  onDirty, onViewLogs, onInspect, customVars }: CanvasProps) {
  const [g, setG] = useState(() => ({ nodes: graph.nodes, edges: graph.edges, start: graph.start, variables: graph.variables ?? {} }));
  const [sel, setSel] = useState<{ kind: "node"; id: string } | { kind: "edge"; gi: number } | null>(null);
  // like Hamsa: Global Settings is the panel you see; clicking a node / transition shows its inspector, closing it goes back
  const home: Panel = globalPanel ? "global" : "inspector";
  const [panel, setPanel] = useState<Panel>(home);
  const [dirty, setDirty] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [layoutOpen, setLayoutOpen] = useState(false);
  const [picker, setPicker] = useState<string | null>(null);
  const renameRef = useRef<HTMLInputElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [focusNew, setFocusNew] = useState<string | null>(null);
  const rf = useReactFlow();
  const touch = () => setDirty(true);
  const stash = useRef<Record<string, Record<string, string>>>({});     // a static message set aside while the node is in prompt mode
  const systemQ = useQuery({ queryKey: ["system-variables"], queryFn: api.systemVariables, staleTime: Infinity });
  const system: SysVar[] = systemQ.data ?? [];
  const custom = customVars ?? {};
  const inferred = useMemo(() => [...new Set(Object.values(graph.infer ?? {}).flatMap(r => Object.keys(((r as { set?: object }).set) ?? {})))], [graph.infer]);
  const ctx: VarContext = useMemo(() => ({ system: system.map(v => v.name), custom: Object.keys(custom), infer: inferred,
    facts: CALL_FACTS.map(f => f[0]) }), [system, custom, inferred]);
  const issues = useMemo(() => validateGraph(g, ctx), [g, ctx]);
  /** The picker's lists for a step: system, custom, and what the steps before it collect. */
  const varGroups = (nodeId?: string): VarGroup[] => [
    { label: "System", items: system.filter(v => !v.name.startsWith("_")).map(v => ({ name: v.name, hint: v.description })) },
    { label: "Custom", items: Object.entries(custom).map(([name, v]) => ({ name, hint: v.type ?? "string" })) },
    { label: "Collected before this step", items: nodeId ? availableAt(g.nodes, g.edges, nodeId).map(name => ({ name })) : [] },
  ].filter(grp => grp.items.length);
  const [issuesOpen, setIssuesOpen] = useState(false);
  useEffect(() => { onDirty?.(dirty); }, [dirty, onDirty]);

  // React Flow nodes: positions live here while editing; everything else comes from `g`
  const build = useCallback((prev: Node<NodeData>[]) => {
    const auto = layout(g.nodes, g.edges, g.start, "horizontal");
    const rows = (src: string) => g.edges.map((e, gi) => ({ e, gi })).filter(x => x.e.from === src)
      .map(x => ({ gi: x.gi, ...rowInfo(x.e, src === "*" ? "any" : g.nodes.find(nd => nd.id === src)?.type ?? "") }));
    const selectedEdge = sel?.kind === "edge" ? sel.gi : null;
    const out: Node<NodeData>[] = g.nodes.map(n => {
      const old = prev.find(p => p.id === n.id);
      return { ...(old ?? {}), id: n.id, type: "flow", position: old?.position ?? n.position ?? auto[n.id],
               selected: sel?.kind === "node" && sel.id === n.id,
               data: { node: n, start: n.id === g.start, active: n.id === activeNode, rows: rows(n.id), selectedEdge } } as Node<NodeData>;
    });
    if (g.edges.some(e => e.from === "*")) {
      const old = prev.find(p => p.id === ANY);
      out.unshift({ ...(old ?? {}), id: ANY, type: "flow", position: old?.position ?? { x: -340, y: -40 }, deletable: false,
                    data: { node: { id: ANY, type: "router" }, start: false, active: false, rows: rows("*"), selectedEdge } } as Node<NodeData>);
    }
    return out;
  }, [g, activeNode, sel]);
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<Node<NodeData>>(build([]));
  useEffect(() => { setRfNodes(prev => build(prev)); }, [build, setRfNodes]);

  const rfEdges: Edge[] = useMemo(() => g.edges.map((e, gi) => ({
    id: `e${gi}`, source: e.from === "*" ? ANY : e.from, sourceHandle: `h-${gi}`, target: e.to,
    animated: e.from === "*" || (sel?.kind === "edge" && sel.gi === gi),
    style: { strokeWidth: sel?.kind === "edge" && sel.gi === gi ? 2.5 : 1.2, ...(e.from === "*" ? { strokeDasharray: "4 3" } : {}) },
  })), [g.edges, sel]);

  const centre = useCallback((id: string) => {
    const n = rf.getNode(id);
    if (n) rf.setCenter(n.position.x + 128, n.position.y + 80, { zoom: Math.max(rf.getZoom(), 0.9), duration: 500 });
  }, [rf]);
  useEffect(() => { if (follow && activeNode) centre(activeNode); }, [activeNode, follow, centre]);
  useEffect(() => { if (locate) centre(locate.id); }, [locate, centre]);

  // ---- edits
  const patchNode = (id: string, patch: Partial<FlowNode>) => { setG(x => ({ ...x, nodes: x.nodes.map(n => n.id === id ? { ...n, ...patch } : n) })); touch(); };
  const renameNode = (id: string, next: string) => {
    if (!next || next === id || g.nodes.some(n => n.id === next)) return;
    setRfNodes(ns => ns.map(n => n.id === id ? { ...n, id: next } : n));
    setG(x => ({ ...x, start: x.start === id ? next : x.start, nodes: x.nodes.map(n => n.id === id ? { ...n, id: next } : n),
                 edges: x.edges.map(e => ({ ...e, from: e.from === id ? next : e.from, to: e.to === id ? next : e.to })) }));
    setSel({ kind: "node", id: next }); touch();
  };
  const inspect = (next: { kind: "node"; id: string } | { kind: "edge"; gi: number }) => {
    setSel(next); setPanel("inspector"); onInspect?.();
  };
  const addNode = (type: string) => {
    let i = 1; while (g.nodes.some(n => n.id === `${type}_${i}`)) i++;
    const id = `${type}_${i}`;
    const r = boxRef.current?.getBoundingClientRect();
    const mid = r ? rf.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 }) : { x: 0, y: 0 };
    const position = { x: mid.x - 128, y: mid.y - 90 };
    // don't drop it on top of another node: move down to the first free spot
    const busy = (p: { x: number; y: number }) => rfNodes.some(n => {
      const h = n.measured?.height ?? 200;
      return Math.abs(n.position.x - p.x) < 280 && p.y < n.position.y + h + 20 && p.y + 200 > n.position.y - 20;
    });
    for (let k = 0; k < 15 && busy(position); k++) position.y += 120;
    setG(s => ({ ...s, nodes: [...s.nodes, { id, type, position }] }));
    inspect({ kind: "node", id }); setAddOpen(false); setFocusNew(id); touch();
    if (type === "tool") setPicker(id);
  };
  // once the new node is on the canvas: bring it into view at a readable size
  useEffect(() => {
    if (!focusNew || !rf.getNode(focusNew)) return;
    const n = rf.getNode(focusNew)!;
    rf.setCenter(n.position.x + 128, n.position.y + 90, { zoom: Math.max(rf.getZoom(), 1), duration: 400 });
    setFocusNew(null);
  }, [focusNew, rfNodes, rf]);
  const deleteNode = (id: string) => {
    setG(x => ({ ...x, nodes: x.nodes.filter(n => n.id !== id), edges: x.edges.filter(e => e.from !== id && e.to !== id) }));
    setSel(null); touch();
  };
  const duplicateNode = (id: string) => {
    const src = g.nodes.find(n => n.id === id); if (!src) return;
    let i = 2; while (g.nodes.some(n => n.id === `${id}_${i}`)) i++;
    const pos = rf.getNode(id)?.position ?? { x: 0, y: 0 };
    const copy = { ...structuredClone(src), id: `${id}_${i}`, position: { x: pos.x + 40, y: pos.y + 60 } };
    setG(x => ({ ...x, nodes: [...x.nodes, copy] })); setSel({ kind: "node", id: copy.id }); touch();
  };
  const setEdge = (gi: number, edge: FlowEdge) => { setG(x => ({ ...x, edges: x.edges.map((e, i) => i === gi ? edge : e) })); touch(); };
  const deleteEdge = (gi: number) => { setG(x => ({ ...x, edges: x.edges.filter((_, i) => i !== gi) })); setSel(null); touch(); };
  const moveEdge = (gi: number, dir: -1 | 1) => {
    setG(x => {
      const same = x.edges.map((e, i) => ({ e, i })).filter(o => o.e.from === x.edges[gi].from).map(o => o.i);
      const k = same.indexOf(gi), j = same[k + dir];
      if (j === undefined) return x;
      const edges = [...x.edges]; [edges[gi], edges[j]] = [edges[j], edges[gi]];
      return { ...x, edges };
    });
    setSel({ kind: "edge", gi: gi }); touch();
  };
  const onConnect = (c: Connection) => {
    if (!c.target || c.target === ANY) return;
    const from = c.source === ANY ? "*" : c.source!;
    if (c.sourceHandle?.startsWith("h-")) setEdge(Number(c.sourceHandle.slice(2)), { ...g.edges[Number(c.sourceHandle.slice(2))], to: c.target });
    else { setG(x => ({ ...x, edges: [...x.edges, { from, to: c.target! }] })); inspect({ kind: "edge", gi: g.edges.length }); touch(); }
  };
  const autoLayout = (dir: "horizontal" | "vertical") => {
    const pos = layout(g.nodes, g.edges, g.start, dir);
    setRfNodes(ns => ns.map(n => (pos[n.id] ? { ...n, position: pos[n.id] } : n)));
    setLayoutOpen(false); touch(); setTimeout(() => rf.fitView({ duration: 400 }), 50);
  };
  const save = () => {
    const pos = Object.fromEntries(rfNodes.map(n => [n.id, { x: Math.round(n.position.x), y: Math.round(n.position.y) }]));
    onSave({ ...graph, start: g.start, nodes: g.nodes.map(n => ({ ...n, position: pos[n.id] ?? n.position })), edges: g.edges,
             variables: Object.keys(g.variables).length ? g.variables : undefined });
    setDirty(false);
  };

  const actions: CanvasActions = {
    open: id => { if (id !== ANY) inspect({ kind: "node", id }); },
    menu: (id, a) => {
      if (a === "rename") { inspect({ kind: "node", id }); setTimeout(() => renameRef.current?.select(), 50); }
      else if (a === "logs") onViewLogs?.(id);
      else if (a === "duplicate") duplicateNode(id);
      else deleteNode(id);
    },
    pickTool: id => setPicker(id), selectEdge: gi => inspect({ kind: "edge", gi }), moveEdge,
    setMode: (id, mode) => {
      const cur = g.nodes.find(nd => nd.id === id);
      if (!cur || (mode === "static") === !!cur.say) return;
      if (mode === "static") patchNode(id, { say: stash.current[id] ?? { ar: "", en: "" } });
      else { if (cur.say) stash.current[id] = cur.say; patchNode(id, { say: undefined }); }
    },
  };

  const node = sel?.kind === "node" ? g.nodes.find(n => n.id === sel.id) : undefined;
  const edge = sel?.kind === "edge" ? g.edges[sel.gi] : undefined;
  const showGlobal = !!globalPanel && (panel === "global" || (panel === "inspector" && !sel));   // nothing selected → global
  const side = aside ?? (showGlobal ? globalPanel : (
    <div className="h-full overflow-y-auto p-3">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold">{panel === "variables" ? "Variables" : node ? "Node inspector" : edge ? "Transition" : "Inspector"}</div>
        {(panel !== "inspector" || sel) && <button className="text-muted hover:text-ink" onClick={() => { setSel(null); setPanel(home); }}>✕</button>}
      </div>
      {panel === "variables" ? <Variables vars={g.variables} infer={graph.infer} system={system} custom={custom} onChange={v => { setG(x => ({ ...x, variables: v })); touch(); }} />
        : node ? <NodeInspector key={node.id} node={node} isStart={node.id === g.start} tools={tools} skills={skills}
            others={g.nodes.map(n => n.id).filter(id => id !== node.id && id !== DONE)}
            onConnectFrom={src => { setG(x => ({ ...x, edges: [...x.edges, { from: src, to: node.id }] })); inspect({ kind: "edge", gi: g.edges.length }); touch(); }}
            onConnectTo={dst => { setG(x => ({ ...x, edges: [...x.edges, { from: node.id, to: dst }] })); inspect({ kind: "edge", gi: g.edges.length }); touch(); }}
            variables={Object.keys(g.variables)} renameRef={renameRef} toolInfo={toolInfo} varGroups={varGroups(node.id)}
            globalEdges={g.edges.map((e, gi) => ({ e, gi })).filter(x => x.e.from === "*" && x.e.to === node.id)}
            onChange={p => patchNode(node.id, p)} onRename={next => renameNode(node.id, next)} onPickTool={() => setPicker(node.id)}
            onStart={() => { setG(x => ({ ...x, start: node.id })); touch(); }} onDelete={() => deleteNode(node.id)}
            onAddGlobal={() => { setG(x => ({ ...x, edges: [...x.edges, { from: "*", to: node.id, when: { llm: "" } }] })); setSel({ kind: "edge", gi: g.edges.length }); touch(); }}
            onOpenEdge={gi => setSel({ kind: "edge", gi })} />
        : edge && sel?.kind === "edge" ? <EdgeInspector key={sel.gi} edge={edge} order={g.edges.filter(e => e.from === edge.from).indexOf(edge) + 1}
            fromTool={["tool", "agent"].includes(g.nodes.find(n => n.id === edge.from)?.type ?? "")} targets={g.nodes.map(n => n.id)}
            onChange={e => setEdge(sel.gi, e)} onDelete={() => deleteEdge(sel.gi)} />
        : <div className="space-y-2 text-sm text-muted">
            <p>Click a node's ⚙ or title to edit it, or a transition row to edit its condition.</p>
            <p>Drag from a row's ● to another node to connect it; the dashed row's ● adds a new transition. Transitions are tried top to bottom — use ▲▼ to reorder.</p>
          </div>}
    </div>));

  return (
    <div className="space-y-2">
      {converted && <div className="rounded-lg bg-soft px-3 py-2 text-xs text-muted">This flow was written as steps. The canvas shows it as a graph
        (each step is reached from “Anywhere” when its conditions hold); saving keeps the same behaviour.</div>}
      <div className={cx("grid gap-3", aside ? "lg:grid-cols-[1fr_26rem]" : showGlobal ? "lg:grid-cols-[1fr_30rem]" : "lg:grid-cols-[1fr_24rem]")}>
        <div ref={boxRef} className="relative h-[70vh] overflow-hidden rounded-xl border border-line bg-panel">
          <Actions.Provider value={actions}>
            <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} fitView minZoom={0.1}
              onNodesChange={c => { onNodesChange(c); if (c.some(x => x.type === "position" && !x.dragging)) touch(); }}
              onConnect={onConnect} onEdgeClick={(_, e) => actions.selectEdge(Number(e.id.slice(1)))}
              onNodeClick={(_, n) => { if (n.id !== ANY) actions.open(n.id); }}
              onPaneClick={() => { setSel(null); setPanel(p => (p === "inspector" ? home : p)); setAddOpen(false); setLayoutOpen(false); }} deleteKeyCode={null}>
              <Background gap={20} /><MiniMap pannable zoomable /><Controls />
            </ReactFlow>
          </Actions.Provider>
          {/* floating toolbar, like Hamsa: add node · variables · auto-layout · global settings */}
          <div className="absolute left-3 top-1/2 z-10 flex -translate-y-1/2 flex-col gap-2">
            <ToolButton label="Add node" active={addOpen} accent onClick={() => { setAddOpen(o => !o); setLayoutOpen(false); }}><NodeIcon type="plus" size={20} stroke={2.2} /></ToolButton>
            <ToolButton label="Variables" active={panel === "variables" && !aside} onClick={() => { setPanel(p => (p === "variables" ? home : "variables")); setSel(null); }}><span className="text-[15px] font-semibold">(x)</span></ToolButton>
            <ToolButton label="Auto layout" active={layoutOpen} onClick={() => { setLayoutOpen(o => !o); setAddOpen(false); }}><NodeIcon type="layout" size={18} /></ToolButton>
            <ToolButton label="Global settings" active={showGlobal && !aside} onClick={() => { setSel(null); setPanel("global"); }}><NodeIcon type="settings" size={18} /></ToolButton>
          </div>
          {addOpen && (
            <div className="absolute left-[4.75rem] top-3 z-20 flex max-h-[calc(100%-1.5rem)] w-[22.5rem] flex-col overflow-hidden rounded-xl border border-line bg-panel shadow-xl" role="menu" aria-label="Add node">
              <div className="px-5 pb-3 pt-4">
                <div className="text-sm font-semibold">Add Node</div>
                <div className="mt-0.5 text-xs text-muted">Choose a node type to add to your workflow</div>
              </div>
              <div className="mx-5 border-t border-line" />
              <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
                {Object.entries(TYPES).map(([type, t]) => (
                  <button key={type} role="menuitem" className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-soft" onClick={() => addNode(type)}>
                    <span className={cx("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-line bg-soft/70", t.tint)}><NodeIcon type={type} size={17} stroke={1.8} /></span>
                    <span className="min-w-0"><span className="block text-sm font-medium">{t.label}</span><span className="block text-xs text-muted">{t.hint}</span></span>
                  </button>))}
              </div>
            </div>
          )}
          {layoutOpen && (
            <div className="absolute left-16 top-1/2 z-20 w-40 rounded-xl border border-line bg-panel p-1 shadow-xl">
              <button className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-soft" onClick={() => autoLayout("vertical")}>↧ Vertical</button>
              <button className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-soft" onClick={() => autoLayout("horizontal")}>↦ Horizontal</button>
            </div>
          )}
          <div className="absolute right-3 top-3 z-10 flex items-center gap-2">
            <ValidationBadge issues={issues} open={issuesOpen} onToggle={() => setIssuesOpen(o => !o)}
              onFocus={id => { inspect({ kind: "node", id }); centre(id); setIssuesOpen(false); }} />
            {dirty && <Badge tone="warn">unsaved</Badge>}
            <Button kind="primary" disabled={!dirty || saving} onClick={save}>{saving ? "Saving…" : "Save flow to draft"}</Button>
          </div>
        </div>
        <div className="h-[70vh] min-h-0 overflow-hidden rounded-xl border border-line bg-panel">{side}</div>
      </div>
      {picker && <ToolPicker tools={tools} info={toolInfo} current={g.nodes.find(n => n.id === picker)?.tool}
        onPick={name => { patchNode(picker, { tool: name }); setPicker(null); }} onClose={() => setPicker(null)} />}
    </div>
  );
}

function ToolButton({ label, active, accent, onClick, children }: { label: string; active?: boolean; accent?: boolean; onClick: () => void; children: ReactNode }) {
  return <button title={label} aria-label={label} onClick={onClick}
    className={cx("flex h-12 w-12 items-center justify-center rounded-xl text-base transition",
      accent ? "bg-brand text-white hover:opacity-90" : cx("border-2 border-line text-ink", active ? "bg-soft" : "bg-panel hover:bg-soft"))}>{children}</button>;
}

function ToolPicker({ tools, info, current, onPick, onClose }: {
  tools: string[]; info?: CanvasProps["toolInfo"]; current?: string; onPick: (t: string) => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const shown = tools.filter(t => `${t} ${info?.[t]?.description ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="flex max-h-[80vh] w-full max-w-xl flex-col rounded-xl bg-panel shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <div className="text-sm font-semibold">Tool configuration</div>
          <button className="text-muted hover:text-ink" onClick={onClose}>✕</button>
        </div>
        <div className="px-4 py-2"><input id="tool-search" autoFocus className="w-full" placeholder="Search tools…" value={q} onChange={e => setQ(e.target.value)} /></div>
        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-4 pb-3">
          {shown.map(t => (
            <div key={t} className={cx("flex items-start gap-3 rounded-lg border px-3 py-2", t === current ? "border-accent bg-accent/5" : "border-line")}>
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-xs font-semibold">{t}</div>
                {info?.[t]?.description && <div className="line-clamp-2 text-[11px] text-muted">{info[t].description}</div>}
              </div>
              {info?.[t] && <><Badge>{info[t].source}</Badge><Badge tone={info[t].kind === "read" ? "neutral" : "warn"}>{info[t].kind}</Badge></>}
              <Button kind={t === current ? "primary" : "default"} onClick={() => onPick(t)}>{t === current ? "Selected" : "Select"}</Button>
            </div>))}
          {shown.length === 0 && <div className="py-6 text-center text-sm text-muted">No tool matches. Add tools to this agent on the Tools page.</div>}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- side panels

const lbl = (text: string, input: React.ReactNode, help?: string) => (
  <label className="block"><div className="mb-1 text-xs font-medium">{text}</div>{input}
    {help && <div className="mt-1 text-[11px] text-muted">{help}</div>}</label>
);

function JsonField({ value, onChange, rows = 3, id }: { value: unknown; onChange: (v: unknown) => void; rows?: number; id: string }) {
  const [text, setText] = useState(value == null ? "" : JSON.stringify(value, null, 2));
  const [bad, setBad] = useState(false);
  return <textarea id={id} rows={rows} className={cx("w-full font-mono text-[11px]", bad && "border-bad")} value={text}
    onChange={e => { setText(e.target.value); try { onChange(e.target.value.trim() ? JSON.parse(e.target.value) : undefined); setBad(false); } catch { setBad(true); } }} />;
}

/** Number field that stores nothing when emptied. */
function NumField({ id, value, min, max, step, onChange }: { id: string; value: number | undefined; min?: number; max?: number; step?: number; onChange: (v: number | undefined) => void }) {
  return <input id={id} type="number" className="w-full" min={min} max={max} step={step} value={value ?? ""}
    onChange={e => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />;
}

/** A line the agent says, in both languages. */
function LangFields({ id, value, onChange }: { id: string; value: Record<string, string> | undefined; onChange: (v: Record<string, string> | undefined) => void }) {
  const put = (lang: string, text: string) => { const next = { ...value, [lang]: text }; onChange(Object.values(next).some(x => x) ? next : undefined); };
  return (
    <div className="space-y-1.5">
      <input id={`${id}-ar`} dir="rtl" className="w-full text-sm" placeholder="العربية" value={value?.ar ?? ""} onChange={e => put("ar", e.target.value)} />
      <input id={`${id}-en`} className="w-full text-sm" placeholder="English" value={value?.en ?? ""} onChange={e => put("en", e.target.value)} />
    </div>
  );
}

/** Conversation node: collect keypad digits (DTMF) into a variable. */
function CaptureFields({ node, onChange }: { node: FlowNode; onChange: (p: Partial<FlowNode>) => void }) {
  const c = node.dtmf_capture;
  const set = (patch: NonNullable<FlowNode["dtmf_capture"]>) => onChange({ dtmf_capture: { ...c, ...patch } });
  const ends = c?.end_keys ?? ["#"];
  return (
    <div className="space-y-2 rounded-lg border border-line p-2">
      <label className="flex items-center gap-2 text-xs font-medium"><input id="node-dtmf-on" type="checkbox" checked={!!c}
        onChange={e => onChange({ dtmf_capture: e.target.checked ? { variable: "", max_digits: 10, end_keys: ["#"], timeout_s: 5 } : undefined })} />
        Collect keypad digits (DTMF) on this step</label>
      {c && <>
        {lbl("Variable", <input id="node-dtmf-var" className="w-full font-mono" placeholder="account_number" value={c.variable ?? ""} onChange={e => set({ variable: e.target.value })} />,
          "snake_case. The digits are saved here and the flow moves on (use “has account_number” as the next transition). On phone calls only.")}
        <div className="grid grid-cols-2 gap-2">
          {lbl("Max digits (1-20)", <NumField id="node-dtmf-max" min={1} max={20} value={c.max_digits} onChange={v => set({ max_digits: v })} />)}
          {lbl("Pause that ends it (s)", <NumField id="node-dtmf-timeout" min={1} max={30} value={c.timeout_s} onChange={v => set({ timeout_s: v })} />)}
        </div>
        <div className="flex items-center gap-3 text-xs"><span className="font-medium">Ends with</span>
          {["#", "*"].map(k => <label key={k} className="flex items-center gap-1"><input type="checkbox" checked={ends.includes(k)}
            onChange={e => set({ end_keys: e.target.checked ? [...ends, k] : ends.filter(x => x !== k) })} />{k}</label>)}</div>
      </>}
    </div>
  );
}

/** Tool node: error behaviour, longest wait, the "one moment" line and the spoken result. */
function ToolOptions({ node, onChange }: { node: FlowNode; onChange: (p: Partial<FlowNode>) => void }) {
  return (
    <div className="space-y-2 rounded-lg border border-line p-2">
      {lbl("If the tool fails", <select id="node-on-error" className="w-full" value={node.on_error ?? "continue"}
        onChange={e => onChange({ on_error: e.target.value === "continue" ? undefined : e.target.value })}>
        <option value="continue">Continue — follow the “On failure” transition</option>
        <option value="retry">Retry, then follow “On failure”</option>
        <option value="fail">Fail — hand the call to a person</option></select>)}
      <div className="grid grid-cols-2 gap-2">
        {node.on_error === "retry" && lbl("Extra attempts (1-5)", <NumField id="node-retries" min={1} max={5} value={node.retries} onChange={v => onChange({ retries: v })} />)}
        {lbl("Longest wait (s)", <NumField id="node-tool-timeout" min={0.1} max={120} step={0.5} value={node.timeout_s} onChange={v => onChange({ timeout_s: v })} />, "Empty: no limit")}
      </div>
      {lbl("While it runs, say", <LangFields id="node-processing" value={node.processing} onChange={v => onChange({ processing: v })} />, "Instead of the standard “one moment”. Empty: standard.")}
      {lbl("When it succeeds, say", <LangFields id="node-tool-say" value={node.say} onChange={v => onChange({ say: v })} />,
        "Optional. Use {{ variable }} for values saved from the result. Empty: say nothing (the next step talks).")}
    </div>
  );
}

/** Settings node: what changes from this step on. An empty field keeps what is already in force. */
function SettingsOptions({ node, onChange }: { node: FlowNode; onChange: (p: Partial<FlowNode>) => void }) {
  const ov = (node.overrides ?? {}) as Record<string, unknown>;
  const section = (k: string) => (ov[k] ?? {}) as Record<string, unknown>;
  const put = (k: string, v: unknown) => { const next = { ...ov }; if (v === undefined || v === "") delete next[k]; else next[k] = v; onChange({ overrides: next }); };
  const putIn = (k: string, f: string, v: unknown) => {
    const sec = { ...section(k) }; if (v === undefined || v === "") delete sec[f]; else sec[f] = v;
    put(k, Object.keys(sec).length ? sec : undefined);
  };
  const catalog = useQuery({ queryKey: ["voice-catalog"], queryFn: api.voiceCatalog });
  const call = section("call"), llm = section("llm"), voice = section("voice");
  const num = (k: string) => call[k] as number | undefined;
  return (
    <div className="space-y-3">
      <p className="text-[11px] text-muted">These apply from this step until another “Change settings” step changes them again. Leave a field empty to keep what is in force.</p>
      {lbl("System prompt", <textarea id="node-ov-prompt" rows={5} dir="auto" className="w-full text-xs" value={String(ov.system_prompt ?? "")}
        onChange={e => put("system_prompt", e.target.value)} />, "Replaces the agent's system prompt for the steps after this one.")}
      <div className="rounded-lg border border-line p-2"><div className="mb-1 text-xs font-semibold">Voice & speech recognition</div>
        {(["ar", "en"] as const).map(l => (
          <label key={l} className="mb-1.5 block text-[11px]"><span className="text-muted">Voice · {l === "ar" ? "Arabic" : "English"}</span>
            <select id={`node-ov-voice-${l}`} className="mt-0.5 w-full text-xs" value={String(voice[l] ?? "")} onChange={e => putIn("voice", l, e.target.value)}>
              <option value="">keep the current voice</option>
              {(catalog.data ?? []).filter(v => v.language === l).map(v => <option key={`${v.provider}-${v.voice}`} value={v.voice}>{v.voice} · {v.gender}{v.dialect ? ` · ${v.dialect}` : ""}</option>)}
            </select></label>))}
        {lbl("Speech-to-text model", <input id="node-ov-stt" className="w-full font-mono text-xs" placeholder="e.g. whisper-large-v3-turbo" value={String(ov.stt_model ?? "")}
          onChange={e => put("stt_model", e.target.value)} />, "A model of the agent's speech-to-text connection.")}</div>
      <div className="rounded-lg border border-line p-2"><div className="mb-1 text-xs font-semibold">Language model</div>
        <div className="grid grid-cols-2 gap-2">
          {lbl("Model", <input id="node-ov-model" className="w-full font-mono text-xs" value={String(llm.model ?? "")} onChange={e => putIn("llm", "model", e.target.value)} />)}
          {lbl("Temperature (0-2)", <NumField id="node-ov-temp" min={0} max={2} step={0.05} value={llm.temperature as number | undefined} onChange={v => putIn("llm", "temperature", v)} />)}
        </div><div className="text-[11px] text-muted">A model of the agent's language-model connection.</div></div>
      <div className="rounded-lg border border-line p-2"><div className="mb-1 text-xs font-semibold">Call settings</div>
        <label className="mb-2 block text-[11px]"><span className="text-muted">Interruptions</span>
          <select id="node-ov-interrupt" className="mt-0.5 w-full text-xs" value={call.interrupt === undefined ? "" : String(call.interrupt)}
            onChange={e => putIn("call", "interrupt", e.target.value === "" ? undefined : e.target.value === "true")}>
            <option value="">keep</option><option value="true">allowed</option><option value="false">not allowed</option></select></label>
        <div className="grid grid-cols-2 gap-2">
          {lbl("Response delay (ms, 100-1500)", <NumField id="node-ov-delay" min={100} max={1500} step={50} value={num("response_delay_ms")} onChange={v => putIn("call", "response_delay_ms", v)} />)}
          {lbl("Inactivity timeout (s, 5-60)", <NumField id="node-ov-inactive" min={5} max={60} value={num("inactivity_s")} onChange={v => putIn("call", "inactivity_s", v)} />)}
          {lbl("Min. interruption (ms, 200-1500)", <NumField id="node-ov-minint" min={200} max={1500} step={50} value={num("min_interruption_ms")} onChange={v => putIn("call", "min_interruption_ms", v)} />)}
          {lbl("VAD threshold (0.2-0.9)", <NumField id="node-ov-vad" min={0.2} max={0.9} step={0.05} value={num("vad_threshold")} onChange={v => putIn("call", "vad_threshold", v)} />)}
        </div></div>
    </div>
  );
}

/** Agent node: which agent takes the call, what it inherits, what is said first. */
function AgentOptions({ node, onChange }: { node: FlowNode; onChange: (p: Partial<FlowNode>) => void }) {
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents, staleTime: 30_000 });
  const known = (agents.data ?? []).some(a => a.id === node.agent);
  return (
    <div className="space-y-2 rounded-lg border border-line p-2">
      {lbl("Agent that takes the call", <select id="node-agent" className="w-full" value={known ? node.agent : node.agent ? "__other" : ""}
        onChange={e => onChange({ agent: e.target.value === "__other" ? node.agent : e.target.value })}>
        <option value="">choose an agent…</option>
        {(agents.data ?? []).map(a => <option key={a.id} value={a.id}>{a.name} · {a.id}</option>)}
        {node.agent && !known && <option value="__other">{node.agent} (not in this project)</option>}</select>,
        "The agent must be published. It answers with its own voice, models, skills and tools.")}
      {lbl("Or an agent id / template", <input id="node-agent-id" className="w-full font-mono text-xs" placeholder="{{ team_agent }}" value={node.agent ?? ""} onChange={e => onChange({ agent: e.target.value })} />)}
      <label className="flex items-start gap-2 text-xs"><input id="node-handoff-history" type="checkbox" className="mt-0.5" checked={!!node.handoff_history}
        onChange={e => onChange({ handoff_history: e.target.checked || undefined })} /><span>Pass the conversation so far
        <span className="block text-[11px] text-muted">The next agent continues without a new greeting.</span></span></label>
      <label className="flex items-start gap-2 text-xs"><input id="node-handoff-vars" type="checkbox" className="mt-0.5" checked={!!node.handoff_variables}
        onChange={e => onChange({ handoff_variables: e.target.checked || undefined })} /><span>Pass the collected variables</span></label>
      {lbl("Say before the hand-off", <LangFields id="node-agent-say" value={node.say} onChange={v => onChange({ say: v })} />, "Optional. {{ variable }} allowed.")}
      <p className="text-[11px] text-muted">If the agent can't take the call (unknown, not published, or too many hand-offs), the “On failure” transition runs — without one, a person takes over.</p>
    </div>
  );
}

/** Transfer node: its own destination, warm / cold, announcement, ring timeout, SIP headers. */
function TransferOptions({ node, onChange }: { node: FlowNode; onChange: (p: Partial<FlowNode>) => void }) {
  const cold = node.transfer_type === "cold";
  return (
    <div className="space-y-2 rounded-lg border border-line p-2">
      {lbl("Transfer to", <input id="node-destination" className="w-full font-mono" placeholder="+966112345678 or an extension" value={node.destination ?? ""}
        onChange={e => onChange({ destination: e.target.value || undefined })} />, "Empty: the agent's human-transfer destination (Settings). {{ variable }} allowed.")}
      {lbl("Type", <select id="node-transfer-type" className="w-full" value={cold ? "cold" : "warm"}
        onChange={e => onChange({ transfer_type: e.target.value === "cold" ? "cold" : undefined })}>
        <option value="warm">Warm — say a line first, then connect</option><option value="cold">Cold — connect at once, silently</option></select>)}
      {!cold && lbl("Announcement", <LangFields id="node-transfer-say" value={node.say} onChange={v => onChange({ say: v })} />, "Empty: the standard hand-off line. {{ variable }} allowed.")}
      {lbl("Ring timeout (s)", <NumField id="node-transfer-timeout" min={1} max={60} value={node.timeout_s} onChange={v => onChange({ timeout_s: v })} />, "1-60. Sent to the phone system with the transfer.")}
      {lbl("SIP headers (JSON)", <JsonField id="node-headers" rows={3} value={node.headers ?? {}} onChange={v => onChange({ headers: v as FlowNode["headers"] })} />, '{"X-Customer-Id": "{{ customer_id }}"}')}
    </div>
  );
}

function NodeInspector({ node, isStart, tools, skills, variables, renameRef, toolInfo, globalEdges, onChange, onRename, onPickTool, onStart,
  onDelete, onAddGlobal, onOpenEdge, others, onConnectFrom, onConnectTo, varGroups }: {
  node: FlowNode; isStart: boolean; tools: string[]; skills: string[]; variables: string[]; varGroups: VarGroup[];
  others: string[]; onConnectFrom: (src: string) => void; onConnectTo: (dst: string) => void;
  renameRef: React.MutableRefObject<HTMLInputElement | null>; toolInfo?: CanvasProps["toolInfo"];
  globalEdges: { e: FlowEdge; gi: number }[];
  onChange: (p: Partial<FlowNode>) => void; onRename: (id: string) => void; onPickTool: () => void; onStart: () => void;
  onDelete: () => void; onAddGlobal: () => void; onOpenEdge: (gi: number) => void;
}) {
  const [id, setId] = useState(node.id);
  const toggle = (list: string[] | undefined, v: string) => (list ?? []).includes(v) ? (list ?? []).filter(x => x !== v) : [...(list ?? []), v];
  const t = TYPES[node.type] ?? TYPES.conversation;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className={cx("flex h-7 w-7 items-center justify-center rounded-md text-white", t.color)}><NodeIcon type={node.type} size={15} /></span>
        <div><div className="text-sm font-semibold">{t.label}</div><div className="text-[11px] text-muted">{t.hint}</div></div>
      </div>
      {lbl("Name", <div className="flex gap-2"><input ref={renameRef} id="node-id" className="min-w-0 flex-1 font-mono" value={id}
        onChange={e => setId(e.target.value.replace(/[^\w-]/g, ""))} onKeyDown={e => { if (e.key === "Enter") onRename(id); }} />
        <Button onClick={() => onRename(id)} disabled={id === node.id}>Rename</Button></div>)}
      <div className="rounded-lg border border-line p-2">
        <div className="mb-1 text-xs font-medium">Connect</div>
        <div className="grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1.5 text-[11px]">
          <span className="text-muted">From</span>
          <select id="connect-from" className="text-xs" value="" onChange={e => e.target.value && onConnectFrom(e.target.value)}>
            <option value="">add a transition from…</option>{others.map(o => <option key={o} value={o}>{o}</option>)}</select>
          {!(node.type === "end" || node.type === "transfer") && <>
            <span className="text-muted">To</span>
            <select id="connect-to" className="text-xs" value="" onChange={e => e.target.value && onConnectTo(e.target.value)}>
              <option value="">add a transition to…</option>{others.map(o => <option key={o} value={o}>{o}</option>)}</select>
          </>}
        </div>
        <div className="mt-1 text-[10px] text-muted">Or drag from a transition row's ● on the canvas. Then set the condition.</div>
      </div>
      {node.type === "conversation" && <>
        <div role="tablist" className="grid grid-cols-2 gap-1 rounded-lg bg-soft p-1 text-xs">
          {([["prompt", "Prompt — the agent writes it"], ["static", "Static — said exactly"]] as const).map(([m, label]) => (
            <button key={m} role="tab" aria-selected={(m === "static") === !!node.say} id={`node-mode-${m}`}
              onClick={() => onChange({ say: m === "static" ? { ar: "", en: "" } : undefined })}
              className={cx("rounded-md px-2 py-1.5", (m === "static") === !!node.say ? "bg-panel font-medium shadow-sm" : "text-muted hover:text-ink")}>{label}</button>))}
        </div>
        {node.say && <>
          <VariablePicker groups={varGroups} fields={[
            { id: "node-say-ar", value: node.say.ar ?? "", onChange: v => onChange({ say: { ...node.say, ar: v } }) },
            { id: "node-say-en", value: node.say.en ?? "", onChange: v => onChange({ say: { ...node.say, en: v } }) }]} />
          {lbl("Message (Arabic)", <textarea id="node-say-ar" rows={2} dir="rtl" className="w-full text-sm" value={node.say.ar ?? ""} onChange={e => onChange({ say: { ...node.say, ar: e.target.value } })} />)}
          {lbl("Message (English)", <textarea id="node-say-en" rows={2} className="w-full text-sm" value={node.say.en ?? ""} onChange={e => onChange({ say: { ...node.say, en: e.target.value } })} />,
            "Said word for word when the flow reaches this step — no model call. Use {{ variable }} to fill in values.")}
          <label className="flex items-center gap-2 text-xs"><input id="node-skip" type="checkbox" checked={!!node.skip_response}
            onChange={e => onChange({ skip_response: e.target.checked || undefined })} />Don't wait for the caller — go straight to the next step</label>
        </>}
        <VariablePicker groups={varGroups} fields={[{ id: "node-instr", value: node.instructions ?? "", onChange: v => onChange({ instructions: v }) }]} />
        {lbl(node.say ? "Prompt for the caller's reply (optional)" : "Prompt (what the agent does at this step)", <textarea id="node-instr" rows={node.say ? 4 : 9} className={cx("w-full text-xs", !node.instructions && !node.say && "border-warn")}
          dir="auto" value={node.instructions ?? ""} placeholder={'e.g. Ask exactly: "Would you like the earliest available appointment, or a specific date?"'}
          onChange={e => onChange({ instructions: e.target.value })} />,
          node.say ? "If the caller replies and no transition fires, the agent follows this prompt. Empty: the message is said again."
          : node.instructions ? "Rules for the whole call belong in Global settings (⚙)." : "A prompt is required — what should the agent say or do here?")}
        <div><div className="mb-1 text-xs font-medium">Variables to collect from the caller's words</div>
          {variables.map(v => (
            <label key={v} className="flex items-center gap-1.5 text-[11px]"><input type="checkbox" checked={(node.extract ?? []).includes(v)}
              onChange={() => onChange({ extract: toggle(node.extract, v) })} /><span className="font-mono">{v}</span></label>))}
          {variables.length === 0 && <div className="text-[11px] text-muted">Declare them with the (x) button.</div>}</div>
        <div><div className="mb-1 text-xs font-medium">Tools the model may call here</div>
          <div className="max-h-40 space-y-0.5 overflow-y-auto">{tools.map(tl => (
            <label key={tl} className="flex items-center gap-1.5 text-[11px]"><input type="checkbox" checked={(node.tools ?? []).includes(tl)}
              onChange={() => onChange({ tools: toggle(node.tools, tl) })} /><span className="font-mono">{tl}</span></label>))}</div></div>
        <CaptureFields node={node} onChange={onChange} />
        <details className="rounded-lg border border-line p-2" open={!!node.llm?.model || node.llm?.temperature != null}>
          <summary className="cursor-pointer text-xs font-medium">Model for this step</summary>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {lbl("Model", <input id="node-llm-model" className="w-full font-mono text-xs" placeholder="the agent's model" value={node.llm?.model ?? ""}
              onChange={e => onChange({ llm: { ...node.llm, model: e.target.value || undefined } })} />)}
            {lbl("Temperature (0-2)", <NumField id="node-llm-temp" min={0} max={2} step={0.05} value={node.llm?.temperature}
              onChange={v => onChange({ llm: { ...node.llm, temperature: v } })} />)}
          </div>
          <div className="mt-1 text-[11px] text-muted">Replies at this step use this model of the agent's language-model connection. Empty: the agent's own.</div>
        </details>
        {lbl("Tools the platform calls on arrival (JSON)", <JsonField id="node-auto" value={node.auto_call} onChange={v => onChange({ auto_call: v as FlowNode["auto_call"] })} />,
          '[{"tool": "name", "args": {"id": "slots.order_id"}, "blocking": true}]')}
      </>}
      {node.type === "tool" && <>
        <div><div className="mb-1 text-xs font-medium">Tool</div>
          <button className="w-full rounded-lg border border-line px-3 py-2 text-left hover:bg-soft" onClick={onPickTool}>
            <div className="font-mono text-xs font-semibold">{node.tool || "Choose a tool…"}</div>
            {node.tool && toolInfo?.[node.tool]?.description && <div className="line-clamp-2 text-[11px] text-muted">{toolInfo[node.tool].description}</div>}
            <div className="text-[10px] text-accent-text">click to change</div>
          </button></div>
        {lbl("Arguments (JSON)", <JsonField id="node-args" value={node.args} onChange={v => onChange({ args: v as FlowNode["args"] })} />,
          'Values: "slots.x" (collected), "parsed.mobile", "=text" (literal), "{{ var }}" (template)')}
        {lbl("Outputs (JSON)", <JsonField id="node-outputs" rows={3} value={node.outputs ?? {}} onChange={v => onChange({ outputs: v as FlowNode["outputs"] })} />,
          '{"patient_count": "result.count"} — values from the tool\'s result saved as variables on success')}
        <ToolOptions node={node} onChange={onChange} />
      </>}
      {node.type === "set" && lbl("Values (JSON) · \"=text\" literal, \"slots.x\" a value, \"{{ … }}\" a template", <JsonField id="node-set" rows={5} value={node.set} onChange={v => onChange({ set: v as FlowNode["set"] })} />,
        '{"tries": "=1", "old_value": null}  — null forgets a value')}
      {node.type === "transfer" && <>
        {lbl("Reason (logged)", <input id="node-reason" className="w-full" value={node.reason ?? ""} onChange={e => onChange({ reason: e.target.value })} />)}
        <TransferOptions node={node} onChange={onChange} />
      </>}
      {node.type === "end" && <>
        {lbl("Final message (Arabic)", <input id="node-say-ar" dir="rtl" className="w-full" value={node.say?.ar ?? ""} onChange={e => onChange({ say: { ...node.say, ar: e.target.value } })} />)}
        {lbl("Final message (English)", <input id="node-say-en" className="w-full" value={node.say?.en ?? ""} onChange={e => onChange({ say: { ...node.say, en: e.target.value } })} />, "Leave both empty for a silent end. Use {{ variable }} to fill in values.")}
      </>}
      {node.type === "settings" && <SettingsOptions node={node} onChange={onChange} />}
      {node.type === "agent" && <AgentOptions node={node} onChange={onChange} />}
      {node.type === "skill" && lbl("Continue in skill", <select id="node-skill" className="w-full" value={node.skill ?? ""} onChange={e => onChange({ skill: e.target.value })}>
        <option value="">choose…</option>{skills.map(s => <option key={s}>{s}</option>)}</select>)}
      <div className="rounded-lg border border-line p-2">
        <div className="text-xs font-medium">Global</div>
        <div className="mb-1 text-[11px] text-muted">Reach this step from anywhere in the flow when a condition holds.</div>
        {globalEdges.map(({ e, gi }) => (
          <button key={gi} className="block w-full truncate rounded px-1.5 py-1 text-left text-[11px] hover:bg-soft" onClick={() => onOpenEdge(gi)}>✳ when {conditionLabel(e)}</button>))}
        <Button kind="ghost" onClick={onAddGlobal}>+ Reach from anywhere when…</Button>
      </div>
      <div className="flex gap-2 pt-1">
        {!isStart && <Button onClick={onStart}>Make start</Button>}
        <Button kind="danger" onClick={onDelete}>Delete node</Button>
      </div>
    </div>
  );
}

const MODES = ["always", "llm", "replied", "filled", "empty", "equals", "stage", "dtmf", "json"] as const;
const KEYPAD = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];

/** Options of a transition: ask yes / no first, arrive silently, and (global ones) return afterwards. */
function EdgeOptions({ edge, onChange }: { edge: FlowEdge; onChange: (e: FlowEdge) => void }) {
  const global = edge.from === "*";
  const flip = (k: "back" | "confirm" | "silent", on: boolean) => { const next = { ...edge }; if (on) next[k] = true as never; else delete next[k]; onChange(next); };
  return (
    <div className="space-y-1.5 rounded-lg border border-line p-2 text-xs">
      <div className="font-medium">When this transition is taken</div>
      <label className="flex items-start gap-2"><input id="edge-confirm" type="checkbox" className="mt-0.5" checked={!!edge.confirm} onChange={e => flip("confirm", e.target.checked)} />
        <span>Ask the caller to confirm first<span className="block text-[11px] text-muted">The agent asks “Just to confirm…”; only a yes goes on.</span></span></label>
      <label className="flex items-start gap-2"><input id="edge-silent" type="checkbox" className="mt-0.5" checked={!!edge.silent} onChange={e => flip("silent", e.target.checked)} />
        <span>Go there silently<span className="block text-[11px] text-muted">The step says nothing (no goodbye, no hand-off line).</span></span></label>
      {global && <label className="flex items-start gap-2"><input id="edge-back" type="checkbox" className="mt-0.5" checked={!!edge.back} onChange={e => flip("back", e.target.checked)} />
        <span>Return to where the caller was afterwards<span className="block text-[11px] text-muted">Once the caller has replied to this step, the call goes back to the step they interrupted.</span></span></label>}
    </div>
  );
}
const CMP_KEYS = CMP.map(c => c[0]);

/** One comparison: variable · operator · value, stored as {op: {variable: value}}. */
function CompareFields({ when, onChange }: { when: Record<string, unknown>; onChange: (w: Record<string, unknown>) => void }) {
  const op = CMP_KEYS.find(k => k in when) ?? "ne";
  const [variable, value] = Object.entries((when[op] ?? {}) as Record<string, unknown>)[0] ?? ["", ""];
  const numeric = ["gt", "gte", "lt", "lte"].includes(op);
  const put = (o: string, v: string, val: unknown) => onChange({ [o]: { [v]: numeric && o === op && val !== "" && !isNaN(Number(val)) ? Number(val) : val } });
  return (
    <div className="space-y-2">
      {lbl("Variable", <input id="edge-cmp-var" className="w-full font-mono" value={variable} onChange={e => put(op, e.target.value, value)} />)}
      {lbl("Operator", <select id="edge-cmp-op" className="w-full" value={op} onChange={e => put(e.target.value, variable, value)}>
        {CMP.map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select>)}
      {lbl(op === "regex" ? "Pattern (regular expression)" : "Value", <input id="edge-cmp-val" className="w-full font-mono" value={String(value ?? "")} onChange={e => put(op, variable, e.target.value)} />,
        numeric ? "Compared as numbers; a value that isn't a number never matches." : undefined)}
    </div>
  );
}

/** Header pill: the flow is fine / has warnings / has errors; the list jumps to the node ("Focus"). */
function ValidationBadge({ issues, open, onToggle, onFocus }: { issues: Issue[]; open: boolean; onToggle: () => void; onFocus: (id: string) => void }) {
  const errors = issues.filter(i => i.level === "error"), warnings = issues.length - errors.length;
  const tone = errors.length ? "border-bad/50 bg-bad/10 text-bad" : warnings ? "border-warn/50 bg-warn/10 text-warn" : "border-good/40 bg-good/10 text-good";
  return (
    <div className="relative">
      <button id="flow-validation" onClick={onToggle} aria-expanded={open} title="Flow validation"
        className={cx("flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium", tone)}>
        {errors.length ? `✕ ${errors.length} error${errors.length > 1 ? "s" : ""}` : warnings ? `⚠ ${warnings} warning${warnings > 1 ? "s" : ""}` : "✓ Valid"}
        {errors.length > 0 && warnings > 0 && <span>· {warnings} ⚠</span>}</button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 max-h-80 w-80 overflow-y-auto rounded-xl border border-line bg-panel p-2 shadow-xl">
          <div className="px-1 pb-1 text-sm font-semibold">Validation</div>
          {issues.length === 0 && <div className="px-1 py-3 text-xs text-muted">No problems found in this flow.</div>}
          {[...errors, ...issues.filter(i => i.level === "warning")].map((i, k) => (
            <div key={k} className="flex items-start gap-2 rounded-lg px-1.5 py-1.5 text-xs hover:bg-soft">
              <span className={i.level === "error" ? "text-bad" : "text-warn"}>{i.level === "error" ? "✕" : "⚠"}</span>
              <span className="min-w-0 flex-1">{i.node && <span className="mr-1 font-mono font-semibold">{i.node}</span>}{i.msg}</span>
              {i.node && <button className="shrink-0 font-medium text-accent-text hover:underline" onClick={() => onFocus(i.node!)}>Focus</button>}
            </div>))}
        </div>
      )}
    </div>
  );
}

function EdgeInspector({ edge, order, fromTool, targets, onChange, onDelete }: {
  edge: FlowEdge; order: number; fromTool: boolean; targets: string[]; onChange: (e: FlowEdge) => void; onDelete: () => void;
}) {
  const w = edge.when ?? {};
  const keys = Object.keys(w);
  const initialMode = edge.on ? "result" : !keys.length ? "always" : keys.length === 1 && MODES.includes(keys[0] as never) ? keys[0]
    : keys.length === 1 && CMP_KEYS.includes(keys[0]) ? "compare" : "json";
  const [mode, setMode] = useState<string>(initialMode);
  const keep = { ...(edge.back ? { back: true } : {}), ...(edge.confirm ? { confirm: edge.confirm } : {}), ...(edge.silent ? { silent: true } : {}) };
  const set = (when?: Record<string, unknown>, on?: string) => onChange({ from: edge.from, to: edge.to, ...(when ? { when } : {}), ...(on ? { on } : {}), ...keep });
  const list = (v: unknown) => (Array.isArray(v) ? v.join(", ") : "");
  return (
    <div className="space-y-3">
      <div className="text-xs">Transition {order} of <span className="font-mono">{edge.from === "*" ? "Anywhere" : edge.from}</span> → {" "}
        <select id="edge-target" className="text-xs" value={edge.to} onChange={e => onChange({ ...edge, to: e.target.value })}>
          {targets.map(t => <option key={t}>{t}</option>)}</select></div>
      {lbl("Follow this transition when", <select id="edge-mode" className="w-full" value={mode} onChange={e => { setMode(e.target.value); if (e.target.value === "always") set(); else if (e.target.value === "dtmf" && !w.dtmf) set({ dtmf: "1" }); else if (e.target.value === "compare" && !CMP_KEYS.some(k => k in w)) set({ ne: { "": "" } }); }}>
        <option value="always">always (keep it last)</option>
        {fromTool && <option value="result">the tool succeeded / failed</option>}
        <option value="llm">the caller's words mean …</option>
        <option value="replied">the caller has replied (to this step)</option>
        <option value="filled">these values are known</option>
        <option value="empty">these values are missing</option>
        <option value="equals">a value equals …</option>
        <option value="compare">a value is not / greater / less / contains / matches …</option>
        <option value="stage">caller-verification stage is …</option>
        <option value="dtmf">the caller presses a key …</option>
        <option value="json">advanced (JSON)</option>
      </select>, "A node's transitions are tried top to bottom, then the ones from Anywhere; the first that holds wins.")}
      {mode === "replied" && w.replied !== true && <Button onClick={() => set({ replied: true })}>Use this condition</Button>}
      {mode === "replied" && w.replied === true && <p className="text-xs text-muted">Moves on once the caller has said something
        since the flow reached this step (Hamsa's “after user reply”).</p>}
      {mode === "result" && <select id="edge-on" className="w-full" value={edge.on ?? "success"} onChange={e => set(edge.when, e.target.value)}>
        <option value="success">On success</option><option value="failure">On failure</option></select>}
      {mode === "llm" && lbl("Question", <input id="edge-llm" className="w-full" value={String(w.llm ?? "")} onChange={e => set({ llm: e.target.value })} />,
        "Short and plain, e.g. “the caller wants a different hospital”. Answered yes / no from the caller's latest words.")}
      {(mode === "filled" || mode === "empty") && lbl("Values (comma-separated)", <input id="edge-list" className="w-full font-mono" value={list(w[mode])}
        onChange={e => set({ [mode]: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} />)}
      {mode === "equals" && lbl("Value = (JSON)", <JsonField id="edge-eq" value={w.equals} onChange={v => set({ equals: v as Record<string, unknown> })} rows={2} />, '{"intent": "book"} or {"verified": true}')}
      {mode === "compare" && <CompareFields when={w} onChange={v => set(v)} />}
      {mode === "dtmf" && <div>
        <div className="grid grid-cols-3 gap-1.5" role="group" aria-label="Keypad">{KEYPAD.map(k => (
          <button key={k} type="button" id={`edge-key-${k === "*" ? "star" : k === "#" ? "hash" : k}`} aria-pressed={w.dtmf === k} onClick={() => set({ dtmf: k })}
            className={cx("rounded-lg border py-2 text-sm font-semibold", w.dtmf === k ? "border-accent bg-accent/10" : "border-line hover:bg-soft")}>{k}</button>))}</div>
        <p className="mt-1 text-[11px] text-muted">Phone callers press a key to take this transition (a menu choice). From “Anywhere” it works at every step — e.g. 0 for an operator. Digits can't be transitions on a step that collects digits; use * or #.</p>
      </div>}
      {mode === "stage" && <select id="edge-stage" className="w-full" value={String(w.stage ?? "")} onChange={e => set({ stage: e.target.value })}>
        {["awaiting_mobile", "awaiting_dob_and_name", "send_otp", "awaiting_otp", "verified"].map(s => <option key={s}>{s}</option>)}</select>}
      {mode === "json" && lbl("Condition (JSON)", <JsonField id="edge-json" value={edge.when} onChange={v => set(v as Record<string, unknown>, edge.on)} rows={6} />,
        "all / any / filled / empty / not_all_filled / equals / ne / gt / gte / lt / lte / contains / not_contains / regex / stage / llm / replied — can be combined with On success / failure")}
      <EdgeOptions edge={edge} onChange={onChange} />
      <Button kind="danger" onClick={onDelete}>Delete transition</Button>
    </div>
  );
}

function Variables({ vars, infer, system, custom, onChange }: {
  vars: NonNullable<FlowGraph["variables"]>; infer?: Record<string, unknown>; system: SysVar[];
  custom: Record<string, { type?: string; default?: unknown; description?: string }>;
  onChange: (v: NonNullable<FlowGraph["variables"]>) => void;
}) {
  const [name, setName] = useState("");
  const [q, setQ] = useState("");
  const fromTools = [...new Set(Object.values(infer ?? {}).flatMap(r => Object.keys(((r as { set?: object }).set) ?? {})))].sort();
  const match = (s: string) => !q || s.toLowerCase().includes(q.toLowerCase());
  const reserved = system.map(v => v.name);
  const problem = name ? (nameProblem(name, [...reserved, ...Object.keys(custom)]) ?? (vars[name] ? "already exists" : null)) : null;
  const groups = [...new Set(system.map(v => v.group))];
  return (
    <div className="space-y-3">
      <input id="var-search" className="w-full" placeholder="Search variables…" value={q} onChange={e => setQ(e.target.value)} />
      <details className="rounded-lg border border-line p-2" open>
        <summary className="cursor-pointer text-xs font-semibold">System variables <span className="font-normal text-muted">· {system.length}, always available</span></summary>
        <div className="mt-1.5 space-y-2">
          {groups.map(g => {
            const rows = system.filter(v => v.group === g && (match(v.name) || match(v.description)));
            return rows.length ? (
              <div key={g}><div className="text-[10px] font-semibold uppercase tracking-wide text-muted">{g}</div>
                {rows.map(v => <div key={v.name} className="flex items-baseline justify-between gap-2 py-0.5 text-[11px]"><span className="font-mono">{v.name}</span>
                  <span className="text-right text-muted">{v.description}</span></div>)}</div>) : null;
          })}
        </div>
      </details>
      <div>
        <div className="mb-1 text-xs font-semibold">Custom variables <span className="font-normal text-muted">· {Object.keys(custom).length} · set in Global Settings → Variables, or passed as params when a call starts</span></div>
        {Object.entries(custom).filter(([k]) => match(k)).map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-2 py-0.5 text-[11px]"><span className="font-mono">{k}</span>
            <span className="text-right text-muted">{v.type ?? "string"}{v.description ? ` · ${v.description}` : ""}</span></div>))}
        {!Object.keys(custom).length && <div className="text-[11px] text-muted">None yet.</div>}
      </div>
      <div>
        <div className="mb-1 text-xs font-semibold">Call facts <span className="font-normal text-muted">· provided by the platform</span></div>
        {CALL_FACTS.filter(([k]) => match(k)).map(([k, d]) => (
          <div key={k} className="flex items-baseline justify-between gap-2 py-0.5 text-[11px]"><span className="font-mono">{k}</span><span className="text-right text-muted">{d}</span></div>))}
      </div>
      {fromTools.length > 0 && <div>
        <div className="mb-1 text-xs font-semibold">Set by tools <span className="font-normal text-muted">· from tool results</span></div>
        <div className="flex flex-wrap gap-1">{fromTools.filter(match).map(k => <span key={k} className="rounded bg-violet-100 px-1.5 font-mono text-[10px] text-violet-800">{k}</span>)}</div>
      </div>}
      <div>
        <div className="mb-1 text-xs font-semibold">Collected from the caller <span className="font-normal text-muted">· {Object.keys(vars).length}</span></div>
        <div className="space-y-2">
          {Object.entries(vars).filter(([k]) => match(k)).map(([k, v]) => (
            <div key={k} className="space-y-1.5 rounded-lg border border-line p-2">
              <div className="flex items-center justify-between"><span className="font-mono text-xs font-medium">{k}</span>
                <Button kind="ghost" onClick={() => { const { [k]: _gone, ...rest } = vars; void _gone; onChange(rest); }}>Remove</Button></div>
              {nameProblem(k, reserved) && <div className="text-[11px] text-bad">The name {nameProblem(k, reserved)}.</div>}
              <select className="w-full text-xs" value={v.type ?? "string"} onChange={e => onChange({ ...vars, [k]: { ...v, type: e.target.value } })}>
                {["string", "integer", "number", "boolean", "date", "array", "object"].map(t => <option key={t}>{t}</option>)}</select>
              <input className="w-full text-xs" placeholder="allowed values, comma-separated (optional)" value={(v.enum ?? []).join(", ")}
                onChange={e => onChange({ ...vars, [k]: { ...v, enum: e.target.value.split(",").map(s => s.trim()).filter(Boolean) } })} />
              <textarea className="w-full text-xs" rows={2} placeholder="what it is (the model reads this)" value={v.description ?? ""}
                onChange={e => onChange({ ...vars, [k]: { ...v, description: e.target.value } })} />
            </div>
          ))}
        </div>
      </div>
      <form className="space-y-1" onSubmit={e => { e.preventDefault(); if (name && !problem) { onChange({ ...vars, [name]: { type: "string" } }); setName(""); } }}>
        <div className="flex gap-2">
          <input id="var-name" className="min-w-0 flex-1 font-mono text-xs" placeholder="new_variable" value={name} onChange={e => setName(e.target.value.replace(/[^\w]/g, ""))} />
          <Button type="submit" disabled={!name || !!problem}>+ Add variable</Button>
        </div>
        {problem && <div className="text-[11px] text-bad">The name {problem}.</div>}
      </form>
    </div>
  );
}
