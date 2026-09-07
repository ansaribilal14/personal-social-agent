# ARCHITECTURE

## 1. Principle

AI does the work. The system enforces the rules. The human owns the publish
button. NOTHING is published without explicit human approval of the exact
version (spec sections 2-3).

## 2. Component map

```
GitHub Actions (12 workflows, cron + issue_comment)
        |
        v
scripts/run_pipeline.py  +  scripts/review_comment_handler.py
        |
        +-- src/pipeline/*        orchestrators (one per stage, idempotent)
        +-- src/state/machine.py  explicit 20-state content machine
        +-- src/db/*              repository over PostgreSQL/Supabase (SQLite in sandbox/tests)
        +-- src/security/*        kill switch, redaction, command authz, injection defense
        +-- src/research          feeds -> research_items (provenance preserved, contamination flagged)
        +-- src/strategy          idea scoring computed in code; "Why me?" gate; pillars
        +-- src/voice             stable voice config + editorial memory + learning loop
        +-- src/generation        writer + iterator (structured output, claims ledger)
        +-- src/critics           7 critic classes (10 named roles) + code-computed quality engine
        +-- src/validation        deterministic X/Threads/unicode/URL validators
        +-- src/similarity        originality engine (exact/near/semantic/hook)
        +-- src/claims            claim ledger (FACT requires source)
        +-- src/scheduling        windows (Asia/Kolkata), collision protection, daily caps
        +-- src/publishing        idempotent outbox -> Buffer, bounded retries, failure alerts
        +-- src/analytics         append-only snapshots, sample-size-guarded analysis, weekly report
        +-- src/review            GitHub issue lifecycle (approve/reject/iterate)
        +-- src/notifications     Discord presentation only (never authoritative)
        |
        v
Buffer (publishing abstraction) --> X, Threads
Discord (review cards, failure alerts, weekly report)
```

## 3. Content lifecycle (state machine)

DISCOVERED -> CANDIDATE -> RESEARCHED -> STRATEGIZED -> DRAFTED ->
QUALITY_REVIEW -> QUALITY_PASSED -> EDITORIALLY_RANKED -> WAITING_APPROVAL ->
APPROVED -> SCHEDULED -> PUBLISHING -> PUBLISHED

Side states: QUALITY_FAILED/REVISING (bounded revision loop), ITERATING
(human-requested new version), REJECTED/CANCELLED/BLOCKED/PUBLISH_FAILED.

Invariants (enforced by `src/state/machine.py` and proven by tests):
- APPROVED is reachable only from WAITING_APPROVAL
- PUBLISHED is reachable only from PUBLISHING
- every invalid transition raises; nothing infers state from text
- approving v1 never authorizes v2; publishing re-verifies the approval event
  for the exact version immediately before calling Buffer

## 4. Outbox pattern (spec 11)

APPROVED -> OUTBOX_CREATED -> PUBLISH_REQUESTED (atomic claim) ->
BUFFER_ACCEPTED -> SCHEDULED; failures -> BUFFER_ERROR (bounded exponential
backoff, same content) -> PUBLISH_FAILED + Discord alert.

Idempotency is enforced at the DATABASE level:
- `publication_outbox.idempotency_key UNIQUE` = `post_uid:platform:version`
- `publications.idempotency_key UNIQUE`
- atomic claim: conditional `UPDATE ... SET status='PUBLISHING' WHERE status IN
  ('OUTBOX_CREATED','PUBLISH_REQUESTED','BUFFER_ERROR')` - exactly one run wins

## 5. Kill switch

`SOCIAL_AUTOMATION_ENABLED` in `system_settings` (DB is authoritative).
Research/drafting/analysis/review run regardless; scheduling and publishing
re-read the switch IMMEDIATELY before acting (never cached). If the DB is
unreadable, publishing is blocked (fail-safe).

## 6. Quality pipeline (spec 24-25)

7 critic classes implement the 10 required editorial roles; each returns
structured `{passed, score, issues, evidence, recommended_changes}`. The final
PASS/FAIL/REVISE is computed in code from config thresholds. Hard gates
(platform_fit, antislop, fact) FAIL the candidate regardless of any score. No
LLM-asserted number can pass a post.

## 7. Prompt-injection defense (spec 57)

External research is wrapped in UNTRUSTED_DATA delimiters, scanned for
instruction patterns (flagged contaminated -> excluded from generation), and
delimiters are neutralized inside untrusted payloads. Comment bodies reach
Python via env vars only; only exact `/approve`, `/reject`, `/iterate` are
parsed; iterate instructions are bounded and stored as inert text.

## 8. Configuration (spec 60)

All thresholds, windows, pillars, voice, retry policy, security lists live in
`config/*.yml`. Nothing is buried in Python. Prompt versions live in
`prompts/library.yml` + `src/prompts.py` registry and are snapshotted onto
every generated version (spec 61).
