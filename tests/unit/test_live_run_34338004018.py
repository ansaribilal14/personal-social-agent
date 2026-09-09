"""Regression tests for live engine run 34338004018 (2026-09-09).

That run exposed three problems:

1. The antislop substantive-ratio gate flagged the MECHANISM and ACTION
   sentences of a good post ("That voltage punches through paint. / Wash
   charge off before it arcs.") as filler - the lexicon could not see
   imperatives or demonstrative anaphora, so the best draft of the run was
   blocked and the user saw nothing.
2. The same story ("orthogonal genetic codes") produced TWO posts in one
   run - GenerationPipeline had no in-run story dedup.
3. When every draft was hard-blocked, the review stage went silent - no
   card, no digest - so the run looked dead from Discord.
"""
import json

import pytest

from src.state.machine import State
from tests.conftest import MockNIM

RAINDROP_POST = (
    "Raindrops are tiny lightning bolts, and they\u2019re corroding cars, "
    "study finds.\n\n"
    "That voltage punches through paint. The study measured 100-500 volts "
    "across millimeter gaps.\n\n"
    "Your clear coat is the insulator they blow through. Wash charge off "
    "before it arcs."
)

WHY = {"why_interesting": "x" * 25, "why_now": "x" * 25,
       "why_this_angle": "x" * 25, "why_this_platform": "single on x" + "x" * 15,
       "why_this_account": "x" * 25}


# ------------------------------------------------------- 1. substance gate
def test_substantive_ratio_counts_mechanism_and_action():
    """The blocked raindrop post must now clear the 0.5 floor: 'That voltage
    punches through paint.' is demonstrative anaphora on a substantive
    sentence; 'Wash charge off before it arcs.' is an imperative."""
    from src.critics.antislop import substantive_ratio
    ratio = substantive_ratio(RAINDROP_POST)
    assert ratio >= 0.5, f"mechanism/action post still flagged: {ratio:.2f}"


def test_substantive_ratio_imperative_alone_counts():
    from src.critics.antislop import substantive_ratio
    post = ("The study measured 100-500 volts across millimeter gaps.\n\n"
            "Wash charge off before it arcs.")
    assert substantive_ratio(post) >= 0.5


def test_substantive_ratio_demonstrative_slop_still_fails():
    """The anaphora rule must not open the door to 'That's the real win.'
    / 'This changes everything.' - no referent, no substance."""
    from src.critics.antislop import substantive_ratio
    post = ("Nice day out today. That is something. This changes everything "
            "for you. Hope you agree with me here.")
    assert substantive_ratio(post) < 0.5


# ------------------------------------------------- 2. in-run story dedup
def _save_idea(repo, statement, angle, source_ids=None, score=90):
    return repo.save_idea(statement, "AI", {"novelty": 80}, score, WHY,
                          source_item_ids=source_ids or [], angle=angle)


def test_generation_skips_idea_sharing_a_source_article(repo):
    """Two winning ideas from the SAME article -> one post, not two."""
    from src.pipeline.production import GenerationPipeline
    r1 = repo.save_research_item({
        "title": "Genetic codes story", "source_url": "https://e.com/a",
        "publisher": "p", "published_at": "Mon, 01 Sep 2026 00:00:00 GMT",
        "summary": "s", "category": "current_development",
        "freshness": "recent"})
    _save_idea(repo, "Parallel genetic codes expand design space",
               "Parallel genetic codes expand the design space", [r1])
    _save_idea(repo, "Two genetic codes running in one cell",
               "Researchers run two genetic codes in one cell", [r1])

    nim = MockNIM(responses={"structured_default": {
        "body": "Concrete body with 100-500 volts.", "thread_posts": None,
        "claims": []}})
    result = GenerationPipeline(repo, nim).run()

    assert len(result["drafts"]) == 1, "same story must draft once per run"


def test_generation_skips_near_identical_angle_without_shared_source(repo):
    """No shared source id, but the angles are the same story reworded ->
    still deduped (jaccard >= 0.45 on content tokens)."""
    from src.pipeline.production import GenerationPipeline
    a = ("Researchers demonstrated two genetic codes running in the same "
         "cell using orthogonal tRNA synthetase pairs")
    b = ("Researchers demonstrated two genetic codes running in the same "
         "cell with orthogonal tRNA synthetase pairs")
    _save_idea(repo, "statement a", a, [11])
    _save_idea(repo, "statement b", b, [22])

    nim = MockNIM(responses={"structured_default": {
        "body": "Concrete body with 100-500 volts.", "thread_posts": None,
        "claims": []}})
    result = GenerationPipeline(repo, nim).run()

    assert len(result["drafts"]) == 1


