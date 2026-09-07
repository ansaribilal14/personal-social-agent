#!/usr/bin/env python3
"""Entrypoint for the interactive Discord bot.

Usage:
  python scripts/run_discord_bot.py

Requires: DISCORD_BOT_TOKEN (+ DISCORD_GUILD_ID for instant command sync,
DISCORD_CHANNEL_* ids for routed notifications). State comes from DATABASE_URL
(Postgres/Supabase) or SQLITE_PATH. See README "Discord bot setup".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from integrations.discord.bot import run  # noqa: E402

if __name__ == "__main__":
    run()
