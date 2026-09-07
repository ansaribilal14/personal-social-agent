"""Buffer publishing adapter (spec section 39) - LIVE-VERIFIED against the
production GraphQL API (developers.buffer.com), token accepted 2026-09-07.

Verification (executed live during setup, see docs/API_COMPATIBILITY.md):
- Endpoint: POST https://api.buffer.com/graphql  (Bearer token)
- Identity:  account { id name email timezone organizations { id name } }
- Channels:  channels(input: { organizationId: ... })  -> 3 live channels
             (twitter / threads / instagram)
- Publish:   mutation createPost(input: CreatePostInput!) with REQUIRED
             mode: ShareMode! (addToQueue|customScheduled|shareNext|shareNow)
             and schedulingType: SchedulingType! (automatic|notification)
             -> PostActionPayload union: PostActionSuccess | MutationError
- Reconcile: post(input: { id }) { id status dueAt sentAt ... }
             status enum: draft|error|needs_approval|scheduled|sending|sent
- Metrics:   post(input: { id }) { metrics { name value } }  (live-probed:
             Reactions / Comments / Eng. Rate / Reposts / Impressions / Clicks)

Design:
- No fabricated operations: only schema-verified operations are used.
- Every failure maps to the typed taxonomy; nothing is silently swallowed.
- A draft probe (saveToDraft) was created and deleted during verification;
  the publishing path itself stays fail-safe OFF until the kill switch is
  explicitly enabled (see src/security/kill_switch.py).
"""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_BASE = "https://api.buffer.com"
GRAPHQL_PATH = "/graphql"

# Buffer names the X service "twitter" on its channels.
SERVICE_ALIASES = {"x": "twitter"}

