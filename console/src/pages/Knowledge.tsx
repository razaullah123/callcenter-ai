import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError, type KbItem, type KbStatus } from "../api";
import { CI, MultiSelect, Pager, RowMenu, ViewMenu, loadColumns, saveColumns } from "../table";
import { Button, ErrorBox, cx } from "../ui";

// Knowledge base (Hamsa's page): documents and free text the project's agents search during calls.

const PAGE = 20;
const STATUS: [KbStatus, string, string][] = [
  ["completed", "Completed", "bg-good/15 text-good"],
  ["processing", "Processing", "bg-sky-500/15 text-sky-700 dark:text-sky-300"],
  ["completed_with_errors", "Completed with Errors", "bg-warn/15 text-warn"],
  ["failed", "Failed", "bg-bad/15 text-bad"],
];
const TYPES: [string, string][] = [["text", "Text"], ["file", "File"]];
const EXTS: [string, string][] = [["pdf", "PDF"], ["docx", "DOCX"], ["txt", "TXT"], ["md", "MD"], ["html", "HTML"], ["epub", "EPUB"]];
const USED: [string, string][] = [["yes", "Yes"], ["no", "No"]];
const COLUMNS = [
  { key: "name", label: "Name", fixed: true }, { key: "type", label: "Type" }, { key: "status", label: "Status" },
  { key: "size", label: "Size" }, { key: "extension", label: "Extension" }, { key: "used", label: "Used" },
  { key: "words", label: "Words" }, { key: "created", label: "Created At" },
];
const COLS_KEY = "hmg.kb.columns.v1";

