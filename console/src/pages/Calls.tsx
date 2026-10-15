import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import AgentPicker from "../AgentPicker";
import { api, type CallRow, type CallStatus } from "../api";
import { RangePicker, presetLabel, rangeOf, type Preset, PRESETS } from "../DateRange";
import { CI, CopyButton, MultiSelect, Pager, RowMenu, ViewMenu, loadColumns, saveColumns } from "../table";
import { ErrorBox, cx, fmtMs } from "../ui";
import CallPanel from "./CallPanel";

const PAGE = 25;
const MAX_EXPORT = 10_000;
const DUR_OPS: [string, string][] = [["between", "is between"], ["gt", "is greater than"], ["lt", "is less than"], ["eq", "is equal to"]];

/** `dur=between:10:60` → the filter; null when absent or malformed. */
function parseDur(v: string | null) {
  const [op, a, b] = (v ?? "").split(":");
  if (!DUR_OPS.some(o => o[0] === op) || a === undefined || a === "" || isNaN(+a) || (op === "between" && (b === undefined || b === "" || isNaN(+b)))) return null;
  return { op, a: +a, b: op === "between" ? +b : undefined };
}

function DurationFilter({ value, onChange }: { value: string | null; onChange: (v: string | null) => void }) {
  const cur = parseDur(value);
  const [op, setOp] = useState(cur?.op ?? "gt");
  const [a, setA] = useState(cur ? String(cur.a) : "");
  const [b, setB] = useState(cur?.b != null ? String(cur.b) : "");
  const ok = a !== "" && +a >= 0 && (op !== "between" || (b !== "" && +b >= +a));
  const label = cur ? `Duration ${DUR_OPS.find(o => o[0] === cur.op)?.[1].replace("is ", "")} ${cur.a}${cur.b != null ? `–${cur.b}` : ""} s` : "Duration";
  return (
    <details className="relative" id="calls-duration">
      <summary className={cx("flex cursor-pointer list-none items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm hover:bg-soft", cur ? "border-accent bg-accent/10" : "border-line")}>{label}</summary>
      <div className="absolute z-20 mt-1 w-72 space-y-2 rounded-xl border border-line bg-panel p-3 shadow-lg">
        <div className="text-xs text-muted">Call length in seconds</div>
        <select aria-label="Condition" className="w-full text-sm" value={op} onChange={e => setOp(e.target.value)}>{DUR_OPS.map(o => <option key={o[0]} value={o[0]}>{o[1]}</option>)}</select>
        <div className="flex items-center gap-2">
          <input aria-label="Seconds" type="number" min={0} className="w-24 text-sm" value={a} onChange={e => setA(e.target.value)} />
          {op === "between" && <><span className="text-xs text-muted">and</span><input aria-label="Up to seconds" type="number" min={0} className="w-24 text-sm" value={b} onChange={e => setB(e.target.value)} /></>}
        </div>
        <div className="flex justify-between">
          <button type="button" className="text-xs text-muted hover:text-ink" onClick={() => { setA(""); setB(""); onChange(null); }}>Clear</button>
          <button type="button" disabled={!ok} className="rounded-lg bg-accent px-3 py-1 text-xs font-medium text-accent-fg disabled:opacity-50"
            onClick={() => { onChange(`${op}:${a}${op === "between" ? `:${b}` : ""}`); (document.getElementById("calls-duration") as HTMLDetailsElement | null)?.removeAttribute("open"); }}>Apply</button>
        </div>
      </div>
    </details>
  );
}

const csvCell = (v: unknown) => {
  const s = v == null ? "" : String(v);
  return /^[=+\-@]/.test(s) && !/^\+?\d[\d ]*$/.test(s) ? `"'${s.replace(/"/g, '""')}"` : /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;   // no spreadsheet formulas
};

