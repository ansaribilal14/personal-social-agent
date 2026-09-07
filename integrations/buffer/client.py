"""Buffer publishing adapter (spec section 39).

VERIFICATION STATUS (docs/API_COMPATIBILITY.md has details):
- VERIFIED: API endpoints respond; with the provided token, Buffer returns
  401 UNAUTHENTICATED ("Access token is not valid") at build time. The token
  was therefore marked BLOCKED: the adapter is implemented per current
  official docs (developers.buffer.com - GraphQL API) and every live path is
  classified gracefully, but LIVE PUBLISHING IS NOT EXECUTED until a valid
  token is configured.

Design:
- GraphQL endpoint: POST {base}/api/graphql with Bearer token.
- Operations used: user identity, channel discovery, createUpdate-style
  scheduled post creation (single + multi-item threads), post retrieval for
  reconciliation, normalized metrics.
- No fabricated operations: anything the schema rejects surfaces as
  BufferError with the response error class - never silently swallowed.
"""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_BASE = "https://api.buffer.com"


class BufferError(Exception):
    pass


class BufferAuthError(BufferError):
    """Token invalid/expired/missing - retrying with same token cannot succeed."""


class BufferValidationError(BufferError):
    """Request rejected as malformed (bad channel, bad text, bad schedule)."""


class BufferUnavailable(BufferError):
    """Network/5xx - retryable with backoff."""


class BufferClient:
    def __init__(self, access_token: str | None = None, base_url: str | None = None,
                 session: requests.Session | None = None):
        self.token = access_token or os.environ.get("BUFFER_API_KEY")
        self.base = (base_url or os.environ.get("BUFFER_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.session = session or requests.Session()

    # ------------------------------------------------------------- plumbing
    def _headers(self) -> dict:
        if not self.token:
            raise BufferAuthError("BUFFER_API_KEY not configured")
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def _classify(self, resp: requests.Response) -> None:
        """Map HTTP responses to the failure taxonomy without leaking bodies."""
        if resp.status_code in (401, 403):
            raise BufferAuthError(f"buffer auth rejected (HTTP {resp.status_code})")
        if resp.status_code == 429:
            raise BufferUnavailable("buffer rate limited (HTTP 429)")
        if resp.status_code >= 500:
            raise BufferUnavailable(f"buffer unavailable (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise BufferValidationError(f"buffer rejected request (HTTP {resp.status_code})")

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict:
        try:
            resp = self.session.post(
                f"{self.base}/api/graphql",
                json={"query": query, "variables": variables or {}},
                headers=self._headers(), timeout=30)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise BufferUnavailable(f"buffer unreachable: {type(exc).__name__}") from exc
        self._classify(resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise BufferError("buffer returned non-JSON response") from exc
        if data.get("errors"):
            # Keep first message only; never log full payloads (may echo tokens).
            msg = str(data["errors"][0].get("message", "unknown graphql error"))[:200]
            code = (data["errors"][0].get("extensions") or {}).get("code", "")
            if code == "UNAUTHENTICATED":
                raise BufferAuthError(msg)
            raise BufferValidationError(msg)
        return data.get("data") or {}

    # ------------------------------------------------------------ identity
    def verify(self) -> dict:
        """Identity check used by maintenance workflow. Raises on any failure."""
        data = self.graphql("query { account { id displayName } }")
        return data.get("account") or {}

    def channels(self) -> list[dict]:
        """Discover connected channels (X / Threads among them)."""
        data = self.graphql(
            "query { channels { id service serviceId displayName } }")
        return data.get("channels") or []

    def channel_for(self, service: str) -> dict:
        """Find the channel for a platform service key ('x' | 'threads')."""
        for ch in self.channels():
            if str(ch.get("service", "")).lower() == service:
                return ch
        raise BufferValidationError(f"no Buffer channel configured for service '{service}'")

    # ----------------------------------------------------------- publishing
    def create_post(self, channel_id: str, text: str, scheduled_at_iso: str | None,
                    thread_texts: list[str] | None = None) -> dict:
        """Create a single post or an ordered thread via Buffer.

        Returns {'buffer_post_id': str, 'scheduled_at': iso|None}.
        Threads are expressed as multiple content items on one update.
        """
        if not text or not text.strip():
            raise BufferValidationError("refusing to create an empty post")
        items = ([t for t in (thread_texts or []) if t and t.strip()] or [text])
        mutation = (
            "mutation CreateUpdate($input: CreateScheduledPostInput!) {"
            "  createScheduledPost(input: $input) {"
            "    id"
            "    scheduledAt"
            "  }"
            "}"
        )
        variables = {
            "input": {
                "channelId": channel_id,
                "content": [{"text": t} for t in items],
                **({"scheduledAt": scheduled_at_iso} if scheduled_at_iso else {}),
            }
        }
        data = self.graphql(mutation, variables)
        post = (data.get("createScheduledPost") or {})
        if not post.get("id"):
            raise BufferError("buffer did not return a post id")
        return {"buffer_post_id": str(post["id"]),
                "scheduled_at": post.get("scheduledAt")}

    def get_post(self, buffer_post_id: str) -> dict:
        """Reconcile actual Buffer state (spec section 39 step 7)."""
        data = self.graphql(
            "query GetUpdate($id: ID!) { update(id: $id) { id status scheduledAt } }",
            {"id": buffer_post_id})
        return data.get("update") or {}

    def post_metrics(self, buffer_post_id: str) -> dict:
        """Normalized metrics. Only fields present in the response are returned -
        the system never invents unavailable metrics (spec section 41)."""
        data = self.graphql(
            "query Metrics($id: ID!) { update(id: $id) { id statistics { "
            "impressions likes replies reposts quotes bookmarks reach engagement } } }",
            {"id": buffer_post_id})
        update = data.get("update") or {}
        stats = update.get("statistics") or {}
        return {k: v for k, v in stats.items() if v is not None}
