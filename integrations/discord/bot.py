"""Interactive Discord bot - the native review surface of the publishing OS.

Complements the GitHub-Issue review surface (spec sections 32-36): the DB
remains the ONLY authoritative state store; Discord mirrors it and drives the
same state machine through the same repository methods.

Commands (slash, ephemeral responses for actions):
  /ping                     - liveness + kill-switch snapshot
  /status                   - kill switch, per-state counts, recent events
  /queue                    - posts waiting for approval
  /show <post_uid>          - current version content of a post
  /approve <post_uid>       - approve THIS version (WAITING_APPROVAL only)
  /reject <post_uid> <reason> - reject (WAITING_APPROVAL / ITERATING)
  /iterate <post_uid> <instruction> - request a new version
  /killswitch on|off|status - global publishing kill switch (audited)
  /help                     - command reference

Authorization (defence in depth):
  - DISCORD_AUTHORIZED_USERS env (comma-separated usernames or IDs) wins;
    fallback: config/security.yml authorized_users (GitHub usernames)
  - the guild owner is accepted unless DISCORD_ALLOW_GUILD_OWNER=false
  - actions are recorded with actor "discord:<username>" in approval_events

Security:
  - command arguments are treated as untrusted DATA (never shell/code)
  - iterate instructions are control-char-stripped and length-bounded
  - no path here can bypass the state machine (move_state validates every
    transition; APPROVED is reachable ONLY from WAITING_APPROVAL)
"""
from __future__ import annotations

import os
import re

import discord
from discord import app_commands
from discord.ext import commands

from src.config import get_config
from src.db.connection import get_db
from src.db.repository import Repository
from src.state.machine import State

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_ITERATE_CHARS = 500
EMBED_BLUE = discord.Color.from_rgb(59, 130, 246)
EMBED_GREEN = discord.Color.from_rgb(34, 197, 94)
EMBED_RED = discord.Color.from_rgb(239, 68, 68)
EMBER_ORANGE = discord.Color.from_rgb(249, 115, 22)


def _repo() -> Repository:
    return Repository(get_db())


def _authorized(interaction: discord.Interaction) -> tuple[bool, str]:
    """Return (allowed, actor). actor is recorded in approval_events."""
    user = interaction.user
    name = getattr(user, "name", "") or str(user)
    actor = f"discord:{name}"
    allowed: set[str] = {u.strip().lstrip("@").lower()
                         for u in os.environ.get("DISCORD_AUTHORIZED_USERS", "").split(",")
                         if u.strip()}
    if not allowed:
        try:
            allowed = {u.lstrip("@").lower() for u in get_config().authorized_users()}
        except Exception:
            allowed = set()
    if name.lower() in allowed or str(user.id) in allowed:
        return True, actor
    guild = interaction.guild
    if guild is not None and guild.owner_id == user.id and \
            os.environ.get("DISCORD_ALLOW_GUILD_OWNER", "true").lower() != "false":
        return True, actor
    return False, actor


def _clean_instruction(text: str) -> str:
    text = CONTROL_CHARS.sub("", text or "")
    text = re.sub(r"```", "", text)
    return text.strip()[:MAX_ITERATE_CHARS]


def _post_embed(post: dict, version: dict, color: discord.Color) -> discord.Embed:
    posts = version.get("thread_posts") or [version.get("body", "")]
    body = "\n\n".join(
        f"**{i}/{len(posts)}**\n{p}" for i, p in enumerate(posts, 1))
    if len(body) > 3900:
        body = body[:3897] + "..."
    embed = discord.Embed(title=f"{post['post_uid']} - {post['platform'].upper()} "
                                f"{post['format'].upper()} v{post['current_version']}",
                          description=body or "*(empty)*", color=color)
    embed.add_field(name="Pillar", value=str(post.get("pillar") or "-"), inline=True)
    embed.add_field(name="Editorial score", value=str(post.get("editorial_score") or "-"),
                    inline=True)
    embed.add_field(name="State", value=str(post.get("state")), inline=True)
    why = str(version.get("why_this_exists") or "-")[:400]
    embed.add_field(name="Why this exists", value=why, inline=False)
    return embed


class SocialOSBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
        if guild_id.isdigit():
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"synced {len(synced)} slash commands to guild {guild_id} (instant)")
        else:
            synced = await self.tree.sync()
            print(f"synced {len(synced)} global slash commands "
                  f"(may take up to 1h to appear everywhere)")

    async def on_ready(self) -> None:
        print(f"Discord bot logged in as {self.user} (id: {self.user.id})")
        print(f"connected to {len(self.guilds)} guild(s)")
        try:
            await self.change_presence(activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="/help - Social Publishing OS"))
        except Exception:
            pass


bot = SocialOSBot()


async def _reply(interaction: discord.Interaction, text: str = "",
                 embed: discord.Embed | None = None, ephemeral: bool = True) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content=text or None, embed=embed,
                                        ephemeral=ephemeral)
    else:
        await interaction.response.send_message(content=text or None, embed=embed,
                                                ephemeral=ephemeral)


# ------------------------------------------------------------------ commands
@bot.tree.command(name="ping", description="Liveness check")
async def ping(interaction: discord.Interaction) -> None:
    from src.security.kill_switch import is_publishing_enabled
    repo = _repo()
    try:
        enabled = is_publishing_enabled(repo)
        switch = "ENABLED" if enabled else "DISABLED (fail-safe)"
    except Exception:
        switch = "UNKNOWN (db unreadable - publishing blocked)"
    await _reply(interaction, f"Pong - `{round(bot.latency * 1000)}ms` - "
                              f"kill switch: **{switch}**")


@bot.tree.command(name="status", description="Pipeline status snapshot")
async def status(interaction: discord.Interaction) -> None:
    repo = _repo()
    from src.security.kill_switch import is_publishing_enabled
    counts: dict[str, int] = {}
    try:
        for row in repo.db.query(
                "SELECT state, COUNT(*) AS n FROM posts GROUP BY state ORDER BY n DESC"):
            counts[str(row["state"])] = int(row["n"])
    except Exception as exc:
        await _reply(interaction, f"db error: {type(exc).__name__}")
        return
    enabled = is_publishing_enabled(repo)
    lines = [f"**Kill switch:** {'ENABLED - publishing allowed' if enabled else 'DISABLED - publishing blocked'}"]
    lines.append("**Posts by state:** " + (", ".join(f"{k} `{v}`" for k, v in counts.items()) or "none yet"))
    lines.append("**Recent events:**")
    for ev in repo.events(limit=5):
        lines.append(f"- `{ev.get('event_type')}` {str(ev.get('created_at', ''))[:19]}")
    embed = discord.Embed(title="Social Publishing OS - status",
                          description="\n".join(lines), color=EMBED_BLUE)
    await _reply(interaction, embed=embed)


@bot.tree.command(name="queue", description="Posts waiting for your approval")
async def queue(interaction: discord.Interaction) -> None:
    repo = _repo()
    waiting = repo.posts_in_state(State.WAITING_APPROVAL.value)
    iterating = repo.posts_in_state(State.ITERATING.value)
    if not waiting:
        text = "Queue is empty - nothing is waiting for approval."
        if iterating:
            text += f"\n({len(iterating)} post(s) are being iterated.)"
        await _reply(interaction, text)
        return
    lines = []
    for p in waiting:
        lines.append(f"**{p['post_uid']}** - {p['platform'].upper()} {p['format'].upper()} "
                     f"v{p['current_version']} - score `{p.get('editorial_score') or '-'}`")
    if iterating:
        lines.append(f"\n*Also iterating: {', '.join(i['post_uid'] for i in iterating)}*")
    embed = discord.Embed(title=f"{len(waiting)} post(s) waiting for approval",
                          description="\n".join(lines)[:4000], color=EMBED_ORANGE)
    embed.set_footer(text="Use /show <uid> to read, /approve <uid> to approve")
    await _reply(interaction, embed=embed)


