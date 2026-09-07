"""Analytics (spec sections 41-45).

- collector: stores ONLY metrics actually exposed by Buffer (never invented);
  append-only snapshots at 1h/6h/24h/48h/7d
- analysis: aggregates by pillar/format/platform/window with sample sizes;
  below min_sample_size -> "insufficient data" (never conclude from 2 posts)
- reports: weekly report; analytics may RECOMMEND strategy, never apply it
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean

from integrations.buffer.client import BufferClient
from src.config import get_config
from src.db.repository import Repository


class MetricsCollector:
    def __init__(self, repo: Repository, buffer_client: BufferClient | None = None):
        self.repo = repo
        self.client = buffer_client or BufferClient()
        self.cfg = get_config().schedule

    def collect_for_post(self, post_row: dict,
                         published_at: datetime | None = None) -> list[dict]:
        if not post_row.get("buffer_post_id"):
            return []
        published_at = published_at or datetime.now(timezone.utc)
        intervals = [int(h) for h in self.cfg.get("analytics", {})
                     .get("snapshot_intervals_hours", [1, 6, 24, 48, 168])]
        age_h = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
        existing = self.repo.metric_snapshots(post_row["id"])
        stored = []
        for interval in intervals:
            enough_time = age_h >= interval * 0.8
            baseline = (not existing and interval == intervals[0])
            if not enough_time and not baseline:
                continue  # window not elapsed yet (baseline always captured)
            metrics = self.client.post_metrics(post_row["buffer_post_id"])
            if metrics:
                self.repo.save_metric_snapshot(post_row["id"], post_row["platform"],
                                               interval, metrics)
                stored.append({"interval_hours": interval, "metrics": metrics})
        return stored

    def collect_all_due(self) -> list[dict]:
        results = []
        for post in self.repo.all_published_posts():
            results.extend(self.collect_for_post(post))
        return results


class AnalyticsEngine:
    def __init__(self, repo: Repository, min_sample: int | None = None):
        self.repo = repo
        if min_sample is None:
            min_sample = int(get_config().schedule.get("analytics", {})
                             .get("min_sample_size", 4))
        self.min_sample = min_sample

    def _latest_metric(self, post_id: int) -> dict | None:
        rows = self.repo.metric_snapshots(post_id)
        if not rows:
            return None
        return rows[-1]["metrics"]

    def _engagement(self, metrics: dict) -> float | None:
        parts = [metrics.get(k) for k in ("likes", "replies", "reposts", "quotes",
                                          "bookmarks")]
        nums = [v for v in parts if isinstance(v, (int, float))]
        if not nums:
            return None
        return float(sum(nums))

    def group_stats(self, group_key: str) -> list[dict]:
        """Aggregate engagement by a post attribute, with sample sizes."""
        posts = self.repo.all_published_posts()
        buckets: dict[str, list] = {}
        for p in posts:
            key = p.get(group_key) or "unknown"
            buckets.setdefault(str(key), []).append(p)
        out = []
        for key, group in buckets.items():
            rates = []
            for p in group:
                m = self._latest_metric(p["id"])
                e = self._engagement(m) if m else None
                if e is not None:
                    rates.append(e)
            out.append({
                "group": key, "n": len(group),
                "n_with_metrics": len(rates),
                "avg_engagement": round(mean(rates), 1) if rates else None,
                "sample_sufficient": len(rates) >= self.min_sample,
                "note": None if len(rates) >= self.min_sample else "insufficient data",
            })
        return out

    def analyze(self) -> dict:
        return {
            "by_pillar": self.group_stats("pillar"),
            "by_platform": self.group_stats("platform"),
            "by_format": self.group_stats("format"),
            "min_sample_size": self.min_sample,
        }

    def weekly_report(self) -> dict:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        published = [p for p in self.repo.all_published_posts()
                     if (p.get("published_at") or "") >= week_ago.isoformat()]
        by_platform = {"x": 0, "threads": 0}
        for p in published:
            by_platform[p["platform"]] = by_platform.get(p["platform"], 0) + 1

        ranked = []
        for p in published:
            m = self._latest_metric(p["id"])
            e = self._engagement(m) if m else None
            ranked.append((e if e is not None else -1, p))
        ranked.sort(key=lambda t: t[0], reverse=True)
        top = [{"post_uid": p["post_uid"], "engagement": e}
               for e, p in ranked[:3] if e >= 0]
        worst = [{"post_uid": p["post_uid"], "engagement": e}
                 for e, p in reversed(ranked[-3:]) if e >= 0]
        analysis = self.analyze()
        return {
            "period": {"start": week_ago.isoformat(), "end": now.isoformat()},
            "publishing": {"x": by_platform["x"], "threads": by_platform["threads"],
                           "total": len(published)},
            "top_content": top,
            "worst_content": worst,
            "best_topics": [g for g in analysis["by_pillar"] if g["sample_sufficient"]],
            "best_formats": analysis["by_format"],
            "best_windows": "insufficient data" if len(published) < self.min_sample
                            else "see scheduled vs published correlation (sample grows)",
            "what_changed": "requires two consecutive weeks of data",
            "appears_working": [g["group"] for g in analysis["by_pillar"]
                                if g["sample_sufficient"] and g["avg_engagement"]],
            "appears_weak": [g["group"] for g in analysis["by_pillar"]
                             if g["sample_sufficient"] and not g["avg_engagement"]],
            "recommended_experiments": [],  # analyst prompt fills this with evidence
            "note": ("Recommendations require evidence + sample size "
                     "(spec section 45); analytics never silently changes strategy "
                     "(spec section 44)."),
        }

    def generate_weekly_report(self) -> dict:
        report = self.weekly_report()
        self.repo.save_strategy_report("weekly", report,
                                       report["period"]["start"], report["period"]["end"])
        return report
