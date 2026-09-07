"""X platform validator - deterministic, config-driven (spec sections 27, 30, 31).

A thread is valid only if EVERY post passes AND thread-level checks pass.
Never silently truncates: failures return issues for the compression loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.config import get_config
from src.validation import unicode as uni
from src.validation.urls import extract_urls, weighted_length_with_urls


@dataclass
class ValidationResult:
    platform: str
    passed: bool
    issues: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def _weight_fn(text: str) -> int:
    return uni.weighted_length(text)


def validate_x_post(text: str) -> ValidationResult:
    cfg = get_config().platform("x")["single"]
    issues: list[str] = []
    if not text or not text.strip():
        return ValidationResult("x", False, ["empty post"])
    length = weighted_length_with_urls(text, cfg["url_length"], _weight_fn)
    if length > cfg["max_length"]:
        issues.append(f"post length {length} exceeds X limit {cfg['max_length']}")
    return ValidationResult("x", not issues, issues,
                            {"weighted_length": length, "limit": cfg["max_length"]})


def validate_x_thread(posts: list[str]) -> ValidationResult:
    x = get_config().platform("x")
    single, thread = x["single"], x["thread"]
    issues: list[str] = []
    if not posts or not any(p.strip() for p in posts):
        return ValidationResult("x", False, ["empty thread"])
    if len(posts) > thread["max_posts"]:
        issues.append(f"thread has {len(posts)} posts; max {thread['max_posts']}")
    lengths = []
    for i, p in enumerate(posts, 1):
        r = validate_x_post(p)
        lengths.append(r.details.get("weighted_length", 0))
        if not r.passed:
            issues.extend(f"post {i}: {iss}" for iss in r.issues)
    total = sum(lengths)
    if total > thread["max_total_length"]:
        issues.append(f"thread total {total} exceeds limit {thread['max_total_length']}")
    if any(not p.strip() for p in posts):
        issues.append("thread contains an empty post")
    first = posts[0].strip()
    if first.lower().startswith(("thread:", "a thread", "1/")):
        issues.append("hook opens with a bare thread banner (weak hook)")
    return ValidationResult("x", not issues, issues,
                            {"post_count": len(posts), "lengths": lengths,
                             "total_weighted_length": total})


# Alias for uniform pipeline usage
validate = validate_x_post
validate_thread = validate_x_thread
