import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ApiKey, type NewApiKey } from "../api";
import { CopyButton } from "../table";
import { Badge, Button, Empty, ErrorBox, Modal, cx } from "../ui";

// API keys (Hamsa: Create API keys). A key belongs to this project and is sent as `Authorization: Token hmg_…`.

const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—");
const EXPIRY: [string, number | null][] = [["Never", null], ["30 days", 30], ["90 days", 90], ["1 year", 365]];
const TONE = { active: "good", expired: "warn", revoked: "neutral" } as const;

function Snippets({ secret }: { secret: string }) {
  const host = window.location.origin;
  const who = `curl ${host}/api/whoami \\\n  -H "Authorization: Token ${secret}"`;
  const call = `curl -X POST ${host}/api/outbound-calls \\\n  -H "Authorization: Token ${secret}" -H "Content-Type: application/json" \\\n  -d '{"agent_id": "<agent id>", "from_number": "+966112000000", "to_number": "+966500000000", "params": {"customer_name": "Sara"}}'`;
  return (
    <div className="space-y-3">
      {[["Check the key", who], ["Place a call", call]].map(([title, code]) => (
        <div key={title}>
          <div className="mb-1 flex items-center justify-between text-xs text-muted"><span>{title}</span><CopyButton text={code} label={`Copy: ${title}`} /></div>
          <pre className="overflow-x-auto rounded-lg bg-soft p-2.5 font-mono text-[11px] leading-relaxed">{code}</pre>
        </div>
      ))}
    </div>
  );
}

