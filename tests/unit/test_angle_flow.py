"""Regression tests: the strategist's editorial ANGLE must survive all the
way to the writer prompt and the stored post - not get silently replaced by
the bare `statement` (spec: strategist_v4 vs writer_v5 contract). Also covers
the `why_this_exists` fallback so the Discord review card never shows a bare
"-" when the model omits the field.
"""
from src.db.repository import Repository
from src.generation.writer import Writer
from src.state.machine import State
from src.voice.profile import VoiceProfile
from tests.conftest import MockNIM

REAL_ANGLE = ("Citizen-science buried underpants worldwide and found "
              "urbanization predicts decomposition better than climate does")
BARE_STATEMENT = "Underpants decompose at different rates in different countries"

WRITER_JSON = {
    "body": "Land use beats climate as a predictor of decomposition.",
    "thread_posts": None,
    "claims": [],
    "concrete_anchors": ["25 countries"],
}


class _RecordingNIM(MockNIM):
    """Like MockNIM but keeps the full (untruncated) last user prompt."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.last_user = ""

    def chat_structured(self, system: str, user: str, **kwargs) -> dict:
        self.last_user = user
        return dict(self.responses.get("structured_default", WRITER_JSON))


def test_generation_pipeline_passes_real_angle_not_bare_statement(repo):
    """Regression for the reported 'baseless post' bug: production.py used to
    pass idea['statement'] to both the writer and create_post(), discarding
    the strategist's real `angle` entirely."""
    from src.pipeline.production import GenerationPipeline

    idea_id = repo.save_idea(
        BARE_STATEMENT, "Personal", {"novelty": 80}, 90,
        {"why_interesting": "x" * 25, "why_now": "x" * 25,
         "why_this_angle": "x" * 25, "why_this_platform": "single on x" + "x" * 15,
         "why_this_account": "x" * 25},
        angle=REAL_ANGLE)

    nim = _RecordingNIM()
    pipeline = GenerationPipeline(repo, nim)
    result = pipeline.run(limit=1)

    assert nim.last_user, "writer was never called"
    assert REAL_ANGLE in nim.last_user, (
        "writer prompt must contain the strategist's real angle")
    assert BARE_STATEMENT not in nim.last_user.split("ANGLE:")[1].split("\n")[0], (
        "the ANGLE line must not just be the bare statement")

    post_id = result["drafts"][0]["post_id"]
    post = repo.get_post(post_id)
    angle_row = repo.db.query("SELECT text FROM angles WHERE id=:i",
                              {"i": post["angle_id"]})
    assert angle_row[0]["text"] == REAL_ANGLE, (
        "the stored angle must be the real editorial angle, not the statement")


def test_writer_falls_back_to_why_me_when_model_omits_why_this_exists(repo):
    """If the model drops `why_this_exists` (contract violation), the Discord
    card should still show a real reason instead of falling back to '-'."""
    nim = MockNIM(responses={"structured_default": {
        "body": "Test post body.", "thread_posts": None, "claims": [],
    }})
    writer = Writer(nim, repo, VoiceProfile())
    why_me = {"why_this_account": "The account documents real building failures"}
    result = writer.write("x", "single", "some angle", "Personal", [], [],
                          why_me=why_me)
    assert result["why_this_exists"] == why_me["why_this_account"]
    assert result["why_this_exists"] != ""


def test_review_card_shows_the_intended_angle_not_just_the_output():
    """A reviewer needs to see the editorial angle the post was supposed to
    make, to judge whether the model actually honored it or drifted back to
    a bare fact restatement (the exact failure mode this file guards
    against)."""
    from src.notifications.discord_review import format_review_card
    post = {"post_uid": "X-2026-TEST", "platform": "x", "format": "single",
            "pillar": "Personal", "current_version": 1}
    version = {"body": "Land use beats climate as a predictor.",
              "why_this_exists": "because"}
    card = format_review_card(post, version, {}, 90, "2026-09-09T19:30:00+05:30",
                              angle=REAL_ANGLE)
    assert REAL_ANGLE in card
    assert "ANGLE" in card
