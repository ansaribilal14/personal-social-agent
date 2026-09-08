"""Anti-AI-slop engine (spec section 23) - first-class, layered.

Layer 1 (deterministic): banned phrases, emoji/em-dash/exclamation caps,
engagement bait, fake urgency, hashtag caps - from config/quality.yml.
Layer 2 (NIM, in critics): "does this sound like a real person with a reason
to post?" and "does it carry an actual observation/opinion/insight?"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F02F"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF]")

ENGAGEMENT_BAIT = [
    r"\brt if\b", r"\blike if\b", r"\bfollow (me|for)\b", r"\bcomment below\b",
    r"\btag someone\b", r"\bshare this\b", r"\bsmash that\b", r"\blink in bio\b",
]
FAKE_URGENCY = [
    r"\bbefore it'?s too late\b", r"\byou won'?t believe\b", r"\bact now\b",
    r"\bhurry\b", r"\blast chance\b", r"\bthis changes everything\b",
    r"\bshocking\b", r"\bgoing viral\b",
]
GENERIC_OPENERS = [
    r"^in today'?s (fast-paced |modern )?world",
    r"^we'?ve all been there",
    r"^let'?s (talk about|dive into|deep dive)",
    r"^hot take:",
    r"^as an ai\b",
]


@dataclass
class SlopReport:
    passed: bool
    issues: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


class AntiSlopEngine:
    def __init__(self, rules: dict | None = None):
        if rules is None:
            from src.config import get_config
            rules = get_config().quality.get("anti_slop", {})
        self.rules = rules

    def check_text(self, text: str, allow_personal_experience: bool = True) -> SlopReport:
        issues: list[str] = []
        evidence: list[str] = []
        if not text or not text.strip():
            return SlopReport(False, ["empty text"])

        banned = self.rules.get("banned_phrases", [])
        low = text.lower()
        for phrase in banned:
            if phrase.lower() in low:
                issues.append(f"banned phrase: '{phrase}'")
                evidence.append(phrase)

        emoji_count = len(EMOJI_RE.findall(text))
        if emoji_count > int(self.rules.get("max_emoji_count", 0)):
            issues.append(f"{emoji_count} emojis (max {self.rules.get('max_emoji_count', 0)})")

        em_dashes = text.count("—") + text.count("–")
        if em_dashes > int(self.rules.get("max_em_dash_per_post", 1)):
            issues.append(f"{em_dashes} em dashes (max {self.rules.get('max_em_dash_per_post', 1)})")

        excl = text.count("!")
        if excl > int(self.rules.get("max_exclamation_marks", 0)):
            issues.append(f"{excl} exclamation marks (max {self.rules.get('max_exclamation_marks', 0)})")

        hashtags = text.count("#")
        if hashtags > int(self.rules.get("max_hashtags", 2)):
            issues.append(f"{hashtags} hashtags (max {self.rules.get('max_hashtags', 2)})")

        if self.rules.get("ban_engagement_bait"):
            for pat in ENGAGEMENT_BAIT:
                if re.search(pat, low):
                    issues.append("engagement bait: " + pat)
        if self.rules.get("ban_fake_urgency"):
            for pat in FAKE_URGENCY:
                if re.search(pat, low):
                    issues.append("fake urgency: " + pat)

        for pat in GENERIC_OPENERS:
            if re.search(pat, low):
                issues.append("generic opener pattern: " + pat)

        # Fabricated personal experience: first-person experience verbs without
        # a backing PERSONAL_EXPERIENCE claim.
        if not allow_personal_experience and \
                re.search(r"\b(i (built|shipped|tried|tested|learned)|my (team|project))\b", low):
            issues.append("first-person experience claim without PERSONAL_EXPERIENCE claim")

        return SlopReport(not issues, issues, evidence)


def substantive_ratio(text: str) -> float:
    """Share of sentences that carry actual content (spec section 23 questions).

    A sentence counts as substantive when it contains a number, a claim verb,
    an opinion marker, an analysis noun (cost/trust/ownership/...), or a real
    question word - and is long enough to carry meaning at all (>= 4 words,
    so fragments like "Strong take." never count). Calibrated on live drafts:
    dense analytical sentences without the original marker words must count,
    or every well-written opinion post gets flagged as slop.
    """
    sentences = [s for s in re.split(r"[.!?\n]", text or "") if s.strip()]
    if not sentences:
        return 0.0
    markers = re.compile(
        r"\d|\b(because|which|means|should|must|isn'?t|doesn'?t|actually|"
        r"instead|problem|reason|evidence|data|results?|worse|better|wrong|right|"
        r"cost|trade|trade-?offs?|ownership|owning|profit|power|control|trust|"
        r"privacy|surveillance|anxiety|risk|attention|asset|persists?|outlasts?|"
        r"erodes?|overrides?|solves?|limits?|bottleneck|floor|"
        r"who|why|how)\b",
        re.IGNORECASE)
    hit = sum(1 for s in sentences
              if len(re.findall(r"[A-Za-z0-9']+", s)) >= 4 and markers.search(s))
    return hit / len(sentences)