function CreateKey({ onClose, onMade }: { onClose: () => void; onMade: (k: NewApiKey) => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"full" | "read">("full");
  const [days, setDays] = useState<number | null>(null);
  const make = useMutation({
    mutationFn: () => api.createApiKey({ name: name.trim(), scope, expires_days: days ?? undefined }),
    onSuccess: k => { qc.invalidateQueries({ queryKey: ["api-keys"] }); onMade(k); },
  });
  return (
    <Modal title="Create API key" sub="For your own systems — a CRM, a website, a script — to use this project's API." onClose={onClose}>
      <form className="space-y-4" onSubmit={e => { e.preventDefault(); if (name.trim()) make.mutate(); }}>
        <label className="block"><div className="mb-1 text-sm font-medium">Name</div>
          <input id="key-name" autoFocus maxLength={60} className="w-full" placeholder="e.g. CRM integration" value={name} onChange={e => setName(e.target.value)} />
          <span className="mt-1 block text-[11px] text-muted">Shown in the audit log next to everything this key changes.</span></label>
        <div>
          <div className="mb-1 text-sm font-medium">What it may do</div>
          {([["full", "Full access", "Start calls and batch calls, change agents, knowledge and numbers — everything except the limits below."],
            ["read", "Read only", "Look at call history, analytics, agents and numbers. Nothing can be changed or started."]] as const).map(([k, label, text]) => (
            <label key={k} className={cx("mb-1.5 flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5", scope === k ? "border-accent bg-accent/5" : "border-line")}>
              <input type="radio" name="scope" className="mt-1" checked={scope === k} onChange={() => setScope(k)} />
              <span><span className="block text-sm font-medium">{label}</span><span className="text-xs text-muted">{text}</span></span></label>))}
        </div>
        <label className="block"><div className="mb-1 text-sm font-medium">Expires</div>
          <select id="key-expiry" className="w-full" value={days ?? ""} onChange={e => setDays(e.target.value ? Number(e.target.value) : null)}>
            {EXPIRY.map(([l, d]) => <option key={l} value={d ?? ""}>{l}</option>)}</select></label>
        <p className="rounded-lg bg-soft/60 p-2.5 text-[11px] text-muted">Whatever its scope, a key can't manage keys, people, projects or secrets, can't listen to calls and never reaches another
          project. It is limited to 120 requests a minute.</p>
        <ErrorBox error={make.error} />
        <div className="flex justify-end gap-2"><Button kind="ghost" onClick={onClose}>Cancel</Button>
          <Button kind="primary" type="submit" disabled={!name.trim() || make.isPending}>{make.isPending ? "Creating…" : "Create key"}</Button></div>
      </form>
    </Modal>
  );
}

export default function ApiKeys() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["api-keys"], queryFn: api.apiKeys });
  const [creating, setCreating] = useState(false);
  const [made, setMade] = useState<NewApiKey | null>(null);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const revoke = useMutation({ mutationFn: (id: string) => api.revokeApiKey(id), onSuccess: () => { setRevoking(null); qc.invalidateQueries({ queryKey: ["api-keys"] }); } });
  const keys = q.data ?? [];
  return (
    <div className="mx-auto max-w-5xl space-y-4 pb-10">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line pb-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">API keys</h1>
          <p className="mt-1 text-sm text-muted">Let your own systems use this project's API. Send the key as <span className="font-mono">Authorization: Token hmg_…</span>.
            Only a fingerprint is kept here, so a lost key can't be shown again — make a new one and revoke the old.</p>
        </div>
        <Button kind="primary" onClick={() => setCreating(true)}>+ Create API key</Button>
      </div>
      <ErrorBox error={q.error} />
      {q.data && !keys.length ? <Empty>No API keys yet.</Empty> : (
        <div className="overflow-x-auto rounded-xl border border-line bg-panel">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line text-left text-xs text-muted">
              <th className="p-3 font-medium">Name</th><th className="p-3 font-medium">Key</th><th className="p-3 font-medium">Access</th><th className="p-3 font-medium">Created</th>
              <th className="p-3 font-medium">Last used</th><th className="p-3 font-medium">Expires</th><th className="p-3 font-medium">Status</th><th className="p-3" /></tr></thead>
            <tbody>
              {keys.map(k => (
                <tr key={k.id} className={cx("border-b border-line last:border-0", k.status !== "active" && "text-muted")}>
                  <td className="p-3 font-medium">{k.name}<div className="text-[11px] font-normal text-muted">{k.created_by ?? ""}</div></td>
                  <td className="p-3 font-mono text-xs">{k.prefix}…</td>
                  <td className="p-3"><Badge tone={k.scope === "full" ? "info" : "neutral"}>{k.scope === "full" ? "Full" : "Read only"}</Badge></td>
                  <td className="p-3 whitespace-nowrap">{when(k.created_at)}</td>
                  <td className="p-3 whitespace-nowrap">{k.last_used_at ? when(k.last_used_at) : "Never"}</td>
                  <td className="p-3 whitespace-nowrap">{k.expires_at ? when(k.expires_at) : "Never"}</td>
                  <td className="p-3"><Badge tone={TONE[k.status]}>{k.status[0].toUpperCase() + k.status.slice(1)}</Badge></td>
                  <td className="p-3 text-right">{k.status !== "revoked" && <button aria-label={`Revoke ${k.name}`} onClick={() => setRevoking(k)} className="rounded px-2 py-1 text-xs text-bad hover:bg-bad/10">Revoke</button>}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      )}
      {creating && <CreateKey onClose={() => setCreating(false)} onMade={k => { setCreating(false); setMade(k); }} />}
      {made && (
        <Modal title="Your new API key" sub="Copy it now — it is not shown again." onClose={() => setMade(null)} wide>
          <div className="flex items-center gap-2 rounded-lg border border-accent/50 bg-accent/5 p-3">
            <code id="new-key" className="min-w-0 flex-1 break-all font-mono text-xs">{made.key}</code>
            <CopyButton text={made.key} label="Copy the key" />
          </div>
          <p className="mt-2 text-xs text-muted">{made.name} · {made.scope === "full" ? "full access" : "read only"} · {made.expires_at ? `expires ${when(made.expires_at)}` : "never expires"}</p>
          <div className="mt-4"><Snippets secret={made.key} /></div>
          <div className="mt-4 flex justify-end"><Button kind="primary" onClick={() => setMade(null)}>I have copied it</Button></div>
        </Modal>
      )}
      {revoking && (
        <Modal title="Revoke API key" onClose={() => setRevoking(null)}>
          <p className="text-sm">Revoke <b>{revoking.name}</b> (<span className="font-mono">{revoking.prefix}…</span>)? Anything using it stops working at once. This can't be undone.</p>
          <div className="mt-3"><ErrorBox error={revoke.error} /></div>
          <div className="mt-4 flex justify-end gap-2"><Button kind="ghost" onClick={() => setRevoking(null)}>Cancel</Button>
            <Button kind="danger" onClick={() => revoke.mutate(revoking.id)} disabled={revoke.isPending}>{revoke.isPending ? "Revoking…" : "Revoke"}</Button></div>
        </Modal>
      )}
    </div>
  );
}
