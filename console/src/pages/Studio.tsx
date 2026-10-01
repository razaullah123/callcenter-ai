import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type AgentDetail, type Bundle, type FlowGraph } from "../api";
import SchemaForm from "../SchemaForm";
import FlowCanvas from "../studio/FlowCanvas";
import { Badge, Button, Card, ErrorBox, fmtTime } from "../ui";
import Playground from "./Playground";

const JsonDiff = lazy(() => import("../studio/JsonDiff"));      // Monaco only loads when a diff is opened

const KINDS: [string, string][] = [["llm", "Language model"], ["stt", "Speech-to-text"], ["tts", "Voice (text-to-speech)"], ["embedding", "Embeddings"]];
const CONNECTION_FIELDS = new Set(["api_key", "base_url", "url", "headers", "timeout_s", "token", "secret", "password"]);
type Tab = "flow" | "settings" | "versions" | "test";

export default function Studio() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["agent", id], queryFn: () => api.agent(id) });
  const [tab, setTab] = useState<Tab>("flow");
  const [note, setNote] = useState("");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["agent", id] }); qc.invalidateQueries({ queryKey: ["agents"] }); };
  const publish = useMutation({ mutationFn: () => api.publishAgent(id, note), onSuccess: () => { setNote(""); refresh(); } });
  const discard = useMutation({ mutationFn: () => api.discardDraft(id), onSuccess: refresh });
  if (!q.data) return <ErrorBox error={q.error} />;
  const d = q.data;
  const live = d.releases.find(r => r.id === d.agent.published_release_id);

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link to="/agents" className="text-xs text-muted hover:underline">← Agents</Link>
          <h1 className="text-xl font-semibold">{d.agent.name}</h1>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span className="font-mono">{d.agent.id}</span>
            {live && <Badge tone="good">live: v{live.version}</Badge>}
            {d.has_draft ? <Badge tone="warn">draft — not live yet{d.agent.draft_updated_at ? ` · ${fmtTime(d.agent.draft_updated_at)}` : ""}</Badge>
              : <Badge>no unpublished changes</Badge>}
            {d.routes.map(r => <Badge key={r.pattern} tone="info">route {r.pattern}</Badge>)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input id="publish-note" className="w-56" placeholder="What changed?" value={note} onChange={e => setNote(e.target.value)} />
          <Button onClick={() => discard.mutate()} disabled={!d.has_draft || discard.isPending}>Discard draft</Button>
          <Button kind="primary" onClick={() => publish.mutate()} disabled={!d.has_draft || publish.isPending}>
            {publish.isPending ? "Publishing…" : "Publish"}</Button>
        </div>
      </div>
      <ErrorBox error={publish.error ?? discard.error} />

      <div className="flex gap-1 border-b border-line">
        {([["flow", "Flow"], ["settings", "Settings"], ["versions", "Versions"], ["test", "Test call"]] as [Tab, string][]).map(([t, l]) => (
          <button key={t} onClick={() => setTab(t)} className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t ? "border-accent font-medium" : "border-transparent text-muted hover:text-ink"}`}>{l}</button>
        ))}
      </div>

      {tab === "flow" && <FlowTab d={d} onSaved={refresh} />}
      {tab === "settings" && <SettingsTab key={JSON.stringify(d.bundle).length} d={d} onSaved={refresh} />}
      {tab === "versions" && <VersionsTab d={d} onChanged={refresh} />}
      {tab === "test" && <TestTab d={d} />}
    </div>
  );
}

function FlowTab({ d, onSaved }: { d: AgentDetail; onSaved: () => void }) {
  const withFlow = Object.entries(d.skills).filter(([, s]) => s.flow?.graph);
  const [skill, setSkill] = useState(withFlow[0]?.[0] ?? "");
  const save = useMutation({ mutationFn: (g: FlowGraph) => api.putDraftSkill(d.agent.id, skill, { graph: g }), onSuccess: onSaved });
  const s = d.skills[skill];
  if (!withFlow.length) return <Card><div className="text-sm text-muted">No skill of this agent has a flow yet.</div></Card>;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">Skill</span>
        {withFlow.map(([k]) => <Button key={k} kind={k === skill ? "default" : "ghost"} onClick={() => setSkill(k)}>{k}</Button>)}
        {s && <span className="text-xs text-muted">· {s.library} v{s.version}</span>}
      </div>
      <ErrorBox error={save.error} />
      {s?.flow?.graph && <FlowCanvas key={`${skill}:${s.version}`} graph={s.flow.graph} converted={!!s.flow.converted} tools={d.tools}
        skills={Object.keys(d.skills).filter(k => k !== "_persona")} saving={save.isPending} onSave={g => save.mutate(g)} />}
    </div>
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
            <label key={k} className="block"><div className="mb-1 text-xs font-medium">{k}</div>
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
  const activate = useMutation({ mutationFn: (rid: number) => api.activateRelease(d.agent.id, rid), onSuccess: onChanged });
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
    </div>
  );
}

function TestTab({ d }: { d: AgentDetail }) {
  const [draft, setDraft] = useState(d.has_draft);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2"><input type="radio" checked={draft} onChange={() => setDraft(true)} disabled={!d.has_draft} /> the draft</label>
        <label className="flex items-center gap-2"><input type="radio" checked={!draft} onChange={() => setDraft(false)} /> the live version</label>
        <span className="text-xs text-muted">Test calls are logged in Call history like any other call.</span>
      </div>
      <Playground key={String(draft)} agent={d.agent.id} draft={draft} embedded />
    </div>
  );
}
