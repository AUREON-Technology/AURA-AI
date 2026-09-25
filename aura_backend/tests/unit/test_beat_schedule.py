"""Celery beat schedule and refresh/consolidate task behaviour (AURA-008)."""

from __future__ import annotations

from aura_core.adapters.runtime import InMemoryEventOutbox
from aura_core.domain.events import DocumentIngested
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.learning import (
    InMemorySourceRegistry,
    KnowledgeRefreshPipeline,
    run_knowledge_refresh,
    run_memory_consolidate,
    run_outbox_drain,
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
        assert "tasks.drain_event_outbox_task" in names
        assert "tasks.generate_daily_briefings_task" in names
        assert "tasks.drain_finetune_queue_task" in names
        assert "tasks.purge_idempotency_keys_task" in names

    def test_idempotency_purge_runs_on_the_realtime_queue(self) -> None:
        from celery_app import celery_app

        purge = celery_app.conf.beat_schedule["purge-idempotency-keys"]
        assert purge["options"]["queue"] == "realtime"
        assert purge["schedule"] <= 3600.0

    def test_refresh_runs_on_the_learning_queue(self) -> None:
        from celery_app import celery_app

        refresh = celery_app.conf.beat_schedule["knowledge-refresh-nightly"]
        assert refresh["options"]["queue"] == "learning"
        consolidate = celery_app.conf.beat_schedule["memory-consolidate-daily"]
        assert consolidate["options"]["queue"] == "learning"
        # The fine-tune drain is deliberately NOT here: it runs on the `training` queue, which only
        # worker-training consumes (tests/unit/test_finetune_deploy_parity.py).
        finetune = celery_app.conf.beat_schedule["drain-finetune-queue"]
        assert finetune["options"]["queue"] == "training"

    def test_outbox_drain_runs_frequently_on_the_realtime_queue(self) -> None:
        from celery_app import celery_app

        drain = celery_app.conf.beat_schedule["drain-event-outbox"]
        assert drain["options"]["queue"] == "realtime"
        assert drain["schedule"] <= 300.0  # delivery latency for a durable trigger


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


class TestOutboxDrainJob:
    async def test_empty_outbox_is_a_successful_noop(self) -> None:
        outbox = InMemoryEventOutbox()
        result = await run_outbox_drain(outbox, lambda entry: None, lambda event: None)
        assert result == {
            "status": "success", "claimed": 0, "processed": 0, "failed": 0, "dead_lettered": 0,
        }

    async def test_a_decoded_entry_is_dispatched_and_marked_processed(self) -> None:
        outbox = InMemoryEventOutbox()
        event = DocumentIngested(document_id=DocumentId("doc1"), owner_id=UserId("u1"))
        await outbox.enqueue(event)
        dispatched = []

        async def dispatch(decoded):
            dispatched.append(decoded)

        result = await run_outbox_drain(
            outbox, lambda entry: event, dispatch,
        )
        assert result["claimed"] == 1 and result["processed"] == 1 and result["failed"] == 0
        assert dispatched == [event]
        assert len(outbox) == 0

    async def test_a_decode_failure_marks_the_entry_failed_not_processed(self) -> None:
        outbox = InMemoryEventOutbox()
        await outbox.enqueue(DocumentIngested(document_id=DocumentId("doc1"), owner_id=UserId("u1")))

        def broken_decode(entry):
            raise ValueError("no decoder registered")

        result = await run_outbox_drain(outbox, broken_decode, lambda event: None, max_attempts=5)
        assert result["failed"] == 1 and result["processed"] == 0
        assert len(outbox) == 1  # left in the queue, not lost

    async def test_a_dispatch_failure_at_max_attempts_dead_letters(self) -> None:
        outbox = InMemoryEventOutbox()
        event = DocumentIngested(document_id=DocumentId("doc1"), owner_id=UserId("u1"))
        entry_id = await outbox.enqueue(event)

        async def always_fails(decoded):
            raise RuntimeError("boom")

        for _ in range(5):
            result = await run_outbox_drain(outbox, lambda entry: event, always_fails, max_attempts=5)
        assert result["dead_lettered"] == 1
        assert await outbox.claim_batch(limit=10) == []
