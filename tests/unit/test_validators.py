"""Deterministic platform validators (spec sections 30-31)."""
from src.validation import unicode as uni
from src.validation.urls import extract_urls, weighted_length_with_urls
from src.validation.x import validate_x_post, validate_x_thread
from src.validation.threads import validate_threads_post, validate_threads_thread


def test_ascii_counts_one():
    assert uni.weighted_length("hello world") == 11


def test_cjk_counts_two():
    assert uni.weighted_length("你好") == 4


def test_emoji_counts_two():
    assert uni.weighted_length("\U0001F600") == 2


def test_url_counts_as_23_on_x():
    text = "see https://example.com/very/long/path?query=1 now"
    ln = weighted_length_with_urls(text, 23, uni.weighted_length)
    plain = uni.weighted_length(text)
    assert ln < plain  # long URL got compressed to 23
    assert "https://example.com" in extract_urls(text)[0]


def test_x_single_under_limit_passes():
    r = validate_x_post("a" * 280)
    assert r.passed


def test_x_single_over_limit_fails():
    r = validate_x_post("a" * 281)
    assert not r.passed
    assert any("exceeds" in i for i in r.issues)


def test_x_single_cjk_over_limit():
    r = validate_x_post("好" * 141)   # 282 weighted
    assert not r.passed


def test_x_thread_every_post_matters():
    posts = ["fine post " + "x" * 260] * 3
    posts[2] = "x" * 300
    r = validate_x_thread(posts)
    assert not r.passed
    assert any("post 3" in i for i in r.issues)


def test_x_thread_never_chopped_paragraph_patterns():
    r = validate_x_thread(["Thread:", "more"])
    assert not r.passed   # bare banner opener rejected


def test_x_empty_post_fails():
    assert not validate_x_post("").passed
    assert not validate_x_thread(["", " "]).passed


def test_threads_500_limit():
    assert validate_threads_post("a" * 500).passed
    assert not validate_threads_post("a" * 501).passed


def test_threads_link_warning_not_hard_fail():
    r = validate_threads_post("read this https://example.com/article now")
    assert r.passed  # warning only
    assert any(i.startswith("WARN") for i in r.issues)


def test_threads_hashtag_warning():
    r = validate_threads_post("#a #b #c #d #e #f content here")
    assert any("hashtags" in i for i in r.issues)
