"""Voice system + editorial memory + learning loop (spec sections 20-22, 46).

- STABLE VOICE: config/voice.yml (never mutated by interactions)
- EDITORIAL/LEARNED preferences: DB voice_preferences; learned ones only
  surface after >= min_signals_for_promotion consistent signals
- Rejected content NEVER becomes a positive style example
"""
from __future__ import annotations

from src.config import get_config
from src.db.repository import Repository


class VoiceProfile:
    """Builds the stable-voice prompt block from config. Versioned."""

    VERSION = "voice_stable_v2"

    def __init__(self, voice_cfg: dict | None = None, learned: list[dict] | None = None):
        self.cfg = voice_cfg if voice_cfg is not None else get_config().voice.get("voice", {})
        self.learned = learned or []

    @property
    def exemplars(self) -> list[str]:
        return [str(e) for e in (self.cfg.get("exemplars") or [])]

    def to_prompt_block(self) -> str:
        lines = ["VOICE PROFILE (stable):"]
        if self.cfg.get("persona"):
            lines.append(f"- persona: {self.cfg['persona']}")
        for key in ("tone", "sentence_length", "vocabulary", "humor", "directness",
                    "technical_depth", "opinion_strength", "punctuation", "emoji_usage",
                    "hashtag_usage"):
            if self.cfg.get(key):
                lines.append(f"- {key}: {self.cfg[key]}")
        hooks = self.cfg.get("hook_preferences") or []
        for h in hooks:
            lines.append(f"- hook rule: {h}")
        endings = self.cfg.get("ending_preferences") or []
        for e in endings:
            lines.append(f"- ending rule: {e}")
        for s in self.cfg.get("specificity") or []:
            lines.append(f"- specificity rule: {s}")
        for v in self.cfg.get("value") or []:
            lines.append(f"- value rule: {v}")
        exemplars = self.exemplars
        if exemplars:
            lines.append("EXEMPLARS OF THE TARGET QUALITY BAR (style reference "
                         "only - never copy their topics, facts, or wording):")
            for i, ex in enumerate(exemplars, 1):
                lines.append(f"  {i}. {ex}")
        forbidden = self.cfg.get("forbidden_patterns") or []
        if forbidden:
            lines.append("- forbidden patterns: " + "; ".join(forbidden))
        if self.learned:
            lines.append("LEARNED EDITORIAL PREFERENCES (promoted from your feedback):")
            for pref in self.learned:
                lines.append(f"- {pref['key']}: {pref['value']}")
        return "\n".join(lines)


class EditorialMemory:
    """What has already been said, approved, rejected (spec section 20)."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def recent_variants(self, limit: int = 200) -> list[dict]:
        return self.repo.recent_variants(limit)

    def topics_used(self, limit: int = 100) -> list[str]:
        rows = self.repo.db.query(
            "SELECT DISTINCT pillar FROM posts WHERE pillar IS NOT NULL LIMIT :n",
            {"n": limit})
        return [r["pillar"] for r in rows]

    def rejection_summary(self, limit: int = 50) -> list[dict]:
        rows = self.repo.db.query(
            "SELECT ae.reason, ae.version, p.post_uid FROM approval_events ae "
            "JOIN posts p ON p.id = ae.post_id WHERE ae.action='REJECTED' "
            "ORDER BY ae.id DESC LIMIT :n", {"n": limit})
        return rows


class LearningEngine:
    """Extracts recurring preferences from approve/reject/iterate patterns."""

    def __init__(self, repo: Repository, min_signals: int | None = None):
        self.repo = repo
        if min_signals is None:
            min_signals = get_config().strategy.get("learning", {}).get(
                "min_signals_for_promotion", 3)
        self.min_signals = int(min_signals)

    def record_feedback(self, action: str, dimension: str, value: str) -> None:
        """action: APPROVED (positive signal) | REJECTED (negative signal).

        Positive signals accumulate evidence for a learned preference.
        Negative signals accumulate against it (never as positive style
        examples - spec section 22).
        """
        pref_id = self.repo.upsert_preference(
            kind="learned", key=dimension,
            value={"prefer": value if action == "APPROVED" else f"avoid:{value}"},
            increment_evidence=1 if action == "APPROVED" else 0)
        if action == "REJECTED":
            row = self.repo.db.query(
                "SELECT evidence_count FROM voice_preferences WHERE id=:i", {"i": pref_id})
            # rejection keeps the counter but marks avoid-direction; promotion
            # still requires APPROVED-consistent evidence, handled at promotion time.
            _ = row

    def promote_eligible(self) -> list[str]:
        rows = self.repo.db.query(
            "SELECT id, key, value, evidence_count FROM voice_preferences "
            "WHERE kind='learned' AND promoted=0")
        promoted = []
        for r in rows:
            if int(r["evidence_count"]) >= self.min_signals:
                val = r["value"]
                if isinstance(val, str) and val.startswith("avoid:"):
                    continue  # rejections never promote to positive guidance
                self.repo.promote_preference(r["id"])
                promoted.append(r["key"])
        return promoted

    def promoted_preferences(self) -> list[dict]:
        return self.repo.promoted_preferences()
