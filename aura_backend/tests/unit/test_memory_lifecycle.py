"""Memory lifecycle: recall ranking, consolidation, quotas, and forgetting.

Two design decisions carry the weight here. Memory is an *enhancement*, so every failure in it
degrades the answer rather than failing the turn. And consolidation supersedes rather than
deletes, because a contradiction does not tell you which side is wrong -- the user may be
correcting the newer fact.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from aura_core.adapters import InMemoryMemoryStore, InMemoryVectorStore
from aura_core.domain.errors import EmbeddingError, MemoryStoreError
from aura_core.domain.identifiers import MemoryId, SessionId, UserId
from aura_core.domain.memory import MemoryKind, MemoryQuery
from aura_core.llm import HashEmbeddingProvider
from aura_core.memory import MemoryEngine


@pytest.fixture
def engine(vectors, embedder, metrics, events, clock) -> MemoryEngine:
    return MemoryEngine(
        store=InMemoryMemoryStore(), vectors=vectors, embedder=embedder,
        index_alias="test_memory", metrics=metrics, event_bus=events, clock=clock,
    )


class TestRemember:
    async def test_a_record_carries_its_provenance_and_kind(self, engine, user) -> None:
        record = await engine.remember(
            owner_id=user, content="The user is vegetarian.", kind=MemoryKind.SEMANTIC,
            subject="diet", source="turn:1",
        )
        assert record.kind is MemoryKind.SEMANTIC
        assert record.subject == "diet"
        assert record.provenance.source == "turn:1"

    async def test_a_near_identical_fact_is_not_stored_twice(self, engine, user) -> None:
        """An extractor running every turn would otherwise fill memory with the same fact."""
        first = await engine.remember(
            owner_id=user, content="The user is vegetarian.", source="turn:1"
        )
        second = await engine.remember(
            owner_id=user, content="The user is vegetarian.", source="turn:2"
        )
        assert second.id == first.id

    async def test_a_batch_is_written_sequentially(self, engine, user) -> None:
        """Concurrently, two near-identical facts would both pass the duplicate check."""
        written = await engine.remember_many([
            {"owner_id": user, "content": "The user is vegetarian.", "source": "t1"},
            {"owner_id": user, "content": "The user is vegetarian.", "source": "t2"},
            {"owner_id": user, "content": "The user works in Cairo.", "source": "t3"},
        ])
        assert len({r.id for r in written}) == 2

    async def test_a_timestamp_comes_from_the_injected_clock(self, engine, user, clock) -> None:
        """Wall-clock stamps make decay untestable and can produce a negative age under skew."""
        record = await engine.remember(owner_id=user, content="A fact.", source="t")
        assert record.created_at == clock.now()


class TestRecall:
    async def test_relevant_memories_are_returned_ranked(self, engine, user) -> None:
        await engine.remember(owner_id=user, content="The user is vegetarian.", source="t1")
        await engine.remember(owner_id=user, content="The user works in Cairo.", source="t2")

        hits = await engine.recall(MemoryQuery(owner_id=user, text="the user is vegetarian"))
        assert hits[0].record.content == "The user is vegetarian."
        assert hits[0].score > 0.0

    async def test_an_empty_query_returns_nothing_without_searching(self, engine, user) -> None:
        await engine.remember(owner_id=user, content="A fact.", source="t")
        assert await engine.recall(MemoryQuery(owner_id=user, text="   ")) == []

    async def test_recall_is_owner_scoped(self, engine) -> None:
        alice, bob = UserId("alice"), UserId("bob")
        await engine.remember(owner_id=alice, content="Alice earns 95000.", source="t")

        assert await engine.recall(MemoryQuery(owner_id=bob, text="Alice earns 95000")) == []

    async def test_kinds_can_be_filtered(self, engine, user) -> None:
        await engine.remember(
            owner_id=user, content="The user prefers dark mode.",
            kind=MemoryKind.SEMANTIC, source="t1",
        )
        hits = await engine.recall(MemoryQuery(
            owner_id=user, text="the user prefers dark mode", kinds=(MemoryKind.EPISODIC,)
        ))
        assert hits == []

    async def test_a_subject_filter_narrows_results(self, engine, user) -> None:
        await engine.remember(
            owner_id=user, content="The user is vegetarian.", subject="diet", source="t1"
        )
        await engine.remember(
            owner_id=user, content="The user is vegetarian at work.", subject="office",
            source="t2",
        )
        hits = await engine.recall(MemoryQuery(
            owner_id=user, text="the user is vegetarian", subject="diet"
        ))
        assert all(h.record.subject == "diet" for h in hits)

    async def test_a_minimum_score_can_return_nothing(self, engine, user) -> None:
        await engine.remember(owner_id=user, content="The user is vegetarian.", source="t")
        hits = await engine.recall(MemoryQuery(
            owner_id=user, text="quarterly revenue forecasting", min_score=0.9
        ))
        assert hits == []

    async def test_a_token_budget_bounds_what_is_returned(self, engine, user) -> None:
        """Memory competes with retrieval for the same context window."""
        for i in range(10):
            await engine.remember(
                owner_id=user, content=f"Distinct fact number {i} " + "x" * 200, source=f"t{i}"
            )
        hits = await engine.recall(MemoryQuery(
            owner_id=user, text="distinct fact number", limit=10, token_budget=120
        ))
        assert sum(max(1, (len(h.record.content) + 3) // 4) for h in hits) <= 120

    async def test_recall_reinforces_only_what_it_returned(self, engine, user) -> None:
        """Otherwise every recall inflates access_count for irrelevant records."""
        used = await engine.remember(
            owner_id=user, content="The user is vegetarian.", source="t1"
        )
        unused = await engine.remember(
            owner_id=user, content="Quarterly revenue forecasting model.", source="t2"
        )
        await engine.recall(MemoryQuery(owner_id=user, text="the user is vegetarian", limit=1))

        assert (await engine._store.get(used.id)).access_count >= 1
        assert (await engine._store.get(unused.id)).access_count == 0

    async def test_an_embedding_failure_degrades_recall_rather_than_the_turn(self, user) -> None:
        """Losing memory degrades answer quality; failing the turn is a worse trade."""

        class BrokenEmbedder(HashEmbeddingProvider):
            async def embed(self, texts, *, for_query: bool = False):  # noqa: ANN001
                raise EmbeddingError("embedding service is down")

        engine = MemoryEngine(
            store=InMemoryMemoryStore(), vectors=InMemoryVectorStore(),
            embedder=BrokenEmbedder(dimension=64), index_alias="m",
        )
        assert await engine.recall(MemoryQuery(owner_id=user, text="anything")) == []

    async def test_a_vector_store_failure_degrades_recall(self, user, embedder) -> None:
        class BrokenVectors(InMemoryVectorStore):
            async def search(self, *args, **kwargs):  # noqa: ANN001
                raise RuntimeError("qdrant is unreachable")

        engine = MemoryEngine(
            store=InMemoryMemoryStore(), vectors=BrokenVectors(), embedder=embedder,
            index_alias="m",
        )
        assert await engine.recall(MemoryQuery(owner_id=user, text="anything")) == []

    async def test_reinforcement_failure_does_not_fail_the_recall(self, user, embedder) -> None:
        """Reinforcement is bookkeeping."""

        class UnwritableStore(InMemoryMemoryStore):
            async def update(self, record):  # noqa: ANN001
                raise RuntimeError("write path is down")

        engine = MemoryEngine(
            store=UnwritableStore(), vectors=InMemoryVectorStore(), embedder=embedder,
            index_alias="m",
        )
        await engine.remember(owner_id=user, content="The user is vegetarian.", source="t")
        hits = await engine.recall(MemoryQuery(owner_id=user, text="the user is vegetarian"))
        assert hits


class TestConsolidation:
    async def test_contradictions_are_superseded_not_deleted(self, engine, user) -> None:
        """The user may be correcting the newer fact; an audit trail of what was believed when
        is worth keeping."""
        old = await engine.remember(
            owner_id=user, content="The user lives in Cairo.", subject="home", source="t1"
        )
        engine._clock.advance(86_400)
        new = await engine.remember(
            owner_id=user, content="The user lives in Alexandria.", subject="home", source="t2"
        )
        outcome = await engine.consolidate(user)

        assert outcome.superseded == 1
        stored_old = await engine._store.get(old.id)
        assert stored_old.superseded_by == new.id
        assert stored_old is not None  # still readable

    async def test_a_single_record_per_subject_is_left_alone(self, engine, user) -> None:
        await engine.remember(
            owner_id=user, content="The user lives in Cairo.", subject="home", source="t1"
        )
        assert (await engine.consolidate(user)).superseded == 0

    async def test_confidence_decays_with_disuse(self, engine, user) -> None:
        record = await engine.remember(
            owner_id=user, content="The user prefers tea.", subject="drink", source="t1"
        )
        engine._clock.advance(60 * 86_400)
        outcome = await engine.consolidate(user)

        assert outcome.decayed >= 1
        assert (await engine._store.get(record.id)).confidence < record.confidence

    async def test_daily_consolidation_decays_like_a_single_run(self, engine, user) -> None:
        """Consolidation runs every night and persists what it decays. Re-applying the decay for
        the *total* age on each run compounded it: a semantic fact with a 180-day half-life fell
        below the floor and was deleted after about three weeks of disuse."""
        record = await engine.remember(
            owner_id=user, content="The user's sister is called Mona.", subject="sister",
            source="t1",
        )
        for _ in range(30):
            engine._clock.advance(86_400)
            await engine.consolidate(user)

        survivor = await engine._store.get(record.id)
        assert survivor is not None, "a month of disuse must not delete a 180-day fact"
        single_run = record.decayed(now=record.last_accessed_at + timedelta(days=30))
        assert survivor.confidence == pytest.approx(single_run.confidence, abs=1e-5)

    async def test_a_clock_behind_the_last_decay_never_raises_confidence(self, engine, user) -> None:
        record = await engine.remember(
            owner_id=user, content="The user drives a blue car.", subject="car", source="t1"
        )
        decayed = record.decayed(now=record.last_accessed_at + timedelta(days=20))
        earlier = decayed.decayed(now=record.last_accessed_at + timedelta(days=10))
        assert earlier.confidence == decayed.confidence

    async def test_records_below_the_confidence_floor_expire(self, engine, user) -> None:
        record = await engine.remember(
            owner_id=user, content="A passing detail.", subject="detail", source="t1"
        )
        engine._clock.advance(3650 * 86_400)
        outcome = await engine.consolidate(user)

        assert outcome.expired >= 1
        assert await engine._store.get(record.id) is None

    async def test_consolidation_reports_what_it_examined(self, engine, user) -> None:
        for i in range(3):
            await engine.remember(owner_id=user, content=f"Fact {i}.", source=f"t{i}")
        outcome = await engine.consolidate(user)

        assert outcome.examined >= 3
        assert outcome.duration_ms >= 0.0

    async def test_kinds_whose_policy_forbids_it_are_skipped(self, engine, user) -> None:
        outcome = await engine.consolidate(user, kinds=[MemoryKind.WORKING])
        assert outcome.examined == 0

    async def test_consolidating_an_empty_memory_is_a_no_op(self, engine, user) -> None:
        outcome = await engine.consolidate(user)
        assert outcome.examined == 0
        assert outcome.superseded == 0


class TestForgetting:
    async def test_a_single_record_can_be_deleted(self, engine, user) -> None:
        record = await engine.remember(owner_id=user, content="A fact.", source="t")
        assert await engine.forget(record.id, owner_id=user) is True
        assert await engine._store.get(record.id) is None

    async def test_deleting_another_users_record_is_refused(self, engine, user) -> None:
        """A memory id in a URL is not a capability."""
        record = await engine.remember(owner_id=user, content="A fact.", source="t")
        assert await engine.forget(record.id, owner_id=UserId("mallory")) is False
        assert await engine._store.get(record.id) is not None

    async def test_deleting_an_unknown_record_is_false(self, engine, user) -> None:
        assert await engine.forget(MemoryId("nope"), owner_id=user) is False

    async def test_everything_from_a_source_can_be_forgotten(self, engine, user) -> None:
        """Only possible because every write records provenance."""
        await engine.remember(owner_id=user, content="Fact A from the doc.", source="doc:1")
        await engine.remember(owner_id=user, content="Fact B from the doc.", source="doc:1")
        await engine.remember(owner_id=user, content="Unrelated fact.", source="turn:9")

        assert await engine.forget_source(owner_id=user, source="doc:1") == 2
        remaining = await engine.recall(MemoryQuery(owner_id=user, text="unrelated fact"))
        assert len(remaining) == 1

    async def test_forgetting_a_source_leaves_no_retrievable_orphans(self, engine, user) -> None:
        """The vector points must go too, or the content is still retrievable."""
        await engine.remember(owner_id=user, content="Fact from the doc.", source="doc:1")
        await engine.forget_source(owner_id=user, source="doc:1")

        assert await engine.recall(MemoryQuery(owner_id=user, text="fact from the doc")) == []

    async def test_forgetting_an_unknown_source_is_zero(self, engine, user) -> None:
        assert await engine.forget_source(owner_id=user, source="never-seen") == 0


class TestQuota:
    async def test_exceeding_the_quota_raises_rather_than_evicting(self, user, embedder) -> None:
        """A user losing memories without being told is worse than a visible error."""
        engine = MemoryEngine(
            store=InMemoryMemoryStore(), vectors=InMemoryVectorStore(), embedder=embedder,
            index_alias="m", max_records_per_owner=2,
        )
        await engine.remember(owner_id=user, content="Distinct fact one.", source="t1")
        await engine.remember(owner_id=user, content="Another separate matter.", source="t2")

        with pytest.raises(MemoryStoreError) as excinfo:
            await engine.remember(owner_id=user, content="A third unrelated thing.", source="t3")
        assert excinfo.value.details["limit"] == 2

    async def test_the_quota_is_per_user(self, embedder) -> None:
        engine = MemoryEngine(
            store=InMemoryMemoryStore(), vectors=InMemoryVectorStore(), embedder=embedder,
            index_alias="m", max_records_per_owner=1,
        )
        await engine.remember(owner_id=UserId("alice"), content="Alice's fact.", source="t")
        await engine.remember(owner_id=UserId("bob"), content="Bob's fact.", source="t")


class TestStats:
    async def test_counts_are_reported_per_kind(self, engine, user) -> None:
        await engine.remember(
            owner_id=user, content="A semantic fact.", kind=MemoryKind.SEMANTIC, source="t1"
        )
        await engine.remember(
            owner_id=user, content="Something that happened.", kind=MemoryKind.EPISODIC,
            source="t2",
        )
        stats = await engine.stats(user)

        assert stats["semantic"] == 1
        assert stats["episodic"] == 1
        assert stats["reflection"] == 0

    async def test_stats_are_owner_scoped(self, engine) -> None:
        await engine.remember(owner_id=UserId("alice"), content="A fact.", source="t")
        assert (await engine.stats(UserId("bob")))["semantic"] == 0
