import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { Card, Empty, ErrorBox, Stat, fmtMs, fmtPct, fmtTime, outcome } from "../ui";

const WINDOWS = [{ h: 1, label: "1 h" }, { h: 24, label: "24 h" }, { h: 168, label: "7 days" }, { h: 720, label: "30 days" }];
const STAGES: [string, string][] = [
  ["stt.result", "Speech-to-text"], ["llm.first_token", "LLM first token"], ["tool.end", "Tool call"],
  ["tts.first_byte", "Turn end → first audio"],
];

export default function Dashboard() {
  const [hours, setHours] = useState(168);
  const stats = useQuery({ queryKey: ["stats", hours], queryFn: () => api.stats(hours), refetchInterval: 15_000 });
  const recent = useQuery({ queryKey: ["calls", "recent"], queryFn: () => api.calls({ limit: 8 }), refetchInterval: 15_000 });
  const t = stats.data?.totals;
  const maxStage = Math.max(1, ...STAGES.map(([k]) => stats.data?.stages[k] ?? 0));

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Dashboard</h1>
          <p className="text-sm text-muted">Calls, outcomes and voice latency.</p>
        </div>
        <div className="flex rounded-lg border border-line bg-panel p-0.5 text-sm">
          {WINDOWS.map(w => (
            <button key={w.h} onClick={() => setHours(w.h)}
              className={`rounded-md px-3 py-1 ${hours === w.h ? "bg-soft font-medium" : "text-muted"}`}>{w.label}</button>
          ))}
        </div>
      </div>
      <ErrorBox error={stats.error} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Calls" value={t?.calls ?? "—"} hint={`${stats.data?.active_calls ?? 0} live now`} />
        <Stat label="Verified" value={t ? fmtPct(t.verified, t.calls) : "—"} hint={`${t?.verified ?? 0} callers`} />
        <Stat label="Booked" value={t?.booked ?? "—"} hint={t ? `${fmtPct(t.booked, t.calls)} of calls` : ""} tone="good" />
        <Stat label="Handed to a human" value={t ? fmtPct(t.handoffs, t.calls) : "—"} hint={`${t?.handoffs ?? 0} calls`}
          tone={t && t.calls && t.handoffs / t.calls > 0.25 ? "warn" : undefined} />
        <Stat label="Response latency p50" value={fmtMs(stats.data?.latency.p50)}
          hint={`p95 ${fmtMs(stats.data?.latency.p95)} · turn end → first audio`}
          tone={(stats.data?.latency.p50 ?? 0) > 2000 ? "warn" : "good"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Calls per hour" className="lg:col-span-2">
          {stats.data?.per_hour.length ? (
            <div className="h-56">
              <ResponsiveContainer>
                <BarChart data={stats.data.per_hour.map(r => ({ ...r, label: new Date(r.hour).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit" }) }))}>
                  <CartesianGrid vertical={false} stroke="var(--color-line)" />
                  <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--color-muted)" }} tickLine={false} axisLine={false} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--color-muted)" }} tickLine={false} axisLine={false} width={28} />
                  <Tooltip contentStyle={{ background: "var(--color-panel)", border: "1px solid var(--color-line)", borderRadius: 8 }} />
                  <Bar dataKey="calls" name="Calls" fill="var(--color-accent)" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="handoffs" name="Handoffs" fill="var(--color-warn)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : <Empty>No calls in this window.</Empty>}
        </Card>

        <Card title="Latency by stage (median)">
          <div className="space-y-3">
            {STAGES.map(([k, label]) => {
              const v = stats.data?.stages[k];
              return (
                <div key={k}>
                  <div className="flex justify-between text-xs"><span>{label}</span><span className="tabular-nums text-muted">{fmtMs(v)}</span></div>
                  <div className="mt-1 h-2 rounded bg-soft">
                    <div className="h-2 rounded bg-accent" style={{ width: `${((v ?? 0) / maxStage) * 100}%` }} />
                  </div>
                </div>
              );
            })}
            <p className="pt-1 text-xs text-muted">The caller also waits for end-of-turn silence (default 550 ms) before the agent starts.</p>
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Recent calls" className="lg:col-span-2" actions={<Link to="/calls" className="text-xs text-accent">All calls →</Link>}>
          {recent.data?.items.length ? (
            <table className="w-full text-sm">
              <tbody>
                {recent.data.items.map(c => (
                  <tr key={c.call_id} className="border-b border-line last:border-0">
                    <td className="py-2"><Link to={`/calls/${c.call_id}`} className="font-mono text-xs text-accent">{c.call_id}</Link></td>
                    <td className="py-2 text-xs text-muted">{fmtTime(c.started_at)}</td>
                    <td className="py-2 text-xs">{c.channel}</td>
                    <td className="py-2 text-xs">{c.last_skill ?? "—"}</td>
                    <td className="py-2 text-right">{outcome(c)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No calls yet.</Empty>}
        </Card>
        <Card title="Where calls ended up">
          {stats.data?.skills.length ? (
            <ul className="space-y-2 text-sm">
              {stats.data.skills.map(s => (
                <li key={s.skill} className="flex justify-between"><span>{s.skill}</span><span className="tabular-nums text-muted">{s.calls}</span></li>
              ))}
            </ul>
          ) : <Empty>—</Empty>}
        </Card>
      </div>
    </div>
  );
}
