"""Deterministic post-shape normalizer (live-run finding 2026-09).

The NIM model (nemotron-3.5-lightning-30b) reliably produces good SENTENCES
but consistently ignores whitespace structure: four revision cycles in a row
came back as single-paragraph walls of text. The native social-post shape is
partly mechanical, so code applies it:

    hook line = the first sentence (short; long ones are split at a clause)
    blocks    = remaining sentences, one or two per block
    punch     = the last, shortest line (a canned exemplar punch is dropped)
    length    = the least-substantive sentences are cut first (never the hook)

Every sentence is preserved or deliberately dropped - the normalizer never
rewrites words, so facts survive untouched. The PostShapeCritic stays as the
honest gate; after normalization it should pass on structure and only flag
genuinely unusable content.
"""
from __future__ import annotations

import re

from src.critics.antislop import (concrete_anchor_in_sentence, sentences,
                                  _word_run_overlap, _WORD_RE)

HOOK_MAX_CHARS = 100
BLOCK_SPLIT_MIN_CHARS = 60     # sentences shorter than this may share a block
PUNCH_MAX_RUN = 5              # closing verbatim-overlap with an exemplar

_CLAUSE_SPLIT_RE = re.compile(r",\s+|;\s+|\s+-\s+|\s+—\s+")

# Sentence boundary: never split before a lowercase word (protects "E. coli",
# "e.g.", "i.e." from becoming fragments - live run produced "engineered E.")
_ABBREV_MASKS = [("E. coli", "E§COLI§"), ("U.S.", "U§S§"), ("U.K.", "U§K§"),
                 ("e.g.", "e§g§"), ("i.e.", "i§e§"), ("etc.", "etc§")]


