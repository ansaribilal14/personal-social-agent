"""Deterministic unicode/character counting (spec section 31).

Implements the documented X weighted-length model:
- default weight 1
- CJK / Hangul / Kana / fullwidth / many emoji ranges weight 2
- (URL shortening handled separately in urls.py)

This exists because LLMs cannot count characters reliably; every platform
limit check in the system goes through this module.
"""
from __future__ import annotations

import unicodedata

# Weight-2 ranges per X's documented character-weighting model.
WEIGHT2_RANGES = (
    (0x1100, 0x11FF),   # Hangul Jamo
    (0x2E80, 0x303E),   # CJK Radicals, Kangxi, CJK Symbols
    (0x3041, 0x33FF),   # Hiragana, Katakana, CJK compat
    (0x3400, 0x4DBF),   # CJK Ext A
    (0x4E00, 0x9FFF),   # CJK Unified
    (0xA000, 0xA4CF),   # Yi
    (0xAC00, 0xD7A3),   # Hangul Syllables
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0xFE30, 0xFE4F),   # CJK Compatibility Forms
    (0xFF00, 0xFF60),   # Fullwidth Forms
    (0x1F300, 0x1F64F), # Emoji pictographs
    (0x1F900, 0x1F9FF), # Supplemental Symbols
    (0x20000, 0x2FFFD), # CJK Ext B
    (0x30000, 0x3FFFD), # CJK Ext C-F
)


def _code_point_weight(ch: str) -> int:
    cp = ord(ch)
    for lo, hi in WEIGHT2_RANGES:
        if lo <= cp <= hi:
            return 2
    return 1


def weighted_length(text: str) -> int:
    """X-style weighted length of text with URLs already replaced/handled."""
    return sum(_code_point_weight(ch) for ch in (text or ""))


def char_class(ch: str) -> str:
    if _code_point_weight(ch) == 2:
        return "wide"
    if unicodedata.category(ch) in ("Cc", "Cf"):
        return "control"
    return "basic"


def grapheme_count(text: str) -> int:
    """Approximate user-perceived character count (Threads limits count
    characters, not weighted units). Combines combining marks into base."""
    count = 0
    for ch in text or "":
        if unicodedata.combining(ch):
            continue
        count += 1
    return count
