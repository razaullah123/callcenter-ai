import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError, type AgentDetail, type AuditRow, type CaseSpec, type AgentCase, type GateStatus, type ReleaseGate } from "../api";
import { Badge, Button, Card, cx, fmtTime } from "../ui";

// Phase 12.7: the agent's test cases, the publish gate and the audit trail. Gate cases run on the draft (simulated
// caller + fixture hospital backend); Publish needs all of them passing on this exact draft, or a written reason.

const EXPECT_HELP = `verified, handoff, booked (true/false) · tools_called [in order] · tools_not_called [..] ·
slots {name: value} · language ar/en · gender male/female · say [regex] · never_say [regex] ·
max_words_per_reply n · max_questions_per_reply n · max_turns n · booked_first_doctor · booked_offered_doctor`;

export const detailOf = (e: unknown): string => {
  if (e instanceof ApiError) {
    const d = e.detail as { errors?: string[]; message?: string } | string;
    if (typeof d === "string") return d;
    if (d?.errors) return d.errors.join(" · ");
    if (d?.message) return d.message;
  }
  return e instanceof Error ? e.message : e ? String(e) : "";
};

export function useAgentEvals(agentId: string) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["agent-evals", agentId], queryFn: () => api.agentEvals(agentId),
    refetchInterval: query => (query.state.data?.job?.status === "running" ? 1500 : false),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["agent-evals", agentId] });
  const run = useMutation({ mutationFn: (body: { cases?: string[]; only?: "gate" | "failed" | "all" }) => api.runAgentEvals(agentId, body),
                            onSuccess: refresh });
  const running = q.data?.job?.status === "running";
  return { q, run, running, refresh };
}

export function gateSummary(g: GateStatus | undefined): string {
  if (!g) return "…";
  if (!g.required) return "no gate cases";
  return `${g.passed.length}/${g.required} passed` + (g.failed.length ? ` · ${g.failed.length} failed` : "")
    + (g.not_run.length ? ` · ${g.not_run.length} not run` : "");
}

function JobProgress({ job }: { job: NonNullable<ReturnType<typeof useAgentEvals>["q"]["data"]>["job"] }) {
  if (!job || job.status !== "running") return null;
  const done = job.done ?? [];
  const pct = job.total ? Math.round((100 * done.length) / job.total) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-xs"><span className="animate-pulse">●</span>
        Running {done.length}/{job.total} — {done.filter(d => d.passed).length} passed, {done.filter(d => !d.passed).length} failed so far</div>
      <div className="h-1.5 overflow-hidden rounded bg-soft"><div className="h-1.5 bg-accent transition-all" style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export function GateBox({ agentId, compact }: { agentId: string; compact?: boolean }) {
  const { q, run, running } = useAgentEvals(agentId);
  const g = q.data?.gate;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={!g ? "neutral" : g.ok ? "good" : g.failed.length ? "bad" : "warn"}>
          {g?.ok ? "✓ checks passed" : g?.failed.length ? "✗ checks failing" : "checks not run"}</Badge>
        <span className="text-xs text-muted">{gateSummary(g)} on this draft</span>
        <span className="ml-auto flex gap-1">
          <Button onClick={() => run.mutate({ only: "gate" })} disabled={running || run.isPending || !g?.required}>▷ Run gate cases</Button>
          {!!g && g.passed.length > 0 && !g.ok && <Button kind="ghost" onClick={() => run.mutate({ only: "failed" })}
            disabled={running || run.isPending}>Run the {g.failed.length + g.not_run.length} left</Button>}
        </span>
      </div>
      <JobProgress job={q.data?.job ?? null} />
      {q.data?.job?.status === "error" && <div className="text-xs text-bad">Run failed: {q.data.job.error}</div>}
      {run.error && <div className="text-xs text-bad">{detailOf(run.error)}</div>}
      {!compact && g && !g.ok && g.failed.length > 0 && (
        <ul className="space-y-0.5 text-xs">{g.failed.map(id => <li key={id}><span className="font-mono">{id}</span>
          <span className="text-muted"> — {g.results[id]?.failed.join("; ")}</span></li>)}</ul>)}
    </div>
  );
}

