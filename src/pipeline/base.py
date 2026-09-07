"""Pipeline orchestrators - one per workflow stage, each idempotent.

Every pipeline: starts a workflow_run record, logs events, marks success/fail.
NIM client injected; pipelines degrade to explicit BLOCKED behavior when
NIM is unavailable - they never fail open into publishing.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from src.config import get_config
from src.db.repository import Repository
from src.state.machine import State


def workflow_id() -> str:
    return os.environ.get("GITHUB_RUN_ID") or f"local-{uuid.uuid4().hex[:8]}"


class PipelineBase:
    stage: str = "base"

    def __init__(self, repo: Repository, nim: Any = None):
        self.repo = repo
        self.nim = nim
        self.run_id = workflow_id()
        self._wf_row = self.repo.start_workflow(self.stage, self.run_id, self.stage)

    def succeed(self) -> None:
        self.repo.finish_workflow(self._wf_row, "success")

    def fail(self, error: str) -> None:
        self.repo.log_event(f"pipeline.{self.stage}.failed", severity="error",
                            workflow_run=self.run_id, payload={"error": error[:300]})
        self.repo.finish_workflow(self._wf_row, "failed")

    def require_nim(self) -> Any:
        """Explicit BLOCKED behavior: NIM steps cannot run without a client."""
        if self.nim is None:
            raise RuntimeError("BLOCKED: NIM client unavailable (NVIDIA_API_KEY missing)")
        return self.nim
