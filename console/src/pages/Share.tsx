import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ShareSettings } from "../api";
import { CopyButton } from "../table";
import { Switch } from "../studio/TestPanel";
import { Badge, Button, ErrorBox, cx } from "../ui";

// Publishing, like Hamsa's: a public page anyone can open and talk to (no sign-in), an embed widget for any website, and what
// visitors may spend. The form is on the left; the right side is the real public page in preview mode, following every change.
// (Releasing a version of the agent — "Publish" in the Studio — is a separate step, and must have happened first.)

const PRESETS: [string, [string, string]][] = [
  ["Ocean", ["#CADCFC", "#A0B9D1"]], ["Violet", ["#C084FC", "#7C3AED"]], ["Emerald", ["#86EFAC", "#059669"]],
  ["Sunset", ["#FCA5A5", "#DC2626"]], ["Amber", ["#FCD34D", "#D97706"]], ["Midnight", ["#93C5FD", "#1E3A8A"]],
  ["Rose", ["#F9A8D4", "#BE185D"]], ["Slate", ["#CBD5E1", "#475569"]],
];
const VISUALIZERS: [ShareSettings["visualizer"], string, string][] = [["orb", "Orb", "Fluid orb"], ["wave", "Wave", "Oscilloscope waveform"], ["aura", "Aura", "Soft aurora glow"]];
const STATES: [string, string][] = [["ready", "Ready to talk"], ["connecting", "Connecting…"], ["listening", "Listening…"], ["thinking", "Thinking…"], ["speaking", "Speaking…"]];
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function Section({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-t border-line pt-5">
      <div><h2 className="text-base font-semibold">{title}</h2>{sub && <p className="text-xs text-muted">{sub}</p>}</div>
      {children}
    </section>
  );
}
const Toggle = ({ id, title, help, checked, onChange }: { id: string; title: string; help: string; checked: boolean; onChange: (v: boolean) => void }) => (
  <div className="flex items-start justify-between gap-4 rounded-lg border border-line bg-soft/40 p-3">
    <div><div className="text-sm font-medium">{title}</div><div className="text-xs text-muted">{help}</div></div>
    <Switch id={id} checked={checked} onChange={onChange} label={title} />
  </div>
);
const Seg = <T extends string>({ id, value, options, onChange }: { id: string; value: T; options: [T, string][]; onChange: (v: T) => void }) => (
  <div id={id} role="group" className="inline-flex rounded-lg border border-line bg-panel p-0.5 text-sm">
    {options.map(([v, label]) => (
      <button key={v} type="button" aria-pressed={value === v} onClick={() => onChange(v)}
        className={cx("rounded-md px-3 py-1", value === v ? "bg-accent text-accent-fg" : "hover:bg-soft")}>{label}</button>))}
  </div>
);
const Num = ({ id, label, value, min, max, step, unit, onChange }: { id: string; label: string; value: number; min: number; max: number; step?: number; unit?: string; onChange: (v: number) => void }) => (
  <label className="block text-xs font-medium">{label}
    <div className="mt-1 flex items-center gap-1.5"><input id={id} type="number" className="w-full" min={min} max={max} step={step ?? 1} value={value}
      onChange={e => onChange(Number(e.target.value))} />{unit && <span className="text-muted">{unit}</span>}</div></label>
);
const ColorField = ({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (v: string) => void }) => (
  <label className="block text-xs font-medium">{label}
    <div className="mt-1 flex items-center gap-2">
      <input id={`${id}-pick`} aria-label={`${label} colour`} type="color" className="h-9 w-12 shrink-0 cursor-pointer !p-0.5" value={value} onChange={e => onChange(e.target.value.toUpperCase())} />
      <input id={id} className="w-full font-mono text-xs" value={value} maxLength={7} onChange={e => onChange(e.target.value)} />
    </div></label>
);