export function PublishDialog({ d, onClose, onPublished }: { d: AgentDetail; onClose: () => void; onPublished: () => void }) {
  const { q } = useAgentEvals(d.agent.id);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [override, setOverride] = useState(false);
  const qc = useQueryClient();
  const g = q.data?.gate;
  const publish = useMutation({
    mutationFn: () => api.publishAgent(d.agent.id, note, override ? reason : undefined),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["agent-audit", d.agent.id] }); onPublished(); onClose(); },
  });
  const canPublish = g?.ok || (override && reason.trim().length >= 10);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-[10vh]" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-lg space-y-3 rounded-xl border border-line bg-panel p-4 shadow-xl">
        <div className="flex items-center justify-between">
          <div className="text-base font-semibold">Publish {d.agent.name}</div>
          <button className="text-muted hover:text-ink" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <p className="text-xs text-muted">Publishing makes the draft the live version: new calls get it, calls in progress keep theirs.
          The agent's gate test cases must pass on this draft first.</p>
        <GateBox agentId={d.agent.id} />
        <label className="block"><div className="mb-1 text-xs font-medium">What changed?</div>
          <input id="publish-note" className="w-full" value={note} onChange={e => setNote(e.target.value)} placeholder="e.g. one flow for booking" /></label>
        {g && !g.ok && (
          <div className="rounded-lg border border-warn/40 p-2">
            <label className="flex items-center gap-2 text-xs font-medium">
              <input id="publish-override" type="checkbox" checked={override} onChange={e => setOverride(e.target.checked)} />
              Publish without passing checks</label>
            {override && <>
              <textarea id="override-reason" rows={2} className="mt-2 w-full text-xs" value={reason} onChange={e => setReason(e.target.value)}
                placeholder="Why it can't wait for the checks (recorded on the release and in the audit log)" />
              <div className="text-[11px] text-muted">{reason.trim().length < 10 ? "At least 10 characters." : "Recorded with your name."}</div>
            </>}
          </div>
        )}
        {publish.error && <div className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">{detailOf(publish.error)}</div>}
        <div className="flex justify-end gap-2">
          <Button kind="ghost" onClick={onClose}>Cancel</Button>
          <Button kind={override && !g?.ok ? "danger" : "primary"} onClick={() => publish.mutate()} disabled={!canPublish || publish.isPending}>
            {publish.isPending ? "Publishing…" : override && !g?.ok ? "Publish anyway" : "⇪ Publish"}</Button>
        </div>
      </div>
    </div>
  );
}

const BLANK: CaseSpec = { suite: "general", title: "", caller: { language: "ar", script: [""] }, expect: {}, max_turns: 18 };

