"""Discord notifications (spec section 32) - presentation only, DB is truth.

Delivery: Discord webhook if configured, else the bot token routes messages
to purpose-specific channels (review / alerts / analytics / errors).
"""
from __future__ import annotations

import json

from integrations.discord.client import DiscordClient
from src.db.repository import Repository


def _repo_enabled() -> bool:
    try:
        from src.config import get_config
        cfg = get_config()
        if cfg.discord_webhook_url:
            return True
        if cfg.discord_bot_token and any(
                cfg.discord_channel(p) for p in ("review", "alerts", "errors", "analytics")):
            return True
        return False
    except Exception:
        return False


def _send_to(purpose: str, text: str) -> bool:
    """Purpose-routed send with a graceful fallback to plain send()."""
    client = DiscordClient()
    try:
        return client.send_to(purpose, text)
    except Exception:
        return client.send(text)


def format_review_card(post: dict, version: dict, scores: dict,
                       editorial_score: int, recommended_slot: str,
                       issue_url: str | None = None) -> str:
    """The review-card format (chunked by the client).

    Approval is Discord-native: react on this message, or type a command.
    A GitHub issue link is appended only when the github surface is enabled.
    """
    sep = "\u2501" * 20
    lines = [
        sep, "NEW POST FOR REVIEW", sep, "",
        f"ID: {post['post_uid']}", "",
        f"Platform: {post['platform'].upper()}",
        f"Format: {post['format'].upper()}",
        f"Pillar: {post.get('pillar', '-')}",
        f"Version: v{post.get('current_version', '-')}", "",
        f"Editorial score: {editorial_score}", "",
        "WHY THIS EXISTS:",
        str(version.get("why_this_exists") or "-")[:300], "", sep,
    ]
    posts = version.get("thread_posts") or [version.get("body", "")]
    total = len(posts)
    for i, p in enumerate(posts, 1):
        lines.append(f"{i}/{total}")
        lines.append(p)
        lines.append("")
    lines.append(sep)
    lines.append("Quality")
    lines.append(f"Originality: {scores.get('originality', '-')}")
    lines.append(f"Voice: {scores.get('voice', '-')}")
    lines.append(f"Factuality: {scores.get('fact', '-')}")
    lines.append(f"Hook: {scores.get('hook', '-')}")
    lines.append(f"Anti-slop: {'PASS' if scores.get('antislop_pass') else 'FAIL'}")
    lines.append(f"Platform: {'PASS' if scores.get('platform_pass') else 'FAIL'}")
    lines.append("")
    lines.append(f"Recommended: {recommended_slot}")
    lines.append("")
    lines.append("HOW TO DECIDE (all right here in Discord):")
    lines.append("\u2705 react on THIS message to approve it")
    lines.append("\u274c react to reject it")
    lines.append("iterate <ID> <instruction> - request changes (type it here)")
    lines.append(f"e.g. iterate {post['post_uid']} make the hook punchier")
    if issue_url:
        lines.append("")
        lines.append(f"GitHub Review (optional audit trail): {issue_url}")
    return "\n".join(lines)


def send_review_card(repo: Repository, post: dict, version: dict, scores: dict,
                     editorial_score: int, recommended_slot: str,
                     issue_url: str | None = None,
                     client: DiscordClient | None = None) -> str | None:
    """Post the review card; return the Discord message id (None if unavailable).

    The message id lets the poller (src/review/discord_poller.py) read card
    reactions for one-click approve/reject. Returns None when Discord is not
    configured or the send was transiently rejected (next run retries).
    """
    if not _repo_enabled():
        repo.log_event("discord.skipped_not_configured", post_id=post["id"])
        return None
    text = format_review_card(post, version, scores, editorial_score,
                              recommended_slot, issue_url)
    client = client or DiscordClient()
    try:
        channel = None
        try:
            from src.config import get_config as _cfg
            channel = _cfg().discord_channel("review")
        except Exception:
            channel = None
        if channel:
            message_id = client.post_message(channel, text)
        else:
            message_id = client.send_to("review", text) and None
        repo.log_event("discord.review_card_sent", post_id=post["id"],
                       payload={"issue_url": issue_url,
                                "message_id": message_id})
        return message_id
    except Exception as exc:
        repo.log_event("discord.review_card_failed", post_id=post["id"],
                       severity="warn", payload={"error": type(exc).__name__})
        return None


def notify_publish_failed(repo: Repository, entry: dict, error: str) -> bool:
    if not _repo_enabled():
        return False
    post = repo.get_post(entry["post_id"]) or {}
    text = (f"\u26a0\ufe0f PUBLISH FAILED\n"
            f"ID: {post.get('post_uid', entry.get('post_id'))}\n"
            f"Platform: {entry.get('platform')}\n"
            f"Version: {entry.get('version')}\n"
            f"Outbox: {entry.get('status')}\n"
            f"Error: {str(error)[:400]}\n"
            f"Content is preserved and NOT regenerated; inspect the outbox row.")
    return _send_to("errors", text)


def notify_weekly_report(repo: Repository, report: dict) -> bool:
    if not _repo_enabled():
        return False
    text = "SOCIAL WEEKLY REPORT\n" + json.dumps(report, indent=1, ensure_ascii=False)
    return _send_to("analytics", text[:3800])
