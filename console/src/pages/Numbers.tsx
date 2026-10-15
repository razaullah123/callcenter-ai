import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type OutboundNumber, type PhoneRoute } from "../api";
import { Button, Card, Empty, ErrorBox, Modal } from "../ui";

// Phone numbers (Hamsa: Telephony → phone numbers): which agent of this project answers a number, an IVR extension
// prefix, or every other call ("*"); and the numbers a batch call / outbound call may dial from. A number belongs to
// one project. The platform has no telephony of its own: the IVR / PBX is the provider (docs/ivr_protocol.md).

const fmtDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "—");

function Confirm({ title, children, action, busy, error, danger, onCancel, onConfirm }: {
  title: string; children: React.ReactNode; action: string; busy?: boolean; error?: unknown; danger?: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="text-sm">{children}</div>
      <div className="mt-3"><ErrorBox error={error} /></div>
      <div className="mt-4 flex justify-end gap-2">
        <Button kind="ghost" onClick={onCancel}>Cancel</Button>
        <Button kind={danger ? "danger" : "primary"} onClick={onConfirm} disabled={busy}>{busy ? "Working…" : action}</Button>
      </div>
    </Modal>
  );
}

/** Hamsa's "Make Outbound Call": one call to one number, with values for the agent's variables. */
export function MakeCall({ numbers, from: fromNumber, fixedAgent, draft, onClose }: {
  numbers: OutboundNumber[]; from?: string; fixedAgent?: string; draft?: boolean; onClose: () => void;
}) {
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const routes = useQuery({ queryKey: ["routes"], queryFn: api.routes });
  const [pickedFrom, setPickedFrom] = useState(fromNumber ?? "");
  const from = numbers.find(n => n.number === pickedFrom) ?? numbers[0];
  const published = (agents.data ?? []).filter(a => a.version != null);
  const own = routes.data?.find(r => r.pattern === from?.number)?.agent_id;           // the agent that answers this number
  const [agent, setAgent] = useState("");
  const [to, setTo] = useState("");
  const [rows, setRows] = useState<{ k: string; v: string }[]>([]);
  const agentId = fixedAgent || agent || (own && published.some(a => a.id === own) ? own : published[0]?.id) || "";
  const bad = !/^\+[1-9]\d{6,14}$/.test(to.trim());
  const call = useMutation({
    mutationFn: () => api.makeOutboundCall({ agent_id: agentId, from_number: from!.number, to_number: to.trim(), draft: draft || undefined,
      params: Object.fromEntries(rows.filter(r => r.k.trim()).map(r => [r.k.trim(), r.v])) }),
  });
  if (!from) {
    return (
      <Modal title={draft ? "Test via phone" : "Make outbound call"} onClose={onClose}>
        <p className="text-sm">No outbound number yet. Add one under <Link to="/numbers" className="underline">Phone numbers</Link> — the number's dial URL is how your IVR / PBX places the call.</p>
        <div className="mt-4 flex justify-end"><Button onClick={onClose}>Close</Button></div>
      </Modal>
    );
  }
  return (
    <Modal title={draft ? "Test via phone" : "Make outbound call"} sub={draft ? "The agent's draft calls your phone — nothing needs to be published." : `From ${from.number}${from.label ? ` — ${from.label}` : ""}`} onClose={onClose}>
      {call.isSuccess ? (
        <div className="space-y-3 text-sm">
          <p>The call to <b className="font-mono">{call.data.to}</b> was queued. Until the server runs with <span className="font-mono">BATCH_LIVE_DIAL=true</span> nothing is really dialed.</p>
          <div className="flex justify-end gap-2"><Button kind="ghost" onClick={onClose}>Close</Button>
            <Link to={`/batch-calls/${call.data.batch_call_id}`} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg">See the call</Link></div>
        </div>
      ) : (
        <form className="space-y-3" onSubmit={e => { e.preventDefault(); if (!bad && agentId) call.mutate(); }}>
          <label className="block"><div className="mb-1 text-sm font-medium">Number to call</div>
            <input id="call-to" autoFocus className="w-full font-mono" placeholder="+966548802968" value={to} onChange={e => setTo(e.target.value)} />
            <span className={`mt-1 block text-[11px] ${to && bad ? "text-warn" : "text-muted"}`}>E.164: a + and the country code, digits only.</span></label>
          {(numbers.length > 1 || !fromNumber) && <label className="block"><div className="mb-1 text-sm font-medium">From number</div>
            <select id="call-from" className="w-full" value={from.number} onChange={e => setPickedFrom(e.target.value)}>
              {numbers.map(n => <option key={n.number} value={n.number}>{n.number}{n.label ? ` — ${n.label}` : ""}</option>)}</select></label>}
          {!fixedAgent && <label className="block"><div className="mb-1 text-sm font-medium">Voice agent</div>
            <select id="call-agent" className="w-full" value={agentId} onChange={e => setAgent(e.target.value)}>{published.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
            <span className="mt-1 block text-[11px] text-muted">Published agents only; the call uses the published version.</span></label>}
          <div>
            <div className="mb-1 flex items-center justify-between"><span className="text-sm font-medium">Custom parameters</span>
              <Button onClick={() => setRows([...rows, { k: "", v: "" }])}>+ Add</Button></div>
            {rows.map((r, i) => (
              <div key={i} className="mb-1.5 grid grid-cols-[1fr_1.4fr_auto] gap-2">
                <input aria-label={`Parameter ${i + 1} name`} className="font-mono text-xs" placeholder="customer_name" value={r.k} onChange={e => setRows(rows.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)))} />
                <input aria-label={`Parameter ${i + 1} value`} className="text-sm" placeholder="Sara" value={r.v} onChange={e => setRows(rows.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)))} />
                <button type="button" aria-label={`Remove parameter ${i + 1}`} onClick={() => setRows(rows.filter((_, j) => j !== i))} className="rounded px-2 text-bad hover:bg-bad/10">✕</button>
              </div>
            ))}
            <p className="text-[11px] text-muted">Values for the agent's variables (e.g. <span className="font-mono">{"{{customer_name}}"}</span> in the greeting). Names the agent doesn't use are ignored.</p>
          </div>
          <ErrorBox error={call.error} />
          <div className="flex justify-end gap-2"><Button kind="ghost" onClick={onClose}>Cancel</Button>
            <Button kind="primary" type="submit" disabled={bad || !agentId || call.isPending}>{call.isPending ? "Calling…" : draft ? "Call me" : "Make call"}</Button></div>
        </form>
      )}
    </Modal>
  );
}