function CaseEditor({ agentId, initial, existing, onDone }: {
  agentId: string; initial: AgentCase | null; existing: string[]; onDone: () => void;
}) {
  const [id, setId] = useState(initial?.id ?? "");
  const [spec, setSpec] = useState<CaseSpec>(initial?.spec ?? BLANK);
  const [gate, setGate] = useState(initial?.gate ?? true);
  const [scripted, setScripted] = useState(initial ? !!initial.spec.caller.script : true);
  const [expectText, setExpectText] = useState(JSON.stringify(initial?.spec.expect ?? {}, null, 2));
  const [factsText, setFactsText] = useState(JSON.stringify(initial?.spec.caller.facts ?? {}, null, 2));
  const [localErr, setLocalErr] = useState("");
  const save = useMutation({
    mutationFn: () => {
      let expect: Record<string, unknown>, facts: Record<string, string>;
      try { expect = JSON.parse(expectText || "{}"); facts = JSON.parse(factsText || "{}"); }
      catch { throw new Error("Expectations and facts must be valid JSON."); }
      const { script, goal, persona, opening, ...rest } = spec.caller;
      const caller = scripted ? { ...rest, script: (script ?? []).filter(l => l.trim()) }
        : { ...rest, goal, persona, opening, ...(Object.keys(facts).length ? { facts } : {}) };
      if (!initial && existing.includes(id)) throw new Error("A case with this id exists.");
      return api.putAgentEval(agentId, id, { ...spec, caller, expect }, gate);
    },
    onSuccess: onDone,
    onError: e => setLocalErr(detailOf(e)),
  });
  const c = spec.caller;
  const setCaller = (x: Partial<CaseSpec["caller"]>) => setSpec(s => ({ ...s, caller: { ...s.caller, ...x } }));
  return (
    <Card title={initial ? `Edit ${initial.id}` : "New test case"}>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="block"><div className="mb-1 text-xs font-medium">Id</div>
          <input id="case-id" className="w-full font-mono text-xs" value={id} disabled={!!initial} placeholder="book_by_name_ar"
            onChange={e => setId(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "_"))} /></label>
        <label className="block"><div className="mb-1 text-xs font-medium">Title</div>
          <input id="case-title" className="w-full" value={spec.title ?? ""} onChange={e => setSpec({ ...spec, title: e.target.value })} /></label>
        <label className="block"><div className="mb-1 text-xs font-medium">Group</div>
          <input id="case-suite" className="w-full" value={spec.suite ?? ""} onChange={e => setSpec({ ...spec, suite: e.target.value })} /></label>
        <div className="flex items-end gap-4">
          <label className="block"><div className="mb-1 text-xs font-medium">Language</div>
            <select id="case-lang" value={c.language ?? "ar"} onChange={e => setCaller({ language: e.target.value as "ar" | "en" })}>
              <option value="ar">Arabic</option><option value="en">English</option></select></label>
          <label className="block"><div className="mb-1 text-xs font-medium">Max turns</div>
            <input id="case-turns" type="number" min={1} max={40} className="w-20" value={spec.max_turns ?? 18}
              onChange={e => setSpec({ ...spec, max_turns: Number(e.target.value) })} /></label>
          <label className="mb-1.5 flex items-center gap-2 text-xs font-medium">
            <input id="case-gate" type="checkbox" checked={gate} onChange={e => setGate(e.target.checked)} /> Gates Publish</label>
        </div>
      </div>
      <div className="mt-3 flex gap-1">
        <Button kind={scripted ? "default" : "ghost"} onClick={() => setScripted(true)}>Script (exact lines)</Button>
        <Button kind={!scripted ? "default" : "ghost"} onClick={() => setScripted(false)}>Simulated caller (goal)</Button>
      </div>
      {scripted ? (
        <label className="mt-2 block"><div className="mb-1 text-xs font-medium">The caller says, one line per turn</div>
          <textarea id="case-script" rows={5} dir="auto" className="w-full text-xs" value={(c.script ?? []).join("\n")}
            onChange={e => setCaller({ script: e.target.value.split("\n") })} /></label>
      ) : (
        <div className="mt-2 grid gap-3 md:grid-cols-2">
          <label className="block"><div className="mb-1 text-xs font-medium">Goal</div>
            <textarea id="case-goal" rows={3} dir="auto" className="w-full text-xs" value={c.goal ?? ""} onChange={e => setCaller({ goal: e.target.value })} /></label>
          <label className="block"><div className="mb-1 text-xs font-medium">Persona</div>
            <textarea id="case-persona" rows={3} dir="auto" className="w-full text-xs" value={c.persona ?? ""} onChange={e => setCaller({ persona: e.target.value })} /></label>
          <label className="block"><div className="mb-1 text-xs font-medium">First words</div>
            <input id="case-opening" dir="auto" className="w-full" value={c.opening ?? ""} onChange={e => setCaller({ opening: e.target.value })} /></label>
          <label className="block"><div className="mb-1 text-xs font-medium">Facts the caller knows (JSON)</div>
            <textarea id="case-facts" rows={3} dir="auto" className="w-full font-mono text-[11px]" value={factsText} onChange={e => setFactsText(e.target.value)} /></label>
        </div>
      )}
      <label className="mt-3 block"><div className="mb-1 text-xs font-medium">Expectations (JSON)</div>
        <textarea id="case-expect" rows={6} className="w-full font-mono text-[11px]" value={expectText} onChange={e => setExpectText(e.target.value)} />
        <div className="whitespace-pre-line text-[11px] text-muted">{EXPECT_HELP}</div></label>
      {localErr && <div className="mt-2 text-xs text-bad">{localErr}</div>}
      <div className="mt-3 flex justify-end gap-2">
        <Button kind="ghost" onClick={onDone}>Cancel</Button>
        <Button kind="primary" onClick={() => { setLocalErr(""); save.mutate(); }} disabled={!id || save.isPending}>{save.isPending ? "Saving…" : "Save case"}</Button>
      </div>
    </Card>
  );
}

