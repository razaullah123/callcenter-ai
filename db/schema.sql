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
