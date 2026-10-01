// Typed client for the control-plane API (/api on the voice server).

export type CallRow = {
  call_id: string; channel: string | null; started_at: string; ended_at: string | null; language: string | null;
  verified: boolean; booked: boolean; handoff: string | null; end_reason: string | null; turns: number;
  last_skill: string | null; config_version: number | null; latency_p50_ms: number | null; extension_call: boolean;
  mobile: string | null; duration_s: number | null;
};

export type EventRow = {
  id: number; event_id: string; call_id: string | null; ts: string; type: string; level: string;
  turn_id: number | null; skill: string | null; step: string | null; latency_ms: number | null;
  data: Record<string, unknown>;
};

export type TurnLatency = {
  turn: number; text: string | null; stt_ms: number | null; llm_first_token_ms: number | null;
  tools: { tool: string; ms: number | null; ok: boolean; cached?: boolean }[];
  first_audio_ms: number | null; total_ms: number | null;
};

export type CallDetail = {
  call: CallRow; events: EventRow[]; turns: TurnLatency[];
  transcript: { role: "user" | "agent" | "system"; text: string; ts: string; turn: number | null }[];
};

export type Stats = {
  window_hours: number; active_calls: number;
  totals: { calls: number; verified: number; booked: number; handoffs: number; active: number; avg_turns: number | null };
  latency: { p50: number | null; p95: number | null; samples: number };
  stages: Record<string, number>;
  per_hour: { hour: string; calls: number; booked: number; handoffs: number }[];
  skills: { skill: string; calls: number }[];
};

export type ActiveCall = {
  call_id: string; started: number; duration_s: number; skill: string | null; step: string | null; turns: number;
  language: string | null; verified: boolean; slots: Record<string, unknown>; last_user: string | null;
  last_agent: string | null;
};

export type ProviderSpec = { provider: string; settings: Record<string, unknown> };
export type AgentConfig = Record<string, ProviderSpec | Record<string, unknown>>;
export type JsonSchema = {
  type?: string | string[]; properties?: Record<string, JsonSchema>; required?: string[]; description?: string;
  default?: unknown; enum?: unknown[]; anyOf?: JsonSchema[]; format?: string; title?: string; minimum?: number;
  maximum?: number; writeOnly?: boolean;
};
export type ProvidersResponse = {
  available: Record<string, string[]>; schemas: Record<string, Record<string, JsonSchema>>; active_version: number;
  agent?: { id: string; name: string; release_id: number | null; version: number | null };
  config: AgentConfig; runtime_knobs: Record<string, string>;
  /** What each running provider actually uses (stored overrides + .env defaults); secrets only as "set". */
  effective?: Record<string, Record<string, unknown>>;
  history: { version: number; created_at: string; author: string; note: string; active: boolean }[];
};

export type SkillSummary = {
  name: string; description: string; status: string; routable: boolean; has_flow: boolean; steps: string[];
  tools: string[];
};
export type SkillDetail = {
  name: string; files: Record<string, string>;
  versions: { version: number; created_at: string; author: string; note: string }[];
};
export type ToolRow = {
  name: string; skill: string; kind: string; source: string; confirm: string; cache_ttl: number; description: string;
};

export type SecretRef = { secret: string };
export const isSecretRef = (v: unknown): v is SecretRef =>
  !!v && typeof v === "object" && !Array.isArray(v) && Object.keys(v as object).length === 1 &&
  typeof (v as SecretRef).secret === "string";
export type Connection = {
  id: string; kind: string; type: string; name: string; settings: Record<string, unknown>;
  updated_at: string | null; updated_by: string | null; used_by: string[];
};
export type ConnectionsResponse = {
  connections: Connection[]; available: Record<string, string[]>;
  schemas: Record<string, Record<string, JsonSchema>>; connection_fields: string[];
};
export type SecretRow = { name: string; hint: string; updated_by: string | null; updated_at: string; used_by: string[] };
export type SecretsResponse = {
  encryption: boolean; secrets: SecretRow[]; missing: { name: string; used_by: string[] }[];
};

