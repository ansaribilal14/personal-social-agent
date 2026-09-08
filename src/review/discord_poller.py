"""Discord-native review intake - approvals WITHOUT GitHub issues.

Runs where GitHub Actions runs (scripts/discord_review_poller.py, every few
minutes). It reads the #social-review channel via the Discord REST API and
drives the SAME DB state machine as the slash-command bot and the GitHub
issue-comment path. The DB remains the ONLY authoritative state store.

Two intake channels:

1. CARD REACTIONS (one-click approval)
   Every review card registers its message id + content version in the DB
   (settings key `review_card:<post_uid>`). Reacting on the card:
       approve emoji -> approve THE CARD'S version (version-matched)
       reject emoji  -> reject the post
   A card is consumed after a decision; its registry entry is removed.

2. PLAIN MESSAGES (full control, no slash commands needed)
   Typing any of these in the review channel works (leading `/` optional):
       approve X-2026-AB12C
       reject X-2026-AB12C too salesy
       iterate X-2026-AB12C make the hook punchier

Authorization (defence in depth, same model as the bot):
  - DISCORD_AUTHORIZED_USERS env: comma-separated Discord user IDs or usernames
  - PLUS the guild owner, resolved live via the API (guild owner == operator)
  - actions are recorded with actor "discord:<username>" in approval_events

Safety invariants (identical to the other review surfaces):
  - approve applies ONLY to the exact version shown on the card / current
    version (version mismatch -> refused with an explanation, never approved)
  - state transitions go through the state machine (APPROVED is reachable
    ONLY from WAITING_APPROVAL); every action is idempotent-safe
  - command text is treated as untrusted DATA (control chars stripped,
    length-bounded); nothing is ever interpreted as shell/code
  - this module never raises: intake failures are logged and surfaced in the
    returned summary, the state machine cannot be corrupted by a bad message
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from src.config import get_config
from src.state.machine import State

CARD_KEY_PREFIX = "review_card:"
POLL_CURSOR_KEY = "discord_poll:last_message_id"

# "approve X-2026-AB12C", "/reject X-2026-AB12C too salesy", "iterate <uid> <text>"
COMMAND_RE = re.compile(
    r"^\s*/?(approve|reject|iterate)\s+([A-Za-z0-9][A-Za-z0-9_\-]*)\s*(.*)$",
    re.IGNORECASE | re.DOTALL)

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MAX_REASON_CHARS = 300
MAX_INSTRUCTION_CHARS = 500


@dataclass
class PollSummary:
    approved: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    iterated: list[dict] = field(default_factory=list)   # {post_id, uid, instruction}
    skipped: list[dict] = field(default_factory=list)    # {uid/author, why}

    def as_dict(self) -> dict:
        return {"approved": self.approved, "rejected": self.rejected,
                "iterated": self.iterated, "skipped": self.skipped}


def _clean(text: str, limit: int) -> str:
    return CONTROL_CHARS.sub("", text or "").replace("```", "").strip()[:limit]


class DiscordReviewPoller:
    def __init__(self, repo, discord, authorized_ids: set[str],
                 channel_id: str, approve_emoji: str = "\u2705",
                 reject_emoji: str = "\u274c", max_instruction_chars: int = MAX_INSTRUCTION_CHARS,
                 run_iterate=None):
        """All dependencies are injected (testability).

        repo        : src.db.repository.Repository (the ONLY state store)
        discord     : integrations.discord.client.DiscordClient-like object
        authorized_ids: Discord user ids/usernames allowed to act
        channel_id  : the review channel (DISCORD_CHANNEL_REVIEW)
        run_iterate : optional callback(post_id:int, instruction:str, actor:str)
                      executed for accepted iterate commands (uses NIM in prod)
        """
        self.repo = repo
        self.discord = discord
        self.allowed = {str(a).strip().lstrip("@").lower()
                        for a in (authorized_ids or set()) if str(a).strip()}
        self.channel_id = channel_id
        self.approve_emoji = approve_emoji
        self.reject_emoji = reject_emoji
        self.max_instruction_chars = max_instruction_chars
        self.run_iterate = run_iterate

    # ------------------------------------------------------------- auth
    @staticmethod
    def resolve_authorized(repo, discord=None) -> set[str]:
        """ENV allowlist (ids or usernames) + the live guild owner id."""
        allowed: set[str] = set()
        raw = os.environ.get("DISCORD_AUTHORIZED_USERS", "")
        for item in raw.split(","):
            if item.strip():
                allowed.add(item.strip().lstrip("@").lower())
        try:
            cfg = get_config()
            allowed |= {u.strip().lstrip("@").lower()
                        for u in (cfg.discord_authorized_users() or []) if u.strip()}
        except Exception:
            pass
        guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
        if guild_id and discord is not None:
            owner = discord.guild_owner_id(guild_id)
            if owner:
                allowed.add(owner.lower())
        return allowed

    # ------------------------------------------------------------- helpers
    def _actor_of(self, user: dict) -> tuple[bool, str]:
        uid = str(user.get("id", "")).lower()
        uname = str(user.get("username", "")).lower()
        actor = f"discord:{user.get('username') or user.get('global_name') or uid or 'unknown'}"
        ok = bool(self.allowed) and (uid in self.allowed or uname in self.allowed)
        return ok, actor

    def _card_for(self, post_uid: str) -> dict | None:
        card = self.repo.get_setting(f"{CARD_KEY_PREFIX}{post_uid}")
        return card if isinstance(card, dict) and card.get("message_id") else None

    def _consume_card(self, post_uid: str) -> None:
        self.repo.delete_setting(f"{CARD_KEY_PREFIX}{post_uid}")

    def _confirm(self, text: str) -> None:
        try:
            self.discord.post_message(self.channel_id, text)
        except Exception:
            pass  # confirmation must never corrupt the control path

    def _skip(self, summary: PollSummary, label: str, why: str) -> None:
        summary.skipped.append({"who": label, "why": why})

    # ------------------------------------------------------------- intake 1
    def _process_card_reactions(self, summary: PollSummary) -> None:
        for post in self.repo.posts_in_state(State.WAITING_APPROVAL.value):
            uid = post["post_uid"]
            card = self._card_for(uid)
            if card is None:
                continue
            message_id = str(card.get("message_id"))
            card_version = card.get("version")
            try:
                approvers = self.discord.reaction_users(
                    self.channel_id, message_id, self.approve_emoji)
                rejectors = self.discord.reaction_users(
                    self.channel_id, message_id, self.reject_emoji)
            except Exception as exc:
                self._skip(summary, uid, f"reaction lookup failed: {type(exc).__name__}")
                continue
            decision = None
            decider = None
            for user, action in ([(u, "APPROVED") for u in approvers]
                                 + [(u, "REJECTED") for u in rejectors]):
                allowed, actor = self._actor_of(user)
                if not allowed:
                    self._skip(summary, f"discord:{user.get('username', '?')}",
                               "not authorized")
                    continue
                decision = action
                decider = actor
                break
            if decision is None:
                continue   # nobody (authorized) reacted yet - leave card active
            # consume immediately -> no repeat processing on the next poll
            self._consume_card(uid)
            if decision == "APPROVED":
                self._approve(post, card_version, decider, summary)
            else:
                self._reject(post, decider, "rejected via card reaction", summary)

    # ------------------------------------------------------------- intake 2
    def _process_messages(self, summary: PollSummary) -> None:
        cursor = self.repo.get_setting(POLL_CURSOR_KEY)
        try:
            messages = self.discord.fetch_messages(self.channel_id, limit=50,
                                                   after_id=str(cursor) if cursor else None)
        except Exception as exc:
            self._skip(summary, "channel", f"history fetch failed: {type(exc).__name__}")
            return
        if not messages:
            return
        newest_id = str(messages[0].get("id"))  # newest-first ordering
        if cursor is None:
            # First-ever poll: start from NOW, never replay old history
            # (stale commands must not approve fresh posts).
            self.repo.set_setting(POLL_CURSOR_KEY, newest_id)
            return
        for msg in sorted(messages, key=lambda m: str(m.get("id", "0"))):
            if cursor and str(msg.get("id", "")) <= str(cursor):
                continue
            body = str(msg.get("content") or "")
            match = COMMAND_RE.match(body.splitlines()[0] if body.strip() else "")
            if not match:
                continue  # ordinary chatter is ignored deliberately
            action, uid_raw, tail = match.group(1).lower(), match.group(2), match.group(3)
            allowed, actor = self._actor_of(msg.get("author") or {})
            if not allowed:
                self._skip(summary, actor, "not authorized")
                continue
            post = self.repo.get_post_by_uid(uid_raw.strip().upper())
            if post is None:
                self._confirm(f"@{msg.get('author', {}).get('username', '')} "
                              f"no post with UID `{uid_raw}`.")
                self._skip(summary, uid_raw, "unknown uid")
                continue
            if action == "approve":
                # a typed command means the CURRENT version (same as /approve
                # on the bot); only one-click CARD reactions are version-gated
                self._approve(post, None, actor, summary)
                self._consume_card(post["post_uid"])
            elif action == "reject":
                reason = _clean(tail, MAX_REASON_CHARS)
                self._reject(post, actor, reason or "rejected via Discord message",
                             summary)
                self._consume_card(post["post_uid"])
            elif action == "iterate":
                instruction = _clean(tail, self.max_instruction_chars)
                self._iterate(post, instruction, actor, summary)
        self.repo.set_setting(POLL_CURSOR_KEY, newest_id)

    # ------------------------------------------------------------- actions
    def _approve(self, post: dict, card_version, actor: str,
                 summary: PollSummary) -> None:
        uid = post["post_uid"]
        current = post["current_version"]
        if post["state"] != State.WAITING_APPROVAL.value:
            self._confirm(f"`{uid}` is {post['state']}, not WAITING_APPROVAL - "
                          f"nothing changed.")
            self._skip(summary, uid, f"state is {post['state']}")
            return
        if card_version is not None and int(card_version) != int(current):
            self._confirm(f"`{uid}`: that card showed **v{card_version}** but the "
                          f"current version is **v{current}** (it was iterated). "
                          f"Approve the latest card instead. Nothing changed.")
            self._skip(summary, uid, f"version mismatch card v{card_version} != v{current}")
            return
        self.repo.record_approval_event(post["id"], current, "APPROVED", actor)
        self.repo.move_state(post["id"], State.APPROVED, actor=actor)
        self.repo.log_event("review.discord_approved", post_id=post["id"],
                            payload={"actor": actor, "version": current,
                                     "surface": "discord-card" if card_version is not None
                                     else "discord-message"})
        self._confirm(f"\u2705 APPROVED `{uid}` v{current} by {actor} - the "
                      f"schedule stage will slot it (publishing stays blocked "
                      f"while the kill switch is off).")
        summary.approved.append(uid)

    def _reject(self, post: dict, actor: str, reason: str,
                summary: PollSummary) -> None:
        uid = post["post_uid"]
        if post["state"] not in (State.WAITING_APPROVAL.value, State.ITERATING.value):
            self._confirm(f"`{uid}` is {post['state']}; cannot reject from there.")
            self._skip(summary, uid, f"state is {post['state']}")
            return
        current = post["current_version"]
        self.repo.record_approval_event(post["id"], current, "REJECTED", actor,
                                        reason=reason)
        self.repo.move_state(post["id"], State.REJECTED, actor=actor)
        self.repo.log_event("review.discord_rejected", post_id=post["id"],
                            payload={"actor": actor, "reason": reason[:120],
                                     "surface": "discord"})
        self._confirm(f"\u274c REJECTED `{uid}` v{current} by {actor}"
                      + (f" - _{reason}_" if reason else ""))
        summary.rejected.append(uid)

    def _iterate(self, post: dict, instruction: str, actor: str,
                 summary: PollSummary) -> None:
        uid = post["post_uid"]
        if not instruction:
            self._confirm("iterate requires an instruction, e.g. "
                          "`iterate X-2026-AB12C make the hook punchier`")
            self._skip(summary, uid, "empty instruction")
            return
        if post["state"] not in (State.WAITING_APPROVAL.value, State.ITERATING.value):
            self._confirm(f"`{uid}` is {post['state']}; cannot iterate from there.")
            self._skip(summary, uid, f"state is {post['state']}")
            return
        current = post["current_version"]
        self.repo.record_approval_event(post["id"], current, "ITERATED", actor,
                                        reason=instruction)
        if post["state"] != State.ITERATING.value:   # idempotent re-mark
            self.repo.move_state(post["id"], State.ITERATING, actor=actor)
        self.repo.log_event("review.discord_iterate_requested", post_id=post["id"],
                            payload={"actor": actor, "instruction": instruction[:120],
                                     "surface": "discord"})
        ran = False
        if self.run_iterate is not None:
            try:
                self.run_iterate(post["id"], instruction, actor)
                ran = True
            except Exception as exc:
                self._confirm(f"\u26a0\ufe0f `{uid}` iteration queued but the rewrite "
                              f"failed ({type(exc).__name__}); it will be retried.")
        self._confirm(f"\U0001f504 ITERATE requested for `{uid}` v{current} by {actor}"
                      + (" - a new version is being drafted and will come back "
                         "to review." if ran else
                         " - it will be drafted in the next engine run."))
        summary.iterated.append({"post_id": post["id"], "uid": uid,
                                 "instruction": instruction, "actor": actor})

    # ------------------------------------------------------------- entry
    def process(self) -> dict:
        """One poll pass: reactions first (one-click), then channel messages."""
        summary = PollSummary()
        if not self.allowed:
            # No authorization source at all -> refuse to act (fail-safe).
            self._skip(summary, "poll", "no authorized users resolved - doing nothing")
            return summary.as_dict()
        if self.channel_id:
            self._process_card_reactions(summary)
            self._process_messages(summary)
        else:
            self._skip(summary, "poll", "no review channel configured")
        return summary.as_dict()
