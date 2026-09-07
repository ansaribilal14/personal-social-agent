"""Offline unit tests for the live-verified integration shapes.

Covers: Buffer GraphQL payload construction (mode/schedulingType REQUIRED),
thread staggering, metric normalization, service aliasing, NIM thinking-off
payload + <think> stripping, Discord bot-token routing. No network access.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from integrations.buffer.client import BufferClient, BufferValidationError
from integrations.nvidia.client import NIMClient
from integrations.discord.client import DiscordClient, channel_for_purpose


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Records requests; answers from a queue of payloads or a callable."""
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse(self.responder(json))


# ------------------------------------------------------------------ Buffer
def test_buffer_posts_to_slash_graphql():
    session = FakeSession(lambda req: {"data": {"account": {"id": "a1"}}})
    client = BufferClient(access_token="tok", session=session)
    client.verify()
    assert session.calls[0]["url"] == "https://api.buffer.com/graphql"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_buffer_post_input_scheduled_requires_mode_and_type():
    client = BufferClient(access_token="tok")
    payload = client._post_input("ch1", "hello", "2026-09-10T09:00:00+00:00")
    assert payload["mode"] == "customScheduled"
    assert payload["schedulingType"] == "automatic"
    assert payload["dueAt"] == "2026-09-10T09:00:00+00:00"
    payload2 = client._post_input("ch1", "hello", None)
    assert payload2["mode"] == "addToQueue"
    assert "dueAt" not in payload2


def test_buffer_create_single_post_success():
    session = FakeSession(lambda req: {"data": {"createPost": {
        "post": {"id": "p1", "status": "scheduled", "dueAt": "2026-09-10T09:00:00Z"}}}})
    client = BufferClient(access_token="tok", session=session)
    result = client.create_post("ch1", "hello", "2026-09-10T09:00:00+00:00")
    assert result["buffer_post_id"] == "p1"
    assert result["buffer_post_ids"] == ["p1"]
    assert result["scheduled_at"] == "2026-09-10T09:00:00Z"
    sent = session.calls[0]["json"]["variables"]["input"]
    assert sent["channelId"] == "ch1"
    assert sent["text"] == "hello"


def test_buffer_thread_staggers_due_at_and_preserves_order():
    slot = "2026-09-10T09:00:00+00:00"
    ids = iter([f"p{i}" for i in range(3)])

    def responder(req):
        pid = next(ids)
        due = req["variables"]["input"].get("dueAt")
        return {"data": {"createPost": {"post": {"id": pid, "status": "scheduled",
                                                 "dueAt": due}}}}

    session = FakeSession(responder)
    client = BufferClient(access_token="tok", session=session)
    result = client.create_post("ch1", "one", slot, thread_texts=["one", "two", "three"])
    assert result["buffer_post_ids"] == ["p0", "p1", "p2"]
    base = datetime.fromisoformat(slot)
    due_1 = datetime.fromisoformat(session.calls[1]["json"]["variables"]["input"]["dueAt"])
    due_2 = datetime.fromisoformat(session.calls[2]["json"]["variables"]["input"]["dueAt"])
    assert (due_1 - base).total_seconds() == 90
    assert (due_2 - base).total_seconds() == 180
    # order preserved verbatim
    texts = [c["json"]["variables"]["input"]["text"] for c in session.calls]
    assert texts == ["one", "two", "three"]


def test_buffer_mutation_error_surfaces_as_validation_error():
    session = FakeSession(lambda req: {"data": {"createPost": {"message": "bad channel"}}})
    client = BufferClient(access_token="tok", session=session)
    with pytest.raises(BufferValidationError):
        client.create_post("chX", "hello", None)


def test_buffer_metrics_normalization():
    session = FakeSession(lambda req: {"data": {"post": {"id": "p1", "metrics": [
        {"name": "Reactions", "value": 1}, {"name": "Impressions", "value": 10},
        {"name": "Eng. Rate", "value": 2.5}, {"name": "Reposts", "value": 0}]}}})
    client = BufferClient(access_token="tok", session=session)
    metrics = client.post_metrics("p1")
    assert metrics == {"reactions": 1, "impressions": 10,
                       "eng_rate": 2.5, "reposts": 0}


def test_buffer_channel_alias_x_maps_to_twitter():
    def responder(req):
        if "organizations" in req["query"]:
            return {"data": {"account": {"organizations": [{"id": "org1", "name": "o"}]}}}
        return {"data": {"channels": [
            {"id": "c1", "service": "twitter", "serviceId": "s", "displayName": "me",
             "isDisconnected": False, "isQueuePaused": False}]}}

    client = BufferClient(access_token="tok", session=FakeSession(responder))
    ch = client.channel_for("x")
    assert ch["service"] == "twitter"


# --------------------------------------------------------------------- NIM
def test_nim_thinking_disabled_for_nemotron(monkeypatch):
    monkeypatch.delenv("NIM_THINKING", raising=False)
    extra = NIMClient._extra_payload("nvidia/nemotron-3.5-lightning-30b-a3b")
    assert extra == {"chat_template_kwargs": {"thinking": False}}


def test_nim_no_extra_payload_for_non_nemotron(monkeypatch):
    monkeypatch.delenv("NIM_THINKING", raising=False)
    assert NIMClient._extra_payload("meta/llama-3.1-8b-instruct") == {}


def test_nim_thinking_respected_when_enabled(monkeypatch):
    monkeypatch.setenv("NIM_THINKING", "true")
    assert NIMClient._extra_payload("nvidia/nemotron-3.5-lightning-30b-a3b") == {}


def test_nim_strips_think_blocks():
    text = "<think>internal reasoning</think>Final answer."
    assert NIMClient._strip_think(text) == "Final answer."


def test_nim_payload_includes_thinking_flag(monkeypatch):
    monkeypatch.delenv("NIM_THINKING", raising=False)
    session = FakeSession(lambda req: {"choices": [{"message": {"content": "ok"}}]})
    client = NIMClient(api_key="k", session=session)
    out = client.chat("sys", "user")
    assert out == "ok"
    sent = session.calls[0]["json"]
    assert sent["chat_template_kwargs"] == {"thinking": False}
    assert sent["model"].startswith("nvidia/nemotron")


# ----------------------------------------------------------------- Discord
def test_discord_bot_token_channel_routing():
    session = FakeSession(lambda req: {})
    client = DiscordClient(webhook_url=None, bot_token="BOT", session=session)
    ok = client.send_to_channel("123", "hello")
    assert ok is True
    call = session.calls[0]
    assert call["url"].endswith("/channels/123/messages")
    assert call["headers"]["Authorization"] == "Bot BOT"


def test_discord_channel_purpose_env(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "BOT")
    monkeypatch.setenv("DISCORD_CHANNEL_REVIEW", "42")
    assert channel_for_purpose("review") == "42"
    assert channel_for_purpose("errors") is None


def test_discord_send_prefers_webhook():
    session = FakeSession(lambda req: {})
    client = DiscordClient(webhook_url="https://discord.invalid/hook",
                           bot_token=None, session=session)
    client.send("hello")
    assert session.calls[0]["url"] == "https://discord.invalid/hook"


def test_discord_send_falls_back_to_alerts_channel(monkeypatch):
    monkeypatch.setenv("DISCORD_CHANNEL_ALERTS", "77")
    session = FakeSession(lambda req: {})
    client = DiscordClient(webhook_url=None, bot_token="BOT", session=session)
    client.send("hello")
    assert session.calls[0]["url"].endswith("/channels/77/messages")
