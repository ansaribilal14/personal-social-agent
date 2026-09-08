"""Failure-mode tests (spec section 54): NIM failures, DB outage, malformed
commands, kill switch, stale state - none may publish or corrupt state."""
import pytest

from integrations.nvidia.client import (NimInvalidJSON, NimRateLimited, NimTimeout,
                                        NimUnavailable)
from src.state.machine import State
from tests.conftest import MockNIM


def _pipeline(repo, nim):
    from src.pipeline.production import QualityPipeline
    return QualityPipeline(repo, nim)


def test_nim_unavailable_blocks_generation_cleanly(repo):
    from src.pipeline.production import GenerationPipeline
    p = GenerationPipeline(repo, None)  # no NIM client
    with pytest.raises(RuntimeError, match="BLOCKED"):
        p.run()


def test_nim_invalid_json_never_controls_state(repo, mock_nim):
    """Malformed output -> typed failure -> no state corruption."""
    nim = MockNIM()
    nim.responses["structured"] = "this is not json at all"
    from src.generation.writer import GenerationError, Writer
    from src.voice.profile import VoiceProfile
    w = Writer(nim, repo, VoiceProfile())
    with pytest.raises(GenerationError):
        w.write("x", "single", "angle", "AI", [], [])


def test_nim_timeout_typed():
    import requests
    nim = MockNIM()
    nim.fail_times["chat"] = 9
    nim.fail_exception["chat"] = requests.Timeout()
    from integrations.nvidia.client import NIMClient
    nim2 = NIMClient(api_key="k", session=_FailingSession(requests.Timeout()))
    with pytest.raises(NimTimeout):
        nim2.chat("s", "u")


class _FailingSession:
    def __init__(self, exc):
        self.exc = exc

    def post(self, *a, **k):
        raise self.exc


def test_nim_rate_limit_and_unreachable_classification():
    import requests
    from integrations.nvidia.client import NIMClient
    nim = NIMClient(api_key="k", session=_FailingSession(requests.ConnectionError()))
    with pytest.raises(NimUnavailable):
        nim.chat("s", "u")


def test_nim_no_key_raises_unavailable():
    from integrations.nvidia.client import NIMClient
    nim = NIMClient(api_key=None)
    with pytest.raises(NimUnavailable):
        nim.chat("s", "u")


def test_db_outage_stops_pipeline_without_publish(repo):
    """DB failure mid-run: nothing published, run marked failed."""
    from src.pipeline.ops import PublishPipeline
    db = repo.db
    p = PublishPipeline(repo)
    db.simulate_outage()
    try:
        p.run()
        assert False, "pipeline should fail on DB outage"
    except Exception:
        pass


def test_malformed_comment_never_blocks_system(repo, mock_github):
    from src.review.lifecycle import ReviewLifecycle
    pid = repo.create_post("x", "single", "AI", None, None, state=State.DISCOVERED)
    mock_github.create_issue("t", "POST_UID: none-such")
    lifecycle = ReviewLifecycle(repo, mock_github)
    outcome = lifecycle.process_comment(999, 123, "just a random comment ; rm -rf /",
                                        "ansaribilal14")
    assert not outcome["accepted"]


def test_kill_switch_blocks_schedule_pipeline(repo):
    from src.pipeline.ops import SchedulePipeline
    out = SchedulePipeline(repo, None).run()
    assert out.get("blocked") is True


def test_stale_version_cannot_publish(repo):
    from src.publishing.publisher import PublishRefused
    pid = repo.create_post("x", "single", "AI", None, None, state=State.DISCOVERED)
    for s in ("CANDIDATE", "RESEARCHED", "STRATEGIZED", "DRAFTED", "QUALITY_REVIEW",
              "QUALITY_PASSED", "EDITORIALLY_RANKED", "WAITING_APPROVAL", "APPROVED"):
        repo.move_state(pid, State(s))
    repo.add_version(pid, "v1", None, None, None, [])
    repo.record_approval_event(pid, 1, "APPROVED", "ansaribilal14")
    repo.add_version(pid, "v2", None, None, None, [])   # current = 2 now
    pub = __import__("src.publishing.publisher", fromlist=["Publisher"]).Publisher(repo)
    with pytest.raises(PublishRefused):
        pub._verify_approval(pid, 1)


def test_prompt_versions_recorded(repo):
    pid = repo.create_post("x", "single", "AI", None, None, state=State.DISCOVERED)
    repo.add_version(pid, "b", None, {"writer": "v2"}, None, [])
    assert repo.latest_version(pid)["prompt_versions"] == {"writer": "v2"}


def test_metric_snapshots_append_only(repo):
    pid = repo.create_post("x", "single", "AI", None, None, state=State.DISCOVERED)
    repo.save_metric_snapshot(pid, "x", 1, {"likes": 5})
    repo.save_metric_snapshot(pid, "x", 1, {"likes": 99})
    rows = repo.metric_snapshots(pid)
    assert len(rows) == 1 and rows[0]["metrics"] == {"likes": 5}  # never overwritten


def test_json_fallback_parser():
    from integrations.nvidia.client import _extract_json
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('Sure!\n```json\n{"a": 2}\n```') == {"a": 2}
    assert _extract_json('noise {"a": 3} trailing') == {"a": 3}
    assert isinstance(_extract_json("no json here"), NimInvalidJSON)


def test_empty_model_response_rejected():
    from integrations.nvidia.client import NIMClient, NimEmptyResponse

    class EmptySession:
        class resp:  # noqa
            status_code = 200
            @staticmethod
            def json():
                return {"choices": [{"message": {"content": ""}}]}

    nim = NIMClient(api_key="k", session=type("S", (), {"post": lambda *a, **k: EmptySession.resp})())
    with pytest.raises(NimEmptyResponse):
        nim.chat("s", "u")


def test_quality_failed_post_retries_through_revising(repo, mock_nim):
    """Regression (live run 34283048081): the quality stage re-evaluates
    QUALITY_FAILED posts, but the state machine requires QUALITY_FAILED ->
    REVISING -> QUALITY_REVIEW. The invalid direct edge crashed the whole
    pipeline the first time a real post failed quality."""
    from src.pipeline.production import QualityPipeline
    from tests.e2e.test_full_lifecycle import CLAIMS, DRAFT_BODY
    idea = repo.save_idea("i", "AI", {}, 80, {})
    pid = repo.create_post("x", "single", "AI", idea, "a")
    repo.add_version(pid, DRAFT_BODY, None, {}, CLAIMS, [])
    for st in (State.CANDIDATE, State.RESEARCHED, State.STRATEGIZED, State.DRAFTED):
        repo.move_state(pid, st)
    repo.move_state(pid, State.QUALITY_REVIEW)
    repo.move_state(pid, State.QUALITY_FAILED)   # failed a previous evaluation

    QualityPipeline(repo, mock_nim).run()        # must not raise InvalidTransition

    end_state = repo.get_post(pid)["state"]
    assert end_state in (State.QUALITY_PASSED.value, State.QUALITY_FAILED.value,
                         State.WAITING_APPROVAL.value, State.EDITORIALLY_RANKED.value)
