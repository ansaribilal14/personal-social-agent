"""Explicit content state machine (spec section 7).

Rules enforced here:
- every transition is declared, none inferred
- invalid transitions raise and never silently pass
- no transition can jump over WAITING_APPROVAL -> APPROVED without an approval event
"""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    DISCOVERED = "DISCOVERED"
    CANDIDATE = "CANDIDATE"
    RESEARCHED = "RESEARCHED"
    STRATEGIZED = "STRATEGIZED"
    DRAFTED = "DRAFTED"
    QUALITY_REVIEW = "QUALITY_REVIEW"
    QUALITY_FAILED = "QUALITY_FAILED"
    REVISING = "REVISING"
    QUALITY_PASSED = "QUALITY_PASSED"
    EDITORIALLY_RANKED = "EDITORIALLY_RANKED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    ITERATING = "ITERATING"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    PUBLISH_FAILED = "PUBLISH_FAILED"


# Declared transition table: from-state -> set of allowed to-states.
# NOTE: there is deliberately NO path that reaches APPROVED except from
# WAITING_APPROVAL, and no path that reaches PUBLISHED except via PUBLISHING.
TRANSITIONS: dict[State, set[State]] = {
    State.DISCOVERED: {State.CANDIDATE, State.CANCELLED},
    State.CANDIDATE: {State.RESEARCHED, State.CANCELLED},
    State.RESEARCHED: {State.STRATEGIZED, State.CANCELLED},
    State.STRATEGIZED: {State.DRAFTED, State.CANCELLED},
    State.DRAFTED: {State.QUALITY_REVIEW},
    State.QUALITY_REVIEW: {State.QUALITY_PASSED, State.QUALITY_FAILED},
    State.QUALITY_FAILED: {State.REVISING, State.BLOCKED, State.CANCELLED},
    State.REVISING: {State.QUALITY_REVIEW, State.DRAFTED},
    State.QUALITY_PASSED: {State.EDITORIALLY_RANKED, State.CANCELLED},
    State.EDITORIALLY_RANKED: {State.WAITING_APPROVAL, State.CANCELLED},
    State.WAITING_APPROVAL: {State.APPROVED, State.ITERATING, State.REJECTED, State.CANCELLED},
    State.ITERATING: {State.REVISING, State.WAITING_APPROVAL, State.REJECTED, State.CANCELLED},
    State.APPROVED: {State.SCHEDULED, State.PUBLISHING, State.CANCELLED},
    State.SCHEDULED: {State.PUBLISHING, State.CANCELLED},
    State.PUBLISHING: {State.PUBLISHED, State.PUBLISH_FAILED, State.SCHEDULED},
    State.PUBLISH_FAILED: {State.PUBLISHING, State.BLOCKED},
    State.REJECTED: set(),
    State.CANCELLED: set(),
    State.BLOCKED: set(),
    State.PUBLISHED: set(),
}


class InvalidTransition(Exception):
    def __init__(self, current: State | str, target: State | str):
        self.current, self.target = current, target
        super().__init__(f"invalid transition: {current} -> {target}")


def can_transition(current: State | str, target: State | str) -> bool:
    cur = State(current)
    tgt = State(target)
    return tgt in TRANSITIONS[cur]


def transition(current: State | str, target: State | str) -> State:
    """Validate and perform a transition. Raises InvalidTransition otherwise."""
    cur, tgt = State(current), State(target)
    if tgt not in TRANSITIONS[cur]:
        raise InvalidTransition(cur, tgt)
    return tgt


def is_terminal(state: State | str) -> bool:
    return len(TRANSITIONS[State(state)]) == 0


def path_to_approval() -> list[State]:
    """The only legal route to APPROVED - used by tests to prove no bypass exists."""
    return [
        State.DISCOVERED, State.CANDIDATE, State.RESEARCHED, State.STRATEGIZED,
        State.DRAFTED, State.QUALITY_REVIEW, State.QUALITY_PASSED,
        State.EDITORIALLY_RANKED, State.WAITING_APPROVAL, State.APPROVED,
    ]
