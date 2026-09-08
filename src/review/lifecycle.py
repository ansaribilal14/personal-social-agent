"""GitHub review lifecycle (spec sections 32-36) - the authoritative control
surface is the DB; GitHub Issues drive it; Discord mirrors it.

/approve /reject /iterate processing:
1. parse + authorize (src/security/commands.py)
2. replay protection via review_commands UNIQUE(issue_id, comment_id)
3. resolve post from issue body marker  [POST_UID: X-2026-XXXXX]
4. version checks: approving v1 never approves v2 (spec 34)
5. state transitions via the state machine
"""
from __future__ import annotations

from src.config import get_config
from src.db.repository import Repository
from src.security.commands import parse_command
from src.state.machine import State

POST_UID_MARKER = "POST_UID:"


class ReviewError(Exception):
    pass


def build_issue_body(post: dict, version: dict, scores: dict, quality: dict) -> str:
    lines = [
        f"{POST_UID_MARKER} {post['post_uid']}",
        "",
        f"Status: {post['state']}",
        f"Platform: {post['platform'].upper()}",
        f"Format: {post['format'].upper()}",
        f"Pillar: {post.get('pillar', '-')}",
        f"Version: {version['version']}",
        f"Quality: {quality.get('overall_score', '-')}",
        "",
        "## Content",
        "```",
    ]
    posts = version.get("thread_posts") or [version.get("body", "")]
    for i, p in enumerate(posts, 1):
        if len(posts) > 1:
            lines.append(f"--- {i}/{len(posts)} ---")
        lines.append(p)
    lines += ["```", "", "## Commands",
              "- `/approve` - approve THIS version",
              "- `/reject <reason>` - reject",
              "- `/iterate <instruction>` - request a new version",
              "",
              "Approved version must exactly match the version published later."]
    return "\n".join(lines)


