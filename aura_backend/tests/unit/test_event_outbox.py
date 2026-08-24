"""``InMemoryEventOutbox`` and ``DurableEventBus`` (Phase E1).

Real Postgres behaviour for ``PostgresEventOutbox`` -- concurrent ``claim_batch`` never
double-claiming (``FOR UPDATE SKIP LOCKED``), the claim lease, dead-lettering -- was verified by
hand against a live (throwaway) Postgres container as part of this phase's work; these tests pin
the same *contract* against the in-memory reference implementation the whole test suite runs on.
"""

from __future__ import annotations

from aura_core.adapters.runtime import DurableEventBus, InMemoryEventBus, InMemoryEventOutbox
from aura_core.domain.events import DocumentIngested, TurnFailed
from aura_core.domain.identifiers import DocumentId, UserId


def _event(quarantined: bool = True) -> DocumentIngested:
    return DocumentIngested(
        document_id=DocumentId("doc1"), version=1, chunks=0, source_type="upload",
        owner_id=UserId("u1"), quarantined=quarantined,
    )


class TestInMemoryEventOutbox:
    async def test_enqueue_then_claim_returns_the_entry(self) -> None:
        outbox = InMemoryEventOutbox()
        entry_id = await outbox.enqueue(_event())
        batch = await outbox.claim_batch(limit=10)
        assert len(batch) == 1
        assert batch[0].id == entry_id
        assert batch[0].event_name == "DocumentIngested"
        assert batch[0].payload["quarantined"] is True

    async def test_a_claimed_entry_is_not_claimable_again(self) -> None:
        outbox = InMemoryEventOutbox()
        await outbox.enqueue(_event())
        first = await outbox.claim_batch(limit=10)
        second = await outbox.claim_batch(limit=10)
        assert len(first) == 1
        assert len(second) == 0

    async def test_mark_processed_removes_the_entry(self) -> None:
        outbox = InMemoryEventOutbox()
        entry_id = await outbox.enqueue(_event())
        await outbox.mark_processed(entry_id)
        assert len(outbox) == 0
        # A processed entry that somehow got claimed again would be a duplicate delivery bug --
        # assert there's nothing left to claim, not just that len() agrees with itself.
        assert await outbox.claim_batch(limit=10) == []

    async def test_mark_failed_below_max_attempts_is_reclaimable(self) -> None:
        outbox = InMemoryEventOutbox()
        entry_id = await outbox.enqueue(_event())
        await outbox.claim_batch(limit=10)
        await outbox.mark_failed(entry_id, error="transient", max_attempts=5)
        batch = await outbox.claim_batch(limit=10)
        assert len(batch) == 1
        assert batch[0].attempts == 1

    async def test_mark_failed_at_max_attempts_dead_letters(self) -> None:
        outbox = InMemoryEventOutbox()
        entry_id = await outbox.enqueue(_event())
        for _ in range(5):
            await outbox.claim_batch(limit=10)
            await outbox.mark_failed(entry_id, error="permanent", max_attempts=5)
        assert await outbox.claim_batch(limit=10) == []
        assert len(outbox) == 0

    async def test_claim_batch_respects_limit(self) -> None:
        outbox = InMemoryEventOutbox()
        for _ in range(5):
            await outbox.enqueue(_event())
        batch = await outbox.claim_batch(limit=2)
        assert len(batch) == 2
        assert len(outbox) == 5


class TestDurableEventBus:
    async def test_durable_event_type_is_enqueued_not_live_dispatched(self) -> None:
        inner = InMemoryEventBus()
        outbox = InMemoryEventOutbox()
        received = []

        async def handler(event):
            received.append(event)

        inner.subscribe_all(handler)
        bus = DurableEventBus(inner=inner, outbox=outbox, durable_event_types=frozenset({DocumentIngested}))

        await bus.publish(_event())

        assert received == []
        assert len(outbox) == 1

    async def test_non_durable_event_type_still_live_dispatches(self) -> None:
        inner = InMemoryEventBus()
        outbox = InMemoryEventOutbox()
        received = []

        async def handler(event):
            received.append(event)

        inner.subscribe_all(handler)
        bus = DurableEventBus(inner=inner, outbox=outbox, durable_event_types=frozenset({DocumentIngested}))

        turn_failed = TurnFailed(error_kind="internal_error", stage="planning")
        await bus.publish(turn_failed)

        assert received == [turn_failed]
        assert len(outbox) == 0

    async def test_subscribe_and_subscribe_all_delegate_to_inner(self) -> None:
        inner = InMemoryEventBus()
        outbox = InMemoryEventOutbox()
        bus = DurableEventBus(inner=inner, outbox=outbox, durable_event_types=frozenset())
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(TurnFailed, handler)
        event = TurnFailed(error_kind="x", stage="y")
        await bus.publish(event)
        assert received == [event]
