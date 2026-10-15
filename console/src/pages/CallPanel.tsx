import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, type CallAnalysis, type CallDetail, type EventRow } from "../api";
import { ErrorBox, cx, fmtMs } from "../ui";
import { Timeline, Waterfall } from "./CallDetail";
import { CopyButton } from "../table";
import { ListenCard } from "../listen";
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
    <div className="flex items-start gap-2.5">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent/10 text-accent">{icon}</span>
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
      <div className="grid grid-cols-2 gap-x-3 gap-y-4">
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
        <div className="grid grid-cols-2 gap-2.5">
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

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** The call's recording, shaped like Hamsa's "Play Recording" bar: play, time, progress, volume and a ⋮ menu (download, delete).
 *  The audio is fetched when play is pressed (each play is logged in the agent's audit log), not when the panel opens. */
function Recording({ d }: { d: CallDetail }) {
  const qc = useQueryClient();
  const r = d.recording;
  const id = d.call.call_id;
  const audio = useRef<HTMLAudioElement | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);
  const [muted, setMuted] = useState(false);
  const [menu, setMenu] = useState(false);
  const total = r?.duration_s ?? 0;
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  const load = useMutation({ mutationFn: () => api.recordingAudio(id), onSuccess: b => setUrl(URL.createObjectURL(b)) });
  const save = useMutation({ mutationFn: () => api.recordingAudio(id, true), onSuccess: b => {
    const u = URL.createObjectURL(b), a = document.createElement("a");
    a.href = u; a.download = `call-${id}.wav`; a.click(); setTimeout(() => URL.revokeObjectURL(u), 1000);
  } });
  const del = useMutation({ mutationFn: () => api.deleteRecording(id), onSuccess: () => { setUrl(null); setPlaying(false); qc.invalidateQueries({ queryKey: ["call", id] }); } });
  if (!r) return null;
  const left = r.expires_at ? Math.max(0, Math.ceil((new Date(r.expires_at).getTime() - Date.now()) / 86400000)) : null;
  const toggle = () => {
    if (!url) { load.mutate(); return; }
    const a = audio.current;
    if (a) { if (a.paused) void a.play(); else a.pause(); }
  };
  return (
    <section>
      <h3 className="mb-3 text-base font-semibold">Play Recording</h3>
      {r.status !== "ok" ? (
        <p className="rounded-xl bg-soft/60 p-3 text-sm text-muted">
          {r.status === "expired" ? "The recording was deleted when its retention period ended." : r.status === "deleted" ? "The recording was deleted."
            : `The recording could not be saved${r.error ? ` (${r.error})` : ""}.`}</p>
      ) : (
        <div className="relative">
          <div className="flex items-center gap-2.5 rounded-xl bg-soft/70 px-3 py-2">
            <button id="recording-play" onClick={toggle} disabled={load.isPending} aria-label={playing ? "Pause" : "Play recording"}
              className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-accent/15 text-accent hover:bg-accent/25 disabled:opacity-50">
              {load.isPending ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
                : playing ? <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="4" width="5" height="16" rx="1" /><rect x="14" y="4" width="5" height="16" rx="1" /></svg>
                : <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15a1 1 0 0 0 1.5.9l12-7.5a1 1 0 0 0 0-1.8l-12-7.5A1 1 0 0 0 7 4.5z" /></svg>}
            </button>
            <span className="shrink-0 text-xs tabular-nums text-muted">{clock(pos)} / {clock(total)}</span>
            <input type="range" aria-label="Seek" min={0} max={total || 1} step={0.1} value={Math.min(pos, total || 1)} disabled={!url}
              onChange={e => { const t = Number(e.target.value); setPos(t); if (audio.current) audio.current.currentTime = t; }}
              className="min-w-0 flex-1 accent-[var(--color-accent)]" />
            <button onClick={() => setMuted(m => !m)} aria-label={muted ? "Unmute" : "Mute"} className="shrink-0 text-muted hover:text-ink">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M11 5 6 9H3v6h3l5 4V5z" />{muted ? <path d="m16 9 5 6m0-6-5 6" /> : <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />}</svg></button>
            <button onClick={() => setMenu(m => !m)} aria-label="More" aria-expanded={menu} className="shrink-0 px-1 text-lg leading-none text-muted hover:text-ink">⋮</button>
          </div>
          {url && <audio ref={audio} src={url} muted={muted} autoPlay className="hidden"
            onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => { setPlaying(false); setPos(0); }}
            onTimeUpdate={e => setPos(e.currentTarget.currentTime)} />}
          {menu && (
            <div className="absolute right-0 z-10 mt-1 w-56 rounded-lg border border-line bg-panel p-1 text-sm shadow-lg">
              <button className="block w-full rounded px-3 py-1.5 text-left hover:bg-soft" disabled={save.isPending} onClick={() => { setMenu(false); save.mutate(); }}>Download</button>
              <button className="block w-full rounded px-3 py-1.5 text-left text-bad hover:bg-bad/10" disabled={del.isPending}
                onClick={() => { setMenu(false); if (window.confirm("Delete this recording now? This can't be undone.")) del.mutate(); }}>Delete recording</button>
              {left != null && <div className="px-3 py-1.5 text-[11px] text-muted">Deleted automatically in {left} day{left === 1 ? "" : "s"}</div>}
            </div>)}
          <div className="mt-2"><ErrorBox error={load.error ?? save.error ?? del.error} /></div>
        </div>
      )}
    </section>
  );
}

