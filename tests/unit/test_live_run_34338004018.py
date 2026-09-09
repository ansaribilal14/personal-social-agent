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
from tests.conftest import GOOD_IDEA, MockNIM

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


# ------------------- run 34342374809: recycled stories + press-release slop
def test_antislop_flags_press_release_feature_enumeration():
    """Run 34342374809 shipped 'It supports 24 languages, light/dark modes,
    React inspection, ...' at score 92 - a feature list is ad copy."""
    from src.critics.antislop import AntiSlopEngine
    body = ("T3rnel Browser extension solves authentication friction.\n\n"
            "It supports 24 languages, light/dark modes, React inspection, "
            "cache/hard reload, and dark-mode PDF export.")
    r = AntiSlopEngine().check_text(body)
    assert not r.passed
    assert any("feature enumeration" in i for i in r.issues)
    # a plain sentence with commas must NOT trip it
    ok = AntiSlopEngine().check_text(
        "The study measured 100-500 volts across millimeter gaps in rain.")
    assert ok.passed, ok.issues


def test_generation_skips_idea_that_already_has_a_post(repo):
    """Idea 42 recycling: a CANDIDATE idea whose story already produced a
    review-queue post must not be drafted again."""
    from src.pipeline.production import GenerationPipeline
    idea_id = _save_idea(repo, "Spi-Fly sparse coding story",
                         "Spi-Fly sparse coding keeps old scents")
    post_id = repo.create_post("x", "single", "AI", idea_id, "its angle")
    for st in (State.CANDIDATE, State.RESEARCHED, State.STRATEGIZED,
               State.DRAFTED, State.QUALITY_REVIEW, State.QUALITY_PASSED,
               State.EDITORIALLY_RANKED, State.WAITING_APPROVAL):
        repo.move_state(post_id, st, actor="test")
    # simulate the recycle: the idea row is somehow CANDIDATE again
    repo.update_idea_status(idea_id, "CANDIDATE")

    nim = MockNIM(responses={"structured_default": {
        "body": "Body.", "thread_posts": None, "claims": []}})
    result = GenerationPipeline(repo, nim).run()
    assert result["drafts"] == [], "recycled idea must not draft again"


def test_generation_skips_story_reworded_under_new_idea_row(repo):
    """Same story reworded as a NEW idea row (different id) must also be
    caught - trigram cosine vs the existing post's angle."""
    from src.pipeline.production import GenerationPipeline
    idea_id = _save_idea(repo, "old row", "Fruit fly brain sparse coding "
                                     "keeps old scents while learning new ones")
    post_id = repo.create_post("x", "single", "AI", idea_id,
                               "Fruit fly brain sparse coding keeps old "
                               "scents while learning new ones")
    for st in (State.CANDIDATE, State.RESEARCHED, State.STRATEGIZED,
               State.DRAFTED, State.QUALITY_REVIEW, State.QUALITY_PASSED,
               State.EDITORIALLY_RANKED, State.WAITING_APPROVAL):
        repo.move_state(post_id, st, actor="test")
    _save_idea(repo, "new row", "Fruit fly brain sparse coding retains old "
                                "scents while new ones are learned", score=99)

    nim = MockNIM(responses={"structured_default": {
        "body": "Body.", "thread_posts": None, "claims": []}})
    result = GenerationPipeline(repo, nim).run()
    assert result["drafts"] == [], "reworded same-story idea must be skipped"


def test_top_ideas_expire_after_three_days(repo):
    db = repo.db
    idea_id = _save_idea(repo, "stale story", "stale angle")
    db.execute("UPDATE ideas SET created_at = datetime('now', '-4 days') "
               "WHERE id=:i", {"i": idea_id})
    assert repo.top_ideas(limit=5) == []
    db.execute("UPDATE ideas SET created_at = datetime('now') "
               "WHERE id=:i", {"i": idea_id})
    assert len(repo.top_ideas(limit=5)) == 1


def test_promo_source_caps_idea_below_promotion_threshold(repo):
    """A 'Show HN' launch must never auto-promote, whatever the strategist
    scores it (the T3rnel feature list topped the ranking at 92)."""
    from src.pipeline.production import IdeaDiscoveryPipeline
    rid = repo.save_research_item({
        "title": "Show HN: T3rnel Browser - drive the browser you're signed into",
        "source_url": "https://t3ratech.github.io/t3rnel-browser-plugin/",
        "publisher": "HN", "published_at": "Mon, 08 Sep 2026 00:00:00 GMT",
        "summary": "s", "category": "current_development",
        "freshness": "recent"})
    promo_idea = dict(GOOD_IDEA)
    promo_idea["source_item_ids"] = [rid]
    nim = MockNIM(responses={"structured_default": {"ideas": [promo_idea]}})
    IdeaDiscoveryPipeline(repo, nim).run()
    row = repo.db.query("SELECT score FROM ideas ORDER BY id DESC LIMIT 1")[0]
    assert row["score"] <= 69, "promo source must be capped below 70"


def test_quality_block_discards_idea(repo):
    """When quality hard-blocks a post, its idea must be retired so the
    story stops resurfacing in later runs."""
    from src.claims.ledger import ClaimLedger
    from src.critics.quality import QualityEngine
    from src.generation.writer import Writer
    from src.pipeline.production import QualityPipeline
    from src.voice.profile import VoiceProfile

    idea_id = _save_idea(repo, "story that will fail", "angle that will fail")
    post_id = repo.create_post("x", "single", "AI", idea_id, "failing angle")
    repo.add_version(post_id, "Body one.", None, {}, {}, [])
    repo.add_version(post_id, "Body two.", None, {}, {}, [])
    repo.add_version(post_id, "Body three.", None, {}, {}, [])
    for st in (State.CANDIDATE, State.RESEARCHED, State.STRATEGIZED,
               State.DRAFTED):
        repo.move_state(post_id, st, actor="test")

    pipeline = QualityPipeline(repo, MockNIM())
    outcome = pipeline._process_post(
        QualityEngine(), ClaimLedger(), Writer(MockNIM(), repo, VoiceProfile()),
        repo.get_post(post_id), max_cycles=0)

    assert outcome["decision"] == "BLOCKED"
    row = repo.db.query("SELECT status FROM ideas WHERE id=:i",
                        {"i": idea_id})[0]
    assert row["status"] == "DISCARDED", "blocked story's idea must be retired"