export default function TestsTab({ d }: { d: AgentDetail }) {
  const { q, run, running, refresh } = useAgentEvals(d.agent.id);
  const [editing, setEditing] = useState<AgentCase | "new" | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const toggle = useMutation({ mutationFn: (c: AgentCase) => api.putAgentEval(d.agent.id, c.id, c.spec, !c.gate), onSuccess: refresh });
  const del = useMutation({ mutationFn: (id: string) => api.deleteAgentEval(d.agent.id, id), onSuccess: () => { setConfirmDel(null); refresh(); } });
  const cases = q.data?.cases ?? [];
  const g = q.data?.gate;
  const live = Object.fromEntries((q.data?.job?.status === "running" ? q.data.job.done ?? [] : []).map(x => [x.case, x]));
  const suites = [...new Set(cases.map(c => c.spec.suite || "general"))];
  return (
    <div className="space-y-4">
      <Card title="Publish gate">
        {!q.data?.has_draft && <p className="mb-2 text-xs text-muted">No draft: runs go against the live release and don't count for the gate.</p>}
        <GateBox agentId={d.agent.id} compact />
      </Card>
      {editing && <CaseEditor key={editing === "new" ? "new" : editing.id} agentId={d.agent.id} initial={editing === "new" ? null : editing}
        existing={cases.map(c => c.id)} onDone={() => { setEditing(null); refresh(); }} />}
      <Card title={`Test cases (${cases.length})`} actions={<>
        <Button onClick={() => run.mutate({ only: "all" })} disabled={running || run.isPending || !cases.length}>Run all</Button>
        <Button kind="primary" onClick={() => setEditing("new")}>＋ New case</Button></>}>
        {!cases.length && <div className="py-6 text-center text-sm text-muted">No test cases yet. Add one: what the caller says, and what must happen.</div>}
        {suites.map(suite => (
          <div key={suite} className="mb-3">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">{suite}</div>
            <table className="w-full text-sm"><tbody>
              {cases.filter(c => (c.spec.suite || "general") === suite).map(c => {
                const r = g?.results[c.id], now = live[c.id];
                const state = now ? (now.passed ? "passed" : "failed") : r ? (r.passed ? "passed" : "failed") : "not run";
                return (
                  <tr key={c.id} className="border-b border-line align-top last:border-0">
                    <td className="w-8 py-2"><input type="checkbox" title="Gates Publish" checked={c.gate} onChange={() => toggle.mutate(c)} /></td>
                    <td className="py-2">
                      <div className="font-mono text-xs">{c.id}</div>
                      <div className="text-xs text-muted">{c.spec.title}</div>
                      {state === "failed" && <div className="mt-0.5 text-[11px] text-bad">{(now?.failed ?? r?.failed ?? []).join("; ")}</div>}
                    </td>
                    <td className="py-2 text-xs text-muted">{c.spec.caller.language ?? "ar"} · {c.spec.caller.script ? "script" : "simulated"}</td>
                    <td className="py-2"><Badge tone={state === "passed" ? "good" : state === "failed" ? "bad" : "neutral"}>{state}</Badge>
                      {!c.gate && <span className="ml-1 text-[10px] text-muted">info only</span>}</td>
                    <td className="whitespace-nowrap py-2 text-right">
                      <Button kind="ghost" onClick={() => run.mutate({ cases: [c.id] })} disabled={running || run.isPending}>▷</Button>
                      <Button kind="ghost" onClick={() => setEditing(c)}>Edit</Button>
                      {confirmDel === c.id
                        ? <><Button kind="danger" onClick={() => del.mutate(c.id)}>Delete</Button><Button kind="ghost" onClick={() => setConfirmDel(null)}>Keep</Button></>
                        : <Button kind="ghost" onClick={() => setConfirmDel(c.id)}>🗑</Button>}
                    </td>
                  </tr>
                );
              })}
            </tbody></table>
          </div>
        ))}
      </Card>
    </div>
  );
}

const ACTIONS: Record<string, string> = {
  publish: "Published", "publish.override": "Published without passing checks", activate: "Made an older version live",
  "draft.discarded": "Discarded the draft", "case.created": "Added a test case", "case.changed": "Changed a test case",
  "case.deleted": "Deleted a test case",
};

function auditText(a: AuditRow): string {
  const x = a.detail as Record<string, unknown> & { version?: number; case?: string; note?: string; override?: { reason: string } };
  if (a.action.startsWith("publish")) return `v${x.version}${x.note ? ` — ${x.note}` : ""}${x.override ? ` · reason: ${x.override.reason}` : ""}`;
  if (a.action === "activate") return `v${x.version}`;
  if (a.action.startsWith("case.")) return `${x.case}${"gate_was" in x ? (x.gate ? " · now gates Publish" : " · no longer gates Publish") : ""}`;
  return "";
}

export function gateBadge(gate: ReleaseGate | null | undefined) {
  if (!gate) return <span className="text-[11px] text-muted">—</span>;
  if (gate.override) return <span title={gate.override.reason}><Badge tone="warn">override</Badge></span>;
  if (!gate.checks.required) return <span className="text-[11px] text-muted">no checks</span>;
  return <Badge tone="good">{gate.checks.passed}/{gate.checks.required} ✓</Badge>;
}

export function AuditCard({ agentId }: { agentId: string }) {
  const q = useQuery({ queryKey: ["agent-audit", agentId], queryFn: () => api.agentAudit(agentId) });
  return (
    <Card title="Audit log">
      {!q.data?.length ? <div className="text-sm text-muted">Nothing yet.</div> : (
        <table className="w-full text-sm"><tbody>
          {q.data.map(a => (
            <tr key={a.id} className="border-b border-line last:border-0">
              <td className="whitespace-nowrap py-1.5 text-xs text-muted">{fmtTime(a.ts)}</td>
              <td className="py-1.5 text-xs">{a.actor ?? "—"}</td>
              <td className={cx("py-1.5 text-xs font-medium", a.action === "publish.override" && "text-warn")}>{ACTIONS[a.action] ?? a.action}</td>
              <td className="py-1.5 text-xs" dir="auto">{auditText(a)}</td>
            </tr>))}
        </tbody></table>)}
    </Card>
  );
}