function ExportMenu({ rows, columns, agentName, fetchAll, busy }: {
  rows: CallRow[]; columns: Col[]; agentName: (id?: string | null) => string; fetchAll: () => Promise<CallRow[]>; busy: boolean;
}) {
  const [working, setWorking] = useState(false);
  const [note, setNote] = useState("");
  const cell = (c: CallRow, k: string): unknown => ({
    time: c.started_at, call: c.call_id, agent: agentName(c.agent_id), user: c.mobile, channel: CHANNELS.find(x => x[0] === c.channel)?.[1] ?? c.channel,
    duration: c.duration_s, status: STATUSES.find(x => x[0] === c.status)?.[1] ?? c.status,
    outcome: [c.booked && "Booked", c.verified && "Verified"].filter(Boolean).join(" + "), language: c.language, turns: c.turns,
    latency: c.latency_p50_ms, version: c.config_version,
  } as Record<string, unknown>)[k];
  const download = (items: CallRow[]) => {
    const lines = [columns.map(c => csvCell(c.label)).join(","), ...items.map(r => columns.map(c => csvCell(cell(r, c.key))).join(","))];
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" }));
    a.download = `call-history-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const all = async () => {
    setWorking(true); setNote("");
    try { const items = await fetchAll(); download(items); if (items.length >= MAX_EXPORT) setNote(`Stopped at ${MAX_EXPORT.toLocaleString()} calls — narrow the filters for the rest.`); }
    catch (e) { setNote(e instanceof Error ? e.message : String(e)); }
    finally { setWorking(false); }
  };
  return (
    <details className="relative">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-soft">Export CSV</summary>
      <div className="absolute right-0 z-20 mt-1 w-64 space-y-1 rounded-xl border border-line bg-panel p-2 shadow-lg">
        <button id="calls-export-page" type="button" disabled={busy || !rows.length} onClick={() => download(rows)} className="w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-soft disabled:opacity-50">
          Current page <span className="text-xs text-muted">({rows.length} calls)</span></button>
        <button id="calls-export-all" type="button" disabled={busy || working} onClick={all} className="w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-soft disabled:opacity-50">
          {working ? "Preparing…" : "All filtered results"}</button>
        <p className="px-3 pb-1 text-[11px] text-muted">The columns you have turned on. Phone numbers are exported as shown here.</p>
        {note && <p className="px-3 pb-1 text-[11px] text-warn">{note}</p>}
      </div>
    </details>
  );
}

export const fmtDuration = (s: number | null | undefined) =>
  s == null ? "—" : `${Math.floor(s / 60)}:${String(Math.max(0, s) % 60).padStart(2, "0")}`;

// ------------------------------------------------------------------ shared bits (also used by the details panel)

export const CHANNELS: [string, string, ReactNode][] = [["web", "Web", CI.globe], ["ivr", "Telephone", CI.phone], ["chat", "Chat Agent", CI.chat]];
export const STATUSES: [CallStatus, string, string][] = [
  ["in_progress", "In Progress", "bg-sky-500/15 text-sky-700 dark:text-sky-300"],
  ["completed", "Completed", "bg-good/15 text-good"],
  ["failed", "Failed", "bg-bad/15 text-bad"],
  ["forwarded", "Forwarded", "bg-warn/15 text-warn"],
  ["terminated", "Terminated", "bg-soft text-muted"],
];
const ALL_STATUSES = STATUSES.map(s => s[0]);

export function StatusBadge({ status }: { status?: CallStatus }) {
  const s = STATUSES.find(x => x[0] === status) ?? STATUSES[1];
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium", s[2])}>
      <span className={cx("h-1.5 w-1.5 rounded-full bg-current", status === "in_progress" && "animate-pulse")} />{s[1]}
    </span>
  );
}

export function ChannelBadge({ channel }: { channel: string | null }) {
  const c = CHANNELS.find(x => x[0] === channel) ?? CHANNELS[0];
  return <span className="inline-flex items-center gap-1.5 rounded-md border border-line bg-soft px-2 py-0.5 text-xs">{c[2]}{c[1]}</span>;
}

export const fmtWhen = (iso: string) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", year: "numeric",
  hour: "2-digit", minute: "2-digit", second: "2-digit" });

// ------------------------------------------------------------------ toolbar controls

type Col = { key: string; label: string; on: boolean; fixed?: boolean; sort?: "time" | "duration" };
const COLUMNS: Col[] = [
  { key: "time", label: "Time", on: true, fixed: true, sort: "time" },
  { key: "call", label: "Call ID", on: false },
  { key: "agent", label: "Agent", on: true },
  { key: "user", label: "User Number", on: true },
  { key: "channel", label: "Channel", on: true },
  { key: "duration", label: "Duration", on: true, sort: "duration" },
  { key: "status", label: "Status", on: true },
  { key: "outcome", label: "Outcome", on: true },
  { key: "language", label: "Language", on: false },
  { key: "turns", label: "Turns", on: false },
  { key: "latency", label: "Latency p50", on: false },
  { key: "version", label: "Agent version", on: false },
];
const COLS_KEY = "hmg.calls.columns.v1";
// ------------------------------------------------------------------ page

export default function Calls() {
  const { id: openId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const set = (patch: Record<string, string | null>, keepPage = false) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v); else next.delete(k);
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  };
  const q = params.get("q") ?? "";
  const [search, setSearch] = useState(q);
  useEffect(() => { setSearch(q); }, [q]);
  useEffect(() => {                                     // search as you type, without a request per key
    if (search.trim() === q) return;
    const t = setTimeout(() => set({ q: search.trim() || null }), 300);
    return () => clearTimeout(t);
  }, [search, q]);                                      // eslint-disable-line react-hooks/exhaustive-deps
  const channels = (params.get("channel") ?? "").split(",").filter(Boolean);
  const statusParam = params.get("status");
  const statuses = statusParam === null ? ALL_STATUSES : statusParam.split(",").filter(Boolean) as CallStatus[];
  const preset = (params.get("preset") === "all" || PRESETS.some(p => p[0] === params.get("preset")) ? params.get("preset") : "all") as Preset;
  const from = params.get("start"), to = params.get("end");
  const sort = params.get("sort") === "duration" ? "duration" : "time";
  const asc = params.get("dir") === "asc";
  const page = Math.max(0, Number(params.get("page") ?? 1) - 1) || 0;
  const agent = params.get("agent") ?? "";
  const dur = parseDur(params.get("dur"));
  const [cols, setCols] = useState(() => loadColumns(COLS_KEY, COLUMNS.filter(c => c.on).map(c => c.key)));
  const saveCols = (v: string[]) => { setCols(v); saveColumns(COLS_KEY, v); };
  const show = (k: string) => k === "time" || cols.includes(k);

  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents, staleTime: 30_000 });
  const agentName = (id?: string | null) => agents.data?.find(a => a.id === id)?.name ?? id ?? "—";
  const durationArgs = dur ? { duration_op: dur.op, duration_a: dur.a, duration_b: dur.b } : {};
  const filterArgs = () => {
    const r = preset === "all" ? null : rangeOf(preset, from, to);
    return { q: q || undefined, agent: agent || undefined, channels: channels.length ? channels.join(",") : undefined,
      status: statuses.length === ALL_STATUSES.length ? undefined : statuses.length ? statuses.join(",") : "none",
      start: r?.start.toISOString(), end: r?.end.toISOString(), sort, desc: !asc, ...durationArgs };
  };
  const fetchAll = async () => {
    const out: CallRow[] = [];
    while (out.length < MAX_EXPORT) {
      const r = await api.calls({ ...filterArgs(), limit: 200, offset: out.length });
      out.push(...r.items);
      if (out.length >= r.total || !r.items.length) break;
    }
    return out;
  };
  const calls = useQuery({
    queryKey: ["calls", q, channels.join(), statuses.join(), preset, from, to, sort, asc, page, agent, params.get("dur")],
    queryFn: () => {
      const r = preset === "all" ? null : rangeOf(preset, from, to);
      return api.calls({
        q: q || undefined, agent: agent || undefined, limit: PAGE, offset: page * PAGE, ...durationArgs,
        channels: channels.length ? channels.join(",") : undefined,
        status: statuses.length === ALL_STATUSES.length ? undefined : statuses.length ? statuses.join(",") : "none",
        start: r?.start.toISOString(), end: r?.end.toISOString(), sort, desc: !asc,
      });
    },
    refetchInterval: 15_000, placeholderData: prev => prev,
  });
  const total = calls.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const filtered = Boolean(q || channels.length || statusParam !== null || preset !== "all" || agent || dur);
  const open = (id: string) => navigate(`/calls/${encodeURIComponent(id)}${location.search}`);
  const sortBy = (k: "time" | "duration") => set(sort === k ? { sort: k === "time" ? null : k, dir: asc ? null : "asc" } : { sort: k === "time" ? null : k, dir: null });

  const cell = "px-4 py-3";
  return (
    <div className={cx("mx-auto max-w-6xl space-y-5 pb-10", openId && "md:mr-[22.5rem] md:max-w-none")}>      {/* the open call docks on the right: the list makes room */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Call History</h1>
        <p className="mt-1 text-sm text-muted">Every call and chat of this project: what was said, how it ended and how fast the agent answered.</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted">{CI.search}</span>
          <input id="calls-search" value={search} onChange={e => setSearch(e.target.value)} style={{ paddingLeft: "2rem" }}
            placeholder="Search by Call ID, User Number, or Agent ID" className="w-80 max-w-full !py-1.5 text-sm" />
        </div>
        <MultiSelect id="calls-channel" label="Channel" options={CHANNELS} value={channels} onChange={v => set({ channel: v.join(",") || null })} />
        <MultiSelect id="calls-status" label="Status" options={STATUSES.map(s => [s[0], s[1]])} value={statuses}
          onChange={v => set({ status: v.length === ALL_STATUSES.length ? null : v.join(",") || "none" })} />
        <AgentPicker value={agent} onChange={v => set({ agent: v || null })} />
        <DurationFilter key={params.get("dur") ?? "none"} value={params.get("dur")} onChange={v => set({ dur: v })} />
        {filtered && <button id="calls-reset" onClick={() => { setSearch(""); setParams(new URLSearchParams(), { replace: true }); }}
          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-soft hover:text-ink">{CI.reset}Reset</button>}
        <div className="ml-auto flex items-center gap-2">
          <RangePicker withAll preset={preset} from={from} to={to}
            onChange={(p, a, b) => set({ preset: p === "all" ? null : p, start: p === "custom" ? a ?? null : null, end: p === "custom" ? b ?? null : null })} />
          <ExportMenu rows={calls.data?.items ?? []} columns={COLUMNS.filter(c => show(c.key))} agentName={agentName} fetchAll={fetchAll} busy={calls.isLoading} />
          <ViewMenu id="calls-view" columns={COLUMNS} shown={cols} onChange={saveCols} />
        </div>
      </div>

      <ErrorBox error={calls.error} />
      <div className="overflow-x-auto rounded-xl border border-line bg-panel">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              {COLUMNS.filter(c => show(c.key)).map(c => (
                <th key={c.key} className={cx(cell, "font-medium whitespace-nowrap")}>
                  {c.sort ? (
                    <button onClick={() => sortBy(c.sort!)} className="inline-flex items-center gap-1 hover:text-ink">
                      {c.label}{sort === c.sort ? (asc ? CI.up : CI.down) : <span className="opacity-50">{CI.sort}</span>}
                    </button>
                  ) : c.label}
                </th>
              ))}
              <th className={cell} />
            </tr>
          </thead>
          <tbody>
            {(calls.data?.items ?? []).map((c: CallRow) => (
              <tr key={c.call_id} onClick={() => open(c.call_id)}
                className={cx("group cursor-pointer border-b border-line last:border-0 hover:bg-soft/60",
                  openId === c.call_id && "bg-accent/5 shadow-[inset_3px_0_0_var(--color-accent)]")}>
                <td className={cx(cell, "whitespace-nowrap text-xs")}>{fmtWhen(c.started_at)}</td>
                {show("call") && <td className={cx(cell, "font-mono text-xs")}>{c.call_id}</td>}
                {show("agent") && <td className={cx(cell, "whitespace-nowrap")}>
                  <span className="inline-flex items-center gap-1">{agentName(c.agent_id)}
                    {c.agent_id && <span className="opacity-0 group-hover:opacity-100"><CopyButton text={c.agent_id} label="Copy agent ID" /></span>}</span>
                </td>}
                {show("user") && <td className={cx(cell, "font-mono text-xs tabular-nums")}>{c.mobile ?? "—"}</td>}
                {show("channel") && <td className={cx(cell, "whitespace-nowrap")}><ChannelBadge channel={c.channel} /></td>}
                {show("duration") && <td className={cx(cell, "tabular-nums")}>{fmtDuration(c.duration_s)}</td>}
                {show("status") && <td className={cell}><StatusBadge status={c.status} /></td>}
                {show("outcome") && <td className={cell}>
                  <span className="flex flex-wrap gap-1 text-xs">
                    {c.booked && <span className="rounded bg-accent/25 px-1.5 py-0.5 font-medium text-accent-text">Booked</span>}
                    {c.verified && <span className="rounded bg-soft px-1.5 py-0.5">Verified</span>}
                    {!c.booked && !c.verified && <span className="text-muted">—</span>}
                  </span>
                </td>}
                {show("language") && <td className={cx(cell, "text-xs")}>{c.language === "ar" ? "Arabic" : c.language === "en" ? "English" : c.language ?? "—"}</td>}
                {show("turns") && <td className={cx(cell, "tabular-nums")}>{c.turns}</td>}
                {show("latency") && <td className={cx(cell, "tabular-nums text-xs")}>{fmtMs(c.latency_p50_ms)}</td>}
                {show("version") && <td className={cx(cell, "text-xs text-muted")}>{c.config_version ? `v${c.config_version}` : "—"}</td>}
                <td className={cx(cell, "w-10 text-right")}><RowMenu items={[{ label: "View Details", icon: CI.eye, onClick: () => open(c.call_id) }]} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!calls.data?.items.length && (
          <div className="py-16 text-center text-sm text-muted">
            {calls.isLoading ? "Loading…" : filtered ? "No calls match these filters." : "No calls yet."}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted">
        <span>{total ? `Showing ${page * PAGE + 1}–${Math.min(total, (page + 1) * PAGE)} of ${total} calls` : ""}
          {preset !== "all" && total ? ` · ${presetLabel(preset, from, to)}` : ""}</span>
        {pages > 1 && <Pager page={page} pages={pages} onPage={p => set({ page: p ? String(p + 1) : null }, true)} />}
      </div>

      {openId && <CallPanel id={openId} onClose={() => navigate(`/calls${location.search}`)} agentName={agentName} />}
    </div>
  );
}