def test_generation_still_fills_top_n_from_distinct_stories(repo):
    """Dedup must not starve the run: distinct stories still produce
    top_n drafts even when the pool has duplicates first."""
    from src.pipeline.production import GenerationPipeline
    r1 = repo.save_research_item({
        "title": "Story A", "source_url": "https://e.com/a",
        "publisher": "p", "published_at": "Mon, 01 Sep 2026 00:00:00 GMT",
        "summary": "s", "category": "current_development",
        "freshness": "recent"})
    r2 = repo.save_research_item({
        "title": "Story B", "source_url": "https://e.com/b",
        "publisher": "p", "published_at": "Mon, 01 Sep 2026 00:00:00 GMT",
        "summary": "s", "category": "current_development",
        "freshness": "recent"})
    r3 = repo.save_research_item({
        "title": "Story C", "source_url": "https://e.com/c",
        "publisher": "p", "published_at": "Mon, 01 Sep 2026 00:00:00 GMT",
        "summary": "s", "category": "current_development",
        "freshness": "recent"})
    _save_idea(repo, "story A v1", "Alpha story about batteries", [r1], score=95)
    _save_idea(repo, "story A v2", "Alpha story about battery cells", [r1], score=94)
    _save_idea(repo, "story B", "Beta story about desalination", [r2], score=93)
    _save_idea(repo, "story C", "Gamma story about fusion magnets", [r3], score=92)

    nim = MockNIM(responses={"structured_default": {
        "body": "Concrete body with 100-500 volts.", "thread_posts": None,
        "claims": []}})
    result = GenerationPipeline(repo, nim).run(limit=3)

    assert len(result["drafts"]) == 3, "distinct stories must fill top_n"


# --------------------------------------------------- 3. blocked-run digest
@pytest.fixture()
def blocked_post(repo):
    idea_id = repo.save_idea("bare statement", "AI", {}, 90, WHY)
    post_id = repo.create_post("x", "single", "AI", idea_id, "some angle")
    repo.add_version(post_id, "Body.", None, {}, {}, [])
    for st in (State.CANDIDATE, State.RESEARCHED, State.STRATEGIZED,
               State.DRAFTED, State.QUALITY_REVIEW, State.QUALITY_FAILED,
               State.BLOCKED):
        repo.move_state(post_id, st, actor="test")
    repo.db.execute(
        "INSERT INTO quality_evaluations (post_id, version, critic, passed, "
        "score, issues, evidence, recommended_changes, engine) "
        "VALUES (:p, 1, 'antislop', 0, 45, :i, '[]', '[]', 'deterministic')",
        {"p": post_id, "i": json.dumps(["substantive sentence ratio 0.40 < 0.5"])})
    return repo.get_post(post_id)


def test_review_run_sends_digest_when_all_drafts_blocked(repo, blocked_post,
                                                         monkeypatch):
    from src.pipeline.ops import ReviewPipeline
    import src.notifications.discord_review as dr

    calls = []
    monkeypatch.setattr(dr, "notify_quality_digest",
                        lambda r, entries: calls.append(entries) or True)
    pipeline = ReviewPipeline(repo, nim=MockNIM(), discord_client=object())
    out = pipeline.run()

    assert out["review_cards_sent"] == 0
    assert len(calls) == 1, "a fully-blocked run must send exactly one digest"
    entry = calls[0][0]
    assert entry["post_uid"] == blocked_post["post_uid"]
    assert any("substantive sentence ratio" in i for i in entry["issues"])


def test_review_digest_fires_once_per_run(repo, blocked_post, monkeypatch):
    from src.pipeline.ops import ReviewPipeline
    import src.notifications.discord_review as dr

    calls = []
    monkeypatch.setattr(dr, "notify_quality_digest",
                        lambda r, entries: calls.append(entries) or True)
    pipeline = ReviewPipeline(repo, nim=MockNIM(), discord_client=object())
    pipeline.run()
    pipeline.run()  # same run instance -> same run_id -> guard must hold

    assert len(calls) == 1, "digest must be deduped per workflow run"


def test_review_digest_silent_when_no_blocked_posts(repo, monkeypatch):
    from src.pipeline.ops import ReviewPipeline
    import src.notifications.discord_review as dr

    calls = []
    monkeypatch.setattr(dr, "notify_quality_digest",
                        lambda r, entries: calls.append(entries) or True)
    ReviewPipeline(repo, nim=MockNIM(), discord_client=object()).run()
    assert calls == [], "no blocked posts -> no digest"
