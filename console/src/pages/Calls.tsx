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
  const [cols, setCols] = useState(() => loadColumns(COLS_KEY, COLUMNS.filter(c => c.on).map(c => c.key)));
  const saveCols = (v: string[]) => { setCols(v); saveColumns(COLS_KEY, v); };
  const show = (k: string) => k === "time" || cols.includes(k);

  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents, staleTime: 30_000 });
  const agentName = (id?: string | null) => agents.data?.find(a => a.id === id)?.name ?? id ?? "—";
  const calls = useQuery({
    queryKey: ["calls", q, channels.join(), statuses.join(), preset, from, to, sort, asc, page, agent],
    queryFn: () => {
      const r = preset === "all" ? null : rangeOf(preset, from, to);
      return api.calls({
        q: q || undefined, agent: agent || undefined, limit: PAGE, offset: page * PAGE,
        channels: channels.length ? channels.join(",") : undefined,
        status: statuses.length === ALL_STATUSES.length ? undefined : statuses.length ? statuses.join(",") : "none",
        start: r?.start.toISOString(), end: r?.end.toISOString(), sort, desc: !asc,
      });
    },
    refetchInterval: 15_000, placeholderData: prev => prev,
  });
  const total = calls.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const filtered = Boolean(q || channels.length || statusParam !== null || preset !== "all" || agent);
  const open = (id: string) => navigate(`/calls/${encodeURIComponent(id)}${location.search}`);
  const sortBy = (k: "time" | "duration") => set(sort === k ? { sort: k === "time" ? null : k, dir: asc ? null : "asc" } : { sort: k === "time" ? null : k, dir: null });

  const cell = "px-4 py-3";
  return (
    <div className="mx-auto max-w-6xl space-y-5 pb-10">
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
        {filtered && <button id="calls-reset" onClick={() => { setSearch(""); setParams(new URLSearchParams(), { replace: true }); }}
          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-soft hover:text-ink">{CI.reset}Reset</button>}
        <div className="ml-auto flex items-center gap-2">
          <RangePicker withAll preset={preset} from={from} to={to}
            onChange={(p, a, b) => set({ preset: p === "all" ? null : p, start: p === "custom" ? a ?? null : null, end: p === "custom" ? b ?? null : null })} />
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