class ReviewLifecycle:
    def __init__(self, repo: Repository, gh_client=None):
        self.repo = repo
        self.gh = gh_client
        cfg = get_config()
        self.authorized = cfg.authorized_users()
        self.max_iterate = int(cfg.security.get("commands", {})
                               .get("max_iterate_instruction_chars", 500))

    # ------------------------------------------------------------ issue ops
    def find_open_review_issue(self, post_uid: str) -> dict | None:
        """Return the open review issue for a post UID, if one exists.

        Dedup guard: without it, every review run re-opens an issue (and
        re-sends a Discord card) for each post still WAITING_APPROVAL.
        """
        if self.gh is None:
            return None
        needle = f"POST #{post_uid} "
        try:
            for issue in self.gh.list_open_issues(label="editorial-review"):
                if str(issue.get("title", "")).startswith(needle):
                    return issue
        except Exception:
            return None
        return None

    def create_review_issue(self, post: dict, version: dict, scores: dict,
                            quality: dict) -> dict:
        if self.gh is None:
            raise ReviewError("GitHub client unavailable")
        issue = self.gh.create_issue(
            title=f"POST #{post['post_uid']} - review {post['platform']} {post['format']}",
            body=build_issue_body(post, version, scores, quality),
            labels=["editorial-review"])
        self.repo.log_event("review.issue_created", post_id=post["id"],
                            payload={"issue_number": issue.get("number")})
        return issue

    # ------------------------------------------------------- comment intake
    def process_comment(self, issue_id: int, comment_id: int, body: str,
                        actor: str) -> dict:
        """Full intake path. Never raises on malformed input; returns outcome."""
        outcome: dict = {"accepted": False}
        if self.repo.command_seen(issue_id, comment_id):
            outcome["reject_reason"] = "replayed comment (already processed)"
            self.repo.record_review_command(issue_id, comment_id, "unknown", "",
                                            actor, False, outcome["reject_reason"])
            return outcome

        # parse_command treats comment text as untrusted data and returns a
        # ParsedCommand; it does not raise on malformed input (spec 51).
        cmd = parse_command(body, actor, self.authorized, self.max_iterate)

        if not cmd.valid:
            self.repo.record_review_command(issue_id, comment_id, cmd.name, cmd.payload,
                                            actor, False, cmd.reject_reason)
            outcome["reject_reason"] = cmd.reject_reason
            return outcome

        try:
            post = self._post_for_issue(issue_id)
        except Exception as exc:
            # GitHub unavailable etc.: refuse safely, state untouched (spec 49).
            outcome["reject_reason"] = f"infrastructure error: {type(exc).__name__}"
            self.repo.record_review_command(issue_id, comment_id, cmd.name, cmd.payload,
                                            actor, False, outcome["reject_reason"])
            return outcome
        if post is None:
            outcome["reject_reason"] = "no post bound to this issue"
            self.repo.record_review_command(issue_id, comment_id, cmd.name, cmd.payload,
                                            actor, False, outcome["reject_reason"])
            return outcome

        handler = {"approve": self._handle_approve, "reject": self._handle_reject,
                   "iterate": self._handle_iterate}[cmd.name]
        try:
            result = handler(post, cmd, actor, issue_id, comment_id)
            outcome.update(result)
            outcome["accepted"] = True
            self.repo.record_review_command(issue_id, comment_id, cmd.name,
                                            cmd.payload, actor, True, None)
        except ReviewError as exc:
            outcome["reject_reason"] = str(exc)
            self.repo.record_review_command(issue_id, comment_id, cmd.name, cmd.payload,
                                            actor, False, str(exc))
        except Exception as exc:
            # Infrastructure failure (GitHub down, DB issue): refuse safely,
            # never half-apply a command (spec 49: no failure corrupts state).
            outcome["reject_reason"] = f"infrastructure error: {type(exc).__name__}"
            self.repo.record_review_command(issue_id, comment_id, cmd.name, cmd.payload,
                                            actor, False, outcome["reject_reason"])
        return outcome

    def _post_for_issue(self, issue_id: int) -> dict | None:
        """Posts are bound to issues via the marker in the issue body."""
        if self.gh is None:
            return None
        issue = self.gh.get_issue(issue_id)
        body = issue.get("body") or ""
        for line in body.splitlines():
            if line.startswith(POST_UID_MARKER):
                uid = line.replace(POST_UID_MARKER, "").strip()
                return self.repo.get_post_by_uid(uid)
        return None

    # -------------------------------------------------------------- handlers
    def _handle_approve(self, post: dict, cmd, actor: str,
                        issue_id: int, comment_id: int) -> dict:
        version = post["current_version"]
        if post["state"] != State.WAITING_APPROVAL.value:
            raise ReviewError(f"state is {post['state']}, not WAITING_APPROVAL")
        # Kill switch does NOT block approval - only scheduling/publishing (spec 12).
        self.repo.record_approval_event(post["id"], version, "APPROVED", actor,
                                        issue_id=issue_id, comment_id=comment_id)
        self.repo.move_state(post["id"], State.APPROVED, actor=actor)
        return {"action": "APPROVED", "version": version}

    def _handle_reject(self, post: dict, cmd, actor: str,
                       issue_id: int, comment_id: int) -> dict:
        version = post["current_version"]
        self.repo.record_approval_event(post["id"], version, "REJECTED", actor,
                                        reason=cmd.payload, issue_id=issue_id,
                                        comment_id=comment_id)
        self.repo.move_state(post["id"], State.REJECTED, actor=actor)
        return {"action": "REJECTED", "version": version}

    def _handle_iterate(self, post: dict, cmd, actor: str,
                        issue_id: int, comment_id: int) -> dict:
        if post["state"] not in (State.WAITING_APPROVAL.value, State.ITERATING.value):
            raise ReviewError(f"state is {post['state']}, cannot iterate")
        self.repo.record_approval_event(post["id"], post["current_version"],
                                        "ITERATED", actor, reason=cmd.payload,
                                        issue_id=issue_id, comment_id=comment_id)
        if post["state"] != State.ITERATING.value:   # idempotent re-mark
            self.repo.move_state(post["id"], State.ITERATING, actor=actor)
        return {"action": "ITERATED", "instruction": cmd.payload,
                "post_id": post["id"]}
