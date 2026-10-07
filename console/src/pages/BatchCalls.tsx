import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type BatchCheck, type BatchConfig, type BatchRow, type BatchStatus } from "../api";
import { Badge, Button, Empty, ErrorBox, Modal, cx } from "../ui";

// Batch calls (Hamsa: Batch Calls): one agent calls a list of people from one number, on a schedule, inside a daily window.

export const BATCH_TONE: Record<BatchStatus, "neutral" | "good" | "warn" | "bad" | "info"> = {
  scheduled: "info", running: "good", paused: "warn", completed: "neutral", failed: "bad", cancelled: "neutral",
};
export const BATCH_LABEL: Record<BatchStatus, string> = {
  scheduled: "Scheduled", running: "Running", paused: "Paused", completed: "Completed", failed: "Failed", cancelled: "Cancelled",
};
// 0 = Monday … 6 = Sunday on the server; shown Sunday first like the Saudi week
export const DAYS: [number, string][] = [[6, "Sun"], [0, "Mon"], [1, "Tue"], [2, "Wed"], [3, "Thu"], [4, "Fri"], [5, "Sat"]];
const TEMPLATE = "phoneNumber,name,city\n+966500000001,Sara,Riyadh\n+966500000002,Omar,Jeddah\n";

export const fmtWhen = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—");

export function zones(): string[] {
  try { return (Intl as unknown as { supportedValuesOf: (k: string) => string[] }).supportedValuesOf("timeZone"); }
  catch { return ["Asia/Riyadh", "Asia/Dubai", "Europe/London", "UTC"]; }
}

export function describeWindow(c: BatchConfig) {
  const days = DAYS.filter(([d]) => c.days.includes(d)).map(([, l]) => l).join(", ");
  return `${c.window_start}–${c.window_end} ${c.timezone} · ${days}`;
}

const toBase64 = (buf: ArrayBuffer) => {
  let s = "";
  const b = new Uint8Array(buf);
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode(...b.subarray(i, i + 0x8000));
  return btoa(s);
};

