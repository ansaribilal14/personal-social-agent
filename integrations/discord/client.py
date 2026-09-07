"""Discord client - notification surface + bot-token channel routing (spec 32).

Discord must never become the authoritative state store; the DB is. Messages
are chunked to respect the 2000-char limit; embeds used for review cards.

Two delivery modes:
1. Webhook (DISCORD_WEBHOOK_URL) - original mode, unchanged behavior.
2. Bot token (DISCORD_BOT_TOKEN) + per-purpose channel ids
   (DISCORD_CHANNEL_REVIEW / _ALERTS / _ANALYTICS / _ERRORS / _GENERAL /
   _STRATEGY) - REST messages posted as the bot ("Social Automation Bot").

If both are configured, the webhook wins for send(); purpose-routed sends
always use the bot token when present.
"""
from __future__ import annotations

import os

import requests

MAX_CONTENT = 1900  # safety margin under 2000

API_BASE = "https://discord.com/api/v10"

PURPOSES = ("review", "alerts", "analytics", "errors", "general", "strategy")


class DiscordError(Exception):
    pass


def channel_for_purpose(purpose: str, bot_token: str | None = None) -> str | None:
    """Resolve a purpose -> channel id from the environment."""
    token = bot_token or os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        return None
    return os.environ.get(f"DISCORD_CHANNEL_{purpose.upper()}".strip()) or None


class DiscordClient:
    def __init__(self, webhook_url: str | None = None, bot_token: str | None = None,
                 session: requests.Session | None = None):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
        self.bot_token = bot_token or os.environ.get("DISCORD_BOT_TOKEN")
        self.session = session or requests.Session()

    # ------------------------------------------------------------- plumbing
    def _bot_headers(self) -> dict:
        if not self.bot_token:
            raise DiscordError("DISCORD_BOT_TOKEN not configured")
        return {"Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json"}

    @staticmethod
    def _chunks(content: str) -> list[str]:
        return [content[i:i + MAX_CONTENT]
                for i in range(0, max(len(content), 1), MAX_CONTENT)]

    def send_to_channel(self, channel_id: str, content: str,
                        embed: dict | None = None) -> bool:
        """Post a message to a channel as the bot. Returns True on 200/204."""
        if not channel_id:
            raise DiscordError("no target channel id")
        if not content and not embed:
            raise DiscordError("refusing to send an empty message")
        ok = True
        for chunk in self._chunks(content or ""):
            body: dict = {"content": chunk[:MAX_CONTENT]}
            if embed is not None and chunk is self._chunks(content or "")[-1]:
                body["embeds"] = [embed]
            resp = self.session.post(f"{API_BASE}/channels/{channel_id}/messages",
                                     json=body, headers=self._bot_headers(),
                                     timeout=20)
            if resp.status_code in (429, 500, 502, 503):
                ok = False
            elif resp.status_code >= 400:
                raise DiscordError(
                    f"discord rejected channel message HTTP {resp.status_code}")
        return ok

    def send_to(self, purpose: str, content: str, embed: dict | None = None) -> bool:
        """Purpose-routed send via bot token (review/alerts/analytics/...)."""
        channel = channel_for_purpose(purpose, self.bot_token)
        if not channel:
            raise DiscordError(f"no channel configured for purpose '{purpose}'")
        return self.send_to_channel(channel, content, embed=embed)

    # ------------------------------------------------------- compatibility
    def send(self, content: str) -> bool:
        """Send a plain message. Webhook if configured, else bot alerts channel.

        Returns True on success; raises DiscordError on config problems;
        returns False on transient rejection (caller decides)."""
        if not content or not content.strip():
            content = "(empty notification)"
        if self.webhook_url:
            ok = True
            for chunk in self._chunks(content):
                resp = self.session.post(self.webhook_url,
                                         json={"content": chunk[:MAX_CONTENT]},
                                         timeout=20)
                if resp.status_code in (429, 500, 502, 503):
                    ok = False
                elif resp.status_code >= 400:
                    raise DiscordError(
                        f"discord rejected message HTTP {resp.status_code}")
            return ok
        # bot-token fallback: alerts purpose (errors/alerts notifications land here)
        channel = channel_for_purpose("alerts", self.bot_token) or \
            channel_for_purpose("general", self.bot_token)
        if not channel:
            raise DiscordError(
                "neither DISCORD_WEBHOOK_URL nor DISCORD_BOT_TOKEN+channels configured")
        return self.send_to_channel(channel, content)

    def send_review_card(self, card: dict) -> bool:
        """card: {title, description} rendered as an embed."""
        embed = {"title": (card.get("title") or "")[:250],
                 "description": (card.get("description") or "")[:4000]}
        if self.webhook_url:
            resp = self.session.post(self.webhook_url, json={"embeds": [embed]},
                                     timeout=20)
            if resp.status_code >= 400:
                raise DiscordError(f"discord rejected embed HTTP {resp.status_code}")
            return resp.status_code == 204
        return self.send_to("review", "", embed=embed)
