# FINAL AUDIT

Date: 2026-09-07. Second, independent pass over the finished build
(fresh-engineer perspective), per spec sections 68-69.

## Implemented and verified

| Capability | Evidence |
|---|---|
| Explicit 20-state machine, no approval bypass | tests/unit/test_state_machine.py (every valid + invalid transition) |
| Deterministic X/Threads validators | tests/unit/test_validators.py |
| Multi-level originality engine | tests/unit/test_similarity.py |
| Anti-slop engine (deterministic layer) | tests/unit/test_quality.py |
| Code-computed quality decision with hard gates | tests/unit/test_quality.py |
| Claim ledger (FACT requires source) | tests/unit/test_claims_scoring.py |
| Idea scoring computed in code | tests/unit/test_claims_scoring.py |
| Command authz, replay protection, version binding | tests/integration/test_review.py, tests/redteam |
| Idempotent outbox + atomic claim + unique publications | tests/integration/test_outbox.py, tests/redteam (concurrent) |
| Scheduling windows/collisions/caps in Asia/Kolkata | tests/integration/test_scheduling.py |
| Bounded retry with SAME content, failure alerts | tests/integration/test_publish.py |
| Kill switch (re-read at scheduling + publishing) | tests/unit/test_security.py, tests/redteam toggle race |
| Prompt-injection defense | tests/unit/test_security.py, tests/redteam |
| Secret redaction | tests/unit/test_security.py |
| Snapshot append-only + sample-size-guarded analytics | tests/failure, tests/e2e |
| Full deterministic lifecycle | tests/e2e/test_full_lifecycle.py |
| 12 workflows, injection-safe env passing, least privilege | YAML validated; scripts/review_comment_handler.py + run_pipeline.py |
| 119 tests green | `python -m pytest tests/` -> 119 passed |

## Audit questions (spec 68) - answered by tests

- Can this publish without me? **No** - approval verification + state machine.
- Can an old version be published? **No** - stale-version refusal (tested).
- Can the same post publish twice? **No** - unique keys + atomic claim (tested, concurrent).
- Can an attacker trigger approval? **No** - allowlist + replay guard (tested).
- Can research content inject instructions? **Flagged + excluded** (tested).
- Can an LLM fabricate a claim? **Fact gate blocks unverified FACTs** (tested).
- Can platform limits be exceeded? **No** - deterministic validators (tested).
- Can Buffer failures duplicate posts? **No** - claim + unique keys (tested).
- Can a workflow retry corrupt state? **No** - idempotent stages + transitions (tested).
- Can analytics silently change strategy? **No** - reports only, by construction.
- Can the kill switch be bypassed? **No** - re-read at publish; toggle race tested.
- Can Discord/GitHub state diverge? **They are presentations; DB is truth.**
- Can two iterations race? **Atomic version allocation + concurrency groups** (tested).
- Can a stale approval publish? **No** (tested).
- Can secrets leak into logs? **Redaction layer; no secret values in repo** (tested).

## Verified vs blocked vs not executed

**VERIFIED**: GitHub token/REST; all 119 tests; workflow YAML; CLI stages;
sandbox DB round-trip; Buffer endpoint behavior with an invalid token
(graceful classification).

**BLOCKED (external, not our code)**: Buffer token rejected (401) - publishing
fails safely until a fresh token is set; NVIDIA NIM key not provided -
AI stages report BLOCKED; Discord webhook not provided - notifications
skipped+logged; Supabase credentials not provided - schema ready in
migrations/.

**NOT EXECUTED (requires the blocked credentials)**: live Buffer publication;
live NIM inference; live Discord delivery; a live end-to-end GitHub
issue-comment round-trip (the code path is fully covered by mock tests and
the workflow YAML is validated; first real /approve should be observed once
on a test issue).

**NOT APPLICABLE**: media/image posting (text-only v1); multi-user authz
(single-owner personal system).

## Known limitations

1. Threads platform limits implemented per documented 500-char model; exact
   reach-suppression behavior of links is heuristic (WARN, not fact).
2. X thread cap is a conservative operational 18 posts (configurable), not a
   platform hard limit.
3. Semantic duplication offline uses trigram cosine (embeddings hook ready);
   with NIM embeddings enabled, recall improves further.
4. Buffer GraphQL mutation names are per current developer docs; because the
   live token was rejected, the first real publish must be observed once with
   a test post (the adapter classifies any schema drift as a typed error
   rather than failing silently).
5. The weekly analyst report provides recommendations only after
   min_sample_size (4) posts with metrics - intentionally conservative.

## Release decision

The repository is production-ready for research/drafting/criticism/review
today (only GitHub + DB secrets required). Scheduling/publishing activates
when: valid BUFFER_API_KEY + enabled kill switch + DATABASE_URL. All safety
gates are test-verified and fail closed.
