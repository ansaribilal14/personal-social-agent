"""PostShapeCritic (posts must look like posts, not essays) + the reject ->
instant-regeneration dispatch path (user feedback 2026-09).

Researched X/Threads conventions encoded here:
- hook first line stands alone (~40-100 chars, hard fail past 110)
- short blocks separated by blank lines; 3+ sentence paragraphs fail
- bodies >160 chars must contain blank-line breaks
- essay cadence (every sentence long and even) fails
No test talks to the network; urllib is monkeypatched.
"""
from __future__ import annotations

import json

from src.critics.critics import PostShapeCritic
from src.state.machine import State


def _version(body: str) -> dict:
    return {"version": 1, "body": body, "thread_posts": None}


SHAPED_POST = ("Agent tool budgets are about to become the bottleneck.\n\n"
               "Every framework ships with 40 tools by default, yet production "
               "agents use 5.\n\n"
               "The default is the bug.")

ESSAY_POST = ("Agent tool budgets are about to become the bottleneck. Every "
              "framework ships with 40 tools by default, yet production agents "
              "use 5. The reason: tool selection errors compound faster than "
              "capability gaps, and teams that cut tool count saw reliability "
              "jump 30 percent in their benchmarks while others kept shipping "
              "regressions that nobody could explain at all.")


# ------------------------------------------------------------- post shape
def test_shaped_post_passes():
    r = PostShapeCritic().evaluate({}, _version(SHAPED_POST), {})
    assert r.passed, r.issues


def test_essay_wall_of_text_fails():
    r = PostShapeCritic().evaluate({}, _version(ESSAY_POST), {})
    assert not r.passed
    assert any("wall of text" in i for i in r.issues)
    assert any("3+ sentences" in i for i in r.issues)


def test_long_hook_line_fails():
    body = ("This is an extremely long opening line that just keeps going and "
            "going far past the character budget a hook should ever need on a "
            "timeline.\n\nThen a block.")
    r = PostShapeCritic().evaluate({}, _version(body), {})
    assert not r.passed
    assert any("hook line too long" in i for i in r.issues)


def test_short_one_liner_is_allowed():
    body = "Production agents use 5 tools, not 40."
    r = PostShapeCritic().evaluate({}, _version(body), {})
    assert r.passed, r.issues


def test_short_dense_line_with_3_sentences_fails():
    body = "Frameworks ship 40 tools. Production agents use 5. Defaults are the bug."
    r = PostShapeCritic().evaluate({}, _version(body), {})
    assert not r.passed


def test_thread_posts_are_shape_checked_individually():
    version = {"thread_posts": [SHAPED_POST, ESSAY_POST], "body": None}
    r = PostShapeCritic().evaluate({}, version, {})
    assert not r.passed


def test_post_shape_is_a_hard_gate_in_quality_engine():
    from src.critics.quality import QualityEngine
    assert "post_shape" in QualityEngine().hard_gates


# ------------------------------------------------- reject -> regeneration
def test_dispatch_engine_run_posts_dispatch(monkeypatch, repo):
    from src.review import regenerate
    captured = {}

    class FakeResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=20):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["headers"] = dict(req.header_items())
        return FakeResponse()

    monkeypatch.setattr(regenerate, "urlopen", fake_urlopen)
    outcome = regenerate.dispatch_engine_run(
        repo, reason="X-2026-AB12 too essay-like", actor="discord:ops",
        repository="acme/social-agent", token="tok-1", ref="main")
    assert outcome == {"dispatched": True, "detail": "HTTP 204"}
    assert captured["url"].endswith(
        "/repos/acme/social-agent/actions/workflows/engine.yml/dispatches")
    assert captured["body"]["ref"] == "main"
    assert captured["body"]["inputs"]["mode"] == "full"
    assert "X-2026-AB12" in captured["body"]["inputs"]["regen_reason"]
    auth = [v for k, v in captured["headers"].items() if k.lower() == "authorization"]
    assert auth == ["Bearer tok-1"]
    # audited in the DB
    events = [e["event_type"] for e in repo.events(limit=10)]
    assert "regenerate.dispatched" in events


def test_dispatch_without_token_is_graceful(repo, monkeypatch):
    from src.review import regenerate
    for var in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REGEN_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    outcome = regenerate.dispatch_engine_run(
        repo, reason="r", actor="discord:ops", repository="acme/social-agent")
    assert outcome["dispatched"] is False
    events = [e["event_type"] for e in repo.events(limit=10)]
    assert "regenerate.skipped_no_token" in events