const SENTIMENT_TONE = { positive: "bg-amber-500/15 text-amber-700 dark:text-amber-300", neutral: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  negative: "bg-soft text-muted" } as const;

/** The model's reading of the call: summary, sentiment, satisfaction estimates, the agent's own outcome fields. */
function Analysis({ d }: { d: CallDetail }) {
  const qc = useQueryClient();
  const a: CallAnalysis | null | undefined = d.analysis;
  const setup = d.analysis_setup;
  const run = useMutation({ mutationFn: () => api.analyzeCall(d.call.call_id), onSuccess: () => qc.invalidateQueries({ queryKey: ["call", d.call.call_id] }) });
  const ended = !!d.call.ended_at;
  const fields = Object.entries(a?.outcome ?? {});
  return (
    <section>
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <h3 className="font-semibold">Call analysis</h3>
        {ended && <button id="analyze-call" onClick={() => run.mutate()} disabled={run.isPending}
          className="rounded-lg border border-line px-3 py-1 text-xs hover:bg-soft disabled:opacity-50">{run.isPending ? "Analysing…" : a ? "Analyze again" : "Analyze"}</button>}
      </div>
      <ErrorBox error={run.error} />
      {!a ? <div className="space-y-1.5 rounded-lg bg-soft/60 p-3 text-sm text-muted">
          {!ended ? <p>The analysis is made when the call has ended.</p>
            : setup?.in_release ? <p>This call hasn't been analysed (it may have ended before the analysis could run). Press <b>Analyze</b> to run it now.</p>
            : setup?.in_draft ? <p><b className="text-ink">Not published yet.</b> Outcome is switched on in this agent's draft, but this call ran on {setup.release_version ? `version ${setup.release_version}` : "a published version"}, which has no analysis.
              {" "}<Link className="underline" to={`/agents/${setup.agent_id}`}>Publish the agent</Link> and new calls get a summary; press <b>Analyze</b> to get one for this call now.</p>
            : <p><b className="text-ink">Analysis is off for this agent.</b> Open <Link className="underline" to={setup ? `/agents/${setup.agent_id}` : "/agents"}>the agent</Link> → Global Settings → Outcome, switch it on, Save to draft and Publish; new calls then get a summary,
              sentiment and your own fields. Press <b>Analyze</b> to try it on this call.</p>}
        </div>
        : a.status !== "ok" ? <p className="rounded-lg bg-warn/10 p-3 text-sm text-warn">{a.status === "skipped" ? "Skipped" : "Failed"}: {a.error}</p> : (
          <div className="space-y-3">
            {a.summary && <div className="group rounded-lg border border-line p-3">
              <div className="flex items-center justify-between gap-2"><span className="text-xs text-muted">Summary</span>
                <span className="opacity-0 group-hover:opacity-100"><CopyButton text={a.summary} label="Copy summary" /></span></div>
              <div dir="auto" className="mt-0.5 text-sm">{a.summary}</div></div>}
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {a.sentiment && <span className={cx("rounded-full px-2.5 py-0.5 font-medium capitalize", SENTIMENT_TONE[a.sentiment])}>{a.sentiment}</span>}
              {a.csat != null && <span className="rounded-full bg-soft px-2.5 py-0.5">CSAT <b>{a.csat}</b> / 5</span>}
              {a.nps != null && <span className="rounded-full bg-soft px-2.5 py-0.5">NPS <b>{a.nps}</b> / 10</span>}
              {a.resolved != null && <span className="rounded-full bg-soft px-2.5 py-0.5">{a.resolved ? "Resolved by the agent" : "Not resolved"}</span>}
            </div>
            {fields.length > 0 && <div className="grid gap-2.5 ">{fields.map(([k, v]) => (
              <div key={k} className="group rounded-lg border border-line p-3"><div className="flex items-center justify-between gap-2"><span className="text-xs text-muted">{humanize(k)}</span>
                <span className="opacity-0 group-hover:opacity-100"><CopyButton text={v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v)} label={`Copy ${humanize(k)}`} /></span></div>
                <div dir="auto" className="mt-0.5 break-words text-sm font-medium">{v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v)}</div></div>))}</div>}
            <p className="text-[11px] text-muted">Read from the masked transcript by {a.model ?? "the agent's model"}. CSAT and NPS are estimates, not survey answers.</p>
          </div>
        )}
    </section>
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
      <Analysis d={d} />
      <section>
        <h3 className="mb-2.5 font-semibold">Outcome Details</h3>
        <div className="grid gap-2.5 ">{facts.map(([k, v]) => <Card key={k} k={k} v={v} />)}</div>
      </section>
      <section>
        <h3 className="mb-2.5 font-semibold">Collected during the call</h3>
        {collected.length ? <div className="grid gap-2.5 ">{collected.map(([k, v]) => <Card key={k} k={humanize(k)} v={v} />)}</div>
          : <p className="text-sm text-muted">The agent collected no variables on this call.</p>}
        <p className="mt-2 text-xs text-muted">Values as logged (personal data is masked in logs).</p>
      </section>
    </div>
  );
}

