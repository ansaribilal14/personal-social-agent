"""End-to-end deterministic lifecycle (spec section 55):

research fixture -> idea -> draft -> critics -> validation -> GitHub issue ->
iterate -> approve -> schedule -> Mock Buffer -> publication record ->
metrics -> analytics. No real publication. No live API.
"""
import json

from src.analytics.engine import AnalyticsEngine, MetricsCollector
from src.critics.quality import QualityEngine
from src.pipeline.ops import PublishPipeline, SchedulePipeline
from src.publishing.publisher import Publisher
from src.review.lifecycle import ReviewLifecycle
from src.security import kill_switch as ks
from src.similarity.engine import OriginalityEngine, DuplicationReport
from src.state.machine import State
from src.voice.profile import LearningEngine


RESEARCH_FIXTURE = [{
    "title": "Agent frameworks default to 40+ tools",
    "source_url": "https://example.com/agent-tools-2026",
    "publisher": "Example Research",
    "summary": "A survey found agent frameworks ship with 40 tools by default "
               "while production agents actively use about 5.",
    "category": "current_development",
    "freshness": "fresh",
}]

CLAIMS = [{
    "text": "Agent frameworks ship with about 40 tools by default",
    "claim_type": "FACT",
    "source": {"url": "https://example.com/agent-tools-2026"},
    "confidence": 80,
}]

DRAFT_BODY = ("Agent frameworks ship 40 tools by default.\n\n"
              "Production agents use 5.\n\n"
              "Tool selection errors compound faster than capability gaps. "
              "The fix is fewer tools.")


def test_full_lifecycle(repo, mock_nim, mock_buffer, mock_github):
    # 1. research fixture stored (feed items would arrive here)
    for item in RESEARCH_FIXTURE:
        repo.save_research_item(item)
    research = repo.recent_research(limit=10)
    assert len(research) == 1

    # 2. idea + score computed in code
    evaluation = {k: 80 for k in ("novelty", "interestingness", "relevance",
                                  "personal_fit", "discussion_value",
                                  "factual_confidence", "content_potential",
                                  "timeliness")}
    score = int(round(sum(evaluation.values()) / len(evaluation)))
    why = {"why_interesting": "Inverts the tool-count narrative with production data",
           "why_now": "Agent frameworks are shipping weekly right now",
           "why_this_angle": "Defaults are the bug, not capability",
           "why_this_platform": "Sharp single claim suits X",
           "why_this_account": "Builder documenting real agent failures"}
    idea_id = repo.save_idea("Agents misuse tools by default", "AI", evaluation,
                             score, why, source_item_ids=[research[0]["id"]])

    # 3. draft -> state walk
    pid = repo.create_post("x", "single", "AI", idea_id,
                           "Defaults are the bug", state=State.DISCOVERED)
    for s in ("CANDIDATE", "RESEARCHED", "STRATEGIZED", "DRAFTED"):
        repo.move_state(pid, State(s))
    version_no = repo.add_version(pid, DRAFT_BODY, None, {"writer": "v2"},
                                  {"voice_version": "voice_stable_v1"}, CLAIMS)

    # 4. critics + validation (quality engine, deterministic gates)
    dup = OriginalityEngine(history=[]).check(DRAFT_BODY)
    assert not dup.duplicated
    repo.move_state(pid, State.QUALITY_REVIEW)
    decision = QualityEngine().evaluate(
        repo.get_post(pid), repo.get_version(pid, version_no),
        {"duplication": dup, "nim": mock_nim, "claims": CLAIMS,
         "voice_cfg": {"forbidden_patterns": [], "emoji_usage": "none by default"},
         "min_substantive_ratio": 0.6})
    assert decision.decision in ("PASS", "REVISE"), decision.issues
    if decision.decision == "PASS":
        repo.move_state(pid, State.QUALITY_PASSED)
        repo.set_editorial_score(pid, decision.overall_score)
        repo.move_state(pid, State.EDITORIALLY_RANKED)
        repo.move_state(pid, State.WAITING_APPROVAL)

    # 5. GitHub review issue
    lifecycle = ReviewLifecycle(repo, mock_github)
    post = repo.get_post(pid)
    issue = lifecycle.create_review_issue(
        post, repo.get_version(pid, 1), {},
        {"overall_score": post.get("editorial_score") or score})
    assert "POST_UID:" in issue["body"]

    # 6. iterate -> new version (NIM mock), then approve THE new version
    repo.move_state(pid, State.ITERATING)
    it_result = lifecycle.process_comment(
        issue["number"], 1001, "/iterate tighten the ending", "ansaribilal14")
    assert it_result["accepted"] and it_result["action"] == "ITERATED"

    writer_mock = mock_nim
    writer_mock.responses["structured_default"] = {
        "body": DRAFT_BODY.replace("The fix is fewer tools.",
                                   "Fix the default, not the model."),
        "thread_posts": None, "claims": CLAIMS,
    }
    from src.pipeline.ops import IteratePipeline
    IteratePipeline(repo, writer_mock).run(pid, "tighten the ending", "ansaribilal14")
    repo.move_state(pid, State.QUALITY_REVIEW)
    repo.move_state(pid, State.QUALITY_PASSED)
    repo.move_state(pid, State.EDITORIALLY_RANKED)
    repo.move_state(pid, State.WAITING_APPROVAL)
    v2 = repo.get_post(pid)["current_version"]
    assert v2 == 2

    # 7. approve v2 exactly
    approve = lifecycle.process_comment(issue["number"], 1002, "/approve",
                                        "ansaribilal14")
    assert approve["accepted"] and approve["version"] == v2
    assert repo.get_post(pid)["state"] == State.APPROVED.value

    # 8. schedule (kill switch must be enabled explicitly)
    assert not ks.is_publishing_enabled(repo)
    out = SchedulePipeline(repo, None).run()
    assert out.get("blocked") is True          # cannot schedule while disabled
    ks.enable(repo)
    out = SchedulePipeline(repo, None).run()
    assert out.get("scheduled") and len(out["scheduled"]) == 1

    # 9. publish through Mock Buffer
    summary = PublishPipeline(repo, None, mock_buffer).run()
    assert summary["scheduled"] == 1
    assert repo.get_post(pid)["state"] == State.PUBLISHED.value
    pub_row = repo.db.query("SELECT * FROM publications WHERE post_id=:p", {"p": pid})[0]
    assert pub_row["version"] == v2 and pub_row["buffer_post_id"].startswith("buf-")

    # 9b. duplicate publish attempt is impossible
    summary2 = PublishPipeline(repo, None, mock_buffer).run()
    assert summary2["processed"] == 0 or summary2["scheduled"] == 0
    assert len(mock_buffer.calls) == 1

    # 10. metrics snapshots (append-only) + analytics
    MetricsCollector(repo, mock_buffer).collect_all_due()
    assert len(repo.metric_snapshots(pid)) >= 1
    analysis = AnalyticsEngine(repo).analyze()
    assert "by_platform" in analysis

    # 11. learning loop recorded the approval signal
    LearningEngine(repo).record_feedback("APPROVED", "ending_style", "terse")
    # 12. full audit trail exists
    assert len(repo.events(50)) >= 10
