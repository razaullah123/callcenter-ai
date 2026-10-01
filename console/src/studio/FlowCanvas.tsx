import {
  addEdge, Background, Controls, Handle, MiniMap, Position, ReactFlow, useEdgesState, useNodesState,
  type Connection, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useMemo, useState } from "react";
import type { FlowEdge, FlowGraph, FlowNode } from "../api";
import { Badge, Button, cx } from "../ui";

// Flow canvas: the skill's graph as nodes and edges. Node positions, nodes, edges and variables are edited here and
// saved as the skill's flow (a new skill version pinned by the agent's draft).

const ANY = "__any__";            // edges from "*" (anywhere) hang off this virtual node
const DONE = "__done__";
const TYPES: Record<string, { label: string; color: string; hint: string }> = {
  conversation: { label: "Conversation", color: "border-accent", hint: "The agent talks: instructions, tools, values to collect" },
  router: { label: "Router", color: "border-violet-500", hint: "Only picks the next edge" },
  set: { label: "Set", color: "border-slate-400", hint: "Sets values" },
  tool: { label: "Tool", color: "border-amber-500", hint: "The platform calls a tool, then follows success / failure" },
  transfer: { label: "Transfer", color: "border-rose-500", hint: "Hands the call to a person" },
  end: { label: "End call", color: "border-rose-700", hint: "Optional closing line, then hang up" },
  skill: { label: "Go to skill", color: "border-emerald-600", hint: "Continues in another skill" },
};

type NodeData = { node: FlowNode; start: boolean };