/** The CSV step: upload, see what was accepted / rejected, fix rejected phone numbers, check again. */
export function RecipientsStep({ value, onChange }: { value: BatchCheck | null; onChange: (c: BatchCheck | null) => void }) {
  const [problem, setProblem] = useState<string | null>(null);
  const check = useMutation({
    mutationFn: (b: { csv_base64?: string; rows?: BatchRow[] }) => api.validateBatchRows(b),
    onSuccess: r => { setProblem(null); onChange(r); },
    onError: e => setProblem(e instanceof Error ? e.message : String(e)),
  });
  const pick = async (f: File | undefined) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".csv")) { setProblem("Choose a .csv file."); return; }
    if (f.size > 50 * 1024 * 1024) { setProblem("The file is larger than 50 MB."); return; }
    check.mutate({ csv_base64: toBase64(await f.arrayBuffer()) });
  };
  const edit = (i: number, phone: string) => value && onChange({ ...value, rejected: value.rejected.map((r, j) => (j === i ? { ...r, phone } : r)) });
  const drop = (i: number) => value && onChange({ ...value, rejected: value.rejected.filter((_, j) => j !== i) });
  const recheck = () => value && check.mutate({ rows: [...value.accepted, ...value.rejected].map(({ errors: _e, ...r }) => r) });
  const template = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([TEMPLATE], { type: "text/csv" }));
    a.download = "batch-calls-template.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="space-y-3">
      <label className="flex cursor-pointer flex-col items-center gap-1 rounded-lg border border-dashed border-line px-4 py-6 text-center hover:bg-soft/60">
        <span className="text-sm font-medium">{value ? "Choose another CSV file" : "Choose a CSV file"}</span>
        <span className="text-xs text-muted">Needs a <span className="font-mono">phoneNumber</span> column (7+ digits, E.164). Optional: <span className="font-mono">name</span>,
          <span className="font-mono"> ignoreE164Validation</span>; every other column becomes a variable for the agent. Max 50 MB.</span>
        <input id="batch-csv" type="file" accept=".csv,text/csv" className="sr-only" onChange={e => { pick(e.target.files?.[0]); e.target.value = ""; }} />
      </label>
      <div className="text-right"><button type="button" onClick={template} className="text-xs text-accent-text underline">Download template</button></div>
      {check.isPending && <p className="text-sm text-muted">Checking…</p>}
      {problem && <div className="rounded-lg bg-warn/10 p-2.5 text-xs text-warn">{problem}</div>}
      {value && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone="good">{value.accepted.length} accepted</Badge>
            <Badge tone={value.rejected.length ? "bad" : "neutral"}>{value.rejected.length} rejected</Badge>
            {value.duplicates > 0 && <Badge tone="warn">{value.duplicates} duplicate number{value.duplicates === 1 ? "" : "s"} (allowed)</Badge>}
            {value.variables.length > 0 && <span className="text-xs text-muted">Variables: {value.variables.join(", ")}</span>}
          </div>
          {value.rejected.length > 0 && (
            <div className="rounded-lg border border-line">
              <div className="border-b border-line px-3 py-1.5 text-xs text-muted">Fix the phone numbers or remove the rows, then check again.</div>
              <div className="max-h-56 divide-y divide-line overflow-auto">
                {value.rejected.map((r, i) => (
                  <div key={i} className="grid items-center gap-2 px-3 py-2 sm:grid-cols-[3rem_12rem_1fr_auto]">
                    <span className="text-xs text-muted">Row {r.row}</span>
                    <input aria-label={`Row ${r.row} phone number`} className="font-mono text-xs" value={r.phone} onChange={e => edit(i, e.target.value)} />
                    <span className="text-xs text-bad">{(r.errors ?? []).join(" ")}</span>
                    <button type="button" aria-label={`Remove row ${r.row}`} onClick={() => drop(i)} className="rounded px-2 py-1 text-xs text-bad hover:bg-bad/10">Remove</button>
                  </div>
                ))}
              </div>
              <div className="border-t border-line p-2 text-right"><Button onClick={recheck} disabled={check.isPending}>Check again</Button></div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CreateBatch({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const numbers = useQuery({ queryKey: ["outbound-numbers"], queryFn: api.outboundNumbers });
  const published = (agents.data ?? []).filter(a => a.version != null);
  const [name, setName] = useState("");
  const [agent, setAgent] = useState("");
  const [from, setFrom] = useState("");
  const [rows, setRows] = useState<BatchCheck | null>(null);
  const [cfg, setCfg] = useState<BatchConfig>({
    send_type: "now", scheduled_at: "", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Riyadh",
    window_start: "09:00", window_end: "18:00", days: [6, 0, 1, 2, 3],
  });
  const tz = useMemo(() => zones(), []);
  const agentId = agent || published[0]?.id || "";
  const fromNumber = from || numbers.data?.[0]?.number || "";
  const create = useMutation({
    mutationFn: () => api.createBatch({ name: name.trim(), agent_id: agentId, from_number: fromNumber,
      rows: (rows?.accepted ?? []).map(({ errors: _e, row: _r, ...r }) => r),
      config: { ...cfg, scheduled_at: cfg.send_type === "schedule" ? cfg.scheduled_at : null } }),
    onSuccess: r => { qc.invalidateQueries({ queryKey: ["batch-calls"] }); onClose(); nav(`/batch-calls/${r.id}`); },
  });
  const day = (d: number) => setCfg({ ...cfg, days: cfg.days.includes(d) ? cfg.days.filter(x => x !== d) : [...cfg.days, d] });
  const ready = name.trim() && agentId && fromNumber && rows && rows.accepted.length > 0 && rows.rejected.length === 0 && cfg.days.length > 0
    && (cfg.send_type === "now" || cfg.scheduled_at);
  return (
    <Modal title="Create batch call" sub="One agent calls everyone in your list from one number." onClose={onClose} wide="xl">
      <form className="space-y-4" onSubmit={e => { e.preventDefault(); if (ready) create.mutate(); }}>
        <label className="block"><div className="mb-1 text-sm font-medium">Batch call name</div>
          <input id="batch-name" autoFocus maxLength={100} className="w-full" placeholder="e.g. October appointment reminders" value={name} onChange={e => setName(e.target.value)} /></label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block"><div className="mb-1 text-sm font-medium">From number</div>
            <select id="batch-from" className="w-full" value={fromNumber} onChange={e => setFrom(e.target.value)}>
              {(numbers.data ?? []).map(n => <option key={n.number} value={n.number}>{n.number}{n.label ? ` — ${n.label}` : ""}</option>)}</select>
            {numbers.data && !numbers.data.length && <span className="mt-1 block text-[11px] text-warn">No outbound number yet — add one under <Link to="/numbers" className="underline">Phone numbers</Link>.</span>}
            <span className="mt-1 block text-[11px] text-muted">Can't be changed later.</span></label>
          <label className="block"><div className="mb-1 text-sm font-medium">Voice agent</div>
            <select id="batch-agent" className="w-full" value={agentId} onChange={e => setAgent(e.target.value)}>
              {published.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
            <span className="mt-1 block text-[11px] text-muted">Published agents only; calls use the published version. Can't be changed later.</span></label>
        </div>

        <div><div className="mb-1 text-sm font-medium">Recipients</div><RecipientsStep value={rows} onChange={setRows} /></div>

        <div className="space-y-3 rounded-lg border border-line p-3">
          <div className="text-sm font-medium">Schedule</div>
          <div className="flex flex-wrap gap-4 text-sm">
            {([["now", "Send now"], ["schedule", "Schedule for later"]] as const).map(([k, l]) => (
              <label key={k} className="flex items-center gap-1.5"><input type="radio" name="send" checked={cfg.send_type === k} onChange={() => setCfg({ ...cfg, send_type: k })} />{l}</label>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {cfg.send_type === "schedule" && <label className="block"><div className="mb-1 text-xs text-muted">Start date and time</div>
              <input id="batch-start" type="datetime-local" className="w-full" value={cfg.scheduled_at ?? ""} onChange={e => setCfg({ ...cfg, scheduled_at: e.target.value })} /></label>}
            <label className="block"><div className="mb-1 text-xs text-muted">Timezone</div>
              <select id="batch-tz" className="w-full" value={cfg.timezone} onChange={e => setCfg({ ...cfg, timezone: e.target.value })}>
                {(tz.includes(cfg.timezone) ? tz : [cfg.timezone, ...tz]).map(z => <option key={z}>{z}</option>)}</select></label>
          </div>
          <div>
            <div className="mb-1 text-sm font-medium">When calls can run</div>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <label className="flex items-center gap-1.5">From <input id="batch-from-time" type="time" value={cfg.window_start} onChange={e => setCfg({ ...cfg, window_start: e.target.value })} /></label>
              <label className="flex items-center gap-1.5">to <input id="batch-to-time" type="time" value={cfg.window_end} onChange={e => setCfg({ ...cfg, window_end: e.target.value })} /></label>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {DAYS.map(([d, l]) => (
                <button key={d} type="button" aria-pressed={cfg.days.includes(d)} onClick={() => day(d)}
                  className={cx("rounded-lg border px-2.5 py-1 text-xs", cfg.days.includes(d) ? "border-accent bg-accent/15 text-accent-text" : "border-line text-muted hover:bg-soft")}>{l}</button>
              ))}
            </div>
            <p className="mt-1 text-[11px] text-muted">Calls outside this window wait for the next one; nothing is skipped. Choose times that suit your recipients.</p>
          </div>
        </div>

        <ErrorBox error={create.error} />
        <div className="flex justify-end gap-2">
          <Button kind="ghost" onClick={onClose}>Cancel</Button>
          <Button kind="primary" type="submit" disabled={!ready || create.isPending}>{create.isPending ? "Creating…" : "Create batch call"}</Button>
        </div>
      </form>
    </Modal>
  );
}

export default function BatchCalls() {
  const q = useQuery({ queryKey: ["batch-calls"], queryFn: api.batchCalls,
    refetchInterval: d => (d.state.data?.some(b => b.status === "running" || b.status === "scheduled") ? 5000 : false) });
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [creating, setCreating] = useState(false);
  const shown = (q.data ?? []).filter(b => (!status || b.status === status) && b.name.toLowerCase().includes(search.toLowerCase()));
  return (
    <div className="mx-auto max-w-6xl space-y-4 pb-10">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line pb-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Batch calls</h1>
          <p className="mt-1 text-sm text-muted">Call a list of people with one agent — now or on a schedule, inside the hours you allow.</p>
        </div>
        <Button kind="primary" onClick={() => setCreating(true)}>+ Create batch call</Button>
      </div>
      <div className="flex flex-wrap gap-2">
        <input id="batch-search" aria-label="Search batch calls" className="w-64" placeholder="Search by name…" value={search} onChange={e => setSearch(e.target.value)} />
        <select id="batch-status" aria-label="Status" value={status} onChange={e => setStatus(e.target.value)}>
          <option value="">All statuses</option>{Object.entries(BATCH_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      </div>
      <ErrorBox error={q.error} />
      {q.data && !shown.length ? (
        <Empty>{q.data.length ? "No batch calls match." : "No batch calls yet. Create one to start calling a list of people."}</Empty>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line bg-panel">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line text-left text-xs text-muted">
              <th className="p-3 font-medium">Name</th><th className="p-3 font-medium">Agent</th><th className="p-3 font-medium">From</th>
              <th className="p-3 font-medium">Recipients</th><th className="p-3 font-medium">Progress</th><th className="p-3 font-medium">Status</th>
              <th className="p-3 font-medium">Created</th></tr></thead>
            <tbody>
              {shown.map(b => (
                <tr key={b.id} className="border-b border-line last:border-0 hover:bg-soft/60">
                  <td className="p-3"><Link to={`/batch-calls/${b.id}`} className="font-medium hover:underline">{b.name}</Link></td>
                  <td className="p-3 text-muted">{b.agent_name}</td>
                  <td className="p-3 font-mono text-xs">{b.from_number}</td>
                  <td className="p-3 tabular-nums">{b.total.toLocaleString()}</td>
                  <td className="p-3"><div className="flex items-center gap-2"><div className="h-1.5 w-24 rounded bg-soft"><div className="h-1.5 rounded bg-accent" style={{ width: `${b.progress}%` }} /></div><span className="text-xs text-muted">{b.progress}%</span></div></td>
                  <td className="p-3"><Badge tone={BATCH_TONE[b.status]}>{BATCH_LABEL[b.status]}</Badge></td>
                  <td className="p-3 whitespace-nowrap text-muted">{fmtWhen(b.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && <CreateBatch onClose={() => setCreating(false)} />}
    </div>
  );
}
