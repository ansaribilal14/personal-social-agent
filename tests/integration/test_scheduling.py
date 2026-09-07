"""Scheduling: windows, timezone, collision protection (spec 37-38)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.scheduling.planner import NoSlotAvailable, SchedulingEngine

TZ = ZoneInfo("Asia/Kolkata")


def _seed(repo, platform, at_iso):
    import uuid as _uuid
    pid = repo.db.execute(
        "INSERT INTO posts (post_uid, platform, format, pillar, state) "
        "VALUES (:u,:p,'single','AI','PUBLISHED')",
        {"u": f"TEST-{_uuid.uuid4().hex[:8]}", "p": platform})
    repo.add_schedule(pid, platform, at_iso)
    return pid


def test_slot_inside_configured_window(repo):
    eng = SchedulingEngine(repo, None, None)
    now = datetime(2026, 9, 6, 6, 0, tzinfo=TZ)   # Sunday 06:00 IST
    slot = eng.find_slot("x", now=now)
    assert slot.at > now
    assert slot.at.hour in range(10, 13) or slot.at.hour in range(18, 21)


def test_same_platform_gap_enforced(repo):
    eng = SchedulingEngine(repo, None, None)
    now = datetime(2026, 9, 6, 6, 0, tzinfo=TZ)
    slot1 = eng.find_slot("x", now=now)
    _seed(repo, "x", slot1.at.isoformat())
    slot2 = eng.find_slot("x", now=now)
    gap = abs((slot2.at - slot1.at).total_seconds()) / 60
    assert gap >= 180


def test_cross_platform_gap_enforced(repo):
    eng = SchedulingEngine(repo, None, None)
    now = datetime(2026, 9, 6, 6, 0, tzinfo=TZ)
    slot1 = eng.find_slot("x", now=now)
    _seed(repo, "x", slot1.at.isoformat())
    slot2 = eng.find_slot("threads", now=now)
    gap = abs((slot2.at - slot1.at).total_seconds()) / 60
    assert gap >= 45


def test_no_cascade_of_three_close_posts(repo):
    eng = SchedulingEngine(repo, None, None)
    now = datetime(2026, 9, 6, 6, 0, tzinfo=TZ)
    s1 = eng.find_slot("x", now=now)
    _seed(repo, "x", s1.at.isoformat())
    s2 = eng.find_slot("x", now=now)
    _seed(repo, "x", s2.at.isoformat())
    s3 = eng.find_slot("x", now=now)
    gaps = [abs((s2.at - s1.at).total_seconds()) / 60,
            abs((s3.at - s2.at).total_seconds()) / 60]
    assert all(g >= 180 for g in gaps)   # the 20:00/20:10/20:25 scenario (spec 38)


def test_recent_published_post_blocks_close_slot(repo):
    eng = SchedulingEngine(repo, None, None)
    now = datetime(2026, 9, 6, 6, 0, tzinfo=TZ)
    # a post published 30 minutes ago
    recent = (now.replace(minute=30)).isoformat()
    pid = repo.db.execute(
        "INSERT INTO posts (post_uid, platform, format, pillar, state) "
        "VALUES ('PUB-1','x','single','AI','PUBLISHED')", {})
    ob = repo.db.execute(
        "INSERT INTO publication_outbox (post_id, idempotency_key, platform, version, body) "
        "VALUES (:p, 'k-pub-1', 'x', 1, 'b')", {"p": pid})
    repo.db.execute(
        "INSERT INTO publications (post_id, outbox_id, idempotency_key, platform, "
        "version, buffer_post_id, published_at) VALUES (:p, :o, 'k1', 'x', 1, 'b1', :t)",
        {"p": pid, "o": ob, "t": recent})
    slot = eng.find_slot("x", now=now)
    gap = abs((slot.at - datetime.fromisoformat(recent)).total_seconds()) / 60
    assert gap >= 240


def test_timezone_is_kolkata(repo):
    eng = SchedulingEngine(repo, None, None)
    assert str(eng.tz) == "Asia/Kolkata"


def test_daily_cap_respected(repo):
    eng = SchedulingEngine(repo, None, None)
    now = datetime(2026, 9, 6, 6, 0, tzinfo=TZ)
    slots = []
    for _ in range(5):
        s = eng.find_slot("x", now=now)
        _seed(repo, "x", s.at.isoformat())
        slots.append(s.at)
    days = [s.date() for s in slots]
    from collections import Counter
    counts = Counter(days)
    assert max(counts.values()) <= 3
