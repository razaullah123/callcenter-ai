import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type AgentDetail, type Bundle, type FlowGraph } from "../api";
import SchemaForm from "../SchemaForm";
import FlowCanvas from "../studio/FlowCanvas";
import TestPanel, { Icon, ICONS, loadFollow, saveFollow, type LiveEvent } from "../studio/TestPanel";
import GlobalSettings, { KNOB_LABELS } from "../studio/GlobalSettings";
import { MakeCall } from "./Numbers";
import TestsTab, { AuditCard, gateBadge, PublishDialog } from "../studio/TestsTab";
import { useTestCall, type TestMode } from "../studio/useTestCall";
import { usePermissions } from "../permissions";
import { Badge, Button, Card, ErrorBox, fmtTime } from "../ui";

const JsonDiff = lazy(() => import("../studio/JsonDiff"));      // Monaco only loads when a diff is opened

const KINDS: [string, string][] = [["llm", "Language model"], ["stt", "Speech-to-text"], ["tts", "Voice (text-to-speech)"], ["embedding", "Embeddings"]];
const CONNECTION_FIELDS = new Set(["api_key", "base_url", "url", "headers", "timeout_s", "token", "secret", "password"]);
type Tab = "flow" | "settings" | "tests" | "versions";

export default function Studio() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const { can, role } = usePermissions();
  const q = useQuery({ queryKey: ["agent", id], queryFn: () => api.agent(id) });
  const [tab, setTab] = useState<Tab>("flow");
  const [publishing, setPublishing] = useState(false);
  const [menu, setMenu] = useState(false);
  const [phoneTest, setPhoneTest] = useState(false);
  const outNumbers = useQuery({ queryKey: ["outbound-numbers"], queryFn: api.outboundNumbers, enabled: phoneTest });
  const [useDraft, setUseDraft] = useState(true);
  const [panel, setPanel] = useState<"chat" | "logs" | "vars" | null>(null);
  const [follow, setFollowState] = useState(loadFollow);       // saved on this device, like Hamsa
  const setFollow = (v: boolean) => { setFollowState(v); saveFollow(v); };
  const [active, setActive] = useState<string | null>(null);        // "skill/node" the test call is in
  const [locate, setLocate] = useState<{ step: string; n: number } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [logNode, setLogNode] = useState<{ node: string; n: number } | null>(null);
  const call = useTestCall();
  const menuRef = useRef<HTMLDivElement>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["agent", id] }); qc.invalidateQueries({ queryKey: ["agents"] }); };
  const discard = useMutation({ mutationFn: () => api.discardDraft(id), onSuccess: refresh });
  const exportAgent = useMutation({
    mutationFn: () => api.exportAgent(id),
    onSuccess: data => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      a.download = `${id}.agent.json`; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    },
  });
  useEffect(() => {
    const close = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as globalThis.Node)) setMenu(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const onEvent = useCallback((e: LiveEvent) => {
    if (e.type === "step.transition" && typeof e.data.step === "string") setActive(e.data.step);
  }, []);
  if (!q.data) return <ErrorBox error={q.error} />;
  const d = q.data;
  const live = d.releases.find(r => r.id === d.agent.published_release_id);
  const testWithDraft = useDraft && d.has_draft;
  const startTest = (mode: TestMode) => {
    setMenu(false); setActive(null); setTab("flow"); setPanel("chat");
    call.start(mode, { agent: d.agent.id, draft: testWithDraft, language: d.bundle.agent?.default_language });
  };

  return (
    <div className="mx-auto max-w-[1500px] space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <Link to="/agents" className="text-xs text-muted hover:underline">← Agents</Link>
          <h1 className="text-xl font-semibold">{d.agent.name}</h1>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span className="font-mono">Agent ID: {d.agent.id}</span>
            {live && <Badge tone="good">live: v{live.version}</Badge>}
            {d.has_draft && <Badge tone="warn">draft — not live yet</Badge>}
            {d.routes.map(r => <Badge key={r.pattern} tone="info">route {r.pattern}</Badge>)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {call.running ? (
            <div className="flex items-center gap-1">
              <span className="flex items-center gap-1.5 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm font-medium">
                {call.mode === "voice" ? "🎙 Browser call" : "💬 Chat"}{testWithDraft ? " · draft" : " · live"}</span>
              <button title="End the test" onClick={call.stop}
                className="flex h-8 w-8 items-center justify-center rounded-lg bg-bad text-white hover:opacity-90">◉</button>
            </div>
          ) : (
            <div ref={menuRef} className="relative">
              <div className="flex overflow-hidden rounded-lg border border-line bg-panel">
                <button className="px-3 py-1.5 text-sm font-medium hover:bg-soft" onClick={() => startTest("voice")}>▷ Test</button>
                <button className="border-l border-line px-2 hover:bg-soft" onClick={() => setMenu(m => !m)} aria-label="Test options">▾</button>
              </div>
              {menu && (
                <div className="absolute right-0 z-20 mt-1 w-56 rounded-lg border border-line bg-panel p-1 shadow-lg">
                  <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-soft" onClick={() => startTest("voice")}>🎙 Browser call</button>
                  <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-soft" onClick={() => startTest("chat")}>💬 Chat</button>
                  <button id="test-phone" className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-soft" onClick={() => { setMenu(false); setPhoneTest(true); }}>📞 Test via phone…</button>
                  <div className="my-1 border-t border-line" />
                  <label className="flex items-center gap-2 px-2 py-1 text-xs">
                    <input type="checkbox" checked={testWithDraft} disabled={!d.has_draft} onChange={e => setUseDraft(e.target.checked)} />
                    Test the draft {d.has_draft ? "" : "(no draft)"}</label>
                </div>
              )}
            </div>
          )}
          <Button onClick={() => { setTab("flow"); setPanel(p => (p === "logs" ? null : "logs")); }}><span className="inline-flex items-center gap-1.5"><Icon d={ICONS.activity} />Live Call Logs</span></Button>
          <Link to={`/agents/${d.agent.id}/share`} className="rounded-lg border border-line bg-panel px-3 py-1.5 text-sm font-medium hover:bg-soft">Share</Link>
          <Button kind="primary" onClick={() => setPublishing(true)} disabled={!d.has_draft || !can("agents", "deploy")} title={can("agents", "deploy") ? undefined : "You have read-only access"}>⇪ Publish</Button>
          {role === "VIEWER" && <span className="rounded-lg border border-line px-3 py-1.5 text-sm text-muted">Read-only</span>}
          <span className={`rounded-lg border px-3 py-1.5 text-sm ${dirty ? "border-warn/50 text-warn" : "border-good/40 text-good"}`}>
            {dirty ? "● Unsaved changes" : "✓ Saved"}</span>
          <Button onClick={() => exportAgent.mutate()} disabled={exportAgent.isPending}>⤓ Export</Button>
          {d.has_draft && <Button kind="ghost" onClick={() => discard.mutate()} disabled={discard.isPending}>Discard draft</Button>}
        </div>
      </div>
      <ErrorBox error={discard.error ?? exportAgent.error} />
      {phoneTest && outNumbers.data && <MakeCall numbers={outNumbers.data} fixedAgent={d.agent.id} draft={testWithDraft} onClose={() => setPhoneTest(false)} />}
      {publishing && <PublishDialog d={d} onClose={() => setPublishing(false)} onPublished={refresh} />}

      <div className="flex gap-1 border-b border-line">
        {([["flow", "Flow"], ["settings", "Settings"], ["tests", "Tests"], ["versions", "Versions"]] as [Tab, string][]).map(([t, l]) => (
          <button key={t} onClick={() => setTab(t)} className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t ? "border-accent font-medium" : "border-transparent text-muted hover:text-ink"}`}>{l}</button>
        ))}
      </div>

      {tab === "flow" && <FlowTab d={d} onSaved={refresh} onDirty={setDirty} active={active} follow={follow}
        locate={locate} onViewLogs={node => { setLogNode({ node, n: Date.now() }); setPanel("logs"); }}
        onOpenSettings={() => setTab("settings")} onInspect={() => setPanel(null)}
        aside={panel && <TestPanel call={call} tab={panel} onTab={setPanel} follow={follow} onFollow={setFollow} presetNode={logNode}
          onEvent={onEvent} onLocate={step => setLocate({ step, n: Date.now() })} onClose={() => setPanel(null)} />} />}
      {tab === "settings" && <SettingsTab key={JSON.stringify(d.bundle).length} d={d} onSaved={refresh} />}
      {tab === "tests" && <TestsTab d={d} />}
      {tab === "versions" && <VersionsTab d={d} onChanged={refresh} />}
    </div>
  );
}

function FlowTab({ d, onSaved, onDirty, active, follow, locate, aside, onViewLogs, onInspect }: {
  d: AgentDetail; onSaved: () => void; onDirty: (dirty: boolean) => void; active: string | null; follow: boolean;
  locate: { step: string; n: number } | null; aside: React.ReactNode; onViewLogs: (node: string) => void;
  onOpenSettings?: () => void; onInspect: () => void;
}) {
  const lib = useQuery({ queryKey: ["tool-library"], queryFn: api.toolLibrary });
  const toolInfo = Object.fromEntries((lib.data?.tools ?? []).map(t => [t.name, {
    description: t.description, kind: t.policy.kind, source: t.source === "mcp" && t.server ? `mcp · ${t.server}` : t.source }]));
  // every tool of the project can be used (one the agent doesn't have yet joins it when the flow is saved)
  const allTools = [...new Set([...d.tools, ...(lib.data?.tools ?? []).filter(t => t.policy.enabled !== false).map(t => t.name)])].sort();
  const withFlow = Object.entries(d.skills).filter(([, s]) => s.flow?.graph);
  const [skill, setSkill] = useState(withFlow[0]?.[0] ?? "");
  const save = useMutation({ mutationFn: (g: FlowGraph) => api.putDraftSkill(d.agent.id, skill, { graph: g }), onSuccess: onSaved });
  // follow the call into whichever skill it is in
  const [activeSkill, activeNode] = active ? [active.split("/")[0], active.split("/").slice(1).join("/")] : [null, null];
  useEffect(() => { if (follow && activeSkill && activeSkill !== skill && d.skills[activeSkill]?.flow?.graph) setSkill(activeSkill); },
    [activeSkill, follow]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const target = locate?.step.split("/")[0];
    if (target && target !== skill && d.skills[target]?.flow?.graph) setSkill(target);
  }, [locate]);  // eslint-disable-line react-hooks/exhaustive-deps
  const s = d.skills[skill];
  if (!withFlow.length) return <Card><div className="text-sm text-muted">No skill of this agent has a flow yet.</div></Card>;
  const locateHere = locate && locate.step.split("/")[0] === skill ? { id: locate.step.split("/").slice(1).join("/"), n: locate.n } : null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">Skill</span>
        {withFlow.map(([k]) => <Button key={k} kind={k === skill ? "default" : "ghost"} onClick={() => setSkill(k)}>
          {k}{activeSkill === k ? " ●" : ""}</Button>)}
        {s && <span className="text-xs text-muted">· {s.library} v{s.version}</span>}
      </div>
      <ErrorBox error={save.error} />
      {s?.flow?.graph && <FlowCanvas key={`${skill}:${s.version}`} graph={s.flow.graph} converted={!!s.flow.converted} tools={allTools}
        skills={Object.keys(d.skills).filter(k => k !== "_persona")} saving={save.isPending} onSave={g => save.mutate(g)}
        onDirty={onDirty} aside={aside} activeNode={activeSkill === skill ? activeNode : null} follow={follow} locate={locateHere}
        toolInfo={toolInfo} onViewLogs={onViewLogs} onInspect={onInspect} customVars={d.bundle.variables}
        globalPanel={<GlobalSettings key={`${skill}:${JSON.stringify(d.bundle).length}`} d={d} skill={skill} onSaved={onSaved} />} />}
    </div>
  );
}

/** Which knowledge base items this agent searches (it gets a search tool in every step when any are chosen). */
function KnowledgeCard({ items, onChange }: { items: string[]; onChange: (items: string[]) => void }) {
  const q = useQuery({ queryKey: ["knowledge"], queryFn: api.knowledge });
  const all = q.data?.items ?? [];
  const missing = items.filter(i => !all.some(x => x.id === i));
  return (
    <Card title="Knowledge base" actions={<Link to="/knowledge-base" className="text-xs text-accent-text hover:underline">Manage →</Link>}>
      {!all.length ? <p className="text-sm text-muted">No knowledge base items in this project yet.</p> : (
        <div className="grid gap-1.5 sm:grid-cols-2">
          {all.map(i => (
            <label key={i.id} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-soft">
              <input type="checkbox" checked={items.includes(i.id)} className="accent-[var(--color-accent)]"
                onChange={e => onChange(e.target.checked ? [...items, i.id] : items.filter(x => x !== i.id))} />
              <span className="truncate">{i.name}</span>
              <span className="ml-auto shrink-0 text-[11px] text-muted">{i.type === "file" ? (i.extension ?? "").toUpperCase() : "Text"} · {i.words.toLocaleString()} words</span>
            </label>
          ))}
        </div>
      )}
      {missing.length > 0 && <p className="mt-2 text-xs text-warn">{missing.length} chosen item(s) were deleted — save to drop them.</p>}
      <p className="mt-2 text-[11px] text-muted">With items chosen, the agent can call “search the knowledge base” in every step and answers
        from what it finds. Saved to the draft; callers get it after Publish.</p>
    </Card>
  );
}

function SettingsTab({ d, onSaved }: { d: AgentDetail; onSaved: () => void }) {
  const [b, setB] = useState<Bundle>(structuredClone(d.bundle));
  const [meta, setMeta] = useState({ name: d.agent.name, description: d.agent.description ?? "" });
  const [phrase, setPhrase] = useState("GREETING");
  const [phraseText, setPhraseText] = useState("");
  const [phraseBad, setPhraseBad] = useState(false);
  useEffect(() => { setPhraseText(JSON.stringify(b.phrases[phrase] ?? null, null, 2)); setPhraseBad(false); }, [phrase]);  // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({ mutationFn: () => api.putDraft(d.agent.id, b), onSuccess: onSaved });
  const rename = useMutation({ mutationFn: () => api.renameAgent(d.agent.id, meta), onSuccess: onSaved });
  const dirty = JSON.stringify(b) !== JSON.stringify(d.bundle);
  const setKnob = (k: string, v: unknown) => setB({ ...b, knobs: { ...b.knobs, [k]: v } });
  const typeOf = (kind: string) => {
    const spec = b.models[kind] ?? {};
    return d.connections.find(c => c.id === spec.provider)?.type ?? spec.type ?? "groq";
  };
  const choiceSchema = (kind: string) => {
    const sch = d.schemas[kind]?.[typeOf(kind)];
    if (!sch) return undefined;
    return { ...sch, required: (sch.required ?? []).filter(r => !CONNECTION_FIELDS.has(r)),
             properties: Object.fromEntries(Object.entries(sch.properties ?? {}).filter(([k]) => !CONNECTION_FIELDS.has(k))) };
  };
  return (
    <div className="space-y-4">
      <div className="sticky top-0 z-10 flex items-center justify-end gap-2 bg-bg/80 py-2 backdrop-blur">
        {dirty && <Badge tone="warn">unsaved</Badge>}
        <Button onClick={() => setB(structuredClone(d.bundle))} disabled={!dirty}>Undo</Button>
        <Button kind="primary" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>{save.isPending ? "Checking…" : "Save to draft"}</Button>
      </div>
      <ErrorBox error={save.error} />

      <Card title="Agent">
        <div className="grid gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
          <label className="block"><div className="mb-1 text-xs font-medium">Name</div><input id="meta-name" className="w-full" value={meta.name} onChange={e => setMeta({ ...meta, name: e.target.value })} /></label>
          <label className="block"><div className="mb-1 text-xs font-medium">Description</div><input id="meta-desc" className="w-full" value={meta.description} onChange={e => setMeta({ ...meta, description: e.target.value })} /></label>
          <Button onClick={() => rename.mutate()} disabled={rename.isPending}>Save name</Button>
        </div>
        <ErrorBox error={rename.error} />
      </Card>

      <Card title="Models and voice">
        <div className="space-y-5">
          {KINDS.map(([kind, title]) => {
            const spec = b.models[kind] ?? {};
            const options = d.connections.filter(c => c.kind === kind);
            const sch = choiceSchema(kind);
            return (
              <div key={kind} className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="w-44 text-sm font-medium">{title}</span>
                  <select id={`conn-${kind}`} className="w-64" value={spec.provider ?? ""} onChange={e => setB({ ...b, models: { ...b.models, [kind]: { provider: e.target.value, settings: {} } } })}>
                    {!spec.provider && <option value="">{spec.type ?? "choose a connection"}</option>}
                    {options.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                  <Link to="/connections" className="text-xs text-muted hover:underline">connections</Link>
                </div>
                {sch && <SchemaForm schema={sch} value={spec.settings ?? {}} onChange={v => setB({ ...b, models: { ...b.models, [kind]: { ...spec, settings: v } } })} />}
              </div>
            );
          })}
        </div>
      </Card>

      <Card title="Behaviour">
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(d.choices.knobs).map(([k, t]) => (
            <label key={k} className="block" title={k}><div className="mb-1 text-xs font-medium">{KNOB_LABELS[k]?.[0] ?? k}</div>
              {t === "bool" ? (
                <select id={`knob-${k}`} className="w-full" value={String(b.knobs[k] ?? "")} onChange={e => setKnob(k, e.target.value === "true")}>
                  <option value="true">true</option><option value="false">false</option></select>
              ) : k === "red_flag_mode" ? (
                <select id={`knob-${k}`} className="w-full" value={String(b.knobs[k] ?? "")} onChange={e => setKnob(k, e.target.value)}>
                  {["empathy_only", "advise_and_continue", "stop"].map(m => <option key={m}>{m}</option>)}</select>
              ) : k === "entry_skill" ? (
                <select id={`knob-${k}`} className="w-full" value={String(b.knobs[k] ?? "")} onChange={e => setKnob(k, e.target.value)}>
                  {Object.keys(d.skills).filter(s => s !== "_persona").map(s => <option key={s}>{s}</option>)}</select>
              ) : (
                <input id={`knob-${k}`} className="w-full" type={t === "str" ? "text" : "number"} value={String(b.knobs[k] ?? "")}
                  onChange={e => setKnob(k, t === "str" ? e.target.value : Number(e.target.value))} />
              )}
            </label>
          ))}
        </div>
      </Card>

      <KnowledgeCard items={b.knowledge?.items ?? []}
        onChange={items => setB(items.length ? { ...b, knowledge: { items } } : (({ knowledge: _k, ...rest }) => { void _k; return rest; })(b) as Bundle)} />

      <Card title="Speech recognition hint">
        <div className="grid gap-3 sm:grid-cols-2">
          {(["ar", "en"] as const).map(l => (
            <label key={l} className="block"><div className="mb-1 text-xs font-medium">{l === "ar" ? "Arabic" : "English"}</div>
              <input id={`hint-${l}`} dir={l === "ar" ? "rtl" : "ltr"} className="w-full" value={b.voice?.stt_hint?.[l] ?? ""}
                onChange={e => setB({ ...b, voice: { ...b.voice, stt_hint: { ...b.voice?.stt_hint, [l]: e.target.value } } })} /></label>))}
        </div>
        <p className="mt-2 text-[11px] text-muted">Place names and words callers use. Keep it short: a long hint can come back as text on background noise.</p>
      </Card>

      <Card title="Fixed lines and notes">
        <div className="grid gap-3 sm:grid-cols-[14rem_1fr]">
          <select id="phrase-name" size={10} className="w-full text-xs" value={phrase} onChange={e => setPhrase(e.target.value)}>
            {d.choices.phrases.map(p => <option key={p}>{p}</option>)}</select>
          <div>
            <textarea id="phrase-text" rows={10} className={`w-full font-mono text-xs ${phraseBad ? "border-bad" : ""}`} value={phraseText}
              onChange={e => { setPhraseText(e.target.value); try { setB({ ...b, phrases: { ...b.phrases, [phrase]: JSON.parse(e.target.value) } }); setPhraseBad(false); } catch { setPhraseBad(true); } }} />
            <p className="text-[11px] text-muted">JSON, usually {"{"}"ar": "…", "en": "…"{"}"}. Lines the platform says word for word (greeting, fillers, handoff, booked…).</p>
          </div>
        </div>
      </Card>

      <Card title="Skills">
        <div className="divide-y divide-line">
          {Object.entries(d.skills).map(([k, s]) => (
            <div key={k} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              <div><span className="font-mono">{k}</span>{s.library !== k && <span className="text-xs text-muted"> ← {s.library}</span>}</div>
              <div className="flex items-center gap-2">
                <select id={`skill-v-${k}`} className="w-28 text-xs" value={String(typeof b.skills[k] === "object" ? (b.skills[k] as { version: number }).version : b.skills[k])}
                  onChange={e => { const v = Number(e.target.value); const cur = b.skills[k]; setB({ ...b, skills: { ...b.skills, [k]: typeof cur === "object" ? { ...cur, version: v } : v } }); }}>
                  {s.versions.map(v => <option key={v.version} value={v.version}>v{v.version}</option>)}</select>
                {k !== "_persona" && <Button kind="ghost" onClick={() => { const { [k]: _x, ...rest } = b.skills; void _x; setB({ ...b, skills: rest }); }}>Remove</Button>}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <select id="skill-add" className="w-64 text-xs" defaultValue="" onChange={e => { if (e.target.value) { setB({ ...b, skills: { ...b.skills, [e.target.value]: 1 } }); e.target.value = ""; } }}>
            <option value="">Add a skill from the library…</option>
            {d.choices.library_skills.filter(s => !(s in b.skills)).map(s => <option key={s}>{s}</option>)}</select>
          <span className="text-[11px] text-muted">Edit skills' text on the <Link className="underline" to="/skills">Skills</Link> page; their flows on the Flow tab.</span>
        </div>
      </Card>

      <Card title={`Tools · ${d.tools.length}`}>
        <div className="flex flex-wrap gap-1">{d.tools.map(t => <Badge key={t}>{t}</Badge>)}</div>
        <p className="mt-2 text-[11px] text-muted">Added, removed and configured on the <Link className="underline" to="/tools">Tools</Link> page.</p>
      </Card>
    </div>
  );
}

function VersionsTab({ d, onChanged }: { d: AgentDetail; onChanged: () => void }) {
  const [a, setA] = useState<number | "draft" | null>(null);
  const [bb, setBb] = useState<number | "draft" | null>(null);
  const qc = useQueryClient();
  const activate = useMutation({ mutationFn: (rid: number) => api.activateRelease(d.agent.id, rid),
    onSuccess: () => { onChanged(); qc.invalidateQueries({ queryKey: ["agent-audit", d.agent.id] }); } });
  const load = async (x: number | "draft") => x === "draft" ? d.bundle : (await api.release(d.agent.id, x)).bundle;
  const diff = useQuery({ queryKey: ["diff", d.agent.id, a, bb], enabled: a !== null && bb !== null,
    queryFn: async () => ({ left: JSON.stringify(await load(a!), null, 2), right: JSON.stringify(await load(bb!), null, 2) }) });
  return (
    <div className="space-y-4">
      <Card title="Releases">
        <table className="w-full text-sm"><tbody>
          {d.releases.map(r => (
            <tr key={r.id} className="border-b border-line last:border-0">
              <td className="py-2 font-medium">v{r.version}</td>
              <td className="py-2 text-xs text-muted">{fmtTime(r.created_at)}</td>
              <td className="py-2 text-xs">{r.author}</td>
              <td className="py-2 text-xs">{r.note}</td>
              <td className="py-2">{gateBadge(r.gate)}</td>
              <td className="py-2 text-right whitespace-nowrap">
                <Button kind="ghost" onClick={() => { setA(r.id); if (bb === null) setBb(d.has_draft ? "draft" : d.agent.published_release_id); }}>Compare</Button>{" "}
                {r.id === d.agent.published_release_id ? <Badge tone="good">live</Badge>
                  : <Button onClick={() => activate.mutate(r.id)} disabled={activate.isPending}>Make live</Button>}
              </td>
            </tr>))}
        </tbody></table>
        <ErrorBox error={activate.error} />
      </Card>
      {a !== null && (
        <Card title="Compare" actions={<>
          <select id="diff-a" className="text-xs" value={String(a)} onChange={e => setA(e.target.value === "draft" ? "draft" : Number(e.target.value))}>
            {d.has_draft && <option value="draft">draft</option>}{d.releases.map(r => <option key={r.id} value={r.id}>v{r.version}</option>)}</select>
          <span className="text-xs">→</span>
          <select id="diff-b" className="text-xs" value={String(bb)} onChange={e => setBb(e.target.value === "draft" ? "draft" : Number(e.target.value))}>
            {d.has_draft && <option value="draft">draft</option>}{d.releases.map(r => <option key={r.id} value={r.id}>v{r.version}</option>)}</select></>}>
          <p className="mb-2 text-[11px] text-muted">Skills show as version numbers; open the Skills page to compare their text.</p>
          {diff.data ? <Suspense fallback={<div className="text-sm text-muted">Loading editor…</div>}><JsonDiff left={diff.data.left} right={diff.data.right} /></Suspense>
            : <ErrorBox error={diff.error} />}
        </Card>
      )}
      <AuditCard agentId={d.agent.id} />
    </div>
  );
}
