#!/usr/bin/env python3
"""GitHub issue-comment handler for review commands.

Reads the untrusted comment from environment variables (never shell
interpolation), resolves the post bound to the issue via the POST_UID marker,
and routes through the authorization + replay-protected pipeline.

Modes:
  iterate  -> /iterate <instruction> : generate next version, re-run quality
  approval -> /approve | /reject     : record approval event, transition state
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from integrations.github.client import GitHubClient  # noqa: E402
from src.config import get_config  # noqa: E402
from src.db.connection import get_db  # noqa: E402
from src.db.repository import Repository  # noqa: E402
from src.pipeline.ops import ApprovalCommandPipeline, IteratePipeline  # noqa: E402
from src.pipeline.production import QualityPipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["iterate", "approval"], required=True)
    args = ap.parse_args()

    cfg = get_config()
    db = get_db()
    repo = Repository(db)
    gh = GitHubClient(cfg.github_token, cfg.github_repo)

    issue_id = int(os.environ["ISSUE_ID"] or 0)
    comment_id = int(os.environ["COMMENT_ID"] or 0)
    body = os.environ.get("COMMENT_BODY", "")
    actor = os.environ.get("COMMENT_ACTOR", "")

    pipeline = ApprovalCommandPipeline(repo, None)
    outcome = pipeline.run(issue_id, comment_id, body, actor)

    print(json.dumps({"outcome": outcome}, indent=1, default=str))

    if not outcome.get("accepted"):
        gh.add_comment(issue_id, f"Command not executed: {outcome.get('reject_reason')}")
        return 0

    action = outcome.get("action")

    if action == "APPROVED":
        post = _post_from_issue(gh, issue_id, repo)
        gh.add_comment(issue_id,
                       f"Version {outcome.get('version')} APPROVED by @{actor}. "
                       "It will be scheduled and published exactly as this version. "
                       "The kill switch must be enabled for scheduling/publishing.")
        gh.update_issue(issue_id, labels=["editorial-review", "approved"])
    elif action == "REJECTED":
        gh.add_comment(issue_id, f"Rejected by @{actor}. Reason: "
                       f"{outcome.get('reason') or '(none)'}")
        gh.update_issue(issue_id, state="closed",
                        labels=["editorial-review", "rejected"])
    elif action == "ITERATED":
        instruction = outcome.get("instruction", "")
        post = _post_from_issue(gh, issue_id, repo)
        if post is None:
            gh.add_comment(issue_id, "Iteration failed: no post bound to this issue.")
            return 0
        nim = _make_nim(cfg)
        if nim is None:
            gh.add_comment(issue_id, "BLOCKED: NIM unavailable "
                           "(NVIDIA_API_KEY missing) - cannot iterate.")
            return 1
        it = IteratePipeline(repo, nim)
        result = it.run(post["id"], instruction, actor)
        # push new version through quality before returning to review
        qp = QualityPipeline(repo, nim)
        qp.run()
        updated = repo.get_post(post["id"])
        version = repo.get_version(post["id"], updated["current_version"])
        gh.add_comment(
            issue_id,
            f"New version {version['version']} generated from your instruction.\n"
            f"Instruction: {instruction}\n"
            f"State: {updated['state']}\n"
            "Quality will re-run; approve only the exact version you want.\n\n"
            "```\n" + (version.get("body") or "")[:1500] + "\n```")
        _notify_discord(f"New version {version['version']} for "
                        f"{post['post_uid']} awaiting your review.")
    return 0


def _post_from_issue(gh: GitHubClient, issue_id: int, repo: Repository):
    from src.review.lifecycle import POST_UID_MARKER
    issue = gh.get_issue(issue_id)
    for line in (issue.get("body") or "").splitlines():
        if line.startswith(POST_UID_MARKER):
            return repo.get_post_by_uid(line.replace(POST_UID_MARKER, "").strip())
    return None


def _make_nim(cfg):
    if not cfg.nim_api_key:
        return None
    from integrations.nvidia.client import NIMClient
    return NIMClient(cfg.nim_api_key)


def _notify_discord(text: str) -> None:
    try:
        from integrations.discord.client import DiscordClient
        DiscordClient().send(text)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