def max_post_chars(platform: str, format: str) -> int:
    """Numeric per-post budget from config/platforms.yml (the single source of
    truth; same 10-char safety margin as platform_budget)."""
    from src.config import get_config
    cfg = get_config().platforms.get(platform, {})
    single_max = int(cfg.get("single", {}).get("max_length", 280))
    if format == "thread":
        thread = cfg.get("thread", {})
        total = int(thread.get("max_total_length", 5600))
        posts = max(int(thread.get("max_posts", 18)), 1)
        return min(single_max, total // posts) - 10
    return single_max - 10


def _split_sentences(text: str) -> list[str]:
    """Sentences across the whole body. A period followed by a lowercase word
    ("E. coli", "e.g. anything") is never a boundary; masks handle the
    abbreviation cases where the follower is capitalized."""
    masked = text.strip()
    for a, b in _ABBREV_MASKS:
        masked = masked.replace(a, b)
    raw = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"\'\u201c\u2018(])|\n+', masked)
    out = []
    for s in raw:
        s = s.strip()
        for a, b in _ABBREV_MASKS:
            s = s.replace(b, a)
        if s:
            out.append(s)
    return out


def _shorten_hook(hook: str) -> str:
    """Cut a too-long first sentence at the last clause boundary that fits."""
    if len(hook) <= HOOK_MAX_CHARS:
        return hook
    parts = [p.strip() for p in _CLAUSE_SPLIT_RE.split(hook) if p.strip()]
    best = hook
    for i in range(len(parts) - 1, 0, -1):
        candidate = ", ".join(parts[:i])
        candidate = candidate.rstrip(",; ")
        if not candidate:
            continue
        if len(candidate) <= HOOK_MAX_CHARS:
            best = candidate
            break
        best = candidate
    if len(best) > HOOK_MAX_CHARS:
        # fall back to the first clause only
        best = parts[0] if parts else best
    return best.rstrip(",; ") + ("." if not best.endswith((".", "!", "?")) else "")


def _substance_score(sentence: str) -> int:
    """Cheap priority for which sentence survives a length cut (higher =
    more concrete). The hook always survives."""
    score = 0
    if re.search(r"\d", sentence):
        score += 2
    if concrete_anchor_in_sentence(sentence):
        score += 2
    words = len(_WORD_RE.findall(sentence))
    if words >= 8:
        score += 1
    return score


def _block_sentence_count(block: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", block.strip()) if s.strip()])


def _exemplar_last_blocks(exemplars: list[str] | None) -> list[str]:
    out = []
    for ex in (exemplars or []):
        blocks = [b for b in re.split(r"\n\s*\n", str(ex).strip()) if b.strip()]
        if blocks:
            out.append(blocks[-1].strip())
    return out


def _drop_canned_punch_from_blocks(blocks: list[str],
                                   exemplars: list[str] | None) -> list[str]:
    """Remove a closing sentence/block that verbatim-copies an exemplar punch."""
    ex_tails = _exemplar_last_blocks(exemplars)
    if not ex_tails or len(blocks) < 2:
        return blocks
    tail = blocks[-1].strip()
    tail_sents = [s for s in re.split(r"(?<=[.!?])\s+", tail) if s.strip()]
    # (a) the whole tail block IS the punch
    run = max((_word_run_overlap(tail, t) for t in ex_tails), default=0)
    if run >= PUNCH_MAX_RUN and len(_WORD_RE.findall(tail)) >= 4:
        return blocks[:-1]
    # (b) the punch is the tail block's last sentence
    if tail_sents:
        last = tail_sents[-1]
        run = max((_word_run_overlap(last, t) for t in ex_tails), default=0)
        if run >= PUNCH_MAX_RUN and len(_WORD_RE.findall(last)) >= 4:
            kept = " ".join(tail_sents[:-1]).strip()
            if kept:
                return blocks[:-1] + [kept]
    return blocks


def normalize_post_shape(body: str, max_chars: int,
                         exemplars: list[str] | None = None) -> str:
    """Return a shaped version of the post. Never raises; returns the input
    unchanged when it cannot be improved safely.

    Idempotent for already-shaped posts: valid blank-line structure within
    budget is returned verbatim (only a canned exemplar punch is removed), so
    the normalizer never churns formatting the model got right.
    """
    text = (body or "").strip()
    if not text:
        return body or ""

    # Fast path: already shaped (blocks, short hook, within budget)?
    blocks_raw = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks_raw) >= 2 and len(blocks_raw[0]) <= HOOK_MAX_CHARS \
            and all(_block_sentence_count(b) <= 2 for b in blocks_raw) \
            and len(text) <= max_chars:
        return "\n\n".join(
            _drop_canned_punch_from_blocks(blocks_raw, exemplars)).strip()

    sents = _split_sentences(text)
    if not sents:
        return body or ""

    # 1. drop a canned exemplar punch (stock closing formula)
    if exemplars:
        ex_tails = _exemplar_last_blocks(exemplars)
        kept = []
        for i, s in enumerate(sents):
            if i > 0 and i == len(sents) - 1 and len(_WORD_RE.findall(s)) >= 4:
                run = max((_word_run_overlap(s, t) for t in ex_tails), default=0)
                if run >= PUNCH_MAX_RUN:
                    continue   # drop the canned punch
            kept.append(s)
        sents = kept or sents
    if not sents:
        return body or ""

    # 2. hook = first sentence, shortened at a clause boundary if needed
    hook = _shorten_hook(sents[0])
    rest = sents[1:]

    # 3. length cut: drop the least-substantive sentences (never the hook)
    def _total() -> int:
        return len(hook) + sum(len(s) + 2 for s in rest)

    while _total() > max_chars and len(rest) > 1:
        weakest_idx = min(
            range(len(rest)),
            key=lambda i: (_substance_score(rest[i]), -i))
        # never leave nothing; keep at least one support sentence
        rest.pop(weakest_idx)
    if _total() > max_chars:
        # cannot fit honestly - return original for the critics to judge
        return body or ""

    # 4. blocks: pairs of short sentences share a block, otherwise one each
    blocks: list[str] = [hook]
    buf: list[str] = []
    for s in rest:
        buf.append(s)
        if len(" ".join(buf)) >= BLOCK_SPLIT_MIN_CHARS or len(buf) >= 2:
            blocks.append(" ".join(buf))
            buf = []
    if buf:
        blocks.append(" ".join(buf))

    return "\n\n".join(b.strip() for b in blocks if b.strip())
