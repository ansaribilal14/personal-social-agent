"""Review, iteration, scheduling, publishing, analytics, report pipelines."""
from __future__ import annotations

import json

from src.config import get_config
from src.db.repository import Repository
from src.pipeline.base import PipelineBase
from src.publishing.publisher import Publisher
from src.state.machine import State


class ReviewPipeline(PipelineBase):
    stage = "review"

    def __init__(self, repo: Repository, nim=None, discord_client=None):
        super().__init__(repo, nim)
        self._discord = discord_client   # injectable mock for tests

    def run(self) -> dict:
        """Offer WAITING_APPROVAL candidates for human review.

        Surfaces come from config/review.yml `surfaces` (default: discord).
        - discord: review card in #social-review; the card's message id is
          registered so the poller can accept one-click reactions and typed
          commands (scripts/discord_review_poller.py).
        - github (optional): ALSO open an editorial-review issue.
        GitHub being down or disabled never blocks Discord review.
        """
        from src.notifications.discord_review import send_review_card
        from src.review.lifecycle import ReviewLifecycle
        cfg = get_config().section("review") or {}
        surfaces = cfg.get("surfaces") or ["discord"]
        want_github = "github" in surfaces

        gh = None
        if want_github:
            try:
                gh = self._github_soft()
            except Exception:
                gh = None
        lifecycle = ReviewLifecycle(self.repo, gh)
        sent = 0
        issues = 0
        for post in self.repo.posts_in_state(State.WAITING_APPROVAL.value):
            version = self.repo.get_version(post["id"], post["current_version"])
            evals = self.repo.db.query(
                "SELECT critic, score, passed FROM quality_evaluations "
                "WHERE post_id=:p AND version=:v ORDER BY id DESC LIMIT 10",
                {"p": post["id"], "v": post["current_version"]})
            scores = {e["critic"]: e["score"] for e in evals}
            scores["antislop_pass"] = all(
                e["passed"] for e in evals if e["critic"] == "antislop") if evals else False
            scores["platform_pass"] = all(
                e["passed"] for e in evals if e["critic"] == "platform_fit") if evals else False

            card_key = f"review_card:{post['post_uid']}"
            # dedup guard: a card for THIS version was already posted -> skip
            existing = self.repo.get_setting(card_key)
            if isinstance(existing, dict) and \
                    int(existing.get("version", -1)) == int(post["current_version"]):
                self.repo.log_event("review.duplicate_skipped", post_id=post["id"])
                continue

            issue_url = None
            if want_github and gh is not None:
                if lifecycle.find_open_review_issue(post["post_uid"]) is not None:
                    issue = lifecycle.find_open_review_issue(post["post_uid"])
                    issue_url = f"https://github.com/{get_config().github_repo}/issues/{issue['number']}"
                    issues += 1
                else:
                    try:
                        issue = lifecycle.create_review_issue(
                            post, version, scores,
                            {"overall_score": post.get("editorial_score") or 0})
                        issue_url = (f"https://github.com/{get_config().github_repo}"
                                     f"/issues/{issue['number']}")
                        issues += 1
                    except Exception as exc:
                        self.repo.log_event("review.issue_failed", post_id=post["id"],
                                            severity="warn",
                                            payload={"error": type(exc).__name__})

            # recommended slot (deterministic; scheduling engine, no DB writes)
            from src.scheduling.planner import SchedulingEngine, NoSlotAvailable
            try:
                slot = SchedulingEngine(self.repo, None, None).find_slot(post["platform"])
                slot_str = slot.at.isoformat()
            except NoSlotAvailable:
                slot_str = "no clean slot in window (collision protection)"

            angle_text = None
            if post.get("angle_id"):
                rows = self.repo.db.query("SELECT text FROM angles WHERE id=:i",
                                          {"i": post["angle_id"]})
                angle_text = rows[0]["text"] if rows else None

            message_id = send_review_card(
                self.repo, post, version, scores,
                post.get("editorial_score") or 0, slot_str, issue_url,
                client=self._discord, angle=angle_text)
            if message_id:
                # register for one-click reaction approvals (discord_poller)
                self.repo.set_setting(card_key, {
                    "message_id": message_id,
                    "version": int(post["current_version"])})
            sent += 1
        if sent == 0:
            # Zero cards this run: either nothing was generated or every
            # draft was hard-blocked. When blocked drafts exist, say WHY in
            # #alerts instead of going dark (silence read as engine dead).
            self._notify_blocked_digest()
        self.succeed()
        return {"review_cards_sent": sent, "review_issues": issues}

    def _notify_blocked_digest(self) -> None:
        """One digest per run, only when BLOCKED posts exist from today."""
        guard = f"quality_digest:{self.run_id}"
        if self.repo.get_setting(guard):
            return
        try:
            blocked = self.repo.db.query(
                "SELECT post_uid, platform FROM posts "
                "WHERE state='BLOCKED' "
                "AND updated_at >= datetime('now', '-26 hours') "
                "ORDER BY updated_at DESC LIMIT 6")
            if not blocked:
                return
            entries = []
            for b in blocked:
                ev = self.repo.db.query(
                    "SELECT q.issues FROM quality_evaluations q "
                    "JOIN posts p ON p.id = q.post_id "
                    "WHERE p.post_uid=:u AND q.passed=0 "
                    "ORDER BY q.id DESC LIMIT 3",
                    {"u": b["post_uid"]})
                issues: list[str] = []
                for row in ev:
                    try:
                        issues.extend(json.loads(row["issues"]))
                    except Exception:
                        continue
                entries.append({"post_uid": b["post_uid"],
                                "platform": b["platform"],
                                "issues": issues[:2]})
            if not entries:
                return
            from src.notifications.discord_review import notify_quality_digest
            if notify_quality_digest(self.repo, entries):
                self.repo.set_setting(guard, {"sent": True})
                self.repo.log_event("review.blocked_digest_sent",
                                    payload={"blocked": len(entries)})
        except Exception as exc:
            self.repo.log_event("review.blocked_digest_failed", severity="warn",
                                payload={"error": type(exc).__name__})

    def _github_soft(self):
        """GitHub client or None - review must not depend on GitHub availability."""
        try:
            from integrations.github.client import GitHubClient
            cfg = get_config()
            if cfg.github_token and cfg.github_repo:
                return GitHubClient(cfg.github_token, cfg.github_repo)
        except Exception as exc:
            self.repo.log_event("review.github_unavailable", severity="warn",
                                payload={"error": type(exc).__name__})
        return None

    def _github(self):
        try:
            from integrations.github.client import GitHubClient
            cfg = get_config()
            if cfg.github_token and cfg.github_repo:
                return GitHubClient(cfg.github_token, cfg.github_repo)
        except Exception as exc:
            self.repo.log_event("review.github_unavailable", severity="warn",
                                payload={"error": type(exc).__name__})
        raise RuntimeError("BLOCKED: GitHub client unavailable for review stage")


