"""Cross-worker consistency of the per-process lexical index (audit F-14, roadmap R2.4 A).

Production runs the gateway with ``--workers 4``. Each worker has its own BM25 index, while the
document store, the vector store and the cache (Redis) are shared. Here two
:class:`KnowledgeEngine` instances over one document store, one vector store and one cache stand
in for two uvicorn workers -- the scenario of the audit's ``repro_lexical.py``.

What is pinned:

- a delete on worker A leaves worker B's index, not just its results;
- an upload on worker A becomes *lexically* searchable on worker B (it never did before);
- a new version on A replaces the old one in B's index;
- the feed's failure modes -- a reset counter, a lost entry, an unreachable cache -- end in a
  correct index (rebuild) or a retried publish, never in a wrong answer or a failed write;
- a rebuild that runs while this worker ingests does not lose that ingest;
- ``chunks_for`` resolves only active versions on the in-memory store (the Postgres half is
  checked against a real instance; see the benchmark report).
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.config import load_settings
from aura_core.container import build_container
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.knowledge import KnowledgeEngine
from aura_core.knowledge.lexical_sync import LexicalChangeFeed
from aura_core.llm.cache import InMemoryCache

RUNBOOK = "The AURA-4419 escalation runbook says to page the on-call SRE."


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def shared_cache() -> InMemoryCache:
    return InMemoryCache(max_entries=1024, default_ttl_s=600)


@pytest.fixture
def worker(documents, vectors, embedder, index_manager, shared_cache):
    """Build one 'gateway worker': its own engine (and BM25 index) over the shared stores."""

    def build(*, cache=None, clock=None, feed=True, **feed_options) -> KnowledgeEngine:
        options = {"check_interval_s": 0.0, **feed_options}
        return KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=index_manager, score_threshold=0.15,
            lexical_feed=(
                LexicalChangeFeed(cache if cache is not None else shared_cache, **options)
                if feed else None
            ),
            **({"monotonic": clock} if clock is not None else {}),
        )

    return build


async def _lexical_ids(engine: KnowledgeEngine, query: str, owner: UserId) -> list[str]:
    """What the lexical half of ``retrieve`` returns on this worker, after catching up."""
    return [chunk_id for chunk_id, _ in await engine._lexical_search(query, owner, True)]


class TestTwoWorkers:
    async def test_the_audit_repro_delete_on_a_is_gone_from_b(self, worker, user) -> None:
        """``repro_lexical.py``: B starts, A ingests, B restarts, A deletes, B searches."""
        a, b = worker(), worker()
        await b.rebuild_lexical_index()
        result = await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        await b.rebuild_lexical_index()
        assert b.lexical_size == a.lexical_size > 0

        assert await a.delete_document(result.document.id, owner_id=user) > 0

        assert not (await a.retrieve("AURA-4419 runbook", owner_id=user)).chunks
        assert not (await b.retrieve("AURA-4419 runbook", owner_id=user)).chunks
        # Gone from B's index itself, not merely filtered out of its results.
        assert b.lexical_size == 0

    async def test_an_upload_on_a_is_lexically_searchable_on_b(self, worker, user) -> None:
        a, b = worker(), worker()
        await b.rebuild_lexical_index()
        assert b.lexical_size == 0

        result = await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)

        ids = await _lexical_ids(b, "AURA-4419", user)
        assert ids and all(i.startswith(result.document.id.value) for i in ids)
        retrieved = await b.retrieve("AURA-4419 runbook", owner_id=user)
        assert retrieved.chunks
        assert retrieved.chunks[0].lexical_rank is not None

    async def test_without_the_feed_b_never_sees_the_upload(self, worker, user) -> None:
        """The defect the feed fixes, pinned so the test above cannot pass vacuously."""
        a, b = worker(feed=False), worker(feed=False)
        await b.rebuild_lexical_index()
        await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        assert await _lexical_ids(b, "AURA-4419", user) == []

    async def test_a_new_version_on_a_replaces_the_old_one_in_b(self, worker, user) -> None:
        a, b = worker(), worker()
        await a.ingest(content="The expense cap is 250 dollars per claim.", title="Policy",
                       document_id=DocumentId("policy"), owner_id=user)
        await b.rebuild_lexical_index()
        await a.ingest(content="The expense cap is 500 dollars per claim.", title="Policy",
                       document_id=DocumentId("policy"), owner_id=user)

        assert await _lexical_ids(b, "250", user) == []
        assert await _lexical_ids(b, "500", user) == ["policy:v2:0"]
        assert b.lexical_size == a.lexical_size

    async def test_tenant_scoping_survives_the_feed(self, worker) -> None:
        alice, bob = UserId("alice"), UserId("bob")
        a, b = worker(), worker()
        await b.rebuild_lexical_index()
        await a.ingest(content=RUNBOOK, title="runbook", owner_id=alice)
        assert await _lexical_ids(b, "AURA-4419", bob) == []
        assert await _lexical_ids(b, "AURA-4419", alice)

    async def test_erasure_on_a_empties_b(self, worker, user) -> None:
        a, b = worker(), worker()
        await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        await a.ingest(content="Quarterly targets are in the AURA-4419 deck.", title="deck",
                       owner_id=user)
        await b.rebuild_lexical_index()
        assert b.lexical_size > 0
        await a.erase_owner(user)
        assert await _lexical_ids(b, "AURA-4419", user) == []
        assert b.lexical_size == 0

    async def test_b_checks_the_feed_at_most_once_per_interval(self, worker, user) -> None:
        clock = _Clock()
        a, b = worker(), worker(clock=clock, check_interval_s=0.5)
        await b.rebuild_lexical_index()
        assert await _lexical_ids(b, "AURA-4419", user) == []  # checks now, then not for 0.5 s

        await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        clock.now += 0.4
        assert await _lexical_ids(b, "AURA-4419", user) == []
        clock.now += 0.2
        assert await _lexical_ids(b, "AURA-4419", user)


class TestFeedFailureModes:
    async def test_a_reset_counter_triggers_a_rebuild(self, worker, user, shared_cache) -> None:
        a, b = worker(), worker()
        for n in range(3):
            await a.ingest(content=f"Note {n} about AURA-{n}00.", title=f"n{n}", owner_id=user)
        await b.rebuild_lexical_index()
        assert b._lexical_cursor == 3

        shared_cache.clear()  # Redis restarted without persistence
        await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)  # head is now 1 < 3

        assert await _lexical_ids(b, "AURA-4419", user)
        assert b.lexical_size == a.lexical_size
        assert b._lexical_cursor == 1

    async def test_a_lost_entry_is_waited_for_then_rebuilt(
        self, worker, user, shared_cache
    ) -> None:
        clock = _Clock()
        a, b = worker(), worker(clock=clock, missing_grace_s=5.0)
        await b.rebuild_lexical_index()
        await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        # The writer died between its INCR and its SET: generation 1 has no entry.
        await shared_cache.delete(f"{LexicalChangeFeed.ENTRY_PREFIX}1")

        assert await _lexical_ids(b, "AURA-4419", user) == []
        assert b._lexical_cursor == 0  # does not skip past the gap
        clock.now += 1.0
        assert await _lexical_ids(b, "AURA-4419", user) == []  # still within the grace
        clock.now += 5.0
        assert await _lexical_ids(b, "AURA-4419", user)  # gave up waiting and rebuilt
        assert b._lexical_cursor == 1

    async def test_entries_after_a_gap_are_still_applied(
        self, worker, user, shared_cache
    ) -> None:
        a, b = worker(), worker(missing_grace_s=60.0)
        await b.rebuild_lexical_index()
        await a.ingest(content="Nothing to see here.", title="first", owner_id=user)
        await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        await shared_cache.delete(f"{LexicalChangeFeed.ENTRY_PREFIX}1")
        assert await _lexical_ids(b, "AURA-4419", user)
        assert b._lexical_cursor == 0

    async def test_too_far_behind_rebuilds_instead_of_replaying(self, worker, user) -> None:
        a, b = worker(), worker(max_replay=2)
        await b.rebuild_lexical_index()
        for n in range(3):
            await a.ingest(content=f"Note {n} about AURA-{n}00.", title=f"n{n}", owner_id=user)
        await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        assert await _lexical_ids(b, "AURA-4419", user)
        assert b._lexical_cursor == 4

    async def test_an_unreachable_cache_never_fails_the_write_and_is_retried(
        self, worker, user, shared_cache
    ) -> None:
        class Flaky(InMemoryCache):
            down = True

            async def incr(self, key, *, ttl_s=None):
                if self.down:
                    raise ConnectionError("redis is down")  # RedisCache.incr raises by design
                return await super().incr(key, ttl_s=ttl_s)

        flaky = Flaky(max_entries=1024, default_ttl_s=600)
        a = worker(cache=flaky)
        b = worker(cache=flaky)
        await b.rebuild_lexical_index()

        result = await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        assert result.created  # the write itself succeeded
        assert await _lexical_ids(b, "AURA-4419", user) == []

        flaky.down = False
        await a.retrieve("anything", owner_id=user)  # A's next sync flushes the pending id
        assert await _lexical_ids(b, "AURA-4419", user)

    async def test_deletes_hold_on_read_even_with_the_feed_down(self, worker, user) -> None:
        class Down(InMemoryCache):
            async def get(self, key):
                return None

            async def get_many(self, keys):
                return {}

            async def incr(self, key, *, ttl_s=None):
                raise ConnectionError("redis is down")

        down = Down()
        a, b = worker(cache=down), worker(cache=down)
        await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        await b.rebuild_lexical_index()
        result = await a.retrieve("AURA-4419", owner_id=user)
        await a.delete_document(result.chunks[0].chunk.document_id, owner_id=user)

        assert not (await b.retrieve("AURA-4419 runbook", owner_id=user)).chunks


class TestRebuild:
    async def test_an_ingest_during_a_rebuild_is_not_lost(
        self, documents, vectors, embedder, index_manager, user
    ) -> None:
        """The rebuild builds a new index and swaps it in. An ingest that lands in the old index
        while the store is being scanned must survive the swap."""
        release = asyncio.Event()
        scanning = asyncio.Event()
        original = documents.iter_all_chunks

        async def slow_iter(*, batch_size: int = 500):
            scanning.set()
            await release.wait()
            async for batch in original(batch_size=batch_size):
                yield batch

        engine = KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=index_manager, score_threshold=0.15,
        )
        documents.iter_all_chunks = slow_iter  # type: ignore[method-assign]
        try:
            rebuild = asyncio.create_task(engine.rebuild_lexical_index())
            await scanning.wait()
            # Lands while the scan is parked, before it has read anything.
            await engine.ingest(content=RUNBOOK, title="runbook", owner_id=user)
            release.set()
            await rebuild
        finally:
            del documents.iter_all_chunks

        assert await _lexical_ids(engine, "AURA-4419", user)

    async def test_queries_during_a_rebuild_read_the_previous_index(
        self, documents, vectors, embedder, index_manager, user
    ) -> None:
        engine = KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=index_manager, score_threshold=0.15,
        )
        await engine.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        release, scanning = asyncio.Event(), asyncio.Event()
        original = documents.iter_all_chunks

        async def slow_iter(*, batch_size: int = 500):
            scanning.set()
            await release.wait()
            async for batch in original(batch_size=batch_size):
                yield batch

        documents.iter_all_chunks = slow_iter  # type: ignore[method-assign]
        try:
            rebuild = asyncio.create_task(engine.rebuild_lexical_index())
            await scanning.wait()
            assert await _lexical_ids(engine, "AURA-4419", user)  # not an emptied index
            release.set()
            await rebuild
        finally:
            del documents.iter_all_chunks


class TestContainerWiring:
    async def test_the_containers_engine_publishes_to_its_cache(self) -> None:
        """The audit's repro, verbatim in shape: the container's engine is worker A, and a
        second engine over the container's stores and cache is worker B."""
        container = build_container(load_settings(refresh=True, validate=False))
        a = container.knowledge
        b = KnowledgeEngine(
            documents=a._documents, vectors=a._vectors, embedder=a._embedder,
            index_manager=a._indexes, top_k=5,
            lexical_feed=LexicalChangeFeed(container.cache_backend, check_interval_s=0.0),
        )
        await container.index_manager.ensure_ready()
        user = UserId("u1")
        await b.rebuild_lexical_index()
        result = await a.ingest(content=RUNBOOK, title="runbook", owner_id=user)
        assert await _lexical_ids(b, "AURA-4419", user)

        await a.delete_document(result.document.id, owner_id=user)
        assert not (await b.retrieve("AURA-4419 runbook", owner_id=user)).chunks
        assert b.lexical_size == 0


