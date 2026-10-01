import { useEffect, useMemo, useRef, useState } from "react";
import { wsUrl } from "../api";
import { Badge, Button, cx } from "../ui";
import type { useTestCall } from "./useTestCall";

// Right-hand test panel of the Agent Studio (like Hamsa's): the conversation, and the call's live log with
// "Follow canvas" — the canvas keeps the active node centred and highlighted while the call moves through the flow.

export type LiveEvent = {
  event_id?: string; ts: string; type: string; level: string; turn_id?: number | null;
  skill?: string | null; step?: string | null; latency_ms?: number | null; data: Record<string, unknown>;
};

const LEVELS = [
  { key: "error", icon: "⊗", tone: "text-bad" }, { key: "warning", icon: "⚠", tone: "text-warn" },
  { key: "info", icon: "ⓘ", tone: "text-good" }, { key: "debug", icon: "⚙", tone: "text-muted" },
];

/** The flow node an event belongs to, as "skill/node". */
export const eventStep = (e: LiveEvent) =>
  (e.type === "step.transition" ? (e.data.step as string | undefined) : undefined) ?? e.step ?? undefined;

function summary(e: LiveEvent): string {
  const d = e.data, ms = e.latency_ms ?? (d.latency_ms as number | undefined);
  const t = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));
  switch (e.type) {
    case "step.transition": return `→ ${String(d.step ?? "").split("/").pop()}${d.action ? ` (${d.action})` : ""}`;
    case "stt.result": return `Heard: ${t(d.text)}`;
    case "turn.start": return `Caller: ${t(d.text)}`;
    case "agent.say": return `Agent: ${t(d.text)}`;
    case "tool.start": return `Calling ${d.tool} ${d.args ? t(d.args) : ""}`;
    case "tool.end": return `${d.tool} done${ms != null ? ` in ${Math.round(ms)} ms` : ""}${d.cached ? " (cached)" : ""}${d.prefetch ? " (prefetch)" : ""}`;
    case "tool.error": return `${d.tool} failed: ${t(d.error)}`;
    case "slot.set": return d.field === "flow:classified" ? `Read the caller's words (${Math.round(Number(ms ?? 0))} ms)` : `${d.field} = ${t(d.value)}`;
    case "policy.block": return `Blocked: ${d.reason}${d.text ? ` — ${t(d.text)}` : ""}`;
    case "llm.first_token": return `Model first token${ms != null ? ` ${Math.round(ms)} ms` : ""}`;
    case "llm.hedge": return `Slow model start: backup request (${t(d)})`;
    case "handoff": return `Transfer: ${t(d.reason)}`;
    case "call.start": return "Call started";
    case "call.end": return `Call ended (${t(d.reason ?? "")})`;
    case "error": return `Error during ${d.during ?? "?"}: ${t(d.error)}`;
    default: return `${e.type} ${Object.keys(d).length ? t(d).slice(0, 160) : ""}`;
  }
}

