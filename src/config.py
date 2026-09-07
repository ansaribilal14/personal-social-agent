"""Central configuration loader.

All knobs live in config/*.yml (spec section 60). Nothing buried in code.
Secrets NEVER come from here - only from environment (GitHub Secrets).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


class Config:
    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or CONFIG_DIR
        self._cache: dict[str, dict[str, Any]] = {}

    def section(self, name: str) -> dict[str, Any]:
        if name not in self._cache:
            path = self.config_dir / f"{name}.yml"
            if not path.exists():
                raise FileNotFoundError(f"missing config file: {path}")
            with path.open("r", encoding="utf-8") as fh:
                self._cache[name] = yaml.safe_load(fh) or {}
        return self._cache[name]

    # convenience accessors -------------------------------------------------
    @property
    def platforms(self) -> dict:
        return self.section("platforms")

    @property
    def strategy(self) -> dict:
        return self.section("strategy")

    @property
    def voice(self) -> dict:
        return self.section("voice")

    @property
    def quality(self) -> dict:
        return self.section("quality")

    @property
    def schedule(self) -> dict:
        return self.section("schedule")

    @property
    def security(self) -> dict:
        return self.section("security")

    def platform(self, name: str) -> dict:
        platforms = self.platforms
        if name not in platforms:
            raise KeyError(f"unknown platform '{name}' in platforms.yml")
        return platforms[name]

    # environment / secrets --------------------------------------------------
    @staticmethod
    def env(name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, default)

    @property
    def nim_api_key(self) -> str | None:
        return self.env("NVIDIA_API_KEY")

    @property
    def buffer_api_key(self) -> str | None:
        return self.env("BUFFER_API_KEY")

    @property
    def discord_webhook_url(self) -> str | None:
        return self.env("DISCORD_WEBHOOK_URL")

    @property
    def github_token(self) -> str | None:
        # Actions injects GITHUB_TOKEN automatically; PAT override supported.
        return self.env("GITHUB_TOKEN") or self.env("GH_PAT")

    @property
    def github_repo(self) -> str | None:
        return self.env("GITHUB_REPOSITORY")

    @property
    def database_url(self) -> str | None:
        return (
            self.env("DATABASE_URL")
            or self.env("SUPABASE_DB_URL")
        )

    @property
    def timezone_name(self) -> str:
        return self.platforms.get("timezone", "Asia/Kolkata")

    def authorized_users(self) -> list[str]:
        override = self.env("AUTHORIZED_REVIEWERS")
        if override:
            return [u.strip().lstrip("@") for u in override.split(",") if u.strip()]
        return [
            u.lstrip("@")
            for u in self.security.get("approval", {}).get("authorized_users", [])
        ]


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
