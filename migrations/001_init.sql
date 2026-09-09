-- 001_init.sql - Personal Social Publishing OS - PostgreSQL/Supabase schema.
-- Apply via Supabase SQL editor or psql. The Python layer (src/db/schema.py)
-- mirrors this schema for SQLite (tests/local) with dialect adjustments.
-- RULES: every state mutation auditable; idempotency keys UNIQUE at DB level;
-- metric snapshots append-only; never store secrets here.

CREATE TABLE IF NOT EXISTS users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    github_login  TEXT UNIQUE,
    display_name  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS profiles (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT REFERENCES users(id),
    handle_x    TEXT,
    handle_threads TEXT,
    bio         TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content_pillars (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    weight     INTEGER NOT NULL DEFAULT 10 CHECK (weight BETWEEN 0 AND 100),
    enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research_sources (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    url          TEXT UNIQUE,
    publisher    TEXT,
    kind         TEXT,                        -- feed | nim_knowledge | manual
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research_items (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title          TEXT NOT NULL,
    source_url     TEXT,
    publisher      TEXT,
    published_at   TIMESTAMPTZ,
    retrieved_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    summary        TEXT,
    topic          TEXT,
    category       TEXT,                      -- current_development | social_signal | evergreen
    freshness      TEXT,                      -- fresh | recent | evergreen
    source_quality INTEGER CHECK (source_quality BETWEEN 0 AND 100),
    relevance      INTEGER CHECK (relevance BETWEEN 0 AND 100),
    contaminated   BOOLEAN NOT NULL DEFAULT FALSE,  -- prompt-injection flag (spec 57)
    workflow_run   TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS claims (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    content_id   BIGINT,                       -- nullable until attached to content
    claim_uid    TEXT UNIQUE NOT NULL,         -- stable claim_id from ledger
    text         TEXT NOT NULL,
    claim_type   TEXT NOT NULL CHECK (claim_type IN
                 ('FACT','OPINION','INFERENCE','SPECULATION','PERSONAL_EXPERIENCE')),
    source_url   TEXT,
    source_title TEXT,
    evidence     TEXT,
    confidence   INTEGER CHECK (confidence BETWEEN 0 AND 100),
    status       TEXT NOT NULL DEFAULT 'PROPOSED'
                 CHECK (status IN ('PROPOSED','VERIFIED','UNVERIFIED','REWRITTEN','REMOVED')),
    verified_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ideas (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idea_uid      TEXT UNIQUE NOT NULL,
    pillar        TEXT,
    statement     TEXT NOT NULL,
    angle         TEXT,                        -- strategist's editorial interpretation,
                                                 -- distinct from the bare statement (see angles table)
    source_item_ids BIGINT[],
    evaluation    JSONB,                       -- component scores from NIM (data only)
    score         INTEGER,                     -- COMPUTED IN CODE (spec 16)
    why_me        JSONB,                       -- the five answers (spec 17)
    status        TEXT NOT NULL DEFAULT 'CANDIDATE'
                  CHECK (status IN ('CANDIDATE','HELD','DISCARDED','PROMOTED')),
    workflow_run  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS angles (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idea_id     BIGINT NOT NULL REFERENCES ideas(id),
    text        TEXT NOT NULL,
    rationale   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS posts (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_uid    TEXT UNIQUE NOT NULL,          -- e.g. X-2026-00183 (spec 32/33)
    idea_id     BIGINT REFERENCES ideas(id),
    angle_id    BIGINT REFERENCES angles(id),
    platform    TEXT NOT NULL CHECK (platform IN ('x','threads')),
    format      TEXT NOT NULL CHECK (format IN ('single','thread')),
    pillar      TEXT,
    state       TEXT NOT NULL,
    editorial_score INTEGER,
    recommended_slot TIMESTAMPTZ,
    current_version INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS post_versions (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id      BIGINT NOT NULL REFERENCES posts(id),
    version      INTEGER NOT NULL,
    body         TEXT NOT NULL,
    thread_posts JSONB,                       -- list of post bodies for threads
    prompt_versions JSONB,                    -- prompt name -> version (spec 61)
    voice_snapshot JSONB,
    claims_snapshot JSONB,
    iteration_instruction TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (post_id, version)                 -- versions immutable (spec 36)
);

CREATE TABLE IF NOT EXISTS platform_variants (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id     BIGINT NOT NULL REFERENCES posts(id),
    platform    TEXT NOT NULL,
    format      TEXT NOT NULL,
    body_digest TEXT,                          -- for exact-dup detection
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (post_id, platform, format)
);

CREATE TABLE IF NOT EXISTS quality_evaluations (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id      BIGINT NOT NULL REFERENCES posts(id),
    version      INTEGER NOT NULL,
    critic       TEXT NOT NULL,
    passed       BOOLEAN,
    score        INTEGER,
    issues       JSONB,
    evidence     JSONB,
    recommended_changes JSONB,
    engine       TEXT NOT NULL,               -- deterministic | nim
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approval_events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id     BIGINT NOT NULL REFERENCES posts(id),
    version     INTEGER NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('APPROVED','REJECTED','ITERATED')),
    actor       TEXT NOT NULL,
    reason      TEXT,
    github_issue_id BIGINT,
    github_comment_id BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_commands (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issue_id        BIGINT NOT NULL,
    comment_id      BIGINT NOT NULL,
    command         TEXT NOT NULL,
    payload         TEXT,
    actor           TEXT NOT NULL,
    accepted        BOOLEAN NOT NULL DEFAULT FALSE,
    reject_reason   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (issue_id, comment_id)             -- replay protection (spec 56)
);

CREATE TABLE IF NOT EXISTS schedules (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id     BIGINT NOT NULL REFERENCES posts(id),
    platform    TEXT NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    scheduled_by TEXT NOT NULL DEFAULT 'system',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS publication_outbox (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id         BIGINT NOT NULL REFERENCES posts(id),
    idempotency_key TEXT UNIQUE NOT NULL,     -- post_uid:platform:version (spec 10)
    platform        TEXT NOT NULL,
    version         INTEGER NOT NULL,
    body            TEXT NOT NULL,
    thread_posts    JSONB,
    status          TEXT NOT NULL DEFAULT 'OUTBOX_CREATED'
                    CHECK (status IN ('OUTBOX_CREATED','PUBLISH_REQUESTED','PUBLISHING',
                                      'BUFFER_ACCEPTED','SCHEDULED','BUFFER_ERROR','PUBLISH_FAILED')),
    buffer_post_id  TEXT,
    buffer_channel_id TEXT,
    scheduled_at    TIMESTAMPTZ,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS publications (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id         BIGINT NOT NULL REFERENCES posts(id),
    outbox_id       BIGINT NOT NULL REFERENCES publication_outbox(id),
    idempotency_key TEXT UNIQUE NOT NULL,
    platform        TEXT NOT NULL,
    version         INTEGER NOT NULL,
    buffer_post_id  TEXT,
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id       BIGINT NOT NULL REFERENCES posts(id),
    platform      TEXT NOT NULL,
    interval_hours INTEGER NOT NULL,          -- 1,6,24,48,168
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metrics       JSONB NOT NULL,              -- only fields actually exposed (spec 41)
    UNIQUE (post_id, interval_hours, captured_at)
);

CREATE TABLE IF NOT EXISTS strategy_reports (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        TEXT NOT NULL,                 -- weekly | experiment
    period_start TIMESTAMPTZ,
    period_end  TIMESTAMPTZ,
    report      JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS voice_preferences (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        TEXT NOT NULL,                 -- stable | editorial | learned
    key         TEXT NOT NULL,
    value       JSONB NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    promoted    BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, key)
);

CREATE TABLE IF NOT EXISTS editorial_preferences (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key         TEXT UNIQUE NOT NULL,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS system_events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type  TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warn','error')),
    post_id     BIGINT,
    workflow_run TEXT,
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workflow     TEXT NOT NULL,
    run_id       TEXT,
    stage        TEXT,
    status       TEXT NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running','success','failed')),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS system_settings (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hot-path indexes
CREATE INDEX IF NOT EXISTS idx_research_items_fresh ON research_items (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_state ON posts (state);
CREATE INDEX IF NOT EXISTS idx_versions_post ON post_versions (post_id, version);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON publication_outbox (status);
CREATE INDEX IF NOT EXISTS idx_snapshots_post ON metric_snapshots (post_id, interval_hours);
CREATE INDEX IF NOT EXISTS idx_events_type ON system_events (event_type, created_at DESC);

-- Kill switch default row (fail-safe: publishing disabled until enabled)
INSERT INTO system_settings (key, value)
VALUES ('SOCIAL_AUTOMATION_ENABLED', 'false'::jsonb)
ON CONFLICT (key) DO NOTHING;
