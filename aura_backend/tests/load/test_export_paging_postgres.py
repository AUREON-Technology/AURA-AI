"""The lists ``/me/export`` pages through, against a real Postgres.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. The export reads a person's
memories and documents a page at a time with ``LIMIT/OFFSET``. Paging is only complete if the order is
total: both queries sorted on a timestamp alone, so rows sharing one (a batch ingested at the same
instant) could be repeated on one page and missing from the next. They now break ties on ``id``.
Here every row shares its timestamp, the pages are small, and the export must still hold each row
exactly once.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timezone

import pytest

from aura_core.adapters.memory_stores import InMemoryVectorStore
from aura_core.adapters.postgres_store import PostgresDocumentStore, PostgresMemoryStore, PostgresPool
from aura_core.api import export as export_module
from aura_core.domain.identifiers import DocumentId, MemoryId, UserId
from aura_core.domain.memory import MemoryKind, MemoryRecord, Provenance
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.knowledge.index_manager import IndexManager
from aura_core.llm.embeddings import HashEmbeddingProvider
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
ALICE = UserId("alice")
# Inserted out of order on purpose: the order a database hands them back is not the id order.
# Enough rows that the planner's top-N sort has real ties to break: a handful come back in a
# stable order whatever the query says. Inserted in a shuffled order: it is not the id order.
IDS = [f"m-{n:03d}" for n in (random.Random(7).sample(range(1, 301), 300))]


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "export_paging") as url:
        yield url


async def test_memories_are_paged_completely_when_they_all_share_a_timestamp(database_url, monkeypatch) -> None:
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    try:
        store = PostgresMemoryStore(database_url, pool=pool)
        for mid in IDS:
            await store.put(MemoryRecord(
                id=MemoryId(mid), kind=MemoryKind.SEMANTIC, owner_id=ALICE, content=f"fact {mid}",
                provenance=Provenance(source="t", recorded_at=T0), subject=mid, confidence=0.8,
                created_at=T0, last_accessed_at=T0,
            ))
        monkeypatch.setattr(export_module, "_PAGE", 7)
        rows, truncated = await export_module.collect(lambda **page: store.list_for_owner(ALICE, **page), 1000)
        assert sorted(r.id.value for r in rows) == sorted(IDS), "every memory exactly once"
        assert truncated is False

        rows, truncated = await export_module.collect(lambda **page: store.list_for_owner(ALICE, **page), 300)
        assert len(rows) == 300 and truncated is False, "exactly the cap is not a cut"
        rows, truncated = await export_module.collect(lambda **page: store.list_for_owner(ALICE, **page), 299)
        assert len(rows) == 299 and len({r.id.value for r in rows}) == 299 and truncated is True
    finally:
        await pool.close()


async def test_documents_are_paged_completely_when_they_all_share_a_timestamp(database_url, monkeypatch) -> None:
    import asyncpg

    documents = PostgresDocumentStore(database_url)
    try:
        vectors = InMemoryVectorStore()
        embedder = HashEmbeddingProvider(dimension=128)
        engine = KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=IndexManager(vectors=vectors, documents=documents, embedder=embedder, alias="exp"),
            score_threshold=0.0,
        )
        wanted = [f"d-{n:03d}" for n in random.Random(11).sample(range(1, 151), 150)]
        for did in wanted:
            await engine.ingest(title=did, content=f"# {did}\n\nBody of {did}.", document_id=DocumentId(did), owner_id=ALICE)
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute("UPDATE aura_documents SET updated_at = $1", T0)
            tied = await conn.fetchval("SELECT count(DISTINCT updated_at) FROM aura_documents")
        finally:
            await conn.close()
        assert tied == 1, "the setup must really tie every row"

        monkeypatch.setattr(export_module, "_PAGE", 7)
        rows, truncated = await export_module.collect(lambda **page: documents.list_active(owner_id=ALICE, **page), 1000)
        assert sorted(d.id.value for d in rows) == sorted(wanted), "every document exactly once"
        assert truncated is False
    finally:
        await documents.close()
