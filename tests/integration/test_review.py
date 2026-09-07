"""Review lifecycle with mock GitHub: command intake, replay protection,
version binding (spec sections 33-36)."""
import pytest

from src.review.lifecycle import ReviewLifecycle
from src.state.machine import State


def _waiting_post(repo):
    pid = repo.create_post("x", "single", "AI", None, None, state=State.DISCOVERED)
    for s in ("CANDIDATE", "RESEARCHED", "STRATEGIZED", "DRAFTED", "QUALITY_REVIEW",
              "QUALITY_PASSED", "EDITORIALLY_RANKED", "WAITING_APPROVAL"):
        repo.move_state(pid, State(s))
    v = repo.add_version(pid, "review me", None, {"writer": "v2"}, None, [])
    return pid, v


def _issue_for(gh, repo, pid):
    post = repo.get_post(pid)
    gh.create_issue(
        title=f"POST #{post['post_uid']} - review",
        body=f"POST_UID: {post['post_uid']}\nStatus: {post['state']}")
    return post["post_uid"]


def test_approve_flow_via_issue(repo, mock_github):
    pid, v = _waiting_post(repo)
    uid = _issue_for(mock_github, repo, pid)
    lifecycle = ReviewLifecycle(repo, mock_github)
    outcome = lifecycle.process_comment(501, 9001, "/approve", "ansaribilal14")
    assert outcome["accepted"] and outcome["action"] == "APPROVED"
    assert repo.get_post(pid)["state"] == State.APPROVED.value
    events = repo.approval_history(pid)
    assert events[0]["action"] == "APPROVED" and events[0]["version"] == v


def test_comment_replay_is_refused(repo, mock_github):
    pid, v = _waiting_post(repo)
    _issue_for(mock_github, repo, pid)
    lifecycle = ReviewLifecycle(repo, mock_github)
    r1 = lifecycle.process_comment(501, 9001, "/approve", "ansaribilal14")
    r2 = lifecycle.process_comment(501, 9001, "/approve", "ansaribilal14")
    assert r1["accepted"]
    assert not r2["accepted"] and "replayed" in r2["reject_reason"]


def test_reject_closes_path(repo, mock_github):
    pid, v = _waiting_post(repo)
    _issue_for(mock_github, repo, pid)
    lifecycle = ReviewLifecycle(repo, mock_github)
    outcome = lifecycle.process_comment(501, 9002, "/reject too generic", "ansaribilal14")
    assert outcome["accepted"]
    assert repo.get_post(pid)["state"] == State.REJECTED.value


def test_iterate_marks_iterating(repo, mock_github):
    pid, v = _waiting_post(repo)
    _issue_for(mock_github, repo, pid)
    lifecycle = ReviewLifecycle(repo, mock_github)
    outcome = lifecycle.process_comment(501, 9003,
                                        "/iterate make the hook sharper", "ansaribilal14")
    assert outcome["accepted"] and outcome["action"] == "ITERATED"
    assert repo.get_post(pid)["state"] == State.ITERATING.value


def test_unauthorized_actor_cannot_touch_state(repo, mock_github):
    pid, v = _waiting_post(repo)
    _issue_for(mock_github, repo, pid)
    lifecycle = ReviewLifecycle(repo, mock_github)
    outcome = lifecycle.process_comment(501, 9004, "/approve", "random_person")
    assert not outcome["accepted"]
    assert repo.get_post(pid)["state"] == State.WAITING_APPROVAL.value


def test_approve_on_non_waiting_state_refused(repo, mock_github):
    pid, v = _waiting_post(repo)
    _issue_for(mock_github, repo, pid)
    repo.move_state(pid, State.REJECTED, actor="tester")
    lifecycle = ReviewLifecycle(repo, mock_github)
    outcome = lifecycle.process_comment(501, 9005, "/approve", "ansaribilal14")
    assert not outcome["accepted"]


def test_version_snapshot_immutable(repo):
    pid, v = _waiting_post(repo)
    ver1 = repo.get_version(pid, 1)
    repo.add_version(pid, "v2 body", None, None, None, [])
    assert repo.get_version(pid, 1)["body"] == ver1["body"]
    with pytest.raises(Exception):
        # direct duplicate insert must violate UNIQUE(post_id, version)
        repo.db.execute(
            "INSERT INTO post_versions (post_id, version, body) VALUES (:p, 1, 'forged')",
            {"p": pid})