export default function Share() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const agent = useQuery({ queryKey: ["agent", id], queryFn: () => api.agent(id) });
  const share = useQuery({ queryKey: ["share", id], queryFn: () => api.share(id) });
  const [s, setS] = useState<ShareSettings | null>(null);
  const [pvState, setPvState] = useState("ready");
  const [confirmOff, setConfirmOff] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const ready = useRef(false);
  useEffect(() => { if (share.data) setS(share.data.settings); }, [share.data]);
  const save = useMutation({ mutationFn: () => api.putShare(id, s!), onSuccess: () => qc.invalidateQueries({ queryKey: ["share", id] }) });
  const off = useMutation({ mutationFn: () => api.deleteShare(id), onSuccess: () => { setConfirmOff(false); qc.invalidateQueries({ queryKey: ["share", id] }); } });

  const name = agent.data?.agent.name ?? "";
  const hasRelease = !!agent.data?.agent.published_release_id;
  const published = !!share.data?.published;
  const dirty = !!(s && share.data && !same(s, share.data.settings));
  const set = (patch: Partial<ShareSettings>) => setS(x => (x ? { ...x, ...patch } : x));
  const setEmbed = (patch: Partial<ShareSettings["embed"]>) => setS(x => (x ? { ...x, embed: { ...x.embed, ...patch } } : x));
  const setLimits = (patch: Partial<ShareSettings["limits"]>) => setS(x => (x ? { ...x, limits: { ...x.limits, ...patch } } : x));

  // the live preview: the real public page, told what to show
  const config = useMemo(() => s && ({
    name: s.show_name ? name : "", description: s.description, tagline: s.tagline, show_transcript: s.show_transcript, theme: s.theme,
    theme_switcher: s.theme_switcher, visualizer: s.visualizer, gradient: s.gradient, bg_dark: s.bg_dark, bg_light: s.bg_light,
    embed: s.embed, allowed_params: s.limits.allowed_params,
  }), [s, name]);
  const push = () => {
    if (config && ready.current) frame.current?.contentWindow?.postMessage({ type: "share-preview", config, state: pvState,
      sample: s?.show_transcript ? ["Hello, how can I help you today?"] : [] }, location.origin);
  };
  useEffect(() => {
    const on = (e: MessageEvent) => { if (e.origin === location.origin && e.data?.type === "share-preview-ready") { ready.current = true; push(); } };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  });  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(push, [config, pvState]);  // eslint-disable-line react-hooks/exhaustive-deps

  const origin = location.origin;
  const link = share.data?.path ? origin + share.data.path : "";
  const snippet = share.data?.token ? `<script src="${origin}/embed.js" data-token="${share.data.token}" async></script>` : "";
  const ok = !!s && /^#[0-9a-fA-F]{6}$/.test(s.embed.color);

  if (!s || !share.data) return <div className="p-6 text-sm text-muted">{share.isLoading ? "Loading…" : <ErrorBox error={share.error} />}</div>;
  return (
    <div className="mx-auto max-w-[1500px] space-y-4 pb-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link to={`/agents/${id}`} className="text-xs text-muted hover:underline">← Back to {name || "the agent"}</Link>
          <h1 className="text-2xl font-bold tracking-tight">Share agent</h1>
          <p className="text-sm text-muted">A public link anyone can use to talk to this agent — no account needed — and a widget for your website.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={published ? "good" : "neutral"}>{published ? "Published" : "Not published"}</Badge>
          {published && dirty && <Badge tone="warn">unsaved changes</Badge>}
          {published && <Button kind="danger" onClick={() => setConfirmOff(true)}>Unpublish</Button>}
          <Button kind="primary" disabled={!hasRelease || save.isPending || !ok || (published && !dirty)} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : published ? "Update link" : "Publish link"}</Button>
        </div>
      </div>
      {!hasRelease && <div className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
        This agent has no published version yet. The link starts the published version, so publish a release first (the Publish button in the Studio).</div>}
      {confirmOff && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm">
          <span className="flex-1">Unpublish? The link and any widget using it stop working at once, and calls in progress on it end.</span>
          <Button kind="ghost" onClick={() => setConfirmOff(false)}>Cancel</Button>
          <Button kind="danger" onClick={() => off.mutate()} disabled={off.isPending}>{off.isPending ? "Unpublishing…" : "Unpublish"}</Button>
        </div>)}
      <ErrorBox error={save.error ?? off.error} />
      {published && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-sm">
          <span className="text-muted">Public link</span>
          <a href={link} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate font-mono text-xs text-accent-text hover:underline">{link}</a>
          <CopyButton text={link} label="Copy link" />
        </div>)}

      <div className="grid gap-6 lg:grid-cols-[1fr_26rem]">
        <div className="space-y-6">
          <Section title="The page" sub="What visitors see before and during the call.">
            <label className="block text-xs font-medium">Description
              <input id="share-desc" className="mt-1 w-full" maxLength={60} placeholder="What is this agent for?" value={s.description} onChange={e => set({ description: e.target.value })} />
              <span className="block text-right text-[11px] text-muted">{s.description.length}/60 · shown under the agent name</span></label>
            <label className="block text-xs font-medium">Short line under the title (optional)
              <input id="share-tagline" className="mt-1 w-full" maxLength={60} placeholder="e.g. Tap to start — we are here to help" value={s.tagline} onChange={e => set({ tagline: e.target.value })} />
              <span className="block text-right text-[11px] text-muted">{s.tagline.length}/60</span></label>
            <Toggle id="share-show-name" title="Show agent name" help="Your agent's internal name — turn on to display it on the page." checked={s.show_name} onChange={v => set({ show_name: v })} />
            <Toggle id="share-show-transcript" title="Show transcript" help="Display the agent's spoken text as a floating overlay during the call." checked={s.show_transcript} onChange={v => set({ show_transcript: v })} />
          </Section>

          <Section title="Theme" sub="The default look for visitors.">
            <Seg id="share-theme" value={s.theme} options={[["light", "☀ Light"], ["dark", "☾ Dark"]]} onChange={v => set({ theme: v })} />
            <Toggle id="share-switcher" title="Visitor theme switcher" help="Show a control so visitors can toggle light or dark. Their choice is remembered on their device." checked={s.theme_switcher} onChange={v => set({ theme_switcher: v })} />
          </Section>

          <Section title="Audio visualizer" sub="Pick a style and colours. Wave and aura use the end colour of the gradient.">
            <div className="grid gap-2 sm:grid-cols-3">
              {VISUALIZERS.map(([v, label, hint]) => (
                <button key={v} type="button" aria-pressed={s.visualizer === v} onClick={() => set({ visualizer: v })}
                  className={cx("rounded-lg border p-3 text-left", s.visualizer === v ? "border-accent bg-accent/5" : "border-line hover:bg-soft")}>
                  <div className="text-sm font-medium">{label}</div><div className="text-xs text-muted">{hint}</div></button>))}
            </div>
            <div>
              <div className="mb-1 text-xs font-medium">Preview state <span className="font-normal text-muted">· only for the preview, not saved</span></div>
              <div className="flex flex-wrap gap-1.5">{STATES.map(([k, label]) => (
                <button key={k} type="button" aria-pressed={pvState === k} onClick={() => setPvState(k)}
                  className={cx("rounded-full border px-3 py-1 text-xs", pvState === k ? "border-accent bg-accent/10 text-accent-text" : "border-line hover:bg-soft")}>{label}</button>))}</div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {PRESETS.map(([label, [a, b]]) => (
                <button key={label} type="button" aria-pressed={same(s.gradient, [a, b])} onClick={() => set({ gradient: [a, b] })}
                  className={cx("rounded-lg border p-1.5 text-left text-xs", same(s.gradient, [a, b]) ? "border-accent" : "border-line hover:bg-soft")}>
                  <span className="mb-1 block h-6 rounded" style={{ background: `linear-gradient(90deg, ${a}, ${b})` }} />{label}</button>))}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <ColorField id="share-g1" label="Gradient start" value={s.gradient[0]} onChange={v => set({ gradient: [v, s.gradient[1]] })} />
              <ColorField id="share-g2" label="Gradient end (accent)" value={s.gradient[1]} onChange={v => set({ gradient: [s.gradient[0], v] })} />
            </div>
          </Section>

          <details className="border-t border-line pt-5">
            <summary className="cursor-pointer text-base font-semibold">Advanced appearance</summary>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <ColorField id="share-bg-dark" label="Page background · dark mode" value={s.bg_dark} onChange={v => set({ bg_dark: v })} />
              <ColorField id="share-bg-light" label="Page background · light mode" value={s.bg_light} onChange={v => set({ bg_light: v })} />
            </div>
          </details>

          <Section title="Embed" sub="Add a widget to any website: a button that opens the agent in an overlay.">
            <div className="grid gap-3 sm:grid-cols-2">
              <div><div className="mb-1 text-xs font-medium">Position</div>
                <Seg id="share-position" value={s.embed.position} onChange={v => setEmbed({ position: v })}
                  options={[["bottom-left", "Bottom left"], ["bottom-center", "Center"], ["bottom-right", "Bottom right"]]} /></div>
              <div><div className="mb-1 text-xs font-medium">Size</div>
                <Seg id="share-size" value={s.embed.size} onChange={v => setEmbed({ size: v })} options={[["sm", "S"], ["md", "M"], ["lg", "L"]]} /></div>
              <label className="block text-xs font-medium">Button label
                <input id="share-label" className="mt-1 w-full" maxLength={40} placeholder="Talk to us" value={s.embed.label} onChange={e => setEmbed({ label: e.target.value })} />
                <span className="text-[11px] font-normal text-muted">Empty: a round icon bubble. Text: a pill button.</span></label>
              <ColorField id="share-embed-color" label="Button colour" value={s.embed.color} onChange={v => setEmbed({ color: v })} />
              <label className="block text-xs font-medium">Launcher
                <select id="share-launcher" className="mt-1 w-full" value={s.embed.launcher} onChange={e => setEmbed({ launcher: e.target.value as ShareSettings["embed"]["launcher"] })}>
                  <option value="auto">Auto — hidden when the page has its own trigger</option><option value="always">Always show the button</option><option value="never">Never (use your own trigger)</option></select></label>
            </div>
            <Toggle id="share-autostart" title="Auto-start call" help="Start the call as soon as the widget opens." checked={s.embed.auto_start} onChange={v => setEmbed({ auto_start: v })} />
            <div>
              <div className="mb-1 flex items-center justify-between text-xs font-medium">Snippet {snippet && <CopyButton text={snippet} label="Copy snippet" />}</div>
              <pre className="overflow-x-auto rounded-lg border border-line bg-soft p-3 text-[11px]">{snippet || "Publish the link to get the snippet."}</pre>
              <p className="mt-1 text-[11px] text-muted">Paste it before &lt;/body&gt;. To open the agent from your own button, give it the attribute <code>data-agent-trigger</code>.
                Options can also be set on the tag: <code>data-position</code>, <code>data-size</code>, <code>data-color</code>, <code>data-label</code>, <code>data-auto-start</code>, <code>data-launcher</code>.</p>
            </div>
          </Section>

          <Section title="Limits" sub="Every visitor call uses speech-recognition, language-model and voice credits. These are the ceilings for this link.">
            <div className="grid gap-3 sm:grid-cols-3">
              <Num id="share-max-min" label="Longest call" unit="min" value={s.limits.max_minutes} min={1} max={60} step={0.5} onChange={v => setLimits({ max_minutes: v })} />
              <Num id="share-max-concurrent" label="Calls at the same time" value={s.limits.max_concurrent} min={1} max={100} onChange={v => setLimits({ max_concurrent: v })} />
              <Num id="share-per-ip" label="Calls per visitor" unit="/ hour" value={s.limits.per_ip_per_hour} min={1} max={1000} onChange={v => setLimits({ per_ip_per_hour: v })} />
            </div>
            <label className="block text-xs font-medium">Websites that may embed the page <span className="font-normal text-muted">· one per line, empty = any</span>
              <textarea id="share-origins" rows={3} className="mt-1 w-full font-mono text-xs" placeholder={"https://www.example.com\nhttps://shop.example.com"}
                value={s.limits.allowed_origins.join("\n")} onChange={e => setLimits({ allowed_origins: e.target.value.split("\n").map(x => x.trim()).filter(Boolean) })} /></label>
            <label className="block text-xs font-medium">Variables the link's web address may set <span className="font-normal text-muted">· comma-separated custom variable names</span>
              <input id="share-params" className="mt-1 w-full font-mono text-xs" placeholder="customer_name, plan"
                value={s.limits.allowed_params.join(", ")} onChange={e => setLimits({ allowed_params: e.target.value.split(",").map(x => x.trim()).filter(Boolean) })} />
              <span className="text-[11px] font-normal text-muted">Example: {link || `${origin}/p/…`}?customer_name=Sara — only the names listed here are passed to the call.</span></label>
          </Section>
        </div>

        <aside className="lg:sticky lg:top-4 lg:self-start">
          <div className="mb-2 flex items-center justify-between text-xs text-muted"><span className="font-semibold uppercase tracking-wide">Live preview</span>
            <span>{published ? "the page as saved + your changes" : "not published yet"}</span></div>
          <div className="overflow-hidden rounded-2xl border border-line bg-panel">
            <iframe ref={frame} title="Live preview of the public page" src="/p/preview" className="h-[640px] w-full border-0" allow="microphone"
              onLoad={() => { ready.current = true; push(); }} />
          </div>
        </aside>
      </div>
    </div>
  );
}
