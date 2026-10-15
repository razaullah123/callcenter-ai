import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, type AgentSummary, type ImportResult } from "../api";
import { CI, CopyButton, MultiSelect, Pager, RowMenu, ViewMenu, loadColumns, saveColumns } from "../table";
import { Button, ErrorBox, cx } from "../ui";
import { usePermissions } from "../permissions";

const PAGE = 20;
const TYPES: [string, string][] = [["flow", "Flow Agent"], ["prompt", "Single Prompt"]];
const LANGS: [string, string][] = [["ar", "Arabic"], ["en", "English"]];
const COLUMNS = [
  { key: "name", label: "Name", fixed: true }, { key: "type", label: "Agent Type" }, { key: "language", label: "Language" },
  { key: "voice", label: "Voice" }, { key: "status", label: "Status" }, { key: "numbers", label: "Phone numbers" },
  { key: "created", label: "Created At" },
];
const COLS_KEY = "hmg.agents.columns.v1";

const ico = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
    strokeLinejoin="round">{d}</svg>
);
const A = {
  bolt: ico(<path d="M13 2 3 14h9l-1 8 10-12h-9z" />),
  flow: ico(<><circle cx="6" cy="6" r="3" /><circle cx="18" cy="18" r="3" /><path d="M9 6h5a4 4 0 0 1 4 4v5" /></>),
  upload: ico(<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M17 8l-5-5-5 5M12 3v12" /></>),
  info: ico(<><circle cx="12" cy="12" r="9" /><path d="M12 16v-4M12 8h.01" /></>),
};

const fmtDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" }) : "—");
const initials = (s: string) => s.replace(/[^A-Za-z؀-ۿ ]/g, "").slice(0, 2).toUpperCase() || "?";
const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function ActionCard({ icon, label, onClick, soon }: { icon: ReactNode; label: string; onClick?: () => void; soon?: string }) {
  return (
    <button onClick={onClick} disabled={!!soon} title={soon}
      className="flex min-w-36 flex-col items-start gap-3 rounded-xl border border-line bg-panel px-4 py-3 text-left text-sm font-medium transition hover:border-accent hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:border-line">
      <span className="text-accent-text">{icon}</span>
      <span>{label}{soon && <span className="ml-1.5 rounded bg-soft px-1 text-[10px] font-normal text-muted">Soon</span>}</span>
    </button>
  );
}

function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[20vh]" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-label={title} className="w-full max-w-md rounded-xl border border-line bg-panel p-5 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-muted hover:bg-soft hover:text-ink">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

function CreateDialog({ type, onClose }: { type: "flow" | "prompt"; onClose: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useMutation({
    mutationFn: () => api.createAgent({ name: name.trim(), description, type }),
    onSuccess: r => { qc.invalidateQueries({ queryKey: ["agents"] }); nav(`/agents/${r.id}`); },
  });
  return (
    <Modal title={type === "flow" ? "Create Flow Agent" : "Create Single Prompt Agent"} onClose={onClose}>
      <form className="space-y-3" onSubmit={e => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
        <label className="block"><div className="mb-1 text-xs font-medium">Enter agent name <span className="text-bad">*</span></div>
          <input id="new-agent-name" autoFocus maxLength={150} className="w-full" value={name} onChange={e => setName(e.target.value)} placeholder="Enter agent name" />
          <div className="mt-0.5 text-right text-[11px] text-muted">{name.length}/150</div></label>
        <label className="block"><div className="mb-1 text-xs font-medium">What it's for <span className="font-normal text-muted">(optional)</span></div>
          <input id="new-agent-desc" className="w-full" value={description} onChange={e => setDescription(e.target.value)} placeholder="e.g. Clinic information line" /></label>
        <p className="flex gap-1.5 text-xs text-muted"><span className="mt-px shrink-0 scale-75">{A.info}</span>
          {type === "flow"
            ? "Starts with one conversation node — build the call as a graph on the canvas (nodes, conditions, tools, transfers)."
            : "One prompt the model follows for the whole call — no graph. Good for information lines and simple assistants."}
          {" "}No caller verification; uses this project's models and tool server.</p>
        <ErrorBox error={create.error} />
        <div className="flex justify-end">
          <Button kind="primary" type="submit" disabled={!name.trim() || create.isPending}>{create.isPending ? "Creating…" : "Create"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function DeleteDialog({ agent, onClose }: { agent: AgentSummary; onClose: () => void }) {
  const qc = useQueryClient();
  const del = useMutation({
    mutationFn: () => api.deleteAgent(agent.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["agents"] }); onClose(); },
  });
  return (
    <Modal title="Delete agent" onClose={onClose}>
      <p className="text-sm">Delete <b>{agent.name}</b> with all its releases, draft and test cases? This can't be undone.
        Its past calls stay in Call History.</p>
      {agent.routes.length > 0 && <p className="mt-2 text-xs text-warn">It answers {agent.routes.map(r => r === "*" ? "all other numbers (default)" : r).join(", ")} — move those
        phone numbers to another agent first.</p>}
      <div className="mt-3"><ErrorBox error={del.error} /></div>
      <div className="mt-4 flex justify-end gap-2">
        <Button kind="ghost" onClick={onClose}>Cancel</Button>
        <Button kind="danger" onClick={() => del.mutate()} disabled={del.isPending}>{del.isPending ? "Deleting…" : "Delete agent"}</Button>
      </div>
    </Modal>
  );
}

const ENCRYPTED = /^[0-9a-f]{24}:[0-9a-f]{32}:[0-9a-f]+$/;

/** Import Agent: a Hamsa agent's JSON (what Hamsa's flow builder loads) → a new agent here. */
function ImportDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [file, setFile] = useState<{ name: string; size: number; text: string } | null>(null);
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const run = useMutation({
    mutationFn: () => api.importAgent({ content: file!.text, name: name.trim() || undefined, filename: file!.name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });
  const pick = async (f: File | undefined) => {
    if (!f) return;
    setProblem(null); run.reset();
    if (f.size > 20_000_000) { setProblem("The file is larger than 20 MB."); return; }
    const text = await f.text();
    if (ENCRYPTED.test(text.slice(0, 200).trim()) || (f.name.endsWith(".hamsa") && !text.trim().startsWith("{"))) {
      setFile(null);
      setProblem("This .hamsa export is encrypted by Hamsa — only Hamsa can open it. Import the agent's JSON instead: "
        + "the agent as Hamsa's flow builder loads it (api.tryhamsa.com/v2/voice-agents/<id>).");
      return;
    }
    try {
      const doc = JSON.parse(text);
      const agent = doc?.data?.workflow ? doc.data : doc;
      if (!agent?.workflow) { setProblem("This JSON isn't a Hamsa agent (it has no workflow)."); return; }
      setName(agent.name ?? "");
    } catch { setProblem("This file isn't JSON."); return; }
    setFile({ name: f.name, size: f.size, text });
  };
  const err = run.error instanceof ApiError ? run.error.detail : null;
  const errors = (err as { errors?: string[] } | null)?.errors;
  const r: ImportResult | undefined = run.data;
  return (
    <Modal title="Import Agent" onClose={onClose}>
      {r ? (
        <div className="space-y-3 text-sm">
          <p>✓ Imported <b>{r.name}</b> as a new agent ({r.stats.nodes} flow steps, {r.stats.edges} connections,
            {" "}{r.stats.tools} API tools, {r.stats.variables} variables).</p>
          {r.report.length > 0 && <div className="rounded-lg border border-line bg-soft/50 p-3">
            <div className="mb-1 text-xs font-medium">What changed on the way</div>
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted">{r.report.map((l, i) => <li key={i}>{l}</li>)}</ul>
          </div>}
          <p className="text-xs text-muted">It answers no phone number yet. Its API tools call the same endpoints as in Hamsa;
            while TOOLS_MODE isn't <code>live</code>, bookings, cancellations and messages are simulated unless
            HYBRID_LIVE_BOOKING is on.</p>
          <div className="flex justify-end gap-2">
            <Button kind="ghost" onClick={onClose}>Close</Button>
            <Button kind="primary" onClick={() => nav(`/agents/${r.id}`)}>Open agent →</Button>
          </div>
        </div>
      ) : (
        <form className="space-y-3" onSubmit={e => { e.preventDefault(); if (file) run.mutate(); }}>
          <label onDragOver={e => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
            onDrop={e => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]); }}
            className={cx("flex cursor-pointer flex-col items-center gap-1 rounded-lg border border-dashed px-4 py-6 text-center text-sm",
              drag ? "border-accent bg-accent/5" : "border-line hover:bg-soft/60")}>
            <span className="text-accent-text">{A.upload}</span>
            {file ? <span><b>{file.name}</b> <span className="text-muted">· {(file.size / 1024).toFixed(0)} KB</span></span>
              : <span>Drop the agent file here or <span className="font-medium text-accent-text">choose a file</span></span>}
            <span className="text-xs text-muted">A Hamsa agent's JSON (.json)</span>
            <input id="import-file" type="file" accept=".json,.hamsa,application/json" className="sr-only"
              onChange={e => pick(e.target.files?.[0])} />
          </label>
          {problem && <div className="rounded-lg border border-warn/40 bg-warn/10 p-2.5 text-xs text-warn">{problem}</div>}
          {file && <label className="block"><div className="mb-1 text-xs font-medium">Agent name</div>
            <input id="import-name" maxLength={150} className="w-full" value={name} onChange={e => setName(e.target.value)} /></label>}
          {run.error && !errors && <ErrorBox error={run.error} />}
          {errors && <div className="rounded-lg border border-bad/40 bg-bad/10 p-2.5 text-xs text-bad">
            <div className="font-medium">The imported agent wouldn't load:</div>
            <ul className="mt-1 list-disc pl-4">{errors.map((x, i) => <li key={i} className="break-words">{x}</li>)}</ul></div>}
          <p className="text-xs text-muted">The flow, prompts, variables and API tools come across; the LLM, voice and keys stay this
            project's (an API key in the file is never imported). You get a report of anything that changed.</p>
          <div className="flex justify-end gap-2">
            <Button kind="ghost" onClick={onClose}>Cancel</Button>
            <Button kind="primary" type="submit" disabled={!file || run.isPending}>{run.isPending ? "Importing…" : "Import"}</Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

export default function Agents() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const [params, setParams] = useSearchParams();
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v); else next.delete(k);
    if (!("page" in patch)) next.delete("page");
    setParams(next, { replace: true });
  };
  const search = params.get("q") ?? "";
  const types = (params.get("type") ?? "").split(",").filter(Boolean);
  const langs = (params.get("language") ?? "").split(",").filter(Boolean);
  const asc = params.get("dir") === "asc";
  const page = Math.max(0, Number(params.get("page") ?? 1) - 1) || 0;
  const [cols, setCols] = useState(() => loadColumns(COLS_KEY, COLUMNS.map(c => c.key)));
  const show = (k: string) => k === "name" || cols.includes(k);
  const [creating, setCreating] = useState<"flow" | "prompt" | null>(null);
  const [deleting, setDeleting] = useState<AgentSummary | null>(null);
  const [importing, setImporting] = useState(false);
  const { can } = usePermissions();
  const [notice, setNotice] = useState<string | null>(null);
  const duplicate = useMutation({
    mutationFn: async (a: AgentSummary) => {
      const names = new Set((q.data ?? []).map(x => x.name.toLowerCase()));
      let name = `${a.name} (Copy)`;
      for (let i = 2; names.has(name.toLowerCase()); i++) name = `${a.name} (Copy ${i})`;
      return api.createAgent({ name, description: a.description, copy_from: a.id });
    },
    onSuccess: (r, a) => { qc.invalidateQueries({ queryKey: ["agents"] }); setNotice(`Duplicated ${a.name} → ${r.id}`); },
    onError: e => setNotice(e instanceof ApiError ? e.message : String(e)),
  });

  const all = q.data ?? [];
  const needle = search.trim().toLowerCase();
  const rows = all
    .filter(a => !needle || a.name.toLowerCase().includes(needle) || a.id.toLowerCase().includes(needle))
    .filter(a => !types.length || types.includes(a.type ?? "flow"))
    .filter(a => !langs.length || (a.languages ?? []).some(l => langs.includes(l)))
    .sort((x, y) => (asc ? 1 : -1) * String(x.created_at ?? "").localeCompare(String(y.created_at ?? "")));
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const shown = rows.slice(page * PAGE, (page + 1) * PAGE);
  const filtered = Boolean(needle || types.length || langs.length);
  const cell = "px-4 py-3";

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-10">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-line pb-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Agents list</h1>
          <p className="mt-1 text-sm text-muted">Manage your agents and their settings.</p>
        </div>
        {can("agents", "create") && <div className="flex flex-wrap gap-3">
          <ActionCard icon={A.bolt} label="Create Prompt Agent" onClick={() => setCreating("prompt")} />
          <ActionCard icon={A.flow} label="Create Flow Agent" onClick={() => setCreating("flow")} />
          <ActionCard icon={A.upload} label="Import Agent" onClick={() => setImporting(true)} />
        </div>}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted">{CI.search}</span>
          <input id="agents-search" value={search} onChange={e => set({ q: e.target.value || null })} style={{ paddingLeft: "2rem" }}
            placeholder="Search agent by name or ID…" className="w-64 max-w-full !py-1.5 text-sm" />
        </div>
        <MultiSelect id="agents-type" label="Agent Type" options={TYPES} value={types} onChange={v => set({ type: v.join(",") || null })} />
        <MultiSelect id="agents-language" label="Language" options={LANGS} value={langs} onChange={v => set({ language: v.join(",") || null })} />
        {filtered && <button onClick={() => setParams(new URLSearchParams(), { replace: true })}
          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-soft hover:text-ink">{CI.reset}Reset</button>}
        <div className="ml-auto"><ViewMenu id="agents-view" columns={COLUMNS} shown={cols} onChange={v => { setCols(v); saveColumns(COLS_KEY, v); }} /></div>
      </div>

      {notice && <div className="flex items-center justify-between rounded-lg border border-line bg-panel px-3 py-2 text-sm">
        <span>{notice}</span><button onClick={() => setNotice(null)} className="text-muted hover:text-ink" aria-label="Dismiss">✕</button></div>}
      <ErrorBox error={q.error} />

      <div className="overflow-x-auto rounded-xl border border-line bg-panel">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              {show("name") && <th className={cx(cell, "font-medium")}>Name</th>}
              {show("type") && <th className={cx(cell, "font-medium")}>Agent Type</th>}
              {show("language") && <th className={cx(cell, "font-medium")}>Language</th>}
              {show("voice") && <th className={cx(cell, "font-medium")}>Voice</th>}
              {show("status") && <th className={cx(cell, "font-medium")}>Status</th>}
              {show("numbers") && <th className={cx(cell, "font-medium")}>Phone numbers</th>}
              {show("created") && <th className={cx(cell, "font-medium")}>
                <button onClick={() => set({ dir: asc ? null : "asc" })} className="inline-flex items-center gap-1 hover:text-ink">
                  Created At{asc ? CI.up : CI.down}</button></th>}
              <th className={cell} />
            </tr>
          </thead>
          <tbody>
            {shown.map(a => (
              <tr key={a.id} onClick={() => nav(`/agents/${a.id}`)} className="group cursor-pointer border-b border-line last:border-0 hover:bg-soft/60">
                <td className={cx(cell, "max-w-72")}>
                  <div className="truncate font-medium" title={a.name}>{a.name}</div>
                  <div className="flex items-center gap-0.5 text-xs text-muted">
                    <span className="truncate font-mono">{a.id}</span>
                    <span className="opacity-0 group-hover:opacity-100"><CopyButton text={a.id} label="Copy agent ID" /></span>
                  </div>
                </td>
                {show("type") && <td className={cx(cell, "whitespace-nowrap")}>{a.type === "prompt" ? "Single Prompt" : "Flow Agent"}</td>}
                {show("language") && <td className={cell}>
                  <div className="flex flex-wrap gap-1">
                    {(a.languages ?? []).map(l => (
                      <span key={l} className={cx("inline-flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-xs",
                        l === a.default_language && "font-medium")} title={l === a.default_language ? "Default language" : undefined}>
                        <span className={cx("rounded-sm px-1 text-[9px] font-bold text-white", l === "ar" ? "bg-[#006c35]" : "bg-[#3c3b6e]")}>{l.toUpperCase()}</span>
                        {LANGS.find(x => x[0] === l)?.[1] ?? l}
                      </span>
                    ))}
                  </div>
                </td>}
                {show("voice") && <td className={cell}>
                  {a.voice ? <span className="inline-flex items-center gap-2 whitespace-nowrap">
                    <span className="grid h-7 w-7 place-items-center rounded-full bg-accent/25 text-[10px] font-semibold text-accent-text">{initials(a.voice)}</span>
                    {title(a.voice)}</span> : <span className="text-muted">—</span>}
                </td>}
                {show("status") && <td className={cell}>
                  <div className="flex flex-wrap gap-1 text-xs">
                    {a.version ? <span className="rounded bg-good/15 px-1.5 py-0.5 font-medium text-good">Published v{a.version}</span>
                      : <span className="rounded bg-soft px-1.5 py-0.5 text-muted">Not published</span>}
                    {a.has_draft && <span className="rounded bg-warn/15 px-1.5 py-0.5 text-warn">Draft changes</span>}
                  </div>
                </td>}
                {show("numbers") && <td className={cx(cell, "text-xs")}>
                  {a.default && <span className="mr-1 rounded bg-accent/25 px-1.5 py-0.5 font-medium text-accent-text">Default</span>}
                  <span className="font-mono">{a.routes.filter(r => r !== "*").join(", ") || (a.default ? "" : "—")}</span>
                </td>}
                {show("created") && <td className={cx(cell, "whitespace-nowrap text-muted")}>{fmtDate(a.created_at)}</td>}
                <td className={cx(cell, "w-10 text-right")}>
                  <RowMenu items={[
                    { label: "Edit", icon: CI.pencil, onClick: () => nav(`/agents/${a.id}`) },
                    { label: "Duplicate Agent", icon: CI.duplicate, onClick: () => duplicate.mutate(a) },
                    { label: "Delete", icon: CI.trash, danger: true, onClick: () => setDeleting(a) },
                  ]} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!shown.length && <div className="py-16 text-center text-sm text-muted">
          {q.isLoading ? "Loading…" : filtered ? "No agents match these filters." : "No agents yet — create one above."}</div>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted">
        <span>{duplicate.isPending ? "Duplicating…" : `${rows.length} agent${rows.length === 1 ? "" : "s"}`} ·{" "}
          <Link to="/numbers" className="text-accent-text hover:underline">Which agent answers which number →</Link></span>
        {pages > 1 && <Pager page={page} pages={pages} onPage={p => set({ page: p ? String(p + 1) : null })} />}
      </div>

      {creating && <CreateDialog type={creating} onClose={() => setCreating(null)} />}
      {deleting && <DeleteDialog agent={deleting} onClose={() => setDeleting(null)} />}
      {importing && <ImportDialog onClose={() => setImporting(false)} />}
    </div>
  );
}
