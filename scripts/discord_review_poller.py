#!/usr/bin/env python3
"""Discord-native review intake - one pass, designed for GitHub Actions cron.

Reads the #social-review channel (card reactions + typed commands), drives the
review state machine, executes accepted iterate requests (NIM) and pushes new
versions back through quality so fresh review cards appear automatically.

Exit codes: 0 = ran (with or without actions), 1 = configuration error.
Intake failures never crash the run; they land in the printed summary.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    from integrations.discord.client import DiscordClient
    from src.config import get_config
    from src.db.connection import get_db
    from src.db.repository import Repository
    from src.review.discord_poller import DiscordReviewPoller

    cfg = get_config()
    repo = Repository(get_db())
    discord = DiscordClient()

    channel = cfg.discord_channel("review")
    token = cfg.discord_bot_token
    if not (token and channel):
        print("discord-approval: DISCORD_BOT_TOKEN / DISCORD_CHANNEL_REVIEW "
              "not configured - nothing to do")
        return 0

    authorized = DiscordReviewPoller.resolve_authorized(repo, discord)
    if not authorized:
        print("discord-approval: no authorized users resolvable (set "
              "DISCORD_AUTHORIZED_USERS or ensure the bot can read the guild "
              "owner) - refusing to act")
        return 0

    def run_iterate(post_id: int, instruction: str, actor: str) -> None:
        """Execute an accepted iterate request now (NIM) instead of waiting
        for the next engine cycle."""
        from src.pipeline.ops import IteratePipeline
        IteratePipeline(repo, _nim()).run(post_id, instruction, actor)

    poller = DiscordReviewPoller(
        repo, discord, authorized, channel,
        approve_emoji=(_emoji(cfg, "approve")),
        reject_emoji=(_emoji(cfg, "reject")),
        run_iterate=run_iterate)
    summary = poller.process()

    if summary["iterated"]:
        # new versions must re-enter quality -> review immediately (no NIM
        # wait for the next 3x-daily engine cycle)
        try:
            from src.pipeline.ops import QualityPipeline, ReviewPipeline
            QualityPipeline(repo, _nim()).run()
            ReviewPipeline(repo).run()
        except Exception as exc:
            print(f"post-iterate quality/review pass failed: "
                  f"{type(exc).__name__}: {exc}")

    print(f"discord-approval summary: {summary}")
    return 0


def _nim():
    """Build the NIM client if a key exists; else None (pipelines BLOCK cleanly)."""
    from src.config import get_config
    cfg = get_config()
    if not cfg.nim_api_key:
        return None
    from integrations.nvidia.client import NIMClient
    return NIMClient(cfg.nim_api_key)


def _emoji(cfg, action: str) -> str:
    try:
        section = cfg.section("review") or {}
        reactions = section.get("reactions") or {}
        value = str(reactions.get(action) or "").strip()
        return value or None
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
