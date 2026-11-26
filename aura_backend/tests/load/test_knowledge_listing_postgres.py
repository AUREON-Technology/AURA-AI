"""INT-111 and F14 against a real Postgres: a user's document list, delete and erasure.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a server whose role can
create databases. Each test runs in its own database built by ``alembic upgrade head``.

INT-111: ``PostgresDocumentStore.list_active(owner)`` returned the owner's documents *plus* every
global one, while the in-memory store returned only the owner's. On Postgres the knowledge list
therefore showed global documents; deleting one did nothing (not the caller's) and it was listed
again. The listing now matches the in-memory store, which the same assertions run against.

F14: delete and erasure on the Postgres store after the embedder changed, and a dense hit for a
tombstoned document checked against the real ``active_chunks`` query.
"""

from __future__ import annotations

import os

import pytest

from aura_core.adapters.memory_stores import InMemoryDocumentStore, InMemoryVectorStore
from aura_core.adapters.postgres_store import PostgresDocumentStore
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.knowledge.index_manager import IndexManager
from aura_core.llm.embeddings import HashEmbeddingProvider
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
ALICE, BOB = UserId("alice-sub"), UserId("bob-sub")


@pytest.fixture(params=["postgres", "memory"])
async def documents(request):
    if request.param == "memory":
        yield InMemoryDocumentStore()
        return
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "kl_test") as url:
        store = PostgresDocumentStore(url)
        try:
            yield store
        finally:
            await store.close()


def _engine(documents, vectors, *, dimension: int = 128) -> KnowledgeEngine:
    embedder = HashEmbeddingProvider(dimension=dimension)
    return KnowledgeEngine(
        documents=documents, vectors=vectors, embedder=embedder,
        index_manager=IndexManager(vectors=vectors, documents=documents, embedder=embedder,
                                   alias="kl"),
        score_threshold=0.0,
    )


async def test_an_owner_listing_has_only_the_owners_documents(documents) -> None:
    engine = _engine(documents, InMemoryVectorStore())
    await engine.ingest(title="global", content="# Global\n\nCompany holidays.",
                        document_id=DocumentId("global-doc"), owner_id=None)
    await engine.ingest(title="mine", content="# Mine\n\nAlice's notes.",
                        document_id=DocumentId("alice-doc"), owner_id=ALICE)
    await engine.ingest(title="bob", content="# Bob\n\nBob's notes.",
                        document_id=DocumentId("bob-doc"), owner_id=BOB)

    assert [d.id.value for d in await documents.list_active(owner_id=ALICE)] == ["alice-doc"]
    assert [d.id.value for d in await documents.list_active(owner_id=None)] == ["global-doc"]


async def test_deleting_a_global_document_is_refused_and_it_stays(documents) -> None:
    engine = _engine(documents, InMemoryVectorStore())
    await engine.ingest(title="global", content="# Global\n\nCompany holidays.",
                        document_id=DocumentId("global-doc"), owner_id=None)

    assert await engine.delete_document(DocumentId("global-doc"), owner_id=ALICE) == 0
    assert [d.id.value for d in await documents.list_active(owner_id=None)] == ["global-doc"]
    assert await documents.list_active(owner_id=ALICE) == []


async def test_delete_and_erase_after_an_embedder_change(documents) -> None:
    vectors = InMemoryVectorStore()
    model_a = _engine(documents, vectors)
    model_b = _engine(documents, vectors, dimension=64)
    await model_a.ingest(title="notes", content="# Notes\n\nParking permits are renewed in May.",
                         document_id=DocumentId("notes"), owner_id=ALICE)
    await model_a.ingest(title="plan", content="# Plan\n\nThe offsite is in June.",
                         document_id=DocumentId("plan"), owner_id=ALICE)
    index_a = await model_a._indexes.active_identity()

    assert await model_b.delete_document(DocumentId("notes"), owner_id=ALICE) > 0
    assert [d.id.value for d in await documents.list_active(owner_id=ALICE)] == ["plan"]

    assert await model_b.erase_owner(ALICE) >= 2
    assert await documents.list_active(owner_id=ALICE) == []
    assert await vectors.count(index_a, owner_id=ALICE) == 0


async def test_a_dense_hit_for_a_tombstoned_document_is_dropped(documents) -> None:
    engine = _engine(documents, InMemoryVectorStore())
    await engine.ingest(title="notes", content="# Notes\n\nParking permits are renewed in May.",
                        document_id=DocumentId("notes"), owner_id=ALICE)
    assert (await engine.retrieve("parking permits renewed", owner_id=ALICE)).chunks

    await documents.tombstone(DocumentId("notes"), owner_id=ALICE)  # vectors left behind

    assert not (await engine.retrieve("parking permits renewed", owner_id=ALICE)).chunks


async def test_owner_stats_count_active_documents_bytes_and_pins_the_same_on_both_stores(documents) -> None:
    """The corpus summary (``GET /api/v3/knowledge``): one owner's active documents, their UTF-8
    size (multi-byte text counts its bytes, not its characters) and pins; never another owner's,
    never a deleted document's; global documents are their own, separate total."""
    engine = _engine(documents, InMemoryVectorStore())
    multibyte = "héllo wörld — ünïcode " * 5
    await engine.ingest(content="a" * 100, title="a1", owner_id=ALICE)
    second = await engine.ingest(content=multibyte, title="a2", owner_id=ALICE)
    gone = await engine.ingest(content="z" * 40, title="gone", owner_id=ALICE)
    await engine.ingest(content="b" * 300, title="b1", owner_id=BOB)
    await engine.ingest(content="g" * 25, title="global", owner_id=None)
    await documents.set_cag_pin(second.document.id, owner_id=ALICE, pinned=True)
    await engine.delete_document(gone.document.id, owner_id=ALICE)

    mine = await documents.owner_stats(owner_id=ALICE)
    assert (mine.documents, mine.pinned) == (2, 1)
    assert mine.content_bytes == 100 + len(multibyte.strip().encode("utf-8")) > 100 + len(multibyte.strip())
    assert (await documents.owner_stats(owner_id=BOB)).content_bytes == 300
    assert (await documents.owner_stats(owner_id=None)).content_bytes == 25
    empty = await documents.owner_stats(owner_id=UserId("nobody"))
    assert (empty.documents, empty.content_bytes, empty.pinned) == (0, 0, 0)
