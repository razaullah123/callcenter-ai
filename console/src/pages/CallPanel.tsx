import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, type CallDetail, type EventRow } from "../api";
import { ErrorBox, cx, fmtMs } from "../ui";
import { Timeline, Waterfall } from "./CallDetail";
import { CopyButton } from "../table";
import { ChannelBadge, StatusBadge, fmtDuration, fmtWhen } from "./Calls";

const TABS = [["overview", "Overview"], ["conversation", "Conversation"], ["outcome", "Outcome"], ["logs", "Logs"]] as const;
type Tab = typeof TABS[number][0];

const ico = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
    strokeLinejoin="round">{d}</svg>
);
const T = {
  bot: ico(<><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M12 4v4M9 13h.01M15 13h.01" /></>),
  hash: ico(<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18" />),
  user: ico(<><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>),
  radio: ico(<><circle cx="12" cy="12" r="2" /><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19 5a10 10 0 0 1 0 14M5 19A10 10 0 0 1 5 5" /></>),
  clock: ico(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  calendar: ico(<><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></>),
  activity: ico(<path d="M22 12h-4l-3 9L9 3l-3 9H2" />),
  cpu: ico(<><rect x="5" y="5" width="14" height="14" rx="2" /><path d="M9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" /></>),
  mic: ico(<><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0M12 17v5" /></>),
  speaker: ico(<><path d="M11 5 6 9H2v6h4l5 4z" /><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14" /></>),
  globe: ico(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></>),
  repeat: ico(<path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3" />),
  flag: ico(<path d="M4 22V4a1 1 0 0 1 1-1h11l-2 4 2 4H5" />),
  tag: ico(<><path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z" /><path d="M7.5 7.5h.01" /></>),
  edit: ico(<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></>),
  x: ico(<path d="M18 6 6 18M6 6l12 12" />),
  search: ico(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>),
};

const LANG: Record<string, string> = { ar: "Arabic", en: "English" };
const avg = (xs: (number | null | undefined)[]) => {
  const v = xs.filter((x): x is number => typeof x === "number");
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};
const offset = (from: string, at: string) => {
  const s = Math.max(0, Math.round((new Date(at).getTime() - new Date(from).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
const humanize = (k: string) => k.replace(/[_.]+/g, " ").replace(/^\w/, c => c.toUpperCase());
const show = (v: unknown) => (v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

function Tile({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-line p-3">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-soft text-muted">{icon}</span>
      <div className="min-w-0">
        <div className="text-xs text-muted">{label}</div>
        <div className="mt-0.5 break-words text-sm font-medium">{children}</div>
      </div>
    </div>
  );
}

function Overview({ d, agentName }: { d: CallDetail; agentName: string }) {
  const c = d.call, a = d.agent;
  const turns = d.turns;
  const stt = avg(turns.map(t => t.stt_ms)), llm = avg(turns.map(t => t.llm_first_token_ms));
  const tts = avg(turns.map(t => (t.first_audio_ms != null && t.llm_first_token_ms != null ? Math.max(0, t.first_audio_ms - t.llm_first_token_ms) : null)));
  const total = avg(turns.map(t => t.first_audio_ms));
  const tools = turns.flatMap(t => t.tools).filter(t => !t.cached);
  return (
    <div className="space-y-6">
      <div className="grid gap-2.5 sm:grid-cols-2">
        <Tile icon={T.bot} label="Agent">{agentName}{a?.version ? <span className="ml-1 text-xs font-normal text-muted">v{a.version}</span> : null}</Tile>
        <Tile icon={T.user} label="User Number"><span className="font-mono">{c.mobile ?? "—"}</span></Tile>
        <Tile icon={T.radio} label="Channel"><ChannelBadge channel={c.channel} />{c.extension_call ? <span className="ml-1 text-xs text-muted">extension</span> : null}</Tile>
        <Tile icon={T.clock} label="Duration">{c.duration_s != null ? fmtDuration(c.duration_s) : c.ended_at ? "—" : "in progress"}</Tile>
        <Tile icon={T.calendar} label="Start Time">{fmtWhen(c.started_at)}</Tile>
        <Tile icon={T.activity} label="Status"><StatusBadge status={c.status} /></Tile>
        <Tile icon={T.cpu} label="LLM">{a?.llm.model ?? "—"}{a?.llm.provider ? <div className="text-xs font-normal text-muted">{a.llm.provider}</div> : null}</Tile>
        <Tile icon={T.mic} label="Speech-to-text">{a?.stt.model ?? "—"}{a?.stt.provider ? <div className="text-xs font-normal text-muted">{a.stt.provider}</div> : null}</Tile>
        <Tile icon={T.speaker} label="Voice">{a?.tts.voice ?? "—"}{a?.tts.provider ? <div className="text-xs font-normal text-muted">{a.tts.provider}</div> : null}</Tile>
        <Tile icon={T.globe} label="Language">{LANG[c.language ?? ""] ?? c.language ?? "—"}</Tile>
        <Tile icon={T.repeat} label="Turns">{c.turns}</Tile>
        <Tile icon={T.flag} label="Ended">{c.end_reason ? humanize(c.end_reason) : c.ended_at ? "—" : "not yet"}</Tile>
      </div>
      <section>
        <h3 className="mb-2.5 font-semibold">Performance Metrics</h3>
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          {([["ASR Processing", stt], ["LLM Response", llm], ["TTS Generation", tts], ["Total Processing", total]] as const).map(([k, v]) => (
            <div key={k} className="rounded-lg border border-line p-3">
              <div className="text-xs text-muted">{k}</div>
              <div className="mt-1 text-lg font-semibold tabular-nums">{fmtMs(v)}</div>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted">Averages per turn. Total = caller stops speaking → agent's first audio.
          {tools.length ? ` ${tools.length} tool call${tools.length > 1 ? "s" : ""}, ${fmtMs(avg(tools.map(t => t.ms)))} on average.` : ""}</p>
      </section>
      {turns.length > 0 && (
        <section>
          <h3 className="mb-2.5 font-semibold">Latency per turn</h3>
          <Waterfall turns={turns} />
        </section>
      )}
    </div>
  );
}

function Conversation({ d }: { d: CallDetail }) {
  const [find, setFind] = useState("");
  const [who, setWho] = useState<"all" | "agent" | "user">("all");
  const lines = d.transcript.filter(l => (who === "all" || l.role === who || l.role === "system")
    && (!find || l.text.toLowerCase().includes(find.toLowerCase())));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1">
          <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted">{T.search}</span>
          <input value={find} onChange={e => setFind(e.target.value)} placeholder="Search in transcript…" style={{ paddingLeft: "2rem" }} className="w-full !py-1.5 text-sm" />
        </div>
        <div className="flex rounded-lg border border-line bg-soft p-0.5 text-sm">
          {(["all", "agent", "user"] as const).map(k => (
            <button key={k} onClick={() => setWho(k)} className={cx("rounded-md px-3 py-1 capitalize", who === k ? "bg-panel font-medium shadow-sm" : "text-muted")}>{k}</button>
          ))}
        </div>
      </div>
      {!lines.length ? <div className="py-10 text-center text-sm text-muted">{d.transcript.length ? "Nothing matches." : "No speech recorded."}</div> : (
        <div className="space-y-3">
          {lines.map((l, i) => l.role === "system" ? (
            <div key={i} className="text-center text-xs text-warn">⚑ {humanize(l.text)} · {offset(d.call.started_at, l.ts)}</div>
          ) : (
            <div key={i} className={cx("flex items-end gap-2", l.role === "user" && "flex-row-reverse")}>
              <span className={cx("grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold",
                l.role === "agent" ? "bg-sky-500/15 text-sky-700 dark:text-sky-300" : "bg-accent/25 text-accent-text")}>{l.role === "agent" ? "A" : "U"}</span>
              <div className={cx("max-w-[80%] rounded-2xl px-3.5 py-2 text-sm",
                l.role === "agent" ? "rounded-bl-sm bg-sky-500/10" : "rounded-br-sm bg-accent/15")}>
                <div dir="auto" className="whitespace-pre-wrap">{l.text}</div>
                <div className={cx("mt-1 text-[10px] text-muted", l.role === "user" && "text-right")}>{offset(d.call.started_at, l.ts)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** What the call produced: the outcome flags + the last value of every variable the agent collected. */
function Outcome({ d }: { d: CallDetail }) {
  const c = d.call;
  const slots = useMemo(() => {
    const out = new Map<string, unknown>();
    for (const e of d.events as EventRow[]) {
      const f = e.type === "slot.set" ? (e.data?.field as string | undefined) : undefined;
      if (f) out.set(f, e.data.value);
    }
    return out;
  }, [d.events]);
  // older calls of the one-flow agent only recorded verification as the identity_confirmed variable
  const verified = c.verified || slots.get("identity_confirmed") === true;
  const collected = [...slots.entries()].filter(([k, v]) => !k.includes(":") && k !== "language" && v != null && v !== ""
    && !(typeof v === "object" && !Object.keys(v as object).length));
  const result = c.booked ? "Appointment booked" : c.handoff ? "Forwarded to a human agent" : verified ? "Caller verified, nothing booked"
    : c.ended_at ? "Not resolved" : "In progress";
  const facts: [string, unknown][] = [
    ["Call outcome", result], ["Verified", verified ? "yes" : "no"], ["Booked", c.booked ? "yes" : "no"],
    ...(c.handoff ? [["Hand-off reason", c.handoff] as [string, unknown]] : []),
    ["Language used", LANG[c.language ?? ""] ?? c.language], ["Ended", c.end_reason ? humanize(c.end_reason) : null],
    ["Last skill", c.last_skill],
  ];
  const Card = ({ k, v }: { k: string; v: unknown }) => (
    <div className="group rounded-lg border border-line p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted">{k}</span>
        <span className="opacity-0 group-hover:opacity-100"><CopyButton text={show(v)} /></span>
      </div>
      <div dir="auto" className="mt-0.5 break-words text-sm font-medium">{show(v)}</div>
    </div>
  );
  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2.5 font-semibold">Outcome Details</h3>
        <div className="grid gap-2.5 sm:grid-cols-2">{facts.map(([k, v]) => <Card key={k} k={k} v={v} />)}</div>
      </section>
      <section>
        <h3 className="mb-2.5 font-semibold">Collected during the call</h3>
        {collected.length ? <div className="grid gap-2.5 sm:grid-cols-2">{collected.map(([k, v]) => <Card key={k} k={humanize(k)} v={v} />)}</div>
          : <p className="text-sm text-muted">The agent collected no variables on this call.</p>}
        <p className="mt-2 text-xs text-muted">Values as logged (personal data is masked in logs). A written summary and caller sentiment
          need the post-call analysis step (PLAN 12.9).</p>
      </section>
    </div>
  );
}

export default function CallPanel({ id, onClose, agentName }: { id: string; onClose: () => void; agentName: (id?: string | null) => string }) {
  const [tab, setTab] = useState<Tab>("overview");
  const q = useQuery({ queryKey: ["call", id], queryFn: () => api.call(id), refetchInterval: d => (d.state.data?.call.ended_at ? false : 3000) });
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !e.defaultPrevented) onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  const d = q.data;
  const name = d?.agent?.name ?? agentName(d?.call.agent_id);
  return (
    <aside role="dialog" aria-label="Call information"
      className="fixed top-0 right-0 z-40 flex h-full w-full max-w-[44rem] flex-col border-l border-line bg-panel shadow-2xl xl:w-[45vw]"
      style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}>
      <header className="flex items-start gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-lg font-semibold">Call Information{d ? ` - ${name}` : ""}</h2>
          <div className="mt-0.5 flex items-center gap-1 text-xs text-muted">
            <span>Call ID</span><span className="truncate font-mono text-ink">{id}</span><CopyButton text={id} label="Copy call ID" />
          </div>
        </div>
        {d?.call.agent_id && (
          <Link to={`/agents/${d.call.agent_id}`} className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-soft">{T.edit}Edit Agent</Link>
        )}
        <button onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted hover:bg-soft hover:text-ink">{T.x}</button>
      </header>
      <nav className="grid grid-cols-4 border-b border-line px-5" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
            className={cx("-mb-px border-b-2 py-2.5 text-sm", tab === k ? "border-accent font-medium" : "border-transparent text-muted hover:text-ink")}>{label}</button>
        ))}
      </nav>
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <ErrorBox error={q.error} />
        {!d ? <div className="py-16 text-center text-sm text-muted">{q.isLoading ? "Loading…" : null}</div>
          : tab === "overview" ? <Overview d={d} agentName={name} />
          : tab === "conversation" ? <Conversation d={d} />
          : tab === "outcome" ? <Outcome d={d} />
          : <Timeline events={d.events} />}
      </div>
      <footer className="border-t border-line px-5 py-2 text-[11px] text-muted">
        <span className="inline-flex items-center gap-1">{T.tag}No recording — call audio isn't stored; the transcript and logs are.</span>
      </footer>
    </aside>
  );
}
