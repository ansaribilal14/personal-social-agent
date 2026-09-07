# EDITORIAL SYSTEM

## 1. Editorial principle

This is not "AI writes posts automatically". It is a personal editorial
operating system: AI researches, synthesizes, drafts, criticizes, iterates and
analyzes; the human retains final publishing authority over the exact version.

Optimization order (spec 46): user voice > editorial quality > accuracy >
originality > audience response. Engagement is never the sole objective.

## 2. Idea model

IDEA (the concept) -> ANGLE (the specific editorial interpretation) ->
PLATFORM VARIANT (x/threads x single/thread). Platform variants are never
treated as independent ideas - this is what makes duplication detection and
analytics meaningful.

## 3. The "Why me?" test

Every candidate must answer, substantively (min 20 chars each, enforced in
code): why interesting, why now, why this angle, why this platform, why this
account. Weak answers -> the idea is HELD, not promoted. A generic news bot
fails this test by design.

## 4. Content pillars

Config-driven (`config/strategy.yml`). Placeholder pillars ship as defaults
(AI 30, Technology 20, Building 20, Opinions 15, Discoveries 10, Personal 5).
Edit the file - nothing is hardcoded.

## 5. Scoring

Component scores (novelty, interestingness, relevance, personal_fit,
discussion_value, factual_confidence, content_potential, timeliness) are
model-assisted DATA. The final score is a weighted sum COMPUTED IN CODE from
`scoring_weights`. Editorial ranking admits only top candidates above
`min_editorial_score` into human review - mediocre-but-valid posts never
reach you.

## 6. Voice

STABLE voice lives in `config/voice.yml` (tone, sentence length, vocabulary,
humor, directness, technical depth, opinion strength, punctuation, emoji and
hashtag policy, hook/ending rules, forbidden patterns). It is never modified
by interactions.

LEARNED preferences live in the DB and surface only after
`min_signals_for_promotion` (3) consistent APPROVED signals. Rejected content
never becomes a positive style example. Approved-immediately vs
iterated-then-approved are tracked as separate signal paths.

## 7. Originality

Six duplication levels: exact, near, semantic (trigram cosine offline;
embeddings hook for NIM in production), hook structure, plus critic-level
angle/opinion checks. Thresholds in `config/quality.yml` - measured
separation: reworded duplicate ~0.50 vs distinct content ~0.05 trigram
cosine. Changing the wording cannot smuggle an old post through.

## 8. Anti-slop

Layer 1 (deterministic): banned phrases, emoji/exclamation/em-dash caps,
engagement bait, fake urgency, generic openers, fabricated-experience checks,
substantive-sentence ratio. Layer 2 (NIM critic): "does this sound like a
real person with a reason to post?" Both must pass - it is a hard gate.

## 9. Claims

Every factual post carries a claim ledger (FACT / OPINION / INFERENCE /
SPECULATION / PERSONAL_EXPERIENCE). FACTs require a URL present in stored
research; unverified FACTs trigger REWRITE/REMOVE and the fact critic hard-
fails the candidate. Inference is never presented as fact; speculation is
labeled; experiences are never fabricated. Provenance: research_item -> claim
-> post_version snapshot.

## 10. Threads

Never a chopped paragraph. Structure: hook -> development -> argument ->
evidence -> conclusion. Coherence critic: every post adds something, no
near-duplicate posts, ending must conclude. Every post independently passes
platform validation; the thread also passes thread-level validation
(compression check included).

## 11. Human review surface

Discord: the review card (ID, platform, format, pillar, editorial score, why
this exists, full content, per-critic quality, recommended slot, issue link).
GitHub issue: the authoritative command surface - /approve, /reject,
/iterate. The DB remains the single source of truth; Discord and GitHub are
presentations of DB state.

## 12. Learning loop

Tracked paths: approved immediately / iterated->approved / iterated->rejected
/ rejected. Dimensions: hooks, length, tone, explanation tolerance, CTAs,
emoji, topics, formats. Analytics may RECOMMEND strategy changes with
evidence and sample size; analytics can never silently change strategy.
