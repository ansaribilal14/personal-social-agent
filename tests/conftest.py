"""Shared fixtures: in-memory DB + deterministic mocks for every integration.

NO test talks to a live API and NO test can publish anything real (spec 55).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.connection import SQLiteDatabase  # noqa: E402
from src.db.repository import Repository  # noqa: E402


@pytest.fixture()
def db():
    database = SQLiteDatabase(":memory:")
    database.migrate()
    yield database
    database.close()


@pytest.fixture()
def repo(db):
    return Repository(db)


@pytest.fixture()
def enabled_repo(repo):
    """Repository with the kill switch ON (for publish-path tests)."""
    repo.set_setting("SOCIAL_AUTOMATION_ENABLED", True)
    return repo


class MockNIM:
    """Deterministic NIM stand-in driven by a scripted response map."""

    def __init__(self, responses: dict | None = None, fail_times: dict | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str]] = []
        self.fail_times = dict(fail_times or {})   # method -> remaining failures
        self.fail_exception: dict = {}

    def chat(self, system: str, user: str, **kwargs) -> str:
        self.calls.append(("chat", user[:80]))
        if self.fail_times.get("chat", 0) > 0:
            self.fail_times["chat"] -= 1
            raise self.fail_exception.get("chat", RuntimeError("mock chat failure"))
        return self.responses.get("chat", "ok")

    def chat_structured(self, system: str, user: str, **kwargs) -> dict:
        self.calls.append(("structured", user[:80]))
        if self.fail_times.get("structured", 0) > 0:
            self.fail_times["structured"] -= 1
            raise self.fail_exception.get("structured", RuntimeError("mock failure"))
        for key, value in self.responses.items():
            if key != "chat" and key in user:
                if isinstance(value, str):
                    return json.loads(value)
                return json.loads(json.dumps(value))
        # deep-copy: callers mutate the returned dict (setdefault fallbacks);
        # sharing one dict across calls leaks mutations between tests.
        default = self.responses.get("structured_default", {"ok": True})
        return json.loads(json.dumps(default))


GOOD_IDEA = {
    "statement": "AI agents increasingly use tools, but knowing when NOT to use a tool is the real skill",
    "angle": "The bottleneck isn't tool availability; it is judgment about when tools help",
    "pillar": "AI",
    "evaluation": {"novelty": 80, "interestingness": 85, "relevance": 75,
                   "personal_fit": 82, "discussion_value": 78,
                   "factual_confidence": 70, "content_potential": 80,
                   "timeliness": 72},
    "why_me": {
        "why_interesting": "Everyone demos tool use; almost nobody shows the failure modes of overusing tools",
        "why_now": "Agent frameworks are shipping weekly and tool budgets are a live engineering question",
        "why_this_angle": "It inverts the popular narrative instead of repeating it",
        "why_this_platform": "X rewards a sharp single claim; the argument fits a short thread",
        "why_this_account": "The account is a builder documenting real agent failures, so it has standing",
    },
    "platform": "x",
    "format": "single",
}


@pytest.fixture()
def mock_nim():
    nim = MockNIM()
    nim.responses["structured_default"] = {
        "ideas": [GOOD_IDEA],
    }
    return nim


class MockBuffer:
    """Mock Buffer client - records calls; can simulate failures."""

    def __init__(self, fail_first: int = 0):
        self.calls: list[dict] = []
        self.fail_first = fail_first
        self.counter = 1000

    def verify(self):
        return {"id": "acct-1", "displayName": "Test"}

    def channels(self):
        return [{"id": "ch-x", "service": "x", "displayName": "X main"},
                {"id": "ch-th", "service": "threads", "displayName": "Threads main"}]

    def channel_for(self, service):
        for ch in self.channels():
            if ch["service"] == service:
                return ch
        from integrations.buffer.client import BufferValidationError
        raise BufferValidationError(f"no channel for {service}")

    def create_post(self, channel_id, text, scheduled_at_iso=None, thread_texts=None):
        if self.fail_first > 0:
            self.fail_first -= 1
            from integrations.buffer.client import BufferUnavailable
            raise BufferUnavailable("simulated buffer outage")
        self.counter += 1
        self.calls.append({"channel_id": channel_id, "text": text,
                           "scheduled_at": scheduled_at_iso,
                           "thread": thread_texts})
        return {"buffer_post_id": f"buf-{self.counter}",
                "scheduled_at": scheduled_at_iso or "2026-01-01T00:00:00+00:00"}

    def get_post(self, buffer_post_id):
        return {"id": buffer_post_id, "status": "scheduled"}

    def post_metrics(self, buffer_post_id):
        return {"impressions": 1200, "likes": 14, "replies": 2, "reposts": 1}


class MockGitHub:
    """In-memory GitHub issue surface."""

    def __init__(self):
        self.issues: dict[int, dict] = {}
        self.comments: dict[int, list[dict]] = {}
        self.counter = 500

    def create_issue(self, title, body, labels=None):
        self.counter += 1
        self.issues[self.counter] = {"number": self.counter, "title": title,
                                     "body": body, "labels": labels or [],
                                     "state": "open"}
        return self.issues[self.counter]

    def add_comment(self, issue_number, body):
        self.comments.setdefault(issue_number, []).append({"body": body})
        return {"id": len(self.comments[issue_number])}

    def list_comments(self, issue_number, since=None):
        return self.comments.get(issue_number, [])

    def update_issue(self, issue_number, state=None, title=None, body=None, labels=None):
        issue = self.issues[issue_number]
        if state:
            issue["state"] = state
        if labels is not None:
            issue["labels"] = labels
        return issue

    def get_issue(self, issue_number):
        return self.issues[issue_number]

    def ensure_labels(self, labels):
        pass


class MockDiscord:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, content):
        self.sent.append(content)
        return True

    def send_review_card(self, card):
        self.sent.append(card.get("description") or card.get("title"))
        return True


@pytest.fixture()
def mock_github():
    return MockGitHub()


@pytest.fixture()
def mock_buffer():
    return MockBuffer()


@pytest.fixture()
def mock_discord():
    return MockDiscord()