class TestChunksForIsActiveOnly:
    async def test_superseded_and_tombstoned_versions_resolve_to_nothing(
        self, knowledge, documents, user
    ) -> None:
        await knowledge.ingest(content="Version one text.", title="Doc",
                               document_id=DocumentId("doc"), owner_id=user)
        await knowledge.ingest(content="Version two text.", title="Doc",
                               document_id=DocumentId("doc"), owner_id=user)
        assert await documents.chunks_for(DocumentId("doc"), 1) == []
        assert await documents.chunks_for(DocumentId("doc"), 2)

        await knowledge.delete_document(DocumentId("doc"), owner_id=user)
        assert await documents.chunks_for(DocumentId("doc"), 2) == []

    async def test_a_new_version_still_retires_the_old_chunks(
        self, knowledge, vectors, index_manager, user
    ) -> None:
        """Retirement reads the old chunks before superseding them; with an active-only
        ``chunks_for`` a read after the supersede would find nothing and orphan the vectors."""
        await knowledge.ingest(content="Version one text.", title="Doc",
                               document_id=DocumentId("doc"), owner_id=user)
        await knowledge.ingest(content="Version two text.", title="Doc",
                               document_id=DocumentId("doc"), owner_id=user)
        identity = await index_manager.resolve_for_read()
        assert await vectors.count(identity) == 1
        assert knowledge.lexical_size == 1