@bot.tree.command(name="show", description="Show a post's current version")
@app_commands.describe(post_uid="Post UID, e.g. X-2026-AB12C")
async def show(interaction: discord.Interaction, post_uid: str) -> None:
    repo = _repo()
    post = repo.get_post_by_uid(post_uid.strip().upper())
    if post is None:
        await _reply(interaction, f"No post with UID `{post_uid}`.", ephemeral=True)
        return
    version = repo.latest_version(post["id"])
    if version is None:
        await _reply(interaction, f"Post `{post_uid}` has no content version yet.")
        return
    color = EMBED_GREEN if post["state"] == State.WAITING_APPROVAL.value else EMBED_BLUE
    await _reply(interaction, embed=_post_embed(post, version, color))


@bot.tree.command(name="approve", description="Approve the CURRENT version of a post")
@app_commands.describe(post_uid="Post UID, e.g. X-2026-AB12C")
async def approve(interaction: discord.Interaction, post_uid: str) -> None:
    allowed, actor = _authorized(interaction)
    if not allowed:
        await _reply(interaction, f"Not authorized - `{actor}` is not an approved reviewer.")
        return
    repo = _repo()
    post = repo.get_post_by_uid(post_uid.strip().upper())
    if post is None:
        await _reply(interaction, f"No post with UID `{post_uid}`.")
        return
    if post["state"] != State.WAITING_APPROVAL.value:
        await _reply(interaction, f"Refused - `{post_uid}` is {post['state']}, "
                                  f"not WAITING_APPROVAL. Nothing changed.")
        return
    version = post["current_version"]
    repo.record_approval_event(post["id"], version, "APPROVED", actor)
    repo.move_state(post["id"], State.APPROVED, actor=actor)
    repo.log_event("review.discord_approved", post_id=post["id"],
                   payload={"actor": actor, "version": version})
    embed = discord.Embed(
        title=f"APPROVED - {post_uid} v{version}",
        description=("The schedule stage will place it in the idempotent outbox; "
                     "publishing stays blocked while the kill switch is off."),
        color=EMBED_GREEN)
    await _reply(interaction, embed=embed)


@bot.tree.command(name="reject", description="Reject a post (with optional reason)")
@app_commands.describe(post_uid="Post UID", reason="Why it is rejected (optional)")
async def reject(interaction: discord.Interaction, post_uid: str,
                 reason: str = "") -> None:
    allowed, actor = _authorized(interaction)
    if not allowed:
        await _reply(interaction, f"Not authorized - `{actor}` is not an approved reviewer.")
        return
    repo = _repo()
    post = repo.get_post_by_uid(post_uid.strip().upper())
    if post is None:
        await _reply(interaction, f"No post with UID `{post_uid}`.")
        return
    if post["state"] not in (State.WAITING_APPROVAL.value, State.ITERATING.value):
        await _reply(interaction, f"Refused - `{post_uid}` is {post['state']}; "
                                  f"cannot reject from there. Nothing changed.")
        return
    reason = _clean_instruction(reason)[:300]
    version = post["current_version"]
    repo.record_approval_event(post["id"], version, "REJECTED", actor, reason=reason)
    repo.move_state(post["id"], State.REJECTED, actor=actor)
    repo.log_event("review.discord_rejected", post_id=post["id"],
                   payload={"actor": actor, "reason": reason[:120]})
    await _reply(interaction, f"REJECTED `{post_uid}` v{version}"
                             + (f" - _{reason}_" if reason else ""))


