"""Scheduling engine (spec sections 37-38).

- windows from platforms.yml (timezone Asia/Kolkata), never hardcoded times
- collision protection: same-platform gap, cross-platform gap, distance from
  recently published posts, per-day caps
- deterministic slot search: first best slot inside the lookahead window
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class NoSlotAvailable(Exception):
    pass


@dataclass
class Slot:
    at: datetime


class SchedulingEngine:
    def __init__(self, repo, platforms_cfg: dict | None, schedule_cfg: dict | None):
        from src.config import get_config
        self.repo = repo
        cfg = get_config()
        self.platforms_cfg = platforms_cfg or cfg.platforms
        self.cfg = schedule_cfg or cfg.schedule
        self.tz = ZoneInfo(self.platforms_cfg.get("timezone", "Asia/Kolkata"))

    def _windows(self, platform: str) -> list[dict]:
        return (self.platforms_cfg.get(platform, {}).get("scheduling", {})
                .get("windows", []))

    def _collides(self, platform: str, candidate: datetime,
                  min_same: int, min_cross: int, min_recent: int,
                  now: datetime | None = None) -> bool:
        now = now or datetime.now(self.tz)
        # future scheduled posts
        for row in self.repo.scheduled_future(now.isoformat()):
            other = datetime.fromisoformat(row["scheduled_at"])
            if other.tzinfo is None:
                other = other.replace(tzinfo=self.tz)
            gap_min = abs((candidate - other).total_seconds()) / 60
            if row["platform"] == platform and gap_min < min_same:
                return True
            if row["platform"] != platform and gap_min < min_cross:
                return True
        # recently published (publications joined posts)
        cutoff = (now - timedelta(minutes=min_recent)).isoformat()
        rows = self.repo.db.query(
            "SELECT pub.published_at, pub.platform FROM publications pub "
            "WHERE pub.published_at >= :c", {"c": cutoff})
        for row in rows:
            other = datetime.fromisoformat(row["published_at"])
            if other.tzinfo is None:
                other = other.replace(tzinfo=self.tz)
            gap_min = abs((candidate - other).total_seconds()) / 60
            limit = min_same if row["platform"] == platform else min_cross
            if gap_min < limit:
                return True
        return False

    def _within_daily_cap(self, platform: str, candidate: datetime) -> bool:
        max_per_day = int(self.platforms_cfg.get(platform, {}).get("scheduling", {})
                          .get("max_per_day", 3))
        day_start = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        rows = self.repo.db.query(
            "SELECT COUNT(*) AS n FROM schedules WHERE platform=:p "
            "AND scheduled_at >= :a AND scheduled_at < :b",
            {"p": platform, "a": day_start.isoformat(), "b": day_end.isoformat()})
        if int(rows[0]["n"]) >= max_per_day:
            return False
        return self._within_total_daily_cap(candidate)

    def _within_total_daily_cap(self, candidate: datetime) -> bool:
        """Cross-platform daily cap (schedule.yml defaults.posts_per_day_total).

        Previously this key was defined but never enforced, so the only real
        ceiling was each platform's own max_per_day (e.g. 3+3=6/day total).
        Absent/invalid config disables this check (falls back to per-platform caps only).
        """
        total_cap = (self.cfg.get("defaults", {}) or {}).get("posts_per_day_total")
        if total_cap is None:
            return True
        total_cap = int(total_cap)
        day_start = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        rows = self.repo.db.query(
            "SELECT COUNT(*) AS n FROM schedules WHERE scheduled_at >= :a "
            "AND scheduled_at < :b", {"a": day_start.isoformat(), "b": day_end.isoformat()})
        return int(rows[0]["n"]) < total_cap

    def find_slot(self, platform: str, now: datetime | None = None) -> Slot:
        """Deterministically pick the earliest clean slot inside windows."""
        sched = self.platforms_cfg.get(platform, {}).get("scheduling", {})
        min_same = int(sched.get("min_gap_same_platform_minutes", 180))
        min_cross = int(sched.get("min_gap_cross_platform_minutes", 45))
        min_recent = int(self.cfg.get("collision", {})
                         .get("min_gap_to_recent_published_minutes", 240))
        window_hours = int(self.cfg.get("lookahead", {}).get("schedule_window_hours", 48))
        now = now or datetime.now(self.tz)
        end = now + timedelta(hours=window_hours)
        step = timedelta(minutes=15)

        candidate = now.replace(minute=0, second=0, microsecond=0) + step
        while candidate <= end:
            day = DAY_NAMES[candidate.weekday()]
            for w in self._windows(platform):
                if day not in w.get("days", []):
                    continue
                h, m = map(int, str(w["start"]).split(":"))
                eh, em = map(int, str(w["end"]).split(":"))
                start = candidate.replace(hour=h, minute=m, second=0, microsecond=0)
                stop = candidate.replace(hour=eh, minute=em, second=0, microsecond=0)
                t = start
                while t <= stop:
                    if t > now and self._within_daily_cap(platform, t) and \
                            not self._collides(platform, t, min_same, min_cross,
                                               min_recent, now=now):
                        return Slot(at=t)
                    t += step
            candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
        raise NoSlotAvailable(
            f"no clean slot for {platform} within {window_hours}h "
            "(collision protection active)")
