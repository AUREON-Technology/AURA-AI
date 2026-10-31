"""Memory write races (audit F-16 / AR-06).

Reinforcement used to be ``store.update(record.touched())`` -- a whole-row write of the copy a
recall had read before ranking. Anything that changed the row in between was reverted by it: a
supersede came undone (the memory came back to life), and concurrent recalls each wrote
``access_count + 1`` of the *same* stale value, so increments were lost.

These tests force the interleavings with events rather than sleeps, so they are deterministic,
and each one fails against the old whole-row path.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from aura_core.adapters import InMemoryMemoryStore
from aura_core.domain.identifiers import MemoryId, UserId
from aura_core.domain.memory import MemoryKind, MemoryQuery, MemoryRecord, Provenance
from aura_core.memory import MemoryEngine


class _PausingStore(InMemoryMemoryStore):
    """Pauses a recall right after it has read its snapshot, until the test lets it go on."""

    def __init__(self) -> None:
        super().__init__()
        self.snapshot_taken = asyncio.Event()
        self.release = asyncio.Event()
        self.pause = False

    async def get_many(self, memory_ids):
        records = await super().get_many(memory_ids)
        if self.pause:
            self.snapshot_taken.set()
            await self.release.wait()
        return records


@pytest.fixture
def store() -> _PausingStore:
    return _PausingStore()


@pytest.fixture
def engine(store, vectors, embedder, metrics, events, clock) -> MemoryEngine:
    return MemoryEngine(
        store=store, vectors=vectors, embedder=embedder,
        index_alias="race_memory", metrics=metrics, event_bus=events, clock=clock,
    )


class TestReinforceVersusSupersede:
    async def test_a_recall_overlapping_a_supersede_does_not_resurrect_the_memory(
        self, engine, store, user
    ) -> None:
        old = await engine.remember(
            owner_id=user, content="The user lives in Cairo.", subject="home", source="t1"
        )
        newer = MemoryId.new()
        store.pause = True

        async def recall():
            return await engine.recall(MemoryQuery(owner_id=user, text="where does the user live"))

        async def supersede():
            await store.snapshot_taken.wait()
            current = await store.get(old.id)
            await store.update(current.superseded(newer))
            store.release.set()

        hits, _ = await asyncio.gather(recall(), supersede())

        # The recall ranked its snapshot (the record was active when it read it)...
        assert [h.record.id for h in hits] == [old.id]
        # ...but its reinforcement must not have written that snapshot back over the supersede.
        stored = await store.get(old.id)
        assert stored.superseded_by == newer
        assert not stored.is_active
        # And a superseded record is not reinforced at all.
        assert stored.access_count == old.access_count

    async def test_a_reinforced_duplicate_that_was_superseded_meanwhile_stays_superseded(
        self, engine, store, user
    ) -> None:
        """``remember`` of a near-duplicate is reinforcement too, from a record it read before
        embedding. If that record is superseded before the write, the new statement is stored as
        a fresh fact rather than reviving the old row."""
        old = await engine.remember(owner_id=user, content="The user is vegetarian.", source="t1")
        newer = MemoryId.new()

        original_get = store.get
        superseded_once = False

        async def get_then_supersede(memory_id):
            nonlocal superseded_once
            record = await original_get(memory_id)
            if record is not None and not superseded_once and memory_id == old.id:
                superseded_once = True
                await store.update(record.superseded(newer))
            return record  # the stale, still-active snapshot

        store.get = get_then_supersede  # type: ignore[method-assign]
        again = await engine.remember(owner_id=user, content="The user is vegetarian.", source="t2")

        assert (await original_get(old.id)).superseded_by == newer
        assert again.id != old.id
        assert again.is_active


class TestConcurrentReinforcement:
    async def test_concurrent_recalls_do_not_lose_access_count_increments(
        self, engine, store, user
    ) -> None:
        record = await engine.remember(owner_id=user, content="The user likes jazz.", source="t")
        query = MemoryQuery(owner_id=user, text="what music does the user like")

        await asyncio.gather(*(engine.recall(query) for _ in range(10)))

        assert (await store.get(record.id)).access_count == record.access_count + 10

    async def test_touch_moves_last_access_forward_never_back(self, store, clock) -> None:
        now = clock.now()
        record = MemoryRecord(
            id=MemoryId("m1"), kind=MemoryKind.SEMANTIC, owner_id=UserId("u"),
            content="A fact.", provenance=Provenance(source="t"),
            last_accessed_at=now, confidence=0.6,
        )
        await store.put(record)

        assert await store.touch_many([record.id], now=now - timedelta(hours=1)) == 1
        stored = await store.get(record.id)
        assert stored.last_accessed_at == now
        assert stored.access_count == 1

    async def test_touch_only_changes_the_reinforcement_fields(self, store, clock) -> None:
        now = clock.now()
        record = MemoryRecord(
            id=MemoryId("m1"), kind=MemoryKind.SEMANTIC, owner_id=UserId("u"),
            content="A fact.", provenance=Provenance(source="t"), subject="s",
            confidence=0.6, metadata={"k": "v"}, last_accessed_at=now,
        )
        await store.put(record)

        assert await store.touch_many(
            [record.id, record.id, MemoryId("missing")],
            now=now + timedelta(minutes=5), confidence_floor=0.9,
        ) == 1  # duplicates collapse; unknown ids are skipped
        stored = await store.get(record.id)
        assert stored == replace(
            record, access_count=1, last_accessed_at=now + timedelta(minutes=5), confidence=0.9
        )

        # A floor below the current value never lowers confidence, and it is capped at 1.
        await store.touch_many([record.id], now=now, confidence_floor=0.1)
        assert (await store.get(record.id)).confidence == 0.9
        await store.touch_many([record.id], now=now, confidence_floor=1.7)
        assert (await store.get(record.id)).confidence == 1.0
