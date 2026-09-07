"""URL extraction and weighted-length accounting.

X behavior (verified against current docs): all URLs are wrapped by t.co and
count as a fixed 23 characters regardless of real length.
Threads behavior: no shortener; the full URL text counts.
"""
from __future__ import annotations

import re

URL_RE = re.compile(
    r"https?://[^\s]+"
    r"|\bwww\.[^\s]+"
    r"|\b[a-z0-9\-]+(\.[a-z0-9\-]+)+(/\S*)?\b(?=\s|$)",
    re.IGNORECASE,
)


def extract_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;:!?)]}\"'") for m in URL_RE.finditer(text or "")]


def weighted_length_with_urls(text: str, url_length: int, weight_fn) -> int:
    """Length where every URL counts as exactly `url_length` characters."""
    total = 0
    pos = 0
    for m in URL_RE.finditer(text or ""):
        start, end = m.span()
        total += weight_fn(text[pos:start])
        total += url_length
        pos = end
    total += weight_fn(text[pos:])
    return total
