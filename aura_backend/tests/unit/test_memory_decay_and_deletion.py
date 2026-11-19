"""Decay across access cycles (F11), and consolidation/forget racing other writers (F27).

F11: consolidation persists the decayed confidence, and reinforcement only moved
``last_accessed_at``. The decayed value became the base for the next access period, so decay
compounded across periods: a semantic fact recalled every week fell below its floor and was
deleted *sooner* than one nobody ever used. Effective confidence is now the record's base
confidence decayed from its last access, and an access restores it.

F27: consolidation wrote whole rows back from the snapshot it listed, so a memory forgotten
while it ran came back, and an access recorded meanwhile was reverted. ``forget`` deleted the
row before the vector point, so a failed vector delete left an orphan nothing could remove.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from aura_core.adapters import InMemoryMemoryStore, InMemoryVectorStore
from aura_core.domain.identifiers import MemoryId
from aura_core.domain.memory import (
    BASE_CONFIDENCE_METADATA_KEY,
    DECAYED_AT_METADATA_KEY,
    MemoryKind,
    MemoryQuery,
)
from aura_core.memory import MemoryEngine

DAY = 86_400


@pytest.fixture
def engine(vectors, embedder, metrics, events, clock) -> MemoryEngine:
    return MemoryEngine(
        store=InMemoryMemoryStore(), vectors=vectors, embedder=embedder,
        index_alias="test_memory", metrics=metrics, event_bus=events, clock=clock,
    )


class TestDecayAcrossAccessCycles:
    async def test_a_weekly_used_fact_survives_a_year_of_nightly_consolidation(
        self, engine, user, clock
    ) -> None:
        used = await engine.remember(
            owner_id=user, content="The user's sister is called Mona.", subject="sister",
            source="t1", confidence=0.8,
        )
        unused = await engine.remember(
            owner_id=user, content="The user once visited Lisbon.", subject="trip",
            source="t1", confidence=0.8,
        )
        assert engine._store is not None
        for day in range(1, 366):
            clock.advance(DAY)
            if day % 7 == 0:
                await engine._store.touch_many([used.id], owner_id=user, at=clock.now())
            await engine.consolidate(user)

        survivor = await engine._store.get(used.id)
        assert survivor is not None, "a fact used every week must not decay to death"
        # Never more than a week without access: the effective value stays near its base.
        assert survivor.confidence >= 0.79
        # The unused fact decays exactly as before: below the 0.6 floor once its 180-day
        # half-life has passed, so it is gone within the year.
        assert await engine._store.get(unused.id) is None

    async def test_an_unused_fact_decays_as_a_single_run_from_its_last_access(
        self, engine, user, clock
    ) -> None:
        record = await engine.remember(
            owner_id=user, content="The user drives a blue car.", subject="car", source="t1",
            confidence=0.8,
        )
        for _ in range(179):
            clock.advance(DAY)
            await engine.consolidate(user)
        survivor = await engine._store.get(record.id)
        assert survivor is not None
        expected = record.decayed(now=record.last_accessed_at + timedelta(days=179))
        assert survivor.confidence == pytest.approx(expected.confidence, abs=1e-5)

        for _ in range(2):
            clock.advance(DAY)
            await engine.consolidate(user)
        assert await engine._store.get(record.id) is None

    async def test_an_access_restores_the_base_confidence(self, engine, user, clock) -> None:
        record = await engine.remember(
            owner_id=user, content="The user prefers tea.", subject="drink", source="t1",
            confidence=0.8,
        )
        clock.advance(60 * DAY)
        await engine.consolidate(user)
        decayed = await engine._store.get(record.id)
        assert decayed.confidence < 0.8

        hits = await engine.recall(MemoryQuery(owner_id=user, text="the user prefers tea"))
        assert [h.record.id for h in hits] == [record.id]

        restored = await engine._store.get(record.id)
        assert restored.confidence == pytest.approx(0.8)
        # A later consolidation measures decay from the access, not from the old value.
        clock.advance(DAY)
        await engine.consolidate(user)
        again = await engine._store.get(record.id)
        assert again.confidence == pytest.approx(
            0.8 * (0.5 + 0.5 * 0.5 ** (1 / 180)), abs=1e-5
        )

    def test_the_domain_touch_restores_the_base(self, user, clock) -> None:
        from aura_core.domain.memory import MemoryRecord, Provenance

        record = MemoryRecord(
            id=MemoryId.new(), kind=MemoryKind.SEMANTIC, owner_id=user, content="A fact.",
            provenance=Provenance(source="t"), confidence=0.8,
            created_at=clock.now(), last_accessed_at=clock.now(),
        )
        decayed = record.decayed(now=clock.now() + timedelta(days=90))
        assert decayed.confidence < 0.8
        assert decayed.base_confidence == pytest.approx(0.8)
        touched = decayed.touched(now=clock.now() + timedelta(days=90))
        assert touched.confidence == pytest.approx(0.8)

    def test_a_record_decayed_before_the_base_existed_recovers_it(self, user, clock) -> None:
        """Rows decayed by the previous code carry only the stamp. Within one access period
        the old decay was exactly ``base * blend(age)``, so the base is recoverable."""
        from aura_core.domain.memory import MemoryRecord, Provenance

        start = clock.now()
        legacy = MemoryRecord(
            id=MemoryId.new(), kind=MemoryKind.SEMANTIC, owner_id=user, content="A fact.",
            provenance=Provenance(source="t"),
            confidence=round(0.8 * (0.5 + 0.5 * 0.5 ** (30 / 180)), 6),
            created_at=start, last_accessed_at=start,
            metadata={DECAYED_AT_METADATA_KEY: (start + timedelta(days=30)).isoformat()},
        )
        assert BASE_CONFIDENCE_METADATA_KEY not in legacy.metadata
        assert legacy.base_confidence == pytest.approx(0.8, abs=1e-5)

    def test_raising_confidence_raises_the_base(self, user, clock) -> None:
        """Reinforcing a restated fact raises ``confidence``; the base follows it up."""
        from aura_core.domain.memory import MemoryRecord, Provenance

        record = MemoryRecord(
            id=MemoryId.new(), kind=MemoryKind.SEMANTIC, owner_id=user, content="A fact.",
            provenance=Provenance(source="t"), confidence=0.7,
            created_at=clock.now(), last_accessed_at=clock.now(),
        ).decayed(now=clock.now() + timedelta(days=10))
        raised = replace(record, confidence=0.9)
        assert raised.base_confidence == pytest.approx(0.9)


class _RacingStore(InMemoryMemoryStore):
    """Runs a hook right after consolidation lists the owner's records -- the window in which
    a user's delete, or a recall's reinforcement, lands in production."""

    def __init__(self) -> None:
        super().__init__()
        self.after_next_list = None

    async def list_for_owner(self, owner_id, **kwargs):  # noqa: ANN001, ANN003
        snapshot = await super().list_for_owner(owner_id, **kwargs)
        hook, self.after_next_list = self.after_next_list, None
        if hook is not None:
            await hook(self)
        return snapshot


class _FlakyVectors(InMemoryVectorStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_deletes = False

    async def delete(self, identity, *, point_ids):  # noqa: ANN001
        if self.fail_deletes:
            raise RuntimeError("vector store unavailable")
        return await super().delete(identity, point_ids=point_ids)


class TestConsolidationRaces:
    @pytest.fixture
    def racing(self, vectors, embedder, metrics, events, clock) -> MemoryEngine:
        return MemoryEngine(
            store=_RacingStore(), vectors=vectors, embedder=embedder,
            index_alias="test_memory", metrics=metrics, event_bus=events, clock=clock,
        )

    async def test_a_memory_forgotten_mid_run_is_not_recreated_by_decay(
        self, racing, user, clock
    ) -> None:
        record = await racing.remember(
            owner_id=user, content="The user prefers tea.", subject="drink", source="t1"
        )
        clock.advance(30 * DAY)

        async def forget(store) -> None:  # noqa: ANN001
            assert await racing.forget(record.id, owner_id=user)

        racing._store.after_next_list = forget
        await racing.consolidate(user, kinds=[MemoryKind.SEMANTIC])

        assert await racing._store.get(record.id) is None, "consolidation resurrected a forgotten memory"

    async def test_a_memory_forgotten_mid_run_is_not_recreated_by_supersede(
        self, racing, user, clock
    ) -> None:
        old = await racing.remember(
            owner_id=user, content="The user lives in Cairo.", subject="home", source="t1"
        )
        clock.advance(DAY)
        await racing.remember(
            owner_id=user, content="The user lives in Alexandria.", subject="home", source="t2"
        )

        async def forget(store) -> None:  # noqa: ANN001
            assert await racing.forget(old.id, owner_id=user)

        racing._store.after_next_list = forget
        await racing.consolidate(user, kinds=[MemoryKind.SEMANTIC])

        assert await racing._store.get(old.id) is None

    async def test_an_access_recorded_mid_run_is_not_reverted(self, racing, user, clock) -> None:
        record = await racing.remember(
            owner_id=user, content="The user prefers tea.", subject="drink", source="t1"
        )
        clock.advance(30 * DAY)

        async def touch(store) -> None:  # noqa: ANN001
            await store.touch_many([record.id], owner_id=user, at=clock.now())

        racing._store.after_next_list = touch
        await racing.consolidate(user, kinds=[MemoryKind.SEMANTIC])

        stored = await racing._store.get(record.id)
        assert stored.access_count == 1
        assert stored.last_accessed_at == clock.now()


class TestForgetLeavesNoOrphanVector:
    @pytest.fixture
    def flaky(self) -> _FlakyVectors:
        return _FlakyVectors()

    @pytest.fixture
    def engine_with_flaky_vectors(self, flaky, embedder, metrics, events, clock) -> MemoryEngine:
        return MemoryEngine(
            store=InMemoryMemoryStore(), vectors=flaky, embedder=embedder,
            index_alias="test_memory", metrics=metrics, event_bus=events, clock=clock,
        )

    async def test_a_failed_vector_delete_keeps_the_row_so_forget_can_be_retried(
        self, engine_with_flaky_vectors, flaky, user
    ) -> None:
        engine = engine_with_flaky_vectors
        record = await engine.remember(owner_id=user, content="A secret fact.", source="t")
        flaky.fail_deletes = True

        with pytest.raises(Exception):
            await engine.forget(record.id, owner_id=user)
        # The row is still there, so the delete can be retried; had the row gone first, the
        # vector point would be an orphan no API could reach.
        assert await engine._store.get(record.id) is not None

        flaky.fail_deletes = False
        assert await engine.forget(record.id, owner_id=user) is True
        assert await engine._store.get(record.id) is None
        assert await flaky.count(engine._identity, owner_id=user) == 0

    async def test_forgetting_another_users_memory_leaves_its_vector(
        self, engine, vectors, user
    ) -> None:
        from aura_core.domain.identifiers import UserId

        record = await engine.remember(owner_id=user, content="A fact.", source="t")
        assert await engine.forget(record.id, owner_id=UserId("mallory")) is False
        assert await vectors.count(engine._identity, owner_id=user) == 1
