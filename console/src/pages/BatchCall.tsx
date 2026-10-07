import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type BatchCheck, type BatchStatus, type RecipientStatus } from "../api";
import { Pager } from "../table";
import { Badge, Button, Empty, ErrorBox, Modal, cx } from "../ui";
import { BATCH_LABEL, BATCH_TONE, RecipientsStep, describeWindow, fmtWhen } from "./BatchCalls";

const PAGE = 50;
const R_LABEL: Record<RecipientStatus, string> = { pending: "Pending", in_progress: "In progress", completed: "Completed", failed: "Failed", no_answer: "No answer" };
const R_TONE: Record<RecipientStatus, "neutral" | "good" | "warn" | "bad" | "info"> = {
  pending: "neutral", in_progress: "info", completed: "good", failed: "bad", no_answer: "warn",
};
type Action = "pause" | "resume" | "cancel" | "retry";
const ACTIONS: { action: Action; label: string; when: BatchStatus[]; confirm: string; danger?: boolean }[] = [
  { action: "pause", label: "Pause", when: ["running"], confirm: "Calls in progress finish; no new calls are placed until you resume." },
  { action: "resume", label: "Resume", when: ["paused"], confirm: "Calling continues from where it stopped." },
  { action: "retry", label: "Retry failed", when: ["completed", "failed", "cancelled"], confirm: "Only recipients that failed or didn't answer (and any still waiting) are called again; completed calls are kept." },
  { action: "cancel", label: "Cancel", when: ["scheduled", "running", "paused"], danger: true, confirm: "Stops this batch call for good. Calls in progress finish." },
];

function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  return <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">{rows.map(([k, v]) => (
    <div key={k}><dt className="text-xs text-muted">{k}</dt><dd className="mt-0.5 text-sm">{v}</dd></div>))}</dl>;
}

function AddRecipients({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [rows, setRows] = useState<BatchCheck | null>(null);
  const add = useMutation({
    mutationFn: () => api.addBatchRecipients(id, { rows: (rows?.accepted ?? []).map(({ errors: _e, row: _r, ...r }) => r) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["batch-call", id] }); qc.invalidateQueries({ queryKey: ["batch-recipients", id] }); onClose(); },
  });
  return (
    <Modal title="Add recipients" sub="Upload another CSV; its recipients join this batch call." onClose={onClose} wide="xl">
      <RecipientsStep value={rows} onChange={setRows} />
      <div className="mt-3"><ErrorBox error={add.error} /></div>
      <div className="mt-4 flex justify-end gap-2">
        <Button kind="ghost" onClick={onClose}>Cancel</Button>
        <Button kind="primary" onClick={() => add.mutate()} disabled={!rows || !rows.accepted.length || rows.rejected.length > 0 || add.isPending}>
          {add.isPending ? "Adding…" : `Add ${rows?.accepted.length ?? 0} recipients`}</Button>
      </div>
    </Modal>
  );
}

