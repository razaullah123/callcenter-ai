import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, type LibraryTool, type McpServer, type ToolLibrary, type ToolPolicy } from "../api";
import { Badge, Button, Card, Empty, ErrorBox } from "../ui";

// Tool library: MCP servers, every tool's policy (what the harness knows about it), API-request tools.

const kindTone = (k: string) => (k === "write" ? "warn" : k === "send" ? "info" : "neutral") as "warn" | "info" | "neutral";
const json = (v: unknown) => (v == null ? "" : JSON.stringify(v, null, 2));

export default function Tools() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["tool-library"], queryFn: api.toolLibrary });
  const [editing, setEditing] = useState<{ name: string; group: string; policy: ToolPolicy; isNew: boolean } | null>(null);
  const [filter, setFilter] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["tool-library"] });
  const groups = useMemo(() => {
    const out: Record<string, LibraryTool[]> = {};
    for (const t of q.data?.tools ?? []) {
      if (filter && !`${t.name} ${t.group} ${t.description}`.toLowerCase().includes(filter.toLowerCase())) continue;
      (out[t.group] ??= []).push(t);
    }
    return out;
  }, [q.data, filter]);
  if (!q.data) return <ErrorBox error={q.error} />;
  const lib = q.data;

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Tools</h1>
          <p className="max-w-3xl text-sm text-muted">
            Everything agents can call: MCP servers' tools, built-in tools and API requests. A tool's policy says
            what the platform enforces — confirmation, the claim a success allows, caller verification, and named
            checks from a pack. Saving publishes a new release of each agent that has the tool.
          </p>
        </div>
        <Button kind="primary" onClick={() => setEditing({
          name: "", group: "home", isNew: true,
          policy: { kind: "read", source: "http", description: "", input_schema: { type: "object", properties: {} },
                    http: { method: "GET", url: "https://" } },
        })}>Add API tool</Button>
      </div>

      {editing && <PolicyEditor lib={lib} init={editing} onDone={() => { setEditing(null); refresh(); }}
        onCancel={() => setEditing(null)} />}

      <Servers lib={lib} onChanged={refresh} />

      <Card title={`Library · ${lib.tools.length} tools`}
        actions={<input id="tool-filter" placeholder="Filter…" value={filter} onChange={e => setFilter(e.target.value)} className="w-48" />}>
        {Object.keys(groups).length === 0 ? <Empty>No tools match.</Empty> : (
          <div className="space-y-5">
            {Object.entries(groups).sort().map(([group, tools]) => (
              <div key={group}>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{group}</div>
                <div className="divide-y divide-line">
                  {tools.map(t => <ToolRow key={t.name} t={t} onChanged={refresh}
                    onEdit={() => setEditing({ name: t.name, group: t.group, policy: structuredClone(t.policy), isNew: false })} />)}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {lib.discovered.length > 0 && (
        <Card title={`Available from servers, not in the library · ${lib.discovered.length}`}>
          <div className="divide-y divide-line">
            {lib.discovered.map(d => (
              <div key={d.name} className="flex items-start justify-between gap-3 py-2">
                <div className="min-w-0">
                  <div className="font-mono text-xs">{d.name} <span className="text-muted">· {d.server ?? "?"}</span></div>
                  <div className="line-clamp-2 text-xs text-muted">{d.description}</div>
                </div>
                <Button onClick={() => setEditing({ name: d.name, group: "home", isNew: true, policy: { kind: "read" } })}>Add</Button>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function ToolRow({ t, onEdit, onChanged }: { t: LibraryTool; onEdit: () => void; onChanged: () => void }) {
  const [testing, setTesting] = useState(false);
  const [args, setArgs] = useState("{}");
  const [result, setResult] = useState<unknown>(null);
  const [removeFrom, setRemoveFrom] = useState("");
  const test = useMutation({ mutationFn: () => api.testTool(t.name, JSON.parse(args || "{}")), onSuccess: setResult });
  const remove = useMutation({
    mutationFn: () => api.deleteTool(t.name, removeFrom === "__library" ? undefined : removeFrom),
    onSuccess: () => { setRemoveFrom(""); onChanged(); },
  });
  const p = t.policy;
  return (
    <div className="py-2.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-xs font-medium">{t.name}</span>
            <Badge tone={kindTone(p.kind)}>{p.kind}</Badge>
            {p.confirm && p.confirm !== "none" && <Badge tone="warn">confirm: {p.confirm}</Badge>}
            <Badge>{t.source}{t.server ? ` · ${t.server}` : ""}</Badge>
            {p.role && <Badge tone="info">{p.role}</Badge>}
            {p.backs && <Badge tone="good">backs “{p.backs}”</Badge>}
            {(p.hooks ?? []).map(h => <Badge key={h}>{h}</Badge>)}
            {!t.available && <Badge tone="bad">server not connected</Badge>}
          </div>
          {t.description && <div className="mt-0.5 line-clamp-2 text-xs text-muted">{t.description}</div>}
          <div className="mt-0.5 text-[11px] text-muted">
            {t.used_by.length ? <>Agents: {t.used_by.join(", ")}</> : "Not used by an agent"}
            {p.timeout_s != null && <> · timeout {p.timeout_s}s</>}{p.cache_ttl ? <> · cache {p.cache_ttl}s</> : null}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {p.kind === "read" && <Button onClick={() => setTesting(x => !x)}>{testing ? "Close" : "Test"}</Button>}
          <Button onClick={onEdit}>Edit policy</Button>
          <select id={`remove-${t.name}`} className="w-40 text-xs" value={removeFrom} onChange={e => setRemoveFrom(e.target.value)}>
            <option value="">Remove…</option>
            {t.used_by.map(a => <option key={a} value={a}>from {a}</option>)}
            {t.used_by.length === 0 && <option value="__library">from the library</option>}
          </select>
          {removeFrom && <Button kind="danger" onClick={() => remove.mutate()} disabled={remove.isPending}>Confirm</Button>}
        </div>
      </div>
      <ErrorBox error={remove.error} />
      {testing && (
        <div className="mt-2 space-y-2 rounded-lg bg-soft p-3">
          <div className="text-xs text-muted">Arguments (JSON). Only read tools run here.</div>
          <textarea id={`args-${t.name}`} className="w-full font-mono text-xs" rows={3} value={args} onChange={e => setArgs(e.target.value)} />
          <Button kind="primary" onClick={() => test.mutate()} disabled={test.isPending}>{test.isPending ? "Running…" : "Run"}</Button>
          <ErrorBox error={test.error} />
          {result != null && <pre className="max-h-64 overflow-auto rounded bg-panel p-2 text-[11px]">{json(result)}</pre>}
        </div>
      )}
      {!testing && result != null && <pre className="mt-2 max-h-40 overflow-auto rounded bg-soft p-2 text-[11px]">{json(result)}</pre>}
    </div>
  );
}

function PolicyEditor({ lib, init, onDone, onCancel }: {
  lib: ToolLibrary; init: { name: string; group: string; policy: ToolPolicy; isNew: boolean };
  onDone: () => void; onCancel: () => void;
}) {
  const [name, setName] = useState(init.name);
  const [group, setGroup] = useState(init.group);
  const [p, setP] = useState<ToolPolicy>(init.policy);
  const [raw, setRaw] = useState({ args: json(init.policy.args), schema: json(init.policy.input_schema),
                                   headers: json(init.policy.http?.headers) });
  const [agents, setAgents] = useState<string[]>([]);
  const [parseError, setParseError] = useState<string | null>(null);
  const set = (k: keyof ToolPolicy, v: unknown) => setP({ ...p, [k]: v === "" ? undefined : v });
  const http = p.source === "http";
  const save = useMutation({
    mutationFn: () => {
      const parse = (s: string) => (s.trim() ? JSON.parse(s) : undefined);
      let policy: ToolPolicy;
      try {
        policy = { ...p, args: parse(raw.args) };
        if (http) policy = { ...policy, input_schema: parse(raw.schema), http: { ...p.http, headers: parse(raw.headers) } };
        setParseError(null);
      } catch (e) { setParseError(`Invalid JSON: ${e}`); throw e; }
      return api.putTool(name, { group, policy, agents });
    },
    onSuccess: onDone,
  });
  const c = lib.choices;
  const field = (label: string, input: React.ReactNode, help?: string) => (
    <label className="block"><div className="mb-1 text-xs font-medium">{label}</div>{input}
      {help && <div className="mt-1 text-[11px] text-muted">{help}</div>}</label>
  );
  return (
    <Card title={init.isNew ? (http ? "New API tool" : `Add ${name} to the library`) : `Policy · ${name}`}>
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          {field("Name", <input id="tool-name" className="w-full font-mono" value={name} disabled={!init.isNew || !http}
            onChange={e => setName(e.target.value)} />)}
          {field("Group", <><input id="tool-group" list="tool-groups" className="w-full" value={group} onChange={e => setGroup(e.target.value)} />
            <datalist id="tool-groups">{c.groups.map(g => <option key={g} value={g} />)}</datalist></>,
            "Usually the skill that uses it; skills without a flow get their group's tools.")}
          {field("Kind", <select id="tool-kind" className="w-full" value={p.kind} onChange={e => set("kind", e.target.value)}>
            {c.kinds.map(k => <option key={k}>{k}</option>)}</select>, "write / send tools change or send something.")}
          {field("Confirmation", <select id="tool-confirm" className="w-full" value={p.confirm ?? (p.kind === "read" ? "none" : "affirm")}
            onChange={e => set("confirm", e.target.value)}>{c.confirm.map(k => <option key={k}>{k}</option>)}</select>,
            "readback: the agent reads the details back and the platform runs it on “yes”.")}
          {field("Timeout (s)", <input id="tool-timeout" type="number" className="w-full" value={p.timeout_s ?? ""}
            onChange={e => set("timeout_s", e.target.value === "" ? undefined : Number(e.target.value))} />)}
          {field("Cache (s)", <input id="tool-cache" type="number" className="w-full" value={p.cache_ttl ?? ""}
            onChange={e => set("cache_ttl", e.target.value === "" ? undefined : Number(e.target.value))} />, "Read tools only.")}
          {field("Success allows the claim", <select id="tool-backs" className="w-full" value={p.backs ?? ""} onChange={e => set("backs", e.target.value)}>
            <option value="">—</option>{c.claims.map(k => <option key={k}>{k}</option>)}</select>,
            "The agent may only say “booked / sent …” after this succeeds.")}
          {field("Line after success", <select id="tool-line" className="w-full" value={p.success_line ?? ""} onChange={e => set("success_line", e.target.value)}>
            <option value="">—</option>{c.phrases.map(k => <option key={k}>{k}</option>)}</select>)}
          {field("Verification role", <select id="tool-role" className="w-full" value={p.role ?? ""} onChange={e => set("role", e.target.value)}>
            <option value="">—</option>{c.roles.map(k => <option key={k}>{k}</option>)}</select>, "Caller verification the platform drives itself.")}
        </div>
        {p.role && field("Role arguments (JSON)", <textarea id="tool-args" className="w-full font-mono text-xs" rows={2} value={raw.args}
          onChange={e => setRaw({ ...raw, args: e.target.value })} />, 'e.g. {"mobile": "mobileNo"} — role parameter → this tool\'s argument')}
        <div>
          <div className="mb-1 text-xs font-medium">Checks and bookkeeping (named hooks)</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(c.hooks).map(([h, phases]) => (
              <label key={h} className="flex items-center gap-1.5 text-xs">
                <input type="checkbox" checked={(p.hooks ?? []).includes(h)} onChange={e => set("hooks",
                  e.target.checked ? [...(p.hooks ?? []), h] : (p.hooks ?? []).filter(x => x !== h))} />
                <span className="font-mono">{h}</span><span className="text-muted">({phases.join(", ")})</span>
              </label>
            ))}
          </div>
        </div>
        {http && (
          <div className="space-y-3 rounded-lg border border-line p-3">
            {field("What it does (the model reads this)", <textarea id="tool-desc" className="w-full" rows={2} value={p.description ?? ""}
              onChange={e => set("description", e.target.value)} />)}
            <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
              {field("Method", <select id="tool-method" className="w-full" value={p.http?.method ?? "GET"}
                onChange={e => setP({ ...p, http: { ...p.http, method: e.target.value } })}>{c.methods.map(m => <option key={m}>{m}</option>)}</select>)}
              {field("URL", <input id="tool-url" className="w-full font-mono text-xs" value={p.http?.url ?? ""}
                onChange={e => setP({ ...p, http: { ...p.http, url: e.target.value } })} />,
                "{arg} placeholders are filled from the arguments; the rest go in the query (GET) or JSON body.")}
            </div>
            {field("Headers (JSON)", <textarea id="tool-headers" className="w-full font-mono text-xs" rows={2} value={raw.headers}
              onChange={e => setRaw({ ...raw, headers: e.target.value })} />, 'Keys as secrets: {"Authorization": {"secret": "MY_API_KEY"}}')}
            {field("Arguments (JSON schema)", <textarea id="tool-schema" className="w-full font-mono text-xs" rows={5} value={raw.schema}
              onChange={e => setRaw({ ...raw, schema: e.target.value })} />)}
          </div>
        )}
        {init.isNew && lib.agents.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <span className="font-medium">Give it to:</span>
            {lib.agents.map(a => (
              <label key={a.id} className="flex items-center gap-1.5">
                <input type="checkbox" checked={agents.includes(a.id)}
                  onChange={e => setAgents(e.target.checked ? [...agents, a.id] : agents.filter(x => x !== a.id))} />{a.name}
              </label>
            ))}
            <span className="text-muted">(none ticked: the default agent)</span>
          </div>
        )}
        {parseError && <ErrorBox error={parseError} />}
        <ErrorBox error={save.error} />
        <div className="flex gap-2">
          <Button kind="primary" onClick={() => save.mutate()} disabled={!name || !group || save.isPending}>
            {save.isPending ? "Saving…" : "Save and publish"}
          </Button>
          <Button kind="ghost" onClick={onCancel}>Cancel</Button>
        </div>
      </div>
    </Card>
  );
}

function Servers({ lib, onChanged }: { lib: ToolLibrary; onChanged: () => void }) {
  const [form, setForm] = useState<{ id?: string; name: string; url: string; header: string; scheme: string;
                                     secret: string; token: string } | null>(null);
  const [preview, setPreview] = useState<{ ok: boolean; tools?: { name: string }[]; error?: string } | null>(null);
  const body = () => form && ({
    name: form.name, url: form.url, transport: "streamable_http",
    auth: { header: form.header || "Authorization", scheme: form.scheme, ...(form.secret ? { secret: form.secret } : {}),
            ...(form.token ? { token: form.token } : {}) },
  });
  const discover = useMutation({ mutationFn: () => api.discoverServer(body()!), onSuccess: setPreview });
  const save = useMutation({
    mutationFn: () => (form!.id ? api.updateServer(form!.id, body()!) : api.addServer(body()!)),
    onSuccess: () => { setForm(null); setPreview(null); onChanged(); },
  });
  const del = useMutation({ mutationFn: (id: string) => api.deleteServer(id), onSuccess: onChanged });
  const edit = (s: McpServer) => setForm({ id: s.id, name: s.name, url: s.url, header: s.auth?.header ?? "Authorization",
                                          scheme: s.auth?.scheme ?? "Bearer", secret: s.auth?.secret ?? "", token: "" });
  return (
    <Card title="MCP servers" actions={!form && <Button onClick={() => setForm({ name: "", url: "https://", header: "Authorization",
      scheme: "Bearer", secret: "", token: "" })}>Add server</Button>}>
      <div className="divide-y divide-line">
        {lib.servers.map(s => (
          <div key={s.id} className="flex flex-wrap items-center justify-between gap-3 py-2">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium">{s.name}</span>
                {s.status.connected ? <Badge tone="good">connected · {s.status.tools.length} tools</Badge> : <Badge tone="bad">not connected</Badge>}
              </div>
              <div className="truncate font-mono text-[11px] text-muted">{s.url}{s.auth?.secret ? ` · 🔒 ${s.auth.secret}` : ""}</div>
              {s.status.error && <div className="text-[11px] text-bad">{s.status.error}</div>}
            </div>
            <div className="flex gap-2">
              <Button onClick={() => edit(s)}>Edit</Button>
              <Button kind="ghost" onClick={() => del.mutate(s.id)}>Delete</Button>
            </div>
          </div>
        ))}
        {lib.servers.length === 0 && <Empty>No MCP server yet.</Empty>}
      </div>
      <ErrorBox error={del.error} />
      {form && (
        <div className="mt-3 space-y-3 rounded-lg border border-line p-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block"><div className="mb-1 text-xs font-medium">Name</div>
              <input id="srv-name" className="w-full font-mono" value={form.name} disabled={!!form.id} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
            <label className="block"><div className="mb-1 text-xs font-medium">URL (streamable HTTP)</div>
              <input id="srv-url" className="w-full font-mono text-xs" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} /></label>
            <label className="block"><div className="mb-1 text-xs font-medium">Auth header / scheme</div>
              <div className="flex gap-2"><input id="srv-header" className="min-w-0 flex-1" value={form.header} onChange={e => setForm({ ...form, header: e.target.value })} />
                <input id="srv-scheme" className="w-24" value={form.scheme} onChange={e => setForm({ ...form, scheme: e.target.value })} /></div></label>
            <label className="block"><div className="mb-1 text-xs font-medium">Token</div>
              <input id="srv-token" type="password" className="w-full" autoComplete="off"
                placeholder={form.secret ? `stored as ${form.secret} — paste to replace` : "paste a token (stored encrypted)"}
                value={form.token} onChange={e => setForm({ ...form, token: e.target.value })} /></label>
          </div>
          {preview && (preview.ok
            ? <div className="rounded-lg bg-good/10 px-3 py-2 text-xs text-good">✓ {preview.tools?.length} tools: {preview.tools?.slice(0, 12).map(t => t.name).join(", ")}{(preview.tools?.length ?? 0) > 12 ? " …" : ""}</div>
            : <div className="rounded-lg bg-bad/10 px-3 py-2 text-xs text-bad">✗ {preview.error}</div>)}
          <ErrorBox error={discover.error ?? save.error} />
          <div className="flex gap-2">
            <Button onClick={() => discover.mutate()} disabled={discover.isPending}>{discover.isPending ? "Connecting…" : "Discover tools"}</Button>
            <Button kind="primary" onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save and connect"}</Button>
            <Button kind="ghost" onClick={() => { setForm(null); setPreview(null); }}>Cancel</Button>
          </div>
        </div>
      )}
    </Card>
  );
}
