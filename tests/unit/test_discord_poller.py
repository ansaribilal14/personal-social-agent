"""Discord-native review intake: one-click card reactions + typed commands.

NO test talks to a live Discord API; the client is a scripted fake. The DB
(in-memory SQLite) stays the only authority; these tests prove that reaction
approvals and plain-text messages drive the SAME state machine with the same
safety properties as the GitHub issue surface (version gating, authorization,
no corrupted transitions).
"""
from __future__ import annotations

import pytest

from src.review.discord_poller import (CARD_KEY_PREFIX, POLL_CURSOR_KEY,
                                       DiscordReviewPoller)
from src.state.machine import State


class FakeDiscord:
    """Scripted Discord REST stand-in (no network)."""

    def __init__(self):
        self.posted: list[tuple[str, str]] = []
        self.reactions: dict[tuple[str, str], list[dict]] = {}
        self.history: list[dict] = []
        self.owner: str | None = None

    def post_message(self, channel_id: str, content: str,
                     embed: dict | None = None) -> str | None:
        self.posted.append((channel_id, content))
        return f"m{len(self.posted):04d}"

    def fetch_messages(self, channel_id: str, limit: int = 50,
                       after_id: str | None = None) -> list[dict]:
        msgs = [m for m in self.history
                if after_id is None or str(m["id"]) > str(after_id)]
        return sorted(msgs, key=lambda m: str(m["id"]), reverse=True)[:limit]

    def reaction_users(self, channel_id: str, message_id: str, emoji: str,
                       limit: int = 25) -> list[dict]:
        return list(self.reactions.get((message_id, emoji), []))

    def remove_own_reaction(self, channel_id: str, message_id: str, emoji: str) -> bool:
        return True

    def guild_owner_id(self, guild_id: str) -> str | None:
        return self.owner


def make_waiting_post(repo, platform: str = "x") -> dict:
    """A post at WAITING_APPROVAL with one content version (v1)."""
    idea = repo.save_idea("idea " + platform, "AI", {}, 80, {})
    pid = repo.create_post(platform, "single", "AI", idea, "angle")
    repo.add_version(pid, "body of " + platform, None, {}, {}, [])
    repo.move_state(pid, State.CANDIDATE)
    repo.move_state(pid, State.RESEARCHED)
    repo.move_state(pid, State.STRATEGIZED)
    repo.move_state(pid, State.DRAFTED)
    repo.move_state(pid, State.QUALITY_REVIEW)
    repo.move_state(pid, State.QUALITY_PASSED)
    repo.move_state(pid, State.EDITORIALLY_RANKED)
    repo.move_state(pid, State.WAITING_APPROVAL)
    return repo.get_post(pid)


@pytest.fixture()
def waiting_post(repo):
    return make_waiting_post(repo)


def make_poller(repo, discord, allowed=("111", "reviewer")) -> DiscordReviewPoller:
    return DiscordReviewPoller(repo, discord, set(allowed), "chan-review")


def register_card(repo, post, message_id: str, version: int | None = None):
    repo.set_setting(f"{CARD_KEY_PREFIX}{post['post_uid']}",
                     {"message_id": message_id,
                      "version": version if version is not None
                      else post["current_version"]})


# --------------------------------------------------------------- card reactions
def test_reaction_approve_moves_state(repo, waiting_post):
    discord = FakeDiscord()
    register_card(repo, waiting_post, "card-1")
    discord.reactions[("card-1", "\u2705")] = [{"id": "111", "username": "ops"}]
    summary = make_poller(repo, discord).process()
    assert summary["approved"] == [waiting_post["post_uid"]]
    assert repo.get_post(waiting_post["id"])["state"] == State.APPROVED.value
    # card consumed -> a repeat poll cannot approve anything again
    assert repo.get_setting(f"{CARD_KEY_PREFIX}{waiting_post['post_uid']}") is None
    summary2 = make_poller(repo, discord).process()
    assert summary2["approved"] == []


