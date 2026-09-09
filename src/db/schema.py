"""Dialect-aware schema definitions.

Production: PostgreSQL/Supabase (migrations/001_init.sql).
Tests/local: SQLite mirror so the full lifecycle is testable without a server.
"""
from __future__ import annotations

POSTGRES_SCHEMA_PATH = "migrations/001_init.sql"

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    github_login TEXT UNIQUE,
    display_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    handle_x TEXT, handle_threads TEXT, bio TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS content_pillars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    weight INTEGER NOT NULL DEFAULT 10 CHECK (weight BETWEEN 0 AND 100),
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS research_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE, publisher TEXT, kind TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS research_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_url TEXT, publisher TEXT,
    published_at TEXT, retrieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    summary TEXT, topic TEXT, category TEXT, freshness TEXT,
    source_quality INTEGER CHECK (source_quality BETWEEN 0 AND 100),
    relevance INTEGER CHECK (relevance BETWEEN 0 AND 100),
    contaminated INTEGER NOT NULL DEFAULT 0,
    workflow_run TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER,
    claim_uid TEXT UNIQUE NOT NULL,
    text TEXT NOT NULL,
    claim_type TEXT NOT NULL CHECK (claim_type IN
        ('FACT','OPINION','INFERENCE','SPECULATION','PERSONAL_EXPERIENCE')),
    source_url TEXT, source_title TEXT, evidence TEXT,
    confidence INTEGER CHECK (confidence BETWEEN 0 AND 100),
    status TEXT NOT NULL DEFAULT 'PROPOSED'
        CHECK (status IN ('PROPOSED','VERIFIED','UNVERIFIED','REWRITTEN','REMOVED')),
    verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_uid TEXT UNIQUE NOT NULL,
    pillar TEXT,
    statement TEXT NOT NULL,
    angle TEXT,
    source_item_ids TEXT,
    evaluation TEXT,
    score INTEGER,
    why_me TEXT,
    status TEXT NOT NULL DEFAULT 'CANDIDATE'
        CHECK (status IN ('CANDIDATE','HELD','DISCARDED','PROMOTED')),
    workflow_run TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS angles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES ideas(id),
    text TEXT NOT NULL, rationale TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_uid TEXT UNIQUE NOT NULL,
    idea_id INTEGER REFERENCES ideas(id),
    angle_id INTEGER REFERENCES angles(id),
    platform TEXT NOT NULL CHECK (platform IN ('x','threads')),
    format TEXT NOT NULL CHECK (format IN ('single','thread')),
    pillar TEXT,
    state TEXT NOT NULL,
    editorial_score INTEGER,
    recommended_slot TEXT,
    current_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS post_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    thread_posts TEXT,
    prompt_versions TEXT,
    voice_snapshot TEXT,
    claims_snapshot TEXT,
    iteration_instruction TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (post_id, version)
);
CREATE TABLE IF NOT EXISTS platform_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    platform TEXT NOT NULL, format TEXT NOT NULL, body_digest TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (post_id, platform, format)
);
CREATE TABLE IF NOT EXISTS quality_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    version INTEGER NOT NULL,
    critic TEXT NOT NULL,
    passed INTEGER,
    score INTEGER,
    issues TEXT, evidence TEXT, recommended_changes TEXT,
    engine TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS approval_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    version INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('APPROVED','REJECTED','ITERATED')),
    actor TEXT NOT NULL,
    reason TEXT,
    github_issue_id INTEGER,
    github_comment_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS review_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    comment_id INTEGER NOT NULL,
    command TEXT NOT NULL,
    payload TEXT,
    actor TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    reject_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (issue_id, comment_id)
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    platform TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    scheduled_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS publication_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    idempotency_key TEXT UNIQUE NOT NULL,
    platform TEXT NOT NULL,
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    thread_posts TEXT,
    status TEXT NOT NULL DEFAULT 'OUTBOX_CREATED'
        CHECK (status IN ('OUTBOX_CREATED','PUBLISH_REQUESTED','PUBLISHING',
                          'BUFFER_ACCEPTED','SCHEDULED','BUFFER_ERROR','PUBLISH_FAILED')),
    buffer_post_id TEXT,
    buffer_channel_id TEXT,
    scheduled_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    outbox_id INTEGER NOT NULL REFERENCES publication_outbox(id),
    idempotency_key TEXT UNIQUE NOT NULL,
    platform TEXT NOT NULL,
    version INTEGER NOT NULL,
    buffer_post_id TEXT,
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    platform TEXT NOT NULL,
    interval_hours INTEGER NOT NULL,
    captured_at TEXT NOT NULL DEFAULT (datetime('now')),
    metrics TEXT NOT NULL,
    UNIQUE (post_id, interval_hours, captured_at)
);
CREATE TABLE IF NOT EXISTS strategy_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    period_start TEXT, period_end TEXT,
    report TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS voice_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    promoted INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (kind, key)
);
CREATE TABLE IF NOT EXISTS editorial_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warn','error')),
    post_id INTEGER,
    workflow_run TEXT,
    payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS workflow_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow TEXT NOT NULL,
    run_id TEXT, stage TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed')),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_research_items_fresh ON research_items (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_state ON posts (state);
CREATE INDEX IF NOT EXISTS idx_versions_post ON post_versions (post_id, version);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON publication_outbox (status);
CREATE INDEX IF NOT EXISTS idx_snapshots_post ON metric_snapshots (post_id, interval_hours);
CREATE INDEX IF NOT EXISTS idx_events_type ON system_events (event_type, created_at DESC);
INSERT OR IGNORE INTO system_settings (key, value) VALUES ('SOCIAL_AUTOMATION_ENABLED', 'false');
"""