export default function TestPanel({ call, tab, onTab, follow, onFollow, onLocate, onEvent, onClose, presetSearch }: {
  call: ReturnType<typeof useTestCall>; tab: "chat" | "logs"; onTab: (t: "chat" | "logs") => void;
  follow: boolean; onFollow: (v: boolean) => void; onLocate: (step: string) => void;
  onEvent: (e: LiveEvent) => void; onClose: () => void;
  /** A node's ⋯ → View logs: filter the log to it. */
  presetSearch?: { text: string; n: number } | null;
}) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [search, setSearch] = useState("");
  useEffect(() => { if (presetSearch) setSearch(presetSearch.text); }, [presetSearch]);
  const [levels, setLevels] = useState<Set<string>>(new Set(["error", "warning", "info", "debug"]));
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [text, setText] = useState("");
  const chatRef = useRef<HTMLDivElement>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  // the call's events, live (they keep coming after a page refresh of the log, but not before the call started)
  useEffect(() => {
    if (!call.callId) return;
    setEvents([]); setOpen(new Set());
    const ws = new WebSocket(wsUrl("/api/live", { call_id: call.callId }));
    ws.onmessage = m => {
      const msg = JSON.parse(m.data);
      if (msg.kind !== "event") return;
      const e = msg.event as LiveEvent;
      setEvents(p => [...p, e]);
      onEventRef.current(e);
    };
    return () => ws.close();
  }, [call.callId]);
  useEffect(() => { chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight }); }, [call.lines]);

  const t0 = events.length ? new Date(events[0].ts).getTime() : 0;
  const shown = useMemo(() => events.map((e, i) => ({ e, i })).filter(({ e }) =>
    levels.has(e.level) && (!search || `${e.type} ${summary(e)} ${eventStep(e) ?? ""}`.toLowerCase().includes(search.toLowerCase()))), [events, levels, search]);
  const download = () => {
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `${call.callId ?? "test"}-log.json`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="text-sm font-semibold">{tab === "logs" ? "Live call logs" : call.mode === "voice" ? "Browser call" : "Chat"}</div>
        <div className="flex items-center gap-2">
          {call.callId && <span className="font-mono text-[10px] text-muted">{call.callId}</span>}
          <button className="text-muted hover:text-ink" onClick={onClose} aria-label="Close panel">✕</button>
        </div>
      </div>
      <div className="flex gap-1 px-3 pt-2">
        <Button kind={tab === "chat" ? "default" : "ghost"} onClick={() => onTab("chat")}>💬 {call.mode === "voice" ? "Call" : "Chat"}</Button>
        <Button kind={tab === "logs" ? "default" : "ghost"} onClick={() => onTab("logs")}>〰 Logs{events.length ? ` (${events.length})` : ""}</Button>
      </div>

      {tab === "chat" ? (
        <>
          <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted">
            <Badge tone={call.state === "speaking" ? "warn" : call.state === "listening" ? "good" : "neutral"}>{call.state}</Badge>
            {call.mode === "voice" && call.running && <div className="h-1.5 w-24 overflow-hidden rounded bg-soft"><div className="h-1.5 bg-accent" style={{ width: `${Math.min(100, call.level * 160)}%` }} /></div>}
            {call.latency.length > 0 && <span className="ml-auto tabular-nums">reply {Math.round(call.latency[call.latency.length - 1])} ms</span>}
          </div>
          <div ref={chatRef} className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 pb-3">
            {call.lines.length === 0 && <div className="py-8 text-center text-sm text-muted">{call.state === "connecting" ? "Connecting…" : "Start a test from the Test button."}</div>}
            {call.lines.map((l, i) => l.role === "sys"
              ? <div key={i} className="text-center text-[11px] text-muted">{l.text}</div>
              : <div key={i} dir="auto" className={cx("max-w-[85%] rounded-2xl px-3 py-2 text-sm", l.role === "agent" ? "self-start bg-soft" : "self-end bg-accent text-white")}>{l.text}</div>)}
          </div>
          {call.mode === "chat" && (
            <form className="flex gap-2 border-t border-line p-2" onSubmit={e => { e.preventDefault(); call.send(text); setText(""); }}>
              <input id="test-chat-input" dir="auto" className="min-w-0 flex-1" placeholder={call.running ? "Type a message…" : "The chat has ended"}
                disabled={!call.running} value={text} onChange={e => setText(e.target.value)} />
              <Button kind="primary" type="submit" disabled={!call.running || !text.trim()}>Send</Button>
            </form>
          )}
        </>
      ) : (
        <>
          <div className="space-y-2 border-b border-line px-3 py-2">
            <input id="log-search" className="w-full" placeholder="Search logs…" value={search} onChange={e => setSearch(e.target.value)} />
            <label className="flex items-start justify-between gap-3">
              <span><span className="text-sm font-medium">Follow canvas</span>
                <span className="block text-[11px] text-muted">Pan the flow so the active node stays centred and highlighted as the call moves.</span></span>
              <input id="follow-canvas" type="checkbox" className="mt-1 h-4 w-4 accent-[var(--color-accent)]" checked={follow} onChange={e => onFollow(e.target.checked)} />
            </label>
            <div className="flex flex-wrap items-center gap-1 text-xs">
              <span className="mr-1 text-muted">Filter by type:</span>
              {LEVELS.map(l => (
                <button key={l.key} title={l.key} onClick={() => { const s = new Set(levels); if (s.has(l.key)) s.delete(l.key); else s.add(l.key); setLevels(s); }}
                  className={cx("rounded-md border px-2 py-0.5", levels.has(l.key) ? "border-line bg-panel" : "border-transparent opacity-40", l.tone)}>{l.icon}</button>
              ))}
              <span className="mx-1 h-4 w-px bg-line" />
              <button title="Download the log" className="rounded-md border border-line px-2 py-0.5" onClick={download} disabled={!events.length}>⤓</button>
              <button title="Clear the log view" className="rounded-md border border-line px-2 py-0.5 text-bad" onClick={() => setEvents([])}>🗑</button>
            </div>
          </div>
          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto overflow-x-hidden px-3 py-2">
            {shown.length === 0 && <div className="py-8 text-center text-sm text-muted">{call.callId ? "No events yet." : "Logs appear when a test call starts."}</div>}
            {shown.map(({ e, i }) => {
              const step = eventStep(e);
              const secs = Math.max(0, Math.round((new Date(e.ts).getTime() - t0) / 1000));
              return (
                <div key={i} className={cx("rounded-lg border border-line border-l-4 bg-panel px-2 py-1.5",
                  e.level === "error" ? "border-l-bad" : e.level === "warning" ? "border-l-warn" : e.type === "step.transition" ? "border-l-accent" : "border-l-line")}>
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-soft px-1.5 font-mono text-[10px] tabular-nums">{String(Math.floor(secs / 60)).padStart(2, "0")}:{String(secs % 60).padStart(2, "0")}</span>
                    <span className="rounded border border-line px-1.5 text-[10px] font-semibold uppercase tracking-wide">{e.type.split(".")[0]}</span>
                    <span className="ml-auto flex items-center gap-1">
                      {step && <button title="Show this step on the canvas" className="text-muted hover:text-accent" onClick={() => onLocate(step)}>⌖</button>}
                      <button className="text-muted hover:text-ink" onClick={() => { const s = new Set(open); if (s.has(i)) s.delete(i); else s.add(i); setOpen(s); }}>{open.has(i) ? "▴" : "▾"}</button>
                    </span>
                  </div>
                  <div dir="auto" className="mt-0.5 break-words text-xs">{summary(e)}</div>
                  {open.has(i) && <pre className="mt-1 max-h-48 overflow-auto rounded bg-soft p-1.5 text-[10px]">{JSON.stringify(e, null, 2)}</pre>}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