def test_reaction_approve_is_version_gated(repo, waiting_post):
    """The card showed v1; the post has iterated to v2 -> refuse, never approve."""
    discord = FakeDiscord()
    register_card(repo, waiting_post, "card-1", version=1)
    # simulate an iteration: post is back at WAITING_APPROVAL with v2
    repo.add_version(waiting_post["id"], "body v2", None, {}, [], [])
    repo.move_state(waiting_post["id"], State.ITERATING)
    repo.move_state(waiting_post["id"], State.REVISING)
    repo.move_state(waiting_post["id"], State.DRAFTED)
    repo.move_state(waiting_post["id"], State.QUALITY_REVIEW)
    repo.move_state(waiting_post["id"], State.QUALITY_PASSED)
    repo.move_state(waiting_post["id"], State.EDITORIALLY_RANKED)
    repo.move_state(waiting_post["id"], State.WAITING_APPROVAL)
    discord.reactions[("card-1", "\u2705")] = [{"id": "111", "username": "ops"}]
    summary = make_poller(repo, discord).process()
    assert summary["approved"] == []
    assert repo.get_post(waiting_post["id"])["state"] == State.WAITING_APPROVAL.value


def test_reaction_reject(repo, waiting_post):
    discord = FakeDiscord()
    register_card(repo, waiting_post, "card-2")
    discord.reactions[("card-2", "\u274c")] = [{"id": "111", "username": "ops"}]
    summary = make_poller(repo, discord).process()
    assert summary["rejected"] == [waiting_post["post_uid"]]
    assert repo.get_post(waiting_post["id"])["state"] == State.REJECTED.value


def test_unauthorized_reaction_is_ignored(repo, waiting_post):
    discord = FakeDiscord()
    register_card(repo, waiting_post, "card-3")
    discord.reactions[("card-3", "\u2705")] = [{"id": "999", "username": "stranger"}]
    summary = make_poller(repo, discord).process()
    assert summary["approved"] == []
    assert repo.get_post(waiting_post["id"])["state"] == State.WAITING_APPROVAL.value
    # the card stays active for a legit reviewer
    assert repo.get_setting(f"{CARD_KEY_PREFIX}{waiting_post['post_uid']}") is not None


# ------------------------------------------------------------- typed commands
def seed_history(repo, discord, messages: list[dict]) -> None:
    """Simulate an already-initialized cursor, then add fresh messages."""
    repo.set_setting(POLL_CURSOR_KEY, "100")
    discord.history = messages


def test_message_approve(repo, waiting_post):
    discord = FakeDiscord()
    seed_history(repo, discord, [{"id": "200", "content":
                                  f"approve {waiting_post['post_uid']}",
                                  "author": {"id": "111", "username": "ops"}}])
    summary = make_poller(repo, discord).process()
    assert summary["approved"] == [waiting_post["post_uid"]]
    assert repo.get_post(waiting_post["id"])["state"] == State.APPROVED.value
    assert repo.get_setting(POLL_CURSOR_KEY) == "200"


def test_message_approve_with_slash_prefix(repo, waiting_post):
    discord = FakeDiscord()
    seed_history(repo, discord, [{"id": "201", "content":
                                  f"/approve {waiting_post['post_uid']}",
                                  "author": {"id": "111", "username": "ops"}}])
    summary = make_poller(repo, discord).process()
    assert summary["approved"] == [waiting_post["post_uid"]]


def test_message_reject_with_reason(repo, waiting_post):
    discord = FakeDiscord()
    seed_history(repo, discord, [{"id": "202", "content":
                                  f"reject {waiting_post['post_uid']} too salesy",
                                  "author": {"id": "111", "username": "ops"}}])
    summary = make_poller(repo, discord).process()
    assert summary["rejected"] == [waiting_post["post_uid"]]
    assert repo.get_post(waiting_post["id"])["state"] == State.REJECTED.value


def test_message_iterate_runs_callback_and_marks_state(repo, waiting_post):
    discord = FakeDiscord()
    calls: list[tuple[int, str, str]] = []
    seed_history(repo, discord, [{"id": "203", "content":
                                  f"iterate {waiting_post['post_uid']} make the "
                                  f"hook punchier",
                                  "author": {"id": "111", "username": "ops"}}])
    poller = DiscordReviewPoller(repo, discord, {"111"}, "chan-review",
                                 run_iterate=lambda pid, ins, actor:
                                 calls.append((pid, ins, actor)))
    summary = poller.process()
    assert summary["iterated"] and calls
    assert calls[0][0] == waiting_post["id"]
    assert "hook punchier" in calls[0][1]
    assert repo.get_post(waiting_post["id"])["state"] == State.ITERATING.value


def test_message_from_unauthorized_author_is_ignored(repo, waiting_post):
    discord = FakeDiscord()
    seed_history(repo, discord, [{"id": "204", "content":
                                  f"approve {waiting_post['post_uid']}",
                                  "author": {"id": "666", "username": "random"}}])
    summary = make_poller(repo, discord).process()
    assert summary["approved"] == []
    assert repo.get_post(waiting_post["id"])["state"] == State.WAITING_APPROVAL.value