/** Hamsa's "send real-time instructions": tell the agent something while the call is running. */
function LiveInstructions({ id, sent }: { id: string; sent: string[] }) {
  const [text, setText] = useState("");
  const [mine, setMine] = useState<string[]>([]);
  const send = useMutation({ mutationFn: () => api.instructCall(id, text.trim()), onSuccess: () => { setMine([...mine, text.trim()]); setText(""); } });
  const shown = [...new Set([...sent, ...mine])];
  return (
    <section className="mt-6 rounded-xl border border-accent/40 bg-accent/5 p-4">
      <h3 className="flex items-center gap-2 font-semibold"><span className="h-2 w-2 animate-pulse rounded-full bg-good" />Live call — instruct the agent</h3>
      <p className="mt-1 text-xs text-muted">The agent follows your instruction from its next reply on and never reads it out. It stays in force for the rest of the call.</p>
      <form className="mt-3 flex gap-2" onSubmit={e => { e.preventDefault(); if (text.trim()) send.mutate(); }}>
        <input id="call-instruction" aria-label="Instruction for the agent" maxLength={500} className="min-w-0 flex-1 text-sm" placeholder="Send instruction to agent…" value={text} onChange={e => setText(e.target.value)} />
        <button type="submit" disabled={!text.trim() || send.isPending} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-50">{send.isPending ? "Sending…" : "Send"}</button>
      </form>
      <div className="mt-2"><ErrorBox error={send.error} /></div>
      {shown.length > 0 && <ul className="mt-3 space-y-1 text-xs">{shown.map((s, i) => <li key={i} className="rounded bg-panel px-2 py-1">{s}</li>)}</ul>}
    </section>
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
      className="fixed top-14 right-0 bottom-0 z-30 flex w-full flex-col border-l border-line bg-panel shadow-2xl md:w-[22rem] md:shadow-none">
      <header className="flex items-start gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-base font-semibold">Call Information{d ? ` - ${name}` : ""}</h2>
          <div className="mt-0.5 flex items-center gap-1 text-xs text-muted">
            <span className="shrink-0 whitespace-nowrap">Call ID:</span><span className="truncate text-ink">{id}</span><CopyButton text={id} label="Copy call ID" />
          </div>
        </div>
        {d?.call.agent_id && (
          <Link to={`/agents/${d.call.agent_id}`} className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-soft">{T.edit}Edit Agent</Link>
        )}
        <button onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted hover:bg-soft hover:text-ink">{T.x}</button>
      </header>
      {d?.recording && <div className="border-b border-line px-5 py-4"><Recording d={d} /></div>}
      <nav className="grid grid-cols-4 border-b border-line px-5" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
            className={cx("-mb-px border-b-2 py-2.5 text-sm", tab === k ? "border-accent font-medium" : "border-transparent text-muted hover:text-ink")}>{label}</button>
        ))}
      </nav>
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <ErrorBox error={q.error} />
        {!d ? <div className="py-16 text-center text-sm text-muted">{q.isLoading ? "Loading…" : null}</div>
          : tab === "overview" ? <><Overview d={d} agentName={name} />{!d.call.ended_at && d.call.status === "in_progress" && <div className="mt-6"><ListenCard callId={id} /></div>}{!d.call.ended_at && d.call.status === "in_progress" && <LiveInstructions id={id} sent={d.events.filter(e => e.type === "slot.set" && e.data?.field === "supervisor_instruction").map(e => String(e.data.value))} />}</>
          : tab === "conversation" ? <Conversation d={d} />
          : tab === "outcome" ? <Outcome d={d} />
          : <Timeline events={d.events} />}
      </div>
      <footer className="border-t border-line px-5 py-2 text-[11px] text-muted">
        <span className="inline-flex items-center gap-1">{T.tag}{d?.recording ? "Recordings are stored encrypted; every play is logged." : "This call was not recorded — the transcript and logs are kept."}</span>
      </footer>
    </aside>
  );
}
