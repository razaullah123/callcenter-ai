import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider, useNodesState, useReactFlow,
  type Connection, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { FlowEdge, FlowGraph, FlowNode } from "../api";
import { Badge, Button, cx } from "../ui";

// Flow canvas (Hamsa-style): a floating toolbar (+ add node · (x) variables · ⊞ auto-layout · ⚙ global settings),
// nodes drawn by type with their transitions as rows — each row has its own handle, in the order they are tried —
// a ⋯ menu per node (rename · view logs · duplicate · delete), and a tool picker for tool nodes.

const ANY = "__any__";            // the "Anywhere" node: edges from "*" (reachable from any step)
const DONE = "__done__";

export const TYPES: Record<string, { label: string; icon: string; color: string; hint: string }> = {
  conversation: { label: "Conversation", icon: "💬", color: "bg-sky-600", hint: "The agent talks: instructions, tools, values to collect" },
  tool: { label: "Tool", icon: "🛠", color: "bg-violet-600", hint: "The platform calls a tool, then follows success or failure" },
  router: { label: "Router", icon: "⑂", color: "bg-indigo-600", hint: "Logic split: picks the next step by conditions" },
  set: { label: "Set variables", icon: "(x)", color: "bg-emerald-600", hint: "Sets values for later steps, then moves on" },
  transfer: { label: "Transfer call", icon: "☎", color: "bg-orange-600", hint: "Hands the call to a person" },
  skill: { label: "Go to skill", icon: "↪", color: "bg-teal-600", hint: "Continues in another skill of this agent" },
  end: { label: "End call", icon: "⏹", color: "bg-rose-600", hint: "Optional closing line, then hang up" },
};

/** Values the platform knows about the call, usable in edge conditions next to the collected variables. */
const CALL_FACTS: [string, string][] = [
  ["verified", "the caller passed verification"], ["identity_confirmed", "they said yes to “Am I speaking to …?”"],
  ["mobile_heard", "a mobile number was heard this turn"], ["code_heard", "a code was heard this turn"],
  ["files_found", "patient files found for the number"], ["otp_exhausted", "3 wrong codes"],
  ["awaiting_confirmation", "a booking / change waits for the caller's yes"],
];

export function conditionLabel(e: FlowEdge): string {
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
    if (k === "llm") return `“${String(v)}”`;
    if (k === "replied") return v ? "after the caller replies" : "before the caller replies";
    return `${k}: ${JSON.stringify(v)}`;
  }).join(" and ");
}

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

type RowInfo = { gi: number; label: string };
type NodeData = { node: FlowNode; start: boolean; active: boolean; rows: RowInfo[]; selectedEdge: number | null };

type CanvasActions = {
  open: (id: string) => void; menu: (id: string, action: "rename" | "logs" | "duplicate" | "delete") => void;
  pickTool: (id: string) => void; selectEdge: (gi: number) => void; moveEdge: (gi: number, dir: -1 | 1) => void;
};
const Actions = createContext<CanvasActions | null>(null);

