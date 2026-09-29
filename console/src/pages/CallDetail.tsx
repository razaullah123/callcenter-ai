import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type CallDetail as Detail, type EventRow, type TurnLatency } from "../api";
import { Badge, Card, Empty, ErrorBox, cx, fmtClock, fmtMs, fmtTime, levelTone, outcome } from "../ui";

export default function CallDetail() {
  const { id = "" } = useParams();
  const q = useQuery({ queryKey: ["call", id], queryFn: () => api.call(id), refetchInterval: d => (d.state.data?.call.ended_at ? false : 3000) });
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Empty>Loading…</Empty>;
  const { call } = q.data;
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/calls" className="text-sm text-muted">← Calls</Link>
        <h1 className="font-mono text-lg font-semibold">{call.call_id}</h1>
        {outcome(call)}
        {call.verified && <Badge tone="info">verified</Badge>}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted">
        {call.mobile && <span>Mobile <span className="font-mono text-ink">{call.mobile}</span></span>}
        <span>Started {fmtTime(call.started_at)}</span>
        {call.ended_at && <span>Ended {fmtTime(call.ended_at)}</span>}
        {call.duration_s != null && <span>Duration {Math.floor(call.duration_s / 60)}:{String(call.duration_s % 60).padStart(2, "0")}</span>}
        <span>Channel {call.channel}</span><span>Language {call.language ?? "—"}</span>
        <span>{call.turns} turns</span><span>Latency p50 {fmtMs(call.latency_p50_ms)}</span>
        {call.config_version && <span>Config v{call.config_version}</span>}
        {call.handoff && <span className="text-warn">Handoff: {call.handoff}</span>}
      </div>
      <div className="grid gap-4 lg:grid-cols-5">
        <Card title="Conversation" className="lg:col-span-2"><Transcript data={q.data} /></Card>
        <Card title="Latency per turn" className="lg:col-span-3"><Waterfall turns={q.data.turns} /></Card>
      </div>
      <Card title={`Timeline · ${q.data.events.length} events`}><Timeline events={q.data.events} /></Card>
    </div>
  );
}

function Transcript({ data }: { data: Detail }) {
  if (!data.transcript.length) return <Empty>No speech recorded.</Empty>;
  return (
    <div className="flex max-h-[32rem] flex-col gap-2 overflow-y-auto pr-1">
      {data.transcript.map((l, i) => (
        l.role === "system"
          ? <div key={i} className="self-center text-xs text-warn">⚑ {l.text}</div>
          : <div key={i} dir="auto" className={cx("max-w-[88%] rounded-xl px-3 py-2 text-sm",
              l.role === "user" ? "self-end bg-accent/10" : "self-start bg-soft")}>
              <div className="mb-0.5 text-[10px] text-muted" dir="ltr">{l.role === "user" ? "Caller" : "Agent"} · {fmtClock(l.ts)}</div>
              {l.text}
            </div>
      ))}
    </div>
  );
}

const SEG = [
  { key: "stt_ms", label: "STT", cls: "bg-sky-500" },
  { key: "llm_first_token_ms", label: "LLM first token", cls: "bg-violet-500" },
  { key: "tools_ms", label: "Tools", cls: "bg-amber-500" },
] as const;

function Waterfall({ turns }: { turns: TurnLatency[] }) {
  if (!turns.length) return <Empty>No turns.</Empty>;
  const rows = turns.map(t => ({ ...t, tools_ms: t.tools.reduce((a, x) => a + (x.cached ? 0 : x.ms ?? 0), 0) }));
  const max = Math.max(1, ...rows.map(r => Math.max(r.first_audio_ms ?? 0, (r.stt_ms ?? 0) + (r.total_ms ?? 0))));
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap gap-3 text-[11px] text-muted">
        {SEG.map(s => <span key={s.key} className="flex items-center gap-1"><i className={cx("inline-block h-2 w-2 rounded-sm", s.cls)} />{s.label}</span>)}
        <span className="flex items-center gap-1"><i className="inline-block h-2 w-0.5 bg-ink" />first audio</span>
      </div>
      {rows.map(r => (
        <div key={r.turn} className="grid grid-cols-[2.5rem_1fr_4.5rem] items-center gap-2">
          <span className="text-xs text-muted">t{r.turn}</span>
          <div className="relative h-5 rounded bg-soft" title={r.text ?? ""}>
            <div className="flex h-5 overflow-hidden rounded">
              {SEG.map(s => {
                const v = (r as unknown as Record<string, number | null>)[s.key] ?? 0;
                return v > 0 ? <div key={s.key} className={s.cls} style={{ width: `${(v / max) * 100}%` }} title={`${s.label} ${fmtMs(v)}`} /> : null;
              })}
            </div>
            {r.first_audio_ms != null && (
              <div className="absolute top-0 h-5 w-0.5 bg-ink" style={{ left: `${Math.min(100, (((r.stt_ms ?? 0) + r.first_audio_ms) / max) * 100)}%` }}
                title={`first audio ${fmtMs(r.first_audio_ms)} after turn end`} />
            )}
          </div>
          <span className="text-right text-xs tabular-nums">{fmtMs(r.first_audio_ms)}</span>
        </div>
      ))}
      <p className="text-xs text-muted">Right column: turn end → first agent audio (excluding end-of-turn silence). Hover a bar for details.</p>
    </div>
  );
}

