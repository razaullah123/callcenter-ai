import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, isSecretRef, type Connection } from "../api";
import SchemaForm from "../SchemaForm";
import { Badge, Button, Card, Empty, ErrorBox, fmtTime } from "../ui";

const KINDS: [string, string][] = [
  ["llm", "Language models"], ["stt", "Speech-to-text"], ["tts", "Text-to-speech"], ["embedding", "Embeddings"],
];

type TestResult = Record<string, unknown> | "running";

export default function Connections() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["connections"], queryFn: api.connections });
  const secrets = useQuery({ queryKey: ["secrets"], queryFn: api.secrets });
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["connections"] });
    qc.invalidateQueries({ queryKey: ["secrets"] });
    qc.invalidateQueries({ queryKey: ["providers"] });
  };
  if (!q.data) return <ErrorBox error={q.error} />;
  const secretNames = (secrets.data?.secrets ?? []).map(s => s.name);
  // a connection holds the account (key, endpoint, headers, timeout); models and voices are chosen per agent
  const only = new Set(q.data.connection_fields);
  const schemas = Object.fromEntries(Object.entries(q.data.schemas).map(([kind, types]) => [kind,
    Object.fromEntries(Object.entries(types).map(([t, sch]) => [t, {
      ...sch, required: (sch.required ?? []).filter(r => only.has(r)),
      properties: Object.fromEntries(Object.entries(sch.properties ?? {}).filter(([k]) => only.has(k))),
    }]))]));

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Connections</h1>
          <p className="max-w-2xl text-sm text-muted">
            Provider accounts shared by agents: endpoint, key and timeout. Each agent picks its model and voice on
            top of a connection. Keys are stored encrypted as <a className="underline" href="/console/secrets">secrets</a>;
            a change reaches new calls only.
          </p>
        </div>
        <Button kind="primary" onClick={() => setAdding(true)} disabled={adding}>Add connection</Button>
      </div>

      {adding && <AddConnection data={{ ...q.data, schemas }} secretNames={secretNames}
        onDone={() => { setAdding(false); refresh(); }} onCancel={() => setAdding(false)} />}

      {KINDS.map(([kind, title]) => {
        const rows = q.data.connections.filter(c => c.kind === kind);
        return (
          <Card key={kind} title={title}>
            {rows.length === 0 ? <Empty>No {title.toLowerCase()} connection yet.</Empty> : (
              <div className="divide-y divide-line">
                {rows.map(c => editing === c.id
                  ? <EditConnection key={c.id} c={c} schema={schemas[c.kind]?.[c.type]} secretNames={secretNames}
                      onDone={() => { setEditing(null); refresh(); }} onCancel={() => setEditing(null)} />
                  : <ConnectionRow key={c.id} c={c} onEdit={() => setEditing(c.id)} onDeleted={refresh} />)}
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}

function ConnectionRow({ c, onEdit, onDeleted }: { c: Connection; onEdit: () => void; onDeleted: () => void }) {
  const [test, setTest] = useState<TestResult | null>(null);
  const [confirm, setConfirm] = useState(false);
  const del = useMutation({ mutationFn: () => api.deleteConnection(c.id), onSuccess: onDeleted });
  const run = async () => {
    setTest("running");
    try { setTest(await api.testConnection(c.id)); } catch (e) { setTest({ ok: false, error: String(e) }); }
  };
  const entries = Object.entries(c.settings);
  return (
    <div className="py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{c.name}</span>
            <Badge>{c.type}</Badge>
            <span className="font-mono text-[11px] text-muted">{c.id}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
            {entries.length === 0 && <span>provider defaults</span>}
            {entries.map(([k, v]) => (
              <span key={k}>{k}: {isSecretRef(v)
                ? <span className="font-mono text-ink">🔒 {v.secret}</span>
                : <span className="text-ink">{typeof v === "object" ? JSON.stringify(v) : String(v)}</span>}</span>
            ))}
          </div>
          <div className="mt-1 text-xs text-muted">
            {c.used_by.length ? <>Used by {c.used_by.map(a => <Badge key={a} tone="info">{a}</Badge>)}</> : "Not used by any agent"}
            {c.updated_at && <> · updated {fmtTime(c.updated_at)}{c.updated_by ? ` by ${c.updated_by}` : ""}</>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={run} disabled={test === "running"}>{test === "running" ? "Testing…" : "Test"}</Button>
          <Button onClick={onEdit}>Edit</Button>
          {confirm
            ? <><Button kind="danger" onClick={() => del.mutate()} disabled={del.isPending}>Delete {c.name}</Button>
                <Button kind="ghost" onClick={() => setConfirm(false)}>Keep</Button></>
            : <Button kind="ghost" onClick={() => setConfirm(true)} disabled={c.used_by.length > 0}
                title={c.used_by.length ? "In use by an agent" : undefined}>Delete</Button>}
        </div>
      </div>
      <ErrorBox error={del.error} />
      {test && test !== "running" && <TestLine r={test} />}
    </div>
  );
}

function TestLine({ r }: { r: Record<string, unknown> }) {
  return (
    <div className={`mt-2 rounded-lg px-3 py-2 text-xs ${r.ok ? "bg-good/10 text-good" : "bg-bad/10 text-bad"}`}>
      {r.ok ? "✓ " : "✗ "}{Object.entries(r).filter(([k]) => k !== "ok").map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}
    </div>
  );
}

function EditConnection({ c, schema, secretNames, onDone, onCancel }: {
  c: Connection; schema: Parameters<typeof SchemaForm>[0]["schema"] | undefined; secretNames: string[];
  onDone: () => void; onCancel: () => void;
}) {
  const [name, setName] = useState(c.name);
  const [settings, setSettings] = useState<Record<string, unknown>>(structuredClone(c.settings));
  const save = useMutation({ mutationFn: () => api.updateConnection(c.id, { name, settings }), onSuccess: onDone });
  return (
    <div className="space-y-3 py-3">
      <label className="block max-w-sm">
        <div className="mb-1 text-xs font-medium">Name</div>
        <input className="w-full" value={name} onChange={e => setName(e.target.value)} />
      </label>
      {schema ? <SchemaForm schema={schema} value={settings} onChange={setSettings} secretNames={secretNames} />
        : <div className="text-sm text-muted">No settings for {c.type}.</div>}
      {c.used_by.length > 0 && (
        <p className="text-xs text-warn">Used by {c.used_by.join(", ")}: new calls on those agents use the change.</p>
      )}
      <ErrorBox error={save.error} />
      <div className="flex gap-2">
        <Button kind="primary" onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
        <Button kind="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

function AddConnection({ data, secretNames, onDone, onCancel }: {
  data: { available: Record<string, string[]>; schemas: Record<string, Record<string, Parameters<typeof SchemaForm>[0]["schema"]>> };
  secretNames: string[]; onDone: () => void; onCancel: () => void;
}) {
  const [kind, setKind] = useState("llm");
  const [type, setType] = useState(data.available.llm?.[0] ?? "groq");
  const [name, setName] = useState("");
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const create = useMutation({
    mutationFn: () => api.createConnection({ kind, type, name, settings }), onSuccess: onDone,
  });
  const schema = data.schemas[kind]?.[type];
  return (
    <Card title="New connection">
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="block">
            <div className="mb-1 text-xs font-medium">Kind</div>
            <select className="w-full" value={kind} onChange={e => {
              setKind(e.target.value); setType(data.available[e.target.value]?.[0] ?? ""); setSettings({});
            }}>
              {KINDS.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
            </select>
          </label>
          <label className="block">
            <div className="mb-1 text-xs font-medium">Provider type</div>
            <select className="w-full" value={type} onChange={e => { setType(e.target.value); setSettings({}); }}>
              {(data.available[kind] ?? []).map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="block">
            <div className="mb-1 text-xs font-medium">Name</div>
            <input className="w-full" placeholder="e.g. OpenAI-compatible KSA" value={name} onChange={e => setName(e.target.value)} />
          </label>
        </div>
        {schema && <SchemaForm schema={schema} value={settings} onChange={setSettings} secretNames={secretNames} />}
        <ErrorBox error={create.error} />
        <div className="flex gap-2">
          <Button kind="primary" onClick={() => create.mutate()} disabled={!name.trim() || create.isPending}>
            {create.isPending ? "Creating…" : "Create connection"}
          </Button>
          <Button kind="ghost" onClick={onCancel}>Cancel</Button>
        </div>
      </div>
    </Card>
  );
}
