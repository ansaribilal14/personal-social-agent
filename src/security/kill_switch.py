"""Global kill switch (spec section 12).

When SOCIAL_AUTOMATION_ENABLED is false (or DB unreadable):
    Research / Drafting / Analysis / Review  -> ALLOWED
    Scheduling / Publishing                  -> BLOCKED

The switch is checked IMMEDIATELY before scheduling and immediately before
publishing. An earlier check is never treated as sufficient.
"""
from __future__ import annotations

from src.config import get_config
from src.db.repository import Repository


class PublishingBlocked(Exception):
    """Raised when scheduling/publishing is attempted while the switch is off
    or the authoritative state cannot be read."""


def _switch_enabled(repo: Repository) -> bool | None:
    try:
        return bool(repo.get_setting("SOCIAL_AUTOMATION_ENABLED", False))
    except Exception:
        # If database state is unavailable: STOP (spec section 3).
        return None


def is_publishing_enabled(repo: Repository) -> bool:
    return _switch_enabled(repo) is True


def assert_scheduling_allowed(repo: Repository) -> None:
    cfg = get_config()
    if not cfg.security.get("kill_switch", {}).get("default") == "enabled" and \
            not is_publishing_enabled(repo):
        raise PublishingBlocked(
            "kill switch: scheduling blocked (SOCIAL_AUTOMATION_ENABLED is false "
            "or state unavailable)")


def assert_publishing_allowed(repo: Repository) -> None:
    """Re-read the switch from the database at call time. Never cached."""
    if not is_publishing_enabled(repo):
        raise PublishingBlocked(
            "kill switch: publishing blocked (SOCIAL_AUTOMATION_ENABLED is false "
            "or state unavailable)")


def enable(repo: Repository) -> None:
    repo.set_setting("SOCIAL_AUTOMATION_ENABLED", True)
    repo.log_event("kill_switch.enabled", severity="warn")


def disable(repo: Repository) -> None:
    repo.set_setting("SOCIAL_AUTOMATION_ENABLED", False)
    repo.log_event("kill_switch.disabled", severity="warn")