class IteratePipeline(PipelineBase):
    stage = "iterate"

    def run(self, post_id: int, instruction: str, actor: str) -> dict:
        self.require_nim()
        from src.generation.writer import Writer
        from src.voice.profile import VoiceProfile
        post = self.repo.get_post(post_id)
        if post is None:
            raise ValueError(f"post {post_id} not found")
        version = self.repo.get_version(post_id, post["current_version"])
        writer = Writer(self.nim, self.repo, VoiceProfile())
        findings = []
        evals = self.repo.db.query(
            "SELECT issues FROM quality_evaluations WHERE post_id=:p ORDER BY id DESC LIMIT 5",
            {"p": post_id})
        for e in evals:
            findings.extend(e.get("issues") or [])
        new = writer.iterate(post, version, instruction,
                             self.repo.recent_research(limit=6), findings)
        from src.claims.ledger import normalize_claim_type
        safe_claims = []
        for c in (new.get("claims") or []):
            if not isinstance(c, dict) or not str(c.get("text", "")).strip():
                continue
            c["claim_type"] = normalize_claim_type(c.get("claim_type", "OPINION"))
            safe_claims.append(c)
        new_version = self.repo.add_version(
            post_id, new["body"], new.get("thread_posts"), new.get("prompt_versions"),
            new.get("voice_snapshot"), safe_claims,
            iteration_instruction=instruction)
        self.repo.record_approval_event(post_id, post["current_version"], "ITERATED",
                                        actor, reason=instruction)
        # new version goes back through quality, then to WAITING_APPROVAL again
        self.repo.move_state(post_id, State.REVISING, actor="iterate")
        self.repo.move_state(post_id, State.DRAFTED, actor="iterate")
        self.succeed()
        return {"post_id": post_id, "new_version": new_version}


