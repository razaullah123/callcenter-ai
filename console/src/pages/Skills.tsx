import Editor, { DiffEditor } from "@monaco-editor/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { editorTheme } from "../monaco";
import { Badge, Button, Card, Empty, ErrorBox, cx, fmtTime } from "../ui";

const FILES = ["SKILL.md", "flow.yaml"] as const;

export default function Skills() {
  const { name } = useParams();
  const list = useQuery({ queryKey: ["skills"], queryFn: api.skills });
  const tools = useQuery({ queryKey: ["tools"], queryFn: api.tools });

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Skills</h1>
        <p className="text-sm text-muted">Each skill is a SKILL.md (instructions, routing keywords) and an optional flow.yaml (steps, tools per step). Saving validates against the tool catalog and applies to new turns immediately.</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <Card title="Skills">
          <ul className="space-y-1">
            {list.data?.map(s => (
              <li key={s.name}>
                <Link to={`/skills/${s.name}`} className={cx("block rounded-lg px-2.5 py-2", s.name === name ? "bg-soft" : "hover:bg-soft")}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{s.name}</span>
                    {s.status === "draft" ? <Badge tone="warn">draft</Badge> : s.has_flow ? <Badge tone="info">flow</Badge> : null}
                  </div>
                  <div className="line-clamp-2 text-xs text-muted">{s.description}</div>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
        {name ? <SkillEditor name={name} toolNames={tools.data?.map(t => t.name) ?? []} key={name} />
          : <Card><Empty>Select a skill to edit.</Empty></Card>}
      </div>
    </div>
  );
}

function SkillEditor({ name, toolNames }: { name: string; toolNames: string[] }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["skill", name], queryFn: () => api.skill(name) });
  const [files, setFiles] = useState<Record<string, string> | null>(null);
  const [tab, setTab] = useState<(typeof FILES)[number]>("SKILL.md");
  const [errors, setErrors] = useState<string[] | null>(null);
  const [note, setNote] = useState("");
  const [compare, setCompare] = useState<{ version: number; files: Record<string, string> } | null>(null);
  useEffect(() => { if (q.data && !files) setFiles({ ...q.data.files }); }, [q.data, files]);

  const save = useMutation({
    mutationFn: () => api.saveSkill(name, files!, note),
    onSuccess: () => {
      setNote(""); setErrors([]); setFiles(null); setCompare(null);
      qc.invalidateQueries({ queryKey: ["skill", name] }); qc.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (e: unknown) => {
      const d = (e as { detail?: { errors?: string[] } }).detail;
      setErrors(d?.errors ?? [String(e)]);
    },
  });
  if (!q.data || !files) return <ErrorBox error={q.error} />;
  const dirty = FILES.some(f => (files[f] ?? "") !== (q.data.files[f] ?? ""));

  const validate = async () => setErrors((await api.validateSkill(name, files)).errors);
  const openVersion = async (v: number) => setCompare({ version: v, files: (await api.skillVersion(name, v)).files });

  return (
    <div className="space-y-4">
      <Card title={<span className="font-mono">{name}</span>} actions={
        <>
          <input placeholder="What changed?" value={note} onChange={e => setNote(e.target.value)} className="w-48 !py-1 text-sm" />
          <Button onClick={validate}>Validate</Button>
          <Button onClick={() => { setFiles({ ...q.data.files }); setErrors(null); }} disabled={!dirty}>Discard</Button>
          <Button kind="primary" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>{save.isPending ? "Saving…" : "Save version"}</Button>
        </>}>
        <div className="mb-2 flex gap-1">
          {FILES.map(f => (
            <button key={f} onClick={() => setTab(f)}
              className={cx("rounded-md px-3 py-1 font-mono text-xs", tab === f ? "bg-soft font-medium" : "text-muted")}>
              {f}{(files[f] ?? "") !== (q.data.files[f] ?? "") && " •"}
            </button>
          ))}
        </div>
        {errors && (errors.length
          ? <div className="mb-2 rounded-lg bg-bad/10 px-3 py-2 text-xs text-bad">{errors.map(e => <div key={e}>✗ {e}</div>)}</div>
          : <div className="mb-2 rounded-lg bg-good/10 px-3 py-2 text-xs text-good">✓ Valid</div>)}
        <div className="overflow-hidden rounded-lg border border-line">
          {compare ? (
            <DiffEditor height="60vh" theme={editorTheme()} language={tab.endsWith(".md") ? "markdown" : "yaml"}
              original={compare.files[tab] ?? ""} modified={files[tab] ?? ""}
              options={{ renderSideBySide: true, readOnly: true, minimap: { enabled: false }, wordWrap: "on" }} />
          ) : (
            <Editor height="60vh" theme={editorTheme()} language={tab.endsWith(".md") ? "markdown" : "yaml"}
              value={files[tab] ?? ""} onChange={v => setFiles({ ...files, [tab]: v ?? "" })}
              options={{ minimap: { enabled: false }, wordWrap: "on", fontSize: 13, scrollBeyondLastLine: false }} />
          )}
        </div>
        {compare && (
          <div className="mt-2 flex items-center gap-2 text-xs">
            <span className="text-muted">Comparing v{compare.version} (left) with the editor (right).</span>
            <Button onClick={() => { setFiles({ ...compare.files }); setCompare(null); }}>Restore v{compare.version} into editor</Button>
            <Button kind="ghost" onClick={() => setCompare(null)}>Close diff</Button>
          </div>
        )}
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Versions">
          {q.data.versions.length ? (
            <ul className="space-y-1 text-sm">
              {q.data.versions.map(v => (
                <li key={v.version} className="flex items-center justify-between gap-2">
                  <span><b>v{v.version}</b> <span className="text-xs text-muted">{fmtTime(v.created_at)} · {v.author}{v.note ? ` · ${v.note}` : ""}</span></span>
                  <Button kind="ghost" onClick={() => openVersion(v.version)}>Diff</Button>
                </li>
              ))}
            </ul>
          ) : <Empty>No saved versions yet — the first save keeps the current file as v1.</Empty>}
        </Card>
        <Card title="Tool names (for flow.yaml)">
          <div className="flex max-h-56 flex-wrap gap-1 overflow-y-auto">
            {toolNames.map(t => (
              <button key={t} className="rounded bg-soft px-1.5 py-0.5 font-mono text-[11px]" title="Copy"
                onClick={() => navigator.clipboard?.writeText(t)}>{t}</button>
            ))}
          </div>
          <button className="mt-2 text-xs text-accent" onClick={() => navigate("/playground")}>Try it in the Playground →</button>
        </Card>
      </div>
    </div>
  );
}
