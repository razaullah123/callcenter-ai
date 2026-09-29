import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Button, Card, Empty, ErrorBox, fmtMs, fmtTime, outcome } from "../ui";

const PAGE = 25;

export const fmtDuration = (s: number | null | undefined) =>
  s == null ? "—" : `${Math.floor(s / 60)}:${String(Math.max(0, s) % 60).padStart(2, "0")}`;

export default function Calls() {
  const [q, setQ] = useState("");
  const [outcomeF, setOutcome] = useState("");
  const [channel, setChannel] = useState("");
  const [page, setPage] = useState(0);
  const calls = useQuery({
    queryKey: ["calls", q, outcomeF, channel, page],
    queryFn: () => api.calls({ q, outcome: outcomeF, channel, limit: PAGE, offset: page * PAGE }),
  });
  const pages = Math.max(1, Math.ceil((calls.data?.total ?? 0) / PAGE));

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold">Call history</h1>
      <div className="flex flex-wrap gap-2">
        <input placeholder="Search mobile or call id…" value={q} onChange={e => { setQ(e.target.value); setPage(0); }} className="w-64" />
        <select value={outcomeF} onChange={e => { setOutcome(e.target.value); setPage(0); }}>
          <option value="">All outcomes</option><option value="booked">Booked</option>
          <option value="handoff">Handed to a human</option><option value="unverified">Not verified</option>
        </select>
        <select value={channel} onChange={e => { setChannel(e.target.value); setPage(0); }}>
          <option value="">All channels</option><option value="ivr">IVR</option><option value="web">Web</option><option value="chat">Text chat</option>
        </select>
      </div>
      <ErrorBox error={calls.error} />
      <Card>
        {calls.data?.items.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th className="py-2 pr-3 font-medium">Call</th><th className="py-2 pr-3 font-medium">Mobile</th>
                  <th className="py-2 pr-3 font-medium">Started</th><th className="py-2 pr-3 font-medium">Ended</th>
                  <th className="py-2 pr-3 font-medium">Duration</th>
                  <th className="py-2 pr-3 font-medium">Channel</th><th className="py-2 pr-3 font-medium">Lang</th>
                  <th className="py-2 pr-3 font-medium">Turns</th><th className="py-2 pr-3 font-medium">Skill</th>
                  <th className="py-2 pr-3 font-medium">Latency p50</th><th className="py-2 pr-3 font-medium">Config</th>
                  <th className="py-2 text-right font-medium">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {calls.data.items.map(c => (
                  <tr key={c.call_id} className="border-b border-line last:border-0 hover:bg-soft/60">
                    <td className="py-2 pr-3"><Link className="font-mono text-xs text-accent" to={`/calls/${c.call_id}`}>{c.call_id}</Link></td>
                    <td className="py-2 pr-3 font-mono text-xs tabular-nums">{c.mobile ?? "—"}</td>
                    <td className="py-2 pr-3 text-xs text-muted">{fmtTime(c.started_at)}</td>
                    <td className="py-2 pr-3 text-xs text-muted">{c.ended_at ? fmtTime(c.ended_at) : <span className="text-good">in progress</span>}</td>
                    <td className="py-2 pr-3 text-xs tabular-nums">{fmtDuration(c.duration_s)}</td>
                    <td className="py-2 pr-3 text-xs">{c.channel}{c.extension_call ? " · ext" : ""}</td>
                    <td className="py-2 pr-3 text-xs">{c.language ?? "—"}</td>
                    <td className="py-2 pr-3 text-xs tabular-nums">{c.turns}</td>
                    <td className="py-2 pr-3 text-xs">{c.last_skill ?? "—"}</td>
                    <td className="py-2 pr-3 text-xs tabular-nums">{fmtMs(c.latency_p50_ms)}</td>
                    <td className="py-2 pr-3 text-xs text-muted">{c.config_version ? `v${c.config_version}` : "—"}</td>
                    <td className="py-2 text-right">{outcome(c)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>{calls.isLoading ? "Loading…" : "No calls match."}</Empty>}
        <div className="mt-3 flex items-center justify-between text-xs text-muted">
          <span>{calls.data?.total ?? 0} calls</span>
          <div className="flex items-center gap-2">
            <Button disabled={page === 0} onClick={() => setPage(p => p - 1)}>Previous</Button>
            <span>{page + 1} / {pages}</span>
            <Button disabled={page + 1 >= pages} onClick={() => setPage(p => p + 1)}>Next</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