const GROUPS: Record<string, string[]> = {
  all: [], speech: ["stt.result", "agent.say", "turn.start", "interrupt", "vad.speech_start", "vad.speech_stop"],
  tools: ["tool.start", "tool.end", "tool.error"], llm: ["llm.request", "llm.first_token", "llm.end"],
  flow: ["skill.enter", "skill.exit", "step.transition", "slot.set", "policy.block", "handoff"],
  errors: ["error", "tool.error"],
};

export function Timeline({ events }: { events: EventRow[] }) {
  const [group, setGroup] = useState("all");
  const [open, setOpen] = useState<number | null>(null);
  const shown = useMemo(() => group === "all" ? events : events.filter(e => GROUPS[group].includes(e.type) ||
    (group === "errors" && e.level === "error")), [events, group]);
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1">
        {Object.keys(GROUPS).map(g => (
          <button key={g} onClick={() => setGroup(g)}
            className={cx("rounded-md px-2.5 py-1 text-xs", group === g ? "bg-soft font-medium" : "text-muted")}>{g}</button>
        ))}
      </div>
      <div className="max-h-[36rem] overflow-y-auto font-mono text-xs">
        {shown.map(e => (
          <div key={e.id} className="border-b border-line/60">
            <button className="grid w-full grid-cols-[6rem_2.5rem_9rem_5rem_1fr] gap-2 py-1 text-left hover:bg-soft/60"
              onClick={() => setOpen(open === e.id ? null : e.id)}>
              <span className="text-muted">{fmtClock(e.ts)}</span>
              <span className="text-muted">{e.turn_id != null ? `t${e.turn_id}` : ""}</span>
              <span><Badge tone={levelTone(e.level)}>{e.type}</Badge></span>
              <span className="tabular-nums text-muted">{e.latency_ms != null ? fmtMs(e.latency_ms) : ""}</span>
              <span className="truncate" dir="auto">{summary(e)}</span>
            </button>
            {open === e.id && <pre className="overflow-x-auto whitespace-pre-wrap bg-soft/50 p-2" dir="auto">{JSON.stringify(e, null, 2)}</pre>}
          </div>
        ))}
        {!shown.length && <Empty>No events in this group.</Empty>}
      </div>
    </div>
  );
}

export function summary(e: { type: string; data: Record<string, unknown>; skill?: string | null }) {
  const d = e.data ?? {};
  const s = (k: string) => (d[k] == null ? "" : String(d[k]));
  switch (e.type) {
    case "agent.say": case "stt.result": case "turn.start": return s("text");
    case "tool.start": return `${s("tool")} ${JSON.stringify(d.args ?? {})}`;
    case "tool.end": return `${s("tool")}${d.cached ? " (cached)" : ""}${d.prefetch ? " (prefetch)" : ""}`;
    case "tool.error": return `${s("tool")}: ${s("error")}`;
    case "slot.set": return `${s("field")} = ${JSON.stringify(d.value)}`;
    case "step.transition": return `${s("previous")} → ${s("step")}`;
    case "skill.enter": case "skill.exit": return `${s("skill")} ${s("reason")}`;
    case "policy.block": case "handoff": return s("reason");
    case "error": return `${s("during")} ${s("error")}`;
    case "llm.end": return `${s("finish")} ${Array.isArray(d.tool_calls) ? (d.tool_calls as string[]).join(", ") : ""}`;
    default: return Object.keys(d).length ? JSON.stringify(d) : "";
  }
}
