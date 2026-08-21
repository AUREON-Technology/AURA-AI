"""Celery beat schedule and refresh/consolidate task behaviour (AURA-008)."""

from __future__ import annotations

from aura_core.domain.identifiers import UserId
from aura_core.learning import (
    InMemorySourceRegistry,
    KnowledgeRefreshPipeline,
    run_knowledge_refresh,
    run_memory_consolidate,
)


class _FakeMemory:
    def __init__(self) -> None:
        self.owners: list[str] = []

    async def consolidate(self, owner_id: UserId, *, kinds=None):
        self.owners.append(owner_id.value)


class TestBeatSchedule:
    def test_schedule_contains_refresh_and_consolidate(self) -> None:
        from celery_app import celery_app

        names = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
        assert "tasks.knowledge_refresh_task" in names
        assert "tasks.memory_consolidate_task" in names
        assert "tasks.generate_insights_for_all_users_task" in names

    def test_refresh_runs_on_the_learning_queue(self) -> None:
        from celery_app import celery_app

        refresh = celery_app.conf.beat_schedule["knowledge-refresh-nightly"]
        assert refresh["options"]["queue"] == "learning"
        consolidate = celery_app.conf.beat_schedule["memory-consolidate-daily"]
        assert consolidate["options"]["queue"] == "learning"


class TestRefreshAndConsolidateJobs:
    async def test_empty_registry_is_a_successful_noop(self, knowledge, index_manager) -> None:
        pipeline = KnowledgeRefreshPipeline(
            registry=InMemorySourceRegistry(),
            fetcher=None,
            knowledge=knowledge,
            index_manager=index_manager,
        )
        result = await run_knowledge_refresh(pipeline, enabled=True)
        assert result["status"] == "success"
        assert result["ingested"] == 0
        assert result["promoted"] is False

    async def test_disabled_refresh_does_not_run(self) -> None:
        result = await run_knowledge_refresh(object(), enabled=False)
        assert result["status"] == "skipped"

    async def test_consolidate_visits_each_owner(self) -> None:
        memory = _FakeMemory()
        result = await run_memory_consolidate(
            memory, [UserId("alice"), UserId("bob")], enabled=True
        )
        assert result == {"status": "success", "owners": 2}
        assert memory.owners == ["alice", "bob"]

    async def test_disabled_consolidate_is_a_noop(self) -> None:
        memory = _FakeMemory()
        result = await run_memory_consolidate(memory, [UserId("alice")], enabled=False)
        assert result["status"] == "skipped"
        assert memory.owners == []