const fmtSize = (b: number) => (b < 1024 ? `${b} B` : b < 1024 * 1024 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`);
const fmtDate = (iso: string) => new Date(iso).toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" });

const ico = (d: ReactNode, cls = "h-5 w-5") => (
  <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
    strokeLinejoin="round">{d}</svg>
);
const K = {
  doc: ico(<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></>),
  text: ico(<path d="M4 7V4h16v3M9 20h6M12 4v16" />),
  upload: ico(<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M17 8l-5-5-5 5M12 3v12" /></>, "h-7 w-7"),
  sparkle: ico(<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" />, "h-4 w-4"),
  eye: ico(<><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>, "h-4 w-4"),
  bot: ico(<><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M12 4v4M9 13h.01M15 13h.01" /></>, "h-4 w-4"),
  redo: ico(<><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></>, "h-4 w-4"),
  trash: ico(<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />, "h-4 w-4"),
};

function StatusBadge({ s }: { s: KbStatus }) {
  const x = STATUS.find(v => v[0] === s) ?? STATUS[1];
  return <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium", x[2])}>
    <span className={cx("h-1.5 w-1.5 rounded-full bg-current", s === "processing" && "animate-pulse")} />{x[1]}</span>;
}

function Modal({ title, sub, children, onClose, wide }: { title: string; sub?: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[14vh]" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-label={title} className={cx("w-full rounded-xl border border-line bg-panel p-5 shadow-xl", wide ? "max-w-2xl" : "max-w-lg")}>
        <div className="mb-4 flex items-start justify-between gap-3">
          <div><h2 className="text-base font-semibold">{title}</h2>{sub && <p className="mt-0.5 text-sm text-muted">{sub}</p>}</div>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-muted hover:bg-soft hover:text-ink">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

function AddDocument({ limits, onClose }: { limits: { file_bytes: number; extensions: string[] }; onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const save = useMutation({
    mutationFn: async () => {
      const buf = new Uint8Array(await file!.arrayBuffer());
      let bin = "";
      for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
      return api.addKbFile({ filename: file!.name, data: btoa(bin) });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["knowledge"] }); onClose(); },
  });
  const pick = (f: File | undefined) => {
    if (!f) return;
    save.reset();
    const ext = f.name.split(".").pop()?.toLowerCase() ?? "";
    if (ext === "doc") { setProblem("Old Word .doc files aren't supported — save it as .docx or PDF."); return; }
    if (![...limits.extensions].includes(ext)) { setProblem(`Unsupported file type .${ext}.`); return; }
    if (f.size > limits.file_bytes) { setProblem(`The file is larger than ${fmtSize(limits.file_bytes)}.`); return; }
    setProblem(null); setFile(f);
  };
  return (
    <Modal title="Add Document" sub="Upload a document to add it to your knowledge base." onClose={onClose}>
      <label onDragOver={e => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={e => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]); }}
        className={cx("flex cursor-pointer flex-col items-center gap-1.5 rounded-lg border border-dashed px-4 py-8 text-center",
          drag ? "border-accent bg-accent/5" : "border-line hover:bg-soft/60")}>
        <span className="text-muted">{K.upload}</span>
        {file ? <span className="text-sm"><b>{file.name}</b> <span className="text-muted">· {fmtSize(file.size)}</span></span>
          : <span className="text-sm font-medium">Drop files to upload <span className="font-normal text-muted">or</span> <span className="text-accent-text">choose a file</span></span>}
        <span className="text-xs text-muted">Max size: {fmtSize(limits.file_bytes)} · Supported: PDF, DOCX, TXT, MD, HTML, EPUB</span>
        <input id="kb-file" type="file" className="sr-only" accept=".pdf,.docx,.txt,.md,.html,.htm,.epub" onChange={e => pick(e.target.files?.[0])} />
      </label>
      {problem && <div className="mt-3 rounded-lg bg-warn/10 p-2.5 text-xs text-warn">{problem}</div>}
      <div className="mt-3"><ErrorBox error={save.error} /></div>
      <p className="mt-2 text-[11px] text-muted">Its text is extracted and indexed; the file itself isn't kept. Scanned PDFs (images) need OCR first.</p>
      <div className="mt-4 flex justify-end gap-2">
        <Button kind="ghost" onClick={() => { setFile(null); setProblem(null); save.reset(); }} disabled={!file}>Clear</Button>
        <Button kind="ghost" onClick={onClose}>Cancel</Button>
        <Button kind="primary" onClick={() => save.mutate()} disabled={!file || save.isPending}>{save.isPending ? "Uploading…" : "Save"}</Button>
      </div>
    </Modal>
  );
}

function AddText({ max, onClose }: { max: number; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const save = useMutation({
    mutationFn: () => api.addKbText({ name: name.trim(), content }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["knowledge"] }); onClose(); },
  });
  return (
    <Modal title="Add Free Text" sub="Add free text to your knowledge base." onClose={onClose}>
      <form className="space-y-3" onSubmit={e => { e.preventDefault(); if (name.trim() && content.trim()) save.mutate(); }}>
        <label className="block"><div className="mb-1 text-sm font-medium">Text Name</div>
          <input id="kb-text-name" autoFocus maxLength={200} className="w-full" placeholder="Enter text name" value={name} onChange={e => setName(e.target.value)} /></label>
        <label className="block"><div className="mb-1 text-sm font-medium">Text Content</div>
          <textarea id="kb-text-content" dir="auto" rows={9} maxLength={max} className="w-full" placeholder="Enter your text here…" value={content} onChange={e => setContent(e.target.value)} />
          <div className="text-right text-[11px] text-muted">{content.length.toLocaleString()} / {max.toLocaleString()}</div></label>
        <ErrorBox error={save.error} />
        <div className="flex justify-end gap-2">
          <Button kind="ghost" onClick={() => { setName(""); setContent(""); }}>Clear</Button>
          <Button kind="ghost" onClick={onClose}>Cancel</Button>
          <Button kind="primary" type="submit" disabled={!name.trim() || !content.trim() || save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
        </div>
      </form>
    </Modal>
  );
}

/** Adds the item to an agent's draft (its other items stay); live after Publish. */
function UseInAgent({ item, onClose }: { item: KbItem; onClose: () => void }) {
  const qc = useQueryClient();
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents });
  const [agent, setAgent] = useState("");
  const pick = agent || agents.data?.[0]?.id || "";
  const detail = useQuery({ queryKey: ["agent", pick], queryFn: () => api.agent(pick), enabled: !!pick });
  const current = detail.data?.bundle.knowledge?.items ?? [];
  const has = current.includes(item.id);
  const use = useMutation({
    mutationFn: () => api.useKb({ agent: pick, items: has ? current.filter(i => i !== item.id) : [...current, item.id] }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["knowledge"] }); qc.invalidateQueries({ queryKey: ["agent", pick] }); },
  });
  return (
    <Modal title={`Use “${item.name}” in an agent`} onClose={onClose}>
      {use.isSuccess ? (
        <div className="space-y-3 text-sm">
          <p>✓ {has ? "Removed from" : "Added to"} the <b>draft</b> of {agents.data?.find(a => a.id === pick)?.name}. Callers get it after you publish the agent.</p>
          <div className="flex justify-end gap-2"><Button kind="ghost" onClick={onClose}>Close</Button>
            <Link to={`/agents/${pick}`} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90">Open agent to publish →</Link></div>
        </div>
      ) : (
        <div className="space-y-3">
          <label className="block"><div className="mb-1 text-xs font-medium">Agent</div>
            <select id="kb-use-agent" className="w-full" value={pick} onChange={e => setAgent(e.target.value)}>
              {(agents.data ?? []).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <p className="text-xs text-muted">{detail.isLoading ? "…" : has ? "This agent's draft already searches this item."
            : `This agent's draft searches ${current.length} item${current.length === 1 ? "" : "s"}.`} The agent gets a
            “search the knowledge base” tool in every step and answers from what it finds.</p>
          <ErrorBox error={use.error} />
          <div className="flex justify-end gap-2">
            <Button kind="ghost" onClick={onClose}>Cancel</Button>
            <Button kind={has ? "danger" : "primary"} disabled={!pick || detail.isLoading || use.isPending} onClick={() => use.mutate()}>
              {has ? "Remove from draft" : "Add to draft"}</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function ViewItem({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["knowledge", id], queryFn: () => api.kbItem(id) });
  const it = q.data;
  return (
    <Modal title={it?.name ?? "…"} onClose={onClose} wide>
      {!it ? <ErrorBox error={q.error} /> : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <StatusBadge s={it.status} /><span>{it.type === "file" ? (it.extension ?? "").toUpperCase() : "Text"}</span>
            <span>· {fmtSize(it.size_bytes)}</span><span>· {it.words.toLocaleString()} words</span><span>· {it.chunks} parts</span>
            <span>· {fmtDate(it.created_at)}{it.created_by ? ` by ${it.created_by}` : ""}</span>
          </div>
          {it.error && <div className="rounded-lg bg-warn/10 p-2.5 text-xs text-warn">{it.error}</div>}
          <div className="text-xs">Used by: {it.used_by.length ? it.used_by.map(u => u.name).join(", ") : "no published agent"}
            {it.draft_by.length > 0 && <> · in the draft of {it.draft_by.map(u => u.name).join(", ")}</>}</div>
          <pre dir="auto" className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-lg bg-soft p-3 font-sans text-sm">{it.content}</pre>
        </div>
      )}
    </Modal>
  );
}