def test_dispatch_http_error_never_raises(repo, monkeypatch):
    from src.review import regenerate
    from urllib.error import HTTPError

    def fake_urlopen(req, timeout=20):
        raise HTTPError(req.full_url, 422, "Unprocessable",
                        {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(regenerate, "urlopen", fake_urlopen)
    outcome = regenerate.dispatch_engine_run(
        repo, reason="r", actor="a", repository="acme/social-agent", token="t")
    assert outcome["dispatched"] is False
    assert "422" in outcome["detail"]
    events = [e["event_type"] for e in repo.events(limit=10)]
    assert "regenerate.dispatch_failed" in events


def test_recent_rejection_reasons_feed_ideas_prompt(repo, monkeypatch):
    """The ideas stage wraps recent rejections as untrusted anti-guidance."""
    from src.pipeline.production import IdeaDiscoveryPipeline
    from tests.conftest import MockNIM

    uid = "X-2026-ZZ999"
    idea = repo.save_idea("stale idea", "AI", {}, 80, {})
    pid = repo.create_post("x", "single", "AI", idea, "stale angle")
    repo.add_version(pid, "body", None, {}, {}, [])
    for s in ("CANDIDATE", "RESEARCHED", "STRATEGIZED", "DRAFTED",
              "QUALITY_REVIEW", "QUALITY_PASSED", "EDITORIALLY_RANKED",
              "WAITING_APPROVAL"):
        repo.move_state(pid, State(s))
    repo.record_approval_event(pid, 1, "REJECTED", "discord:ops",
                               reason="sounds like a statement, no real value")
    repo.move_state(pid, State.REJECTED)

    nim = MockNIM(responses={"structured_default": {"ideas": []}})
    captured = {}
    orig = nim.chat_structured

    def spy(system, user, **kwargs):
        captured["user"] = user
        return orig(system, user, **kwargs)

    nim.chat_structured = spy
    IdeaDiscoveryPipeline(repo, nim).run()
    assert "structured" in nim.calls[0][0] if nim.calls else True
    prompt = captured["user"]
    assert "RECENT REJECTIONS" in prompt
    assert "sounds like a statement" in prompt
    assert "UNTRUSTED" in prompt.upper()


def test_poller_script_wires_regeneration(repo, monkeypatch):
    """The Actions entrypoint passes run_regenerate into the poller."""
    import inspect
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] /
           "scripts" / "discord_review_poller.py").read_text()
    assert "run_regenerate" in src
    assert "dispatch_engine_run" in src
    # and the workflow grants the permission to dispatch
    yml = (Path(__file__).resolve().parents[2] /
           ".github/workflows/discord-approval.yml").read_text()
    assert "actions: write" in yml


# ---------------------------------------------- calibration fixes from live run
def test_substantive_ratio_counts_proper_noun_anchors():
    """Named specifics (NASA, Max Planck Institute) carry substance even
    without essay marker words - live run produced false 0.00 ratios."""
    from src.critics.antislop import substantive_ratio
    post = ("Tiny sparks eat paint.\n\n"
            "A study from the Max Planck Institute shows charged drops blow "
            "holes in coatings.\n\n"
            "NASA saw the same effect on shuttle panels.")
    assert substantive_ratio(post) >= 0.6
    # pure fluff still fails
    assert substantive_ratio("Nice day out today. Hope you agree. Cool stuff.") < 0.5


def test_part_labels_are_meta_labels():
    """The model literally wrote 'Hook: ...' in a live run - block it."""
    from src.critics.antislop import AntiSlopEngine
    for labeled in ("Hook: Raindrops punch through paint.",
                    "Punch: the default is the bug.",
                    "Takeaway: ship less."):
        r = AntiSlopEngine().check_text(labeled)
        assert not r.passed, labeled


def test_revision_budget_bumped_to_three():
    from src.config import get_config
    assert int(get_config().quality.get("thresholds", {}).get(
        "max_revision_cycles", 2)) == 3


