#!/usr/bin/env python3
"""CLI entrypoint for all workflow stages.

Usage:
  python scripts/run_pipeline.py --stage research
  python scripts/run_pipeline.py --stage ideas
  python scripts/run_pipeline.py --stage generate
  python scripts/run_pipeline.py --stage quality
  python scripts/run_pipeline.py --stage review
  python scripts/run_pipeline.py --stage iterate --post-id 12 --instruction "..."
  python scripts/run_pipeline.py --stage approval --issue-id 5 --comment-id 99 \
         --actor user --body-file /tmp/comment.txt
  python scripts/run_pipeline.py --stage schedule
  python scripts/run_pipeline.py --stage publish
  python scripts/run_pipeline.py --stage analytics
  python scripts/run_pipeline.py --stage weekly-report
  python scripts/run_pipeline.py --stage maintenance
  python scripts/run_pipeline.py --stage tests          # self-test suite marker
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_config  # noqa: E402
from src.db.connection import get_db  # noqa: E402
from src.db.repository import Repository  # noqa: E402


def make_nim():
    """Build the NIM client if a key exists; else None (pipelines BLOCK cleanly)."""
    cfg = get_config()
    if not cfg.nim_api_key:
        return None
    from integrations.nvidia.client import NIMClient
    return NIMClient(cfg.nim_api_key)


def read_body_file(path: str | None, fallback: str = "") -> str:
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description="personal-social-agent pipeline runner")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--post-id", type=int)
    parser.add_argument("--instruction")
    parser.add_argument("--issue-id", type=int)
    parser.add_argument("--comment-id", type=int)
    parser.add_argument("--actor", default=os.environ.get("GITHUB_ACTOR", ""))
    parser.add_argument("--body-file")
    parser.add_argument("--body")
    args = parser.parse_args()

    db = get_db()
    repo = Repository(db)

    def out(payload) -> int:
        print(json.dumps(payload, indent=1, ensure_ascii=False, default=str))
        return 0

    try:
        if args.stage == "research":
            from src.pipeline.production import ResearchPipeline
            return out(ResearchPipeline(repo, make_nim()).run())

        if args.stage == "ideas":
            from src.pipeline.production import IdeaDiscoveryPipeline
            return out(IdeaDiscoveryPipeline(repo, make_nim()).run())

        if args.stage == "generate":
            from src.pipeline.production import GenerationPipeline
            return out(GenerationPipeline(repo, make_nim()).run())

        if args.stage == "quality":
            from src.pipeline.production import QualityPipeline
            return out(QualityPipeline(repo, make_nim()).run())

        if args.stage == "review":
            from src.pipeline.ops import ReviewPipeline
            return out(ReviewPipeline(repo, make_nim()).run())

        if args.stage == "iterate":
            if not (args.post_id and args.instruction and args.actor):
                print("iterate requires --post-id, --instruction, --actor", file=sys.stderr)
                return 2
            from src.pipeline.ops import IteratePipeline
            return out(IteratePipeline(repo, make_nim()).run(
                args.post_id, args.instruction, args.actor))

        if args.stage == "approval":
            body = read_body_file(args.body_file, args.body or "")
            if not (args.issue_id and args.comment_id and args.actor):
                print("approval requires --issue-id, --comment-id, --actor",
                      file=sys.stderr)
                return 2
            from src.pipeline.ops import ApprovalCommandPipeline
            return out(ApprovalCommandPipeline(repo, make_nim()).run(
                args.issue_id, args.comment_id, body, args.actor))

        if args.stage == "schedule":
            from src.pipeline.ops import SchedulePipeline
            return out(SchedulePipeline(repo, make_nim()).run())

        if args.stage == "publish":
            from src.pipeline.ops import PublishPipeline
            return out(PublishPipeline(repo, make_nim()).run())

        if args.stage == "analytics":
            from src.pipeline.ops import AnalyticsPipeline
            return out(AnalyticsPipeline(repo, make_nim()).run())

        if args.stage == "weekly-report":
            from src.pipeline.ops import WeeklyReportPipeline
            return out(WeeklyReportPipeline(repo, make_nim()).run())

        if args.stage == "maintenance":
            from src.pipeline.ops import MaintenancePipeline
            return out(MaintenancePipeline(repo, make_nim()).run())

        print(f"unknown stage '{args.stage}'", file=sys.stderr)
        return 2
    except Exception as exc:
        # Final safety net: log + nonzero exit. Never publish on error paths.
        try:
            repo.log_event("cli.error", severity="error",
                           payload={"stage": args.stage,
                                    "error": f"{type(exc).__name__}: {exc}"[:300]})
        except Exception:
            pass
        print(f"ERROR [{args.stage}]: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
