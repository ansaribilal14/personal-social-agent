"""Outbox idempotency + publication uniqueness at DB level (spec 10/11)."""
import pytest

from src.publishing.publisher import PublishRefused, Publisher
from src.security import kill_switch as ks
from src.state.machine import State


def _approved_post(repo, body="A concise observation about tool budgets in agents."):
    pid = repo.create_post("x", "single", "AI", None, None, state=State.DISCOVERED)
    for s in ("CANDIDATE", "RESEARCHED", "STRATEGIZED", "DRAFTED", "QUALITY_REVIEW",
              "QUALITY_PASSED", "EDITORIALLY_RANKED", "WAITING_APPROVAL", "APPROVED"):
        repo.move_state(pid, State(s))
    v = repo.add_version(pid, body, None, {"writer": "v2"}, None, [])
    repo.record_approval_event(pid, v, "APPROVED", "ansaribilal14")
    return pid, v


def test_outbox_creation_is_idempotent(repo):
    pid, v = _approved_post(repo)
    o1 = repo.create_outbox_entry(pid, "x", v, "body", None)
    o2 = repo.create_outbox_entry(pid, "x", v, "body", None)
    assert o1 == o2


def test_two_publications_for_same_key_impossible(repo):
    pid, v = _approved_post(repo)
    ob = repo.create_outbox_entry(pid, "x", v, "body", None)
    pub = repo.record_publication(pid, ob, "x", v, "buf-1")
    dup = repo.record_publication(pid, ob, "x", v, "buf-2")  # INSERT OR IGNORE
    rows = repo.db.query("SELECT * FROM publications WHERE idempotency_key=:k",
                         {"k": pub})
    assert len(rows) == 1
    assert rows[0]["buffer_post_id"] == "buf-1"


def test_duplicate_outbox_rejects_second_unique_key_only_for_same_version(repo):
    pid, v1 = _approved_post(repo)
    v2 = repo.add_version(pid, "new body after iterate", None, {"writer": "v2"}, None, [])
    o1 = repo.create_outbox_entry(pid, "x", v1, "old", None)
    o2 = repo.create_outbox_entry(pid, "x", v2, "new", None)
    assert o1 != o2  # different versions are independent identities (spec 34)


def test_publisher_refuses_unapproved_state(repo):
    pid = repo.create_post("x", "single", "AI", None, None, state=State.WAITING_APPROVAL)
    repo.add_version(pid, "draft", None, None, None, [])
    repo.create_outbox_entry(pid, "x", 1, "draft", None)
    pub = Publisher(repo)
    with pytest.raises(PublishRefused):
        pub._verify_approval(pid, 1)


def test_publisher_refuses_stale_version(repo, enabled_repo):
    """v1 approved, v2 current -> publishing v1 must be REFUSED (spec 34)."""
    pid, v1 = _approved_post(repo, "version one")
    repo.add_version(pid, "version two", None, None, None, [])
    repo.create_outbox_entry(pid, "x", v1, "version one", None)
    pub = Publisher(repo)
    with pytest.raises(PublishRefused):
        pub._verify_approval(pid, v1)


def test_publisher_accepts_exact_version_with_kill_switch_on(enabled_repo, mock_buffer):
    pid, v = _approved_post(enabled_repo)
    ob = enabled_repo.create_outbox_entry(pid, "x", v, "approved body text", None)
    pub = Publisher(enabled_repo, mock_buffer)
    pub._publish_entry(enabled_repo.get_outbox(ob))
    post = enabled_repo.get_post(pid)
    assert post["state"] == State.PUBLISHED.value
    assert mock_buffer.calls[0]["text"] == "approved body text"


def test_approval_of_v1_does_not_authorize_v2(repo, mock_buffer):
    ks.enable(repo)
    pid, v1 = _approved_post(repo, "v1 text")
    v2 = repo.add_version(pid, "v2 text", None, None, None, [])
    repo.create_outbox_entry(pid, "x", v2, "v2 text", None)
    pub = Publisher(repo, mock_buffer)
    outbox_v2 = repo.db.query(
        "SELECT id FROM publication_outbox WHERE post_id=:p AND version=:v",
        {"p": pid, "v": v2})[0]["id"]
    with pytest.raises(PublishRefused):
        pub._publish_entry(repo.get_outbox(outbox_v2))


def test_later_rejection_blocks_publish(repo):
    pid, v = _approved_post(repo)
    repo.create_outbox_entry(pid, "x", v, "body", None)
    repo.record_approval_event(pid, v, "REJECTED", "ansaribilal14", reason="changed mind")
    pub = Publisher(repo)
    with pytest.raises(PublishRefused):
        pub._verify_approval(pid, v)
