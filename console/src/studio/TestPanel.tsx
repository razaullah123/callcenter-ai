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

const Icon = ({ d, className }: { d: string[]; className?: string }) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    strokeLinejoin="round" className={className} aria-hidden="true">{d.map((x, i) => <path key={i} d={x} />)}</svg>
);
export const ICONS = {
  error: ["M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20", "m15 9-6 6", "m9 9 6 6"],
  warning: ["m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3", "M12 9v4", "M12 17h.01"],
  info: ["M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20", "M12 16v-4", "M12 8h.01"],
  debug: ["m8 2 1.88 1.88", "M14.12 3.88 16 2", "M9 7.13v-1a3.003 3.003 0 1 1 6 0v1",
          "M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6", "M12 20v-9",
          "M6.53 9C4.6 8.8 3 7.1 3 5", "M6 13H2", "M3 21c0-2.1 1.7-3.9 3.8-4", "M20.97 5c0 2.1-1.6 3.8-3.5 4",
          "M22 13h-4", "M17.2 17c2.1.1 3.8 1.9 3.8 4"],
  download: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "m7 10 5 5 5-5", "M12 15V3"],
  trash: ["M3 6h18", "M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6", "M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2", "M10 11v6", "M14 11v6"],
  search: ["M11 3a8 8 0 1 0 0 16a8 8 0 1 0 0-16", "m21 21-4.3-4.3"],
  activity: ["M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"],
  close: ["M18 6 6 18", "m6 6 12 12"],
};
export { Icon };

const LEVELS = [
  { key: "error", tone: "text-bad", label: "Error" }, { key: "warning", tone: "text-warn", label: "Warning" },
  { key: "info", tone: "text-good", label: "Information" }, { key: "debug", tone: "text-muted", label: "Debug" },
] as const;

/** Hamsa's log categories. */
export function category(e: LiveEvent): string {
  if (e.type === "step.transition") return "Node Movement";
  if (e.type.startsWith("tool.")) return "Tool Execution";
  if (/^(turn|stt|agent|llm|interrupt|handoff|slot)\b/.test(e.type) || e.type === "policy.block") return "Conversation";
  return "System";
}

/** Small bubble above the control on hover (Hamsa's tooltips). */
function Tip({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <span className="group relative inline-flex">
      {children}
      <span role="tooltip" className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-soft px-2 py-1 text-[11px] font-medium text-ink opacity-0 shadow-sm transition-opacity delay-300 group-hover:opacity-100">
        {text}</span>
    </span>
  );
}

const FOLLOW_KEY = "hmg.studio.live-follow.v1";
/** Follow canvas, saved on this device (Hamsa keeps it per device too). */
export function loadFollow(): boolean {
  try { return JSON.parse(localStorage.getItem(FOLLOW_KEY) ?? "{}").followCanvas !== false; } catch { return true; }
}
export function saveFollow(v: boolean): void {
  try { localStorage.setItem(FOLLOW_KEY, JSON.stringify({ followCanvas: v })); } catch { /* private mode */ }
}

/** Hamsa's 32 px square tool button: outlined, coloured icon; `on` = filled with the accent colour. */
function SquareButton({ title, onClick, on, disabled, tone, children }: {
  title: string; onClick: () => void; on?: boolean; disabled?: boolean; tone?: string; children: React.ReactNode;
}) {
  return (
    <Tip text={title}><button type="button" aria-label={title} aria-pressed={on} onClick={onClick} disabled={disabled}
      className={cx("inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border transition-colors disabled:pointer-events-none disabled:opacity-50",
        on ? "border-brand bg-brand text-black hover:bg-brand/90" : cx("border-line bg-panel hover:bg-soft", tone))}>
      {children}
    </button></Tip>
  );
}

/** On / off switch (Hamsa's "Follow canvas"). */
export function Switch({ id, checked, onChange, label }: { id?: string; checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
      className={cx("relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
        checked ? "bg-brand" : "bg-line")}>
      <span className={cx("inline-block h-4 w-4 rounded-full bg-white shadow transition-transform", checked ? "translate-x-[18px]" : "translate-x-0.5")} />
    </button>
  );
}

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

