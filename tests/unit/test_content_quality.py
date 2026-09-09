"""Content-quality overhaul tests (user feedback: posts were stale, generic,
valueless). Covers: anti-slop v2 detectors (meta-labels, not-X-it's-Y,
press-release cadence, abstract soup, concrete anchors), research freshness
filtering, writer v4 prompt contracts, voice v2 block, and the bounded
auto-revision loop in the quality stage."""
from __future__ import annotations

from src.critics.antislop import AntiSlopEngine, concrete_anchors
from src.state.machine import State
from tests.conftest import MockNIM


ANCHORED_POST = ("Agent frameworks ship 40 tools by default.\n\n"
                 "Production agents use 5.\n\n"
                 "Tool selection errors compound faster than capability gaps, "
                 "so the default is the bug.")


# --------------------------------------------------------------- antislop v2

def test_meta_label_fails():
    r = AntiSlopEngine().check_text(
        "Child-monitoring apps trade privacy for peace of mind. As an opinion: "
        "children learn surveillance is the default state of intimacy.")
    assert not r.passed
    assert any("meta-label" in i for i in r.issues)


def test_not_x_its_y_variants_fail():
    cases = [
        "That isn't a tweak—it's a new engineering palette for biology.",
        "Memory-optimized architectures aren't a luxury—they're the critical "
        "path for real-time AI deployment.",
        "It's not just a filter, it's a whole new workflow for the team.",
    ]
    for text in cases:
        r = AntiSlopEngine().check_text(text)
        assert not r.passed, text
        assert any("not X, it's Y" in i for i in r.issues)


def test_press_release_cliches_fail():
    r = AntiSlopEngine().check_text(
        "The fruit-fly connectome opens a new design space and moves the field "
        "toward brain-like systems.")
    assert not r.passed
    assert any("press-release" in i for i in r.issues)


def test_abstract_noun_soup_fails():
    r = AntiSlopEngine().check_text(
        "The digital landscape is becoming an ecosystem where every narrative "
        "journey reframes the paradigm of the modern data framework.")
    assert not r.passed
    assert any("abstract noun soup" in i for i in r.issues)


def test_missing_concrete_anchor_fails():
    r = AntiSlopEngine().check_text(
        "Sparse coding suggests models can retain patterns without overwriting "
        "old ones, trading capacity for permanence in resilient systems.")
    assert not r.passed
    assert any("concrete anchor" in i for i in r.issues)


def test_anchored_post_passes_all_gates():
    r = AntiSlopEngine().check_text(ANCHORED_POST)
    assert r.passed, r.issues


def test_concrete_anchors_extracts_numbers_quotes_names():
    anchors = concrete_anchors(
        'Postgres 16 parallelizes GIN builds 2.4x, and "a column type, not a '
        'cluster" is the new rule. Even SQLite ate the dependency.')
    assert any(a.startswith("2.4") for a in anchors)
    assert any(a.startswith('"') for a in anchors)
    assert "SQLite" in anchors and "GIN" in anchors


def test_quote_regex_is_apostrophe_safe():
    # "Landauer's ... today's" must never parse as a quoted phrase
    anchors = concrete_anchors(
        "The gap between Landauer's principle and today's CMOS is vast.")
    assert not any(a.startswith("'") for a in anchors)
    assert "CMOS" in anchors and "Landauer's" in anchors


# ------------------------------------------------------------ freshness gate

def _save_item(repo, url, title, pub):
    return repo.save_research_item({
        "title": title, "source_url": url, "publisher": "t.example",
        "published_at": pub, "summary": "s", "category": "current_development"})


def test_recent_research_max_age_filters_stale_items(repo):
    from datetime import datetime, timedelta, timezone
    fmt = lambda dt: dt.strftime("%a, %d %b %Y %H:%M:%S %z")  # noqa: E731
    now = datetime.now(timezone.utc)
    _save_item(repo, "https://a.example/1", "Fresh item", fmt(now - timedelta(days=2)))
    _save_item(repo, "https://b.example/2", "Stale item", fmt(now - timedelta(days=30)))
    fresh = repo.recent_research(limit=10, max_age_days=7)
    assert [r["title"] for r in fresh] == ["Fresh item"]
    # fallback: nothing within the window -> newest items still returned
    stale_only = repo.recent_research(limit=10, max_age_days=1)
    assert len(stale_only) == 2


