"""State machine: every valid transition allowed, every invalid one refused
(spec section 7: test every valid and invalid transition)."""
import pytest

from src.state.machine import (TRANSITIONS, InvalidTransition, State, can_transition,
                               is_terminal, path_to_approval, transition)

VALID_STATES = list(State)


def test_every_valid_transition_succeeds():
    checked = 0
    for src, targets in TRANSITIONS.items():
        for tgt in targets:
            assert transition(src, tgt) == tgt
            assert can_transition(src, tgt)
            checked += 1
    assert checked > 20  # the table is fully exercised


def test_every_invalid_transition_raises():
    for src, targets in TRANSITIONS.items():
        for tgt in VALID_STATES:
            if tgt not in targets:
                with pytest.raises(InvalidTransition):
                    transition(src, tgt)
                assert not can_transition(src, tgt)


def test_no_path_to_approval_except_via_waiting_approval():
    approved = State.APPROVED
    sources = {src for src, tgts in TRANSITIONS.items() if approved in tgts}
    assert sources == {State.WAITING_APPROVAL}


def test_no_path_to_published_except_via_publishing():
    sources = {src for src, tgts in TRANSITIONS.items() if State.PUBLISHED in tgts}
    assert sources == {State.PUBLISHING}


def test_approval_cannot_jump_straight_to_publishing_terminal():
    # APPROVED -> PUBLISHING is allowed only through the publish pipeline
    # (SCHEDULED or APPROVED states), never straight to PUBLISHED.
    assert State.PUBLISHED not in TRANSITIONS[State.APPROVED]


def test_full_legal_path_exists():
    path = path_to_approval()
    for cur, nxt in zip(path, path[1:]):
        assert can_transition(cur, nxt)


def test_terminal_states():
    for s in (State.PUBLISHED, State.REJECTED, State.CANCELLED, State.BLOCKED):
        assert is_terminal(s)
    assert not is_terminal(State.WAITING_APPROVAL)


def test_repository_rejects_invalid_transition(repo):
    from src.state.machine import State as S
    pid = repo.create_post("x", "single", "AI", None, None, state=S.DISCOVERED)
    with pytest.raises(InvalidTransition):
        repo.move_state(pid, S.APPROVED)   # DISCOVERED -> APPROVED must fail
    assert repo.get_post(pid)["state"] == S.DISCOVERED.value