export default function TestPanel({ call, tab, onTab, follow, onFollow, onLocate, onEvent, onClose, presetNode }: {
  call: ReturnType<typeof useTestCall>; tab: "chat" | "logs" | "vars"; onTab: (t: "chat" | "logs" | "vars") => void;
  follow: boolean; onFollow: (v: boolean) => void; onLocate: (step: string) => void;
  onEvent: (e: LiveEvent) => void; onClose: () => void;
  /** A node's ⋯ → View logs: show only that node's lines ("Filtered by node · Show all"). */
  presetNode?: { node: string; n: number } | null;
}) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [search, setSearch] = useState("");
  const [node, setNode] = useState<string | null>(null);
  useEffect(() => { if (presetNode) setNode(presetNode.node); }, [presetNode]);
  const listRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  // like Hamsa: nothing selected = every type; selecting types shows only those
  const [levels, setLevels] = useState<Set<string>>(new Set());
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

  // the variables the call has collected so far: the latest slot.set per field (the flow's own bookkeeping excluded)
  const vars = useMemo(() => {
    const latest = new Map<string, { value: unknown; source?: string; ts: string }>();
    for (const e of events) {
      if (e.type !== "slot.set" || typeof e.data.field !== "string" || e.data.field.startsWith("flow:")) continue;
      latest.set(e.data.field, { value: e.data.value, source: e.data.source as string | undefined, ts: e.ts });
    }
    return [...latest.entries()].map(([name, v]) => ({ name, ...v }));
  }, [events]);

  const t0 = events.length ? new Date(events[0].ts).getTime() : 0;
  const ofNode = (e: LiveEvent) => { const st = eventStep(e); return !!st && (st === node || st.endsWith(`/${node}`)); };
  const shown = useMemo(() => events.map((e, i) => ({ e, i })).filter(({ e }) =>
    (!levels.size || levels.has(e.level)) && (!node || ofNode(e))
    && (!search || `${e.type} ${category(e)} ${summary(e)} ${eventStep(e) ?? ""}`.toLowerCase().includes(search.toLowerCase()))),
  [events, levels, search, node]);  // eslint-disable-line react-hooks/exhaustive-deps
  const filtered = levels.size > 0 || !!search || !!node;
  const clearFilters = () => { setLevels(new Set()); setSearch(""); setNode(null); };
  // keep the newest line in view while the reader is at the bottom; otherwise offer "Scroll to bottom"
  useEffect(() => { if (atBottom) listRef.current?.scrollTo({ top: listRef.current.scrollHeight }); }, [shown.length, atBottom]);
  const toBottom = () => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" }); setAtBottom(true); };
  const download = () => {
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `${call.callId ?? "test"}-log.json`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="text-base font-semibold">{tab === "logs" ? "Live Call Logs" : tab === "vars" ? "Variables" : call.mode === "voice" ? "Browser call" : "Chat"}</div>
        <div className="flex items-center gap-2">
          {call.callId && <span className="font-mono text-[10px] text-muted">{call.callId}</span>}
          <button className="text-muted hover:text-ink" onClick={onClose} aria-label="Close panel"><Icon d={ICONS.close} /></button>
        </div>
      </div>
      {(call.running || call.lines.length > 0) && (
        <div className="flex gap-1 px-3 pt-2">
          <Button kind={tab === "chat" ? "default" : "ghost"} onClick={() => onTab("chat")}>💬 {call.mode === "voice" ? "Call" : "Chat"}</Button>
          <Button kind={tab === "logs" ? "default" : "ghost"} onClick={() => onTab("logs")}>Logs{events.length ? ` (${events.length})` : ""}</Button>
          <Button kind={tab === "vars" ? "default" : "ghost"} onClick={() => onTab("vars")}>(x) Variables{vars.length ? ` (${vars.length})` : ""}</Button>
        </div>
      )}

      {tab === "vars" ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          <div className="mb-2 text-[11px] text-muted">Values collected during this call, updated live (the latest value of each).</div>
          {vars.length === 0 && <div className="py-10 text-center text-sm text-muted">{call.callId ? "Nothing collected yet." : "Start a call to see its variables."}</div>}
          <div className="space-y-1.5">
            {vars.map(v => (
              <div key={v.name} className="rounded-lg border border-line px-2.5 py-1.5">
                <div className="flex items-center gap-2"><span className="font-mono text-xs font-semibold">{v.name}</span>
                  {v.source && <span className="rounded bg-soft px-1.5 text-[10px] text-muted">{v.source}</span>}
                  <span className="ml-auto text-[10px] tabular-nums text-muted">{new Date(v.ts).toLocaleTimeString()}</span></div>
                <div dir="auto" className="mt-0.5 break-words text-xs">{typeof v.value === "string" ? v.value : JSON.stringify(v.value)}</div>
              </div>))}
          </div>
        </div>
      ) : tab === "chat" ? (
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
              : <div key={i} dir="auto" className={cx("max-w-[85%] rounded-2xl px-3 py-2 text-sm", l.role === "agent" ? "self-start bg-soft" : "self-end bg-accent text-accent-fg")}>{l.text}</div>)}
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
          <div className="space-y-3 border-b border-line px-3 py-3">
            <div className="relative">
              <Icon d={ICONS.search} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input id="log-search" className="w-full rounded-lg py-2" style={{ paddingLeft: "2.25rem" }} placeholder="Search logs..." value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <div>
              <div className="flex items-center justify-between gap-3">
                <label htmlFor="follow-canvas" className="text-sm font-medium">Follow canvas</label>
                <Switch id="follow-canvas" checked={follow} onChange={onFollow} label="Follow canvas" />
              </div>
              <div className="mt-0.5 text-[11px] leading-snug text-muted">Pan the flow so the active node stays centred in the visible area (needed for the highlight when many nodes are off-screen).</div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-sm text-muted">Filter by Type:</span>
              {LEVELS.map(l => (
                <SquareButton key={l.key} title={`${levels.has(l.key) ? "Hide" : "Show"} ${l.label}`} tone={l.tone} on={levels.has(l.key)}
                  onClick={() => { const s = new Set(levels); if (s.has(l.key)) s.delete(l.key); else s.add(l.key); setLevels(s); }}>
                  <Icon d={ICONS[l.key]} /></SquareButton>
              ))}
              <span className="mx-2 h-6 w-px bg-line" />
              <SquareButton title="Export Logs" onClick={download} disabled={!events.length}><Icon d={ICONS.download} /></SquareButton>
              <SquareButton title="Clear All Logs" tone="text-bad" onClick={() => { setEvents([]); setOpen(new Set()); }}><Icon d={ICONS.trash} /></SquareButton>
            </div>
            {node && (
              <div className="flex items-center gap-2 rounded-md bg-soft px-2 py-1 text-xs">
                <span className="text-muted">Filtered by node</span><span className="truncate font-mono">{node.split("/").pop()}</span>
                <button className="ml-auto font-medium text-accent-text hover:underline" onClick={() => setNode(null)}>Show all</button>
              </div>
            )}
          </div>
          <div className="relative min-h-0 flex-1">
          <div ref={listRef} onScroll={e => { const el = e.currentTarget; setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 40); }}
            className="h-full space-y-1.5 overflow-y-auto overflow-x-hidden px-3 py-2">
            {shown.length === 0 && (
              <div className="flex flex-col items-center py-14 text-center">
                <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-soft"><Icon d={ICONS.activity} /></div>
                <div className="text-base font-semibold">{events.length && filtered ? "No Matching Logs" : "No Logs Yet"}</div>
                <div className="mt-1 text-sm text-muted">{events.length && filtered ? "Try adjusting your search terms or filters to find what you're looking for."
                  : "Start a call to see live events and debugging information."}</div>
                {events.length > 0 && filtered && <div className="mt-3"><Button onClick={clearFilters}>Clear Filters</Button></div>}
              </div>
            )}
            {shown.map(({ e, i }) => {
              const step = eventStep(e);
              const secs = Math.max(0, Math.round((new Date(e.ts).getTime() - t0) / 1000));
              return (
                <div key={i} className={cx("rounded-lg border border-line border-l-4 bg-panel px-2 py-1.5",
                  e.level === "error" ? "border-l-bad" : e.level === "warning" ? "border-l-warn" : e.type === "step.transition" ? "border-l-accent" : "border-l-line")}>
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-soft px-1.5 font-mono text-[10px] tabular-nums">{String(Math.floor(secs / 60)).padStart(2, "0")}:{String(secs % 60).padStart(2, "0")}</span>
                    <span className="rounded border border-line px-1.5 text-[10px] font-semibold uppercase tracking-wide">{category(e)}</span>
                    <span className="ml-auto flex items-center gap-1">
                      {step && <Tip text="Locate node on canvas"><button aria-label="Locate node on canvas" className="text-muted hover:text-accent-text" onClick={() => onLocate(step)}>⌖</button></Tip>}
                      <button className="text-muted hover:text-ink" onClick={() => { const s = new Set(open); if (s.has(i)) s.delete(i); else s.add(i); setOpen(s); }}>{open.has(i) ? "▴" : "▾"}</button>
                    </span>
                  </div>
                  <div dir="auto" className="mt-0.5 break-words text-xs">{summary(e)}</div>
                  {open.has(i) && <pre className="mt-1 max-h-48 overflow-auto rounded bg-soft p-1.5 text-[10px]">{JSON.stringify(e, null, 2)}</pre>}
                </div>
              );
            })}
          </div>
          {!atBottom && shown.length > 0 && (
            <button onClick={toBottom} className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-line bg-panel px-3 py-1 text-xs font-medium shadow-md hover:bg-soft">
              ↓ Scroll to bottom</button>
          )}
          </div>
        </>
      )}
    </div>
  );
}
