# TESTING

Run: `python -m pytest tests/ -v` (119 tests, ~7s, no network, no secrets).

The GitHub Actions `maintenance` workflow runs the same suite on every push
and on demand (`workflow_dispatch` with `run_tests=true`).

## Coverage by spec section 54

### Unit tests (tests/unit)
- **test_state_machine.py** - EVERY valid transition succeeds; EVERY invalid
  transition raises; APPROVED reachable only via WAITING_APPROVAL; PUBLISHED
  only via PUBLISHING; terminal states; repository refuses invalid moves.
- **test_validators.py** - X weighted length (ASCII/CJK/emoji), URL=23,
  single/thread limits, empty posts, Threads 500 + warnings.
- **test_similarity.py** - exact, near, semantic (trigram + embeddings hook),
  hook duplication, thread flattening, no false positives on new content.
- **test_security.py** - command parsing, authorization, bounded instructions,
  control-char stripping, shell-injection-as-text, injection scanning, data
  wrapping, kill switch default/toggle/unreadable-DB, redaction.
- **test_quality.py** - anti-slop (banned phrases, bait, urgency, emoji),
  substantive ratio, fact hard gate, hard-gate FAIL overrides scores,
  code-computed decision, voice/hook critics.
- **test_claims_scoring.py** - FACT verification rules, opinion/speculation
  handling, decision actions, idea score computed from components in code.

### Integration tests (tests/integration)
- **test_outbox.py** - idempotent outbox creation, publication uniqueness,
  version identity, refusal on unapproved/stale/rejected state.
- **test_scheduling.py** - window compliance, same-platform 180min gap,
  cross-platform 45min gap, the 3-close-posts cascade scenario, distance from
  recent publications, daily caps, Asia/Kolkata timezone.
- **test_publish.py** - full publish via mock Buffer, retry with SAME content,
  retry exhaustion -> PUBLISH_FAILED, auth errors not retried, no publish
  without approval even with the switch on.
- **test_review.py** - approve/reject/iterate via issue comments, replay
  refusal, unauthorized actors, version immutability.

### Failure tests (tests/failure/test_failure_modes.py)
NIM unavailable/invalid-JSON/timeout/rate-limit/unreachable classification,
DB outage stops pipelines without publishing, malformed comments, kill switch
blocks schedule, stale version refusal, prompt versions recorded, snapshots
append-only, JSON fallback parser, empty responses.

### Red-team tests (tests/redteam/test_redteam.py)
Publish-without-approval (forged outbox), old-version approval attack, comment
replay attack, shell injection via comment (stored as inert text), prompt
injection via research, kill-switch toggle race, concurrent duplicate publish
(atomic claim - exactly one Buffer call), concurrent /iterate (atomic version
allocation), platform-limit bypass, unverified-claim bypass, GitHub outage
safety, SQLi via post_uid (parametrized queries).

### End-to-end (tests/e2e/test_full_lifecycle.py)
Research fixture -> idea -> draft -> critics -> validation -> GitHub issue ->
iterate -> approve exact version -> schedule (kill switch verified both
states) -> mock Buffer -> publication record -> duplicate publish blocked ->
metric snapshots -> analytics -> learning signal -> full audit trail.

## Mocks (tests/conftest.py)

MockNIM (scripted structured outputs + failure injection), MockBuffer
(recorded calls + `fail_first` outage simulation), MockGitHub (in-memory
issues), MockDiscord. No test can reach a live API or publish anything.

## Honest limits

- Live Buffer publishing: NOT EXECUTED (token rejected at build time).
- Live NIM calls: NOT EXECUTED (no key at build time).
- Supabase Postgres deployment: NOT EXECUTED (schema applied on the SQLite
  dialect mirror; SQL is kept Postgres-compatible).
