import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type AgentConfig, type ProviderSpec } from "../api";
import SchemaForm from "../SchemaForm";
import { Badge, Button, Card, ErrorBox, fmtTime } from "../ui";

const KINDS: [string, string, string][] = [
  ["stt", "Speech-to-text", "Transcribes the caller"],
  ["llm", "Language model", "Runs the conversation and tools"],
  ["tts", "Text-to-speech", "The agent's voice (Arabic + English)"],
  ["embedding", "Embeddings", "Location search (must match the stored vectors)"],
];

export default function Providers() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["providers"], queryFn: api.providers });
  const [draft, setDraft] = useState<AgentConfig | null>(null);
  const [note, setNote] = useState("");
  const [tests, setTests] = useState<Record<string, Record<string, unknown> | "running">>({});
  useEffect(() => { if (q.data && !draft) setDraft(structuredClone(q.data.config)); }, [q.data, draft]);

  const save = useMutation({
    mutationFn: () => api.saveProviders(draft!, note),
    onSuccess: () => { setNote(""); setDraft(null); qc.invalidateQueries({ queryKey: ["providers"] }); },
  });
  const activate = useMutation({
    mutationFn: (v: number) => api.activate(v),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ["providers"] }); },
  });
  if (!q.data || !draft) return <ErrorBox error={q.error} />;
  const dirty = JSON.stringify(draft) !== JSON.stringify(q.data.config);

  const spec = (kind: string) => (draft[kind] ?? { provider: "groq", settings: {} }) as ProviderSpec;
  const update = (kind: string, next: ProviderSpec) => setDraft({ ...draft, [kind]: next });
  const runtime = (draft.runtime ?? {}) as Record<string, unknown>;

  const test = async (kind: string) => {
    setTests(t => ({ ...t, [kind]: "running" }));
    try {
      const s = spec(kind);
      const r = await api.testProvider(kind, s.provider, s.settings);
      setTests(t => ({ ...t, [kind]: r }));
    } catch (e) {
      setTests(t => ({ ...t, [kind]: { ok: false, error: String(e) } }));
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Providers</h1>
          <p className="text-sm text-muted">Active config <b>v{q.data.active_version}</b>. Saving creates a new version; calls already in progress keep the version they started with.</p>
        </div>
        <div className="flex items-center gap-2">
          <input placeholder="What changed?" value={note} onChange={e => setNote(e.target.value)} className="w-56" />
          <Button onClick={() => setDraft(structuredClone(q.data.config))} disabled={!dirty}>Discard</Button>
          <Button kind="primary" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
            {save.isPending ? "Saving…" : "Save as new version"}
          </Button>
        </div>
      </div>
      <ErrorBox error={save.error} />

      {KINDS.map(([kind, title, hint]) => {
        const s = spec(kind);
        const schema = q.data.schemas[kind]?.[s.provider];
        const result = tests[kind];
        return (
          <Card key={kind} title={<span>{title} <span className="font-normal text-muted">· {hint}</span></span>}
            actions={<Button onClick={() => test(kind)} disabled={result === "running"}>{result === "running" ? "Testing…" : "Test"}</Button>}>
            <div className="mb-3 flex items-center gap-2">
              <span className="text-xs text-muted">Provider</span>
              <select value={s.provider} onChange={e => update(kind, { provider: e.target.value, settings: {} })}>
                {(q.data.available[kind] ?? []).map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            {schema ? <SchemaForm schema={schema} value={s.settings} onChange={v => update(kind, { ...s, settings: v })}
                effective={s.provider === ((q.data.config[kind] as ProviderSpec | undefined)?.provider ?? "groq")
                  ? q.data.effective?.[kind] : undefined} />
              : <div className="text-sm text-muted">No settings.</div>}
            {result && result !== "running" && Object.keys(result).length > 0 && (
              <div className={`mt-3 rounded-lg px-3 py-2 text-xs ${result.ok ? "bg-good/10 text-good" : "bg-bad/10 text-bad"}`}>
                {result.ok ? "✓ " : "✗ "}
                {Object.entries(result).filter(([k]) => k !== "ok").map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}
              </div>
            )}
          </Card>
        );
      })}

      <Card title="Runtime settings">
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(q.data.runtime_knobs).map(([k, type]) => (
            <label key={k} className="block">
              <div className="mb-1 text-xs font-medium">{k}</div>
              {k === "red_flag_mode" ? (
                <select className="w-full" value={String(runtime[k] ?? "")} onChange={e => setDraft({ ...draft, runtime: { ...runtime, [k]: e.target.value } })}>
                  <option value="advise_and_continue">advise_and_continue — empathy + ER/997 line, then continue</option>
                  <option value="empathy_only">empathy_only — empathy, then continue</option>
                  <option value="stop">stop — safety message only</option>
                </select>
              ) : (
                <input className="w-full" type={type === "str" ? "text" : "number"} value={String(runtime[k] ?? "")}
                  onChange={e => setDraft({ ...draft, runtime: { ...runtime, [k]: type === "str" ? e.target.value : Number(e.target.value) } })} />
              )}
            </label>
          ))}
        </div>
      </Card>

      <Card title="Version history">
        <table className="w-full text-sm">
          <tbody>
            {q.data.history.map(h => (
              <tr key={h.version} className="border-b border-line last:border-0">
                <td className="py-2 font-medium">v{h.version}</td>
                <td className="py-2 text-xs text-muted">{fmtTime(h.created_at)}</td>
                <td className="py-2 text-xs">{h.author}</td>
                <td className="py-2 text-xs">{h.note}</td>
                <td className="py-2 text-right">
                  {h.active ? <Badge tone="good">active</Badge>
                    : <Button onClick={() => activate.mutate(h.version)} disabled={activate.isPending}>Activate</Button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
