import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

/** The current project's agents (Hamsa's "All agents" filter). `allLabel` null: an agent must be picked — empty
 * means "the project's main agent" (the server picks it). */
export default function AgentPicker({ value, onChange, allLabel = "All agents", id = "agent-filter" }: {
  value: string; onChange: (agent: string) => void; allLabel?: string | null; id?: string;
}) {
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.agents, staleTime: 30_000 });
  return (
    <select id={id} aria-label="Agent" value={value} onChange={e => onChange(e.target.value)} className="!py-1.5 text-sm">
      <option value="">{allLabel ?? "Main agent"}</option>
      {(agents.data ?? []).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
    </select>
  );
}
