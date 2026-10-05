import AgentPicker from "../AgentPicker";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type EventRow } from "../api";
import { useLive } from "../live";
import { Badge, Button, Card, Empty, ErrorBox, fmtMs, fmtTime, levelTone } from "../ui";
import { summary } from "./CallDetail";

const TYPES = ["", "turn.start", "agent.say", "stt.result", "llm.first_token", "tool.start", "tool.end", "tool.error",
  "slot.set", "step.transition", "policy.block", "handoff", "interrupt", "error", "call.start", "call.end"];

export default function Logs() {
  const [filters, setFilters] = useState({ call_id: "", type: "", level: "", text: "" });
  const [applied, setApplied] = useState(filters);
  const [tail, setTail] = useState(false);
  const [open, setOpen] = useState<string | number | null>(null);
  const [agent, setAgent] = useState("");
  const q = useQuery({
    queryKey: ["events", applied, agent],
    queryFn: () => api.events({ ...applied, type: applied.type ? [applied.type] : undefined, limit: 300, agent: agent || undefined }),
    enabled: !tail,
  });
  const live = useLive(undefined, 300);
  const rows: (EventRow | (Omit<EventRow, "id"> & { id: string }))[] = tail
    ? live.events.filter(e => (!applied.type || e.type === applied.type) && (!applied.level || e.level === applied.level) &&
        (!applied.call_id || e.call_id === applied.call_id) &&
        (!applied.text || JSON.stringify(e.data).toLowerCase().includes(applied.text.toLowerCase())))
    : q.data ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold">Logs</h1>
      <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); setApplied(filters); }}>
        <AgentPicker value={agent} onChange={setAgent} />
        <input placeholder="Call id" value={filters.call_id} onChange={e => setFilters({ ...filters, call_id: e.target.value })} className="w-48" />
        <select value={filters.type} onChange={e => setFilters({ ...filters, type: e.target.value })}>
          {TYPES.map(t => <option key={t} value={t}>{t || "All types"}</option>)}
        </select>
        <select value={filters.level} onChange={e => setFilters({ ...filters, level: e.target.value })}>
          <option value="">All levels</option><option value="info">info</option><option value="warning">warning</option><option value="error">error</option>
        </select>
        <input placeholder="Search in data…" value={filters.text} onChange={e => setFilters({ ...filters, text: e.target.value })} className="w-56" />
        <Button type="submit" kind="primary">Apply</Button>
        <label className="ml-auto flex items-center gap-2 text-sm">
          <input type="checkbox" checked={tail} onChange={e => setTail(e.target.checked)} /> Live tail
        </label>
      </form>
      <ErrorBox error={q.error} />
      <Card>
        <div className="overflow-x-auto font-mono text-xs">
          {rows.map(e => (
            <div key={e.id} className="border-b border-line/60">
              <button className="grid w-full min-w-[48rem] grid-cols-[10rem_9rem_9rem_4.5rem_1fr] gap-2 py-1 text-left hover:bg-soft/60"
                onClick={() => setOpen(open === e.id ? null : e.id)}>
                <span className="text-muted">{fmtTime(e.ts)}</span>
                <span className="truncate">{e.call_id ? <Link to={`/calls/${e.call_id}`} className="text-accent-text" onClick={ev => ev.stopPropagation()}>{e.call_id}</Link> : "—"}</span>
                <span><Badge tone={levelTone(e.level)}>{e.type}</Badge></span>
                <span className="tabular-nums text-muted">{e.latency_ms != null ? fmtMs(e.latency_ms) : ""}</span>
                <span className="truncate" dir="auto">{summary(e)}</span>
              </button>
              {open === e.id && <pre className="overflow-x-auto whitespace-pre-wrap bg-soft/50 p-2" dir="auto">{JSON.stringify(e, null, 2)}</pre>}
            </div>
          ))}
          {!rows.length && <Empty>{tail ? "Waiting for live events…" : q.isLoading ? "Loading…" : "No events match."}</Empty>}
        </div>
      </Card>
    </div>
  );
}
