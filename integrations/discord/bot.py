"""Interactive Discord bot - the native review surface of the publishing OS.

The DB remains the ONLY authoritative state store; Discord mirrors it and
drives the same state machine through the same repository methods.

Two interaction styles:

1. BUTTONS (easiest, while the bot process is online)
   /review lists the posts waiting for approval as cards with
   [Approve] [Iterate] [Reject] buttons. Approve applies to the shown
   version; Iterate and Reject open a small modal (instruction / reason).
   Rejecting ALWAYS triggers an instant full regeneration (see below).

2. SLASH COMMANDS
   /ping                     - liveness + kill-switch snapshot
   /status                   - kill switch, per-state counts, recent events
   /queue                    - posts waiting for approval
   /review                   - interactive approve/iterate/reject buttons
   /show <post_uid>          - current version content of a post
   /approve <post_uid>       - approve THIS version (WAITING_APPROVAL only)
   /reject <post_uid> [reason] - reject + instant regeneration
   /iterate <post_uid> <instruction> - request a new version
   /killswitch on|off|status - global publishing kill switch (audited)
   /help                     - command reference

REJECT -> INSTANT REGENERATION (user request): a rejection - here or via the
card reactions / typed commands processed by discord-approval.yml - dispatches
the full engine workflow (research -> ideas -> generate -> quality -> review)
immediately. The ideas stage reads the rejection reasons from the DB and
steers away from them, so fresh, different candidates land in #social-review
within minutes. The loop continues until the author approves.

Authorization (defence in depth):
  - DISCORD_AUTHORIZED_USERS env (comma-separated usernames or IDs) wins;
    fallback: config/security.yml authorized_users (GitHub usernames)
  - the guild owner is accepted unless DISCORD_ALLOW_GUILD_OWNER=false
  - actions are recorded with actor "discord:<username>" in approval_events

Security:
  - command arguments and modal inputs are treated as untrusted DATA (never
    shell/code), control-char-stripped and length-bounded
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
DEFAULT_REPO_SLUG = "ansaribilal14/personal-social-agent"


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


def _dispatch_regeneration(repo: Repository, reason: str, actor: str) -> dict:
    """Reject -> instant full engine re-run. Never raises; graceful when no
    GitHub token is available (falls back to the next scheduled cycle)."""
    try:
        from src.review.regenerate import dispatch_engine_run
        cfg = get_config()
        slug = os.environ.get("GITHUB_REPOSITORY", "").strip() or DEFAULT_REPO_SLUG
        return dispatch_engine_run(repo, reason=reason, actor=actor,
                                   repository=slug, token=cfg.github_token())
    except Exception as exc:
        return {"dispatched": False, "detail": type(exc).__name__}


# ------------------------------------------------------------- apply helpers
# Shared by slash commands and buttons so both surfaces behave identically.

def _apply_approve(repo: Repository, post: dict, actor: str) -> tuple[bool, str]:
    if post["state"] != State.WAITING_APPROVAL.value:
        return False, (f"Refused - `{post['post_uid']}` is {post['state']}, "
                       f"not WAITING_APPROVAL. Nothing changed.")
    version = post["current_version"]
    repo.record_approval_event(post["id"], version, "APPROVED", actor)
    repo.move_state(post["id"], State.APPROVED, actor=actor)
    repo.log_event("review.discord_approved", post_id=post["id"],
                   payload={"actor": actor, "version": version})
    return True, (f"APPROVED - `{post['post_uid']}` v{version}\n"
                  f"The schedule stage will place it in the idempotent outbox; "
                  f"publishing stays blocked while the kill switch is off.")


def _apply_reject(repo: Repository, post: dict, actor: str, reason: str) -> str:
    uid = post["post_uid"]
    version = post["current_version"]
    repo.record_approval_event(post["id"], version, "REJECTED", actor, reason=reason)
    repo.move_state(post["id"], State.REJECTED, actor=actor)
    repo.log_event("review.discord_rejected", post_id=post["id"],
                   payload={"actor": actor, "reason": reason[:120]})
    outcome = _dispatch_regeneration(repo, reason or f"{uid} rejected", actor)
    if outcome.get("dispatched"):
        regen = ("\U0001f504 Regeneration started NOW: research -> ideas -> "
                 "drafts -> quality -> review, steered away from what you "
                 "rejected. Fresh cards land in #social-review in a few minutes.")
    else:
        regen = ("\u2139\ufe0f Fresh candidates will be generated on the next "
                 f"scheduled cycle (dispatch: {outcome.get('detail', 'unknown')}).")
    return (f"REJECTED `{uid}` v{version}" + (f" - _{reason}_" if reason else "")
            + f"\n{regen}")


def _apply_iterate(repo: Repository, post: dict, actor: str, instruction: str) -> str:
    version = post["current_version"]
    repo.record_approval_event(post["id"], version, "ITERATED", actor,
                               reason=instruction)
    if post["state"] != State.ITERATING.value:  # idempotent re-mark
        repo.move_state(post["id"], State.ITERATING, actor=actor)
    repo.log_event("review.discord_iterate_requested", post_id=post["id"],
                   payload={"actor": actor, "instruction": instruction[:120]})
    return (f"ITERATE requested for `{post['post_uid']}` v{version} - "
            f"the iterate stage will draft a new version.\n"
            f"Instruction: _{instruction[:400]}_")


def _resolve_post(repo: Repository, post_uid: str) -> tuple[dict | None, str | None]:
    post = repo.get_post_by_uid(post_uid.strip().upper())
    if post is None:
        return None, f"No post with UID `{post_uid}`."
    return post, None


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


# ------------------------------------------------------------------ buttons
class IterateModal(discord.ui.Modal, title="Request a new version"):
    instruction = discord.ui.TextInput(
        label="What should change?", style=discord.TextStyle.paragraph,
        max_length=MAX_ITERATE_CHARS,
        placeholder="e.g. make the hook punchier, drop the last block")

    def __init__(self, post_uid: str):
        super().__init__()
        self.post_uid = post_uid

    async def on_submit(self, interaction: discord.Interaction) -> None:
        allowed, actor = _authorized(interaction)
        if not allowed:
            await interaction.response.send_message(
                f"Not authorized - `{actor}` is not an approved reviewer.",
                ephemeral=True)
            return
        text = _clean_instruction(str(self.instruction.value))
        if not text:
            await interaction.response.send_message(
                "iterate requires an instruction.", ephemeral=True)
            return
        repo = _repo()
        post, err = _resolve_post(repo, self.post_uid)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if post["state"] not in (State.WAITING_APPROVAL.value, State.ITERATING.value):
            await interaction.response.send_message(
                f"Refused - `{self.post_uid}` is {post['state']}; "
                f"cannot iterate from there. Nothing changed.", ephemeral=True)
            return
        await interaction.response.send_message(
            _apply_iterate(repo, post, actor, text), ephemeral=False)


class RejectModal(discord.ui.Modal, title="Reject + instant regeneration"):
    reason = discord.ui.TextInput(
        label="Why reject? (steers the regeneration)", style=discord.TextStyle.paragraph,
        required=False, max_length=300,
        placeholder="e.g. sounds like an essay, no hook, already known")

    def __init__(self, post_uid: str):
        super().__init__()
        self.post_uid = post_uid

    async def on_submit(self, interaction: discord.Interaction) -> None:
        allowed, actor = _authorized(interaction)
        if not allowed:
            await interaction.response.send_message(
                f"Not authorized - `{actor}` is not an approved reviewer.",
                ephemeral=True)
            return
        reason = _clean_instruction(str(self.reason.value))[:300]
        repo = _repo()
        post, err = _resolve_post(repo, self.post_uid)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if post["state"] not in (State.WAITING_APPROVAL.value, State.ITERATING.value):
            await interaction.response.send_message(
                f"Refused - `{self.post_uid}` is {post['state']}; "
                f"cannot reject from there. Nothing changed.", ephemeral=True)
            return
        await interaction.response.send_message(
            _apply_reject(repo, post, actor, reason), ephemeral=False)


class ReviewDecisionView(discord.ui.View):
    """One-click decision buttons under a review card (bot process online)."""

    def __init__(self, post_uid: str):
        super().__init__(timeout=None)  # persistent: survives restarts by custom_id
        self.post_uid = post_uid

    @discord.ui.button(label="Approve", emoji="\u2705",
                       style=discord.ButtonStyle.success,
                       custom_id="socialos:approve")
    async def approve_button(self, interaction: discord.Interaction,
                             button: discord.ui.Button) -> None:
        allowed, actor = _authorized(interaction)
        if not allowed:
            await interaction.response.send_message(
                f"Not authorized - `{actor}` is not an approved reviewer.",
                ephemeral=True)
            return
        repo = _repo()
        post, err = _resolve_post(repo, self.post_uid)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        ok, msg = _apply_approve(repo, post, actor)
        await interaction.response.send_message(
            msg, ephemeral=False,
            embed=discord.Embed(title=f"{self.post_uid} decision",
                                description=msg, color=EMBED_GREEN if ok else EMBED_RED))

    @discord.ui.button(label="Iterate", emoji="\U0001f504",
                       style=discord.ButtonStyle.primary,
                       custom_id="socialos:iterate")
    async def iterate_button(self, interaction: discord.Interaction,
                             button: discord.ui.Button) -> None:
        await interaction.response.send_modal(IterateModal(self.post_uid))

    @discord.ui.button(label="Reject", emoji="\u274c",
                       style=discord.ButtonStyle.danger,
                       custom_id="socialos:reject")
    async def reject_button(self, interaction: discord.Interaction,
                            button: discord.ui.Button) -> None:
        await interaction.response.send_modal(RejectModal(self.post_uid))


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
    embed.set_footer(text="Use /review for approve/iterate/reject buttons")
    await _reply(interaction, embed=embed)


@bot.tree.command(name="review", description="Interactive review: approve / iterate / reject buttons")
async def review(interaction: discord.Interaction) -> None:
    repo = _repo()
    waiting = repo.posts_in_state(State.WAITING_APPROVAL.value)
    if not waiting:
        await _reply(interaction, "Queue is empty - nothing is waiting for approval.")
        return
    await _reply(interaction, f"{len(waiting)} post(s) to review - "
                              f"buttons below \U0001f447 (reject = instant "
                              f"fresh batch)", ephemeral=False)
    for p in waiting[:5]:
        version = repo.latest_version(p["id"])
        if version is None:
            continue
        embed = _post_embed(p, version, EMBED_GREEN)
        embed.set_footer(text="Approve applies to the version shown. "
                              "Reject regenerates everything immediately.")
        try:
            await interaction.followup.send(embed=embed,
                                            view=ReviewDecisionView(p["post_uid"]),
                                            ephemeral=False)
        except Exception:
            break


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
    post, err = _resolve_post(repo, post_uid)
    if err:
        await _reply(interaction, err)
        return
    ok, msg = _apply_approve(repo, post, actor)
    await _reply(interaction, msg)


@bot.tree.command(name="reject", description="Reject a post - regenerates fresh candidates immediately")
@app_commands.describe(post_uid="Post UID",
                       reason="Why it is rejected (optional; steers the regeneration)")
async def reject(interaction: discord.Interaction, post_uid: str,
                 reason: str = "") -> None:
    allowed, actor = _authorized(interaction)
    if not allowed:
        await _reply(interaction, f"Not authorized - `{actor}` is not an approved reviewer.")
        return
    repo = _repo()
    post, err = _resolve_post(repo, post_uid)
    if err:
        await _reply(interaction, err)
        return
    if post["state"] not in (State.WAITING_APPROVAL.value, State.ITERATING.value):
        await _reply(interaction, f"Refused - `{post_uid}` is {post['state']}; "
                                  f"cannot reject from there. Nothing changed.")
        return
    reason = _clean_instruction(reason)[:300]
    await _reply(interaction, _apply_reject(repo, post, actor, reason))


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
    post, err = _resolve_post(repo, post_uid)
    if err:
        await _reply(interaction, err)
        return
    if post["state"] not in (State.WAITING_APPROVAL.value, State.ITERATING.value):
        await _reply(interaction, f"Refused - `{post_uid}` is {post['state']}; "
                                  f"cannot iterate from there. Nothing changed.")
        return
    await _reply(interaction, _apply_iterate(repo, post, actor, instruction))


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
        "/review - approve / iterate / reject with BUTTONS (easiest)\n"
        "/queue - what is waiting for approval\n"
        "/show <uid> - read the exact current version\n"
        "/approve <uid> - approve THIS version (WAITING_APPROVAL only)\n"
        "/reject <uid> [reason] - reject + INSTANT full regeneration\n"
        "/iterate <uid> <instruction> - request a new version\n"
        "/killswitch on|off|status - global publishing switch (audited)\n"
        "/status - pipeline snapshot   /ping - liveness\n\n"
        "Review cards in #social-review also accept one-tap \u2705/\u274c "
        "reactions and typed commands (`reject <uid> reason`).\n"
        "Rejecting ALWAYS triggers an immediate re-run so you keep getting "
        "new choices until you approve.\n\n"
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