# ------------------------------------------------ live-run root-cause fixes
def test_writer_attaches_best_source_in_multi_source_brief():
    """FACT claims without a URL inherit the best-matching research item's
    URL when the brief has several sources (live run: all facts unverified)."""
    from src.generation.writer import Writer
    from tests.conftest import MockNIM

    class FakeRepo:
        def db_query(self):
            return []

    nim = MockNIM(responses={"structured_default": {
        "body": "shape", "thread_posts": None,
        "claims": [
            {"text": "The Roman telescope camera is 300 megapixels",
             "claim_type": "FACT", "source_url": None, "confidence": 80},
            {"text": "Unrelated opinion", "claim_type": "OPINION",
             "source_url": None, "confidence": 60},
        ]}})
    research = [
        {"title": "Roman space telescope 300 megapixel camera", "summary": "wide sky survey",
         "source_url": "https://example.com/roman"},
        {"title": "Deep sea mining rules", "summary": "international seabed authority",
         "source_url": "https://example.com/seabed"},
    ]
    writer = Writer(nim, None)
    result = writer.write("x", "single", "angle", "AI", research, claims=[])
    fact = [c for c in result["claims"] if c["claim_type"] == "FACT"][0]
    assert fact["source_url"] == "https://example.com/roman"


def test_ideas_stage_excludes_research_that_already_produced_posts(repo):
    """Staleness guard: items behind PROMOTED ideas are dropped from the pool
    (same stories every run = the stale-posts loop)."""
    from src.pipeline.production import IdeaDiscoveryPipeline
    from src.state.machine import State
    from tests.conftest import MockNIM

    r1 = repo.save_research_item({"title": "used story", "source_url": "https://e.com/1",
                                  "summary": "s", "category": "current_development",
                                  "freshness": "fresh"})
    r2 = repo.save_research_item({"title": "fresh story", "source_url": "https://e.com/2",
                                  "summary": "s", "category": "current_development",
                                  "freshness": "fresh"})
    repo.save_idea("old idea", "AI", {}, 80, {}, source_item_ids=[r1],
                   status="PROMOTED")

    captured = {}

    class SpyNIM(MockNIM):
        def chat_structured(self, system, user, **kwargs):
            captured["user"] = user
            return {"ideas": []}

    IdeaDiscoveryPipeline(repo, SpyNIM(responses={})).run()
    assert "fresh story" in captured["user"]
    assert "used story" not in captured["user"]


def test_stock_punch_line_reuse_fails():
    """A live run ended three different posts with the same exemplar punch
    ('The default is the bug.') - short verbatim closings are flagged."""
    from src.critics.antislop import AntiSlopEngine
    from src.config import get_config
    rules = dict(get_config().quality.get("anti_slop", {}))
    rules["exemplars"] = get_config().voice.get("voice", {}).get("exemplars") or []
    body = ("Roman's first image would need over half a million 4K TVs.\n\n"
            "The telescope's 300-megapixel camera sees 100x wider than Hubble.\n\n"
            "The default is the bug.")
    r = AntiSlopEngine(rules).check_text(body)
    assert not r.passed
    assert any("stock punch line" in i for i in r.issues)


def test_ideas_stage_holds_duplicate_source_ideas(repo):
    """One idea per research item per run - same-source ideas are HELD."""
    from src.pipeline.production import IdeaDiscoveryPipeline
    from tests.conftest import MockNIM

    r1 = repo.save_research_item({"title": "one story", "source_url": "https://e.com/a",
                                  "summary": "s", "category": "current_development",
                                  "freshness": "fresh"})
    why = {f"why_{k}": "x" * 30 for k in
           ("interesting", "now", "this_angle", "this_platform", "this_account")}
    why["why_this_platform"] = "the argument lands best as a punchy single on x"
    idea_a = {"statement": "idea A", "pillar": "AI",
              "evaluation": {"novelty": 80}, "why_me": why,
              "source_item_ids": [r1], "platform": "x", "format": "single"}
    idea_b = dict(idea_a, statement="idea B")
    IdeaDiscoveryPipeline(repo, MockNIM(responses={"structured_default": {
        "ideas": [idea_a, idea_b]}})).run()
    promoted = repo.db.query(
        "SELECT COUNT(*) AS n FROM ideas WHERE status != 'HELD'")[0]["n"]
    held = repo.db.query(
        "SELECT COUNT(*) AS n FROM ideas WHERE status='HELD'")[0]["n"]
    assert promoted == 1 and held >= 1