export type ToolPolicy = {
  kind: "read" | "write" | "send"; confirm?: string; timeout_s?: number; cache_ttl?: number; idempotent?: boolean;
  role?: string; args?: Record<string, string>; hooks?: string[]; backs?: string; success_line?: string;
  source?: "mcp" | "local" | "http"; description?: string; input_schema?: Record<string, unknown>;
  http?: { method?: string; url?: string; headers?: Record<string, unknown> };
};
export type LibraryTool = {
  name: string; group: string; source: string; policy: ToolPolicy; description: string;
  input_schema: Record<string, unknown> | null; server: string | null; available: boolean; used_by: string[];
};
export type McpServer = {
  id: string; name: string; url: string; transport: string; auth: Record<string, string>; enabled: boolean;
  status: { connected: boolean; tools: string[]; error: string | null };
};
export type ToolLibrary = {
  servers: McpServer[]; tools: LibraryTool[];
  discovered: { name: string; server: string | null; description: string; input_schema: Record<string, unknown> | null }[];
  choices: { kinds: string[]; confirm: string[]; roles: string[]; claims: string[]; sources: string[]; methods: string[];
             phrases: string[]; hooks: Record<string, string[]>; groups: string[] };
  agents: { id: string; name: string }[];
};

export type AgentSummary = {
  id: string; name: string; description: string; version: number | null; published_at: string | null;
  has_draft: boolean; draft_updated_at: string | null; routes: string[]; default: boolean; skills: string[];
};
export type FlowNode = {
  id: string; type: string; instructions?: string; tools?: string[]; auto_call?: Record<string, unknown>[];
  extract?: string[]; set?: Record<string, unknown>; tool?: string; args?: Record<string, unknown>; reason?: string;
  say?: Record<string, string>; skill?: string; position?: { x: number; y: number };
};
export type FlowEdge = { from: string; to: string; when?: Record<string, unknown>; on?: string };
export type FlowGraph = {
  start: string; nodes: FlowNode[]; edges: FlowEdge[]; common_tools?: string[];
  infer?: Record<string, unknown>; reset?: Record<string, unknown>;
  variables?: Record<string, { type?: string; enum?: string[]; description?: string }>;
};
export type Bundle = {
  schema: number; agent: { name: string; languages: string[]; default_language: string };
  models: Record<string, { provider?: string; type?: string; settings?: Record<string, unknown> }>;
  knobs: Record<string, unknown>; voice: { stt_hint?: Record<string, string> };
  phrases: Record<string, unknown>; skills: Record<string, number | { skill: string; version: number }>;
  tools: Record<string, unknown>;
};
export type AgentDetail = {
  agent: { id: string; name: string; description: string; published_release_id: number | null;
           draft_updated_at: string | null; draft_updated_by: string | null };
  has_draft: boolean; bundle: Bundle; tools: string[];
  skills: Record<string, { library: string; version: number; files: Record<string, string>;
                           flow: { converted?: boolean; graph?: FlowGraph; errors?: string[]; error?: string } | null;
                           versions: { version: number; created_at: string; author: string; note: string }[] }>;
  releases: { id: number; version: number; author: string; note: string; created_at: string }[];
  routes: { pattern: string; agent_id: string; priority: number }[];
  connections: { id: string; kind: string; type: string; name: string }[];
  schemas: Record<string, Record<string, JsonSchema>>;
  choices: { knobs: Record<string, string>; phrases: string[]; library_skills: string[] };
};