function OutboundNumbers() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["outbound-numbers"], queryFn: api.outboundNumbers });
  const blank = { number: "", label: "", dial_url: "", dial_token: "" };
  const [form, setForm] = useState(blank);
  const [calling, setCalling] = useState<OutboundNumber | null>(null);
  const [removing, setRemoving] = useState<OutboundNumber | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["outbound-numbers"] });
  const put = useMutation({ mutationFn: () => api.putOutboundNumber({ number: form.number.trim(), label: form.label.trim() || undefined,
    dial_url: form.dial_url.trim(), dial_token: form.dial_token || undefined }), onSuccess: () => { setForm(blank); refresh(); } });
  const del = useMutation({ mutationFn: (n: string) => api.deleteOutboundNumber(n), onSuccess: () => { setRemoving(null); refresh(); } });
  const edit = (n: OutboundNumber) => setForm({ number: n.number, label: n.label ?? "", dial_url: n.dial_url, dial_token: "" });
  return (
    <Card title="Outbound numbers (batch calls)">
      <p className="mb-3 text-sm text-muted">A number a batch call or a single outbound call may dial from. For every call the platform sends a request to the
        number's <b>dial URL</b>; your IVR / PBX places the call and, once the person answers, connects its audio to the agent's IVR socket
        (see <span className="font-mono">docs/ivr_protocol.md</span>, “Outbound calls”). Until the server runs with <span className="font-mono">BATCH_LIVE_DIAL=true</span>, no real call is placed.</p>
      {!(q.data ?? []).length ? <Empty>No outbound number yet.</Empty> : (
        <div className="divide-y divide-line">
          {(q.data ?? []).map(n => (
            <div key={n.number} className="flex flex-wrap items-center gap-3 py-2 text-sm">
              <span className="font-mono">{n.number}</span>{n.label && <span className="text-muted">{n.label}</span>}
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted" title={n.dial_url}>{n.dial_url}{n.has_token ? " · token set" : ""}</span>
              <span className="text-xs text-muted">{fmtDate(n.created_at)}</span>
              <Button onClick={() => setCalling(n)}>Make outbound call</Button>
              <Button kind="ghost" onClick={() => edit(n)}>Edit</Button>
              <Button kind="ghost" onClick={() => setRemoving(n)}>Remove</Button>
            </div>
          ))}
        </div>
      )}
      <form className="mt-3 grid gap-2 border-t border-line pt-3 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); put.mutate(); }}>
        <input id="out-number" className="font-mono" placeholder="+966112000000" aria-label="Number" value={form.number} onChange={e => setForm({ ...form, number: e.target.value })} />
        <input id="out-label" placeholder="Label (optional)" aria-label="Label" value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} />
        <input id="out-url" className="font-mono sm:col-span-2" placeholder="https://pbx.example.com/dial" aria-label="Dial URL" value={form.dial_url} onChange={e => setForm({ ...form, dial_url: e.target.value })} />
        <input id="out-token" type="password" className="sm:col-span-2" placeholder="Bearer token for the dial URL (optional, stored as a secret; leave empty to keep the current one)" aria-label="Dial token"
          value={form.dial_token} onChange={e => setForm({ ...form, dial_token: e.target.value })} />
        <div className="sm:col-span-2"><Button kind="primary" type="submit" disabled={!form.number.trim() || !form.dial_url.trim() || put.isPending}>Save outbound number</Button></div>
      </form>
      <ErrorBox error={put.error} />
      {calling && <MakeCall numbers={q.data ?? []} from={calling.number} onClose={() => setCalling(null)} />}
      {removing && (
        <Confirm title="Remove outbound number" action="Remove" danger busy={del.isPending} error={del.error} onCancel={() => setRemoving(null)} onConfirm={() => del.mutate(removing.number)}>
          Remove <b className="font-mono">{removing.number}</b>? Batch calls can no longer dial from it. This can't be undone.
        </Confirm>
      )}
    </Card>
  );
}