@bot.tree.command(name="iterate", description="Request a new version with an instruction")
@app_commands.describe(post_uid="Post UID", instruction="What to change (max 500 chars)")
async def iterate(interaction: discord.Interaction, post_uid: str,
                  instruction: str) -> None:
    allowed, actor = _authorized(interaction)
    if not allowed:
        await _reply(interaction, f"Not authorized - `{actor}` is not an approved reviewer.")
        return
    instruction = _clean_instruction(instruction)
    if not instruction:
        await _reply(interaction, "iterate requires an instruction.")
        return
    repo = _repo()
    post = repo.get_post_by_uid(post_uid.strip().upper())
    if post is None:
        await _reply(interaction, f"No post with UID `{post_uid}`.")
        return
    if post["state"] not in (State.WAITING_APPROVAL.value, State.ITERATING.value):
        await _reply(interaction, f"Refused - `{post_uid}` is {post['state']}; "
                                  f"cannot iterate from there. Nothing changed.")
        return
    version = post["current_version"]
    repo.record_approval_event(post["id"], version, "ITERATED", actor, reason=instruction)
    if post["state"] != State.ITERATING.value:  # idempotent re-mark
        repo.move_state(post["id"], State.ITERATING, actor=actor)
    repo.log_event("review.discord_iterate_requested", post_id=post["id"],
                   payload={"actor": actor, "instruction": instruction[:120]})
    await _reply(interaction, f"ITERATE requested for `{post_uid}` v{version} - "
                              f"the iterate stage will draft a new version.\n"
                              f"Instruction: _{instruction[:400]}_")


@bot.tree.command(name="killswitch", description="Global publishing kill switch")
@app_commands.describe(state="on (allow publishing) / off (block) / status")
@app_commands.choices(state=[
    app_commands.Choice(name="on - allow scheduling+publishing", value="on"),
    app_commands.Choice(name="off - block scheduling+publishing", value="off"),
    app_commands.Choice(name="status - show current state", value="status"),
])
async def killswitch(interaction: discord.Interaction, state: str) -> None:
    from src.security.kill_switch import (disable, enable, is_publishing_enabled,
                                          PublishingBlocked)
    if state == "status":
        try:
            enabled = is_publishing_enabled(_repo())
            text = "Kill switch is **ENABLED** - scheduling+publishing allowed." \
                if enabled else "Kill switch is **DISABLED** - fail-safe blocking."
        except Exception:
            text = "Kill switch state **UNREADABLE** - publishing blocked (fail-safe)."
        await _reply(interaction, text)
        return
    allowed, actor = _authorized(interaction)
    if not allowed:
        await _reply(interaction, f"Not authorized - `{actor}` is not an approved reviewer.")
        return
    repo = _repo()
    try:
        if state == "on":
            enable(repo)
        else:
            disable(repo)
    except Exception as exc:
        await _reply(interaction, f"Could not change the switch: {type(exc).__name__}")
        return
    try:
        from integrations.discord.client import DiscordClient
        DiscordClient().send_to(
            "alerts", f"{'ENABLED' if state == 'on' else 'DISABLED'} kill switch "
                      f"by {actor} via Discord /killswitch")
    except Exception:
        pass  # notification must never block the control path
    await _reply(interaction, f"Kill switch **{'ENABLED' if state == 'on' else 'DISABLED'}** "
                              f"(recorded in audit log).")


@bot.tree.command(name="help", description="How to drive the publishing OS from Discord")
async def help_cmd(interaction: discord.Interaction) -> None:
    text = (
        "**Social Publishing OS - Discord surface**\n"
        "The AI drafts, critics gate, and **you** own the publish button.\n\n"
        "/queue - what is waiting for approval\n"
        "/show <uid> - read the exact current version\n"
        "/approve <uid> - approve THIS version (WAITING_APPROVAL only)\n"
        "/reject <uid> [reason] - reject\n"
        "/iterate <uid> <instruction> - request a new version\n"
        "/killswitch on|off|status - global publishing switch (audited)\n"
        "/status - pipeline snapshot   /ping - liveness\n\n"
        "Nothing is ever published without your explicit approval of the "
        "exact version, and never while the kill switch is off."
    )
    await _reply(interaction, text, ephemeral=False)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction,
                               error: app_commands.AppCommandError) -> None:
    msg = f"command failed: {type(error).__name__}"
    try:
        await _reply(interaction, msg)
    except Exception:
        pass


def run() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is not configured (see README setup)")
    try:
        bot.run(token)
    except discord.errors.LoginFailure as exc:  # pragma: no cover
        raise SystemExit("Discord rejected the bot token - check DISCORD_BOT_TOKEN") from exc
