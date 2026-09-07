"""Discord webhook client - notification surface ONLY (spec section 32).

Discord must never become the authoritative state store; the DB is. Messages
are chunked to respect the 2000-char limit; embeds used for review cards.
"""
from __future__ import annotations

import os

import requests

MAX_CONTENT = 1900  # safety margin under 2000


class DiscordError(Exception):
    pass


class DiscordClient:
    def __init__(self, webhook_url: str | None = None,
                 session: requests.Session | None = None):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
        self.session = session or requests.Session()

    def send(self, content: str) -> bool:
        """Send a plain message. Returns True on success; raises DiscordError on
        config problems; returns False on transient rejection (caller decides)."""
        if not self.webhook_url:
            raise DiscordError("DISCORD_WEBHOOK_URL not configured")
        chunks = [content[i:i + MAX_CONTENT] for i in range(0, max(len(content), 1), MAX_CONTENT)]
        ok = True
        for chunk in chunks:
            resp = self.session.post(self.webhook_url,
                                     json={"content": chunk[:MAX_CONTENT]}, timeout=20)
            if resp.status_code in (429, 500, 502, 503):
                ok = False
            elif resp.status_code >= 400:
                raise DiscordError(f"discord rejected message HTTP {resp.status_code}")
        return ok

    def send_review_card(self, card: dict) -> bool:
        """card: {title, description} rendered as an embed."""
        if not self.webhook_url:
            raise DiscordError("DISCORD_WEBHOOK_URL not configured")
        resp = self.session.post(
            self.webhook_url,
            json={"embeds": [{"title": card.get("title", "")[:250],
                              "description": (card.get("description") or "")[:4000]}]},
            timeout=20)
        if resp.status_code >= 400:
            raise DiscordError(f"discord rejected embed HTTP {resp.status_code}")
        return resp.status_code == 204