def test_ordinary_chatter_never_touches_state(repo, waiting_post):
    discord = FakeDiscord()
    seed_history(repo, discord, [
        {"id": "205", "content": "this looks great lol", "author":
         {"id": "111", "username": "ops"}},
        {"id": "206", "content": "approved!! nice", "author":
         {"id": "111", "username": "ops"}},   # not an exact command -> ignored
    ])
    summary = make_poller(repo, discord).process()
    assert summary["approved"] == []
    assert repo.get_post(waiting_post["id"])["state"] == State.WAITING_APPROVAL.value
    # cursor still advances so history is not rescanned forever
    assert repo.get_setting(POLL_CURSOR_KEY) == "206"


def test_first_poll_sets_cursor_without_replaying_history(repo, waiting_post):
    """A fresh deployment must not process old channel history."""
    discord = FakeDiscord()
    discord.history = [{"id": "300", "content": f"approve {waiting_post['post_uid']}",
                        "author": {"id": "111", "username": "ops"}}]
    summary = make_poller(repo, discord).process()
    assert summary["approved"] == []
    assert repo.get_setting(POLL_CURSOR_KEY) == "300"
    assert repo.get_post(waiting_post["id"])["state"] == State.WAITING_APPROVAL.value


def test_unknown_uid_gets_a_reply(repo):
    discord = FakeDiscord()
    seed_history(repo, discord, [{"id": "207", "content": "approve NOPE-2099-ZZZZZ",
                                  "author": {"id": "111", "username": "ops"}}])
    summary = make_poller(repo, discord).process()
    assert summary["approved"] == []
    assert any("NOPE-2099-ZZZZZ" in text for _, text in discord.posted)


# ------------------------------------------------------------- authorization
def test_guild_owner_resolved_as_authorized(repo, waiting_post, monkeypatch):
    monkeypatch.setenv("DISCORD_GUILD_ID", "guild-1")
    monkeypatch.delenv("DISCORD_AUTHORIZED_USERS", raising=False)
    discord = FakeDiscord()
    discord.owner = "555"
    allowed = DiscordReviewPoller.resolve_authorized(repo, discord)
    assert "555" in allowed
    seed_history(repo, discord, [{"id": "208", "content":
                                  f"approve {waiting_post['post_uid']}",
                                  "author": {"id": "555", "username": "boss"}}])
    poller = DiscordReviewPoller(repo, discord, allowed, "chan-review")
    summary = poller.process()
    assert summary["approved"] == [waiting_post["post_uid"]]


def test_no_authorized_users_refuses_to_act(repo, waiting_post):
    discord = FakeDiscord()
    register_card(repo, waiting_post, "card-9")
    discord.reactions[("card-9", "\u2705")] = [{"id": "999", "username": "x"}]
    poller = DiscordReviewPoller(repo, discord, set(), "chan-review")
    summary = poller.process()
    assert summary["approved"] == [] and summary["skipped"]
    assert repo.get_post(waiting_post["id"])["state"] == State.WAITING_APPROVAL.value


# ------------------------------------------------- review pipeline integration
def test_review_pipeline_registers_card_without_github(repo, monkeypatch):
    """Discord-only mode: no issue needed, card sent + registered for reactions."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_REVIEW", "chan-review")
    from src.pipeline.ops import ReviewPipeline
    discord = FakeDiscord()
    make_waiting_post(repo)
    result = ReviewPipeline(repo, None, discord_client=discord).run()
    assert result["review_cards_sent"] == 1 and result["review_issues"] == 0
    # second run must not duplicate the card (dedup by version)
    result2 = ReviewPipeline(repo, None, discord_client=discord).run()
    assert result2["review_cards_sent"] == 0
    card = repo.get_setting(f"{CARD_KEY_PREFIX}"
                            f"{repo.posts_in_state(State.WAITING_APPROVAL.value)[0]['post_uid']}")
    assert card and card["message_id"].startswith("m")


def test_review_pipeline_survives_discord_outage(repo, monkeypatch):
    """Discord down -> no crash, no card registered; next run retries."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_REVIEW", "chan-review")

    class DeadDiscord:
        def post_message(self, *a, **k):
            raise RuntimeError("discord down")

    from src.pipeline.ops import ReviewPipeline
    make_waiting_post(repo)
    result = ReviewPipeline(repo, None, discord_client=DeadDiscord()).run()
    assert result["review_cards_sent"] == 1   # attempted
    post = repo.posts_in_state(State.WAITING_APPROVAL.value)[0]
    assert repo.get_setting(f"{CARD_KEY_PREFIX}{post['post_uid']}") is None


