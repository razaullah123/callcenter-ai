import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, type AgentVoices, type CatalogVoice } from "../api";
import { Button, cx } from "../ui";

// Voices (like Hamsa's): the voice library of the TTS providers this project uses — All voices / Favorite voices /
// Currently used, search + filters, a card per voice with ▶ sample and ☆ favourite. Change an agent's voice in its
// Settings tab; the sample text can be your own.

const LANG: Record<string, string> = { ar: "Arabic", en: "English" };
const FAV_KEY = "hmg-console-favorite-voices";
const loadFav = (): string[] => { try { return JSON.parse(localStorage.getItem(FAV_KEY) ?? "[]"); } catch { return []; } };
const keyOf = (v: CatalogVoice) => `${v.provider}:${v.language}:${v.voice}`;
const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function play(b64: string) {
  new Audio(`data:audio/wav;base64,${b64}`).play().catch(() => { /* autoplay blocked: click again */ });
}

function UseVoiceDialog({ v, agents, onClose }: { v: CatalogVoice; agents: AgentVoices[]; onClose: () => void }) {
  const qc = useQueryClient();
  const fits = agents.filter(a => a.provider === v.provider && a.languages.includes(v.language));
  const [agent, setAgent] = useState(fits[0]?.agent ?? "");
  const pick = fits.find(a => a.agent === agent);
  const current = pick ? (pick.draft_voices[v.language] ?? pick.voices[v.language]?.voice) : null;
  const use = useMutation({
    mutationFn: () => api.useVoice({ agent, language: v.language, voice: v.voice }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["voice-catalog"] }); qc.invalidateQueries({ queryKey: ["voices"] });
                       qc.invalidateQueries({ queryKey: ["agent", agent] }); qc.invalidateQueries({ queryKey: ["agents"] }); },
  });
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[18vh]" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-md rounded-xl border border-line bg-panel p-4 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-base font-semibold">Use {title(v.voice)} ({LANG[v.language] ?? v.language})</div>
          <button className="text-muted hover:text-ink" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {use.data ? (
          <div className="space-y-3 text-sm">
            <p>✓ <b>{title(v.voice)}</b> is now the {LANG[v.language]} voice of <b>{pick?.name}</b>'s draft
              {use.data.previous && use.data.previous !== v.voice ? <> (was {title(use.data.previous)})</> : null}.</p>
            <p className="text-muted">Callers still hear the live version until you publish the agent (its test checks run first).</p>
            <div className="flex justify-end gap-2"><Button kind="ghost" onClick={onClose}>Close</Button>
              <Link to={`/agents/${agent}`} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90">Open agent to publish →</Link></div>
          </div>
        ) : !fits.length ? (
          <p className="text-sm text-muted">No agent in this project speaks {LANG[v.language]} with {v.provider}. Choose this voice in an agent's Settings tab instead.</p>
        ) : (
          <form className="space-y-3" onSubmit={e => { e.preventDefault(); use.mutate(); }}>
            <label className="block"><div className="mb-1 text-xs font-medium">Agent</div>
              <select id="use-voice-agent" className="w-full" value={agent} onChange={e => setAgent(e.target.value)}>
                {fits.map(a => <option key={a.agent} value={a.agent}>{a.name}</option>)}
              </select></label>
            <p className="text-xs text-muted">Its {LANG[v.language]} voice now: <b>{current ? title(current) : "—"}</b>.
              The change goes into the agent's <b>draft</b>; publish the agent to make it live.</p>
            {use.error && <div className="text-xs text-bad">{use.error instanceof ApiError ? use.error.message : String(use.error)}</div>}
            <div className="flex justify-end gap-2">
              <Button kind="ghost" onClick={onClose}>Cancel</Button>
              <Button kind="primary" type="submit" disabled={!agent || current === v.voice || use.isPending}>
                {current === v.voice ? "Already this voice" : use.isPending ? "Saving…" : "Use this voice"}</Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function VoiceCard({ v, fav, onFav, onPlay, onUse, busy }: {
  v: CatalogVoice; fav: boolean; onFav: () => void; onPlay: () => void; onUse: () => void; busy: boolean;
}) {
  const female = v.gender === "female";
  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-panel p-3">
      <span className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accent/20 text-base font-semibold text-accent-text">
        {v.voice[0].toUpperCase()}
        <span className={cx("absolute -bottom-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full text-[9px] text-white",
          female ? "bg-pink-500" : "bg-sky-600")} title={female ? "Female" : "Male"}>{female ? "♀" : "♂"}</span>
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate font-semibold">{title(v.voice)}</div>
        <div className="truncate text-[11px] text-muted">{v.style ?? "Conversational"}</div>
        <div className="truncate text-[11px] text-muted">{v.dialect}</div>
        {v.used_by.length > 0 && <div className="mt-0.5 truncate text-[10px]">
          <span className="rounded bg-accent/20 px-1 font-medium text-accent-text">in use</span>{" "}
          {v.used_by.map(u => <Link key={u.agent} to={`/agents/${u.agent}`} className="text-muted hover:underline">{u.name}</Link>)}</div>}
        {v.draft_by.length > 0 && <div className="mt-0.5 truncate text-[10px]">
          <span className="rounded bg-warn/15 px-1 font-medium text-warn">in draft</span>{" "}
          {v.draft_by.map(u => <Link key={u.agent} to={`/agents/${u.agent}`} className="text-muted hover:underline">{u.name}</Link>)}</div>}
        <button onClick={onUse} className="mt-1 text-[11px] font-medium text-accent-text hover:underline">Use this voice</button>
      </div>
      <button aria-label={fav ? "Remove from favorites" : "Add to favorites"} title={fav ? "Favorite" : "Add to favorites"} onClick={onFav}
        className={cx("text-lg leading-none", fav ? "text-amber-500" : "text-line hover:text-muted")}>★</button>
      <button aria-label={`Play ${v.voice}`} title="Play a sample" onClick={onPlay} disabled={busy}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line hover:bg-soft disabled:opacity-50">
        {busy ? <span className="animate-pulse">•</span> : "▶"}</button>
    </div>
  );
}

export default function Voices() {
  const q = useQuery({ queryKey: ["voice-catalog"], queryFn: api.voiceCatalog });
  const agents = useQuery({ queryKey: ["voices"], queryFn: api.voices });
  const [using, setUsing] = useState<CatalogVoice | null>(null);
  const [tab, setTab] = useState<"all" | "fav" | "used">("all");
  const [search, setSearch] = useState("");
  const [f, setF] = useState({ language: "", gender: "", dialect: "" });
  const [fav, setFav] = useState<string[]>(loadFav);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const preview = useMutation({
    mutationFn: (v: CatalogVoice) => {
      setBusy(keyOf(v)); setErr("");
      const agent = v.used_by[0]?.agent;
      return api.voicePreview({ agent, language: v.language, voice: v.voice, text: text.trim() || undefined });
    },
    onSuccess: r => { setBusy(null); play(r.audio); },
    onError: e => { setBusy(null); setErr(e instanceof ApiError ? e.message : String(e)); },
  });
  const toggleFav = (v: CatalogVoice) => setFav(list => {
    const k = keyOf(v), next = list.includes(k) ? list.filter(x => x !== k) : [...list, k];
    try { localStorage.setItem(FAV_KEY, JSON.stringify(next)); } catch { /* private mode */ }
    return next;
  });
  const all = q.data ?? [];
  const dialects = useMemo(() => [...new Set(all.map(v => v.dialect))], [all]);
  const shown = all.filter(v => (tab === "all" || (tab === "fav" ? fav.includes(keyOf(v)) : v.used_by.length + v.draft_by.length > 0))
    && (!search || v.voice.includes(search.toLowerCase().trim()))
    && (!f.language || v.language === f.language) && (!f.gender || v.gender === f.gender) && (!f.dialect || v.dialect === f.dialect));
  const TABS = [["all", "All voices"], ["fav", "Favorite voices"], ["used", "Currently used"]] as const;
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Voices</h1>
        <p className="text-sm text-muted">Listen to the voices your agents can speak with. Pick one for an agent in its Settings tab.</p>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {TABS.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={cx("rounded-lg py-2.5 text-sm font-medium", tab === k ? "bg-accent text-accent-fg" : "text-muted hover:bg-soft hover:text-ink")}>{l}</button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-xs text-muted">Filter Result {shown.length} Voice{shown.length === 1 ? "" : "s"}</span>
        <input id="voice-search" className="w-48 !py-1.5 text-sm" placeholder="Search by name" value={search} onChange={e => setSearch(e.target.value)} />
        <select id="voice-language" aria-label="Language" className="!py-1.5 text-sm" value={f.language} onChange={e => setF({ ...f, language: e.target.value })}>
          <option value="">Language</option><option value="ar">Arabic</option><option value="en">English</option></select>
        <select id="voice-gender" aria-label="Gender" className="!py-1.5 text-sm" value={f.gender} onChange={e => setF({ ...f, gender: e.target.value })}>
          <option value="">Gender</option><option value="female">Female</option><option value="male">Male</option></select>
        <select id="voice-dialect" aria-label="Dialect" className="!py-1.5 text-sm" value={f.dialect} onChange={e => setF({ ...f, dialect: e.target.value })}>
          <option value="">Dialect</option>{dialects.map(d => <option key={d} value={d}>{d}</option>)}</select>
        {(search || f.language || f.gender || f.dialect) &&
          <button className="text-xs text-accent-text hover:underline" onClick={() => { setSearch(""); setF({ language: "", gender: "", dialect: "" }); }}>Reset</button>}
        <input id="voice-text" dir="auto" className="ml-auto w-72 !py-1.5 text-sm" placeholder="Your own sample text (optional)" value={text} onChange={e => setText(e.target.value)} />
      </div>
      {err && <div className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">{err}</div>}
      {q.isLoading ? <div className="text-sm text-muted">Loading…</div> : q.error
        ? <div className="text-sm text-bad">{q.error instanceof Error ? q.error.message : String(q.error)}</div>
        : !shown.length ? <div className="rounded-xl border border-line py-12 text-center text-sm text-muted">
            {tab === "fav" ? "No favorite voices yet — ★ a voice to keep it here." : !all.length ? "None of this project's voice services publishes a voice list." : "No voice matches these filters."}</div> : (
          <>
            {(["ar", "en"] as const).filter(l => shown.some(v => v.language === l)).map(l => (
              <div key={l}>
                <div className="mb-2 text-xs font-medium text-muted">{LANG[l]}</div>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {shown.filter(v => v.language === l).map(v => (
                    <VoiceCard key={keyOf(v)} v={v} fav={fav.includes(keyOf(v))} onFav={() => toggleFav(v)}
                      onPlay={() => preview.mutate(v)} onUse={() => setUsing(v)} busy={busy === keyOf(v)} />
                  ))}
                </div>
              </div>
            ))}
          </>)}
      {using && <UseVoiceDialog v={using} agents={agents.data ?? []} onClose={() => setUsing(null)} />}
      <p className="text-[11px] text-muted">Voices come from the voice service your agents use ({[...new Set(all.map(v => v.provider))].join(", ") || "—"}).
        Samples play the sentence above, or a short greeting in the voice's language.</p>
    </div>
  );
}
