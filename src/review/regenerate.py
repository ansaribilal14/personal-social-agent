"""Reject -> instant full regeneration (user feedback 2026-09).

When the author rejects a post on Discord (card reaction, typed command, or
slash command), they want fresh choices AT THAT MOMENT - not at the next
3x-daily cron slot. This module dispatches the `engine` GitHub Actions
workflow immediately, which runs research -> ideas -> generation -> quality
-> review and lands fresh review cards in #social-review.

Anti-repeat: every dispatch carries the rejection reason as the
`regen_reason` input (audit trail + step summary), and the ideas stage
independently reads the latest REJECTED approval_events from the DB into the
strategist prompt as REJECTION ANTI-GUIDANCE, so the new batch steers away
from rejected angles/patterns. Reject -> review -> reject loops until the
author approves.

Token resolution order: GITHUB_REGEN_TOKEN (PAT) > GH_TOKEN > GITHUB_TOKEN.
In GitHub Actions, GITHUB_TOKEN works when the calling workflow grants
`actions: write`. Locally, export GITHUB_REGEN_TOKEN (a PAT with repo scope).

Safety: this module NEVER blocks or crashes the reject path - every failure
mode returns {"dispatched": False, "detail": ...} and logs a DB event.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

WORKFLOW_FILE = "engine.yml"
REGEN_REASON_INPUT = "regen_reason"
MAX_REASON_CHARS = 300


def recent_rejection_reasons(repo, limit: int = 8) -> list[dict]:
    """Latest REJECTED decisions with reason, platform, pillar - untrusted
    data for the strategist prompt (wrapped in DATA markers by the caller)."""
    rows = repo.db.query(
        "SELECT p.post_uid, p.platform, p.pillar, ae.reason "
        "FROM approval_events ae JOIN posts p ON p.id = ae.post_id "
        "WHERE ae.action='REJECTED' ORDER BY ae.id DESC LIMIT :n", {"n": limit})
    return [dict(r) for r in rows]


def _resolve_token(token: str | None) -> str:
    return (token or os.environ.get("GITHUB_REGEN_TOKEN")
            or os.environ.get("GH_TOKEN")
            or os.environ.get("GITHUB_TOKEN") or "").strip()


def dispatch_engine_run(repo, reason: str = "", actor: str = "",
                        repository: str | None = None, token: str | None = None,
                        ref: str | None = None, timeout: int = 20) -> dict:
    """POST one workflow_dispatch for the engine workflow. Never raises.

    Returns {"dispatched": bool, "detail": str}; every outcome is logged as a
    DB event so the audit trail shows who triggered the regeneration.
    """
    repo_slug = repository or os.environ.get("GITHUB_REPOSITORY", "").strip()
    tok = _resolve_token(token)
    branch = (ref or os.environ.get("GITHUB_REF_NAME") or "main").strip() or "main"
    clean_reason = " ".join(str(reason or "").split())[:MAX_REASON_CHARS]

    if not repo_slug:
        return _log(repo, "regenerate.skipped_no_repository", clean_reason, actor,
                    {"detail": "GITHUB_REPOSITORY not set"})
    if not tok:
        return _log(repo, "regenerate.skipped_no_token", clean_reason, actor,
                    {"detail": "no GitHub token (set GITHUB_REGEN_TOKEN locally)"})

    url = (f"https://api.github.com/repos/{repo_slug}/actions/"
           f"workflows/{WORKFLOW_FILE}/dispatches")
    body = {"ref": branch,
            "inputs": {"mode": "full", REGEN_REASON_INPUT: clean_reason}}
    req = Request(url, data=json.dumps(body).encode("utf-8"), headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {tok}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "personal-social-agent",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
    except HTTPError as exc:
        detail = f"HTTP {exc.code}"
        try:
            detail += f": {exc.read().decode('utf-8', 'replace')[:200]}"
        except Exception:
            pass
        return _log(repo, "regenerate.dispatch_failed", clean_reason, actor,
                    {"error": detail})
    except (URLError, TimeoutError, OSError) as exc:
        return _log(repo, "regenerate.dispatch_failed", clean_reason, actor,
                    {"error": type(exc).__name__})
    return _log(repo, "regenerate.dispatched", clean_reason, actor,
                {"status": status, "ref": branch,
                 "workflow": WORKFLOW_FILE})


def _log(repo, event_type: str, reason: str, actor: str, payload: dict) -> dict:
    try:
        repo.log_event(event_type, payload={"actor": actor,
                                            "reason": reason[:120], **payload})
    except Exception:
        pass  # logging must never break the control path
    dispatched = event_type == "regenerate.dispatched"
    return {"dispatched": dispatched,
            "detail": payload.get("detail") or payload.get("error") or
                      (f"HTTP {payload.get('status')}" if dispatched else "unknown")}