def test_research_by_ids_preserves_order(repo):
    i1 = _save_item(repo, "https://a.example/1", "One", "Wed, 01 Jan 2026 00:00:00 +0000")
    i2 = _save_item(repo, "https://a.example/2", "Two", "Wed, 01 Jan 2026 01:00:00 +0000")
    rows = repo.research_by_ids([i2, i1])
    assert [r["id"] for r in rows] == [i2, i1]


def test_research_by_urls(repo):
    _save_item(repo, "https://a.example/x", "X", "Wed, 01 Jan 2026 00:00:00 +0000")
    rows = repo.research_by_urls(["https://a.example/x", "https://missing.example/"])
    assert len(rows) == 1 and rows[0]["title"] == "X"


# -------------------------------------------------------- writer v4 + voice v2

def test_writer_prompt_v5_has_shape_and_contracts():
    from src.prompts import render, versions_used
    text = render("writer", voice_block="VOICE")
    for marker in ("SHAPE CONTRACT", "SPECIFICITY CONTRACT", "VALUE CONTRACT",
                   "BANNED CONSTRUCTIONS", "concrete_anchors", "hook_line",
                   "EXEMPLARS OF THE TARGET QUALITY BAR"):
        assert marker in text, marker
    assert versions_used("writer") == {"writer": "v5"}


def test_strategist_v4_and_iterator_v4_registered():
    from src.prompts import render, versions_used
    assert versions_used("strategist") == {"strategist": "v4"}
    assert versions_used("iterator") == {"iterator": "v4"}
    assert "RECENT REJECTIONS" in render("strategist")
    assert "POST SHAPE" in render("iterator")


def test_voice_profile_v3_block():
    from src.voice.profile import VoiceProfile
    block = VoiceProfile().to_prompt_block()
    assert "persona:" in block
    assert "format rule:" in block
    assert "specificity rule:" in block
    assert "value rule:" in block
    assert VoiceProfile.VERSION == "voice_stable_v3"


def test_exemplar_verbatim_copy_fails():
    """The style exemplars in the prompt must never come back as output."""
    from src.config import get_config
    exemplars = get_config().voice.get("voice", {}).get("exemplars") or []
    assert len(exemplars) >= 4
    copied = ("Frameworks demo thirty tools; real deployments settle at "
              "four or five, so something is off.")
    rules = dict(get_config().quality.get("anti_slop", {}))
    rules["exemplars"] = exemplars
    r = AntiSlopEngine(rules).check_text(copied)
    assert not r.passed
    assert any("exemplar" in i for i in r.issues)
    # a merely similar but original post is fine
    original = ("Tool budgets quietly decide agent reliability. A survey put "
                "default tool counts at 40 while real deployments settle near "
                "5, and trimming the list cut regression rates noticeably.")
    r2 = AntiSlopEngine(rules).check_text(original)
    assert r2.passed, r2.issues


def test_writer_auto_attaches_single_source_url():
    """FACT claims without a source_url inherit the only source URL in the brief."""
    from src.generation.writer import Writer
    from src.voice.profile import VoiceProfile
    nim = MockNIM(responses={"ANGLE:": {
        "body": ANCHORED_POST, "thread_posts": None,
        "claims": [{"text": "Frameworks ship 40 tools", "claim_type": "FACT",
                    "source_url": None, "confidence": 80}],
        "why_this_exists": "x", "concrete_anchors": ["40", "5"]}})
    writer = Writer(nim, None, VoiceProfile())
    research = [{"title": "t", "source_url": "https://example.com/a",
                 "summary": "s", "article": ""}]
    out = writer.write("x", "single", "angle", "AI", research, claims=[])
    assert out["claims"][0]["source_url"] == "https://example.com/a"


