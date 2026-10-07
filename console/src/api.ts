// Typed client for the control-plane API (/api on the voice server).

export type CallRow = {
  call_id: string; channel: string | null; started_at: string; ended_at: string | null; language: string | null;
  verified: boolean; booked: boolean; handoff: string | null; end_reason: string | null; turns: number;
  last_skill: string | null; config_version: number | null; latency_p50_ms: number | null; extension_call: boolean;
  mobile: string | null; duration_s: number | null;
  agent_id?: string | null; release_id?: number | null;
  /** in_progress | completed | failed | forwarded | terminated */
  status?: CallStatus;
};
export type CallStatus = "in_progress" | "completed" | "failed" | "forwarded" | "terminated";
export type CallAgent = {
  id: string; name: string; version: number | null;
  llm: { provider: string | null; model: string | null }; stt: { provider: string | null; model: string | null };
  tts: { provider: string | null; voice: string | null; model: string | null };
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

/** What the model read from a finished call. csat (1-5) and nps (0-10) are estimates, not survey answers. */
export type CallAnalysis = {
  status: "ok" | "skipped" | "failed"; error: string | null; summary: string | null; sentiment: "positive" | "neutral" | "negative" | null;
  csat: number | null; nps: number | null; resolved: boolean | null; outcome: Record<string, unknown>; model: string | null; created_at: string;
};
export type AnalysisField = { name: string; type: "string" | "number" | "boolean" | "enum" | "array" | "object"; description?: string; options?: string[] };
/** Was post-call analysis on for the version a call ran on, and in the agent's draft? */
export type AnalysisSetup = { agent_id: string; release_version: number | null; in_release: boolean; in_draft: boolean };
export type Satisfaction = {
  analyzed: number; skipped: number; failed: number;
  csat: { score: number | null; average: number | null; calls: number }; nps: { score: number | null; calls: number };
  sentiment: { positive: number | null; neutral: number | null; negative: number | null; calls: number };
  resolved: { score: number | null; calls: number };
};
/** What the console knows about a call's recording (null: the call was not recorded). */
export type CallRecording = { status: "ok" | "failed" | "expired" | "deleted"; duration_s: number | null; size_bytes: number | null; expires_at: string | null; error: string | null };
export type CallDetail = {
  recording?: CallRecording | null;
  call: CallRow; events: EventRow[]; turns: TurnLatency[]; agent?: CallAgent | null; analysis?: CallAnalysis | null; analysis_setup?: AnalysisSetup | null;
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

export type Count = { key: string; calls: number };
export type DashboardData = {
  start: string; end: string; granularity: "hour" | "day"; live_calls: number;
  totals: {
    calls: number; ended: number; total_duration_s: number; avg_duration_s: number | null;
    lt30: number; s30_120: number; gt120: number; verified: number; booked: number; handoffs: number;
    verified_only: number; unresolved: number; avg_turns: number | null; errors: number; avg_words_per_reply: number | null;
  };
  performance: {
    stt_ms: number | null; llm_ms: number | null; tool_ms: number | null; tts_ms: number | null;
    latency_ms: number | null; latency_p50_ms: number | null; latency_p95_ms: number | null;
  };
  /** t: local wall time of the bucket start ("2026-10-04 09:00:00") */
  series: { t: string; calls: number; booked: number; handoffs: number }[];
  channels: Count[]; languages: Count[]; skills: Count[]; handoff_reasons: Count[];
};

export type ActiveCall = {
  call_id: string; started: number; duration_s: number; skill: string | null; step: string | null; turns: number;
  language: string | null; verified: boolean; slots: Record<string, unknown>; last_user: string | null;
  last_agent: string | null; agent_id: string | null;
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
  source?: "mcp" | "local" | "http" | "web"; description?: string; input_schema?: Record<string, unknown>;
  /** false = inactive: not offered to the agent, refuses to run */ enabled?: boolean;
  /** write / send only: answers {queued: true} at once, the call finishes in the background */ async?: boolean;
  say_start?: Record<string, string>; say_done?: Record<string, string>;
  http?: { method?: string; url?: string; headers?: Record<string, unknown>; auth?: ToolAuth };
};
/** An API tool's authentication; secret parts are {"secret": NAME} references. */
export type ToolAuth =
  | { type: "none" }
  | { type: "bearer"; token: unknown }
  | { type: "token"; token: unknown }
  | { type: "basic"; username: unknown; password: unknown }
  | { type: "api_key"; header: string; value: unknown };
export type ToolTestOverride = { url?: string; method?: string; timeout_s?: number; headers?: Record<string, unknown> };
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
  /** flow: a flow graph drives the call · prompt: the model follows a prompt */
  type?: "flow" | "prompt"; languages?: string[]; default_language?: string; voice?: string | null;
  voice_provider?: string | null; created_at?: string | null; updated_at?: string | null;
};
export type ImportResult = {
  id: string; release_id: number; version: number; name: string; tools: string[]; report: string[];
  stats: { nodes: number; edges: number; tools: number; variables: number };
};
export type FlowNode = {
  id: string; type: string; instructions?: string; tools?: string[]; auto_call?: Record<string, unknown>[];
  extract?: string[]; set?: Record<string, unknown>; tool?: string; args?: Record<string, unknown>; reason?: string;
  say?: Record<string, string>; skill?: string; position?: { x: number; y: number };
  /** tool nodes: slot ← path in the tool's result ("result.count") */
  outputs?: Record<string, string>;
  /** conversation: keypad digits → a variable */
  dtmf_capture?: { variable?: string; max_digits?: number; end_keys?: string[]; timeout_s?: number };
  /** static message: move on without waiting for the caller */
  skip_response?: boolean;
  /** transfer: own number / extension, warm (announce) or cold, ring timeout, SIP headers */
  destination?: string; transfer_type?: string; timeout_s?: number; headers?: Record<string, string>;
  /** tool: continue / retry / fail, extra attempts, said while it runs */
  on_error?: string; retries?: number; processing?: Record<string, string>;
  /** settings node: what changes from here on · conversation: model / temperature for this step */
  overrides?: Record<string, unknown>; llm?: { model?: string; temperature?: number };
  /** agent node: the agent that takes the call, and what it inherits */
  agent?: string; handoff_history?: boolean; handoff_variables?: boolean;
};
export type FlowEdge = {
  from: string; to: string; when?: Record<string, unknown>; on?: string;
  /** global edge: return to where the caller was afterwards · ask yes / no first (true or {lang: question}) · arrive silently */
  back?: boolean; confirm?: boolean | Record<string, string>; silent?: boolean;
};
export type FlowGraph = {
  start: string; nodes: FlowNode[]; edges: FlowEdge[]; common_tools?: string[];
  infer?: Record<string, unknown>; reset?: Record<string, unknown>;
  variables?: Record<string, { type?: string; enum?: string[]; description?: string }>;
};
export type ShareSettings = {
  description: string; tagline: string; show_name: boolean; show_transcript: boolean; theme: "light" | "dark"; theme_switcher: boolean;
  visualizer: "orb" | "wave" | "aura"; gradient: string[]; bg_dark: string; bg_light: string;
  embed: { position: "bottom-left" | "bottom-center" | "bottom-right"; size: "sm" | "md" | "lg"; label: string; auto_start: boolean;
    launcher: "auto" | "always" | "never"; color: string };
  limits: { max_minutes: number; max_concurrent: number; per_ip_per_hour: number; allowed_origins: string[]; allowed_params: string[] };
};
export type ShareInfo = { published: boolean; token: string | null; path: string | null; settings: ShareSettings; defaults: ShareSettings };
export type Bundle = {
  schema: number; agent: { name: string; languages: string[]; default_language: string };
  models: Record<string, { provider?: string; type?: string; settings?: Record<string, unknown> }>;
  knobs: Record<string, unknown>; voice: { stt_hint?: Record<string, string> };
  phrases: Record<string, unknown>; skills: Record<string, number | { skill: string; version: number }>;
  tools: Record<string, unknown>;
  /** knowledge base items this agent searches (search_knowledge_base) */
  knowledge?: { items: string[] };
  /** custom variables: available everywhere as {{ name }}; a call can pass new values as `params` */
  variables?: Record<string, { type?: string; default?: unknown; description?: string }>;
  /** call webhook: call.start / call.end JSON posted to `url` (auth = a secret sent as a Bearer token) */
  webhook?: { url?: string; auth?: SecretRef | null; signing?: SecretRef | null; events?: string[]; include_transcript?: boolean };
  /** post-call analysis: summary, sentiment, satisfaction estimates and the agent's own outcome fields */
  analysis?: { enabled?: boolean; summary?: boolean; sentiment?: boolean; satisfaction?: boolean; fields?: AnalysisField[] };
};

export type KbStatus = "processing" | "completed" | "completed_with_errors" | "failed";
export type KbItem = {
  id: string; name: string; type: "text" | "file" | "url"; source_url?: string | null; extension: string | null; size_bytes: number; words: number;
  chunks: number; status: KbStatus; error: string | null; created_by: string | null; created_at: string;
  updated_at: string; used_by: { agent: string; name: string }[]; draft_by: { agent: string; name: string }[];
  content?: string;
};
export type KbList = {
  items: KbItem[]; usage_bytes: number; quota_bytes: number;
  limits: { file_bytes: number; text_chars: number; extensions: string[]; urls: number };
};
export type AgentDetail = {
  agent: { id: string; name: string; description: string; published_release_id: number | null;
           draft_updated_at: string | null; draft_updated_by: string | null };
  has_draft: boolean; bundle: Bundle; tools: string[];
  skills: Record<string, { library: string; version: number; files: Record<string, string>;
                           flow: { converted?: boolean; graph?: FlowGraph; errors?: string[]; error?: string } | null;
                           versions: { version: number; created_at: string; author: string; note: string }[] }>;
  releases: { id: number; version: number; author: string; note: string; created_at: string; gate?: ReleaseGate | null }[];
  routes: { pattern: string; agent_id: string; priority: number }[];
  connections: { id: string; kind: string; type: string; name: string }[];
  schemas: Record<string, Record<string, JsonSchema>>;
  choices: { knobs: Record<string, string>; phrases: string[]; library_skills: string[] };
};

// ---- test cases + publish gate (12.7)
export type CaseSpec = {
  suite?: string; title?: string; max_turns?: number; tags?: string[]; judge_note?: string;
  caller: { language?: "ar" | "en"; script?: string[]; goal?: string; persona?: string; opening?: string;
            facts?: Record<string, string> };
  expect: Record<string, unknown>;
};
export type AgentCase = { id: string; spec: CaseSpec; gate: boolean; updated_by?: string | null; updated_at?: string };
export type CaseResult = { passed: boolean; failed: string[]; run_id: string; at: string; turns?: number; duration_s?: number };
export type GateStatus = { draft_hash: string | null; required: number; passed: string[]; failed: string[]; not_run: string[];
                           ok: boolean; results: Record<string, CaseResult> };
export type EvalJob = { id: string; status?: "running" | "done" | "error"; total?: number; error?: string | null;
                        run_id?: string | null; done?: { case: string; passed: boolean; failed?: string[] }[] };
export type ReleaseGate = { checks: { required: number; passed: number; failed: string[]; not_run: string[] };
                            runs: string[]; override?: { reason: string; by: string | null } };
export type AuditRow = { id: number; ts: string; action: string; actor: string | null; detail: Record<string, unknown>;
                         agent_id?: string; workspace_id?: string | null };

export type Project = { id: string; name: string; created_at: string | null; platform_default: boolean; agents: number;
  connections: number; mcp_servers: number; tools: number; secrets: number; routes: string[];
  role: "owner" | "admin"; mine: boolean; access?: "member" | "platform"; label: string | null; owner: string | null; default: boolean | null };
export type User = { id: string; email: string; name: string; default_project: string | null };
export type AuthStatus = { mode: "setup" | "login" | "signed_in" | "token" | "open"; user?: User; accounts: boolean;
  mail: boolean; open?: boolean };
export type Member = { user_id: string; name: string; email: string; role: string; status: "joined"; joined_at: string; you: boolean };
export type Invitation = { id: string; email: string; role: string; status: "invited" | "expired" | "joined";
  invited_by: string | null; created_at: string; expires_at: string };

const TOKEN_KEY = "hmg-console-token";
const PROJECT_KEY = "hmg-console-project";
const DEFAULT_PROJECT_KEY = "hmg-console-default-project";
const store = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
/** The default project (★): the one this browser opens with. */
export const getDefaultProject = () => store.get(DEFAULT_PROJECT_KEY) ?? "hmg";
export const setDefaultProject = (id: string) => store.set(DEFAULT_PROJECT_KEY, id);
/** The project every console request works in (sent as X-Project; empty: the server picks the user's default). */
let currentProject = store.get(PROJECT_KEY) ?? "";
export const getProject = () => currentProject;
export const setProject = (id: string) => { currentProject = id; store.set(PROJECT_KEY, id); };
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; } };
export const setToken = (t: string) => { try { localStorage.setItem(TOKEN_KEY, t); } catch { /* private mode */ } };

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail
      : (detail as { message?: string } | null)?.message ?? `HTTP ${status}`);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(currentProject ? { "X-Project": currentProject } : {}),
               ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail; } catch { /* not json */ }
    const d = detail as { login?: boolean; project?: boolean } | null;
    if (res.status === 401 && d?.login) window.dispatchEvent(new Event("console-auth"));      // sign in again
    if (res.status === 404 && d?.project && currentProject) {                                  // project gone / no access
      setProject(""); window.dispatchEvent(new Event("console-project"));
    }
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

