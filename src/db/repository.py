"""Repository layer - every state mutation is audited via system_events.

Key guarantees implemented HERE at the DB level (spec sections 3, 10, 11):
- publication_outbox.idempotency_key UNIQUE -> duplicate publish attempts fail
- review_commands (issue_id, comment_id) UNIQUE -> comment replay fails
- post_versions (post_id, version) UNIQUE -> versions immutable
- metric_snapshots append-only
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import get_config
from src.db.connection import Database
from src.state.machine import State, transition


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def _loads(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ audit
    def log_event(self, event_type: str, severity: str = "info",
                  post_id: int | None = None, workflow_run: str | None = None,
                  payload: Any = None) -> None:
        self.db.execute(
            "INSERT INTO system_events (event_type, severity, post_id, workflow_run, payload) "
            "VALUES (:t, :s, :p, :w, :pl)",
            {"t": event_type, "s": severity, "p": post_id, "w": workflow_run, "pl": _json(payload)},
        )

    def events(self, limit: int = 100) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM system_events ORDER BY id DESC LIMIT :n", {"n": limit})
        for r in rows:
            r["payload"] = _loads(r.get("payload"))
        return rows

    def start_workflow(self, workflow: str, run_id: str | None, stage: str) -> int:
        return self.db.execute(
            "INSERT INTO workflow_runs (workflow, run_id, stage) VALUES (:w, :r, :s)",
            {"w": workflow, "r": run_id, "s": stage})

    def finish_workflow(self, row_id: int, status: str) -> None:
        self.db.execute(
            "UPDATE workflow_runs SET status=:s, finished_at=:f WHERE id=:i",
            {"s": status, "f": utcnow(), "i": row_id})

    # ------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self.db.query("SELECT value FROM system_settings WHERE key=:k", {"k": key})
        if not rows:
            return default
        return _loads(rows[0]["value"])

    def set_setting(self, key: str, value: Any) -> None:
        params = {"k": key, "v": _json(value), "t": utcnow()}
        self.db.execute(
            "INSERT INTO system_settings (key, value, updated_at) VALUES (:k, :v, :t) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            params)

    def delete_setting(self, key: str) -> None:
        """Remove a setting row entirely (get_setting falls back to default)."""
        self.db.execute("DELETE FROM system_settings WHERE key=:k", {"k": key})

    # -------------------------------------------------------------- research
    def save_research_item(self, item: dict, workflow_run: str | None = None) -> int:
        if item.get("source_url"):
            self.db.execute(
                "INSERT OR IGNORE INTO research_sources (url, publisher, kind) "
                "VALUES (:u, :p, :k)",
                {"u": item.get("source_url"), "p": item.get("publisher"),
                 "k": item.get("category") or "feed"})
        return self.db.execute(
            "INSERT INTO research_items (title, source_url, publisher, published_at, summary, "
            "topic, category, freshness, source_quality, relevance, contaminated, workflow_run) "
            "VALUES (:title, :url, :publisher, :published_at, :summary, :topic, :category, "
            ":freshness, :source_quality, :relevance, :contaminated, :run)",
            {"title": item["title"], "url": item.get("source_url"),
             "publisher": item.get("publisher"), "published_at": item.get("published_at"),
             "summary": item.get("summary"), "topic": item.get("topic"),
             "category": item.get("category"), "freshness": item.get("freshness"),
             "source_quality": item.get("source_quality"),
             "relevance": item.get("relevance"),
             "contaminated": 1 if item.get("contaminated") else 0, "run": workflow_run})

    def recent_research(self, limit: int = 20, include_contaminated: bool = False) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM research_items "
            "WHERE (:inc=1 OR contaminated=0) ORDER BY id DESC LIMIT :n",
            {"inc": 1 if include_contaminated else 0, "n": limit})
        return rows

    # ---------------------------------------------------------------- ideas
    def save_idea(self, statement: str, pillar: str, evaluation: dict,
                  score: int, why_me: dict, source_item_ids: list[int] | None = None,
                  status: str = "CANDIDATE", workflow_run: str | None = None) -> int:
        return self.db.execute(
            "INSERT INTO ideas (idea_uid, pillar, statement, source_item_ids, evaluation, "
            "score, why_me, status, workflow_run) VALUES (:u, :p, :s, :si, :e, :sc, :w, :st, :r)",
            {"u": f"IDEA-{uuid.uuid4().hex[:12]}", "p": pillar, "s": statement,
             "si": ",".join(str(i) for i in (source_item_ids or [])),
             "e": _json(evaluation), "sc": score, "w": _json(why_me), "st": status,
             "r": workflow_run})

    def update_idea_status(self, idea_id: int, status: str) -> None:
        self.db.execute("UPDATE ideas SET status=:s WHERE id=:i", {"s": status, "i": idea_id})

    def top_ideas(self, limit: int = 3, min_score: int = 0) -> list[dict]:
        return self.db.query(
            "SELECT * FROM ideas WHERE status='CANDIDATE' AND score >= :m "
            "ORDER BY score DESC LIMIT :n", {"m": min_score, "n": limit})

    # ---------------------------------------------------------------- posts
    def create_post(self, platform: str, format: str, pillar: str, idea_id: int | None,
                    angle: str | None, state: State = State.DISCOVERED) -> int:
        now = utcnow()
        post_uid = f"{'X' if platform == 'x' else 'TH'}-{datetime.now(timezone.utc):%Y}-" \
                   f"{uuid.uuid4().hex[:5].upper()}"
        post_id = self.db.execute(
            "INSERT INTO posts (post_uid, idea_id, platform, format, pillar, state) "
            "VALUES (:u, :i, :p, :f, :pi, :s)",
            {"u": post_uid, "i": idea_id, "p": platform, "f": format, "pi": pillar,
             "s": state.value})
        if angle:
            angle_id = self.db.execute(
                "INSERT INTO angles (idea_id, text) VALUES (:i, :t)",
                {"i": idea_id, "t": angle})
            self.db.execute("UPDATE posts SET angle_id=:a WHERE id=:p",
                            {"a": angle_id, "p": post_id})
        self.log_event("post.created", post_id=post_id,
                       payload={"platform": platform, "format": format})
        return post_id

    def get_post(self, post_id: int) -> dict | None:
        rows = self.db.query("SELECT * FROM posts WHERE id=:i", {"i": post_id})
        return rows[0] if rows else None

    def get_post_by_uid(self, post_uid: str) -> dict | None:
        rows = self.db.query("SELECT * FROM posts WHERE post_uid=:u", {"u": post_uid})
        return rows[0] if rows else None

    def move_state(self, post_id: int, target: State, actor: str = "system",
                   workflow_run: str | None = None) -> State:
        post = self.get_post(post_id)
        if post is None:
            raise ValueError(f"post {post_id} not found")
        new_state = transition(post["state"], target)
        self.db.execute(
            "UPDATE posts SET state=:s, updated_at=:t WHERE id=:i",
            {"s": new_state.value, "t": utcnow(), "i": post_id})
        self.log_event("post.transition", post_id=post_id, workflow_run=workflow_run,
                       payload={"from": post["state"], "to": new_state.value, "actor": actor})
        return new_state

    def set_editorial_score(self, post_id: int, score: int) -> None:
        self.db.execute("UPDATE posts SET editorial_score=:s WHERE id=:i",
                        {"s": score, "i": post_id})

    def set_recommended_slot(self, post_id: int, slot_iso: str) -> None:
        self.db.execute("UPDATE posts SET recommended_slot=:s WHERE id=:i",
                        {"s": slot_iso, "i": post_id})

    def posts_in_state(self, *states: str) -> list[dict]:
        placeholders = ",".join(f":s{i}" for i in range(len(states)))
        params = {f"s{i}": s for i, s in enumerate(states)}
        return self.db.query(
            f"SELECT * FROM posts WHERE state IN ({placeholders}) ORDER BY id", params)

    # --------------------------------------------------------------- versions
    def add_version(self, post_id: int, body: str, thread_posts: list[str] | None,
                    prompt_versions: dict | None, voice_snapshot: dict | None,
                    claims_snapshot: list | None,
                    iteration_instruction: str | None = None) -> int:
        """Atomic version allocation: version = MAX(version)+1 computed inside
        the INSERT, guarded by UNIQUE(post_id, version). Racing /iterate runs
        serialize; no competing versions are silently created (spec 53)."""
        vid = self.db.execute(
            "INSERT INTO post_versions (post_id, version, body, thread_posts, prompt_versions, "
            "voice_snapshot, claims_snapshot, iteration_instruction) "
            "VALUES (:p, (SELECT COALESCE(MAX(version),0)+1 FROM post_versions "
            "            WHERE post_id=:p), :b, :tp, :pv, :vs, :cs, :ii)",
            {"p": post_id, "b": body, "tp": _json(thread_posts),
             "pv": _json(prompt_versions), "vs": _json(voice_snapshot),
             "cs": _json(claims_snapshot), "ii": iteration_instruction})
        row = self.db.query(
            "SELECT version FROM post_versions WHERE id=:i", {"i": vid})
        version = row[0]["version"]
        self.db.execute(
            "UPDATE posts SET current_version=:v, updated_at=:t WHERE id=:i",
            {"v": version, "t": utcnow(), "i": post_id})
        self.log_event("post.version_added", post_id=post_id, payload={"version": version})
        return version

    def get_version(self, post_id: int, version: int) -> dict | None:
        rows = self.db.query(
            "SELECT * FROM post_versions WHERE post_id=:p AND version=:v",
            {"p": post_id, "v": version})
        if rows:
            for key in ("thread_posts", "prompt_versions", "voice_snapshot",
                        "claims_snapshot"):
                rows[0][key] = _loads(rows[0].get(key))
        return rows[0] if rows else None

    def latest_version(self, post_id: int) -> dict | None:
        return self.get_version(post_id, self.get_post(post_id)["current_version"])

    # ----------------------------------------------------------- quality evals
    def save_quality_eval(self, post_id: int, version: int, critic: str,
                          passed: bool | None, score: int | None, issues: list,
                          evidence: list, recommended_changes: list, engine: str) -> None:
        self.db.execute(
            "INSERT INTO quality_evaluations (post_id, version, critic, passed, score, issues, "
            "evidence, recommended_changes, engine) VALUES (:p,:v,:c,:pa,:s,:i,:e,:r,:en)",
            {"p": post_id, "v": version, "c": critic, "pa": None if passed is None else int(passed),
             "s": score, "i": _json(issues), "e": _json(evidence),
             "r": _json(recommended_changes), "en": engine})

    # ---------------------------------------------------------------- claims
    def save_claim(self, claim: dict) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO claims (claim_uid, text, claim_type, source_url, "
            "source_title, evidence, confidence, status) "
            "VALUES (:u,:t,:ct,:su,:st,:ev,:cf,:s)",
            {"u": claim["claim_id"], "t": claim["text"], "ct": claim["claim_type"],
             "su": claim.get("source", {}).get("url") if isinstance(claim.get("source"), dict)
                   else claim.get("source_url"),
             "st": claim.get("source", {}).get("title") if isinstance(claim.get("source"), dict)
                   else claim.get("source_title"),
             "ev": claim.get("evidence"), "cf": claim.get("confidence"),
             "s": claim.get("status", "PROPOSED")})

    # ----------------------------------------------------------- approvals
    def record_approval_event(self, post_id: int, version: int, action: str, actor: str,
                              reason: str | None = None, issue_id: int | None = None,
                              comment_id: int | None = None) -> None:
        self.db.execute(
            "INSERT INTO approval_events (post_id, version, action, actor, reason, "
            "github_issue_id, github_comment_id) VALUES (:p,:v,:a,:ac,:r,:i,:c)",
            {"p": post_id, "v": version, "a": action, "ac": actor, "r": reason,
             "i": issue_id, "c": comment_id})

    def approval_history(self, post_id: int) -> list[dict]:
        return self.db.query(
            "SELECT * FROM approval_events WHERE post_id=:p ORDER BY id", {"p": post_id})

    def record_review_command(self, issue_id: int, comment_id: int, command: str,
                              payload: str, actor: str, accepted: bool,
                              reject_reason: str | None = None) -> None:
        # INSERT OR IGNORE: a replayed comment must not crash intake; the
        # UNIQUE(issue_id, comment_id) row already records the first verdict.
        self.db.execute(
            "INSERT OR IGNORE INTO review_commands (issue_id, comment_id, command, payload, "
            "actor, accepted, reject_reason) VALUES (:i,:c,:cm,:p,:a,:ac,:r)",
            {"i": issue_id, "c": comment_id, "cm": command, "p": payload, "a": actor,
             "ac": int(accepted), "r": reject_reason})

    def command_seen(self, issue_id: int, comment_id: int) -> bool:
        rows = self.db.query(
            "SELECT id FROM review_commands WHERE issue_id=:i AND comment_id=:c",
            {"i": issue_id, "c": comment_id})
        return bool(rows)

    # -------------------------------------------------------------- outbox
    def create_outbox_entry(self, post_id: int, platform: str, version: int,
                            body: str, thread_posts: list[str] | None) -> int:
        """Idempotent outbox creation. The UNIQUE(idempotency_key) constraint is the
        hard guarantee that one approved version can only ever publish once."""
        post = self.get_post(post_id)
        key = f"{post['post_uid']}:{platform}:{version}"
        try:
            outbox_id = self.db.execute(
                "INSERT INTO publication_outbox (post_id, idempotency_key, platform, version, "
                "body, thread_posts) VALUES (:p,:k,:pl,:v,:b,:tp)",
                {"p": post_id, "k": key, "pl": platform, "v": version, "b": body,
                 "tp": _json(thread_posts)})
        except Exception:
            existing = self.db.query(
                "SELECT id FROM publication_outbox WHERE idempotency_key=:k", {"k": key})
            if existing:
                return existing[0]["id"]
            raise
        self.log_event("outbox.created", post_id=post_id, payload={"key": key})
        return outbox_id

    def get_outbox(self, outbox_id: int) -> dict | None:
        rows = self.db.query("SELECT * FROM publication_outbox WHERE id=:i", {"i": outbox_id})
        if rows:
            rows[0]["thread_posts"] = _loads(rows[0].get("thread_posts"))
        return rows[0] if rows else None

    def outbox_by_key(self, idempotency_key: str) -> dict | None:
        rows = self.db.query(
            "SELECT * FROM publication_outbox WHERE idempotency_key=:k", {"k": idempotency_key})
        return rows[0] if rows else None

    def set_outbox_status(self, outbox_id: int, status: str,
                          error: str | None = None, buffer_post_id: str | None = None,
                          scheduled_at: str | None = None) -> None:
        entry = self.get_outbox(outbox_id)
        self.db.execute(
            "UPDATE publication_outbox SET status=:s, last_error=:e, buffer_post_id=:b, "
            "scheduled_at=:sa, attempt_count=attempt_count+1, updated_at=:t WHERE id=:i",
            {"s": status, "e": error, "b": buffer_post_id, "sa": scheduled_at,
             "t": utcnow(), "i": outbox_id})
        self.log_event("outbox.status", severity="warn" if error else "info",
                       post_id=entry["post_id"] if entry else None,
                       payload={"id": outbox_id, "from": entry["status"] if entry else None,
                                "to": status, "error": error})

    def outbox_due(self, now_iso: str | None = None) -> list[dict]:
        """Entries ready to hand to Buffer:
        - OUTBOX_CREATED / PUBLISH_REQUESTED: immediately
        - BUFFER_ERROR: retryable until max_attempts, respecting backoff
        """
        rows = self.db.query(
            "SELECT * FROM publication_outbox WHERE status IN "
            "('OUTBOX_CREATED','PUBLISH_REQUESTED','BUFFER_ERROR') ORDER BY id")
        max_attempts = int(get_config().schedule.get("retry", {}).get("max_attempts", 4))
        base = int(get_config().schedule.get("retry", {}).get("backoff_base_seconds", 60))
        factor = int(get_config().schedule.get("retry", {}).get("backoff_factor", 2))
        now = datetime.now(timezone.utc)
        due = []
        for r in rows:
            if r["status"] == "BUFFER_ERROR":
                if int(r.get("attempt_count") or 0) >= max_attempts:
                    continue  # exhausted; marked PUBLISH_FAILED by publisher path
                updated = r.get("updated_at") or r.get("created_at")
                try:
                    then = datetime.fromisoformat(str(updated))
                    if then.tzinfo is None:
                        then = then.replace(tzinfo=timezone.utc)
                except ValueError:
                    then = now
                backoff = base * (factor ** (int(r.get("attempt_count") or 1) - 1))
                if now < then + timedelta(seconds=backoff):
                    continue  # backoff not elapsed yet
            r["thread_posts"] = _loads(r.get("thread_posts"))
            due.append(r)
        return due

    def claim_outbox_entry(self, outbox_id: int) -> bool:
        """Atomically claim an outbox entry for publication.

        Exactly ONE concurrent publish run can flip a claimable entry
        (OUTBOX_CREATED / PUBLISH_REQUESTED / BUFFER_ERROR-retry) into the
        in-flight PUBLISHING status via this conditional UPDATE; the loser
        gets rowcount 0 and skips. This is the hard guarantee that two
        publish workflows can never double-publish the same approved
        version (spec section 53).
        """
        rowcount = self.db.execute(
            "UPDATE publication_outbox SET status='PUBLISHING', updated_at=:t "
            "WHERE id=:i AND status IN ('OUTBOX_CREATED','PUBLISH_REQUESTED','BUFFER_ERROR')",
            {"t": utcnow(), "i": outbox_id})
        return int(rowcount) == 1

    def record_publication(self, post_id: int, outbox_id: int, platform: str,
                           version: int, buffer_post_id: str) -> str:
        post = self.get_post(post_id)
        key = f"{post['post_uid']}:{platform}:{version}"
        self.db.execute(
            "INSERT OR IGNORE INTO publications (post_id, outbox_id, idempotency_key, platform, "
            "version, buffer_post_id, published_at) VALUES (:p,:o,:k,:pl,:v,:b,:t)",
            {"p": post_id, "o": outbox_id, "k": key, "pl": platform, "v": version,
             "b": buffer_post_id, "t": utcnow()})
        return key

    # ------------------------------------------------------------ schedules
    def add_schedule(self, post_id: int, platform: str, scheduled_at: str) -> None:
        self.db.execute(
            "INSERT INTO schedules (post_id, platform, scheduled_at) VALUES (:p,:pl,:s)",
            {"p": post_id, "pl": platform, "s": scheduled_at})

    def scheduled_future(self, now_iso: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM schedules WHERE scheduled_at >= :n ORDER BY scheduled_at",
            {"n": now_iso})

    # ------------------------------------------------------- metric snapshots
    def save_metric_snapshot(self, post_id: int, platform: str, interval_hours: int,
                             metrics: dict) -> None:
        """Append-only. Never overwrites (spec section 42)."""
        self.db.execute(
            "INSERT OR IGNORE INTO metric_snapshots (post_id, platform, interval_hours, metrics) "
            "VALUES (:p,:pl,:i,:m)",
            {"p": post_id, "pl": platform, "i": interval_hours, "m": _json(metrics)})

    def metric_snapshots(self, post_id: int) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM metric_snapshots WHERE post_id=:p ORDER BY captured_at",
            {"p": post_id})
        for r in rows:
            r["metrics"] = _loads(r.get("metrics"))
        return rows

    def all_published_posts(self) -> list[dict]:
        return self.db.query(
            "SELECT p.*, pub.buffer_post_id FROM posts p "
            "JOIN publications pub ON pub.post_id = p.id ORDER BY p.id")

    # ------------------------------------------------------------- reports
    def save_strategy_report(self, kind: str, report: dict,
                             period_start: str | None = None,
                             period_end: str | None = None) -> int:
        return self.db.execute(
            "INSERT INTO strategy_reports (kind, period_start, period_end, report) "
            "VALUES (:k,:ps,:pe,:r)",
            {"k": kind, "ps": period_start, "pe": period_end, "r": _json(report)})

    # ---------------------------------------------------- editorial memory
    def remember_variant(self, post_id: int, platform: str, format: str,
                         body: str, digest: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO platform_variants (post_id, platform, format, body_digest) "
            "VALUES (:p,:pl,:f,:d)", {"p": post_id, "pl": platform, "f": format, "d": digest})

    def recent_variants(self, limit: int = 200) -> list[dict]:
        rows = self.db.query(
            "SELECT pv.*, p.pillar FROM post_versions pv JOIN posts p ON p.id = pv.post_id "
            "ORDER BY pv.id DESC LIMIT :n", {"n": limit})
        for r in rows:
            r["thread_posts"] = _loads(r.get("thread_posts"))
        return rows

    # --------------------------------------------------------- preferences
    def upsert_preference(self, kind: str, key: str, value: Any,
                          increment_evidence: int = 0) -> int:
        row = self.db.query(
            "SELECT id, evidence_count FROM voice_preferences WHERE kind=:k AND key=:ke",
            {"k": kind, "ke": key})
        if row:
            self.db.execute(
                "UPDATE voice_preferences SET value=:v, evidence_count=evidence_count+:inc, "
                "updated_at=:t WHERE id=:i",
                {"v": _json(value), "inc": increment_evidence, "t": utcnow(),
                 "i": row[0]["id"]})
            return row[0]["id"]
        return self.db.execute(
            "INSERT INTO voice_preferences (kind, key, value, evidence_count) "
            "VALUES (:k,:ke,:v,:inc)",
            {"k": kind, "ke": key, "v": _json(value), "inc": increment_evidence})

    def promoted_preferences(self) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM voice_preferences WHERE promoted=1 AND kind='learned'")
        for r in rows:
            r["value"] = _loads(r.get("value"))
        return rows

    def promote_preference(self, pref_id: int) -> None:
        self.db.execute("UPDATE voice_preferences SET promoted=1 WHERE id=:i", {"i": pref_id})