export default function BatchCall() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["batch-call", id], queryFn: () => api.batchCall(id),
    refetchInterval: d => (d.state.data && ["running", "scheduled"].includes(d.state.data.status) ? 3000 : false) });
  const [tab, setTab] = useState<"summary" | "recipients" | "config">("summary");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [dialog, setDialog] = useState<null | { kind: "confirm"; action: Action } | { kind: "rename" } | { kind: "delete" } | { kind: "add" }>(null);
  const [newName, setNewName] = useState("");
  const recips = useQuery({ queryKey: ["batch-recipients", id, status, search, page], enabled: tab === "recipients",
    queryFn: () => api.batchRecipients(id, { status, q: search, limit: PAGE, offset: page * PAGE }),
    refetchInterval: q.data?.status === "running" ? 3000 : false });
  const done = () => { qc.invalidateQueries({ queryKey: ["batch-call", id] }); qc.invalidateQueries({ queryKey: ["batch-calls"] }); qc.invalidateQueries({ queryKey: ["batch-recipients", id] }); setDialog(null); };
  const act = useMutation({ mutationFn: (a: Action) => api.batchAction(id, a), onSuccess: done });
  const rename = useMutation({ mutationFn: () => api.renameBatch(id, newName.trim()), onSuccess: done });
  const del = useMutation({ mutationFn: () => api.deleteBatch(id), onSuccess: () => { qc.invalidateQueries({ queryKey: ["batch-calls"] }); nav("/batch-calls"); } });
  const drop = useMutation({ mutationFn: (rid: number) => api.removeBatchRecipient(id, rid), onSuccess: done });
  const b = q.data;
  if (!b) return <ErrorBox error={q.error} />;
  const dialogAction = dialog?.kind === "confirm" ? ACTIONS.find(a => a.action === dialog.action) : undefined;
  const c = b.counts;
  return (
    <div className="mx-auto max-w-6xl space-y-4 pb-10">
      <div><Link to="/batch-calls" className="text-xs text-muted hover:text-ink">← Batch calls</Link></div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold tracking-tight">{b.name}<Badge tone={BATCH_TONE[b.status]}>{BATCH_LABEL[b.status]}</Badge></h1>
          <p className="mt-1 text-sm text-muted">{b.agent_name} · from <span className="font-mono">{b.from_number}</span> · created {fmtWhen(b.created_at)}{b.created_by ? ` by ${b.created_by}` : ""}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {ACTIONS.filter(a => a.when.includes(b.status) && (a.action !== "retry" || (c.failed ?? 0) + (c.no_answer ?? 0) + (c.pending ?? 0) > 0)).map(a => (
            <Button key={a.action} kind={a.danger ? "danger" : a.action === "retry" ? "primary" : "default"} onClick={() => setDialog({ kind: "confirm", action: a.action })}>{a.label}</Button>))}
          <Button onClick={() => { setNewName(b.name); setDialog({ kind: "rename" }); }}>Rename</Button>
          <Button onClick={() => q.refetch()}>Reload</Button>
          <Button kind="ghost" onClick={() => setDialog({ kind: "delete" })}>Delete</Button>
        </div>
      </div>

      <div className="flex gap-1 border-b border-line">
        {([["summary", "Summary"], ["recipients", "Recipients"], ["config", "Configuration"]] as const).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={cx("-mb-px border-b-2 px-3 py-2 text-sm", tab === k ? "border-accent font-medium" : "border-transparent text-muted hover:text-ink")}>{l}</button>))}
      </div>

      {tab === "summary" && (
        <div className="space-y-5">
          <div className="rounded-xl border border-line bg-panel p-4">
            <div className="mb-2 flex items-center justify-between text-sm"><span className="font-medium">Progress</span><span className="text-muted">{b.progress}% of {b.total.toLocaleString()}</span></div>
            <div className="h-2 rounded bg-soft"><div className="h-2 rounded bg-accent transition-all" style={{ width: `${b.progress}%` }} /></div>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
              {(Object.keys(R_LABEL) as RecipientStatus[]).map(s => (
                <button key={s} onClick={() => { setTab("recipients"); setStatus(s); setPage(0); }} className="rounded-lg border border-line p-3 text-left hover:bg-soft/60">
                  <div className="text-xl font-semibold tabular-nums">{(c[s] ?? 0).toLocaleString()}</div><div className="text-xs text-muted">{R_LABEL[s]}</div></button>))}
            </div>
          </div>
          <Facts rows={[["Status", BATCH_LABEL[b.status]], ["Recipients", b.total.toLocaleString()], ["Started", fmtWhen(b.started_at)], ["Finished", fmtWhen(b.finished_at)],
            ["Voice agent", b.agent_name], ["From number", <span className="font-mono" key="n">{b.from_number}</span>]]} />
        </div>
      )}

      {tab === "config" && (
        <div className="rounded-xl border border-line bg-panel p-4">
          <Facts rows={[["Send type", b.config.send_type === "now" ? "Send now" : "Scheduled"],
            ["Start", b.config.send_type === "schedule" ? `${b.config.scheduled_at} (${b.config.timezone})` : "As soon as possible"],
            ["Timezone", b.config.timezone], ["Daily window", `${b.config.window_start} – ${b.config.window_end}`],
            ["Allowed days", describeWindow(b.config).split(" · ")[1]], ["Concurrency", "Shared with all batch calls (set by the server)"]]} />
          <p className="mt-4 text-xs text-muted">The number, agent and schedule can't be changed after creation.</p>
        </div>
      )}

      {tab === "recipients" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <input id="rec-search" aria-label="Search recipients" className="w-64" placeholder="Search phone or name…" value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} />
            <select id="rec-status" aria-label="Status" value={status} onChange={e => { setStatus(e.target.value); setPage(0); }}>
              <option value="">All ({b.total})</option>{(Object.keys(R_LABEL) as RecipientStatus[]).map(s => <option key={s} value={s}>{R_LABEL[s]} ({c[s] ?? 0})</option>)}</select>
            {!["completed", "failed", "cancelled"].includes(b.status) && <div className="ml-auto"><Button onClick={() => setDialog({ kind: "add" })}>Add recipients</Button></div>}
          </div>
          <ErrorBox error={recips.error ?? drop.error} />
          {recips.data && !recips.data.items.length ? <Empty>No recipients match.</Empty> : (
            <div className="overflow-x-auto rounded-xl border border-line bg-panel">
              <table className="w-full text-sm">
                <thead><tr className="border-b border-line text-left text-xs text-muted">
                  <th className="p-3 font-medium">Phone</th><th className="p-3 font-medium">Name</th><th className="p-3 font-medium">Status</th>
                  <th className="p-3 font-medium">Variables</th><th className="p-3 font-medium">Call</th><th className="p-3" /></tr></thead>
                <tbody>
                  {(recips.data?.items ?? []).map(r => (
                    <tr key={r.id} className="border-b border-line align-top last:border-0">
                      <td className="p-3 font-mono text-xs">{r.phone}</td>
                      <td className="p-3">{r.name ?? "—"}</td>
                      <td className="p-3"><Badge tone={R_TONE[r.status]}>{R_LABEL[r.status]}</Badge>
                        {r.error && <div className="mt-1 max-w-64 text-[11px] text-muted">{r.error}</div>}</td>
                      <td className="p-3 text-xs text-muted">{Object.entries(r.variables).map(([k, v]) => `${k}: ${v}`).join(" · ") || "—"}</td>
                      <td className="p-3 text-xs">{r.call_id ? <Link to={`/calls/${r.call_id}`} className="text-accent-text underline">{r.duration_s != null ? `${Math.round(r.duration_s)} s` : "Open"}</Link> : "—"}</td>
                      <td className="p-3 text-right">{r.status !== "in_progress" && <button aria-label={`Remove ${r.phone}`} onClick={() => drop.mutate(r.id)} className="rounded px-2 py-1 text-xs text-bad hover:bg-bad/10">Remove</button>}</td>
                    </tr>))}
                </tbody>
              </table>
            </div>
          )}
          {recips.data && <Pager page={page} pages={Math.max(1, Math.ceil(recips.data.total / PAGE))} onPage={setPage} />}
        </div>
      )}

      {dialogAction && (
        <Modal title={`${dialogAction.label} “${b.name}”?`} onClose={() => setDialog(null)}>
          <p className="text-sm">{dialogAction.confirm}</p>
          <div className="mt-3"><ErrorBox error={act.error} /></div>
          <div className="mt-4 flex justify-end gap-2">
            <Button kind="ghost" onClick={() => setDialog(null)}>Back</Button>
            <Button kind={dialogAction.danger ? "danger" : "primary"} onClick={() => act.mutate(dialogAction.action)} disabled={act.isPending}>{act.isPending ? "Working…" : dialogAction.label}</Button>
          </div>
        </Modal>
      )}
      {dialog?.kind === "rename" && (
        <Modal title="Rename batch call" onClose={() => setDialog(null)}>
          <form onSubmit={e => { e.preventDefault(); if (newName.trim()) rename.mutate(); }}>
            <input id="batch-rename" autoFocus maxLength={100} className="w-full" aria-label="Name" value={newName} onChange={e => setNewName(e.target.value)} />
            <div className="mt-3"><ErrorBox error={rename.error} /></div>
            <div className="mt-4 flex justify-end gap-2"><Button kind="ghost" onClick={() => setDialog(null)}>Cancel</Button>
              <Button kind="primary" type="submit" disabled={!newName.trim() || newName.trim() === b.name || rename.isPending}>Save</Button></div>
          </form>
        </Modal>
      )}
      {dialog?.kind === "delete" && (
        <Modal title="Delete batch call" onClose={() => setDialog(null)}>
          <p className="text-sm">Delete <b>{b.name}</b> and its {b.total.toLocaleString()} recipients? This can't be undone.{b.status === "running" && " Calls in progress will finish, but nothing more is placed."}</p>
          <div className="mt-3"><ErrorBox error={del.error} /></div>
          <div className="mt-4 flex justify-end gap-2"><Button kind="ghost" onClick={() => setDialog(null)}>Cancel</Button>
            <Button kind="danger" onClick={() => del.mutate()} disabled={del.isPending}>{del.isPending ? "Deleting…" : "Delete"}</Button></div>
        </Modal>
      )}
      {dialog?.kind === "add" && <AddRecipients id={id} onClose={() => setDialog(null)} />}
    </div>
  );
}
