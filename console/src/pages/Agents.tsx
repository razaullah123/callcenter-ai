import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, fmtTime } from "../ui";

export default function Agents() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const routes = useQuery({ queryKey: ["routes"], queryFn: api.routes });
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [copyFrom, setCopyFrom] = useState("");
  const create = useMutation({
    mutationFn: () => api.createAgent({ name, description, copy_from: copyFrom || undefined }),
    onSuccess: r => { qc.invalidateQueries({ queryKey: ["agents"] }); nav(`/agents/${r.id}`); },
  });
  const [route, setRoute] = useState({ pattern: "", agent_id: "" });
  const putRoute = useMutation({
    mutationFn: (b: { pattern: string; agent_id: string }) => api.putRoute(b),
    onSuccess: () => { setRoute({ pattern: "", agent_id: "" }); qc.invalidateQueries({ queryKey: ["routes"] }); qc.invalidateQueries({ queryKey: ["agents"] }); },
  });
  const delRoute = useMutation({
    mutationFn: (p: string) => api.deleteRoute(p),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["routes"] }); qc.invalidateQueries({ queryKey: ["agents"] }); },
  });
  if (!q.data) return <ErrorBox error={q.error} />;
  const names = Object.fromEntries(q.data.map(a => [a.id, a.name]));

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Agents</h1>
          <p className="max-w-2xl text-sm text-muted">Each agent has its own flow, prompts, tools, models and voice. Edit
            in the studio, test the draft with a call, then publish — callers only ever reach published versions.</p>
        </div>
        <Button kind="primary" onClick={() => setCreating(true)} disabled={creating}>New agent</Button>
      </div>

      {creating && (
        <Card title="New agent">
          <form className="space-y-3" onSubmit={e => { e.preventDefault(); create.mutate(); }}>
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="block"><div className="mb-1 text-xs font-medium">Name</div>
                <input id="agent-name" className="w-full" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Clinic information line" /></label>
              <label className="block sm:col-span-2"><div className="mb-1 text-xs font-medium">What it's for</div>
                <input id="agent-desc" className="w-full" value={description} onChange={e => setDescription(e.target.value)} /></label>
            </div>
            <label className="block max-w-sm"><div className="mb-1 text-xs font-medium">Start from</div>
              <select id="agent-copy" className="w-full" value={copyFrom} onChange={e => setCopyFrom(e.target.value)}>
                <option value="">A blank agent (one conversation node, no caller verification)</option>
                {q.data.map(a => <option key={a.id} value={a.id}>A copy of {a.name}</option>)}
              </select></label>
            <ErrorBox error={create.error} />
            <div className="flex gap-2">
              <Button kind="primary" type="submit" disabled={!name.trim() || create.isPending}>{create.isPending ? "Creating…" : "Create and open"}</Button>
              <Button kind="ghost" onClick={() => setCreating(false)}>Cancel</Button>
            </div>
          </form>
        </Card>
      )}

      <Card title={`${q.data.length} agents`}>
        {q.data.length === 0 ? <Empty>No agents yet.</Empty> : (
          <div className="divide-y divide-line">
            {q.data.map(a => (
              <Link key={a.id} to={`/agents/${a.id}`} className="flex flex-wrap items-center justify-between gap-3 py-3 hover:bg-soft/50">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{a.name}</span>
                    {a.default && <Badge tone="info">answers by default</Badge>}
                    {a.has_draft && <Badge tone="warn">unpublished changes</Badge>}
                  </div>
                  <div className="text-xs text-muted">{a.description || a.id}</div>
                  <div className="mt-0.5 text-[11px] text-muted">{a.skills.filter(s => s !== "_persona").join(" · ")}</div>
                </div>
                <div className="text-right text-xs text-muted">
                  <div>{a.version ? <>release <b className="text-ink">v{a.version}</b></> : "not published"}</div>
                  {a.published_at && <div>{fmtTime(a.published_at)}</div>}
                  {a.routes.length > 0 && <div className="font-mono">{a.routes.join(", ")}</div>}
                </div>
              </Link>
            ))}
          </div>
        )}
      </Card>

      <Card title="Phone routes">
        <p className="mb-3 text-xs text-muted">Which agent answers: an exact number, an IVR extension prefix like <span className="font-mono">8880*</span>,
          or <span className="font-mono">*</span> for every other call. The most specific match wins.</p>
        <div className="divide-y divide-line">
          {(routes.data ?? []).map(r => (
            <div key={r.pattern} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="font-mono">{r.pattern}</span>
              <span className="flex-1 text-muted">→ {names[r.agent_id] ?? r.agent_id}</span>
              {r.pattern === "*"
                ? <select id="default-route" className="w-56 text-xs" value={r.agent_id} onChange={e => putRoute.mutate({ pattern: "*", agent_id: e.target.value })}>
                    {q.data.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select>
                : <Button kind="ghost" onClick={() => delRoute.mutate(r.pattern)}>Remove</Button>}
            </div>
          ))}
        </div>
        <form className="mt-3 flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); putRoute.mutate(route); }}>
          <input id="route-pattern" className="w-44 font-mono" placeholder="8880* or 0112345678" value={route.pattern}
            onChange={e => setRoute({ ...route, pattern: e.target.value })} />
          <select id="route-agent" className="w-56" value={route.agent_id} onChange={e => setRoute({ ...route, agent_id: e.target.value })}>
            <option value="">agent…</option>{q.data.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <Button type="submit" disabled={!route.pattern || !route.agent_id || putRoute.isPending}>Add route</Button>
        </form>
        <ErrorBox error={putRoute.error ?? delRoute.error} />
      </Card>
    </div>
  );
}
