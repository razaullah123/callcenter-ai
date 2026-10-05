import { useQuery } from "@tanstack/react-query";
import { type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import AgentPicker from "../AgentPicker";
import { PRESETS, RangePicker, addDays, midnight, presetLabel, rangeOf, ymd, type Preset } from "../DateRange";
import { api, type Count, type DashboardData } from "../api";
import { useLive } from "../live";
import { ErrorBox, cx, fmtMs } from "../ui";

const TABS = [["overview", "Overview"], ["performance", "Performance"], ["outcome", "Satisfaction & Outcome"],
  ["live", "Live calls"]] as const;
type Tab = typeof TABS[number][0];

// ------------------------------------------------------------------ formatting

function fmtDur(s: number | null | undefined) {
  if (s == null || !isFinite(s)) return "0s";
  const t = Math.round(s);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
}
const pct = (n: number, d: number) => (d ? `${Math.round((100 * n) / d)}%` : "0%");
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const LANG: Record<string, string> = { ar: "Arabic", en: "English", unknown: "Unknown" };
const CHANNEL: Record<string, string> = { web: "Web call", ivr: "Telephone", chat: "Chat" };

// ------------------------------------------------------------------ icons (lucide-style strokes)

const ico = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round">{d}</svg>
);
const I = {
  pulse: ico(<path d="M22 12h-4l-3 9L9 3l-3 9H2" />),
  hash: ico(<><path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18" /></>),
  clock: ico(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  timer: ico(<><circle cx="12" cy="14" r="8" /><path d="M10 2h4M12 14l3-3" /></>),
  bars: ico(<path d="M3 3v18h18M8 17V11M13 17V7M18 17v-4" />),
  doc: ico(<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></>),
  userCheck: ico(<><circle cx="9" cy="8" r="4" /><path d="M2 21a7 7 0 0 1 14 0M16 11l2 2 4-4" /></>),
  gauge: ico(<><path d="M12 14l4-4" /><path d="M3.3 19a10 10 0 1 1 17.4 0" /></>),
  heart: ico(<path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7z" />),
  phone: ico(<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z" />),
  download: ico(<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 10l5 5 5-5M12 15V3" /></>),
};

// ------------------------------------------------------------------ building blocks

function Section({ icon, title, children, aside }: { icon: ReactNode; title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="space-y-4">
      <div className="flex items-center gap-3">
        <span className="text-ink">{icon}</span>
        <h2 className="shrink-0 text-lg font-semibold">{title}</h2>
        <div className="h-px flex-1 bg-gradient-to-r from-line to-transparent" />
        {aside}
      </div>
      {children}
    </section>
  );
}

function Metric({ label, value, hint, icon, tone }: {
  label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode; tone?: "good" | "warn" | "bad";
}) {
  return (
    <div className="rounded-xl border border-line bg-panel p-5 transition-shadow hover:shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm font-medium">{label}</div>
        {icon && <span className="-mt-1 grid h-11 w-11 shrink-0 place-items-center rounded-full bg-soft text-ink">{icon}</span>}
      </div>
      <div className={cx("mt-2 text-2xl font-bold tabular-nums", tone === "good" && "text-good", tone === "warn" && "text-warn",
        tone === "bad" && "text-bad")}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

function ListCard({ icon, title, rows }: { icon: ReactNode; title: string; rows: [ReactNode, ReactNode][] }) {
  return (
    <div className="rounded-xl border border-line bg-panel p-5">
      <div className="mb-4 flex items-center gap-3">
        <span className="grid h-8 w-8 place-items-center rounded-md bg-soft">{icon}</span>
        <div className="font-semibold">{title}</div>
      </div>
      <div className="space-y-2.5">
        {rows.map(([k, v], i) => (
          <div key={i} className="flex items-center justify-between rounded-lg bg-soft px-3 py-2.5 text-sm">
            <span className="text-muted">{k}</span><span className="font-semibold tabular-nums">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ChartCard({ title, sub, children, className }: { title: string; sub: string; children: ReactNode; className?: string }) {
  return (
    <div className={cx("rounded-xl border border-line bg-panel p-5", className)}>
      <div className="font-semibold">{title}</div>
      <div className="mt-0.5 text-sm text-muted">{sub}</div>
      <div className="mt-4 h-72">{children}</div>
    </div>
  );
}

function NoData() {
  return (
    <div className="grid h-full place-items-center text-center">
      <div>
        <div className="font-medium">No Data Available</div>
        <div className="mt-1 text-sm text-muted">No data available for the selected time period. Try adjusting your filters or date range.</div>
      </div>
    </div>
  );
}

const tooltipStyle = { background: "var(--color-panel)", border: "1px solid var(--color-line)", borderRadius: 8, fontSize: 12 };

function Donut({ data }: { data: { name: string; value: number; color: string }[] }) {
  if (!data.some(d => d.value > 0)) return <NoData />;
  return (
    <ResponsiveContainer>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius="48%" outerRadius="78%" paddingAngle={data.filter(d => d.value).length > 1 ? 1 : 0}
          stroke="none">
          {data.map(d => <Cell key={d.name} fill={d.color} />)}
        </Pie>
        <Tooltip contentStyle={tooltipStyle} />
        <Legend content={() => (
          <ul className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-muted">
            {data.map(d => <li key={d.name} className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: d.color }} />{d.name}</li>)}
          </ul>
        )} />
      </PieChart>
    </ResponsiveContainer>
  );
}

// ------------------------------------------------------------------ tabs

/** Every bucket of the window (hours or days) up to now, so empty periods show as 0 instead of being skipped. */
function timeline(d: DashboardData, start: Date, end: Date) {
  const hourly = d.granularity === "hour";
  const by = new Map(d.series.map(r => [r.t.replace(" ", "T").slice(0, hourly ? 13 : 10), r]));
  const stop = Math.min(end.getTime(), Date.now());
  const out: { label: string; calls: number; booked: number; handoffs: number }[] = [];
  let t = hourly ? new Date(start.getFullYear(), start.getMonth(), start.getDate(), start.getHours()) : midnight(start);
  for (let i = 0; t.getTime() < stop && i < 400; i++) {
    const key = hourly ? `${ymd(t)}T${String(t.getHours()).padStart(2, "0")}` : ymd(t);
    const r = by.get(key);
    out.push({ label: hourly ? `${String(t.getHours()).padStart(2, "0")}:00` : t.toLocaleDateString([], { month: "short", day: "numeric" }),
               calls: r?.calls ?? 0, booked: r?.booked ?? 0, handoffs: r?.handoffs ?? 0 });
    t = hourly ? new Date(t.getTime() + 3_600_000) : addDays(t, 1);
  }
  return out;
}

const axis = { tick: { fontSize: 11, fill: "var(--color-muted)" }, tickLine: false, axisLine: false };

function Overview({ d, start, end }: { d: DashboardData; start: Date; end: Date }) {
  const t = d.totals;
  const series = timeline(d, start, end);
  return (
    <div className="space-y-8">
      <Section icon={I.bars} title="Overall Information">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Metric label="Live Sessions" value={d.live_calls} hint="Concurrent calls" icon={I.pulse} />
          <Metric label="Total Sessions" value={t.calls} hint="In selected window" icon={I.hash} />
          <Metric label="Avg Session Duration" value={fmtDur(t.avg_duration_s)} hint="Per session" icon={I.clock} />
          <Metric label="Total Session Duration" value={fmtDur(t.total_duration_s)} hint="Cumulative time" icon={I.timer} />
        </div>
      </Section>
      <Section icon={I.hash} title="Additional Metrics">
        <div className="grid gap-4 md:grid-cols-3">
          <ListCard icon={I.bars} title="Calls by Duration Bucket"
            rows={[["Less than 30s", t.lt30], ["30-120s", t.s30_120], ["More than 120s", t.gt120]]} />
          <ListCard icon={I.doc} title="Avg Words per AI Response"
            rows={[["Per response", Math.round(t.avg_words_per_reply ?? 0)], ["Turns per call", (t.avg_turns ?? 0).toFixed(1)]]} />
          <ListCard icon={I.userCheck} title="Forwarded to Human Agent"
            rows={[["of total", pct(t.handoffs, t.calls)], ["Calls", t.handoffs]]} />
        </div>
      </Section>
      <Section icon={I.doc} title="Charts">
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title="Calls by Duration Bucket" sub="Distribution of call durations">
            <Donut data={[{ name: "Less than 30s", value: t.lt30, color: "#f59e0b" },
                          { name: "30-120s", value: t.s30_120, color: "#2563eb" },
                          { name: "More than 120s", value: t.gt120, color: "#a3a3a3" }]} />
          </ChartCard>
          <ChartCard title={`Calls Over Time (${d.granularity === "hour" ? "Hourly" : "Daily"})`}
            sub={`Total sessions by ${d.granularity}`}>
            {t.calls ? (
              <ResponsiveContainer>
                <AreaChart data={series} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <defs>
                    <linearGradient id="calls-fill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--color-accent)" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="var(--color-accent)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} stroke="var(--color-line)" />
                  <XAxis dataKey="label" {...axis} minTickGap={16} />
                  <YAxis allowDecimals={false} {...axis} width={36} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Area type="monotone" dataKey="calls" name="Calls" stroke="var(--color-accent)" strokeWidth={2} fill="url(#calls-fill)" />
                  <Area type="monotone" dataKey="booked" name="Booked" stroke="#2563eb" strokeWidth={1.5} fill="none" />
                </AreaChart>
              </ResponsiveContainer>
            ) : <NoData />}
          </ChartCard>
        </div>
      </Section>
    </div>
  );
}

function Performance({ d }: { d: DashboardData }) {
  const p = d.performance, t = d.totals;
  const bars = [["Speech-to-text", p.stt_ms], ["LLM first token", p.llm_ms], ["Tool calls", p.tool_ms],
    ["Speech generation", p.tts_ms], ["Latency", p.latency_ms]].map(([name, ms]) => ({ name: name as string, ms: Math.round((ms as number | null) ?? 0) }));
  return (
    <div className="space-y-8">
      <Section icon={I.gauge} title="Performance Metrics">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          <Metric label="ASR Processing Time" value={fmtMs(p.stt_ms)} hint="Speech-to-text, average per utterance" />
          <Metric label="NLU/LLM Response Time" value={fmtMs(p.llm_ms)} hint="Average time to the first token" />
          <Metric label="TTS Generation Time" value={fmtMs(p.tts_ms)} hint="LLM first token → first audio" />
          <Metric label="Latency" value={fmtMs(p.latency_ms)} hint={<>Turn end → first audio · p50 {fmtMs(p.latency_p50_ms)} · p95 {fmtMs(p.latency_p95_ms)}</>}
            tone={(p.latency_ms ?? 0) > 2500 ? "warn" : undefined} />
          <Metric label="Error Rate" value={pct(t.errors, t.calls)} hint={`Sessions with errors (${t.errors})`}
            tone={t.calls && t.errors / t.calls > 0.05 ? "bad" : undefined} />
        </div>
      </Section>
      <Section icon={I.bars} title="Performance Charts">
        <ChartCard title="Processing Times" sub="Average processing times for system components">
          {bars.some(b => b.ms) ? (
            <ResponsiveContainer>
              <BarChart data={bars} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--color-line)" strokeDasharray="3 3" />
                <XAxis dataKey="name" {...axis} />
                <YAxis {...axis} width={56} tickFormatter={v => `${v}ms`} />
                <Tooltip contentStyle={tooltipStyle} formatter={v => [`${v} ms`, "Average"]} cursor={{ fill: "var(--color-soft)" }} />
                <Bar dataKey="ms" fill="var(--color-accent)" maxBarSize={220} />
              </BarChart>
            </ResponsiveContainer>
          ) : <NoData />}
        </ChartCard>
        <p className="text-xs text-muted">Tool calls are the HIS lookups and bookings; the caller also waits for the end-of-turn
          silence (default 550 ms) before the agent starts. Per-turn details are in each call's page.</p>
      </Section>
    </div>
  );
}

function Breakdown({ title, rows, label }: { title: string; rows: Count[]; label?: (k: string) => string }) {
  const total = rows.reduce((n, r) => n + r.calls, 0);
  return (
    <div className="rounded-xl border border-line bg-panel p-5">
      <div className="mb-3 font-semibold">{title}</div>
      {rows.length ? (
        <ul className="space-y-2.5">
          {rows.slice(0, 6).map(r => (
            <li key={r.key}>
              <div className="flex justify-between gap-3 text-sm"><span className="truncate" title={r.key}>{label ? label(r.key) : r.key}</span>
                <span className="shrink-0 tabular-nums text-muted">{r.calls} · {pct(r.calls, total)}</span></div>
              <div className="mt-1 h-1.5 rounded bg-soft"><div className="h-1.5 rounded bg-accent" style={{ width: pct(r.calls, total) }} /></div>
            </li>
          ))}
        </ul>
      ) : <div className="text-sm text-muted">No calls in this window.</div>}
    </div>
  );
}

function Outcome({ d }: { d: DashboardData }) {
  const t = d.totals;
  const dist = [
    { name: "Booked", value: t.booked, color: "#97de00" },
    { name: "Verified only", value: t.verified_only, color: "#2563eb" },
    { name: "Handed to human", value: t.handoffs, color: "#f59e0b" },
    { name: "Not resolved", value: t.unresolved, color: "#a3a3a3" },
  ];
  return (
    <div className="space-y-8">
      <Section icon={I.heart} title="Outcome Metrics">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          <Metric label="Booking Rate" value={pct(t.booked, t.calls)} hint={`${plural(t.booked, "appointment", "appointments")} booked`} tone={t.booked ? "good" : undefined} />
          <Metric label="Verification Rate" value={pct(t.verified, t.calls)} hint={`${plural(t.verified, "caller", "callers")} verified`} />
          <Metric label="First-Call Resolution (FCR)" value={pct(t.calls - t.handoffs, t.calls)} hint="Resolved without hand-off" />
          <Metric label="Escalation Rate" value={pct(t.handoffs, t.calls)} hint="Calls escalated to human agents"
            tone={t.calls && t.handoffs / t.calls > 0.25 ? "warn" : undefined} />
          <div className="rounded-xl border border-line bg-panel p-5">
            <div className="mb-2 text-sm font-medium">Outcome Distribution</div>
            {dist.map(x => (
              <div key={x.name} className="flex justify-between py-0.5 text-sm">
                <span>{x.name}</span><span className="font-semibold tabular-nums">{pct(x.value, t.calls)}</span>
              </div>
            ))}
          </div>
        </div>
        <p className="text-xs text-muted">Caller satisfaction (CSAT, NPS, sentiment) needs a post-call analysis step, which isn't built yet
          (PLAN 12.9). Until then these come from what the agent actually did on each call.</p>
      </Section>
      <Section icon={I.bars} title="Outcome Analysis">
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title="Outcome Distribution" sub="Booked, verified only, handed to a human or not resolved">
            <Donut data={dist} />
          </ChartCard>
          <div className="grid gap-4 sm:grid-cols-2">
            <Breakdown title="Languages" rows={d.languages} label={k => LANG[k] ?? k} />
            <Breakdown title="Channels" rows={d.channels} label={k => CHANNEL[k] ?? k} />
            <Breakdown title="Hand-off reasons" rows={d.handoff_reasons} />
            <Breakdown title="Where calls ended" rows={d.skills} />
          </div>
        </div>
      </Section>
    </div>
  );
}

function LiveCalls({ agent }: { agent: string }) {
  const live = useLive();
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents, staleTime: 30_000 });
  const name = (id: string | null) => agents.data?.find(a => a.id === id)?.name ?? id ?? "—";
  const active = live.active.filter(c => !agent || c.agent_id === agent);
  return (
    <Section icon={I.phone} title="Live Calls">
      <div className="flex justify-end">
        <span className="flex items-center gap-2 text-sm text-muted">
          <span className={cx("h-2 w-2 rounded-full", live.connected ? "bg-good" : "bg-warn")} />
          {live.connected ? "Live monitoring & analytics" : "Reconnecting…"}
        </span>
      </div>
      {active.length ? (
        <div className="overflow-x-auto rounded-xl border border-line bg-panel">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line text-left text-xs text-muted">
              <th className="px-4 py-2.5 font-medium">Call</th><th className="px-4 py-2.5 font-medium">Agent</th>
              <th className="px-4 py-2.5 font-medium">Duration</th><th className="px-4 py-2.5 font-medium">Language</th>
              <th className="px-4 py-2.5 font-medium">Step</th><th className="px-4 py-2.5 font-medium">Caller said</th>
              <th className="px-4 py-2.5" />
            </tr></thead>
            <tbody>
              {active.map(c => (
                <tr key={c.call_id} className="border-b border-line last:border-0">
                  <td className="px-4 py-2.5 font-mono text-xs">{c.call_id}</td>
                  <td className="px-4 py-2.5">{name(c.agent_id)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{fmtDur(c.duration_s)}</td>
                  <td className="px-4 py-2.5">{LANG[c.language ?? ""] ?? c.language ?? "—"}</td>
                  <td className="px-4 py-2.5 text-xs">{c.step ?? c.skill ?? "—"}{c.verified && <span className="ml-1.5 rounded bg-good/15 px-1 text-good">verified</span>}</td>
                  <td className="max-w-xs truncate px-4 py-2.5 text-xs text-muted">{c.last_user ?? "—"}</td>
                  <td className="px-4 py-2.5 text-right"><Link to="/live" className="text-xs font-medium text-accent-text hover:underline">Watch →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="grid place-items-center py-16 text-center">
          <span className="grid h-14 w-14 place-items-center rounded-full bg-soft">{I.phone}</span>
          <div className="mt-3 font-semibold">No Live Calls</div>
          <div className="mt-1 text-sm text-muted">There are currently no live calls. New calls will appear here in live time.</div>
        </div>
      )}
    </Section>
  );
}

// ------------------------------------------------------------------ export

function exportCsv(d: DashboardData, label: string, agentName: string) {
  const t = d.totals, p = d.performance;
  const rows: (string | number | null)[][] = [
    ["Dashboard export", label], ["Agent", agentName], ["From", d.start], ["To", d.end], [],
    ["Metric", "Value"],
    ["Total sessions", t.calls], ["Avg session duration (s)", Math.round(t.avg_duration_s ?? 0)],
    ["Total session duration (s)", Math.round(t.total_duration_s)], ["Calls < 30s", t.lt30], ["Calls 30-120s", t.s30_120],
    ["Calls > 120s", t.gt120], ["Avg words per AI response", Math.round(t.avg_words_per_reply ?? 0)],
    ["Avg turns per call", (t.avg_turns ?? 0).toFixed(1)], ["Verified", t.verified], ["Booked", t.booked],
    ["Handed to human", t.handoffs], ["Sessions with errors", t.errors],
    ["ASR ms (avg)", Math.round(p.stt_ms ?? 0)], ["LLM first token ms (avg)", Math.round(p.llm_ms ?? 0)],
    ["Tool call ms (avg)", Math.round(p.tool_ms ?? 0)], ["TTS ms (avg)", Math.round(p.tts_ms ?? 0)],
    ["Latency ms (avg)", Math.round(p.latency_ms ?? 0)], ["Latency ms (p95)", Math.round(p.latency_p95_ms ?? 0)], [],
    [d.granularity === "hour" ? "Hour" : "Day", "Calls", "Booked", "Handoffs"],
    ...d.series.map(r => [r.t, r.calls, r.booked, r.handoffs]),
  ];
  const csv = rows.map(r => r.map(v => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([String.fromCharCode(0xfeff) + csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `dashboard-${d.start.slice(0, 10)}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ------------------------------------------------------------------ page

export default function Dashboard() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some(x => x[0] === params.get("tab")) ? params.get("tab") : "overview") as Tab;
  const preset = (PRESETS.some(x => x[0] === params.get("preset")) ? params.get("preset") : "today") as Preset;
  const from = params.get("start"), to = params.get("end");
  const agent = params.get("agent") ?? "";
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v); else next.delete(k);
    setParams(next, { replace: true });
  };
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents, staleTime: 30_000 });
  const range = rangeOf(preset, from, to);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const q = useQuery({
    queryKey: ["dashboard", preset, from, to, agent, tz],
    queryFn: () => {
      const r = rangeOf(preset, from, to);       // "last hour" moves with every refresh
      return api.dashboard({ start: r.start.toISOString(), end: r.end.toISOString(), agent: agent || undefined, tz });
    },
    refetchInterval: 15_000, enabled: tab !== "live",
  });
  const label = presetLabel(preset, from, to);

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-10">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-muted">Monitor your system activity and performance</p>
        </div>
        {tab !== "live" && (
          <button id="dashboard-export" disabled={!q.data}
            onClick={() => q.data && exportCsv(q.data, label, agents.data?.find(a => a.id === agent)?.name ?? "All agents")}
            className="flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft disabled:opacity-50">
            {I.download}Export</button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <AgentPicker value={agent} onChange={v => set({ agent: v || null })} />
        {tab !== "live" && <RangePicker preset={preset} from={from} to={to}
          onChange={(p, a, b) => set({ preset: p, start: p === "custom" ? a ?? null : null, end: p === "custom" ? b ?? null : null })} />}
        {q.isFetching && tab !== "live" && <span className="text-xs text-muted">updating…</span>}
      </div>

      <nav className="grid grid-cols-2 border-t border-line pt-1 sm:grid-cols-4" role="tablist">
        {TABS.map(([k, name]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => set({ tab: k === "overview" ? null : k })}
            className={cx("border-b-2 px-2 py-3 text-sm transition-colors",
              tab === k ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink")}>{name}</button>
        ))}
      </nav>

      {tab === "live" ? <LiveCalls agent={agent} /> : (
        <>
          <ErrorBox error={q.error} />
          {!q.data ? <div className="grid h-60 place-items-center text-sm text-muted">{q.isLoading ? "Loading…" : null}</div>
            : tab === "performance" ? <Performance d={q.data} />
            : tab === "outcome" ? <Outcome d={q.data} />
            : <Overview d={q.data} start={range.start} end={range.end} />}
        </>
      )}
    </div>
  );
}
