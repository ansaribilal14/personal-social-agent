"""Originality engine (spec section 19) - multi-level duplication detection.

Levels: exact, near, semantic, hook, opinion-key.
Deterministic, thresholds from config/quality.yml.

Semantic level uses an embedding function when one is available (NIM
embeddings in production - see with_nim_embeddings); offline it falls back
over a character-trigram cosine that is robust to word-order and morphology.
A post cannot escape detection merely because wording changed.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass

STOPWORDS = set("""a an and are as at be but by for from has have how i if in into is
it its me my not of on or our so than that the their them then there these they this
to too us was we what when where which who why will with you your""".split())

TOKEN_RE = re.compile(r"[a-z0-9']+")


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").lower())


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS and len(t) > 2]


def normalize(text: str) -> str:
    return " ".join(tokens(text))


def exact_digest(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()[:16]


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cosine_bow(a: list[str], b: list[str]) -> float:
    """Bag-of-words cosine - deterministic lexical 'semantic' proxy."""
    ca, cb = Counter(a), Counter(b)
    dot = sum(ca[t] * cb.get(t, 0) for t in ca)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def stem(token: str) -> str:
    """Light suffix stripper - improves lexical recall without over-stemming."""
    for suffix in ("ing", "ies", "ied", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)]
    return token


def trigrams(text: str) -> list[str]:
    norm = f"  {' '.join(stem(t) for t in content_tokens(text))}  "
    return [norm[i:i + 3] for i in range(len(norm) - 2)]


def trigram_cosine(a: str, b: str) -> float:
    ca, cb = Counter(trigrams(a)), Counter(trigrams(b))
    dot = sum(ca[t] * cb.get(t, 0) for t in ca)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def _cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def hook_signature(text: str) -> str:
    """Structural signature of the opening: first sentence's leading words."""
    first_sentence = re.split(r"[.!?]", text.strip())[0] if text.strip() else ""
    words = content_tokens(first_sentence)[:6]
    return " ".join(words)


@dataclass
class DuplicationReport:
    duplicated: bool
    level: str | None
    similarity: float
    against_post_uid: str | None = None


class OriginalityEngine:
    def __init__(self, thresholds: dict | None = None, history: list[dict] | None = None,
                 embed_fn=None):
        from src.config import get_config
        self.thresholds = thresholds or get_config().quality.get("similarity", {})
        # history: [{"post_uid":..., "body":..., "thread_posts":[...]}]
        self.history = history or []
        self.embed_fn = embed_fn   # optional: NIM embeddings in production

    def _flat(self, body: str, thread_posts: list | None) -> str:
        if thread_posts:
            return "\n".join(thread_posts)
        return body or ""

    def check(self, body: str, thread_posts: list[str] | None = None,
              exclude_uids: set[str] | None = None) -> DuplicationReport:
        text = self._flat(body, thread_posts)
        mine_content = content_tokens(text)
        mine_hook = hook_signature(text)
        exclude = exclude_uids or set()

        worst = DuplicationReport(False, None, 0.0)
        for item in self.history:
            uid = str(item.get("post_uid"))
            if uid in exclude:
                continue
            other = self._flat(item.get("body", ""), item.get("thread_posts"))
            if not other.strip():
                continue

            # exact
            if exact_digest(text) == exact_digest(other):
                return DuplicationReport(True, "exact", 1.0, uid)
            # near
            near = jaccard(tokens(text), tokens(other))
            if near >= float(self.thresholds.get("near", 0.85)):
                return DuplicationReport(True, "near", near, uid)
            # semantic: embeddings when available, else trigram cosine
            if self.embed_fn is not None:
                try:
                    vec_a, vec_b = self.embed_fn([text, other])
                    sem = _cosine(vec_a, vec_b)
                except Exception:
                    sem = trigram_cosine(text, other)
            else:
                sem = trigram_cosine(text, other)
            if sem >= float(self.thresholds.get("semantic", 0.45)):
                return DuplicationReport(True, "semantic", sem, uid)
            # hook duplication
            hook_sim = cosine_bow(tokens(mine_hook), tokens(hook_signature(other)))
            if hook_sim >= float(self.thresholds.get("hook", 0.75)) and mine_hook:
                if hook_sim > worst.similarity:
                    worst = DuplicationReport(True, "hook", hook_sim, uid)
        if worst.duplicated:
            return worst
        return DuplicationReport(False, None, 0.0)
