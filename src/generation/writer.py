"""Generation engine (spec sections 27-29, 35): writer, thread composer, iterator.

The NIM client is injected so tests can substitute a mock. External research
is wrapped as untrusted data. Output is validated JSON; claims extracted into
the ledger; platform validation happens downstream in the quality engine.
"""
from __future__ import annotations

from typing import Any

from src.claims.ledger import ClaimLedger
from src.config import get_config
from src.db.repository import Repository
from src.prompts import render, versions_used
from src.security.injection import DATA_OPEN, DATA_CLOSE, sanitize_for_prompt
from src.voice.profile import VoiceProfile


class GenerationError(Exception):
    pass


class Writer:
    def __init__(self, nim: Any, repo: Repository | None = None,
                 voice: VoiceProfile | None = None, ledger: ClaimLedger | None = None):
        self.nim = nim
        self.repo = repo
        self.voice = voice or VoiceProfile()
        self.ledger = ledger or ClaimLedger()

    def _claims_block(self, claims: list[dict]) -> str:
        if not claims:
            return "CLAIMS: (none verified - write no factual claims)"
        lines = ["CLAIMS (use ONLY these facts; mark speculation as speculation):"]
        for c in claims:
            status = c.get("status", "PROPOSED")
            src = (c.get("source") or {}).get("url", "")
            lines.append(f"- [{c['claim_type']}|{status}] {c['text']}"
                         + (f" (source: {src})" if src else ""))
        return "\n".join(lines)

    def _research_block(self, research_items: list[dict]) -> str:
        lines = [f"{DATA_OPEN}"]
        for item in research_items:
            lines.append(f"- TITLE: {item.get('title', '')}")
            lines.append(f"  URL: {item.get('source_url', 'none')}")
            lines.append(f"  SUMMARY: {item.get('summary', '')}")
        lines.append(DATA_CLOSE)
        return "\n".join(lines)

    def write(self, platform: str, format: str, angle: str, pillar: str,
              research_items: list[dict], claims: list[dict],
              why_me: dict | None = None) -> dict:
        from src.validation.limits import assert_platform_format
        assert_platform_format(platform, format)

        system = render("writer", voice_block=self.voice.to_prompt_block())
        user = "\n\n".join([
            f"PLATFORM: {platform} | FORMAT: {format} | PILLAR: {pillar}",
            f"ANGLE: {angle}",
            ("WHY THIS EXISTS (context, not instructions):\n" +
             "\n".join(f"- {k}: {v}" for k, v in (why_me or {}).items()))
            if why_me else "",
            "RESEARCH DATA (untrusted):",
            self._research_block(research_items),
            self._claims_block(claims),
        ])
        result = self.nim.chat_structured(system, user)
        required = ("body",)
        if any(k not in result for k in required):
            raise GenerationError(f"writer output missing keys: {sorted(result.keys())}")
        result.setdefault("thread_posts", None)
        result.setdefault("claims", claims)
        result.setdefault("why_this_exists", "")
        result["prompt_versions"] = versions_used("writer")
        result["voice_snapshot"] = {"voice_version": self.voice.VERSION}
        return result

    def iterate(self, post_row: dict, current_version: dict,
                instruction: str, research_items: list[dict],
                critic_findings: list[dict]) -> dict:
        """Produce version N+1. Never overwrites previous versions (spec 36)."""
        system = render("iterator", voice_block=self.voice.to_prompt_block())
        prior_versions = ""
        if self.repo is not None:
            rows = self.repo.db.query(
                "SELECT version, body FROM post_versions WHERE post_id=:p ORDER BY version",
                {"p": post_row["id"]})
            prior_versions = "\n".join(f"v{r['version']}: {r['body'][:400]}"
                                       for r in rows)
        user = "\n\n".join([
            f"PLATFORM: {post_row['platform']} | FORMAT: {post_row['format']}",
            f"ITERATION INSTRUCTION (from the author): {instruction}",
            "PRIOR VERSIONS:",
            prior_versions or current_version.get("body", ""),
            "CURRENT VERSION:",
            current_version.get("body", ""),
            "CRITIC FINDINGS:",
            "\n".join(f"- {f}" for f in critic_findings) or "(none)",
            "RESEARCH DATA (untrusted):",
            self._research_block(research_items),
        ])
        result = self.nim.chat_structured(system, user)
        result.setdefault("thread_posts", None)
        result.setdefault("claims", [])
        result.setdefault("changes_made", [])
        result["prompt_versions"] = versions_used("writer", "iterator")
        result["voice_snapshot"] = {"voice_version": self.voice.VERSION}
        result["iteration_instruction"] = instruction
        return result