function FlowNodeView({ id, data, selected }: NodeProps<Node<NodeData>>) {
  const act = useContext(Actions)!;
  const [menu, setMenu] = useState(false);
  const n = data.node;
  const anywhere = id === ANY;
  const t = anywhere ? { label: "Anywhere", icon: "✳", color: "bg-slate-500", hint: "" } : TYPES[n.type] ?? TYPES.conversation;
  const body = (() => {
    if (anywhere) return <div className="text-[11px] text-muted">Edges here are checked from every step.</div>;
    if (n.id === DONE) return <div className="text-[11px] text-muted">Quiet end of the old step flow.</div>;
    switch (n.type) {
      case "tool": return (
        <button className="w-full rounded-md border border-line bg-soft/60 px-2 py-1 text-left" onClick={e => { e.stopPropagation(); act.pickTool(id); }}>
          <div className="text-[10px] text-muted">● Tool · click to change</div>
          <div className="truncate text-[11px] font-semibold">{n.tool || "choose a tool…"}</div>
          {n.args && Object.keys(n.args).length > 0 && <div className="truncate font-mono text-[10px] text-muted">{JSON.stringify(n.args)}</div>}
        </button>);
      case "router": return <div className="text-[11px] text-muted">Logic splitting node with conditional routing</div>;
      case "set": return <div className="flex flex-wrap gap-1"><span className="text-[10px] text-muted">{Object.keys(n.set ?? {}).length} variables</span>
        {Object.keys(n.set ?? {}).slice(0, 4).map(k => <span key={k} className="rounded bg-emerald-100 px-1 text-[10px] text-emerald-800">{k}</span>)}</div>;
      case "transfer": return <div className="line-clamp-2 text-[11px] text-muted">{n.reason || "Transfer to a person"}</div>;
      case "end": return <div className="line-clamp-2 text-[11px] text-muted" dir="auto">{n.say?.en || n.say?.ar || "Silent end"}</div>;
      case "skill": return <div className="text-[11px] text-muted">→ {n.skill || "choose a skill…"}</div>;
      default: return (
        <>
          <div className="line-clamp-3 rounded-md bg-soft/60 px-2 py-1 text-[11px] text-muted" dir="auto">{n.instructions || "No instructions yet"}</div>
          {(n.extract?.length ?? 0) > 0 && <div className="mt-1 flex flex-wrap items-center gap-1"><span className="text-[10px] text-muted">Collecting:</span>
            {n.extract!.map(v => <span key={v} className="rounded bg-red-50 px-1 text-[10px] text-red-800">{v}</span>)}</div>}
          {(n.tools?.length ?? 0) > 0 && <div className="mt-1 text-[10px] text-muted">🛠 {n.tools!.length} tools</div>}
        </>);
    }
  })();
  const auto = !anywhere && ["set", "router"].includes(n.type) === false && data.rows.length === 0 && n.type !== "end" && n.type !== "transfer";
  return (
    <div className={cx("w-64 rounded-xl border bg-panel text-left shadow-sm",
      selected ? "border-accent ring-2 ring-accent/30" : "border-line",
      data.active && "!border-brand shadow-[0_0_0_4px_rgba(230,58,64,0.3)]")}>
      {!anywhere && <Handle type="target" position={Position.Left} className="!h-3 !w-3 !bg-slate-400" />}
      <div className="flex items-center gap-2 border-b border-line px-2 py-1.5">
        <span className={cx("flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[11px] text-white", t.color)}>{t.icon}</span>
        <button className="min-w-0 flex-1 truncate text-left text-xs font-semibold" onClick={e => { e.stopPropagation(); act.open(id); }}>
          {n.id === DONE ? "Done" : anywhere ? "Anywhere" : n.id}</button>
        {data.start && <span className="rounded bg-accent/15 px-1 text-[9px] font-semibold text-accent-text">START</span>}
        {!anywhere && <button title="Settings" className="text-muted hover:text-ink" onClick={e => { e.stopPropagation(); act.open(id); }}>⚙</button>}
        {!anywhere && <div className="relative">
          <button title="More" className="px-1 text-muted hover:text-ink" onClick={e => { e.stopPropagation(); setMenu(m => !m); }}>⋯</button>
          {menu && <div className="absolute right-0 z-30 mt-1 w-36 rounded-lg border border-line bg-panel p-1 text-xs shadow-lg" onMouseLeave={() => setMenu(false)}>
            {([["rename", "✎ Rename"], ["logs", "📜 View logs"], ["duplicate", "⧉ Duplicate"], ["delete", "🗑 Delete"]] as const).map(([a, l]) => (
              <button key={a} className={cx("block w-full rounded px-2 py-1 text-left hover:bg-soft", a === "delete" && "text-bad")}
                onClick={e => { e.stopPropagation(); setMenu(false); act.menu(id, a); }}>{l}</button>))}
          </div>}
        </div>}
      </div>
      {data.active && <div className="bg-red-50 px-2 py-0.5 text-[10px] font-semibold text-red-800">● active in the test call</div>}
      <div className="space-y-1 px-2 py-1.5">{body}</div>
      {!(n.type === "end" || n.type === "transfer") && (
        <div className="border-t border-line px-2 py-1.5">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted">Transitions</div>
          <div className="space-y-1">
            {data.rows.map((r, i) => (
              <div key={r.gi} className={cx("group relative flex items-center gap-1 rounded-md border px-1.5 py-1",
                data.selectedEdge === r.gi ? "border-accent bg-accent/5" : "border-line bg-soft/40")}
                onClick={e => { e.stopPropagation(); act.selectEdge(r.gi); }}>
                <span className="text-[10px] text-muted">{i + 1}.</span>
                <span className="min-w-0 flex-1 truncate text-[11px]" dir="auto" title={r.label}>{r.label}</span>
                <span className="hidden gap-0.5 group-hover:flex">
                  <button className="text-[10px] text-muted hover:text-ink" title="Try earlier" onClick={e => { e.stopPropagation(); act.moveEdge(r.gi, -1); }}>▲</button>
                  <button className="text-[10px] text-muted hover:text-ink" title="Try later" onClick={e => { e.stopPropagation(); act.moveEdge(r.gi, 1); }}>▼</button>
                </span>
                <Handle id={`h-${r.gi}`} type="source" position={Position.Right} className="!-right-[13px] !h-2.5 !w-2.5 !bg-accent" />
              </div>
            ))}
            {auto && <div className="text-[10px] text-muted">No transitions — the call stays here.</div>}
            <div className="relative rounded-md border border-dashed border-line px-1.5 py-1 text-[10px] text-muted">
              + drag from ● to add a transition
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
};

export default function FlowCanvas(props: CanvasProps) {
  return <ReactFlowProvider><Canvas {...props} /></ReactFlowProvider>;
}

type Panel = "inspector" | "variables" | "global";

function Canvas({ graph, converted, tools, skills, onSave, saving, aside, globalPanel, toolInfo, activeNode, follow, locate,
  onDirty, onViewLogs, onInspect }: CanvasProps) {
  const [g, setG] = useState(() => ({ nodes: graph.nodes, edges: graph.edges, start: graph.start, variables: graph.variables ?? {} }));
  const [sel, setSel] = useState<{ kind: "node"; id: string } | { kind: "edge"; gi: number } | null>(null);
  const [panel, setPanel] = useState<Panel>("inspector");
  const [dirty, setDirty] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [layoutOpen, setLayoutOpen] = useState(false);
  const [picker, setPicker] = useState<string | null>(null);
  const renameRef = useRef<HTMLInputElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [focusNew, setFocusNew] = useState<string | null>(null);
  const rf = useReactFlow();
  const touch = () => setDirty(true);
  useEffect(() => { onDirty?.(dirty); }, [dirty, onDirty]);

  // React Flow nodes: positions live here while editing; everything else comes from `g`
  const build = useCallback((prev: Node<NodeData>[]) => {
    const auto = layout(g.nodes, g.edges, g.start, "horizontal");
    const rows = (src: string) => g.edges.map((e, gi) => ({ e, gi })).filter(x => x.e.from === src).map(x => ({ gi: x.gi, label: conditionLabel(x.e) }));
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
  };

  const node = sel?.kind === "node" ? g.nodes.find(n => n.id === sel.id) : undefined;
  const edge = sel?.kind === "edge" ? g.edges[sel.gi] : undefined;
  const side = aside ?? (panel === "global" && globalPanel ? globalPanel : (
    <div className="h-full overflow-y-auto p-3">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold">{panel === "variables" ? "Variables" : node ? "Node inspector" : edge ? "Transition" : "Inspector"}</div>
        {(panel !== "inspector" || sel) && <button className="text-muted hover:text-ink" onClick={() => { setSel(null); setPanel("inspector"); }}>✕</button>}
      </div>
      {panel === "variables" ? <Variables vars={g.variables} infer={graph.infer} onChange={v => { setG(x => ({ ...x, variables: v })); touch(); }} />
        : node ? <NodeInspector key={node.id} node={node} isStart={node.id === g.start} tools={tools} skills={skills}
            others={g.nodes.map(n => n.id).filter(id => id !== node.id && id !== DONE)}
            onConnectFrom={src => { setG(x => ({ ...x, edges: [...x.edges, { from: src, to: node.id }] })); inspect({ kind: "edge", gi: g.edges.length }); touch(); }}
            onConnectTo={dst => { setG(x => ({ ...x, edges: [...x.edges, { from: node.id, to: dst }] })); inspect({ kind: "edge", gi: g.edges.length }); touch(); }}
            variables={Object.keys(g.variables)} renameRef={renameRef} toolInfo={toolInfo}
            globalEdges={g.edges.map((e, gi) => ({ e, gi })).filter(x => x.e.from === "*" && x.e.to === node.id)}
            onChange={p => patchNode(node.id, p)} onRename={next => renameNode(node.id, next)} onPickTool={() => setPicker(node.id)}
            onStart={() => { setG(x => ({ ...x, start: node.id })); touch(); }} onDelete={() => deleteNode(node.id)}
            onAddGlobal={() => { setG(x => ({ ...x, edges: [...x.edges, { from: "*", to: node.id, when: { llm: "" } }] })); setSel({ kind: "edge", gi: g.edges.length }); touch(); }}
            onOpenEdge={gi => setSel({ kind: "edge", gi })} />
        : edge && sel?.kind === "edge" ? <EdgeInspector key={sel.gi} edge={edge} order={g.edges.filter(e => e.from === edge.from).indexOf(edge) + 1}
            fromTool={g.nodes.find(n => n.id === edge.from)?.type === "tool"} targets={g.nodes.map(n => n.id)}
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
      <div className={cx("grid gap-3", aside ? "lg:grid-cols-[1fr_26rem]" : "lg:grid-cols-[1fr_24rem]")}>
        <div ref={boxRef} className="relative h-[70vh] overflow-hidden rounded-xl border border-line bg-panel">
          <Actions.Provider value={actions}>
            <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} fitView minZoom={0.1}
              onNodesChange={c => { onNodesChange(c); if (c.some(x => x.type === "position" && !x.dragging)) touch(); }}
              onConnect={onConnect} onEdgeClick={(_, e) => actions.selectEdge(Number(e.id.slice(1)))}
              onNodeClick={(_, n) => { if (n.id !== ANY) actions.open(n.id); }}
              onPaneClick={() => { setSel(null); setAddOpen(false); setLayoutOpen(false); }} deleteKeyCode={null}>
              <Background gap={20} /><MiniMap pannable zoomable /><Controls />
            </ReactFlow>
          </Actions.Provider>
          {/* floating toolbar, like Hamsa: add node · variables · auto-layout · global settings */}
          <div className="absolute left-3 top-1/2 z-10 flex -translate-y-1/2 flex-col gap-2">
            <ToolButton label="Add node" active={addOpen} accent onClick={() => { setAddOpen(o => !o); setLayoutOpen(false); }}>＋</ToolButton>
            <ToolButton label="Variables" active={panel === "variables" && !aside} onClick={() => { setPanel(p => (p === "variables" ? "inspector" : "variables")); setSel(null); }}>(x)</ToolButton>
            <ToolButton label="Auto layout" active={layoutOpen} onClick={() => { setLayoutOpen(o => !o); setAddOpen(false); }}>⊞</ToolButton>
            <ToolButton label="Global settings" active={panel === "global" && !aside} onClick={() => setPanel(p => (p === "global" ? "inspector" : "global"))}>⚙</ToolButton>
          </div>
          {addOpen && (
            <div className="absolute left-16 top-1/2 z-20 w-72 -translate-y-1/2 rounded-xl border border-line bg-panel p-2 shadow-xl">
              <div className="px-2 pb-1 text-sm font-semibold">Add node</div>
              <div className="px-2 pb-2 text-[11px] text-muted">Choose a node type to add to your flow</div>
              {Object.entries(TYPES).map(([type, t]) => (
                <button key={type} className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-soft" onClick={() => addNode(type)}>
                  <span className={cx("mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[11px] text-white", t.color)}>{t.icon}</span>
                  <span><span className="block text-sm font-medium">{t.label}</span><span className="block text-[11px] text-muted">{t.hint}</span></span>
                </button>))}
            </div>
          )}
          {layoutOpen && (
            <div className="absolute left-16 top-1/2 z-20 w-40 rounded-xl border border-line bg-panel p-1 shadow-xl">
              <button className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-soft" onClick={() => autoLayout("vertical")}>↧ Vertical</button>
              <button className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-soft" onClick={() => autoLayout("horizontal")}>↦ Horizontal</button>
            </div>
          )}
          <div className="absolute right-3 top-3 z-10 flex items-center gap-2">
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
    className={cx("flex h-11 w-11 items-center justify-center rounded-xl border text-base shadow-sm transition",
      accent ? "border-transparent bg-brand text-white hover:opacity-90" : active ? "border-accent bg-accent/10" : "border-line bg-panel hover:bg-soft")}>{children}</button>;
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

function NodeInspector({ node, isStart, tools, skills, variables, renameRef, toolInfo, globalEdges, onChange, onRename, onPickTool, onStart,
  onDelete, onAddGlobal, onOpenEdge, others, onConnectFrom, onConnectTo }: {
  node: FlowNode; isStart: boolean; tools: string[]; skills: string[]; variables: string[];
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
        <span className={cx("flex h-7 w-7 items-center justify-center rounded-md text-xs text-white", t.color)}>{t.icon}</span>
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
        {lbl("Prompt (what the agent does at this step)", <textarea id="node-instr" rows={9} className={cx("w-full text-xs", !node.instructions && "border-warn")}
          dir="auto" value={node.instructions ?? ""} placeholder={'e.g. Ask exactly: "Would you like the earliest available appointment, or a specific date?"'}
          onChange={e => onChange({ instructions: e.target.value })} />,
          node.instructions ? "Rules for the whole call belong in Global settings (⚙)." : "A prompt is required — what should the agent say or do here?")}
        <div><div className="mb-1 text-xs font-medium">Variables to collect from the caller's words</div>
          {variables.map(v => (
            <label key={v} className="flex items-center gap-1.5 text-[11px]"><input type="checkbox" checked={(node.extract ?? []).includes(v)}
              onChange={() => onChange({ extract: toggle(node.extract, v) })} /><span className="font-mono">{v}</span></label>))}
          {variables.length === 0 && <div className="text-[11px] text-muted">Declare them with the (x) button.</div>}</div>
        <div><div className="mb-1 text-xs font-medium">Tools the model may call here</div>
          <div className="max-h-40 space-y-0.5 overflow-y-auto">{tools.map(tl => (
            <label key={tl} className="flex items-center gap-1.5 text-[11px]"><input type="checkbox" checked={(node.tools ?? []).includes(tl)}
              onChange={() => onChange({ tools: toggle(node.tools, tl) })} /><span className="font-mono">{tl}</span></label>))}</div></div>
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
      </>}
      {node.type === "set" && lbl("Values (JSON) · \"=text\" literal, \"slots.x\" a value, \"{{ … }}\" a template", <JsonField id="node-set" rows={5} value={node.set} onChange={v => onChange({ set: v as FlowNode["set"] })} />,
        '{"tries": "=1", "old_value": null}  — null forgets a value')}
      {node.type === "transfer" && lbl("Reason (logged)", <input id="node-reason" className="w-full" value={node.reason ?? ""} onChange={e => onChange({ reason: e.target.value })} />)}
      {node.type === "end" && <>
        {lbl("Final message (Arabic)", <input id="node-say-ar" dir="rtl" className="w-full" value={node.say?.ar ?? ""} onChange={e => onChange({ say: { ...node.say, ar: e.target.value } })} />)}
        {lbl("Final message (English)", <input id="node-say-en" className="w-full" value={node.say?.en ?? ""} onChange={e => onChange({ say: { ...node.say, en: e.target.value } })} />, "Leave both empty for a silent end.")}
      </>}
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

const MODES = ["always", "llm", "replied", "filled", "empty", "equals", "stage", "json"] as const;

function EdgeInspector({ edge, order, fromTool, targets, onChange, onDelete }: {
  edge: FlowEdge; order: number; fromTool: boolean; targets: string[]; onChange: (e: FlowEdge) => void; onDelete: () => void;
}) {
  const w = edge.when ?? {};
  const keys = Object.keys(w);
  const initialMode = edge.on ? "result" : !keys.length ? "always" : keys.length === 1 && MODES.includes(keys[0] as never) ? keys[0] : "json";
  const [mode, setMode] = useState<string>(initialMode);
  const set = (when?: Record<string, unknown>, on?: string) => onChange({ from: edge.from, to: edge.to, ...(when ? { when } : {}), ...(on ? { on } : {}) });
  const list = (v: unknown) => (Array.isArray(v) ? v.join(", ") : "");
  return (
    <div className="space-y-3">
      <div className="text-xs">Transition {order} of <span className="font-mono">{edge.from === "*" ? "Anywhere" : edge.from}</span> → {" "}
        <select id="edge-target" className="text-xs" value={edge.to} onChange={e => onChange({ ...edge, to: e.target.value })}>
          {targets.map(t => <option key={t}>{t}</option>)}</select></div>
      {lbl("Follow this transition when", <select id="edge-mode" className="w-full" value={mode} onChange={e => { setMode(e.target.value); if (e.target.value === "always") set(); }}>
        <option value="always">always (keep it last)</option>
        {fromTool && <option value="result">the tool succeeded / failed</option>}
        <option value="llm">the caller's words mean …</option>
        <option value="replied">the caller has replied (to this step)</option>
        <option value="filled">these values are known</option>
        <option value="empty">these values are missing</option>
        <option value="equals">a value equals …</option>
        <option value="stage">caller-verification stage is …</option>
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
      {mode === "stage" && <select id="edge-stage" className="w-full" value={String(w.stage ?? "")} onChange={e => set({ stage: e.target.value })}>
        {["awaiting_mobile", "awaiting_dob_and_name", "send_otp", "awaiting_otp", "verified"].map(s => <option key={s}>{s}</option>)}</select>}
      {mode === "json" && lbl("Condition (JSON)", <JsonField id="edge-json" value={edge.when} onChange={v => set(v as Record<string, unknown>, edge.on)} rows={6} />,
        "all / any / filled / empty / not_all_filled / equals / stage / llm / replied — can be combined with On success / failure")}
      <Button kind="danger" onClick={onDelete}>Delete transition</Button>
    </div>
  );
}

function Variables({ vars, infer, onChange }: {
  vars: NonNullable<FlowGraph["variables"]>; infer?: Record<string, unknown>; onChange: (v: NonNullable<FlowGraph["variables"]>) => void;
}) {
  const [name, setName] = useState("");
  const [q, setQ] = useState("");
  const fromTools = [...new Set(Object.values(infer ?? {}).flatMap(r => Object.keys(((r as { set?: object }).set) ?? {})))].sort();
  const match = (s: string) => !q || s.toLowerCase().includes(q.toLowerCase());
  return (
    <div className="space-y-3">
      <input id="var-search" className="w-full" placeholder="Search variables…" value={q} onChange={e => setQ(e.target.value)} />
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
              <select className="w-full text-xs" value={v.type ?? "string"} onChange={e => onChange({ ...vars, [k]: { ...v, type: e.target.value } })}>
                {["string", "integer", "number", "boolean", "date"].map(t => <option key={t}>{t}</option>)}</select>
              <input className="w-full text-xs" placeholder="allowed values, comma-separated (optional)" value={(v.enum ?? []).join(", ")}
                onChange={e => onChange({ ...vars, [k]: { ...v, enum: e.target.value.split(",").map(s => s.trim()).filter(Boolean) } })} />
              <textarea className="w-full text-xs" rows={2} placeholder="what it is (the model reads this)" value={v.description ?? ""}
                onChange={e => onChange({ ...vars, [k]: { ...v, description: e.target.value } })} />
            </div>
          ))}
        </div>
      </div>
      <form className="flex gap-2" onSubmit={e => { e.preventDefault(); if (name && !vars[name]) { onChange({ ...vars, [name]: { type: "string" } }); setName(""); } }}>
        <input id="var-name" className="min-w-0 flex-1 font-mono text-xs" placeholder="new_variable" value={name} onChange={e => setName(e.target.value.replace(/[^\w]/g, ""))} />
        <Button type="submit" disabled={!name}>+ Add variable</Button>
      </form>
    </div>
  );
}
