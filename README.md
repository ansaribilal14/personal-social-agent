# Personal Social Publishing Operating System

AI does the work. The system enforces the rules. **The human owns the publish
button.**

An autonomous editorial pipeline for **X + Threads** built on GitHub Actions +
NVIDIA NIM + GitHub Issues (review commands) + Discord (review surface) +
Buffer (publishing) + PostgreSQL/Supabase (runtime state).

> Full build specification lives in the project brief; current system docs:
> [docs/](docs) - [ARCHITECTURE](docs/ARCHITECTURE.md) |
> [SECURITY](docs/SECURITY.md) | [OPERATIONS](docs/OPERATIONS.md) |
> [TESTING](docs/TESTING.md) | [EDITORIAL_SYSTEM](docs/EDITORIAL_SYSTEM.md) |
> [API_COMPATIBILITY](docs/API_COMPATIBILITY.md) |
> [FINAL_AUDIT](docs/FINAL_AUDIT.md)

## What it does

1. **Research** - discovers current developments, social signals, evergreen
   material ("what is worth having something to say about?", never "trending").
2. **Ideas** - generates candidates, scores them in code, applies the "Why
   me?" test, holds weak ones.
3. **Generation** - writes X/Threads singles and structured threads in your
   voice, with a claim ledger.
4. **Quality** - 10 editorial critics + deterministic validators + anti-slop
   engine + originality engine; PASS/FAIL/REVISE computed in code. Unverified
   facts never survive.
5. **Review** - top candidates become GitHub issues + Discord review cards.
   You run `/approve`, `/reject <reason>` or `/iterate <instruction>`.
6. **Publishing** - approved versions go through an idempotent outbox to
   Buffer, with collision-protected scheduling (Asia/Kolkata), bounded
   retries, and failure alerts.
7. **Analytics** - append-only metric snapshots, sample-size-guarded analysis,
   weekly reports. Analytics may recommend; only you decide.

**Nothing is published without your explicit approval of the exact version.**
The kill switch (`SOCIAL_AUTOMATION_ENABLED`) can stop scheduling/publishing
at any moment.

## Repository layout

```
.github/workflows/   12 composable workflows (research..maintenance)
src/                 domain engines (state, security, db, research, strategy,
                     generation, critics, validation, similarity, claims,
                     voice, scheduling, publishing, analytics, review, ...)
integrations/        nvidia (NIM), buffer, github, discord, supabase
prompts/             versioned prompt library
config/              platforms, strategy, voice, quality, schedule, security
migrations/          PostgreSQL/Supabase schema (SQLite mirror for tests)
tests/               119 tests: unit, integration, failure, red-team, e2e
scripts/             CLI entrypoints (run_pipeline.py, review_comment_handler.py)
docs/                architecture, security, operations, testing, audit
```

## Quick start

```bash
pip install -e ".[dev]"
python -m pytest tests/              # 119 tests, no network needed
python scripts/run_pipeline.py --stage maintenance
```

Set the GitHub Secrets (`BUFFER_API_KEY`, `NVIDIA_API_KEY`,
`DISCORD_WEBHOOK_URL`, `DATABASE_URL`/`SUPABASE_DB_URL`), apply
`migrations/001_init.sql`, and flip `SOCIAL_AUTOMATION_ENABLED` to `true` when
you are ready to allow scheduling/publishing. Details:
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## The rules the system enforces

- No publication without explicit human approval of the exact version.
- No path to APPROVED except through WAITING_APPROVAL (test-proven).
- Idempotent publishing: one approved version can never publish twice.
- The kill switch is re-checked immediately before scheduling and publishing.
- Research content is untrusted data; injection attempts are flagged.
- FACT claims require real sources; unverified facts are rewritten or removed.
- The LLM never counts characters - deterministic validators do.