# Seconds between two consecutive items of a multi-item thread. The new
# Buffer GraphQL API has no reply-chain primitive, so a thread is expressed
# as N ordered scheduled posts, staggered by this gap (configurable).
THREAD_GAP_SECONDS = int(os.environ.get("BUFFER_THREAD_GAP_SECONDS", "90"))


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
        self._organization_id: str | None = None

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
                f"{self.base}{GRAPHQL_PATH}",
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

    @staticmethod
    def _payload_errors(result: Any) -> str | None:
        """Union payloads carry MutationError fragments as {message: ...}."""
        if isinstance(result, dict) and result.get("message"):
            return str(result["message"])[:200]
        return None

    # ------------------------------------------------------------ identity
    def verify(self) -> dict:
        """Identity check used by the maintenance workflow. Raises on failure."""
        data = self.graphql("query { account { id name email timezone } }")
        return data.get("account") or {}

    def organization_id(self) -> str:
        """First organization of the account (channels are org-scoped)."""
        if self._organization_id:
            return self._organization_id
        data = self.graphql("query { account { organizations { id name } } }")
        orgs = ((data.get("account") or {}).get("organizations")) or []
        if not orgs:
            raise BufferValidationError("no Buffer organization on account")
        self._organization_id = str(orgs[0]["id"])
        return self._organization_id

    def channels(self) -> list[dict]:
        """Discover connected channels (twitter/threads/instagram among them)."""
        data = self.graphql(
            "query Channels($org: OrganizationId!) {"
            "  channels(input: { organizationId: $org }) {"
            "    id service serviceId displayName isDisconnected isQueuePaused"
            "  }"
            "}", {"org": self.organization_id()})
        return data.get("channels") or []

    def channel_for(self, service: str) -> dict:
        """Find the connected channel for a platform service key.

        Accepts the internal platform key ('x') or Buffer's own name
        ('twitter'/'threads'). Raises if the channel is missing/disconnected.
        """
        wanted = SERVICE_ALIASES.get(str(service).lower(), str(service).lower())
        for ch in self.channels():
            if str(ch.get("service", "")).lower() != wanted:
                continue
            if ch.get("isDisconnected"):
                continue
            return ch
        raise BufferValidationError(
            f"no connected Buffer channel for service '{service}'")

    # ----------------------------------------------------------- publishing
    def _post_input(self, channel_id: str, text: str,
                    scheduled_at_iso: str | None) -> dict:
        """CreatePostInput per live schema: mode + schedulingType REQUIRED.

        - scheduled: mode=customScheduled + dueAt (fixed slot from the
          collision-protected scheduling engine)
        - unscheduled: mode=addToQueue (Buffer places it per channel queue)
        """
        if scheduled_at_iso:
            return {"channelId": channel_id, "text": text,
                    "mode": "customScheduled", "schedulingType": "automatic",
                    "dueAt": scheduled_at_iso}
        return {"channelId": channel_id, "text": text,
                "mode": "addToQueue", "schedulingType": "automatic"}

    def create_post(self, channel_id: str, text: str, scheduled_at_iso: str | None,
                    thread_texts: list[str] | None = None) -> dict:
        """Create a single post or an ordered multi-item thread via Buffer.

        Returns {'buffer_post_id': str, 'scheduled_at': iso|None,
                 'buffer_post_ids': [str, ...]}.
        Threads: the current Buffer GraphQL schema has no reply-chain
        primitive, so thread items are expressed as N ordered posts, each at
        its own dueAt (first at the requested slot, then +THREAD_GAP_SECONDS
        each). Content is preserved verbatim, nothing is flattened.
        """
        if not text or not text.strip():
            raise BufferValidationError("refusing to create an empty post")
        items = [t for t in (thread_texts or []) if t and t.strip()] or [text]

        mutation = (
            "mutation CreatePost($input: CreatePostInput!) {"
            "  createPost(input: $input) {"
            "    ... on PostActionSuccess { post { id status dueAt } }"
            "    ... on MutationError { message }"
            "  }"
            "}"
        )

        from datetime import datetime, timedelta

        ids: list[str] = []
        first_scheduled_at: str | None = None
        for i, item in enumerate(items):
            slot: str | None = None
            if scheduled_at_iso and i > 0:
                base_dt = datetime.fromisoformat(scheduled_at_iso.replace("Z", "+00:00"))
                slot = (base_dt + timedelta(seconds=THREAD_GAP_SECONDS * i)).isoformat()
            elif scheduled_at_iso:
                slot = scheduled_at_iso
            variables = {"input": self._post_input(channel_id, item, slot)}
            data = self.graphql(mutation, variables)
            payload = data.get("createPost") or {}
            err = self._payload_errors(payload)
            if err:
                raise BufferValidationError(f"buffer createPost rejected: {err}")
            post = payload.get("post") or {}
            if not post.get("id"):
                raise BufferError("buffer did not return a post id")
            ids.append(str(post["id"]))
            if i == 0:
                first_scheduled_at = post.get("dueAt")

        return {"buffer_post_id": ids[0],
                "scheduled_at": first_scheduled_at,
                "buffer_post_ids": ids}

    def get_post(self, buffer_post_id: str) -> dict:
        """Reconcile actual Buffer state (spec section 39 step 7)."""
        data = self.graphql(
            "query GetPost($id: PostId!) { post(input: { id: $id }) {"
            "  id status dueAt sentAt channelId channelService"
            "} }", {"id": buffer_post_id})
        return data.get("post") or {}

    # Buffer status enum (live): draft|error|needs_approval|scheduled|sending|sent
    STATUS_MAP = {
        "draft": "DRAFT", "error": "ERROR", "needs_approval": "NEEDS_APPROVAL",
        "scheduled": "SCHEDULED", "sending": "SENDING", "sent": "SENT",
    }

    def post_metrics(self, buffer_post_id: str) -> dict:
        """Normalized metrics. Only fields present in the response are returned -
        the system never invents unavailable metrics (spec section 41).

        Live response shape: metrics { name value } with display names such as
        'Reactions', 'Impressions', 'Eng. Rate' - normalized here to lowercase
        snake_case keys (reactions, comments, eng_rate, reposts, impressions,
        clicks).
        """
        data = self.graphql(
            "query Metrics($id: PostId!) { post(input: { id: $id }) {"
            "  id metrics { name value }"
            "} }", {"id": buffer_post_id})
        post = data.get("post") or {}
        out: dict = {}
        for m in post.get("metrics") or []:
            name = str(m.get("name") or "").strip().lower()
            name = name.replace(".", "_").replace(" ", "_").replace("-", "_")
            while "__" in name:
                name = name.replace("__", "_")
            value = m.get("value")
            if name and value is not None:
                out[name] = value
        return out
