"""Cross-worker lexical convergence against a real Postgres (audit F-14).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN (see
``test_adaptation_postgres.py``).

Each "worker" here is its own ``KnowledgeEngine`` with its **own** ``PostgresDocumentStore`` and
connection pool over one database -- what two uvicorn workers are -- and worker B gets an empty
vector store of its own, so everything B returns came through its lexical index. That isolates
the property under test: an upload, a new version, a delete and an erasure served by worker A
reach B's BM25 index through ``aura_corpus_revisions``, with no restart of B.

The in-memory suite (``tests/unit/test_knowledge_lifecycle.py``) covers the same behaviour on
the reference adapter; this file is what shows the SQL -- the revision bumps inside their
transactions, the active-chunk listing and fetch -- does what that adapter does.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adapters.memory_stores import InMemoryVectorStore
from aura_core.adapters.postgres_store import PostgresDocumentStore
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.knowledge.index_manager import IndexManager
from aura_core.llm.embeddings import HashEmbeddingProvider

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
ALICE, BOB = UserId("alice-sub"), UserId("bob-sub")
RUNBOOK = "The AURA-4419 escalation runbook says to page the on-call SRE."


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"lexical_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield urlunsplit(urlsplit(_URL)._replace(path=f"/{name}"))
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


def _worker(database_url: str) -> tuple[KnowledgeEngine, PostgresDocumentStore]:
    documents = PostgresDocumentStore(database_url)
    vectors = InMemoryVectorStore()
    embedder = HashEmbeddingProvider(dimension=64)
    engine = KnowledgeEngine(
        documents=documents, vectors=vectors, embedder=embedder,
        index_manager=IndexManager(
            vectors=vectors, documents=documents, embedder=embedder, alias="lexical"
        ),
        score_threshold=0.0,
    )
    return engine, documents


async def _started(database_url: str) -> tuple[KnowledgeEngine, PostgresDocumentStore]:
    """A worker after `Container.startup`: its vector index exists and its BM25 index is
    rebuilt from the store."""
    engine, documents = _worker(database_url)
    await engine._indexes.ensure_ready()  # noqa: SLF001
    await engine.rebuild_lexical_index()
    return engine, documents


def _found(result, document_id: str) -> list:
    return [
        c for c in result.chunks
        if c.chunk.document_id.value == document_id and c.lexical_rank is not None
    ]


async def test_writes_on_one_worker_converge_into_another_workers_lexical_index(
    database_url,
) -> None:
    worker_a, store_a = await _started(database_url)
    worker_b, store_b = await _started(database_url)
    try:
        assert worker_b.lexical_size == 0

        # An upload on A is found lexically on B.
        await worker_a.ingest(
            content=RUNBOOK, title="Runbook", document_id=DocumentId("runbook"), owner_id=ALICE
        )
        assert _found(await worker_b.retrieve("AURA-4419 runbook", owner_id=ALICE), "runbook")
        # ...and never by another tenant.
        assert not (await worker_b.retrieve("AURA-4419 runbook", owner_id=BOB)).chunks

        # A global upload on A is found by everyone on B.
        await worker_a.ingest(
            content="Company holidays: the AURA-77 office closes on Fridays.", title="Holidays",
            document_id=DocumentId("holidays"), owner_id=None,
        )
        for reader in (ALICE, BOB):
            assert _found(await worker_b.retrieve("AURA-77 holidays", owner_id=reader), "holidays")

        # A new version on A replaces the old one in B's index.
        await worker_a.ingest(
            content="The expense cap is 250 dollars per claim.", title="Policy",
            document_id=DocumentId("policy"), owner_id=ALICE,
        )
        await worker_b.retrieve("expense cap", owner_id=ALICE)
        await worker_a.ingest(
            content="The expense cap is 500 dollars per claim.", title="Policy",
            document_id=DocumentId("policy"), owner_id=ALICE,
        )
        hits = _found(await worker_b.retrieve("expense cap dollars", owner_id=ALICE), "policy")
        assert hits and {c.chunk.document_version for c in hits} == {2}
        assert worker_b.lexical_size == worker_a.lexical_size

        # A delete on A leaves B's index on B's next search of that owner, whatever the query.
        await worker_a.delete_document(DocumentId("runbook"), owner_id=ALICE)
        before = worker_b.lexical_size
        await worker_b.retrieve("unrelated words", owner_id=ALICE)
        assert worker_b.lexical_size == before - 1
        assert not _found(await worker_b.retrieve("AURA-4419 runbook", owner_id=ALICE), "runbook")

        # An erasure on A empties that owner on B and touches nobody else.
        await worker_a.ingest(
            content=RUNBOOK, title="Runbook", document_id=DocumentId("rb-bob"), owner_id=BOB
        )
        await worker_a.erase_owner(ALICE)
        assert not (await worker_b.retrieve("expense cap dollars", owner_id=ALICE)).chunks
        assert _found(await worker_b.retrieve("AURA-4419 runbook", owner_id=BOB), "rb-bob")
        assert worker_b.lexical_size == worker_a.lexical_size

        revisions = await store_b.corpus_revisions([ALICE.value, BOB.value, "global"])
        assert set(revisions) == {ALICE.value, BOB.value, "global"}
        assert all(r > 0 for r in revisions.values())
    finally:
        await store_a._pool.close()  # noqa: SLF001 -- release the pool before the DB is dropped
        await store_b._pool.close()  # noqa: SLF001


async def test_the_revision_bump_commits_with_the_change(database_url) -> None:
    """A reader that sees a new revision also sees the change it stands for: the bump is in
    the writing transaction, so a failed write moves nothing."""
    import asyncpg

    _, store = _worker(database_url)
    try:
        from aura_core.domain.knowledge import Chunk, Document

        doc = Document(id=DocumentId("d1"), version=1, title="T", content="body", owner_id=ALICE)
        chunk = Chunk(
            id=Chunk.build_id(doc.id, 1, 0), document_id=doc.id, document_version=1, index=0,
            content="body", owner_id=ALICE,
        )
        await store.put(doc, [chunk])
        assert await store.corpus_revisions([ALICE.value]) == {ALICE.value: 1}
        assert await store.active_chunk_ids(ALICE.value) == [chunk.id.value]

        # Superseding a version that is not active changes nothing, so bumps nothing.
        await store.supersede(doc.id, old_version=9, new_version=10)
        assert await store.corpus_revisions([ALICE.value]) == {ALICE.value: 1}
        await store.supersede(doc.id, old_version=1, new_version=2)
        assert await store.corpus_revisions([ALICE.value]) == {ALICE.value: 2}
        assert await store.active_chunk_ids(ALICE.value) == []
        assert await store.active_chunks([chunk.id.value]) == []

        # A put that fails mid-transaction rolls its bump back with it.
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute(
                "ALTER TABLE aura_chunks ADD CONSTRAINT no_boom CHECK (content <> 'boom')"
            )
        finally:
            await conn.close()
        bad = Chunk(
            id=Chunk.build_id(DocumentId("d2"), 1, 0), document_id=DocumentId("d2"),
            document_version=1, index=0, content="boom", owner_id=ALICE,
        )
        with pytest.raises(Exception):
            await store.put(
                Document(id=DocumentId("d2"), version=1, title="T", content="boom", owner_id=ALICE),
                [bad],
            )
        assert await store.corpus_revisions([ALICE.value]) == {ALICE.value: 2}
    finally:
        await store._pool.close()  # noqa: SLF001
