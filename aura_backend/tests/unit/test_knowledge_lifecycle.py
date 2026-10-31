"""Knowledge ingestion, versioning, and hybrid-retrieval degradation.

The behaviours worth pinning are the ones that produce a *silently wrong* corpus rather than a
visible error: chunks from a superseded version left in the index, a partially-embedded document
recorded as ingested, and a lexical index that is empty after a restart so hybrid retrieval
quietly becomes dense-only.
"""

from __future__ import annotations

import pytest

from aura_core.domain.errors import EmbeddingError
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.knowledge import KnowledgeEngine, RuleEntityExtractor
from aura_core.knowledge.engine import KnowledgeEngine as Engine


class TestIngestion:
    async def test_a_document_is_chunked_indexed_and_retrievable(
        self, knowledge, user, handbook
    ) -> None:
        result = await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        assert result.chunks > 0
        assert knowledge.lexical_size == result.chunks

        retrieved = await knowledge.retrieve("annual leave", owner_id=user)
        assert retrieved.chunks

    async def test_re_ingesting_identical_content_is_a_no_op(
        self, knowledge, user, handbook
    ) -> None:
        """The idempotency key that keeps nightly refreshes from churning the index."""
        first = await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        second = await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        assert second.created is False
        assert second.chunks == 0
        assert second.skipped_reason
        assert second.document.version == first.document.version

    async def test_new_content_creates_a_new_version(self, knowledge, user, handbook) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        updated = await knowledge.ingest(
            content=handbook + "\n\n## Remote Work\nTwo days per week.",
            title="Handbook", document_id=DocumentId("handbook"), owner_id=user,
        )
        assert updated.document.version == 2
        assert updated.created is True

    async def test_superseded_chunks_are_removed_from_both_indexes(
        self, knowledge, user
    ) -> None:
        """Otherwise stale and fresh content mix in one result set with no way to tell which is
        which."""
        await knowledge.ingest(
            content="The expense cap is 250 dollars per claim.", title="Policy",
            document_id=DocumentId("policy"), owner_id=user,
        )
        await knowledge.ingest(
            content="The expense cap is 500 dollars per claim.", title="Policy",
            document_id=DocumentId("policy"), owner_id=user,
        )
        result = await knowledge.retrieve("expense cap dollars per claim", owner_id=user)
        contents = " ".join(c.chunk.content for c in result.chunks)

        assert "500" in contents
        assert "250" not in contents

    async def test_an_embedding_failure_aborts_the_ingestion(
        self, vectors, documents, index_manager, safety, graph, metrics, user, handbook
    ) -> None:
        """A partially-embedded document recorded as ingested is how a corpus silently becomes
        unsearchable."""
        from aura_core.llm import HashEmbeddingProvider

        class HalfBrokenEmbedder(HashEmbeddingProvider):
            def __init__(self) -> None:
                super().__init__(dimension=128)
                self.calls = 0

            @property
            def max_batch_size(self) -> int:
                return 2

            async def embed(self, texts, *, for_query: bool = False):  # noqa: ANN001
                self.calls += 1
                if self.calls > 1 and not for_query:
                    raise EmbeddingError("embedding backend degraded mid-document")
                return await super().embed(texts, for_query=for_query)

        embedder = HalfBrokenEmbedder()
        from aura_core.knowledge import IndexManager

        engine = KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=IndexManager(
                vectors=vectors, documents=documents, embedder=embedder,
                alias="test_knowledge", metrics=metrics,
            ),
            safety=safety, graph=graph, extractor=RuleEntityExtractor(), metrics=metrics,
            score_threshold=0.15,
        )
        with pytest.raises(EmbeddingError):
            await engine.ingest(
                content=handbook * 6, title="Handbook",
                document_id=DocumentId("handbook"), owner_id=user,
            )

    async def test_graph_extraction_failure_does_not_fail_the_ingestion(
        self, vectors, documents, embedder, index_manager, safety, graph, metrics, user, handbook
    ) -> None:
        """Extraction is an enhancement to the ingestion the user actually asked for."""

        class BrokenExtractor:
            name = "broken"

            async def extract(self, text, *, source=""):  # noqa: ANN001
                raise RuntimeError("extraction blew up")

        engine = KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=index_manager, safety=safety, graph=graph,
            extractor=BrokenExtractor(), metrics=metrics, score_threshold=0.15,
        )
        result = await engine.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        assert result.chunks > 0

    async def test_entities_reach_the_graph(self, knowledge, user, handbook, graph) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        assert graph.size()[0] > 0


