import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useLive } from "../live";
import { Badge, Button, Card, Empty, cx, fmtClock, fmtMs, levelTone } from "../ui";

/** "End call" with an inline confirmation (no browser dialog). */
function EndCall({ callId }: { callId: string }) {
  const [step, setStep] = useState<"idle" | "confirm" | "ending" | "error">("idle");
  const [error, setError] = useState("");
  const end = async () => {
    setStep("ending");
    try { await api.endCall(callId); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); setStep("error"); }
  };
  if (step === "confirm") return (
    <span className="flex items-center gap-2 text-xs">
      <span className="text-muted">End this call?</span>
      <Button kind="danger" onClick={end}>End call</Button>
      <Button kind="ghost" onClick={() => setStep("idle")}>Cancel</Button>
    </span>
  );
  if (step === "ending") return <span className="text-xs text-muted">Ending…</span>;
  return (
    <span className="flex items-center gap-2 text-xs">
      {step === "error" && <span className="text-bad">{error}</span>}
      <Button kind="danger" onClick={() => setStep("confirm")}>End call</Button>
    </span>
  );
}
import { summary } from "./CallDetail";

export default function Live() {
  const [selected, setSelected] = useState<string | undefined>();
  const { active, events, connected } = useLive(selected);
  const current = active.find(c => c.call_id === selected);

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Live calls</h1>
        <Badge tone={connected ? "good" : "bad"}>{connected ? "connected" : "reconnecting…"}</Badge>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={`Active now · ${active.length}`}>
          {active.length ? (
            <ul className="space-y-2">
              {active.map(c => (
                <li key={c.call_id}>
                  <button onClick={() => setSelected(c.call_id === selected ? undefined : c.call_id)}
                    className={cx("w-full rounded-lg border px-3 py-2 text-left", c.call_id === selected ? "border-accent bg-accent/5" : "border-line hover:bg-soft")}>
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs">{c.call_id}</span>
                      <span className="text-xs tabular-nums text-muted">{Math.floor(c.duration_s / 60)}:{String(c.duration_s % 60).padStart(2, "0")}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {c.verified && <Badge tone="info">verified</Badge>}
                      {c.skill && <Badge>{c.skill}</Badge>}
                      {c.language && <Badge>{c.language}</Badge>}
                      <Badge>{c.turns} turns</Badge>
                    </div>
                    {c.last_agent && <div className="mt-1 truncate text-xs text-muted" dir="auto">🤖 {c.last_agent}</div>}
                  </button>
                  <div className="mt-1 flex justify-end"><EndCall callId={c.call_id} /></div>
                </li>
              ))}
            </ul>
          ) : <Empty>No calls right now. Start one from the Playground.</Empty>}
        </Card>

        <Card className="lg:col-span-2" title={selected ? <span>Events · <span className="font-mono">{selected}</span></span> : "All events"}
          actions={selected && (
            <span className="flex items-center gap-3">
              {current && <EndCall callId={selected} />}
              <Link to={`/calls/${selected}`} className="text-xs text-accent">Open call →</Link>
            </span>
          )}>
          {current && (
            <div className="mb-3 grid gap-2 rounded-lg bg-soft/60 p-3 text-xs sm:grid-cols-2">
              <div><span className="text-muted">Step </span>{current.step ?? "—"}</div>
              <div><span className="text-muted">Choices </span>{JSON.stringify(current.slots)}</div>
              {current.last_user && <div dir="auto" className="sm:col-span-2">👤 {current.last_user}</div>}
              {current.last_agent && <div dir="auto" className="sm:col-span-2">🤖 {current.last_agent}</div>}
            </div>
          )}
          <div className="max-h-[34rem] overflow-y-auto font-mono text-xs">
            {events.map(e => (
              <div key={e.id} className="grid grid-cols-[5.5rem_8rem_9rem_4.5rem_1fr] gap-2 border-b border-line/60 py-1">
                <span className="text-muted">{fmtClock(e.ts)}</span>
                <span className="truncate text-muted">{e.call_id}</span>
                <span><Badge tone={levelTone(e.level)}>{e.type}</Badge></span>
                <span className="tabular-nums text-muted">{e.latency_ms != null ? fmtMs(e.latency_ms) : ""}</span>
                <span className="truncate" dir="auto">{summary(e)}</span>
              </div>
            ))}
            {!events.length && <Empty>Waiting for events…</Empty>}
          </div>
        </Card>
      </div>
    </div>
  );
}
