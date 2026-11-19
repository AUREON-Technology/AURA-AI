"""Deleting and erasing knowledge must not depend on the embedder matching the index (F14), and a
deleted document must never come back from a stale vector index.

``delete_document`` resolved the index through ``resolve_for_read()``, which raises when no alias
is published or the alias was built by a different embedding model -- so after an embedder
change nobody could delete a document, and ``erase_owner`` (the admin "forget user" path) failed
half-way. And dense hits were trusted as live: only lexical hits were checked against the
document store, so a vector point the delete did not reach brought the document back.
"""

from __future__ import annotations

import pytest

from aura_core.adapters import InMemoryVectorStore
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.knowledge import IndexManager, KnowledgeEngine, RuleEntityExtractor
from aura_core.llm import HashEmbeddingProvider


def _engine(*, vectors, documents, embedder, safety, graph, metrics) -> KnowledgeEngine:
    return KnowledgeEngine(
        documents=documents, vectors=vectors, embedder=embedder,
        index_manager=IndexManager(
            vectors=vectors, documents=documents, embedder=embedder,
            alias="test_knowledge", metrics=metrics,
        ),
        safety=safety, graph=graph, extractor=RuleEntityExtractor(), metrics=metrics,
        score_threshold=0.0,
    )


@pytest.fixture
def model_a(vectors, documents, embedder, safety, graph, metrics) -> KnowledgeEngine:
    return _engine(vectors=vectors, documents=documents, embedder=embedder, safety=safety,
                   graph=graph, metrics=metrics)


@pytest.fixture
def model_b(vectors, documents, safety, graph, metrics) -> KnowledgeEngine:
    """The same stores, after the embedder changed: the alias still points at model A's index."""
    return _engine(vectors=vectors, documents=documents,
                   embedder=HashEmbeddingProvider(dimension=64), safety=safety, graph=graph,
                   metrics=metrics)


class TestDeleteAfterAnEmbedderChange:
    async def test_a_document_can_be_deleted_and_never_comes_back(
        self, model_a, model_b, vectors, documents, user, handbook
    ) -> None:
        await model_a.ingest(content=handbook, title="Handbook",
                             document_id=DocumentId("handbook"), owner_id=user)
        index_a = await model_a._indexes.active_identity()
        assert await vectors.count(index_a, owner_id=user) > 0

        removed = await model_b.delete_document(DocumentId("handbook"), owner_id=user)

        assert removed > 0
        assert await documents.get(DocumentId("handbook")) is None
        assert await documents.list_active(owner_id=user) == []
        assert await vectors.count(index_a, owner_id=user) == 0
        # Once the alias is rebuilt or reverted, the document is still gone.
        assert not (await model_a.retrieve("annual leave", owner_id=user)).chunks

    async def test_erase_owner_completes(self, model_a, model_b, vectors, documents, user,
                                         handbook) -> None:
        await model_a.ingest(content=handbook, title="Handbook",
                             document_id=DocumentId("handbook"), owner_id=user)
        await model_a.ingest(content="Second document about parking permits.", title="Parking",
                             document_id=DocumentId("parking"), owner_id=user)
        index_a = await model_a._indexes.active_identity()

        assert await model_b.erase_owner(user) >= 2

        assert await documents.list_active(owner_id=user) == []
        assert await vectors.count(index_a, owner_id=user) == 0

    async def test_delete_works_with_no_published_index(
        self, vectors, documents, embedder, safety, graph, metrics, user
    ) -> None:
        """A document whose ingestion never published an index (or whose alias was removed)
        must still be deletable."""
        engine = _engine(vectors=vectors, documents=documents, embedder=embedder,
                         safety=safety, graph=graph, metrics=metrics)
        await engine.ingest(content="A short note about the fire drill.", title="Note",
                            document_id=DocumentId("note"), owner_id=user)
        vectors._aliases.clear()  # noqa: SLF001 - the "no alias published" state

        assert await engine.delete_document(DocumentId("note"), owner_id=user) > 0
        assert await documents.get(DocumentId("note")) is None

    async def test_another_users_index_points_are_untouched(
        self, model_a, model_b, vectors, user, handbook
    ) -> None:
        other = UserId("bob")
        await model_a.ingest(content=handbook, title="Handbook",
                             document_id=DocumentId("handbook"), owner_id=user)
        await model_a.ingest(content=handbook + "\n\nBob's copy.", title="Handbook",
                             document_id=DocumentId("bob-handbook"), owner_id=other)
        index_a = await model_a._indexes.active_identity()
        bob_points = await vectors.count(index_a, owner_id=other)

        await model_b.erase_owner(user)

        assert await vectors.count(index_a, owner_id=other) == bob_points


class _UndeletableVectors(InMemoryVectorStore):
    """A vector store whose deletes fail -- or a replica that has not caught up yet."""

    fail = False

    async def delete(self, identity, *, point_ids):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("vector store unavailable")
        return await super().delete(identity, point_ids=point_ids)


class TestDenseHitsAreLivenessChecked:
    async def test_a_tombstoned_document_is_not_returned_from_a_stale_index(
        self, model_a, documents, user, handbook
    ) -> None:
        await model_a.ingest(content=handbook, title="Handbook",
                             document_id=DocumentId("handbook"), owner_id=user)
        assert (await model_a.retrieve("annual leave", owner_id=user)).chunks

        # The row is tombstoned, but every vector point is still in the index.
        await documents.tombstone(DocumentId("handbook"), owner_id=user)

        result = await model_a.retrieve("annual leave", owner_id=user)
        assert not result.chunks
        assert not result.below_threshold

    async def test_a_failed_vector_delete_still_hides_the_document(
        self, documents, embedder, safety, graph, metrics, user, handbook
    ) -> None:
        vectors = _UndeletableVectors()
        engine = _engine(vectors=vectors, documents=documents, embedder=embedder,
                         safety=safety, graph=graph, metrics=metrics)
        await engine.ingest(content=handbook, title="Handbook",
                            document_id=DocumentId("handbook"), owner_id=user)
        vectors.fail = True

        assert await engine.delete_document(DocumentId("handbook"), owner_id=user) > 0

        assert await documents.list_active(owner_id=user) == []
        assert not (await engine.retrieve("annual leave", owner_id=user)).chunks
