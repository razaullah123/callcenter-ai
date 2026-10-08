-- Voice agent database schema (reference data for local tools).
-- Idempotent: safe to run repeatedly.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

-- Hospitals / medical centers. reference_id is the HIS ProjectID used by MCP tools.
CREATE TABLE IF NOT EXISTS projects (
    id              uuid PRIMARY KEY,
    project_name    text NOT NULL,
    ar_name         text,
    reference_id    integer,
    latitude        double precision,
    longitude       double precision,
    aliases         jsonb,            -- array of Arabic / English name variants
    timezone        text,
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    city_name_en    text,
    city_name_ar    text,
    base_extension  text,
    prefix_allow    boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS projects_reference_id_uq ON projects (reference_id) WHERE reference_id IS NOT NULL;

-- Cities / districts used to turn a caller's spoken location into coordinates.
CREATE TABLE IF NOT EXISTS locations (
    id                uuid PRIMARY KEY,
    city_name_en      text,
    city_name_ar      text,
    district_name_en  text,
    district_name_ar  text,
    latitude          double precision,
    longitude         double precision,
    aliases           jsonb,          -- {city_name_arabic, city_name_english, district_name_arabic, district_name_english}
    active            boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    embedding         vector(1024)    -- EMBEDDING_URL of "{city_name_en} {city_name_ar} {district_name_en} {district_name_ar}"
);
CREATE INDEX IF NOT EXISTS idx_locations_embedding
    ON locations USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_locations_trgm_city_name_en ON locations USING gin (city_name_en gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_locations_trgm_city_name_ar ON locations USING gin (city_name_ar gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_locations_trgm_district_name_en ON locations USING gin (district_name_en gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_locations_trgm_district_name_ar ON locations USING gin (district_name_ar gin_trgm_ops);

-- IVR access control (same tokens / numbers as the existing IVR integration; imported by
-- scripts/clone_reference_data.py — the voice agent never reads the source database at run time).
CREATE TABLE IF NOT EXISTS blacklisted_tokens (
    id              bigserial PRIMARY KEY,
    token_jti       text NOT NULL UNIQUE,
    username        text NOT NULL,
    blacklisted_at  timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blacklisted_tokens_expires ON blacklisted_tokens (expires_at);
CREATE TABLE IF NOT EXISTS white_listed_numbers (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mobile_number  text NOT NULL UNIQUE,
    name           text,
    active         boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- control plane (Phase 9)

-- One row per call (upserted from call.start / call.end / key events).
CREATE TABLE IF NOT EXISTS calls (
    call_id         text PRIMARY KEY,
    channel         text,                 -- ivr | web | chat
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    language        text,
    verified        boolean NOT NULL DEFAULT false,
    booked          boolean NOT NULL DEFAULT false,
    handoff         text,                 -- reason, when transferred to a human
    end_reason      text,
    turns           integer NOT NULL DEFAULT 0,
    last_skill      text,
    config_version  integer,
    latency_p50_ms  double precision,
    extension_call  boolean NOT NULL DEFAULT false
);
-- caller's mobile (the number they gave and was looked up, else the IVR calling number) — PII: kept only in this
-- row (events / logs stay masked) and cleared after EVENT_RETENTION_DAYS by the retention job
ALTER TABLE calls ADD COLUMN IF NOT EXISTS mobile text;
CREATE INDEX IF NOT EXISTS calls_mobile ON calls (mobile);
CREATE INDEX IF NOT EXISTS calls_started_idx ON calls (started_at DESC);

-- Every harness / voice event (PII already redacted by the event bus).
CREATE TABLE IF NOT EXISTS call_events (
    id          bigserial PRIMARY KEY,
    event_id    text,
    call_id     text,
    ts          timestamptz NOT NULL,
    type        text NOT NULL,
    level       text NOT NULL,
    turn_id     integer,
    skill       text,
    step        text,
    latency_ms  double precision,
    data        jsonb
);
CREATE INDEX IF NOT EXISTS call_events_call_idx ON call_events (call_id, ts);
CREATE INDEX IF NOT EXISTS call_events_ts_idx ON call_events (ts DESC);
CREATE INDEX IF NOT EXISTS call_events_type_idx ON call_events (type, ts DESC);

-- Versioned agent configuration (providers + runtime knobs). New calls use the active version.
CREATE TABLE IF NOT EXISTS agent_config (
    version     serial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    author      text,
    note        text,
    config      jsonb NOT NULL,
    active      boolean NOT NULL DEFAULT false
);

-- Versioned skill files (SKILL.md / flow.yaml). The files on disk are the live version.
CREATE TABLE IF NOT EXISTS skill_versions (
    id          serial PRIMARY KEY,
    skill       text NOT NULL,
    version     integer NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    author      text,
    note        text,
    files       jsonb NOT NULL,
    UNIQUE (skill, version)
);

-- Eval runs (Phase 6): summary + per-case results (transcripts, checks, judge scores).
CREATE TABLE IF NOT EXISTS eval_runs (
    id           text PRIMARY KEY,
    started_at   timestamptz NOT NULL,
    finished_at  timestamptz,
    model        text,
    mode         text,
    summary      jsonb,
    results      jsonb
);

-- Eval runs started from the console, in progress (any worker can report them; Phase 12.3).
CREATE TABLE IF NOT EXISTS eval_jobs (
    id          text PRIMARY KEY,
    status      text NOT NULL,                -- running | done | error
    total       integer NOT NULL,
    done        jsonb NOT NULL DEFAULT '[]',
    run_id      text,
    error       text,
    worker      text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Patient-data access audit (PDPL): who / which call touched which patient's data, with which tool.
CREATE TABLE IF NOT EXISTS patient_access_audit (
    id          bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL,
    call_id     text NOT NULL,
    channel     text,
    tool        text NOT NULL,
    kind        text NOT NULL,          -- read | write | send
    patient_id  text,
    ok          boolean NOT NULL,
    confirmed   boolean NOT NULL DEFAULT false,   -- write executed after the caller's explicit yes
    verified    boolean NOT NULL DEFAULT false,   -- caller was OTP-verified at the time
    detail      text
);
CREATE INDEX IF NOT EXISTS patient_access_audit_patient_idx ON patient_access_audit (patient_id, ts DESC);
CREATE INDEX IF NOT EXISTS patient_access_audit_ts_idx ON patient_access_audit (ts DESC);

-- ---------------------------------------------------------------- platform (Phase 12.1): many agents, all in the DB
-- An agent release is an immutable snapshot of everything a call needs (flow / skills, prompts, tools, model choice,
-- voice, knobs); a call loads its agent's published release once and keeps it to the end.
CREATE TABLE IF NOT EXISTS workspaces (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Secrets (API keys, tokens): Fernet ciphertext under MASTER_KEY (.env); referenced by name as {"secret": NAME}.
CREATE TABLE IF NOT EXISTS secrets (
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    name          text NOT NULL,
    ciphertext    text NOT NULL,
    hint          text NOT NULL DEFAULT '',     -- "••••2f9a": enough to recognise a key, never the key
    updated_by    text,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, name)
);

-- Named provider connections (STT / LLM / TTS / embeddings). Shared by agents; edits apply to new calls.
CREATE TABLE IF NOT EXISTS providers (
    id            text PRIMARY KEY,
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    kind          text NOT NULL CHECK (kind IN ('stt', 'llm', 'tts', 'embedding')),
    name          text NOT NULL,
    type          text NOT NULL,                -- registry name: groq | openai_compat | custom_http | fake
    settings      jsonb NOT NULL DEFAULT '{}',  -- connection settings; keys as {"secret": NAME}
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    text,
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id            text PRIMARY KEY,
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    name          text NOT NULL,                -- tools are namespaced by this name
    transport     text NOT NULL DEFAULT 'streamable_http',
    url           text NOT NULL,
    auth          jsonb NOT NULL DEFAULT '{}',  -- {"header", "scheme", "secret": NAME}
    enabled       boolean NOT NULL DEFAULT true,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, name)
);

-- Tool library with each tool's policy (kind, confirmation, timeout, cache …). Releases freeze the policies they use.
CREATE TABLE IF NOT EXISTS tools (
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    name          text NOT NULL,
    grp           text NOT NULL,                -- group (today: the skill it belongs to)
    source        text NOT NULL DEFAULT 'mcp',  -- mcp | local
    policy        jsonb NOT NULL DEFAULT '{}',
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, name)
);

-- Skill library (reusable instructions + flows); versions live in skill_versions.
CREATE TABLE IF NOT EXISTS skills (
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    name          text NOT NULL,
    repo_hash     text,                         -- hash of the repo files last imported (repo sync)
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, name)
);
ALTER TABLE skill_versions ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'hmg';

CREATE TABLE IF NOT EXISTS agents (
    id                    text PRIMARY KEY,
    workspace_id          text NOT NULL REFERENCES workspaces (id),
    name                  text NOT NULL,
    description           text NOT NULL DEFAULT '',
    published_release_id  bigint,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_releases (
    id          bigserial PRIMARY KEY,
    agent_id    text NOT NULL REFERENCES agents (id),
    version     integer NOT NULL,
    bundle      jsonb NOT NULL,
    author      text,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (agent_id, version)
);

-- Which agent answers: an IVR number / extension prefix, or '*' (default).
CREATE TABLE IF NOT EXISTS phone_routes (
    id            bigserial PRIMARY KEY,
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    pattern       text NOT NULL,                -- '*' | exact number | extension prefix
    agent_id      text NOT NULL REFERENCES agents (id),
    priority      integer NOT NULL DEFAULT 0,
    UNIQUE (workspace_id, pattern)
);

ALTER TABLE phone_routes ADD COLUMN IF NOT EXISTS label text;
ALTER TABLE phone_routes ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS platform_meta (
    key    text PRIMARY KEY,
    value  jsonb NOT NULL
);

ALTER TABLE calls ADD COLUMN IF NOT EXISTS agent_id text;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS release_id bigint;

-- Agent Studio (Phase 12.6): the working copy an agent is edited in; published as a new release.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS draft jsonb;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS draft_updated_at timestamptz;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS draft_updated_by text;

-- ---------------------------------------------------------------- publish gate (Phase 12.7)
-- Each agent's test cases (an evals/cases spec: caller, expect, …). Gate cases must pass on the draft before Publish.
CREATE TABLE IF NOT EXISTS agent_eval_cases (
    agent_id    text NOT NULL,
    id          text NOT NULL,
    spec        jsonb NOT NULL,
    gate        boolean NOT NULL DEFAULT true,
    updated_by  text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, id)
);
-- Latest result of each case on the agent's current draft: {draft_hash, cases: {id: {passed, spec_hash, run_id, …}}}
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gate jsonb;
-- How a release got published: the checks that passed, or an override with its reason.
ALTER TABLE agent_releases ADD COLUMN IF NOT EXISTS gate jsonb;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS agent_id text;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS target jsonb;
-- Who published / rolled back / overrode the gate / changed the test cases, and why.
CREATE TABLE IF NOT EXISTS agent_audit (
    id        bigserial PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    agent_id  text NOT NULL,
    action    text NOT NULL,
    actor     text,
    detail    jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS agent_audit_agent_idx ON agent_audit (agent_id, ts DESC);

-- Project audit log (Project settings → Audit log): agent events + project events (agent_id '').
ALTER TABLE agent_audit ADD COLUMN IF NOT EXISTS workspace_id text;
UPDATE agent_audit u SET workspace_id = a.workspace_id FROM agents a
 WHERE u.workspace_id IS NULL AND a.id = u.agent_id;
CREATE INDEX IF NOT EXISTS agent_audit_ws_idx ON agent_audit (workspace_id, ts DESC);

-- ---------------------------------------------------------------- user accounts + project members (console sign-in)
CREATE TABLE IF NOT EXISTS console_users (
    id               text PRIMARY KEY,
    email            text NOT NULL UNIQUE,          -- lower-case
    name             text NOT NULL DEFAULT '',
    password_hash    text NOT NULL,                 -- scrypt
    default_project  text,                          -- ★ the project this user opens with
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_login_at    timestamptz
);
CREATE TABLE IF NOT EXISTS console_sessions (
    token_hash  text PRIMARY KEY,                   -- sha256 of the bearer token; the token itself is never stored
    user_id     text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS project_members (
    workspace_id  text NOT NULL,
    user_id       text NOT NULL,
    role          text NOT NULL,                    -- owner | admin | viewer (read-only)
    label         text,                             -- the member's own name for the project (only they see it)
    agent_ids     text[],                           -- NULL: every agent of the project; else only these
    joined_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS project_invitations (
    id            text PRIMARY KEY,
    workspace_id  text NOT NULL,
    email         text NOT NULL,
    role          text NOT NULL DEFAULT 'admin',
    token_hash    text NOT NULL UNIQUE,
    invited_by    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    accepted_at   timestamptz
);
CREATE INDEX IF NOT EXISTS project_invitations_ws_idx ON project_invitations (workspace_id);
ALTER TABLE project_members ADD COLUMN IF NOT EXISTS agent_ids text[];
ALTER TABLE project_invitations ADD COLUMN IF NOT EXISTS agent_ids text[];


-- Knowledge base (12.9): documents and free text per project, split into chunks the agents search during a call
-- (search_knowledge_base). The original file isn't kept — only its extracted text.
CREATE TABLE IF NOT EXISTS kb_items (
    id            text PRIMARY KEY,
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    name          text NOT NULL,
    type          text NOT NULL,                  -- text | file | url
    extension     text,                           -- pdf | docx | txt | html | epub | md (files)
    size_bytes    integer NOT NULL DEFAULT 0,
    words         integer NOT NULL DEFAULT 0,
    chunks        integer NOT NULL DEFAULT 0,
    status        text NOT NULL DEFAULT 'processing',   -- processing | completed | completed_with_errors | failed
    error         text,
    content       text,                           -- the extracted text
    created_by    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE kb_items ADD COLUMN IF NOT EXISTS source_url text;   -- url items: the page (or the site) the text came from
CREATE INDEX IF NOT EXISTS kb_items_ws_idx ON kb_items (workspace_id, created_at DESC);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id            bigserial PRIMARY KEY,
    item_id       text NOT NULL REFERENCES kb_items (id) ON DELETE CASCADE,
    workspace_id  text NOT NULL,
    seq           integer NOT NULL,
    text          text NOT NULL,
    embedding     vector(1024)                    -- NULL when the embedding service was unavailable (keyword search)
);
CREATE INDEX IF NOT EXISTS kb_chunks_item_idx ON kb_chunks (item_id, seq);
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx ON kb_chunks USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS kb_chunks_text_idx ON kb_chunks USING gin (to_tsvector('simple', text));

-- ---------------------------------------------------------------- public page / embed (Publishing)
-- One link per agent anyone can open and talk to; deleting the row unpublishes. The link only starts the published release.
CREATE TABLE IF NOT EXISTS agent_shares (
    agent_id      text PRIMARY KEY REFERENCES agents (id) ON DELETE CASCADE,
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    token         text NOT NULL UNIQUE,
    settings      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- batch (outbound) calls
-- Numbers an agent may dial from: each has a "dial URL" — the platform POSTs every call to it, the IVR / PBX places the
-- call and connects its audio to our IVR socket once the callee answers.
CREATE TABLE IF NOT EXISTS outbound_numbers (
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    number        text NOT NULL,
    label         text,
    dial_url      text NOT NULL,
    dial_auth     jsonb,                                  -- {"secret": NAME}: bearer token for the dial URL
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, number)
);
CREATE TABLE IF NOT EXISTS batch_calls (
    id            text PRIMARY KEY,
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    name          text NOT NULL,
    agent_id      text NOT NULL,
    from_number   text NOT NULL,
    status        text NOT NULL DEFAULT 'scheduled',     -- scheduled | running | paused | completed | failed | cancelled
    config        jsonb NOT NULL DEFAULT '{}'::jsonb,    -- send_type, scheduled_at, timezone, window_start / _end, days
    created_by    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS batch_calls_ws_idx ON batch_calls (workspace_id, created_at DESC);
CREATE TABLE IF NOT EXISTS batch_recipients (
    id            bigserial PRIMARY KEY,
    batch_id      text NOT NULL REFERENCES batch_calls (id) ON DELETE CASCADE,
    phone         text NOT NULL,
    name          text,
    variables     jsonb NOT NULL DEFAULT '{}'::jsonb,
    status        text NOT NULL DEFAULT 'pending',       -- pending | queued | in_progress | completed | failed | no_answer
    call_id       text,
    error         text,
    attempts      integer NOT NULL DEFAULT 0,
    dialed_at     timestamptz,
    ended_at      timestamptz,
    duration_s    real
);
CREATE INDEX IF NOT EXISTS batch_recipients_idx ON batch_recipients (batch_id, status, id);

-- ---------------------------------------------------------------- call webhook delivery history (latest 200 per agent)
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id            bigserial PRIMARY KEY,
    agent_id      text NOT NULL REFERENCES agents (id) ON DELETE CASCADE,
    workspace_id  text NOT NULL,
    call_id       text,
    event         text NOT NULL,
    ok            boolean NOT NULL,
    detail        text,
    attempts      integer NOT NULL DEFAULT 1,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS webhook_deliveries_agent_idx ON webhook_deliveries (agent_id, id DESC);

-- ---------------------------------------------------------------- post-call analysis (summary, sentiment, satisfaction estimates, outcome fields)
CREATE TABLE IF NOT EXISTS call_analysis (
    call_id       text PRIMARY KEY,
    workspace_id  text NOT NULL,
    agent_id      text,
    started_at    timestamptz,                    -- the call's start (for date ranges)
    status        text NOT NULL,                  -- ok | skipped | failed
    error         text,
    summary       text,
    sentiment     text,                           -- positive | neutral | negative
    csat          smallint,                       -- 1-5, estimated from the transcript
    nps           smallint,                       -- 0-10, estimated from the transcript
    resolved      boolean,
    outcome       jsonb NOT NULL DEFAULT '{}'::jsonb,   -- the agent's own fields
    model         text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS call_analysis_ws_idx ON call_analysis (workspace_id, started_at DESC);

-- ---------------------------------------------------------------- call recordings (the audio is a file, encrypted, under RECORDINGS_DIR)
CREATE TABLE IF NOT EXISTS call_recordings (
    call_id       text PRIMARY KEY,
    workspace_id  text NOT NULL,
    agent_id      text,
    status        text NOT NULL,                  -- ok | failed | expired | deleted
    path          text,                           -- relative to RECORDINGS_DIR
    size_bytes    bigint,
    duration_s    double precision,
    error         text,
    started_at    timestamptz,
    expires_at    timestamptz NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS call_recordings_expiry_idx ON call_recordings (expires_at) WHERE status = 'ok';

-- ---------------------------------------------------------------- API keys (per project; only the hash is stored)
CREATE TABLE IF NOT EXISTS api_keys (
    id            text PRIMARY KEY,
    workspace_id  text NOT NULL REFERENCES workspaces (id),
    name          text NOT NULL,
    prefix        text NOT NULL,                 -- the first characters, to recognise a key in the list
    key_hash      text NOT NULL UNIQUE,          -- sha256 of the key; the key itself is shown once, when it is made
    scope         text NOT NULL DEFAULT 'full',  -- read | full
    created_by    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    expires_at    timestamptz,
    revoked_at    timestamptz
);
CREATE INDEX IF NOT EXISTS api_keys_ws_idx ON api_keys (workspace_id, created_at DESC);
