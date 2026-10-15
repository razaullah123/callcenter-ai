/** Pieces of a call's details (the Call History side panel, the Live page): latency waterfall and event timeline. */
import { useMemo, useState } from "react";
import { type EventRow, type TurnLatency } from "../api";
import { Badge, Empty, cx, fmtClock, fmtMs, levelTone } from "../ui";

const SEG = [
  { key: "stt_ms", label: "STT", cls: "bg-sky-500" },
  { key: "llm_first_token_ms", label: "LLM first token", cls: "bg-violet-500" },
  { key: "tools_ms", label: "Tools", cls: "bg-amber-500" },
] as const;

export function Waterfall({ turns }: { turns: TurnLatency[] }) {
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
