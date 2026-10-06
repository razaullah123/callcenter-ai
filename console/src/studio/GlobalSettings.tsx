import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, type AgentDetail, type Bundle } from "../api";
import { Badge, Button, ErrorBox, cx } from "../ui";

// The flow canvas's ⚙ panel, laid out like Hamsa's "Global Settings": one collapsible section per area — system
// prompt, voice, LLM, noise, knowledge base, tools, outcome, phone number, call settings, webhook. Everything edits the
// agent's draft (callers get it after Publish); one Save at the bottom.

const ico = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
);
const IC = {
  prompt: ico(<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></>),
  voice: ico(<><path d="M11 5 6 9H2v6h4l5 4z" /><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14" /></>),
  llm: ico(<><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M12 8V4M8 14h.01M16 14h.01M2 14h2M20 14h2" /></>),
  noise: ico(<><path d="M2 12h2M6 8v8M10 5v14M14 9v6M18 7v10M22 12h-2" /></>),
  book: ico(<><path d="M12 7v14" /><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" /></>),
  tools: ico(<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />),
  outcome: ico(<><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></>),
  phone: ico(<path d="M13.832 16.568a1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 6.392 6.384" />),
  call: ico(<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></>),
  hook: ico(<><path d="M18 16.98h-5.99c-1.1 0-1.95.94-2.48 1.9A4 4 0 0 1 2 17c.01-.7.2-1.4.57-2" /><path d="m6 17 3.13-5.78c.53-.97.1-2.18-.5-3.1a4 4 0 1 1 6.89-4.06" /><path d="m12 6 3.13 5.73C15.66 12.7 16.9 13 18 13a4 4 0 0 1 0 8" /></>),
  chev: ico(<path d="m6 9 6 6 6-6" />),
  chevR: ico(<path d="m9 6 6 6-6 6" />),
  expand: ico(<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />),
};

/** Human names for the per-agent knobs (also used by the Settings tab). */
export const KNOB_LABELS: Record<string, [string, string]> = {
  voice_end_silence_ms: ["Response delay (ms)", "Silence after the caller stops talking before the agent answers"],
  voice_barge_in_ms: ["Minimum interruption (ms)", "Caller speech needed to interrupt the agent"],
  voice_barge_in_confirm: ["Interrupt only for real words", "Transcribe the interruption first: ignore echo, coughs and noise"],
  voice_interrupt: ["Interrupt", "The caller can interrupt the agent while it speaks; off: the agent always finishes"],
  voice_level_gate_db: ["Background voice filter (dB)", "Ignore speech this much quieter than the caller (TV, people nearby); 0 = off"],
  voice_vad_threshold: ["VAD activation threshold", "Speech probability that counts as speech — higher is less sensitive to noise"],
  voice_filler_after_s: ["Thinking voice after (s)", "Say a short filler (“one moment…”) when the answer takes longer than this"],
  voice_inactivity_s: ["User inactivity timeout (s)", "Ask “are you still there?” after this much silence; 0 = off"],
  call_max_minutes: ["Max call duration (min)", "End the call with a closing line after this long; 0 = no limit"],
  llm_hedge_after_s: ["Backup LLM request after (s)", "No first token by then → race an identical request; 0 = off"],
  require_verification: ["Caller verification", "Verify the caller (mobile + code) before anything else"],
  red_flag_mode: ["Serious symptoms", "What the agent does when a caller describes an emergency"],
  ivr_transfer_destination: ["Human transfer destination", "Extension / queue for “talk to a person”"],
  ivr_barge_in_grace_ms: ["Phone: echo grace (ms)", "No interruptions right after the agent starts talking on phone lines"],
  ivr_chunk_ms: ["Phone: audio chunk (ms)", "Size of the audio pieces sent to the phone line"],
  entry_skill: ["Entry skill", "Where the conversation starts (after verification)"],
  main_flow: ["Main flow", "One flow graph runs the whole call"],
};

// Known models per provider type (any other name can be typed with "Custom…").
const LLM_MODELS: Record<string, [string, string][]> = {
  groq: [["openai/gpt-oss-120b", "Strongest on Groq; best for complex flows."], ["openai/gpt-oss-20b", "Fast and cheaper; good for simple agents."],
         ["llama-3.3-70b-versatile", "Reliable and balanced."], ["moonshotai/kimi-k2-instruct", "Strong tool use, longer replies."],
         ["llama-3.1-8b-instant", "Ultra-fast, low cost; simple tasks."]],
  openai_compatible: [["gpt-4.1", "Reliable and balanced for most agents."], ["gpt-4.1-mini", "Quicker responses with solid quality."],
                      ["gpt-4.1-nano", "Lightweight; simple, repetitive tasks."], ["gpt-4o", "Most natural, human-like conversations."],
                      ["gpt-4o-mini", "Fast, smooth chat for customer-facing agents."]],
};
const STT_MODELS: Record<string, [string, string][]> = {
  groq: [["whisper-large-v3", "Most accurate Arabic / English."], ["whisper-large-v3-turbo", "Faster, slightly less accurate."]],
  openai_compatible: [["whisper-1", "OpenAI Whisper."], ["gpt-4o-transcribe", "Higher accuracy."], ["gpt-4o-mini-transcribe", "Fast, low cost."]],
};

function Section({ id, icon, title, sub, open, onToggle, children, badge }: {
  id: string; icon: ReactNode; title: string; sub: string; open: boolean; onToggle: (id: string) => void; children: ReactNode; badge?: ReactNode;
}) {
  return (
    <section className="border-b border-line">
      <button onClick={() => onToggle(id)} aria-expanded={open} className="flex w-full items-start gap-3 px-4 py-3.5 text-left hover:bg-soft/50">
        <span className="mt-0.5 text-muted">{icon}</span>
        <span className="min-w-0 flex-1"><span className="flex items-center gap-2 font-semibold">{title}{badge}</span>
          <span className="block text-xs text-muted">{sub}</span></span>
        <span className="mt-1 text-muted">{open ? IC.chev : IC.chevR}</span>
      </button>
      {open && <div className="space-y-4 px-4 pb-5">{children}</div>}
    </section>
  );
}

function Row({ title, help, children }: { title: string; help?: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-line p-3">
      <div className="min-w-0"><div className="text-sm font-medium">{title}</div>{help && <div className="text-[11px] text-muted">{help}</div>}</div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function Switch({ id, on, onChange }: { id: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button id={id} role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className={cx("relative h-5 w-9 rounded-full transition", on ? "bg-accent" : "bg-line")}>
      <span className={cx("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all", on ? "left-[18px]" : "left-0.5")} />
    </button>
  );
}

function Slider({ id, title, help, value, min, max, step, unit, fmt, onChange }: {
  id: string; title: string; help: string; value: number; min: number; max: number; step: number; unit?: string;
  fmt?: (v: number) => string; onChange: (v: number) => void;
}) {
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex items-center justify-between"><label htmlFor={id} className="text-sm font-medium">{title}</label>
        <span className="text-sm tabular-nums">{fmt ? fmt(value) : value}{unit ? ` ${unit}` : ""}</span></div>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(Number(e.target.value))}
        className="mt-2 w-full !border-0 !p-0 accent-[var(--color-accent)]" />
      <div className="text-[11px] text-muted">{help}</div>
    </div>
  );
}

function ModelPicker({ id, label, models, value, onChange }: { id: string; label: string; models: [string, string][]; value: string; onChange: (v: string) => void }) {
  const known = models.some(([m]) => m === value);
  const [custom, setCustom] = useState(!known && !!value);
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium">{label}</label>
      <select id={id} className="w-full" value={custom ? "__custom" : value} onChange={e => {
        if (e.target.value === "__custom") { setCustom(true); return; }
        setCustom(false); onChange(e.target.value);
      }}>
        {!value && !custom && <option value="">— provider default —</option>}
        {models.map(([m, d]) => <option key={m} value={m}>{m} — {d}</option>)}
        <option value="__custom">Custom…</option>
      </select>
      {custom && <input aria-label={`${label} name`} className="mt-2 w-full font-mono text-xs" placeholder="model name" value={value} onChange={e => onChange(e.target.value)} />}
      {!custom && models.find(([m]) => m === value) && <div className="mt-1 text-[11px] text-muted">{models.find(([m]) => m === value)![1]}</div>}
    </div>
  );
}

function PromptDialog({ title, value, onChange, onClose }: { title: string; value: string; onChange: (v: string) => void; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-label={title} className="flex h-[85vh] w-full max-w-5xl flex-col rounded-xl border border-line bg-panel shadow-xl">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="font-semibold">{title}</h2>
          <span className="text-xs text-muted">{value.length.toLocaleString()} characters · {value.split("\n").length} lines</span>
          <Button onClick={onClose}>Done</Button>
        </div>
        <textarea autoFocus dir="auto" spellCheck={false} className="min-h-0 flex-1 resize-none !rounded-none !border-0 p-5 font-mono text-[13px] leading-relaxed"
          value={value} onChange={e => onChange(e.target.value)} />
      </div>
    </div>
  );
}

export default function GlobalSettings({ d, skill, onSaved, onClose }: {
  d: AgentDetail; skill: string; onSaved: () => void; onClose?: () => void;
}) {
  const persona = d.skills._persona?.files["SKILL.md"] ?? "";
  const flowText = d.skills[skill]?.files["SKILL.md"] ?? "";
  const [p, setP] = useState(persona);
  const [f, setF] = useState(flowText);
  const [b, setB] = useState<Bundle>(() => structuredClone(d.bundle));
  const [open, setOpen] = useState<Set<string>>(new Set(["prompt"]));
  const [editing, setEditing] = useState<"persona" | "flow" | null>(null);
  const catalog = useQuery({ queryKey: ["voice-catalog"], queryFn: api.voiceCatalog });
  const kb = useQuery({ queryKey: ["knowledge"], queryFn: api.knowledge });
  const toggle = (id: string) => setOpen(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const bundleDirty = JSON.stringify(b) !== JSON.stringify(d.bundle);
  const dirty = bundleDirty || p !== persona || f !== flowText;
  const save = useMutation({
    mutationFn: async () => {
      if (p !== persona) await api.putDraftSkill(d.agent.id, "_persona", { files: { "SKILL.md": p }, note: "system prompt (global settings)" });
      if (f !== flowText) await api.putDraftSkill(d.agent.id, skill, { files: { ...d.skills[skill].files, "SKILL.md": f }, note: "flow prompt (global settings)" });
      if (bundleDirty) await api.putDraft(d.agent.id, b);
    },
    onSuccess: onSaved,
  });
  const knob = <T,>(k: string, dflt: T): T => (b.knobs[k] ?? dflt) as T;
  const setKnob = (k: string, v: unknown) => setB(x => ({ ...x, knobs: { ...x.knobs, [k]: v } }));
  const model = (kind: string) => b.models[kind] ?? {};
  const typeOf = (kind: string) => d.connections.find(c => c.id === model(kind).provider)?.type ?? model(kind).type ?? "groq";
  const setModel = (kind: string, patch: Record<string, unknown>) =>
    setB(x => ({ ...x, models: { ...x.models, [kind]: { ...x.models[kind], settings: { ...(x.models[kind]?.settings ?? {}), ...patch } } } }));
  const setConn = (kind: string, id: string) => setB(x => ({ ...x, models: { ...x.models, [kind]: { provider: id, settings: {} } } }));
  const greeting = (b.phrases.GREETING ?? {}) as Record<string, string>;
  const langs = b.agent?.languages?.length ? b.agent.languages : ["ar", "en"];
  const kbItems = b.knowledge?.items ?? [];
  const connSelect = (kind: string) => (
    <div>
      <label htmlFor={`gs-conn-${kind}`} className="mb-1 block text-sm font-medium">Provider</label>
      <select id={`gs-conn-${kind}`} className="w-full" value={model(kind).provider ?? ""} onChange={e => setConn(kind, e.target.value)}>
        {!model(kind).provider && <option value="">{model(kind).type ?? "choose a connection"}</option>}
        {d.connections.filter(c => c.kind === kind).map(c => <option key={c.id} value={c.id}>{c.name} · {c.type}</option>)}
      </select>
      <div className="mt-1 text-[11px] text-muted">Keys and URLs live on the <Link className="underline" to="/connections">connection</Link>.</div>
    </div>
  );
  const voices = (lang: string) => (catalog.data ?? []).filter(v => v.language === lang);
  const tts = (model("tts").settings ?? {}) as Record<string, unknown>;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-lg font-semibold">Global Settings</h2>
        {onClose && <button onClick={onClose} aria-label="Close" className="rounded p-1 text-muted hover:bg-soft hover:text-ink">✕</button>}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <Section id="prompt" icon={IC.prompt} title="System prompt" sub="Configure agent behavior and responses." open={open.has("prompt")} onToggle={toggle}>
          <div>
            <div className="mb-1 flex items-center justify-between"><span className="text-sm font-medium">System prompt <span className="text-bad">*</span></span>
              <button onClick={() => setEditing("persona")} className="flex items-center gap-1 text-xs text-accent-text hover:underline">{IC.expand}Open editor</button></div>
            <textarea id="global-persona" rows={7} dir="auto" className="w-full font-mono text-[11px]" value={p} onChange={e => setP(e.target.value)} />
            <div className="text-[11px] text-muted">The whole agent: who it is, tone, language rules, what it never does. ## ar / ## en sections per language; {"{{ }}"} templates allowed.</div>
          </div>
          {d.skills[skill] && <div>
            <div className="mb-1 flex items-center justify-between"><span className="text-sm font-medium">Flow prompt · <span className="font-mono">{skill}</span></span>
              <button onClick={() => setEditing("flow")} className="flex items-center gap-1 text-xs text-accent-text hover:underline">{IC.expand}Open editor</button></div>
            <textarea id="global-flow" rows={5} dir="auto" className="w-full font-mono text-[11px]" value={f} onChange={e => setF(e.target.value)} />
            <div className="text-[11px] text-muted">Shared rules for every node of this flow, so they aren't repeated in each node.</div>
          </div>}
          <div>
            <div className="mb-1 text-sm font-medium">Greeting message</div>
            {langs.map(l => (
              <input key={l} aria-label={`Greeting (${l})`} dir={l === "ar" ? "rtl" : "ltr"} className="mb-2 w-full" placeholder={`default greeting (${l})`}
                value={greeting[l] ?? ""} onChange={e => setB(x => ({ ...x, phrases: { ...x.phrases, GREETING: { ...greeting, [l]: e.target.value } } }))} />
            ))}
            <div className="text-[11px] text-muted">Said word for word when the call starts. Empty: the platform default.</div>
          </div>
        </Section>

        <Section id="voice" icon={IC.voice} title="Voice Settings" sub="Configure voice, language, and speech settings." open={open.has("voice")} onToggle={toggle}>
          {connSelect("tts")}
          {langs.map(l => {
            const k = `voice_${l}`, cur = String(tts[k] ?? ""), list = voices(l);
            return (
              <div key={l}>
                <label htmlFor={`gs-voice-${l}`} className="mb-1 block text-sm font-medium">Voice · {l === "ar" ? "Arabic" : "English"}</label>
                {list.length ? (
                  <select id={`gs-voice-${l}`} className="w-full" value={cur} onChange={e => setModel("tts", { [k]: e.target.value })}>
                    {!list.some(v => v.voice === cur) && <option value={cur}>{cur || "— provider default —"}</option>}
                    {list.map(v => <option key={`${v.provider}-${v.voice}`} value={v.voice}>{v.voice} · {v.gender}{v.dialect ? ` · ${v.dialect}` : ""}</option>)}
                  </select>
                ) : <input id={`gs-voice-${l}`} className="w-full" value={cur} onChange={e => setModel("tts", { [k]: e.target.value })} />}
              </div>
            );
          })}
          <div className="text-[11px] text-muted">Listen to voices and compare them on the <Link className="underline" to="/voices">Voices</Link> page.</div>
          <div className="border-t border-line pt-4">{connSelect("stt")}</div>
          <ModelPicker id="gs-stt-model" label="STT model" models={STT_MODELS[typeOf("stt")] ?? []}
            value={String(model("stt").settings?.model ?? "")} onChange={v => setModel("stt", { model: v })} />
          <div>
            <div className="mb-1 text-sm font-medium">Vocabulary hint</div>
            {langs.map(l => (
              <input key={l} aria-label={`Vocabulary hint (${l})`} dir={l === "ar" ? "rtl" : "ltr"} className="mb-2 w-full" value={b.voice?.stt_hint?.[l] ?? ""}
                onChange={e => setB(x => ({ ...x, voice: { ...x.voice, stt_hint: { ...x.voice?.stt_hint, [l]: e.target.value } } }))} />
            ))}
            <div className="text-[11px] text-muted">Names callers say (hospitals, places) so speech-to-text spells them right. Keep it short.</div>
          </div>
        </Section>

        <Section id="llm" icon={IC.llm} title="LLM Configuration" sub="Configure AI model and provider settings." open={open.has("llm")} onToggle={toggle}>
          {connSelect("llm")}
          <ModelPicker id="gs-llm-model" label="Model" models={LLM_MODELS[typeOf("llm")] ?? []}
            value={String(model("llm").settings?.model ?? "")} onChange={v => setModel("llm", { model: v })} />
          <Slider id="gs-temp" title="Temperature" help="Lower: steadier, more predictable answers. Higher: more varied wording."
            value={Number(model("llm").settings?.temperature ?? 0.3)} min={0} max={1} step={0.05} fmt={v => v.toFixed(2)}
            onChange={v => setModel("llm", { temperature: v })} />
          <Slider id="gs-max-tokens" title="Max reply length" help="Longest answer the model may write (tokens). Voice replies are short."
            value={Number(model("llm").settings?.max_tokens ?? 400)} min={100} max={1500} step={50} unit="tokens"
            onChange={v => setModel("llm", { max_tokens: v })} />
        </Section>

        <Section id="noise" icon={IC.noise} title="Noise Cancellation" sub="Configure noise settings to improve call quality." open={open.has("noise")} onToggle={toggle}>
          <Row title={KNOB_LABELS.voice_barge_in_confirm[0]} help={KNOB_LABELS.voice_barge_in_confirm[1]}>
            <Switch id="gs-barge-confirm" on={knob("voice_barge_in_confirm", true)} onChange={v => setKnob("voice_barge_in_confirm", v)} /></Row>
          <Slider id="gs-level-gate" title={KNOB_LABELS.voice_level_gate_db[0]} help={KNOB_LABELS.voice_level_gate_db[1]}
            value={knob("voice_level_gate_db", 12)} min={0} max={30} step={1} unit="dB" onChange={v => setKnob("voice_level_gate_db", v)} />
          <Slider id="gs-vad" title={KNOB_LABELS.voice_vad_threshold[0]} help={KNOB_LABELS.voice_vad_threshold[1]}
            value={knob("voice_vad_threshold", 0.5)} min={0.2} max={0.9} step={0.05} fmt={v => v.toFixed(2)} onChange={v => setKnob("voice_vad_threshold", v)} />
          <div className="text-[11px] text-muted">Phone lines also get echo cancellation (server setting).</div>
        </Section>

        <Section id="kb" icon={IC.book} title="Knowledge Base" sub="Provide domain-specific information to improve responses." open={open.has("kb")} onToggle={toggle}
          badge={kbItems.length ? <Badge>{kbItems.length}</Badge> : undefined}>
          {!(kb.data?.items ?? []).length ? <p className="text-sm text-muted">No knowledge base items in this project yet — <Link className="underline" to="/knowledge-base">add some</Link>.</p> : (
            <div className="space-y-1">
              {(kb.data?.items ?? []).map(i => (
                <label key={i.id} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-soft">
                  <input type="checkbox" checked={kbItems.includes(i.id)} className="accent-[var(--color-accent)]"
                    onChange={e => {
                      const items = e.target.checked ? [...kbItems, i.id] : kbItems.filter(x => x !== i.id);
                      setB(x => { const { knowledge: _k, ...rest } = x; void _k; return items.length ? { ...rest, knowledge: { items } } as Bundle : rest as Bundle; });
                    }} />
                  <span className="truncate">{i.name}</span><span className="ml-auto text-[11px] text-muted">{i.words.toLocaleString()} words</span>
                </label>
              ))}
            </div>
          )}
          <div className="text-[11px] text-muted">With items chosen, the agent searches them (“search the knowledge base”) in every step.</div>
        </Section>

        <Section id="tools" icon={IC.tools} title="Tools" sub="Connect to external services and APIs." open={open.has("tools")} onToggle={toggle}
          badge={<Badge>{d.tools.length}</Badge>}>
          <div className="flex flex-wrap gap-1">{d.tools.map(t => <Link key={t} to={`/tools?tool=${encodeURIComponent(t)}`} className="rounded border border-line bg-soft px-1.5 py-0.5 font-mono text-[11px] hover:border-accent">{t}</Link>)}</div>
          <div className="text-[11px] text-muted">Flow nodes call these. Add and configure them in <Link className="underline" to="/tools">Tools Templates</Link>.</div>
        </Section>

        <Section id="outcome" icon={IC.outcome} title="Outcome" sub="Define post-conversation processing prompts." open={open.has("outcome")} onToggle={toggle}
          badge={<span className="rounded bg-soft px-1.5 text-[10px] font-medium text-muted">Soon</span>}>
          <p className="text-sm text-muted">Post-call analysis (a summary, satisfaction and your own fields extracted from each call) is planned (12.9).
            Today each call records its outcome — booked, verified, handed to a person — in <Link className="underline" to="/calls">Call history</Link>.</p>
        </Section>

        <Section id="phone" icon={IC.phone} title="Phone Number" sub="Assign a phone number for calls." open={open.has("phone")} onToggle={toggle}
          badge={d.routes.length ? <Badge>{d.routes.length}</Badge> : undefined}>
          {d.routes.length ? (
            <div className="space-y-1">{d.routes.map(r => (
              <div key={r.pattern} className="flex items-center justify-between rounded-lg border border-line px-3 py-2 text-sm">
                <span className="font-mono">{r.pattern}</span><span className="text-[11px] text-muted">priority {r.priority}</span></div>))}</div>
          ) : <p className="text-sm text-muted">No phone numbers point to this agent.</p>}
          <Link to="/numbers" className="inline-block text-sm text-accent-text hover:underline">Manage phone numbers →</Link>
        </Section>

        <Section id="call" icon={IC.call} title="Call Settings" sub="Configure call behavior and thresholds." open={open.has("call")} onToggle={toggle}>
          <Row title={KNOB_LABELS.voice_interrupt[0]} help={KNOB_LABELS.voice_interrupt[1]}>
            <Switch id="gs-interrupt" on={knob("voice_interrupt", true)} onChange={v => setKnob("voice_interrupt", v)} /></Row>
          <Row title={KNOB_LABELS.require_verification[0]} help={KNOB_LABELS.require_verification[1]}>
            <Switch id="gs-verify" on={knob("require_verification", true)} onChange={v => setKnob("require_verification", v)} /></Row>
          <Row title={KNOB_LABELS.red_flag_mode[0]} help={KNOB_LABELS.red_flag_mode[1]}>
            <select id="gs-red-flag" className="text-sm" value={knob("red_flag_mode", "empathy_only")} onChange={e => setKnob("red_flag_mode", e.target.value)}>
              <option value="empathy_only">Empathy, continue</option><option value="advise_and_continue">Advise ER, continue</option><option value="stop">Safety message only</option>
            </select></Row>
          <Slider id="gs-response-delay" title="Response Delay" help={KNOB_LABELS.voice_end_silence_ms[1]}
            value={knob("voice_end_silence_ms", 550)} min={200} max={2000} step={50} unit="ms" onChange={v => setKnob("voice_end_silence_ms", v)} />
          <Slider id="gs-inactivity" title="User Inactivity Timeout" help={KNOB_LABELS.voice_inactivity_s[1]}
            value={knob("voice_inactivity_s", 0)} min={0} max={60} step={1} fmt={v => (v ? `${v} s` : "off")} onChange={v => setKnob("voice_inactivity_s", v)} />
          <div className="rounded-lg border border-line p-3">
            <div className="flex items-center justify-between gap-3"><label htmlFor="gs-max-call" className="text-sm font-medium">Max Call Duration</label>
              <span className="flex items-center gap-1.5"><input id="gs-max-call" type="number" min={0} max={120} className="w-20 text-right"
                value={knob("call_max_minutes", 0)} onChange={e => setKnob("call_max_minutes", Number(e.target.value))} /><span className="text-sm text-muted">min</span></span></div>
            <div className="text-[11px] text-muted">{KNOB_LABELS.call_max_minutes[1]}</div>
          </div>
          <Slider id="gs-min-interrupt" title="Minimum Interruption Duration" help={KNOB_LABELS.voice_barge_in_ms[1]}
            value={knob("voice_barge_in_ms", 300) / 1000} min={0.1} max={2} step={0.05} unit="s" fmt={v => v.toFixed(2)} onChange={v => setKnob("voice_barge_in_ms", Math.round(v * 1000))} />
          <Slider id="gs-thinking" title="Thinking Voice" help={KNOB_LABELS.voice_filler_after_s[1]}
            value={knob("voice_filler_after_s", 0.7)} min={0.3} max={5} step={0.1} unit="s" fmt={v => v.toFixed(1)} onChange={v => setKnob("voice_filler_after_s", v)} />
          <div>
            <label htmlFor="gs-transfer" className="mb-1 block text-sm font-medium">{KNOB_LABELS.ivr_transfer_destination[0]}</label>
            <input id="gs-transfer" className="w-full" value={knob("ivr_transfer_destination", "")} onChange={e => setKnob("ivr_transfer_destination", e.target.value)} />
            <div className="mt-1 text-[11px] text-muted">{KNOB_LABELS.ivr_transfer_destination[1]}</div>
          </div>
          <div className="rounded-lg bg-soft/60 p-3 text-[11px] text-muted">Always on in our platform: Arabic gender agreement, switching language when the caller does,
            ending the call when the caller says goodbye. Language / voice per caller is set by the agent's languages.</div>
        </Section>

        <Section id="webhook" icon={IC.hook} title="Call Webhook" sub="Send all call events and conversation data to your webhook." open={open.has("webhook")} onToggle={toggle}
          badge={<span className="rounded bg-soft px-1.5 text-[10px] font-medium text-muted">Soon</span>}>
          <p className="text-sm text-muted">Not built yet. Call events are in <Link className="underline" to="/logs">Logs</Link> and each call's record in Call history.</p>
        </Section>
      </div>

      <div className="space-y-2 border-t border-line p-3">
        <ErrorBox error={save.error} />
        <div className="flex items-center gap-2">
          {dirty && <Badge tone="warn">unsaved</Badge>}
          <span className="mr-auto text-[11px] text-muted">Saved to the draft; callers get it after Publish.</span>
          <Button onClick={() => { setP(persona); setF(flowText); setB(structuredClone(d.bundle)); }} disabled={!dirty}>Undo</Button>
          <Button kind="primary" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>{save.isPending ? "Saving…" : "Save to draft"}</Button>
        </div>
      </div>

      {editing && <PromptDialog title={editing === "persona" ? "System prompt" : `Flow prompt · ${skill}`} value={editing === "persona" ? p : f}
        onChange={editing === "persona" ? setP : setF} onClose={() => setEditing(null)} />}
    </div>
  );
}
