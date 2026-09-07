"""Publishing pipeline (spec sections 3, 10, 11, 12, 40).

Flow: APPROVED -> outbox (idempotent) -> kill switch re-check -> approval
re-verification (exact version match) -> BUFFER_ACCEPTED -> SCHEDULED ->
publication record. Failure: BUFFER_ERROR with bounded exponential backoff ->
PUBLISH_FAILED -> Discord notification. Content is never regenerated because
publishing failed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from integrations.buffer.client import (BufferAuthError, BufferClient,
                                        BufferError, BufferUnavailable,
                                        BufferValidationError)
from src.config import get_config
from src.db.repository import Repository
from src.security.kill_switch import PublishingBlocked, assert_publishing_allowed
from src.state.machine import State


class PublishRefused(Exception):
    """Approval/state mismatch - the publish attempt is REFUSED, not retried."""


class AlreadyClaimed(Exception):
    """Another publish run claimed this outbox entry - skip (spec 53)."""


class Publisher:
    def __init__(self, repo: Repository, buffer_client: BufferClient | None = None):
        self.repo = repo
        self.client = buffer_client or BufferClient()
        self.cfg = get_config().schedule

    # ---------------------------------------------------------- guard rails
    def _verify_approval(self, post_id: int, version: int) -> None:
        """Explicit-approval verification immediately before publish (spec 3/34).

        Checks: state, an APPROVED event for THIS exact version, version match
        with the latest version, and no later rejection.
        """
        post = self.repo.get_post(post_id)
        if post is None:
            raise PublishRefused(f"post {post_id} missing")
        if post["state"] not in (State.APPROVED.value, State.SCHEDULED.value,
                                 State.PUBLISHING.value):
            raise PublishRefused(f"state {post['state']} is not publishable")
        events = self.repo.approval_history(post_id)
        approved_versions = {e["version"] for e in events if e["action"] == "APPROVED"}
        if version not in approved_versions:
            raise PublishRefused(
                f"version {version} has no APPROVED event (approved: {sorted(approved_versions)})")
        if post["current_version"] != version:
            raise PublishRefused(
                f"stale approval: current version {post['current_version']} != {version}")
        later_rejects = [e for e in events
                         if e["action"] == "REJECTED" and e["version"] >= version]
        if later_rejects:
            raise PublishRefused("later REJECTED event exists")

    # ------------------------------------------------------------ main path
    def enqueue_approved(self, post_id: int) -> int:
        """Called on /approve: create the outbox entry (idempotent)."""
        post = self.repo.get_post(post_id)
        version = post["current_version"]
        ver = self.repo.get_version(post_id, version)
        if post["state"] != State.APPROVED.value:
            raise PublishRefused(f"state {post['state']} is not APPROVED")
        return self.repo.create_outbox_entry(
            post_id, post["platform"], version, ver["body"], ver.get("thread_posts"))

    def process_outbox(self) -> dict:
        """Process due outbox entries. Returns summary counters."""
        summary = {"processed": 0, "scheduled": 0, "failed": 0, "skipped": 0}
        for entry in self.repo.outbox_due():
            summary["processed"] += 1
            try:
                self._publish_entry(entry)
                summary["scheduled"] += 1
            except PublishingBlocked as exc:
                self.repo.set_outbox_status(entry["id"], "PUBLISH_REQUESTED",
                                            error=str(exc))
                self.repo.log_event("publish.blocked_kill_switch",
                                    severity="warn", post_id=entry["post_id"],
                                    payload={"outbox_id": entry["id"]})
                summary["skipped"] += 1
            except AlreadyClaimed:
                summary["skipped"] += 1
            except PublishRefused as exc:
                self.repo.set_outbox_status(entry["id"], "PUBLISH_FAILED",
                                            error=f"REFUSED: {exc}")
                summary["failed"] += 1
                self._notify_failure(entry, str(exc))
            except (BufferAuthError, BufferValidationError) as exc:
                # Not retryable: invalid token / bad request. Fail definitively.
                self.repo.set_outbox_status(entry["id"], "PUBLISH_FAILED",
                                            error=str(exc))
                summary["failed"] += 1
                self._notify_failure(entry, str(exc))
            except (BufferUnavailable, BufferError) as exc:
                self._handle_retryable(entry, exc)
                summary["failed"] += 1
        return summary

    def _publish_entry(self, entry: dict) -> None:
        # Atomic claim: exactly one run may proceed to Buffer (spec 53).
        if not self.repo.claim_outbox_entry(entry["id"]):
            raise AlreadyClaimed(f"outbox {entry['id']} claimed by another run")
        # Kill switch: re-read IMMEDIATELY before publishing (spec 12).
        assert_publishing_allowed(self.repo)
        # Approval verification: immediately before publish (spec 3).
        self._verify_approval(entry["post_id"], entry["version"])

        post = self.repo.get_post(entry["post_id"])
        if post["state"] != State.PUBLISHING.value:  # retries may already be here
            self.repo.move_state(entry["post_id"], State.PUBLISHING, actor="publisher")

        channel = self.client.channel_for(
            get_config().platform(post["platform"])["buffer_service"])
        slot = self._scheduled_slot(entry["post_id"])
        result = self.client.create_post(
            channel["id"], entry["body"], slot, entry.get("thread_posts"))

        self.repo.set_outbox_status(entry["id"], "BUFFER_ACCEPTED",
                                    buffer_post_id=result["buffer_post_id"])
        self.repo.record_publication(entry["post_id"], entry["id"],
                                     entry["platform"], entry["version"],
                                     result["buffer_post_id"])
        self.repo.set_outbox_status(entry["id"], "SCHEDULED",
                                    scheduled_at=result.get("scheduled_at"))
        self.repo.add_schedule(entry["post_id"], entry["platform"],
                               result.get("scheduled_at") or slot or
                               datetime.now(timezone.utc).isoformat())
        # PUBLISHED = accepted and recorded by Buffer; actual platform posting
        # is reconciled from Buffer update status by the maintenance workflow.
        self.repo.move_state(entry["post_id"], State.PUBLISHED, actor="publisher")
        self.repo.log_event("publish.accepted", post_id=entry["post_id"],
                            payload={"buffer_post_id": result["buffer_post_id"],
                                     "buffer_post_ids": result.get("buffer_post_ids") or [result["buffer_post_id"]],
                                     "scheduled_at": result.get("scheduled_at")})

    def _scheduled_slot(self, post_id: int) -> str | None:
        rows = self.repo.db.query(
            "SELECT scheduled_at FROM schedules WHERE post_id=:p "
            "ORDER BY id DESC LIMIT 1", {"p": post_id})
        return rows[0]["scheduled_at"] if rows else None

    def _handle_retryable(self, entry: dict, exc: Exception) -> None:
        retry = self.cfg.get("retry", {})
        max_attempts = int(retry.get("max_attempts", 4))
        base = int(retry.get("backoff_base_seconds", 60))
        factor = int(retry.get("backoff_factor", 2))
        attempts = int(entry.get("attempt_count") or 0) + 1
        if attempts >= max_attempts:
            self.repo.set_outbox_status(entry["id"], "PUBLISH_FAILED", error=str(exc))
            self.repo.move_state(entry["post_id"], State.PUBLISH_FAILED,
                                 actor="publisher")
            self._notify_failure(entry, f"retry exhausted: {exc}")
            return
        next_try = datetime.now(timezone.utc) + timedelta(seconds=base * (factor ** (attempts - 1)))
        self.repo.set_outbox_status(entry["id"], "BUFFER_ERROR", error=str(exc))
        # content returns to SCHEDULED; retry uses the SAME content (spec 40)
        try:
            self.repo.move_state(entry["post_id"], State.SCHEDULED, actor="publisher")
        except Exception:
            pass
        self.repo.log_event("publish.retry_scheduled", severity="warn",
                            post_id=entry["post_id"],
                            payload={"attempt": attempts, "next_attempt": next_try.isoformat(),
                                     "backoff_seconds": base * (factor ** (attempts - 1))})

    def _notify_failure(self, entry: dict, error: str) -> None:
        self.repo.log_event("publish.failed", severity="error",
                            post_id=entry["post_id"], payload={"error": error[:300]})
        try:
            from src.notifications.discord_review import notify_publish_failed
            notify_publish_failed(self.repo, entry, error)
        except Exception:
            pass  # notification failure must not corrupt publish state