class TestDeletion:
    async def test_deleting_removes_the_document_from_retrieval(
        self, knowledge, user, handbook
    ) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        removed = await knowledge.delete_document(DocumentId("handbook"), owner_id=user)

        assert removed > 0
        assert not (await knowledge.retrieve("annual leave", owner_id=user)).chunks
        assert knowledge.lexical_size == 0

    async def test_deleting_an_unknown_document_is_zero(self, knowledge, user) -> None:
        assert await knowledge.delete_document(DocumentId("nope"), owner_id=user) == 0

    async def test_deletion_is_owner_scoped(self, knowledge, user, handbook) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        removed = await knowledge.delete_document(
            DocumentId("handbook"), owner_id=UserId("mallory")
        )
        assert removed == 0


class TestRetrievalDegradation:
    async def test_dense_failure_degrades_to_lexical_rather_than_failing(
        self, vectors, documents, index_manager, safety, graph, metrics, user, handbook
    ) -> None:
        """A legitimate degradation: the fallback still searches the real corpus, unlike the
        legacy zero-vector fallback which searched nothing while appearing to work (TD-03)."""
        from aura_core.llm import HashEmbeddingProvider

        class QueryBrokenEmbedder(HashEmbeddingProvider):
            async def embed(self, texts, *, for_query: bool = False):  # noqa: ANN001
                if for_query:
                    raise EmbeddingError("embedding service is down")
                return await super().embed(texts, for_query=for_query)

        embedder = QueryBrokenEmbedder(dimension=128)
        from aura_core.knowledge import IndexManager

        engine = KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=IndexManager(
                vectors=vectors, documents=documents, embedder=embedder,
                alias="test_knowledge", metrics=metrics,
            ),
            safety=safety, graph=graph, extractor=RuleEntityExtractor(), metrics=metrics,
            score_threshold=0.0,
        )
        await engine.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        result = await engine.retrieve("AURA-4419", owner_id=user)
        assert result.chunks  # lexical still finds the exact identifier

    async def test_a_reranker_failure_falls_back_to_fusion_order(
        self, vectors, documents, embedder, index_manager, safety, graph, metrics, user, handbook
    ) -> None:
        class BrokenReranker:
            async def rerank(self, query, texts, *, top_k):  # noqa: ANN001
                raise RuntimeError("cross-encoder is unavailable")

        engine = KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=index_manager, safety=safety, graph=graph,
            extractor=RuleEntityExtractor(), metrics=metrics, score_threshold=0.0,
            reranker=BrokenReranker(),
        )
        await engine.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        assert (await engine.retrieve("annual leave", owner_id=user)).chunks

    async def test_retrieval_before_any_index_exists_reports_unavailable(
        self, knowledge, user
    ) -> None:
        """Distinct from a genuine empty result: nothing was searched, so the caller must not
        present it as 'we looked and found nothing'."""
        result = await knowledge.retrieve("anything at all", owner_id=user)
        assert result.chunks == ()
        assert result.strategy == "unavailable"
        assert result.is_empty_but_searched is False

    async def test_an_out_of_corpus_query_returns_nothing(
        self, knowledge, user, handbook
    ) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        result = await knowledge.retrieve("the melting point of tungsten", owner_id=user)
        assert not result.chunks
        assert result.is_empty_but_searched

    async def test_retrieval_is_owner_scoped(self, knowledge, handbook) -> None:
        alice, bob = UserId("alice"), UserId("bob")
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=alice,
        )
        assert not (await knowledge.retrieve("annual leave", owner_id=bob)).chunks


class TestChunkIdParsing:
    def test_a_well_formed_id_round_trips(self) -> None:
        assert Engine._parse_chunk_id("handbook:v2:5") == ("handbook", 2, 5)

    def test_a_document_id_containing_colons_is_handled(self) -> None:
        """Source ids like `source:root` are real; splitting from the left would break them."""
        assert Engine._parse_chunk_id("src:root:v1:0") == ("src:root", 1, 0)

    @pytest.mark.parametrize("bad", ["nonsense", "a:b:c", "", "handbook:vX:1"])
    def test_a_malformed_id_yields_no_document(self, bad: str) -> None:
        assert Engine._parse_chunk_id(bad)[0] is None