export default function Numbers() {
  const qc = useQueryClient();
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const routes = useQuery({ queryKey: ["routes"], queryFn: api.routes });
  const [route, setRoute] = useState({ pattern: "", agent_id: "", label: "" });
  const [reassign, setReassign] = useState<{ r: PhoneRoute; agent_id: string } | null>(null);
  const [relabel, setRelabel] = useState<{ r: PhoneRoute; label: string } | null>(null);
  const [removing, setRemoving] = useState<PhoneRoute | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["routes"] }); qc.invalidateQueries({ queryKey: ["agents"] }); };
  const putRoute = useMutation({ mutationFn: (b: { pattern: string; agent_id: string; label?: string }) => api.putRoute(b),
    onSuccess: () => { setRoute({ pattern: "", agent_id: "", label: "" }); setReassign(null); setRelabel(null); refresh(); } });
  const delRoute = useMutation({ mutationFn: (p: string) => api.deleteRoute(p), onSuccess: () => { setRemoving(null); refresh(); } });
  const list = agents.data ?? [];
  const names = Object.fromEntries(list.map(a => [a.id, a.name]));
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Phone numbers</h1>
        <p className="text-sm text-muted">Which agent answers: an exact number, an IVR extension prefix like <span className="font-mono">8880*</span>,
          or <span className="font-mono">*</span> for every other call. The most specific match wins. A number belongs to one project and to one agent at a time.</p>
      </div>
      <Card title="Numbers and routes">
        {!(routes.data ?? []).length ? <Empty>No number reaches this project yet.</Empty> : (
          <div className="divide-y divide-line">
            {(routes.data ?? []).map(r => (
              <div key={r.pattern} className="flex flex-wrap items-center gap-3 py-2.5 text-sm">
                <span className="w-44 font-mono">{r.pattern === "*" ? "* (every other call)" : r.pattern}</span>
                <span className="w-40 truncate text-muted" title={r.label ?? ""}>{r.label || "—"}</span>
                <select aria-label={`Agent for ${r.pattern}`} id={r.pattern === "*" ? "default-route" : undefined} className="w-56 text-xs" value={r.agent_id}
                  onChange={e => e.target.value !== r.agent_id && setReassign({ r, agent_id: e.target.value })}>
                  {list.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
                <span className="flex-1 text-xs text-muted">{fmtDate(r.created_at)}</span>
                <Button kind="ghost" onClick={() => setRelabel({ r, label: r.label ?? "" })}>Edit label</Button>
                {r.pattern !== "*" && <Button kind="ghost" onClick={() => setRemoving(r)}>Unassign</Button>}
              </div>
            ))}
          </div>
        )}
        <form className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3" onSubmit={e => { e.preventDefault(); putRoute.mutate({ ...route, label: route.label.trim() || undefined }); }}>
          <input id="route-pattern" className="w-44 font-mono" placeholder="8880* or +966112345678" value={route.pattern}
            onChange={e => setRoute({ ...route, pattern: e.target.value })} />
          <input id="route-label" className="w-44" placeholder="Label (e.g. Support line)" maxLength={100} value={route.label} onChange={e => setRoute({ ...route, label: e.target.value })} />
          <select id="route-agent" className="w-56" value={route.agent_id} onChange={e => setRoute({ ...route, agent_id: e.target.value })}>
            <option value="">agent…</option>{list.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <Button kind="primary" type="submit" disabled={!route.pattern || !route.agent_id || putRoute.isPending}>Add number</Button>
        </form>
        <ErrorBox error={reassign || relabel || removing ? null : putRoute.error ?? delRoute.error} />
      </Card>
      <OutboundNumbers />

      {reassign && (
        <Confirm title="Reassign number" action="Reassign" busy={putRoute.isPending} error={putRoute.error} onCancel={() => { setReassign(null); putRoute.reset(); }}
          onConfirm={() => putRoute.mutate({ pattern: reassign.r.pattern, agent_id: reassign.agent_id })}>
          Calls to <b className="font-mono">{reassign.r.pattern === "*" ? "every other number" : reassign.r.pattern}</b> now go to <b>{names[reassign.agent_id] ?? reassign.agent_id}</b> instead of{" "}
          <b>{names[reassign.r.agent_id] ?? reassign.r.agent_id}</b>. Calls in progress are not affected.
        </Confirm>
      )}
      {relabel && (
        <Modal title="Edit label" sub={relabel.r.pattern} onClose={() => { setRelabel(null); putRoute.reset(); }}>
          <form onSubmit={e => { e.preventDefault(); putRoute.mutate({ pattern: relabel.r.pattern, agent_id: relabel.r.agent_id, label: relabel.label.trim() }); }}>
            <input id="route-relabel" autoFocus maxLength={100} className="w-full" aria-label="Label" value={relabel.label} onChange={e => setRelabel({ ...relabel, label: e.target.value })} />
            <div className="mt-3"><ErrorBox error={putRoute.error} /></div>
            <div className="mt-4 flex justify-end gap-2"><Button kind="ghost" onClick={() => setRelabel(null)}>Cancel</Button>
              <Button kind="primary" type="submit" disabled={putRoute.isPending}>Save</Button></div>
          </form>
        </Modal>
      )}
      {removing && (
        <Confirm title="Unassign number" action="Unassign" danger busy={delRoute.isPending} error={delRoute.error} onCancel={() => { setRemoving(null); delRoute.reset(); }}
          onConfirm={() => delRoute.mutate(removing.pattern)}>
          Stop routing <b className="font-mono">{removing.pattern}</b> to <b>{names[removing.agent_id] ?? removing.agent_id}</b>? Calls to it will go to the default agent instead.
        </Confirm>
      )}
    </div>
  );
}