function FlowNodeView({ data, selected }: NodeProps<Node<NodeData>>) {
  const n = data.node;
  if (n.id === ANY) {
    return <div className="rounded-full border-2 border-dashed border-line bg-panel px-4 py-2 text-xs text-muted">
      Anywhere<Handle type="source" position={Position.Right} /></div>;
  }
  const t = TYPES[n.type] ?? TYPES.conversation;
  const detail = n.type === "tool" ? n.tool : n.type === "transfer" ? n.reason : n.type === "end" ? (n.say?.en || n.say?.ar)
    : n.type === "skill" ? `→ ${n.skill}` : n.type === "set" ? Object.keys(n.set ?? {}).join(", ") : n.instructions;
  return (
    <div className={cx("w-56 rounded-lg border-2 bg-panel px-3 py-2 text-left shadow-sm", t.color, selected && "ring-2 ring-accent/40")}>
      <Handle type="target" position={Position.Left} />
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-semibold">{n.id === DONE ? "Done" : n.id}</span>
        <span className="text-[10px] uppercase tracking-wide text-muted">{t.label}</span>
      </div>
      {data.start && <div className="text-[10px] font-medium text-accent">start</div>}
      {detail && <div className="mt-1 line-clamp-3 text-[11px] text-muted">{detail}</div>}
      {(n.extract?.length ?? 0) > 0 && <div className="mt-1 text-[10px] text-muted">collects {n.extract!.join(", ")}</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { flow: FlowNodeView };

export function conditionLabel(e: FlowEdge): string {
  if (e.on) return e.on;
  return describe(e.when);
}
function describe(w: Record<string, unknown> | undefined): string {
  if (!w || !Object.keys(w).length) return "always";
  return Object.entries(w).map(([k, v]) => {
    if (k === "all" || k === "any") return (v as Record<string, unknown>[]).map(describe).join(k === "all" ? " and " : " or ");
    if (k === "filled") return `has ${(v as string[]).join(", ")}`;
    if (k === "empty") return `no ${(v as string[]).join(", ")}`;
    if (k === "not_all_filled") return `not done: ${(v as string[]).join(", ")}`;
    if (k === "equals") return Object.entries(v as object).map(([a, b]) => `${a} = ${b}`).join(", ");
    if (k === "stage") return `stage ${v}`;
    if (k === "llm") return `“${String(v).slice(0, 40)}”`;
    return `${k}: ${JSON.stringify(v)}`;
  }).join(" and ");
}

function layout(g: FlowGraph): Record<string, { x: number; y: number }> {
  // nodes without a saved position: layers from the start node along the edges, the rest in list order
  const pos: Record<string, { x: number; y: number }> = {};
  const layer: Record<string, number> = { [g.start]: 0 };
  const queue = [g.start];
  while (queue.length) {
    const id = queue.shift()!;
    for (const e of g.edges.filter(x => x.from === id)) {
      if (layer[e.to] === undefined) { layer[e.to] = layer[id] + 1; queue.push(e.to); }
    }
  }
  let next = Math.max(0, ...Object.values(layer)) + 1;
  const rows: Record<number, number> = {};
  for (const n of g.nodes) {
    const l = layer[n.id] ?? next++;
    rows[l] = (rows[l] ?? 0) + 1;
    pos[n.id] = n.position ?? { x: l * 290, y: 60 + (rows[l] - 1) * 150 };
  }
  return pos;
}

function toFlow(g: FlowGraph): { nodes: Node<NodeData>[]; edges: Edge[] } {
  const pos = layout(g);
  const nodes: Node<NodeData>[] = g.nodes.map(n => ({ id: n.id, type: "flow", position: pos[n.id], data: { node: n, start: n.id === g.start } }));
  if (g.edges.some(e => e.from === "*")) {
    nodes.unshift({ id: ANY, type: "flow", position: { x: -260, y: -60 }, data: { node: { id: ANY, type: "router" }, start: false }, deletable: false });
  }
  const edges: Edge[] = g.edges.map((e, i) => ({
    id: `e${i}`, source: e.from === "*" ? ANY : e.from, target: e.to, label: conditionLabel(e),
    data: { edge: e }, animated: e.from === "*", style: e.from === "*" ? { strokeDasharray: "4 3" } : undefined,
    labelStyle: { fontSize: 10 },
  }));
  return { nodes, edges };
}

function fromFlow(base: FlowGraph, nodes: Node<NodeData>[], edges: Edge[], vars: FlowGraph["variables"], start: string): FlowGraph {
  return {
    ...base, start,
    nodes: nodes.filter(n => n.id !== ANY).map(n => ({ ...n.data.node, id: n.id, position: { x: Math.round(n.position.x), y: Math.round(n.position.y) } })),
    edges: edges.map(e => ({ ...((e.data as { edge?: FlowEdge })?.edge ?? {}), from: e.source === ANY ? "*" : e.source, to: e.target })),
    variables: vars && Object.keys(vars).length ? vars : undefined,
  };
}

export default function FlowCanvas({ graph, converted, tools, skills, onSave, saving }: {
  graph: FlowGraph; converted: boolean; tools: string[]; skills: string[];
  onSave: (g: FlowGraph) => void; saving: boolean;
}) {
  const initial = useMemo(() => toFlow(graph), [graph]);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NodeData>>(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges);
  const [vars, setVars] = useState(graph.variables ?? {});
  const [start, setStart] = useState(graph.start);
  const [sel, setSel] = useState<{ kind: "node" | "edge"; id: string } | null>(null);
  const [panel, setPanel] = useState<"inspector" | "variables">("inspector");
  const [dirty, setDirty] = useState(false);

  const touch = () => setDirty(true);
  const onConnect = useCallback((c: Connection) => {
    setEdges(es => addEdge({ ...c, label: "always", data: { edge: { from: c.source, to: c.target } } }, es)); touch();
  }, [setEdges]);
  const updateNode = (id: string, patch: Partial<FlowNode>) => {
    setNodes(ns => ns.map(n => n.id === id ? { ...n, data: { ...n.data, node: { ...n.data.node, ...patch } } } : n)); touch();
  };
  const renameNode = (id: string, next: string) => {
    if (!next || nodes.some(n => n.id === next)) return;
    setNodes(ns => ns.map(n => n.id === id ? { ...n, id: next, data: { ...n.data, node: { ...n.data.node, id: next } } } : n));
    setEdges(es => es.map(e => ({ ...e, source: e.source === id ? next : e.source, target: e.target === id ? next : e.target })));
    if (start === id) setStart(next);
    setSel({ kind: "node", id: next }); touch();
  };
  const updateEdge = (id: string, edge: FlowEdge) => {
    setEdges(es => es.map(e => e.id === id ? { ...e, data: { edge }, label: conditionLabel(edge) } : e)); touch();
  };
  const addNode = (type: string) => {
    let i = 1; while (nodes.some(n => n.id === `${type}_${i}`)) i++;
    const id = `${type}_${i}`;
    setNodes(ns => [...ns, { id, type: "flow", position: { x: 120 + ns.length * 30, y: 420 }, data: { node: { id, type }, start: false } }]);
    setSel({ kind: "node", id }); setPanel("inspector"); touch();
  };
  const remove = () => {
    if (!sel) return;
    if (sel.kind === "node") {
      setNodes(ns => ns.filter(n => n.id !== sel.id));
      setEdges(es => es.filter(e => e.source !== sel.id && e.target !== sel.id));
    } else setEdges(es => es.filter(e => e.id !== sel.id));
    setSel(null); touch();
  };
  const current = sel?.kind === "node" ? nodes.find(n => n.id === sel.id) : undefined;
  const currentEdge = sel?.kind === "edge" ? edges.find(e => e.id === sel.id) : undefined;
  const shown = nodes.map(n => ({ ...n, data: { ...n.data, start: n.id === start } }));

  return (
    <div className="space-y-2">
      {converted && <div className="rounded-lg bg-soft px-3 py-2 text-xs text-muted">This flow was written as steps. The canvas shows it as a graph
        (each step is reached from “Anywhere” when its conditions hold); saving keeps the same behaviour.</div>}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">Add:</span>
        {Object.entries(TYPES).map(([t, v]) => <Button key={t} kind="ghost" title={v.hint} onClick={() => addNode(t)}>+ {v.label}</Button>)}
        <div className="ml-auto flex items-center gap-2">
          {dirty && <Badge tone="warn">unsaved</Badge>}
          <Button kind="primary" disabled={!dirty || saving} onClick={() => { onSave(fromFlow(graph, nodes, edges, vars, start)); setDirty(false); }}>
            {saving ? "Saving…" : "Save flow to draft"}</Button>
        </div>
      </div>
      <div className="grid gap-3 lg:grid-cols-[1fr_22rem]">
        <div className="h-[68vh] overflow-hidden rounded-xl border border-line bg-panel">
          <ReactFlow nodes={shown} edges={edges} nodeTypes={nodeTypes} fitView
            onNodesChange={c => { onNodesChange(c); if (c.some(x => x.type === "position" && !x.dragging)) touch(); }}
            onEdgesChange={onEdgesChange} onConnect={onConnect}
            onNodeClick={(_, n) => { if (n.id !== ANY) { setSel({ kind: "node", id: n.id }); setPanel("inspector"); } }}
            onEdgeClick={(_, e) => { setSel({ kind: "edge", id: e.id }); setPanel("inspector"); }}
            onPaneClick={() => setSel(null)} deleteKeyCode={null}>
            <Background gap={20} /><MiniMap pannable zoomable /><Controls />
          </ReactFlow>
        </div>
        <div className="h-[68vh] overflow-y-auto rounded-xl border border-line bg-panel p-3">
          <div className="mb-3 flex gap-1">
            <Button kind={panel === "inspector" ? "default" : "ghost"} onClick={() => setPanel("inspector")}>Inspector</Button>
            <Button kind={panel === "variables" ? "default" : "ghost"} onClick={() => setPanel("variables")}>Variables ({Object.keys(vars).length})</Button>
          </div>
          {panel === "variables" ? <Variables vars={vars} onChange={v => { setVars(v); touch(); }} />
            : current ? <NodeInspector key={current.id} node={current.data.node} isStart={current.id === start} tools={tools}
                skills={skills} variables={Object.keys(vars)} onChange={p => updateNode(current.id, p)}
                onRename={next => renameNode(current.id, next)} onStart={() => { setStart(current.id); touch(); }} onDelete={remove} />
            : currentEdge ? <EdgeInspector key={currentEdge.id} edge={(currentEdge.data as { edge: FlowEdge }).edge
                ?? { from: currentEdge.source, to: currentEdge.target }} fromTool={nodes.find(n => n.id === currentEdge.source)?.data.node.type === "tool"}
                onChange={e => updateEdge(currentEdge.id, e)} onDelete={remove} />
            : <div className="text-sm text-muted">Select a node or an edge. Drag from a node's right handle to another node to connect them.</div>}
        </div>
      </div>
    </div>
  );
}

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

function NodeInspector({ node, isStart, tools, skills, variables, onChange, onRename, onStart, onDelete }: {
  node: FlowNode; isStart: boolean; tools: string[]; skills: string[]; variables: string[];
  onChange: (p: Partial<FlowNode>) => void; onRename: (id: string) => void; onStart: () => void; onDelete: () => void;
}) {
  const [id, setId] = useState(node.id);
  const toggle = (list: string[] | undefined, v: string) => (list ?? []).includes(v) ? (list ?? []).filter(x => x !== v) : [...(list ?? []), v];
  return (
    <div className="space-y-3">
      {lbl("Node id", <div className="flex gap-2"><input id="node-id" className="min-w-0 flex-1 font-mono" value={id} onChange={e => setId(e.target.value.replace(/[^\w-]/g, ""))} />
        <Button onClick={() => onRename(id)} disabled={id === node.id}>Rename</Button></div>)}
      {lbl("Type", <select id="node-type" className="w-full" value={node.type} onChange={e => onChange({ type: e.target.value })}>
        {Object.entries(TYPES).map(([t, v]) => <option key={t} value={t}>{v.label}</option>)}</select>, TYPES[node.type]?.hint)}
      {node.type === "conversation" && <>
        {lbl("Instructions", <textarea id="node-instr" rows={8} className="w-full text-xs" value={node.instructions ?? ""}
          onChange={e => onChange({ instructions: e.target.value })} />, "What the agent does at this step. Shared rules belong in the skill / persona.")}
        <div><div className="mb-1 text-xs font-medium">Tools the model may call here</div>
          <div className="max-h-40 space-y-0.5 overflow-y-auto">{tools.map(t => (
            <label key={t} className="flex items-center gap-1.5 text-[11px]"><input type="checkbox" checked={(node.tools ?? []).includes(t)}
              onChange={() => onChange({ tools: toggle(node.tools, t) })} /><span className="font-mono">{t}</span></label>))}
            {tools.length === 0 && <div className="text-[11px] text-muted">This agent has no tools yet (Tools page).</div>}</div></div>
        <div><div className="mb-1 text-xs font-medium">Values to collect from the caller's words</div>
          {variables.map(v => (
            <label key={v} className="flex items-center gap-1.5 text-[11px]"><input type="checkbox" checked={(node.extract ?? []).includes(v)}
              onChange={() => onChange({ extract: toggle(node.extract, v) })} /><span className="font-mono">{v}</span></label>))}
          {variables.length === 0 && <div className="text-[11px] text-muted">Declare variables in the Variables tab.</div>}</div>
        {lbl("Tools the platform calls on arrival (JSON)", <JsonField id="node-auto" value={node.auto_call} onChange={v => onChange({ auto_call: v as FlowNode["auto_call"] })} />,
          '[{"tool": "name", "args": {"id": "slots.order_id"}, "blocking": true}]')}
      </>}
      {node.type === "tool" && <>
        {lbl("Tool", <select id="node-tool" className="w-full" value={node.tool ?? ""} onChange={e => onChange({ tool: e.target.value })}>
          <option value="">choose…</option>{tools.map(t => <option key={t}>{t}</option>)}</select>)}
        {lbl("Arguments (JSON)", <JsonField id="node-args" value={node.args} onChange={v => onChange({ args: v as FlowNode["args"] })} />,
          'Values: "slots.x" (collected), "=text" (literal)')}
      </>}
      {node.type === "set" && lbl("Values (JSON)", <JsonField id="node-set" value={node.set} onChange={v => onChange({ set: v as FlowNode["set"] })} />, '{"tries": "=1"}')}
      {node.type === "transfer" && lbl("Reason (logged)", <input id="node-reason" className="w-full" value={node.reason ?? ""} onChange={e => onChange({ reason: e.target.value })} />)}
      {node.type === "end" && <>
        {lbl("Closing line (Arabic)", <input id="node-say-ar" dir="rtl" className="w-full" value={node.say?.ar ?? ""} onChange={e => onChange({ say: { ...node.say, ar: e.target.value } })} />)}
        {lbl("Closing line (English)", <input id="node-say-en" className="w-full" value={node.say?.en ?? ""} onChange={e => onChange({ say: { ...node.say, en: e.target.value } })} />)}
      </>}
      {node.type === "skill" && lbl("Continue in skill", <select id="node-skill" className="w-full" value={node.skill ?? ""} onChange={e => onChange({ skill: e.target.value })}>
        <option value="">choose…</option>{skills.map(s => <option key={s}>{s}</option>)}</select>)}
      <div className="flex gap-2 pt-2">
        {!isStart && <Button onClick={onStart}>Make start</Button>}
        <Button kind="danger" onClick={onDelete}>Delete node</Button>
      </div>
    </div>
  );
}

const MODES = ["always", "llm", "filled", "empty", "equals", "stage", "json"] as const;

function EdgeInspector({ edge, fromTool, onChange, onDelete }: {
  edge: FlowEdge; fromTool: boolean; onChange: (e: FlowEdge) => void; onDelete: () => void;
}) {
  const w = edge.when ?? {};
  const keys = Object.keys(w);
  const initialMode = edge.on ? "result" : !keys.length ? "always" : keys.length === 1 && MODES.includes(keys[0] as never) ? keys[0] : "json";
  const [mode, setMode] = useState<string>(initialMode);
  const set = (when?: Record<string, unknown>, on?: string) => onChange({ from: edge.from, to: edge.to, ...(when ? { when } : {}), ...(on ? { on } : {}) });
  const list = (v: unknown) => (Array.isArray(v) ? v.join(", ") : "");
  return (
    <div className="space-y-3">
      <div className="text-xs"><span className="font-mono">{edge.from === "*" ? "Anywhere" : edge.from}</span> → <span className="font-mono">{edge.to}</span></div>
      {lbl("Follow this edge when", <select id="edge-mode" className="w-full" value={mode} onChange={e => { setMode(e.target.value); if (e.target.value === "always") set(); }}>
        <option value="always">always (put it last)</option>
        {fromTool && <option value="result">the tool succeeded / failed</option>}
        <option value="llm">the caller's words mean …</option>
        <option value="filled">these values are known</option>
        <option value="empty">these values are missing</option>
        <option value="equals">a value equals …</option>
        <option value="stage">caller-verification stage is …</option>
        <option value="json">advanced (JSON)</option>
      </select>, "Edges are tried in order: a node's own, then the ones from Anywhere. The first that matches wins.")}
      {mode === "result" && <select id="edge-on" className="w-full" value={edge.on ?? "success"} onChange={e => set(undefined, e.target.value)}>
        <option value="success">success</option><option value="failure">failure</option></select>}
      {mode === "llm" && lbl("Question", <input id="edge-llm" className="w-full" value={String(w.llm ?? "")} onChange={e => set({ llm: e.target.value })} />,
        "Short and plain, e.g. “the caller wants a different hospital”. Answered yes / no from the caller's latest words.")}
      {(mode === "filled" || mode === "empty") && lbl("Values (comma-separated)", <input id="edge-list" className="w-full font-mono" value={list(w[mode])}
        onChange={e => set({ [mode]: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} />)}
      {mode === "equals" && lbl("Value = (JSON)", <JsonField id="edge-eq" value={w.equals} onChange={v => set({ equals: v as Record<string, unknown> })} rows={2} />, '{"intent": "book"}')}
      {mode === "stage" && <select id="edge-stage" className="w-full" value={String(w.stage ?? "")} onChange={e => set({ stage: e.target.value })}>
        {["awaiting_mobile", "awaiting_dob_and_name", "send_otp", "awaiting_otp", "verified"].map(s => <option key={s}>{s}</option>)}</select>}
      {mode === "json" && lbl("Condition (JSON)", <JsonField id="edge-json" value={edge.when} onChange={v => set(v as Record<string, unknown>)} rows={6} />,
        "all / any / filled / empty / not_all_filled / equals / stage / llm")}
      <Button kind="danger" onClick={onDelete}>Delete edge</Button>
    </div>
  );
}

function Variables({ vars, onChange }: { vars: NonNullable<FlowGraph["variables"]>; onChange: (v: NonNullable<FlowGraph["variables"]>) => void }) {
  const [name, setName] = useState("");
  return (
    <div className="space-y-3">
      <p className="text-[11px] text-muted">Values the caller gives, read from their words by the model at nodes that collect them.
        Available to edges (“has …”, “… = …”) and tool arguments as slots.x.</p>
      {Object.entries(vars).map(([k, v]) => (
        <div key={k} className="space-y-1.5 rounded-lg border border-line p-2">
          <div className="flex items-center justify-between"><span className="font-mono text-xs font-medium">{k}</span>
            <Button kind="ghost" onClick={() => { const { [k]: _gone, ...rest } = vars; void _gone; onChange(rest); }}>Remove</Button></div>
          <select className="w-full text-xs" value={v.type ?? "string"} onChange={e => onChange({ ...vars, [k]: { ...v, type: e.target.value } })}>
            {["string", "integer", "number", "boolean", "date"].map(t => <option key={t}>{t}</option>)}</select>
          <input className="w-full text-xs" placeholder="allowed values, comma-separated (optional)" value={(v.enum ?? []).join(", ")}
            onChange={e => onChange({ ...vars, [k]: { ...v, enum: e.target.value.split(",").map(s => s.trim()).filter(Boolean) || undefined } })} />
          <input className="w-full text-xs" placeholder="what it is (helps the model)" value={v.description ?? ""}
            onChange={e => onChange({ ...vars, [k]: { ...v, description: e.target.value } })} />
        </div>
      ))}
      <form className="flex gap-2" onSubmit={e => { e.preventDefault(); if (name && !vars[name]) { onChange({ ...vars, [name]: { type: "string" } }); setName(""); } }}>
        <input id="var-name" className="min-w-0 flex-1 font-mono text-xs" placeholder="new_variable" value={name} onChange={e => setName(e.target.value.replace(/[^\w]/g, ""))} />
        <Button type="submit" disabled={!name}>Add</Button>
      </form>
    </div>
  );
}