def test_research_pipeline_computes_freshness(repo, mock_nim):
    """Freshness is computed in code from published_at, not trusted from feeds."""
    from datetime import datetime, timedelta, timezone
    from src.pipeline.production import ResearchPipeline
    from tests.conftest import MockNIM
    import src.config as cfg_mod
    pipe = ResearchPipeline(repo, MockNIM())
    fmt = lambda dt: dt.strftime("%a, %d %b %Y %H:%M:%S %z")  # noqa: E731
    now = datetime.now(timezone.utc)
    items = pipe._fetch_feed  # attribute exists
    item_new = {"title": "n", "source_url": "https://n.example/1",
                "published_at": fmt(now - timedelta(hours=2)), "summary": "",
                "category": "current_development"}
    item_old = {"title": "o", "source_url": "https://o.example/1",
                "published_at": fmt(now - timedelta(days=40)), "summary": "",
                "category": "current_development"}
    from src.pipeline.production import item_age_days
    assert item_age_days(item_new) < 3
    assert item_age_days(item_old) > 21


# --------------------------------------------------- bounded auto-revision

GENERIC_BODY = ("Memory optimized architectures reshape the landscape of the "
                "compute ecosystem today. The journey reframes the paradigm of "
                "modern infrastructure narratives for every engineering team.")


def _failed_post(repo, bodies):
    idea = repo.save_idea("i", "AI", {}, 80, {})
    pid = repo.create_post("x", "single", "AI", idea, "a")
    for st in (State.CANDIDATE, State.RESEARCHED, State.STRATEGIZED, State.DRAFTED):
        repo.move_state(pid, st)
    for body in bodies:
        repo.add_version(pid, body, None, {}, [], [])
    repo.move_state(pid, State.QUALITY_REVIEW)
    repo.move_state(pid, State.QUALITY_FAILED)
    return pid


def test_quality_auto_revises_failed_post_with_findings(repo):
    from src.pipeline.production import QualityPipeline
    pid = _failed_post(repo, [GENERIC_BODY])
    repo.save_quality_eval(pid, 1, "antislop", False, 45,
                           ["abstract noun soup in: 'Memory optimized...'"],
                           [], ["remove filler; add a real observation"],
                           "deterministic")
    nim = MockNIM(responses={
        "Fix these critic findings": {
            "body": ANCHORED_POST, "thread_posts": None,
            "claims": [], "changes_made": ["replaced abstractions with specifics"]}})
    QualityPipeline(repo, nim).run()

    assert repo.version_count(pid) == 2          # auto-revision produced v2
    end = repo.get_post(pid)["state"]
    assert end in (State.WAITING_APPROVAL.value, State.EDITORIALLY_RANKED.value,
                   State.QUALITY_PASSED.value)
    v2 = repo.get_version(pid, 2)
    assert v2["body"] == ANCHORED_POST
    assert v2["iteration_instruction"].startswith("auto:")


def test_quality_blocks_post_after_revision_budget(repo):
    from src.pipeline.production import QualityPipeline
    pid = _failed_post(repo, [GENERIC_BODY, GENERIC_BODY + " Extra.",
                              GENERIC_BODY + " More."])
    nim = MockNIM()   # must not even be consulted
    QualityPipeline(repo, nim).run()

    assert repo.get_post(pid)["state"] == State.BLOCKED.value
    assert repo.version_count(pid) == 3          # no version churn
    assert not any(kind == "structured" for kind, _ in nim.calls)


def test_auto_revise_survives_model_failure(repo):
    """A failing revision model must not crash the quality stage."""
    from src.pipeline.production import QualityPipeline
    pid = _failed_post(repo, [GENERIC_BODY])
    nim = MockNIM(fail_times={"structured": 99})
    QualityPipeline(repo, nim).run()
    end = repo.get_post(pid)["state"]
    assert end in (State.QUALITY_FAILED.value, State.QUALITY_REVIEW.value,
                   State.BLOCKED.value)