const TOKEN_KEY = "hmg-console-token";
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; } };
export const setToken = (t: string) => { try { localStorage.setItem(TOKEN_KEY, t); } catch { /* private mode */ } };

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}),
               ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail; } catch { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const qs = (p: Record<string, unknown>) => {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach(x => u.append(k, String(x))); else u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
};

export const api = {
  stats: (hours = 24) => req<Stats>(`/api/stats${qs({ hours })}`),
  calls: (p: { limit?: number; offset?: number; q?: string; outcome?: string; channel?: string }) =>
    req<{ total: number; items: CallRow[] }>(`/api/calls${qs(p)}`),
  call: (id: string) => req<CallDetail>(`/api/calls/${encodeURIComponent(id)}`),
  events: (p: { call_id?: string; type?: string[]; level?: string; text?: string; before_id?: number; limit?: number }) =>
    req<EventRow[]>(`/api/events${qs(p)}`),
  liveCalls: () => req<ActiveCall[]>("/api/live/calls"),
  endCall: (id: string) => req<{ call_id: string; ended: boolean }>(`/api/calls/${encodeURIComponent(id)}/end`,
    { method: "POST" }),
  providers: () => req<ProvidersResponse>("/api/providers"),
  saveProviders: (config: AgentConfig, note: string) =>
    req<{ version: number }>("/api/providers", { method: "PUT", body: JSON.stringify({ config, note }) }),
  activate: (version: number) => req<{ version: number }>(`/api/providers/activate/${version}`, { method: "POST" }),
  testProvider: (kind: string, provider: string, settings: Record<string, unknown>) =>
    req<Record<string, unknown>>("/api/providers/test", { method: "POST", body: JSON.stringify({ kind, provider, settings }) }),
  skills: () => req<SkillSummary[]>("/api/skills"),
  skill: (name: string) => req<SkillDetail>(`/api/skills/${name}`),
  validateSkill: (name: string, files: Record<string, string>) =>
    req<{ errors: string[] }>(`/api/skills/${name}/validate`, { method: "POST", body: JSON.stringify({ files }) }),
  saveSkill: (name: string, files: Record<string, string>, note: string) =>
    req<{ version: number }>(`/api/skills/${name}`, { method: "PUT", body: JSON.stringify({ files, note }) }),
  skillVersion: (name: string, v: number) =>
    req<{ files: Record<string, string> }>(`/api/skills/${name}/versions/${v}`),
  tools: () => req<ToolRow[]>("/api/tools"),
  connections: () => req<ConnectionsResponse>("/api/connections"),
  createConnection: (body: { kind: string; type: string; name: string; settings: Record<string, unknown> }) =>
    req<{ id: string }>("/api/connections", { method: "POST", body: JSON.stringify(body) }),
  updateConnection: (id: string, body: { name?: string; settings: Record<string, unknown> }) =>
    req<{ id: string; used_by: string[] }>(`/api/connections/${encodeURIComponent(id)}`,
      { method: "PUT", body: JSON.stringify(body) }),
  deleteConnection: (id: string) =>
    req<{ deleted: string }>(`/api/connections/${encodeURIComponent(id)}`, { method: "DELETE" }),
  testConnection: (id: string) =>
    req<Record<string, unknown>>(`/api/connections/${encodeURIComponent(id)}/test`, { method: "POST" }),
  agents: () => req<AgentSummary[]>("/api/agents"),
  agent: (id: string) => req<AgentDetail>(`/api/agents/${encodeURIComponent(id)}`),
  createAgent: (body: { name: string; description?: string; copy_from?: string }) =>
    req<{ id: string }>("/api/agents", { method: "POST", body: JSON.stringify(body) }),
  renameAgent: (id: string, body: { name: string; description: string }) =>
    req<{ id: string }>(`/api/agents/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteAgent: (id: string) => req<{ deleted: string }>(`/api/agents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  putDraft: (id: string, bundle: Bundle) =>
    req<{ ok: boolean }>(`/api/agents/${encodeURIComponent(id)}/draft`, { method: "PUT", body: JSON.stringify({ bundle }) }),
  putDraftSkill: (id: string, key: string, body: { graph?: FlowGraph; files?: Record<string, string>; note?: string }) =>
    req<{ skill: string; version: number }>(`/api/agents/${encodeURIComponent(id)}/draft/skills/${encodeURIComponent(key)}`,
      { method: "PUT", body: JSON.stringify(body) }),
  discardDraft: (id: string) => req<{ ok: boolean }>(`/api/agents/${encodeURIComponent(id)}/draft/discard`, { method: "POST" }),
  publishAgent: (id: string, note: string) =>
    req<{ id: number; version: number }>(`/api/agents/${encodeURIComponent(id)}/publish`, { method: "POST", body: JSON.stringify({ note }) }),
  activateRelease: (id: string, release: number) =>
    req<{ version: number }>(`/api/agents/${encodeURIComponent(id)}/activate/${release}`, { method: "POST" }),
  exportAgent: (id: string) => req<Record<string, unknown>>(`/api/agents/${encodeURIComponent(id)}/export`),
  release: (id: string, release: number) =>
    req<{ bundle: Bundle; version: number }>(`/api/agents/${encodeURIComponent(id)}/releases/${release}`),
  routes: () => req<{ pattern: string; agent_id: string; priority: number }[]>("/api/routes"),
  putRoute: (body: { pattern: string; agent_id: string; priority?: number }) =>
    req<{ ok: boolean }>("/api/routes", { method: "PUT", body: JSON.stringify(body) }),
  deleteRoute: (pattern: string) => req<{ ok: boolean }>(`/api/routes?pattern=${encodeURIComponent(pattern)}`, { method: "DELETE" }),
  toolLibrary: () => req<ToolLibrary>("/api/tool-library"),
  putTool: (name: string, body: { group: string; policy: ToolPolicy; agents?: string[]; note?: string }) =>
    req<{ name: string; releases: unknown[] }>(`/api/tool-library/${encodeURIComponent(name)}`,
      { method: "PUT", body: JSON.stringify(body) }),
  deleteTool: (name: string, agent?: string) =>
    req<Record<string, unknown>>(`/api/tool-library/${encodeURIComponent(name)}${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`,
      { method: "DELETE" }),
  testTool: (name: string, args: Record<string, unknown>) =>
    req<{ ok: boolean; ms: number; data?: unknown; error?: string }>(`/api/tool-library/${encodeURIComponent(name)}/test`,
      { method: "POST", body: JSON.stringify({ args }) }),
  discoverServer: (body: Record<string, unknown>) =>
    req<{ ok: boolean; tools?: { name: string; description: string }[]; error?: string }>("/api/mcp-servers/discover",
      { method: "POST", body: JSON.stringify(body) }),
  addServer: (body: Record<string, unknown>) =>
    req<{ id: string }>("/api/mcp-servers", { method: "POST", body: JSON.stringify(body) }),
  updateServer: (id: string, body: Record<string, unknown>) =>
    req<{ id: string }>(`/api/mcp-servers/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteServer: (id: string) =>
    req<{ deleted: string }>(`/api/mcp-servers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  secrets: () => req<SecretsResponse>("/api/secrets"),
  putSecret: (name: string, value: string) =>
    req<{ name: string; used_by: string[]; note: string | null }>(`/api/secrets/${encodeURIComponent(name)}`,
      { method: "PUT", body: JSON.stringify({ value }) }),
  deleteSecret: (name: string) =>
    req<{ deleted: string }>(`/api/secrets/${encodeURIComponent(name)}`, { method: "DELETE" }),
};

export function wsUrl(path: string, params: Record<string, string | undefined> = {}) {
  const token = getToken();
  const p = qs({ ...params, token: token || undefined });
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}${p}`;
}

// ---------------------------------------------------------------- evals
export type EvalCase = { id: string; suite: string; title: string; language: string; mode: string };
export type EvalCheck = { check: string; passed: boolean; detail: string };
export type EvalResult = {
  case: string; suite: string; title: string; mode: string; passed: boolean; turns: number; duration_s: number;
  error: string | null; checks: EvalCheck[]; transcript: { role: string; text: string }[];
  tool_calls: [string, Record<string, unknown>][]; latency: { llm_first_token_p50: number | null };
  judge?: { naturalness?: number; politeness?: number; brevity?: number; task?: number; issues?: string; error?: string };
};
export type EvalSummary = {
  cases: number; passed: number; checks: Record<string, { passed: number; total: number }>;
  judge: Record<string, number | null> | null; llm_first_token_p50: number | null;
};
export type EvalRun = { id: string; started_at: string; finished_at: string; model: string; mode: string; summary: EvalSummary };

export const evalsApi = {
  cases: () => req<EvalCase[]>("/api/evals/cases"),
  run: (body: { suite?: string; cases?: string[]; mode: string; judge: boolean }) =>
    req<{ job: string; total: number }>("/api/evals/run", { method: "POST", body: JSON.stringify(body) }),
  job: (job: string) => req<{ status: string; total: number; done: { case: string; passed: boolean }[]; run_id: string | null; error: string | null }>(`/api/evals/jobs/${job}`),
  runs: () => req<EvalRun[]>("/api/evals/runs"),
  runDetail: (id: string) => req<EvalRun & { results: EvalResult[] }>(`/api/evals/runs/${id}`),
};