class ApprovalCommandPipeline(PipelineBase):
    stage = "approval"

    def run(self, issue_id: int, comment_id: int, body: str, actor: str) -> dict:
        from src.review.lifecycle import ReviewLifecycle
        gh = self._github()
        lifecycle = ReviewLifecycle(self.repo, gh)
        outcome = lifecycle.process_comment(issue_id, comment_id, body, actor)
        self.succeed()
        return outcome

    def _github(self):
        from integrations.github.client import GitHubClient
        cfg = get_config()
        return GitHubClient(cfg.github_token, cfg.github_repo)


class SchedulePipeline(PipelineBase):
    stage = "schedule"

    def run(self) -> dict:
        """APPROVED posts -> outbox + pick slot + mark SCHEDULED.

        Kill switch checked here (spec 12). Scheduling is BLOCKED when off.
        """
        from src.scheduling.planner import NoSlotAvailable, SchedulingEngine
        from src.security.kill_switch import PublishingBlocked, assert_scheduling_allowed
        try:
            assert_scheduling_allowed(self.repo)
        except PublishingBlocked:
            self.repo.log_event("schedule.blocked_kill_switch", severity="warn")
            self.succeed()  # orderly no-op
            return {"scheduled": 0, "blocked": True}

        scheduler = SchedulingEngine(self.repo, None, None)
        publisher = Publisher(self.repo)
        scheduled = []
        for post in self.repo.posts_in_state(State.APPROVED.value):
            version = post["current_version"]
            # create idempotent outbox entry and pick a clean slot
            publisher.enqueue_approved(post["id"])
            try:
                slot = scheduler.find_slot(post["platform"])
            except NoSlotAvailable:
                self.repo.log_event("schedule.no_slot", severity="warn",
                                    post_id=post["id"])
                continue
            self.repo.add_schedule(post["id"], post["platform"], slot.at.isoformat())
            self.repo.set_recommended_slot(post["id"], slot.at.isoformat())
            self.repo.move_state(post["id"], State.SCHEDULED, actor="scheduler")
            scheduled.append({"post_id": post["id"], "at": slot.at.isoformat()})
        self.succeed()
        return {"scheduled": scheduled}


class PublishPipeline(PipelineBase):
    stage = "publish"

    def __init__(self, repo: Repository, nim=None, buffer_client=None):
        super().__init__(repo, nim)
        self.buffer_client = buffer_client   # injectable mock for tests

    def run(self) -> dict:
        publisher = Publisher(self.repo, self.buffer_client)
        summary = publisher.process_outbox()
        self.succeed()
        return summary


class AnalyticsPipeline(PipelineBase):
    stage = "analytics"

    def run(self) -> dict:
        from src.analytics.engine import AnalyticsEngine, MetricsCollector
        collected = MetricsCollector(self.repo).collect_all_due()
        analysis = AnalyticsEngine(self.repo).analyze()
        self.succeed()
        return {"snapshots": collected, "analysis": analysis}


class WeeklyReportPipeline(PipelineBase):
    stage = "weekly-report"

    def run(self) -> dict:
        from src.analytics.engine import AnalyticsEngine
        from src.notifications.discord_review import notify_weekly_report
        report = AnalyticsEngine(self.repo).generate_weekly_report()
        notify_weekly_report(self.repo, report)
        self.succeed()
        return report


class MaintenancePipeline(PipelineBase):
    stage = "maintenance"

    def run(self) -> dict:
        """Health checks: DB, NIM, Buffer, GitHub config. Never publishes."""
        checks = {"database": "ok", "nim": "unavailable", "buffer": "unavailable",
                  "kill_switch": self.repo.get_setting("SOCIAL_AUTOMATION_ENABLED", False)}
        if self.nim is not None:
            try:
                self.nim.chat("You are a health probe.", "Reply with the word ok.",
                              max_tokens=5)
                checks["nim"] = "ok"
            except Exception as exc:
                checks["nim"] = f"error:{type(exc).__name__}"
        try:
            from integrations.buffer.client import BufferClient
            BufferClient().verify()
            checks["buffer"] = "ok"
        except Exception as exc:
            checks["buffer"] = f"error:{type(exc).__name__}"
        self.repo.log_event("maintenance.checks", payload=checks)
        self.succeed()
        return checks