# ------------------------------------------------- reject -> instant regeneration
def test_message_reject_triggers_regenerate_once(repo, waiting_post):
    """A typed reject dispatches ONE full regeneration per poll pass, with the
    rejection reasons as anti-guidance payload."""
    discord = FakeDiscord()
    calls = []

    def run_regenerate(reason: str, actor: str) -> dict:
        calls.append({"reason": reason, "actor": actor})
        return {"dispatched": True, "detail": "HTTP 204"}

    poller = make_poller(repo, discord)
    poller.run_regenerate = run_regenerate
    discord.history = [{"id": "2001", "content":
                        f"reject {waiting_post['post_uid']} sounds like an essay",
                        "author": {"id": "111", "username": "ops"}}]
    # first poll only sets the cursor (never replays history)
    poller.process()
    discord.history.append({"id": "2002", "content":
                            f"reject {waiting_post['post_uid']} no real value",
                            "author": {"id": "111", "username": "ops"}})
    summary = poller.process()
    assert summary["rejected"] == [waiting_post["post_uid"]]
    assert repo.get_post(waiting_post["id"])["state"] == State.REJECTED.value
    assert len(calls) == 1
    assert "no real value" in calls[0]["reason"]
    assert summary["regenerated"] == {"dispatched": True, "detail": "HTTP 204"}
    # the confirmation about regeneration is posted to the channel
    assert any("regeneration started now" in p[1].lower() for p in discord.posted)


def test_reaction_reject_triggers_regenerate_once(repo, waiting_post):
    """Card-reaction rejects also trigger the instant regeneration."""
    discord = FakeDiscord()
    register_card(repo, waiting_post, "card-9")
    discord.reactions[("card-9", "\u274c")] = [{"id": "111", "username": "ops"}]
    calls = []
    poller = make_poller(repo, discord)
    poller.run_regenerate = lambda reason, actor: (
        calls.append(reason) or {"dispatched": True, "detail": "ok"})
    summary = poller.process()
    assert summary["rejected"] == [waiting_post["post_uid"]]
    assert len(calls) == 1 and waiting_post["post_uid"] in calls[0]
    assert summary["regenerated"]["dispatched"] is True


def test_no_rejects_means_no_regeneration(repo, waiting_post):
    discord = FakeDiscord()
    calls = []
    poller = make_poller(repo, discord)
    poller.run_regenerate = lambda reason, actor: (
        calls.append(reason) or {"dispatched": True, "detail": "ok"})
    # first poll only sets the cursor (never replays history)
    discord.history = [{"id": "3001", "content": "ordinary chatter",
                        "author": {"id": "111", "username": "ops"}}]
    poller.process()
    discord.history.append({"id": "3002", "content":
                            f"approve {waiting_post['post_uid']}",
                            "author": {"id": "111", "username": "ops"}})
    discord.history.append({"id": "3003", "content": "more chatter",
                            "author": {"id": "111", "username": "ops"}})
    summary = poller.process()
    assert summary["approved"] == [waiting_post["post_uid"]]
    assert calls == []
    assert summary["regenerated"] is None


def test_regen_callback_failure_never_breaks_poll(repo, waiting_post):
    """A crashing regeneration callback is absorbed; the reject still lands."""
    discord = FakeDiscord()
    poller = make_poller(repo, discord)
    def boom(reason, actor):
        raise RuntimeError("actions api down")
    poller.run_regenerate = boom
    # first poll only sets the cursor (never replays history)
    discord.history = [{"id": "4001", "content": "ordinary chatter",
                        "author": {"id": "111", "username": "ops"}}]
    poller.process()
    discord.history.append({"id": "4002", "content":
                            f"reject {waiting_post['post_uid']} stale",
                            "author": {"id": "111", "username": "ops"}})
    discord.history.append({"id": "4003", "content": "ignored filler",
                            "author": {"id": "111", "username": "ops"}})
    summary = poller.process()
    assert summary["rejected"] == [waiting_post["post_uid"]]
    assert summary["regenerated"]["dispatched"] is False
    assert repo.get_post(waiting_post["id"])["state"] == State.REJECTED.value
