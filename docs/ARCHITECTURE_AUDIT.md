# FORENSIC REPOSITORY AUDIT (Phase 0)

Date: 2026-09-07
Auditor: principal architect (build session)
Subject: GitHub account `ansaribilal14` and candidate repositories

## 1. Environment inspected

| Item | Finding |
|---|---|
| GitHub identity | token verified as `ansaribilal14` (id 295587816) |
| Public repos | 10 |
| Existing "personal-social-agent" | none found |
| Closest prior art | `daily-linkedin-posts-pipeline` (LinkedIn carousel image tool), `babymonster-documentary-pipeline`, `shorts_factory`, plus Kotlin/TS/Rust apps |
| Reusable code for this spec | none - no existing Buffer/NIM/Supabase/Discord integration code, no state machine, no editorial pipeline |
| Local toolchain | Python 3.12.14, git 2.47.3, pytest 9.0.2, requests/PyYAML available |
| Local Postgres | none; `DATABASE_URL` in sandbox points at a SQLite file - runtime supports both Postgres (production) and SQLite (sandbox/tests) |

## 2. Prior-art repository audit

`daily-linkedin-posts-pipeline` was inspected (390 tree entries): a Puppeteer-based
carousel image factory with a committed `.env` (noted as a bad practice; this
repository must never do that - see SECURITY.md). Nothing from it satisfies any
requirement of this specification. The remaining repositories are unrelated
(Kotlin Android apps, a TypeScript streaming site, a Rust privacy project).

Decision: **create a fresh repository** `personal-social-agent` implementing the
spec from zero. Nothing is imported from prior repos.

## 3. What exists after this build

Implemented from scratch (see docs/ARCHITECTURE.md for details):

- explicit content state machine (20 states) + outbox status machine, fully tested
- PostgreSQL/Supabase schema (23 tables) + SQLite mirror for sandbox/tests
- isolated integrations: NVIDIA NIM (OpenAI-compatible), Buffer (GraphQL),
  GitHub (issues/comments), Discord (webhook), Supabase (psycopg2)
- research engine with feed fetching + provenance preservation + contamination flagging
- idea engine with code-computed scoring and the "Why me?" gate
- voice system (stable config profile + learned preferences with promotion threshold)
- 10-critic system + quality engine where PASS/FAIL/REVISE is computed in code
- deterministic platform validators (X weighted-length with URL shortening;
  Threads 500-char) - the LLM is never trusted to count
- originality engine: exact / near / semantic (trigram or embeddings) / hook levels
- claim ledger with FACT-requires-source verification
- scheduling engine (Asia/Kolkata windows, collision protection, daily caps)
- publishing via idempotent outbox with atomic claim, bounded backoff, Discord alerts
- analytics: append-only metric snapshots, sample-size-guarded analysis, weekly report
- 12 GitHub Actions workflows with script-injection-safe env passing
- 119 automated tests including failure modes and a red-team suite

## 4. Credential audit at build time

| Credential | Status | Evidence |
|---|---|---|
| GitHub PAT (`ghp_…` redacted) | VERIFIED WORKING | `GET /user` 200 |
| Buffer token (`op…q3Gt` redacted) | BLOCKED at build time - rejected | `api.bufferapp.com/1/user.json` 401 invalid; `api.buffer.com/api/1/user.json` 401 UNAUTHENTICATED; GraphQL introspection path 404. All variants exhausted (Bearer, query param, alternate header). The adapter is implemented per current docs and classifies the 401 gracefully; publishing will fail safely (never silently) until a valid token is configured. |
| NVIDIA NIM key | NOT PROVIDED | adapter + failure taxonomy implemented; pipelines mark themselves BLOCKED without it |
| Discord webhook | NOT PROVIDED | client implemented; notifications are skipped and logged when unset |
| Supabase | NOT PROVIDED | full schema in migrations/; runtime accepts `SUPABASE_DB_URL`/`DATABASE_URL` (postgres://) |

## 5. Engineering findings logged as issues during the build

The following defects were found by the test/red-team passes and fixed in the
same session (each covered by regression tests):

1. [SECURITY] Concurrent publish runs could both reach Buffer -> fixed with an
   atomic outbox claim (`claim_outbox_entry`, conditional UPDATE) - tests/redteam.
2. [RELIABILITY] Retry path re-picked `BUFFER_ERROR` entries immediately with no
   backoff -> fixed: outbox retry queue honors bounded exponential backoff.
3. [STATE] `PUBLISHING -> PUBLISHING` and `ITERATING -> ITERATING` crashes on
   retries -> fixed: idempotent publisher moves + modeled transitions.
4. [DATA] `UPDATE` rowcounts were masked by `lastrowid` semantics -> execute()
   now returns rowcount for UPDATE/DELETE; claim logic depends on it.
5. [EDITORIAL] Hook critic measured the whole first line of single posts ->
   fixed to first sentence; semantic threshold recalibrated with measured
   separation (reworded dup ~0.50 vs distinct ~0.05 trigram cosine).
6. [SECURITY] voice.yml YAML quoting bug silently broke the voice profile ->
   fixed; config load is now exercised by every quality test.
