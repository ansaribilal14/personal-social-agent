# API COMPATIBILITY

Verified against the LIVE production services on 2026-09-07 with the
customer-provided keys. Anything not exercised live is marked explicitly -
nothing is invented.

## 1. NVIDIA NIM

| Property | Value |
|---|---|
| Endpoint | `POST https://integrate.api.nvidia.com/v1/chat/completions` (OpenAI-compatible) |
| Auth | `Authorization: Bearer $NVIDIA_API_KEY` |
| Request | `{"model", "messages": [{role, content}], "temperature", "max_tokens", "chat_template_kwargs"}` |
| Response | OpenAI-shaped `choices[0].message.content` |
| Model | default `nvidia/nemotron-3.5-lightning-30b-a3b` (configurable via `NIM_MODEL`) |
| Model EOL note | `meta/llama-3.3-70b-instruct` reached end-of-life on NIM 2026-08-26 (HTTP 410) - do not use |
| Reasoning models | nemotron-3 family defaults to `chat_template_kwargs: {thinking: false}` (live-verified: clean final answers); with `NIM_THINKING=true` reasoning stays on and `<think>` blocks are stripped defensively |
| Rate limits | provider-enforced; client treats HTTP 429 as `NimRateLimited` with bounded retry |
| Structured output | enforced prompt-side ("JSON only") + tolerant extractor + schema-key validation; malformed output -> typed `NimInvalidJSON`, never interpreted |
| Embeddings (originality) | optional `embeddings()` capability hook on the client; when absent the deterministic trigram path is used |
| Status | **LIVE-VERIFIED 2026-09-07**: real key accepted; clean tweet generated end-to-end through the production client with zero reasoning leakage |

## 2. Buffer

| Property | Value |
|---|---|
| Current API | **GraphQL `POST https://api.buffer.com/graphql`** (Bearer token) - live-verified 2026-09-07; legacy REST (`api.bufferapp.com/1/`) is deprecated, sunset 2027-02-01, and rejects new tokens with 401 "Public API tokens are not accepted for REST API access" |
| Identity | `account { id name email timezone organizations { id name } }` - live-verified |
| Channels | `channels(input: { organizationId })` (organizationId REQUIRED) - live-verified: twitter + threads + instagram channels discovered |
| Publish mutation | `createPost(input: CreatePostInput!)` with REQUIRED `mode: ShareMode!` (addToQueue / customScheduled / shareNext / shareNow) and `schedulingType: SchedulingType!` (automatic / notification); payload is a UNION: `PostActionSuccess \| MutationError` (inline fragments required) - live-verified with a draft probe (created + deleted) |
| Threads | the schema has no reply-chain primitive; threads are published as N ordered posts, first at the requested slot, then +`BUFFER_THREAD_GAP_SECONDS` (default 90s) each |
| Reconciliation | `post(input: { id }) { id status dueAt sentAt }`; status enum: draft / error / needs_approval / scheduled / sending / sent - live-verified |
| Metrics | `post(input: { id }) { metrics { name value } }` - live-probed shape: Reactions / Comments / Eng. Rate / Reposts / Impressions / Clicks, normalized to snake_case |
| Platform keys | internal `x` mapped to Buffer service `twitter`; `threads` passthrough; disconnected channels are skipped |
| Error taxonomy | 401/403 -> `BufferAuthError` (no retry); 429/5xx -> `BufferUnavailable` (bounded retry); 4xx -> `BufferValidationError`; GraphQL `errors[].extensions.code == UNAUTHENTICATED` -> `BufferAuthError`; MutationError fragments -> `BufferValidationError` |
| Status | **LIVE-VERIFIED 2026-09-07**: token accepted; account + 3 channels read; draft post created (saveToDraft) and deleted via the production client. Real publishing stays fail-safe OFF behind the kill switch until explicitly enabled. |

## 3. X (via Buffer)

| Property | Value (config/platforms.yml) |
|---|---|
| Post length | 280 weighted characters; URLs count as 23 (t.co); CJK/Hangul/emoji weight 2 |
| Threads | reply chains; operational cap 18 posts, 5600 total weighted chars (configurable); published via Buffer as ordered staggered posts (see section 2) |
| Media | supported by platform; text-only in v1 |
| Status | VERIFIED (documented model implemented deterministically in `src/validation/`); Buffer channel LIVE (`twitter`: connected, not disconnected) |

## 4. Threads (via Buffer)

| Property | Value |
|---|---|
| Post length | 500 characters (plain count) |
| Links/hashtags | links typically reduce reach -> WARN (not hard fail); hashtag guidance enforced as WARN |
| Threads | reply-chain model; cap 50 posts / 8000 chars (configurable) |
| Status | VERIFIED (documented limits); Buffer channel LIVE (`threads`: connected, not disconnected) |

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
| Delivery modes | (1) webhook URL `POST {webhook}` JSON `{content}` / `{embeds}`; (2) **bot token** (`Authorization: Bot $DISCORD_BOT_TOKEN`) posting to purpose-routed channels via `POST /channels/{id}/messages` (API v10) |
| Interactive bot | discord.py 2.x slash commands: ping, status, queue, show, approve, reject, iterate, killswitch, help - syncs instantly to `DISCORD_GUILD_ID` |
| Authorization | `DISCORD_AUTHORIZED_USERS` (names/IDs) -> fallback config authorized_users; guild owner accepted unless `DISCORD_ALLOW_GUILD_OWNER=false`; actions recorded as actor `discord:<name>` |
| Limits | 2000 chars per message (client chunks at 1900); 429/5xx treated transient |
| Status | **LIVE-VERIFIED 2026-09-07**: token accepted (`Social Automation Bot`, id 1546327280169525350); guild "Personal Social OS" reachable with review/alerts/analytics/strategy/errors channels; bot logged in and synced 9 slash commands |

## 7. PostgreSQL / Supabase

| Property | Value |
|---|---|
| Schema | `migrations/001_init.sql` (23 tables, unique idempotency keys, append-only snapshots) |
| Connection | `SUPABASE_DB_URL` / `DATABASE_URL` (postgres://) via psycopg2; SQLite mirror for sandbox/tests |
| Status | VERIFIED (schema applied + exercised on SQLite mirror); Supabase deployment NOT EXECUTED - no credentials provided |