export type CatalogVoice = { voice: string; language: string; gender: string; dialect: string; model?: string; style?: string;
  provider: string; used_by: { agent: string; name: string }[]; draft_by: { agent: string; name: string }[] };
export type AgentVoices = { agent: string; name: string; provider: string | null; languages: string[];
  draft_voices: Record<string, string | null>;
  voices: Record<string, { voice: string | null; model: string | null }> };

export type WebhookDelivery = { id: number; call_id: string | null; event: string; ok: boolean; detail: string | null; attempts: number; created_at: string };
export type ApiKey = { id: string; name: string; prefix: string; scope: "read" | "full"; created_by: string | null; created_at: string;
  last_used_at: string | null; expires_at: string | null; revoked_at: string | null; status: "active" | "expired" | "revoked" };
export type NewApiKey = ApiKey & { key: string; note: string };
export type BatchStatus = "scheduled" | "running" | "paused" | "completed" | "failed" | "cancelled";
export type RecipientStatus = "pending" | "in_progress" | "completed" | "failed" | "no_answer";
export type BatchConfig = { send_type: "now" | "schedule"; scheduled_at?: string | null; timezone: string;
  window_start: string; window_end: string; days: number[] };
export type BatchRow = { row?: number; phone: string; name?: string | null; variables: Record<string, string>;
  ignore_e164?: boolean; errors?: string[] };
