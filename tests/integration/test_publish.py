"""Publish pipeline failure modes + retries + Discord notify (spec 40)."""
import pytest

from integrations.buffer.client import BufferAuthError
from src.publishing.publisher import PublishRefused, Publisher
from src.state.machine import State
from src.security import kill_switch as ks
from tests.conftest import MockBuffer


def _rewind_outbox(repo, outbox_id, minutes=30):
    """Simulate that the retry backoff window has already elapsed."""
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    repo.db.execute("UPDATE publication_outbox SET updated_at=:t WHERE id=:i",
                    {"t": past, "i": outbox_id})


def _approved_post(repo, body="body text for publish"):
    pid = repo.create_post("x", "single", "AI", None, None, state=State.DISCOVERED)
    for s in ("CANDIDATE", "RESEARCHED", "STRATEGIZED", "DRAFTED", "QUALITY_REVIEW",
              "QUALITY_PASSED", "EDITORIALLY_RANKED", "WAITING_APPROVAL", "APPROVED"):
        repo.move_state(pid, State(s))
    v = repo.add_version(pid, body, None, {"writer": "v2"}, None, [])
    repo.record_approval_event(pid, v, "APPROVED", "ansaribilal14")
    return pid, v


def test_full_publish_with_mock_buffer(repo, mock_buffer):
    ks.enable(repo)
    pid, v = _approved_post(repo)
    ob = repo.create_outbox_entry(pid, "x", v, "body text for publish", None)
    summary = Publisher(repo, mock_buffer).process_outbox()
    assert summary["scheduled"] == 1
    post = repo.get_post(pid)
    assert post["state"] == State.PUBLISHED.value
    pub = repo.db.query("SELECT * FROM publications WHERE post_id=:p", {"p": pid})
    assert pub and pub[0]["buffer_post_id"].startswith("buf-")


def test_buffer_failure_retries_same_content(repo, mock_buffer):
    ks.enable(repo)
    """BUFFER_ERROR -> bounded retry with SAME content; never regenerates (spec 40)."""
    pid, v = _approved_post(repo, "immutable content")
    failing = MockBuffer(fail_first=1)
    ob = repo.create_outbox_entry(pid, "x", v, "immutable content", None)
    summary = Publisher(repo, failing).process_outbox()
    assert summary["failed"] == 1
    entry = repo.get_outbox(ob)
    assert entry["status"] == "BUFFER_ERROR"
    assert entry["attempt_count"] == 1
    # content unchanged
    assert repo.latest_version(pid)["body"] == "immutable content"
    # simulate backoff elapsing, then the retry succeeds with SAME content
    _rewind_outbox(repo, ob, minutes=10)
    summary2 = Publisher(repo, failing).process_outbox()
    assert summary2["scheduled"] == 1
    assert repo.get_post(pid)["state"] == State.PUBLISHED.value
    assert failing.calls[0]["text"] == "immutable content"


def test_retry_exhaustion_marks_publish_failed(repo, mock_buffer):
    ks.enable(repo)
    pid, v = _approved_post(repo, "doomed content")
    failing = MockBuffer(fail_first=99)
    ob = repo.create_outbox_entry(pid, "x", v, "doomed content", None)
    for _ in range(5):
        Publisher(repo, failing).process_outbox()
        _rewind_outbox(repo, ob, minutes=30)   # make backoff elapsed each cycle
    entry = repo.get_outbox(ob)
    assert entry["status"] == "PUBLISH_FAILED"
    assert repo.get_post(pid)["state"] == State.PUBLISH_FAILED.value


def test_auth_error_is_not_retried(repo, mock_buffer):
    ks.enable(repo)
    pid, v = _approved_post(repo, "x")

    class AuthFailBuffer(MockBuffer):
        def create_post(self, *a, **k):
            raise BufferAuthError("buffer auth rejected (HTTP 401)")

    ob = repo.create_outbox_entry(pid, "x", v, "x", None)
    summary = Publisher(repo, AuthFailBuffer()).process_outbox()
    entry = repo.get_outbox(ob)
    assert entry["status"] == "PUBLISH_FAILED"
    assert "401" in (entry["last_error"] or "")


def test_publish_process_never_publishes_without_approval_even_if_enabled(repo, mock_buffer):
    ks.enable(repo)
    pid, v = _approved_post(repo, "sneaky")
    # forge an outbox row without approval event
    repo.db.execute("DELETE FROM approval_events WHERE post_id=:p", {"p": pid})
    repo.db.execute(
        "INSERT INTO publication_outbox (post_id, idempotency_key, platform, version, body) "
        "VALUES (:p, 'forged:key:1', 'x', 1, 'sneaky')", {"p": pid})
    summary = Publisher(repo, mock_buffer).process_outbox()
    assert summary["failed"] == 1
    assert mock_buffer.calls == []