function TrySearch({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const run = useMutation({ mutationFn: () => api.searchKb({ query }) });
  return (
    <Modal title="Try a question" sub="What an agent finds when a caller asks this (all items of the project)." onClose={onClose} wide>
      <form className="flex gap-2" onSubmit={e => { e.preventDefault(); if (query.trim()) run.mutate(); }}>
        <input id="kb-try" autoFocus dir="auto" className="min-w-0 flex-1" placeholder="e.g. What are the visiting hours?" value={query} onChange={e => setQuery(e.target.value)} />
        <Button kind="primary" type="submit" disabled={!query.trim() || run.isPending}>{run.isPending ? "Searching…" : "Search"}</Button>
      </form>
      <div className="mt-3"><ErrorBox error={run.error} /></div>
      {run.data && (
        <div className="mt-3 space-y-2">
          <div className="text-[11px] text-muted">{run.data.mode === "hybrid" ? "Meaning + keywords" : "Keywords only (embedding service unavailable)"}</div>
          {run.data.results.length ? run.data.results.map((r, i) => (
            <div key={i} className="rounded-lg border border-line p-3">
              <div className="mb-1 text-xs font-medium text-accent-text">{r.source}</div>
              <div dir="auto" className="whitespace-pre-wrap text-sm">{r.text}</div>
            </div>
          )) : <p className="text-sm text-muted">Nothing relevant found.</p>}
        </div>
      )}
    </Modal>
  );
}

export default function Knowledge() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["knowledge"], queryFn: api.knowledge,
    refetchInterval: d => (d.state.data?.items.some(i => i.status === "processing") ? 2000 : false),
  });
  const [params, setParams] = useSearchParams();
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v); else next.delete(k);
    if (!("page" in patch)) next.delete("page");
    setParams(next, { replace: true });
  };
  const list = (k: string) => (params.get(k) ?? "").split(",").filter(Boolean);
  const search = params.get("q") ?? "";
  const [types, statuses, exts, used] = [list("type"), list("status"), list("ext"), list("used")];
  const sort = (params.get("sort") ?? "created") as "created" | "size" | "words";
  const asc = params.get("dir") === "asc";
  const page = Math.max(0, Number(params.get("page") ?? 1) - 1) || 0;
  const [cols, setCols] = useState(() => loadColumns(COLS_KEY, COLUMNS.map(c => c.key)));
  const show = (k: string) => k === "name" || cols.includes(k);
  const [dialog, setDialog] = useState<null | { kind: "doc" } | { kind: "text" } | { kind: "try" } | { kind: "view"; id: string }
    | { kind: "use"; item: KbItem } | { kind: "delete"; item: KbItem }>(null);
  const del = useMutation({ mutationFn: (id: string) => api.deleteKb(id), onSuccess: () => { qc.invalidateQueries({ queryKey: ["knowledge"] }); setDialog(null); } });
  const redo = useMutation({ mutationFn: (id: string) => api.reprocessKb(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge"] }) });

  const rows = useMemo(() => {
    const n = search.trim().toLowerCase();
    const key = (i: KbItem) => (sort === "size" ? i.size_bytes : sort === "words" ? i.words : new Date(i.created_at).getTime());
    return (q.data?.items ?? [])
      .filter(i => !n || i.name.toLowerCase().includes(n) || i.id.includes(n))
      .filter(i => !types.length || types.includes(i.type))
      .filter(i => !statuses.length || statuses.includes(i.status))
      .filter(i => !exts.length || exts.includes(i.extension ?? ""))
      .filter(i => !used.length || used.includes(i.used_by.length ? "yes" : "no"))
      .sort((a, b) => (asc ? 1 : -1) * (key(a) - key(b)));
  }, [q.data, search, types.join(), statuses.join(), exts.join(), used.join(), sort, asc]);   // eslint-disable-line react-hooks/exhaustive-deps
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const shown = rows.slice(page * PAGE, (page + 1) * PAGE);
  const filtered = Boolean(search || types.length || statuses.length || exts.length || used.length);
  const d = q.data;
  const pct = d ? Math.min(100, (100 * d.usage_bytes) / d.quota_bytes) : 0;
  const sortHead = (k: "created" | "size" | "words", label: string) => (
    <button onClick={() => set(sort === k ? { sort: k === "created" ? null : k, dir: asc ? null : "asc" } : { sort: k === "created" ? null : k, dir: null })}
      className="inline-flex items-center gap-1 hover:text-ink">{label}{sort === k ? (asc ? CI.up : CI.down) : <span className="opacity-50">{CI.sort}</span>}</button>
  );
  const cell = "px-4 py-3";

  return (
    <div className="mx-auto max-w-6xl space-y-5 pb-10">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-line pb-5">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Knowledge Base</h1>
          <p className="mt-1 text-sm text-muted">Documents and notes your agents search during calls.</p>
        </div>
        <div className="flex flex-wrap gap-3">
          {([["doc", K.doc, "Add Document"], ["text", K.text, "Add Free Text"]] as const).map(([k, icon, label]) => (
            <button key={k} id={`kb-add-${k}`} onClick={() => setDialog({ kind: k })}
              className="flex min-w-36 flex-col items-start gap-3 rounded-xl border border-line bg-panel px-4 py-3 text-left text-sm font-medium transition hover:border-accent hover:shadow-sm">
              <span className="text-accent-text">{icon}</span>{label}</button>
          ))}
        </div>
      </div>
      {d && (
        <div className="ml-auto w-64 text-[11px] text-muted">
          <div className="flex justify-between"><span>Knowledge base usage</span><span className="text-ink">{fmtSize(d.usage_bytes)} / {fmtSize(d.quota_bytes)}</span></div>
          <div className="mt-1 h-1.5 rounded bg-soft"><div className={cx("h-1.5 rounded", pct > 90 ? "bg-bad" : "bg-accent")} style={{ width: `${pct}%` }} /></div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted">{CI.search}</span>
          <input id="kb-search" value={search} onChange={e => set({ q: e.target.value || null })} style={{ paddingLeft: "2rem" }}
            placeholder="Search knowledge base items…" className="w-64 max-w-full !py-1.5 text-sm" />
        </div>
        <MultiSelect id="kb-type" label="Type" options={TYPES} value={types} onChange={v => set({ type: v.join(",") || null })} />
        <MultiSelect id="kb-status" label="Status" options={STATUS.map(s => [s[0], s[1]])} value={statuses} onChange={v => set({ status: v.join(",") || null })} />
        <MultiSelect id="kb-ext" label="Extension" options={EXTS} value={exts} onChange={v => set({ ext: v.join(",") || null })} />
        <MultiSelect id="kb-used" label="Used" options={USED} value={used} onChange={v => set({ used: v.join(",") || null })} />
        {filtered && <button onClick={() => setParams(new URLSearchParams(), { replace: true })}
          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-soft hover:text-ink">{CI.reset}Reset</button>}
        <div className="ml-auto flex items-center gap-2">
          <button id="kb-try-open" onClick={() => setDialog({ kind: "try" })} disabled={!d?.items.length}
            className="flex items-center gap-1.5 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft disabled:opacity-50">{K.sparkle}Try a question</button>
          <ViewMenu id="kb-view" columns={COLUMNS} shown={cols} onChange={v => { setCols(v); saveColumns(COLS_KEY, v); }} />
        </div>
      </div>

      <ErrorBox error={q.error ?? del.error ?? redo.error} />
      <div className="overflow-x-auto rounded-xl border border-line bg-panel">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className={cx(cell, "font-medium")}>Name</th>
              {show("type") && <th className={cx(cell, "font-medium")}>Type</th>}
              {show("status") && <th className={cx(cell, "font-medium")}>Status</th>}
              {show("size") && <th className={cx(cell, "font-medium")}>{sortHead("size", "Size")}</th>}
              {show("extension") && <th className={cx(cell, "font-medium")}>Extension</th>}
              {show("used") && <th className={cx(cell, "font-medium")}>Used</th>}
              {show("words") && <th className={cx(cell, "font-medium")}>{sortHead("words", "Words")}</th>}
              {show("created") && <th className={cx(cell, "font-medium")}>{sortHead("created", "Created At")}</th>}
              <th className={cell}><span className="sr-only">More actions</span></th>
            </tr>
          </thead>
          <tbody>
            {shown.map(i => (
              <tr key={i.id} onClick={() => setDialog({ kind: "view", id: i.id })} className="cursor-pointer border-b border-line last:border-0 hover:bg-soft/60">
                <td className={cx(cell, "max-w-72")}>
                  <div className="flex items-center gap-2"><span className="shrink-0 text-muted">{i.type === "file" ? K.doc : K.text}</span>
                    <span className="truncate font-medium" title={i.name}>{i.name}</span></div>
                  {i.error && <div className="mt-0.5 line-clamp-1 text-[11px] text-warn" title={i.error}>{i.error}</div>}
                </td>
                {show("type") && <td className={cell}>{i.type === "file" ? "File" : "Text"}</td>}
                {show("status") && <td className={cell}><StatusBadge s={i.status} /></td>}
                {show("size") && <td className={cx(cell, "tabular-nums text-muted")}>{fmtSize(i.size_bytes)}</td>}
                {show("extension") && <td className={cell}>{i.extension ? <span className="rounded border border-line bg-soft px-1.5 py-0.5 text-[11px] uppercase">{i.extension}</span> : "—"}</td>}
                {show("used") && <td className={cx(cell, "text-xs")}>
                  {i.used_by.length ? <span className="rounded bg-accent/25 px-1.5 py-0.5 font-medium text-accent-text" title={i.used_by.map(u => u.name).join(", ")}>Yes · {i.used_by.length}</span>
                    : i.draft_by.length ? <span className="rounded bg-warn/15 px-1.5 py-0.5 text-warn" title={i.draft_by.map(u => u.name).join(", ")}>In draft</span>
                    : <span className="text-muted">No</span>}
                </td>}
                {show("words") && <td className={cx(cell, "tabular-nums")}>{i.words.toLocaleString()}</td>}
                {show("created") && <td className={cx(cell, "whitespace-nowrap text-muted")}>{fmtDate(i.created_at)}</td>}
                <td className={cx(cell, "w-10 text-right")}>
                  <RowMenu items={[
                    { label: "View", icon: K.eye, onClick: () => setDialog({ kind: "view", id: i.id }) },
                    { label: "Use in agent…", icon: K.bot, onClick: () => setDialog({ kind: "use", item: i }) },
                    { label: "Process again", icon: K.redo, onClick: () => redo.mutate(i.id) },
                    { label: "Delete", icon: K.trash, danger: true, onClick: () => setDialog({ kind: "delete", item: i }) },
                  ]} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!shown.length && (
          <div className="grid place-items-center py-16 text-center">
            <span className="grid h-12 w-12 place-items-center rounded-full bg-soft text-muted">{K.doc}</span>
            <div className="mt-3 font-semibold">{q.isLoading ? "Loading…" : filtered ? "No items match these filters" : "No knowledge base items yet"}</div>
            {!filtered && !q.isLoading && <div className="mt-1 max-w-sm text-sm text-muted">Get started by adding your first knowledge base item. You can add documents or free text content.</div>}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted">
        <span>{rows.length} item{rows.length === 1 ? "" : "s"} · agents search them with a “search the knowledge base” tool — choose items per agent here or in the agent's Settings.</span>
        {pages > 1 && <Pager page={page} pages={pages} onPage={p => set({ page: p ? String(p + 1) : null })} />}
      </div>

      {dialog?.kind === "doc" && d && <AddDocument limits={d.limits} onClose={() => setDialog(null)} />}
      {dialog?.kind === "text" && <AddText max={d?.limits.text_chars ?? 25000} onClose={() => setDialog(null)} />}
      {dialog?.kind === "try" && <TrySearch onClose={() => setDialog(null)} />}
      {dialog?.kind === "view" && <ViewItem id={dialog.id} onClose={() => setDialog(null)} />}
      {dialog?.kind === "use" && <UseInAgent item={dialog.item} onClose={() => setDialog(null)} />}
      {dialog?.kind === "delete" && (
        <Modal title="Delete item" onClose={() => setDialog(null)}>
          <p className="text-sm">Delete <b>{dialog.item.name}</b>? This can't be undone.</p>
          {dialog.item.used_by.length > 0 && <p className="mt-2 text-xs text-warn">{dialog.item.used_by.map(u => u.name).join(", ")} search{dialog.item.used_by.length === 1 ? "es" : ""} it now — they simply won't find it any more.</p>}
          <div className="mt-3"><ErrorBox error={del.error instanceof ApiError ? del.error : null} /></div>
          <div className="mt-4 flex justify-end gap-2">
            <Button kind="ghost" onClick={() => setDialog(null)}>Cancel</Button>
            <Button kind="danger" onClick={() => del.mutate(dialog.item.id)} disabled={del.isPending}>{del.isPending ? "Deleting…" : "Delete"}</Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
