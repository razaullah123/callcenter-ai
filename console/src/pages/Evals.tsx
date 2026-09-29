import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { evalsApi, type EvalResult } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, cx, fmtMs, fmtTime } from "../ui";

export default function Evals() {
  const qc = useQueryClient();
  const cases = useQuery({ queryKey: ["eval-cases"], queryFn: evalsApi.cases });
  const runs = useQuery({ queryKey: ["eval-runs"], queryFn: evalsApi.runs });
  const [suite, setSuite] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [judge, setJudge] = useState(false);
  const [mode, setMode] = useState("auto");
  const [job, setJob] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);

  const jobQ = useQuery({ queryKey: ["eval-job", job], queryFn: () => evalsApi.job(job!), enabled: !!job,
    refetchInterval: d => (d.state.data?.status === "running" ? 2000 : false) });
  useEffect(() => {
    if (jobQ.data && jobQ.data.status !== "running") {
      qc.invalidateQueries({ queryKey: ["eval-runs"] });
      if (jobQ.data.run_id) setOpenRun(jobQ.data.run_id);
    }
  }, [jobQ.data, qc]);

  const suites = useMemo(() => [...new Set(cases.data?.map(c => c.suite))], [cases.data]);
  const visible = cases.data?.filter(c => !suite || c.suite === suite) ?? [];
  const running = jobQ.data?.status === "running";

  const start = async () => {
    setError(null);
    try {
      const body = picked.size ? { cases: [...picked], mode, judge } : { suite: suite || undefined, mode, judge };
      setJob((await evalsApi.run(body)).job);
    } catch (e) { setError(e); }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Evals</h1>
        <p className="text-sm text-muted">Simulated callers (Najdi / English, male / female) run full conversations against the active provider
          config and skills, with a fixture hospital system — no patient data, no real bookings. Checks cover verification, tool order,
          booking outcome, language, gender agreement, one question per reply and no medical advice.</p>
      </div>
      <ErrorBox error={error} />
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card title={`Cases · ${visible.length}`} actions={
          <select value={suite} onChange={e => { setSuite(e.target.value); setPicked(new Set()); }} className="!py-1 text-xs">
            <option value="">All suites</option>{suites.map(s => <option key={s}>{s}</option>)}
          </select>}>
          <ul className="max-h-80 space-y-1 overflow-y-auto">
            {visible.map(c => (
              <li key={c.id}>
                <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-soft">
                  <input type="checkbox" checked={picked.has(c.id)} onChange={e => {
                    const n = new Set(picked); e.target.checked ? n.add(c.id) : n.delete(c.id); setPicked(n);
                  }} />
                  <span className="font-mono text-xs">{c.id}</span>
                  <Badge>{c.suite}</Badge><Badge>{c.language}</Badge>{c.mode === "script" && <Badge tone="info">script</Badge>}
                  <span className="truncate text-xs text-muted">{c.title}</span>
                </label>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Run">
          <div className="space-y-3 text-sm">
            <div>{picked.size ? `${picked.size} selected cases` : suite ? `Suite: ${suite}` : "All cases"}</div>
            <label className="block"><span className="text-xs text-muted">Caller</span>
              <select className="mt-1 w-full" value={mode} onChange={e => setMode(e.target.value)}>
                <option value="auto">auto — script if the case has one, else LLM caller</option>
                <option value="llm">LLM caller for every case</option>
              </select>
            </label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={judge} onChange={e => setJudge(e.target.checked)} />
              LLM judge (naturalness, politeness, brevity, task)</label>
            <Button kind="primary" onClick={start} disabled={running}>{running ? "Running…" : "Run evals"}</Button>
            {jobQ.data && (
              <div className="rounded-lg bg-soft/60 p-2 text-xs">
                <div>{jobQ.data.status} · {jobQ.data.done.length}/{jobQ.data.total}</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {jobQ.data.done.map(d => <Badge key={d.case} tone={d.passed ? "good" : "bad"}>{d.case}</Badge>)}
                </div>
                {jobQ.data.error && <div className="mt-1 text-bad">{jobQ.data.error}</div>}
              </div>
            )}
          </div>
        </Card>
      </div>

      <Card title="Runs">
        {runs.data?.length ? (
          <table className="w-full text-sm">
            <tbody>
              {runs.data.map(r => (
                <tr key={r.id} onClick={() => setOpenRun(r.id)}
                  className={cx("cursor-pointer border-b border-line last:border-0 hover:bg-soft/60", openRun === r.id && "bg-soft")}>
                  <td className="py-2 font-mono text-xs">{r.id}</td>
                  <td className="py-2 text-xs text-muted">{fmtTime(r.started_at)}</td>
                  <td className="py-2 text-xs">{r.model}</td>
                  <td className="py-2"><Badge tone={r.summary.passed === r.summary.cases ? "good" : "warn"}>{r.summary.passed}/{r.summary.cases} passed</Badge></td>
                  <td className="py-2 text-xs text-muted">LLM first token {fmtMs(r.summary.llm_first_token_p50)}</td>
                  <td className="py-2 text-xs text-muted">{r.summary.judge ? `judge task ${r.summary.judge.task ?? "—"}/5` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>No runs yet.</Empty>}
      </Card>
      {openRun && <RunDetail id={openRun} />}
    </div>
  );
}

function RunDetail({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["eval-run", id], queryFn: () => evalsApi.runDetail(id) });
  const [open, setOpen] = useState<string | null>(null);
  if (!q.data) return <ErrorBox error={q.error} />;
  const s = q.data.summary;
  return (
    <Card title={<span>Run <span className="font-mono">{id}</span></span>}>
      <div className="mb-4 flex flex-wrap gap-2">
        {Object.entries(s.checks).map(([k, v]) => (
          <Badge key={k} tone={v.passed === v.total ? "good" : "bad"}>{k} {v.passed}/{v.total}</Badge>
        ))}
        {s.judge && Object.entries(s.judge).map(([k, v]) => <Badge key={k} tone="info">{k} {v ?? "—"}/5</Badge>)}
      </div>
      <div className="space-y-2">
        {q.data.results.map(r => <ResultRow key={r.case} r={r} open={open === r.case} toggle={() => setOpen(open === r.case ? null : r.case)} />)}
      </div>
    </Card>
  );
}

function ResultRow({ r, open, toggle }: { r: EvalResult; open: boolean; toggle: () => void }) {
  const failed = r.checks.filter(c => !c.passed);
  return (
    <div className="rounded-lg border border-line">
      <button onClick={toggle} className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left">
        <Badge tone={r.passed ? "good" : "bad"}>{r.passed ? "pass" : "fail"}</Badge>
        <span className="font-mono text-xs">{r.case}</span>
        <span className="text-xs text-muted">{r.turns} turns · {r.duration_s}s · {r.mode}</span>
        {failed.length > 0 && <span className="truncate text-xs text-bad">{failed.map(c => `${c.check}${c.detail ? ` (${c.detail})` : ""}`).join("; ")}</span>}
        {r.judge?.task != null && <span className="ml-auto text-xs text-muted">judge {r.judge.naturalness}/{r.judge.politeness}/{r.judge.brevity}/{r.judge.task}</span>}
      </button>
      {open && (
        <div className="grid gap-3 border-t border-line p-3 md:grid-cols-2">
          <div className="flex max-h-96 flex-col gap-1.5 overflow-y-auto">
            {r.transcript.map((t, i) => (
              <div key={i} dir="auto" className={cx("max-w-[90%] rounded-lg px-2.5 py-1.5 text-xs", t.role === "caller" ? "self-end bg-accent/10" : "self-start bg-soft")}>{t.text}</div>
            ))}
          </div>
          <div className="space-y-2 text-xs">
            <div>{r.checks.map(c => <div key={c.check} className={c.passed ? "text-good" : "text-bad"}>{c.passed ? "✓" : "✗"} {c.check} <span className="text-muted">{c.detail}</span></div>)}</div>
            {r.judge?.issues && <div className="text-muted">Judge: {r.judge.issues}</div>}
            <div className="font-mono text-[11px] text-muted">{r.tool_calls.map(([t]) => t).join(" → ")}</div>
          </div>
        </div>
      )}
    </div>
  );
}
