import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { Button, Card, Empty, ErrorBox } from "../ui";

// Phone numbers (Hamsa: Telephony → phone numbers): which agent of this project answers a number, an IVR extension
// prefix, or every other call ("*"). A number belongs to one project.

export default function Numbers() {
  const qc = useQueryClient();
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const routes = useQuery({ queryKey: ["routes"], queryFn: api.routes });
  const [route, setRoute] = useState({ pattern: "", agent_id: "" });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["routes"] }); qc.invalidateQueries({ queryKey: ["agents"] }); };
  const putRoute = useMutation({ mutationFn: (b: { pattern: string; agent_id: string }) => api.putRoute(b),
    onSuccess: () => { setRoute({ pattern: "", agent_id: "" }); refresh(); } });
  const delRoute = useMutation({ mutationFn: (p: string) => api.deleteRoute(p), onSuccess: refresh });
  const list = agents.data ?? [];
  const names = Object.fromEntries(list.map(a => [a.id, a.name]));
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Phone numbers</h1>
        <p className="text-sm text-muted">Which agent answers: an exact number, an IVR extension prefix like <span className="font-mono">8880*</span>,
          or <span className="font-mono">*</span> for every other call. The most specific match wins. A number belongs to one project.</p>
      </div>
      <Card title="Numbers and routes">
        {!(routes.data ?? []).length ? <Empty>No number reaches this project yet.</Empty> : (
          <div className="divide-y divide-line">
            {(routes.data ?? []).map(r => (
              <div key={r.pattern} className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-sm">
                <span className="w-40 font-mono">{r.pattern === "*" ? "* (every other call)" : r.pattern}</span>
                <span className="flex-1 text-muted">→ {names[r.agent_id] ?? r.agent_id}</span>
                {r.pattern === "*"
                  ? <select id="default-route" className="w-56 text-xs" value={r.agent_id} onChange={e => putRoute.mutate({ pattern: "*", agent_id: e.target.value })}>
                      {list.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </select>
                  : <Button kind="ghost" onClick={() => delRoute.mutate(r.pattern)}>Remove</Button>}
              </div>
            ))}
          </div>
        )}
        <form className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3" onSubmit={e => { e.preventDefault(); putRoute.mutate(route); }}>
          <input id="route-pattern" className="w-44 font-mono" placeholder="8880* or 0112345678" value={route.pattern}
            onChange={e => setRoute({ ...route, pattern: e.target.value })} />
          <select id="route-agent" className="w-56" value={route.agent_id} onChange={e => setRoute({ ...route, agent_id: e.target.value })}>
            <option value="">agent…</option>{list.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <Button kind="primary" type="submit" disabled={!route.pattern || !route.agent_id || putRoute.isPending}>Add number</Button>
        </form>
        <ErrorBox error={putRoute.error ?? delRoute.error} />
      </Card>
    </div>
  );
}
