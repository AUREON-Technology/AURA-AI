"""Is document ingestion idempotent? (roadmap P1.4)

The question has several distinct halves, and each is pinned separately because each failed (or
held) for a different reason:

* **Sequential re-ingest of identical content** -- held already: ``KnowledgeEngine.ingest`` checks
  the content hash before doing anything.
* **A retry after partial completion** (vectors upserted, then the document write failed) -- holds
  *only if* the retry lands on the same document id, because chunk ids are
  ``{document_id}:v{version}:{index}`` and the vector write is an upsert. The derived id used
  Python's ``hash(title)``, which is salted per process (``PYTHONHASHSEED``), so a retry in another
  gateway worker, a Celery worker, or after a restart got a *different* id and left the first
  attempt's vectors orphaned -- still searchable, duplicating every chunk.
* **Two users uploading the same title** -- the derived id was not owner-scoped, so the second
  user's upload superseded the first user's document (and the result stayed owned by the first
  user, so user A could read user B's content).
* **Delete, then re-upload the same file** -- the in-memory ``get_by_hash`` returned tombstoned
  and superseded versions (the Postgres adapter filters ``status = 'active'``), so the re-upload
  was skipped as "unchanged" and never indexed.
* **Concurrent duplicate submissions** (a double tap, a client retry racing the original) --
  both passed the hash check before either wrote, so both indexed.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from aura_core.adapters.memory_stores import InMemoryDocumentStore
from aura_core.domain.errors import ConflictError, DependencyUnavailableError
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.knowledge import IndexManager, KnowledgeEngine, RuleEntityExtractor
from aura_core.llm import HashEmbeddingProvider

BACKEND = Path(__file__).resolve().parents[2]

ALICE = UserId("alice")
BOB = UserId("bob")


def _engine(*, documents, vectors, embedder, safety, graph, metrics) -> KnowledgeEngine:
    return KnowledgeEngine(
        documents=documents, vectors=vectors, embedder=embedder,
        index_manager=IndexManager(
            vectors=vectors, documents=documents, embedder=embedder,
            alias="test_knowledge", metrics=metrics,
        ),
        safety=safety, graph=graph, extractor=RuleEntityExtractor(), metrics=metrics,
        score_threshold=0.15,
    )


async def _vector_count(index_manager: IndexManager, vectors, owner: UserId) -> int:
    identity = await index_manager.resolve_for_read()
    return await vectors.count(identity, owner_id=owner)


class _SlowEmbedder(HashEmbeddingProvider):
    """A real embedder that yields to the event loop, the way every networked one does. Without a
    suspension point concurrent ingests run back to back and no race is observable."""

    async def embed(self, texts, *, for_query: bool = False):  # noqa: ANN001
        await asyncio.sleep(0.01)
        return await super().embed(texts, for_query=for_query)


class _PutFailsOnce(InMemoryDocumentStore):
    """The document write fails once -- after the vectors were already upserted -- like a Postgres
    blip mid-ingest. That is the "partial completion" a Celery retry or client retry follows."""

    def __init__(self) -> None:
        super().__init__()
        self.failures_left = 1

    async def put(self, document, chunks) -> None:  # noqa: ANN001
        if self.failures_left:
            self.failures_left -= 1
            raise DependencyUnavailableError("postgres blip", dependency="postgres")
        await super().put(document, chunks)


class TestSequentialReingest:
    async def test_identical_content_is_indexed_once(
        self, knowledge, index_manager, vectors, handbook
    ) -> None:
        first = await knowledge.ingest(content=handbook, title="Handbook", owner_id=ALICE)
        before = await _vector_count(index_manager, vectors, ALICE)
        second = await knowledge.ingest(content=handbook, title="Handbook", owner_id=ALICE)

        assert first.created and not second.created
        assert second.skipped_reason == "unchanged_content"
        assert second.document.id == first.document.id
        assert await _vector_count(index_manager, vectors, ALICE) == before == first.chunks

    async def test_the_same_content_under_another_title_is_not_indexed_twice(
        self, knowledge, index_manager, vectors, handbook
    ) -> None:
        first = await knowledge.ingest(content=handbook, title="Handbook", owner_id=ALICE)
        again = await knowledge.ingest(content=handbook, title="handbook (1).pdf", owner_id=ALICE)
        assert again.skipped_reason == "unchanged_content"
        assert again.document.id == first.document.id
        assert await _vector_count(index_manager, vectors, ALICE) == first.chunks


class TestRetryAfterPartialFailure:
    async def test_a_retry_after_the_document_write_failed_leaves_no_orphan_vectors(
        self, vectors, safety, graph, metrics, handbook
    ) -> None:
        documents = _PutFailsOnce()
        embedder = HashEmbeddingProvider(dimension=128)
        engine = _engine(
            documents=documents, vectors=vectors, embedder=embedder,
            safety=safety, graph=graph, metrics=metrics,
        )
        with pytest.raises(DependencyUnavailableError):
            await engine.ingest(content=handbook, title="Handbook", owner_id=ALICE)
        assert await _vector_count(engine._indexes, vectors, ALICE) > 0  # the partial write

        # The retry runs on a *fresh engine* over the same stores -- what a Celery retry on
        # another worker, or a client retry routed to another gateway process, looks like.
        retry_engine = _engine(
            documents=documents, vectors=vectors, embedder=embedder,
            safety=safety, graph=graph, metrics=metrics,
        )
        result = await retry_engine.ingest(content=handbook, title="Handbook", owner_id=ALICE)

        assert result.created
        assert await _vector_count(retry_engine._indexes, vectors, ALICE) == result.chunks

    def test_the_derived_document_id_does_not_depend_on_the_process_hash_seed(self) -> None:
        """The cross-process half of the retry guarantee. ``hash(str)`` is salted per interpreter,
        so this runs the derivation in two interpreters with different seeds."""
        script = textwrap.dedent(
            """
            import asyncio
            from aura_core.adapters.memory_stores import InMemoryDocumentStore, InMemoryVectorStore
            from aura_core.domain.identifiers import UserId
            from aura_core.knowledge import IndexManager, KnowledgeEngine
            from aura_core.llm import HashEmbeddingProvider

            async def main():
                documents, vectors = InMemoryDocumentStore(), InMemoryVectorStore()
                embedder = HashEmbeddingProvider(dimension=64)
                engine = KnowledgeEngine(
                    documents=documents, vectors=vectors, embedder=embedder,
                    index_manager=IndexManager(vectors=vectors, documents=documents,
                                               embedder=embedder, alias="k"),
                )
                result = await engine.ingest(
                    content="The expense cap is 250 dollars.", title="Quarterly report",
                    owner_id=UserId("alice"),
                )
                print(result.document.id.value)

            asyncio.run(main())
            """
        )
        ids = set()
        for seed in ("1", "2", "3"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "AURA_LOG_LEVEL": "ERROR"}
            out = subprocess.run(
                [sys.executable, "-c", script], cwd=BACKEND, env=env,
                capture_output=True, text=True, timeout=120, check=True,
            )
            ids.add(out.stdout.strip().splitlines()[-1])
        assert len(ids) == 1, f"derived document id varies with PYTHONHASHSEED: {ids}"


class TestOwnership:
    async def test_the_same_title_from_two_users_creates_two_documents(
        self, knowledge
    ) -> None:
        alice = await knowledge.ingest(
            content="Alice's private notes about the merger.", title="Notes", owner_id=ALICE,
        )
        bob = await knowledge.ingest(
            content="Bob's grocery list: eggs, milk.", title="Notes", owner_id=BOB,
        )

        assert alice.document.id != bob.document.id
        assert bob.document.owner_id == BOB
        assert bob.document.version == 1
        # Alice's document is untouched -- not superseded by Bob's upload.
        alice_hits = await knowledge.retrieve("private notes merger", owner_id=ALICE)
        assert any("merger" in c.chunk.content for c in alice_hits.chunks)
        leaked = await knowledge.retrieve("grocery list eggs milk", owner_id=ALICE)
        assert not any("grocery" in c.chunk.content for c in leaked.chunks)

    async def test_an_explicit_document_id_owned_by_someone_else_is_refused(
        self, knowledge
    ) -> None:
        await knowledge.ingest(
            content="Alice's contract terms.", title="Contract",
            document_id=DocumentId("contract"), owner_id=ALICE,
        )
        with pytest.raises(ConflictError):
            await knowledge.ingest(
                content="Bob overwrites it.", title="Contract",
                document_id=DocumentId("contract"), owner_id=BOB,
            )
        hits = await knowledge.retrieve("contract terms", owner_id=ALICE)
        assert any("Alice's contract" in c.chunk.content for c in hits.chunks)

    async def test_deleting_someone_elses_document_removes_nothing(
        self, knowledge, index_manager, vectors
    ) -> None:
        result = await knowledge.ingest(
            content="Alice's contract terms.", title="Contract", owner_id=ALICE,
        )
        before = await _vector_count(index_manager, vectors, ALICE)

        removed = await knowledge.delete_document(result.document.id, owner_id=BOB)

        assert removed == 0
        assert await _vector_count(index_manager, vectors, ALICE) == before
        hits = await knowledge.retrieve("contract terms", owner_id=ALICE)
        assert hits.chunks


class TestReingestAfterLifecycleChanges:
    async def test_a_deleted_document_can_be_uploaded_again(
        self, knowledge, index_manager, vectors, handbook
    ) -> None:
        first = await knowledge.ingest(content=handbook, title="Handbook", owner_id=ALICE)
        await knowledge.delete_document(first.document.id, owner_id=ALICE)
        assert await _vector_count(index_manager, vectors, ALICE) == 0

        again = await knowledge.ingest(content=handbook, title="Handbook", owner_id=ALICE)

        assert again.created, again.skipped_reason
        assert await _vector_count(index_manager, vectors, ALICE) == again.chunks
        hits = await knowledge.retrieve("annual leave", owner_id=ALICE)
        assert hits.chunks

    async def test_reverting_to_earlier_content_creates_a_new_version(
        self, knowledge
    ) -> None:
        doc = DocumentId("policy")
        await knowledge.ingest(
            content="The expense cap is 250 dollars per claim.", title="Policy",
            document_id=doc, owner_id=ALICE,
        )
        await knowledge.ingest(
            content="The expense cap is 500 dollars per claim.", title="Policy",
            document_id=doc, owner_id=ALICE,
        )
        reverted = await knowledge.ingest(
            content="The expense cap is 250 dollars per claim.", title="Policy",
            document_id=doc, owner_id=ALICE,
        )

        assert reverted.created and reverted.document.version == 3
        hits = await knowledge.retrieve("expense cap dollars per claim", owner_id=ALICE)
        contents = " ".join(c.chunk.content for c in hits.chunks)
        assert "250" in contents and "500" not in contents


class TestConcurrentDuplicates:
    async def test_concurrent_identical_submissions_index_once(
        self, documents, vectors, safety, graph, metrics, handbook
    ) -> None:
        engine = _engine(
            documents=documents, vectors=vectors, embedder=_SlowEmbedder(dimension=128),
            safety=safety, graph=graph, metrics=metrics,
        )
        # Different titles on purpose: a double-tapped upload of a renamed copy has a different
        # derived id, so only the content-hash check can catch it.
        results = await asyncio.gather(*(
            engine.ingest(content=handbook, title=f"Handbook copy {i}", owner_id=ALICE)
            for i in range(5)
        ))

        created = [r for r in results if r.created]
        assert len(created) == 1
        assert all(r.skipped_reason == "unchanged_content" for r in results if not r.created)
        assert {r.document.id for r in results} == {created[0].document.id}
        assert await _vector_count(engine._indexes, vectors, ALICE) == created[0].chunks
        assert len(await documents.list_active(owner_id=ALICE)) == 1

    async def test_concurrent_new_versions_of_one_document_do_not_collide(
        self, documents, vectors, safety, graph, metrics
    ) -> None:
        engine = _engine(
            documents=documents, vectors=vectors, embedder=_SlowEmbedder(dimension=128),
            safety=safety, graph=graph, metrics=metrics,
        )
        doc = DocumentId("policy")
        await engine.ingest(
            content="Version one of the policy.", title="Policy", document_id=doc, owner_id=ALICE,
        )
        results = await asyncio.gather(*(
            engine.ingest(
                content=f"Revision {i} of the policy text.", title="Policy",
                document_id=doc, owner_id=ALICE,
            )
            for i in range(4)
        ))

        versions = sorted(r.document.version for r in results)
        assert versions == [2, 3, 4, 5]  # each revision got its own version, none overwritten
        active = await documents.get(doc)
        assert active.version == 5
        # Exactly one version's chunks remain indexed.
        identity = await engine._indexes.resolve_for_read()
        live = await documents.chunks_for(doc, active.version)
        assert await vectors.count(identity, owner_id=ALICE) == len(live)