export type BatchCheck = { accepted: BatchRow[]; rejected: BatchRow[]; duplicates: number; variables: string[] };
export type BatchCall = {
  id: string; name: string; agent_id: string; agent_name: string; from_number: string; status: BatchStatus; config: BatchConfig;
  created_by: string | null; created_at: string; started_at: string | null; finished_at: string | null;
  counts: Partial<Record<RecipientStatus, number>>; total: number; progress: number;
};
export type BatchRecipient = {
  id: number; phone: string; name: string | null; variables: Record<string, string>; status: RecipientStatus; call_id: string | null;
  error: string | null; attempts: number; dialed_at: string | null; ended_at: string | null; duration_s: number | null;
};
export type OutboundNumber = { number: string; label: string | null; dial_url: string; has_token: boolean; created_at?: string | null };
export type PhoneRoute = { pattern: string; agent_id: string; priority: number; label?: string | null; created_at?: string | null };

export const api = {
  apiKeys: () => req<ApiKey[]>("/api/api-keys"),
  createApiKey: (b: { name: string; scope: "read" | "full"; expires_days?: number }) =>
    req<NewApiKey>("/api/api-keys", { method: "POST", body: JSON.stringify(b) }),
  revokeApiKey: (id: string) => req<{ id: string; revoked: boolean }>(`/api/api-keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
  makeOutboundCall: (b: { agent_id: string; from_number: string; to_number: string; params: Record<string, string>; draft?: boolean }) =>
    req<{ batch_call_id: string; recipient_id: number; to: string }>("/api/outbound-calls", { method: "POST", body: JSON.stringify(b) }),
  outboundNumbers: () => req<OutboundNumber[]>("/api/outbound-numbers"),
  putOutboundNumber: (b: { number: string; label?: string; dial_url: string; dial_token?: string }) =>
    req<{ number: string }>("/api/outbound-numbers", { method: "PUT", body: JSON.stringify(b) }),
  deleteOutboundNumber: (n: string) => req<{ number: string }>(`/api/outbound-numbers/${encodeURIComponent(n)}`, { method: "DELETE" }),
  batchCalls: () => req<BatchCall[]>("/api/batch-calls"),
  batchCall: (id: string) => req<BatchCall>(`/api/batch-calls/${encodeURIComponent(id)}`),
  validateBatchRows: (b: { csv_base64?: string; rows?: BatchRow[] }) =>
    req<BatchCheck>("/api/batch-calls/validate", { method: "POST", body: JSON.stringify(b) }),
  createBatch: (b: { name: string; agent_id: string; from_number: string; rows: BatchRow[]; config: BatchConfig }) =>
    req<{ id: string; recipients: number; duplicates: number }>("/api/batch-calls", { method: "POST", body: JSON.stringify(b) }),
  batchAction: (id: string, action: "pause" | "resume" | "cancel" | "retry") =>
    req<{ id: string; status: BatchStatus }>(`/api/batch-calls/${encodeURIComponent(id)}/actions/${action}`, { method: "POST" }),
  renameBatch: (id: string, name: string) =>
    req<{ id: string; name: string }>(`/api/batch-calls/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  deleteBatch: (id: string) => req<{ deleted: string }>(`/api/batch-calls/${encodeURIComponent(id)}`, { method: "DELETE" }),
  batchRecipients: (id: string, p: { status?: string; q?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
    return req<{ items: BatchRecipient[]; total: number; counts: Partial<Record<RecipientStatus, number>> }>(
      `/api/batch-calls/${encodeURIComponent(id)}/recipients?${qs}`);
  },
  addBatchRecipients: (id: string, b: { csv_base64?: string; rows?: BatchRow[] }) =>
    req<{ added: number }>(`/api/batch-calls/${encodeURIComponent(id)}/recipients`, { method: "POST", body: JSON.stringify(b) }),
  removeBatchRecipient: (id: string, rid: number) =>
    req<{ deleted: number }>(`/api/batch-calls/${encodeURIComponent(id)}/recipients/${rid}`, { method: "DELETE" }),
  voices: () => req<AgentVoices[]>("/api/voices"),
  voiceCatalog: () => req<CatalogVoice[]>("/api/voices/catalog"),
  share: (id: string) => req<ShareInfo>(`/api/agents/${encodeURIComponent(id)}/share`),
  putShare: (id: string, settings: ShareSettings) => req<ShareInfo>(`/api/agents/${encodeURIComponent(id)}/share`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings }) }),
  deleteShare: (id: string) => req<{ published: boolean; ended_calls: number }>(`/api/agents/${encodeURIComponent(id)}/share`, { method: "DELETE" }),
  systemVariables: () => req<{ group: string; name: string; description: string }[]>("/api/variables/system"),
  useVoice: (body: { agent: string; language: string; voice: string }) =>
    req<{ agent: string; language: string; voice: string; previous: string | null }>("/api/voices/use",
      { method: "POST", body: JSON.stringify(body) }),
  voicePreview: (body: { agent?: string; language: string; voice?: string; text?: string }) =>
    req<{ audio: string; sample_rate: number; text: string; ms: number; seconds: number }>("/api/voices/preview",
      { method: "POST", body: JSON.stringify(body) }),
  authStatus: () => req<AuthStatus>("/api/auth/status"),
  setup: (body: { name: string; email: string; password: string }) =>
    req<{ token: string; user: User }>("/api/auth/setup", { method: "POST", body: JSON.stringify(body) }),
  login: (email: string, password: string) =>
    req<{ token: string; user: User }>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => req<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  updateMe: (body: { name?: string; password?: string; current_password?: string }) =>
    req<{ user: User }>("/api/me", { method: "PUT", body: JSON.stringify(body) }),
  setDefaultProjectOnServer: (project: string) =>
    req<{ default_project: string }>("/api/me/default-project", { method: "PUT", body: JSON.stringify({ project }) }),
  setProjectLabel: (id: string, label: string) =>
    req<{ label: string | null }>(`/api/projects/${encodeURIComponent(id)}/label`, { method: "PUT", body: JSON.stringify({ label }) }),
  members: (id: string) => req<{ members: Member[]; invitations: Invitation[]; can_manage: boolean; you: string | null;
    mail: boolean; accounts: boolean }>(`/api/projects/${encodeURIComponent(id)}/members`),
  invite: (id: string, email: string) =>
    req<{ invitation: Invitation; link: string; emailed: boolean }>(`/api/projects/${encodeURIComponent(id)}/invitations`,
      { method: "POST", body: JSON.stringify({ email, role: "admin" }) }),
  resendInvite: (id: string, inv: string) =>
    req<{ link: string; emailed: boolean }>(`/api/projects/${encodeURIComponent(id)}/invitations/${inv}/resend`, { method: "POST" }),
  revokeInvite: (id: string, inv: string) =>
    req<{ ok: boolean }>(`/api/projects/${encodeURIComponent(id)}/invitations/${inv}`, { method: "DELETE" }),
  removeMember: (id: string, user: string) =>
    req<{ ok: boolean }>(`/api/projects/${encodeURIComponent(id)}/members/${user}`, { method: "DELETE" }),
  invitation: (token: string) =>
    req<{ project: string; email: string; invited_by: string | null; status: string; has_account: boolean }>(`/api/invitations/${token}`),
  acceptInvitation: (token: string, body: { name?: string; password: string }) =>
    req<{ token: string; user: User; project: string }>(`/api/invitations/${token}/accept`, { method: "POST", body: JSON.stringify(body) }),
  projects: () => req<Project[]>("/api/projects"),
  createProject: (name: string, copy_setup: boolean) =>
    req<{ id: string; name: string }>("/api/projects", { method: "POST", body: JSON.stringify({ name, copy_setup }) }),
  renameProject: (id: string, name: string) =>
    req<{ id: string; name: string }>(`/api/projects/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ name }) }),
  stats: (hours = 24, agent?: string) => req<Stats>(`/api/stats${qs({ hours, agent })}`),
  dashboard: (p: { start: string; end: string; agent?: string; tz?: string }) =>
    req<DashboardData>(`/api/dashboard${qs(p)}`),
  calls: (p: { limit?: number; offset?: number; q?: string; outcome?: string; channel?: string; agent?: string;
                channels?: string; status?: string; start?: string; end?: string; sort?: string; desc?: boolean;
                duration_op?: string; duration_a?: number; duration_b?: number }) =>
    req<{ total: number; items: CallRow[] }>(`/api/calls${qs(p)}`),
  call: (id: string) => req<CallDetail>(`/api/calls/${encodeURIComponent(id)}`),
  /** The recording's audio (needs the sign-in header, so it is fetched, not linked). */
  recordingAudio: async (id: string, download = false): Promise<Blob> => {
    const token = getToken();
    const res = await fetch(`/api/calls/${encodeURIComponent(id)}/recording${download ? "?download=true" : ""}`, {
      headers: { ...(currentProject ? { "X-Project": currentProject } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
    if (!res.ok) {
      let detail: unknown = res.statusText;
      try { detail = (await res.json()).detail; } catch { /* not json */ }
      throw new ApiError(res.status, detail);
    }
    return res.blob();
  },
  deleteRecording: (id: string) => req<{ deleted: boolean }>(`/api/calls/${encodeURIComponent(id)}/recording`, { method: "DELETE" }),
  analyzeCall: (id: string) => req<CallAnalysis>(`/api/calls/${encodeURIComponent(id)}/analyze`, { method: "POST" }),
  satisfaction: (p: { start: string; end: string; agent?: string }) => req<Satisfaction>(`/api/analytics/satisfaction${qs(p)}`),
  events: (p: { call_id?: string; type?: string[]; level?: string; text?: string; before_id?: number; limit?: number; agent?: string }) =>
    req<EventRow[]>(`/api/events${qs(p)}`),
  liveCalls: () => req<ActiveCall[]>("/api/live/calls"),
  instructCall: (id: string, text: string) => req<{ call_id: string; sent: boolean }>(`/api/calls/${encodeURIComponent(id)}/instruction`,
    { method: "POST", body: JSON.stringify({ text }) }),
  endCall: (id: string) => req<{ call_id: string; ended: boolean }>(`/api/calls/${encodeURIComponent(id)}/end`,
    { method: "POST" }),
  providers: (agent?: string) => req<ProvidersResponse>(`/api/providers${qs({ agent })}`),
  saveProviders: (config: AgentConfig, note: string, agent?: string) =>
    req<{ version: number }>("/api/providers", { method: "PUT", body: JSON.stringify({ config, note, agent: agent || null }) }),
  activate: (version: number, agent?: string) =>
    req<{ version: number }>(`/api/providers/activate/${version}${qs({ agent })}`, { method: "POST" }),
  testProvider: (kind: string, provider: string, settings: Record<string, unknown>, agent?: string) =>
    req<Record<string, unknown>>("/api/providers/test", { method: "POST", body: JSON.stringify({ kind, provider, settings, agent: agent || null }) }),
  projectAudit: (id: string) => req<AuditRow[]>(`/api/projects/${encodeURIComponent(id)}/audit`),
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
  createAgent: (body: { name: string; description?: string; copy_from?: string; type?: "flow" | "prompt" }) =>
    req<{ id: string }>("/api/agents", { method: "POST", body: JSON.stringify(body) }),
  importAgent: (body: { content: string; name?: string; filename?: string }) =>
    req<ImportResult>("/api/agents/import", { method: "POST", body: JSON.stringify(body) }),
  renameAgent: (id: string, body: { name: string; description: string }) =>
    req<{ id: string }>(`/api/agents/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteAgent: (id: string) => req<{ deleted: string }>(`/api/agents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  putDraft: (id: string, bundle: Bundle) =>
    req<{ ok: boolean }>(`/api/agents/${encodeURIComponent(id)}/draft`, { method: "PUT", body: JSON.stringify({ bundle }) }),
  testWebhook: (id: string, webhook: Bundle["webhook"], typed?: { token?: string; signing_secret?: string }) =>
    req<{ ok: boolean; detail: string }>(`/api/agents/${encodeURIComponent(id)}/webhook/test`, { method: "POST", body: JSON.stringify({ webhook, ...typed }) }),
  webhookDeliveries: (id: string) => req<WebhookDelivery[]>(`/api/agents/${encodeURIComponent(id)}/webhook/deliveries?limit=50`),
  putDraftSkill: (id: string, key: string, body: { graph?: FlowGraph; files?: Record<string, string>; note?: string }) =>
    req<{ skill: string; version: number }>(`/api/agents/${encodeURIComponent(id)}/draft/skills/${encodeURIComponent(key)}`,
      { method: "PUT", body: JSON.stringify(body) }),
  discardDraft: (id: string) => req<{ ok: boolean }>(`/api/agents/${encodeURIComponent(id)}/draft/discard`, { method: "POST" }),
  publishAgent: (id: string, note: string, override_reason?: string) =>
    req<{ id: number; version: number; gate: ReleaseGate }>(`/api/agents/${encodeURIComponent(id)}/publish`,
      { method: "POST", body: JSON.stringify({ note, override_reason: override_reason || null }) }),
  agentEvals: (id: string) =>
    req<{ cases: AgentCase[]; gate: GateStatus; has_draft: boolean; job: EvalJob | null }>(`/api/agents/${encodeURIComponent(id)}/evals`),
  putAgentEval: (id: string, caseId: string, spec: CaseSpec, gate: boolean) =>
    req<{ ok: boolean }>(`/api/agents/${encodeURIComponent(id)}/evals/${encodeURIComponent(caseId)}`,
      { method: "PUT", body: JSON.stringify({ spec, gate }) }),
  deleteAgentEval: (id: string, caseId: string) =>
    req<{ deleted: string }>(`/api/agents/${encodeURIComponent(id)}/evals/${encodeURIComponent(caseId)}`, { method: "DELETE" }),
  runAgentEvals: (id: string, body: { cases?: string[]; only?: "gate" | "failed" | "all" }) =>
    req<{ job: string; total: number; draft: boolean }>(`/api/agents/${encodeURIComponent(id)}/evals/run`,
      { method: "POST", body: JSON.stringify(body) }),
  agentAudit: (id: string) => req<AuditRow[]>(`/api/agents/${encodeURIComponent(id)}/audit`),
  activateRelease: (id: string, release: number) =>
    req<{ version: number }>(`/api/agents/${encodeURIComponent(id)}/activate/${release}`, { method: "POST" }),
  exportAgent: (id: string) => req<Record<string, unknown>>(`/api/agents/${encodeURIComponent(id)}/export`),
  release: (id: string, release: number) =>
    req<{ bundle: Bundle; version: number }>(`/api/agents/${encodeURIComponent(id)}/releases/${release}`),
  routes: () => req<PhoneRoute[]>("/api/routes"),
  putRoute: (body: { pattern: string; agent_id: string; priority?: number; label?: string }) =>
    req<{ ok: boolean }>("/api/routes", { method: "PUT", body: JSON.stringify(body) }),
  deleteRoute: (pattern: string) => req<{ ok: boolean }>(`/api/routes?pattern=${encodeURIComponent(pattern)}`, { method: "DELETE" }),
  toolLibrary: () => req<ToolLibrary>("/api/tool-library"),
  knowledge: () => req<KbList>("/api/knowledge"),
  kbItem: (id: string) => req<KbItem>(`/api/knowledge/${encodeURIComponent(id)}`),
  addKbText: (body: { name: string; content: string }) =>
    req<{ id: string }>("/api/knowledge/text", { method: "POST", body: JSON.stringify(body) }),
  addKbFile: (body: { filename: string; data: string; name?: string }) =>
    req<{ id: string; words: number }>("/api/knowledge/file", { method: "POST", body: JSON.stringify(body) }),
  addKbUrl: (body: { name: string; url: string; urls?: string[] }) =>
    req<{ id: string; pages: number }>("/api/knowledge/url", { method: "POST", body: JSON.stringify(body) }),
  discoverKbUrl: (url: string) =>
    req<{ urls: string[] }>("/api/knowledge/url/discover", { method: "POST", body: JSON.stringify({ url }) }),
  renameKb: (id: string, name: string) =>
    req<{ id: string; name: string }>(`/api/knowledge/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  deleteKb: (id: string) => req<{ deleted: string }>(`/api/knowledge/${encodeURIComponent(id)}`, { method: "DELETE" }),
  reprocessKb: (id: string) => req<{ id: string }>(`/api/knowledge/${encodeURIComponent(id)}/reprocess`, { method: "POST" }),
  searchKb: (body: { query: string; items?: string[] }) =>
    req<{ mode?: string; results: { source: string; text: string }[] }>("/api/knowledge/search",
      { method: "POST", body: JSON.stringify(body) }),
  useKb: (body: { agent: string; items: string[] }) =>
    req<{ agent: string; items: string[] }>("/api/knowledge/use", { method: "POST", body: JSON.stringify(body) }),
  putTool: (name: string, body: { group: string; policy: ToolPolicy; agents?: string[]; note?: string }) =>
    req<{ name: string; releases: unknown[] }>(`/api/tool-library/${encodeURIComponent(name)}`,
      { method: "PUT", body: JSON.stringify(body) }),
  deleteTool: (name: string, agent?: string) =>
    req<Record<string, unknown>>(`/api/tool-library/${encodeURIComponent(name)}${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`,
      { method: "DELETE" }),
  testTool: (name: string, args: Record<string, unknown>, override?: ToolTestOverride) =>
    req<{ ok: boolean; ms: number; data?: unknown; error?: string }>(`/api/tool-library/${encodeURIComponent(name)}/test`,
      { method: "POST", body: JSON.stringify({ args, override: override ?? {} }) }),
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
  const p = qs({ ...params, project: currentProject || undefined, token: token || undefined });
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
  run: (body: { suite?: string; cases?: string[]; mode: string; judge: boolean; agent?: string }) =>
    req<{ job: string; total: number }>("/api/evals/run", { method: "POST", body: JSON.stringify(body) }),
  job: (job: string) => req<{ status: string; total: number; done: { case: string; passed: boolean }[]; run_id: string | null; error: string | null }>(`/api/evals/jobs/${job}`),
  runs: (agent?: string) => req<EvalRun[]>(`/api/evals/runs${qs({ agent })}`),
  runDetail: (id: string) => req<EvalRun & { results: EvalResult[] }>(`/api/evals/runs/${id}`),
};
