"""Run queue abstractions for workflow execution."""

from __future__ import annotations

from typing import Protocol

from awap.domain import WorkflowRun
from awap.repository import WorkflowRepository


class RunQueue(Protocol):
    """Queue interface used by workers to claim workflow runs."""

    backend_name: str

    def claim_next_run(self, worker_id: str, lease_seconds: int) -> WorkflowRun | None: ...


class SQLiteRunQueue:
    """Development queue adapter backed by the repository lease table."""

    backend_name = "sqlite_lease_table"

    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    def claim_next_run(self, worker_id: str, lease_seconds: int) -> WorkflowRun | None:
        return self._repository.claim_next_queued_run(worker_id, lease_seconds)
