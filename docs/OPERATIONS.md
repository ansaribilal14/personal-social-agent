# OPERATIONS

## 1. Required GitHub Secrets

| Secret | Purpose | Status at build |
|---|---|---|
| `BUFFER_API_KEY` | publishing via Buffer | SET (token rejected by Buffer - refresh it; see below) |
| `NVIDIA_API_KEY` | all AI stages (research synthesis, ideas, writing, critics) | NOT SET - ideas/generate/quality pipelines report BLOCKED without it |
| `DISCORD_WEBHOOK_URL` | review cards, failure alerts, weekly report | NOT SET - notifications skipped+logged |
| `DATABASE_URL` or `SUPABASE_DB_URL` | PostgreSQL/Supabase runtime state | NOT SET - apply migrations/001_init.sql to Supabase, then set the pooler URL (postgres://...) |
| `AUTHORIZED_REVIEWERS` (optional) | comma-separated GitHub logins allowed to run /approve /reject /iterate | defaults to config/security.yml (`ansaribilal14`) |

The GitHub PAT you provided was used only for initial setup (repo creation,
secret writes, push) and is NOT needed by the workflows - they use the
built-in `github.token`.

## 2. Enabling the pipeline (first run)

1. Set the secrets above.
2. Apply `migrations/001_init.sql` in the Supabase SQL editor (or let the
   runner's Postgres connection apply it).
3. Trigger **maintenance** (`workflow_dispatch`) and check the output JSON:
   `database`, `nim`, `buffer` should each report `ok`.
4. Open a PR or run any pipeline manually via `workflow_dispatch` to watch the
   chain: research -> ideas -> generate -> quality -> review.
5. Review candidates on GitHub issues / Discord, then `/approve`.

## 3. The kill switch

```bash
# via psql / Supabase SQL editor:
UPDATE system_settings SET value='true'  WHERE key='SOCIAL_AUTOMATION_ENABLED';  -- allow scheduling+publishing
UPDATE system_settings SET value='false' WHERE key='SOCIAL_AUTOMATION_ENABLED';  -- emergency stop
```

Fail-safe default: disabled. When disabled, research/drafting/analysis/review
still run; scheduling and publishing are refused and audited.

## 4. Manual stage runs

```bash
python scripts/run_pipeline.py --stage research       # feeds -> research_items
python scripts/run_pipeline.py --stage ideas          # candidate ideas + code scoring
python scripts/run_pipeline.py --stage generate       # drafts for top ideas
python scripts/run_pipeline.py --stage quality        # critics + gates
python scripts/run_pipeline.py --stage review         # GitHub issues + Discord cards
python scripts/run_pipeline.py --stage schedule       # APPROVED -> outbox + slot
python scripts/run_pipeline.py --stage publish        # outbox -> Buffer
python scripts/run_pipeline.py --stage analytics      # snapshots + aggregates
python scripts/run_pipeline.py --stage weekly-report  # strategy report + Discord
python scripts/run_pipeline.py --stage maintenance    # health checks + stale recovery
```

Review commands on GitHub issues: `/approve`, `/reject <reason>`,
`/iterate <instruction>` (first line of the comment only; max 500 chars).

## 5. Failure handling cheat-sheet

| Symptom | Meaning | Action |
|---|---|---|
| outbox `BUFFER_ERROR` | transient Buffer failure; bounded backoff retries with SAME content | nothing - it retries; check system_events |
| outbox `PUBLISH_FAILED` | retry exhausted / auth error / refusal | inspect `last_error`; content is preserved, never regenerated |
| `publish.blocked_kill_switch` events | publish attempted while disabled | enable the switch if intended |
| Discord silent | webhook unset or rejected | set `DISCORD_WEBHOOK_URL`; DB rows remain authoritative |
| NIM BLOCKED in logs | `NVIDIA_API_KEY` missing/invalid | set a valid key; no state was mutated |
| stale `PUBLISHING` outbox rows | runner died mid-flight | maintenance resets them to the queue after 30 min |

## 6. Data model notes

- `post_versions` is append-only; UNIQUE(post_id, version) makes versions
  immutable identities.
- `metric_snapshots` is append-only: 1h/6h/24h/48h/7d windows (+ t0 baseline).
- `review_commands` UNIQUE(issue_id, comment_id) blocks comment replay.
- `system_events` is the audit trail for every state mutation.

## 7. Buffer token refresh

The stored Buffer token was rejected at build time (verified 2026-09-07).
Generate a fresh token at buffer.com (Developers), then update the
`BUFFER_API_KEY` secret. No code change is needed; the adapter discovers
channels and posts through the current GraphQL API.
