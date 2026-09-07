"""Threads (Meta) platform validator - deterministic, config-driven.

Threads posts: 500-character limit (plain character count, not weighted).
Thread: replies chained; links/hashtags treated as reach-reduction warnings.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.config import get_config
from src.validation import unicode as uni
from src.validation.urls import extract_urls


@dataclass
class ValidationResult:
    platform: str
    passed: bool
    issues: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def validate_threads_post(text: str) -> ValidationResult:
    cfg = get_config().platform("threads")["single"]
    issues: list[str] = []
    if not text or not text.strip():
        return ValidationResult("threads", False, ["empty post"])
    length = uni.grapheme_count(text)
    if length > cfg["max_length"]:
        issues.append(f"post length {length} exceeds Threads limit {cfg['max_length']}")
    urls = extract_urls(text)
    hashtags = text.count("#")
    details = {"length": length, "limit": cfg["max_length"],
               "url_count": len(urls), "hashtag_count": hashtags}
    if len(urls) > 0:
        issues.append("WARN: external links typically reduce Threads reach")
    if hashtags > get_config().platform("threads")["hashtags"]["recommended_max"]:
        issues.append(f"WARN: {hashtags} hashtags exceeds recommended "
                      f"{get_config().platform('threads')['hashtags']['recommended_max']}")
    hard = [i for i in issues if not i.startswith("WARN")]
    return ValidationResult("threads", not hard, issues, details)


def validate_threads_thread(posts: list[str]) -> ValidationResult:
    th = get_config().platform("threads")
    issues: list[str] = []
    if not posts or not any(p.strip() for p in posts):
        return ValidationResult("threads", False, ["empty thread"])
    if len(posts) > th["thread"]["max_posts"]:
        issues.append(f"thread has {len(posts)} posts; max {th['thread']['max_posts']}")
    lengths = []
    for i, p in enumerate(posts, 1):
        r = validate_threads_post(p)
        lengths.append(r.details.get("length", 0))
        hard_issues = [x for x in r.issues if not x.startswith("WARN")]
        if hard_issues:
            issues.extend(f"post {i}: {x}" for x in hard_issues)
    if any(not p.strip() for p in posts):
        issues.append("thread contains an empty post")
    total = sum(lengths)
    if total > th["thread"]["max_total_length"]:
        issues.append(f"thread total {total} exceeds limit {th['thread']['max_total_length']}")
    return ValidationResult("threads", not issues, issues,
                            {"post_count": len(posts), "lengths": lengths, "total": total})


validate = validate_threads_post
validate_thread = validate_threads_thread