class TestLexicalIndexRebuild:
    async def test_the_index_is_repopulated_from_the_document_store(
        self, knowledge, user, handbook
    ) -> None:
        """The lexical index is process-local, so a restart or a new replica starts empty and
        hybrid retrieval would silently degrade to dense-only."""
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        original = knowledge.lexical_size

        knowledge._lexical.clear()
        assert knowledge.lexical_size == 0

        rebuilt = await knowledge.rebuild_lexical_index()
        assert rebuilt == original
        assert knowledge.lexical_size == original

    async def test_rebuilding_restores_lexical_retrieval(
        self, knowledge, user, handbook
    ) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        knowledge._lexical.clear()
        await knowledge.rebuild_lexical_index()

        assert (await knowledge.retrieve("AURA-4419", owner_id=user)).chunks

    async def test_rebuilding_an_empty_store_is_zero(self, knowledge) -> None:
        assert await knowledge.rebuild_lexical_index() == 0

    async def test_rebuilding_preserves_owner_scoping(self, knowledge, handbook) -> None:
        alice, bob = UserId("alice"), UserId("bob")
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=alice,
        )
        await knowledge.rebuild_lexical_index()
        assert not (await knowledge.retrieve("AURA-4419", owner_id=bob)).chunks


class TestLexicalIndexAcrossWorkers:
    """The BM25 index is per process, so each gateway worker has its own, while the document
    and vector stores are shared. A delete or a new version that lands on worker A updates only
    A's index. Worker B must still never return the retired text (F-14). Two engines over one
    pair of stores stand in for two uvicorn workers."""

    @staticmethod
    def _second_worker(documents, vectors, embedder, index_manager) -> KnowledgeEngine:
        return KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=index_manager, score_threshold=0.15,
        )

    async def test_a_document_deleted_on_another_worker_is_not_retrieved(
        self, knowledge, documents, vectors, embedder, index_manager, user, handbook
    ) -> None:
        worker_b = self._second_worker(documents, vectors, embedder, index_manager)
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        await knowledge.ingest(
            content="The AURA-4419 escalation runbook says to page the on-call SRE.",
            title="Runbook", document_id=DocumentId("runbook"), owner_id=user,
        )
        await worker_b.rebuild_lexical_index()
        before = await worker_b.retrieve("AURA-4419 reimbursement", owner_id=user)
        assert any(c.chunk.document_id.value == "handbook" for c in before.chunks)
        indexed = worker_b.lexical_size

        assert await knowledge.delete_document(DocumentId("handbook"), owner_id=user) > 0

        after = await worker_b.retrieve("AURA-4419 reimbursement", owner_id=user)
        assert all(c.chunk.document_id.value != "handbook" for c in after.chunks)
        # The stale hits are evicted from B's index, and only those: the live document is
        # still found on B.
        assert worker_b.lexical_size < indexed
        assert any(c.chunk.document_id.value == "runbook" for c in after.chunks)

    async def test_a_version_superseded_on_another_worker_is_not_retrieved(
        self, knowledge, documents, vectors, embedder, index_manager, user
    ) -> None:
        worker_b = self._second_worker(documents, vectors, embedder, index_manager)
        await knowledge.ingest(
            content="The expense cap is 250 dollars per claim.", title="Policy",
            document_id=DocumentId("policy"), owner_id=user,
        )
        await worker_b.rebuild_lexical_index()
        await knowledge.ingest(
            content="The expense cap is 500 dollars per claim.", title="Policy",
            document_id=DocumentId("policy"), owner_id=user,
        )

        result = await worker_b.retrieve("expense cap dollars per claim", owner_id=user)
        contents = " ".join(c.chunk.content for c in result.chunks)
        assert "250" not in contents
        assert "500" in contents


