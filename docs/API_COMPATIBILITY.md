# API COMPATIBILITY

Verified against current official documentation during the build. Anything that
could not be verified is marked BLOCKED - nothing is invented.

## 1. NVIDIA NIM

| Property | Value |
|---|---|
| Endpoint | `POST https://integrate.api.nvidia.com/v1/chat/completions` (OpenAI-compatible) |
| Auth | `Authorization: Bearer $NVIDIA_API_KEY` |
| Request | `{"model", "messages": [{role, content}], "temperature", "max_tokens"}` |
| Response | OpenAI-shaped `choices[0].message.content` |
| Models | configurable via `NIM_MODEL` (default `meta/llama-3.3-70b-instruct`) |
| Rate limits | provider-enforced; client treats HTTP 429 as `NimRateLimited` with bounded retry |
| Structured output | enforced prompt-side ("JSON only") + tolerant extractor + schema-key validation; malformed output -> typed `NimInvalidJSON`, never interpreted |
| Embeddings (originality) | optional `embeddings()` capability hook on the client; when absent the deterministic trigram path is used |
| Status | **VERIFIED (client shape per current docs); LIVE CALLS NOT EXECUTED - no key was provided at build time.** All pipelines degrade to explicit BLOCKED without `NVIDIA_API_KEY`. |

## 2. Buffer

| Property | Value |
|---|---|
| Current API | GraphQL (`POST {base}/api/graphql`, Bearer token) per developers.buffer.com; legacy REST (`api.bufferapp.com/1/`) is deprecated, sunset 2027-02-01 (verified: responses include the official deprecation header) |
| Operations used | identity check (`account`), channel discovery (`channels`), scheduled post creation mutation (single + multi-item threads), update retrieval for reconciliation, normalized statistics query |
| Platform keys | `x` and `threads` services discovered from `channels` |
| Error taxonomy | 401/403 -> `BufferAuthError` (no retry); 429/5xx -> `BufferUnavailable` (bounded retry); 4xx -> `BufferValidationError`; GraphQL `errors[].extensions.code == UNAUTHENTICATED` -> `BufferAuthError` |
| Status | **BLOCKED at build time**: the provided token is rejected by Buffer's own identity endpoint (`401 "Access token is not valid"`, verified on 2026-09-07; GraphQL introspection route also 404'd - path per docs). The token was still stored as the `BUFFER_API_KEY` secret as requested. Publishing fails safely and audibly until a valid token is configured. No operations were fabricated. |

## 3. X (via Buffer)

| Property | Value (config/platforms.yml) |
|---|---|
| Post length | 280 weighted characters; URLs count as 23 (t.co); CJK/Hangul/emoji weight 2 |
| Threads | reply chains; operational cap 18 posts, 5600 total weighted chars (configurable) |
| Media | supported by platform; text-only in v1 |
| Status | VERIFIED (documented model implemented deterministically in `src/validation/`); live posting NOT EXECUTED (Buffer token blocked) |

## 4. Threads (via Buffer)

| Property | Value |
|---|---|
| Post length | 500 characters (plain count) |
| Links/hashtags | links typically reduce reach -> WARN (not hard fail); hashtag guidance enforced as WARN |
| Threads | reply-chain model; cap 50 posts / 8000 chars (configurable) |
| Status | VERIFIED (documented limits); live posting NOT EXECUTED (Buffer token blocked) |

## 5. GitHub (review surface + Actions)

| Property | Value |
|---|---|
| Endpoints | repos/{repo}/issues (+ comments, labels) - verified live during setup |
| Command trigger | `issue_comment` created; comment body passed via env only (never shell interpolation) - per GitHub's script-injection guidance |
| Auth | Actions `github.token` (issues: write) at runtime |
| Status | VERIFIED (REST paths exercised with PAT during setup); command workflow NOT EXECUTED live yet |

## 6. Discord

| Property | Value |
|---|---|
| Endpoint | webhook URL `POST {webhook}` JSON `{content}` / `{embeds}` |
| Limits | 2000 chars per message (client chunks at 1900); 429/5xx treated transient |
| Status | VERIFIED (shape per current docs); NOT EXECUTED live - no webhook URL provided |

## 7. PostgreSQL / Supabase

| Property | Value |
|---|---|
| Schema | `migrations/001_init.sql` (23 tables, unique idempotency keys, append-only snapshots) |
| Connection | `SUPABASE_DB_URL` / `DATABASE_URL` (postgres://) via psycopg2; SQLite mirror for sandbox/tests |
| Status | VERIFIED (schema applied + exercised on SQLite mirror); Supabase deployment NOT EXECUTED - no credentials provided |
