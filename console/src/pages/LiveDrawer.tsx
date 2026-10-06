import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ActiveCall, type TurnLatency } from "../api";
import { Badge, cx, fmtClock, fmtMs } from "../ui";

export const LANG: Record<string, string> = { ar: "Arabic", en: "English", unknown: "Unknown" };
export const CHANNEL: Record<string, string> = { web: "Web call", ivr: "Telephone", chat: "Chat" };
export const channelOf = (callId: string) => (callId.startsWith("ivr-") ? "ivr" : callId.startsWith("chat-") ? "chat" : "web");

const TABS = [["overview", "Overview"], ["conversation", "Conversation"], ["outcome", "Outcome"]] as const;
type Tab = typeof TABS[number][0];

const avg = (rows: TurnLatency[], f: (t: TurnLatency) => number | null) => {
  const v = rows.map(f).filter((x): x is number => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};
const dur = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s) % 60).padStart(2, "0")}`;

/** Right-hand details of a call in progress: overview + live metrics, speaker-labelled transcript, outcome (Hamsa's drawer). */
export default function LiveDrawer({ call, agentName, onClose }: { call: ActiveCall; agentName: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const q = useQuery({ queryKey: ["live-call", call.call_id], queryFn: () => api.call(call.call_id), refetchInterval: 2000 });
  const d = q.data;
  const ended = !!d?.call.ended_at;
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  useEffect(() => { if (tab === "conversation") end.current?.scrollIntoView({ block: "end" }); }, [tab, d?.transcript.length]);

  const turns = d?.turns ?? [];
  const metrics: [string, number | null][] = [
    ["ASR", avg(turns, t => t.stt_ms)], ["LLM first token", avg(turns, t => t.llm_first_token_ms)],
    ["Latency (turn end → audio)", avg(turns, t => t.first_audio_ms)],
  ];

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/30" onClick={onClose} />
      <aside className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-line bg-panel shadow-xl" role="dialog" aria-label="Call details">
        <header className="flex items-start justify-between gap-3 border-b border-line p-4">
          <div className="min-w-0">
            <div className="truncate font-semibold">{agentName}</div>
            <div className="truncate font-mono text-xs text-muted">{call.call_id}</div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge tone={ended ? "neutral" : "good"}>{ended ? "Ended" : "In progress"}</Badge>
            <button onClick={onClose} aria-label="Close" className="rounded-md px-2 py-1 text-muted hover:bg-soft hover:text-ink">✕</button>
          </div>
        </header>
        <nav className="grid grid-cols-3 border-b border-line" role="tablist">
          {TABS.map(([k, name]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
              className={cx("border-b-2 px-2 py-2.5 text-sm", tab === k ? "border-accent font-medium" : "border-transparent text-muted hover:text-ink")}>{name}</button>
          ))}
        </nav>
        <div className="flex-1 overflow-y-auto p-4">
          {tab === "overview" && (
            <div className="space-y-5">
              <dl className="grid grid-cols-2 gap-3 text-sm">
                {([["Session ID", <span className="break-all font-mono text-xs">{call.call_id}</span>],
                  ["Status", ended ? "Ended" : "In progress"],
                  ["Started", new Date(call.started * 1000).toLocaleString()],
                  ["Duration", dur(d?.call.duration_s ?? call.duration_s)],
                  ["Channel", CHANNEL[channelOf(call.call_id)]],
                  ["Language", LANG[call.language ?? ""] ?? call.language ?? "—"],
                  ["Step", call.step ?? call.skill ?? "—"],
                  ["Turns", d?.call.turns ?? call.turns]] as [string, React.ReactNode][]).map(([k, v]) => (
                  <div key={k} className="rounded-lg bg-soft px-3 py-2"><dt className="text-xs text-muted">{k}</dt><dd className="mt-0.5 font-medium">{v}</dd></div>
                ))}
              </dl>
              <div>
                <div className="mb-2 text-sm font-semibold">Performance (average so far)</div>
                <div className="space-y-2">
                  {metrics.map(([k, v]) => (
                    <div key={k} className="flex justify-between rounded-lg bg-soft px-3 py-2 text-sm">
                      <span className="text-muted">{k}</span><span className="font-semibold tabular-nums">{fmtMs(v)}</span>
                    </div>
                  ))}
                </div>
              </div>
              <p className="text-xs text-muted">Listening in on the call's audio isn't available yet — this view follows the conversation as text.</p>
            </div>
          )}
          {tab === "conversation" && (
            <div className="space-y-3">
              {d?.transcript.length ? d.transcript.map((m, i) => m.role === "system" ? (
                <div key={i} className="text-center text-xs text-muted">{m.text}</div>
              ) : (
                <div key={i} className={cx("flex flex-col", m.role === "user" ? "items-start" : "items-end")}>
                  <div className="mb-0.5 text-[11px] text-muted">{m.role === "user" ? "Caller" : "Agent"} · {fmtClock(m.ts)}</div>
                  <div dir="auto" className={cx("max-w-[85%] rounded-2xl px-3 py-2 text-sm",
                    m.role === "user" ? "bg-soft" : "bg-accent/15")}>{m.text}</div>
                </div>
              )) : <div className="py-10 text-center text-sm text-muted">{q.isLoading ? "Loading…" : "Waiting for the first words…"}</div>}
              <div ref={end} />
            </div>
          )}
          {tab === "outcome" && (
            ended && d ? (
              <div className="space-y-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <Badge tone={d.call.handoff ? "warn" : "good"}>{d.call.handoff ? "Escalated" : "Resolved"}</Badge>
                  {d.call.verified && <Badge tone="info">verified</Badge>}
                  {d.call.booked && <Badge tone="good">booked</Badge>}
                </div>
                {d.call.handoff && <div className="rounded-lg bg-soft px-3 py-2"><span className="text-muted">Hand-off reason · </span>{d.call.handoff}</div>}
                {d.call.end_reason && <div className="rounded-lg bg-soft px-3 py-2"><span className="text-muted">Ended by · </span>{d.call.end_reason}</div>}
                <p className="text-xs text-muted">AI summary and sentiment need the post-call analysis step (PLAN 12.9).</p>
              </div>
            ) : <div className="py-10 text-center text-sm text-muted">The outcome appears once the call has ended.</div>
          )}
        </div>
        <footer className="border-t border-line p-3 text-right">
          <Link to={`/calls/${encodeURIComponent(call.call_id)}`} className="text-sm font-medium text-accent-text hover:underline">Open full call →</Link>
        </footer>
      </aside>
    </>
  );
}