class TestLexicalIndexConvergesAcrossWorkers:
    """The other half of F-14: every worker's BM25 index converges with the shared document
    store, not only at restart. A write on worker A bumps a per-owner corpus revision in the
    store; worker B compares it before searching and refreshes only that owner's shard. Two
    engines over one pair of stores stand in for two uvicorn workers, each started (rebuilt)
    before the other one writes."""

    RUNBOOK = "The AURA-4419 escalation runbook says to page the on-call SRE."

    @staticmethod
    def _worker(documents, vectors, embedder, index_manager) -> KnowledgeEngine:
        return KnowledgeEngine(
            documents=documents, vectors=vectors, embedder=embedder,
            index_manager=index_manager, score_threshold=0.15,
        )

    @staticmethod
    def _lexical_hits(result, document_id: str) -> list:
        return [
            c for c in result.chunks
            if c.chunk.document_id.value == document_id and c.lexical_rank is not None
        ]

    async def test_a_document_ingested_on_another_worker_is_found_lexically(
        self, documents, vectors, embedder, index_manager, user
    ) -> None:
        worker_a = self._worker(documents, vectors, embedder, index_manager)
        worker_b = self._worker(documents, vectors, embedder, index_manager)
        await worker_a.rebuild_lexical_index()
        await worker_b.rebuild_lexical_index()

        await worker_a.ingest(
            content=self.RUNBOOK, title="Runbook", document_id=DocumentId("runbook"),
            owner_id=user,
        )

        result = await worker_b.retrieve("AURA-4419 runbook", owner_id=user)
        assert self._lexical_hits(result, "runbook"), "worker B's lexical search missed it"
        assert worker_b.lexical_size == worker_a.lexical_size

    async def test_a_global_document_ingested_on_another_worker_is_found_by_every_user(
        self, documents, vectors, embedder, index_manager
    ) -> None:
        worker_a = self._worker(documents, vectors, embedder, index_manager)
        worker_b = self._worker(documents, vectors, embedder, index_manager)
        await worker_b.rebuild_lexical_index()

        await worker_a.ingest(
            content=self.RUNBOOK, title="Runbook", document_id=DocumentId("runbook"),
            owner_id=None,
        )

        for reader in (UserId("alice"), UserId("bob")):
            result = await worker_b.retrieve("AURA-4419 runbook", owner_id=reader)
            assert self._lexical_hits(result, "runbook")

    async def test_a_document_deleted_on_another_worker_leaves_its_index(
        self, documents, vectors, embedder, index_manager, user
    ) -> None:
        """Not just filtered when it happens to be a hit: gone from B's index on B's next search
        of that owner, whatever the query."""
        worker_a = self._worker(documents, vectors, embedder, index_manager)
        worker_b = self._worker(documents, vectors, embedder, index_manager)
        await worker_a.ingest(
            content=self.RUNBOOK, title="Runbook", document_id=DocumentId("runbook"),
            owner_id=user,
        )
        await worker_b.rebuild_lexical_index()
        assert worker_b.lexical_size > 0

        assert await worker_a.delete_document(DocumentId("runbook"), owner_id=user) > 0

        await worker_b.retrieve("something else entirely", owner_id=user)
        assert worker_b.lexical_size == 0
        result = await worker_b.retrieve("AURA-4419 runbook", owner_id=user)
        assert not result.chunks

    async def test_a_new_version_on_another_worker_replaces_the_old_one_lexically(
        self, documents, vectors, embedder, index_manager, user
    ) -> None:
        worker_a = self._worker(documents, vectors, embedder, index_manager)
        worker_b = self._worker(documents, vectors, embedder, index_manager)
        await worker_a.ingest(
            content="The expense cap is 250 dollars per claim.", title="Policy",
            document_id=DocumentId("policy"), owner_id=user,
        )
        await worker_b.rebuild_lexical_index()
        await worker_a.ingest(
            content="The expense cap is 500 dollars per claim.", title="Policy",
            document_id=DocumentId("policy"), owner_id=user,
        )

        result = await worker_b.retrieve("expense cap dollars per claim", owner_id=user)
        hits = self._lexical_hits(result, "policy")
        assert hits and all(c.chunk.document_version == 2 for c in hits)
        assert worker_b.lexical_size == worker_a.lexical_size

    async def test_an_erasure_on_another_worker_empties_that_owner_only(
        self, documents, vectors, embedder, index_manager
    ) -> None:
        alice, bob = UserId("alice"), UserId("bob")
        worker_a = self._worker(documents, vectors, embedder, index_manager)
        worker_b = self._worker(documents, vectors, embedder, index_manager)
        for owner in (alice, bob):
            await worker_a.ingest(
                content=self.RUNBOOK, title="Runbook",
                document_id=DocumentId(f"rb-{owner.value}"), owner_id=owner,
            )
        await worker_b.rebuild_lexical_index()

        await worker_a.erase_owner(alice)

        assert not (await worker_b.retrieve("AURA-4419 runbook", owner_id=alice)).chunks
        assert self._lexical_hits(
            await worker_b.retrieve("AURA-4419 runbook", owner_id=bob), "rb-bob"
        )
        assert worker_b.lexical_size == worker_a.lexical_size

    async def test_an_unreachable_revision_check_keeps_the_local_index(
        self, documents, vectors, embedder, index_manager, user
    ) -> None:
        """The check keeps the index fresh; it is not a gate. If the store cannot answer it,
        retrieval runs on what this worker already has instead of failing the turn."""
        worker = self._worker(documents, vectors, embedder, index_manager)
        await worker.ingest(
            content=self.RUNBOOK, title="Runbook", document_id=DocumentId("runbook"),
            owner_id=user,
        )

        async def unavailable(owner_keys):
            raise RuntimeError("postgres is down")

        documents.corpus_revisions = unavailable
        result = await worker.retrieve("AURA-4419 runbook", owner_id=user)
        assert self._lexical_hits(result, "runbook")
