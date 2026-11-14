"""The fine-tune drain reuses the worker's container and the approval service's public surface
(audit FSA-091).

``_drain_finetune_queue`` built a fresh container -- an asyncpg pool, a Redis client, every
provider -- on every 120 s beat tick and never closed it; ``FineTuneJobs`` reached into
``ApprovalService._store`` for a lookup the service already offers as ``get``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tasks

BACKEND = Path(__file__).resolve().parents[2]


class _Job:
    def __init__(self, status) -> None:
        from aura_core.learning.finetune import FineTuneJobStatus

        self.id = "job-1"
        self.status = FineTuneJobStatus(status)

    def as_dict(self) -> dict:
        return {"id": self.id, "status": str(self.status)}


class _Jobs:
    def __init__(self) -> None:
        self.ran: list[str] = []

    async def list(self, *, limit: int = 20):
        return [_Job("queued")]

    async def run(self, job_id: str):
        self.ran.append(job_id)
        return _Job("succeeded")


class _Container:
    def __init__(self) -> None:
        self.finetune_jobs = _Jobs()


async def test_the_drain_runs_on_the_worker_s_cached_container(monkeypatch) -> None:
    import aura_core.container as container_module

    def refuse(*args, **kwargs):
        raise AssertionError("the drain built a new container instead of reusing the worker's")

    monkeypatch.setattr(container_module, "build_container", refuse)
    shared = _Container()

    async def cached():
        return shared

    monkeypatch.setattr(tasks, "_ensure_worker_container", cached)
    result = await tasks._drain_finetune_queue()
    assert result["ran"] == 1 and shared.finetune_jobs.ran == ["job-1"]


async def test_a_single_job_run_uses_the_same_container(monkeypatch) -> None:
    import aura_core.container as container_module

    monkeypatch.setattr(container_module, "build_container", lambda *a, **k: pytest.fail("built a container"))
    shared = _Container()

    async def cached():
        return shared

    monkeypatch.setattr(tasks, "_ensure_worker_container", cached)
    result = await tasks._run_finetune_job("job-1")
    assert shared.finetune_jobs.ran == ["job-1"] and result["job"]["id"] == "job-1"


def test_finetune_jobs_use_the_approval_service_s_public_getter() -> None:
    source = (BACKEND / "aura_core" / "learning" / "finetune.py").read_text(encoding="utf-8")
    assert "_approvals._store" not in source, "FineTuneJobs reaches into ApprovalService's private store"
