"""Chunking, hybrid retrieval, index lifecycle (ADR-005, ADR-007)."""

from __future__ import annotations

import pytest

from aura_core.domain import DocumentId, IndexIdentity, UserId
from aura_core.domain.errors import ConflictError, IndexNotReadyError
from aura_core.knowledge import (
    BM25Index,
    ChunkingConfig,
    IndexManager,
    PromotionGate,
    chunk_text,
    reciprocal_rank_fusion,
    split_sentences,
    tokenize,
)
from aura_core.ports.storage import GLOBAL_OWNER, VectorPoint


class TestChunking:
    def test_heading_path_is_preserved_for_citation(self, handbook: str) -> None:
        """The reason a citation can read "Leave > Annual Leave" rather than "chunk 7"."""
        pieces = chunk_text(handbook)
        paths = [p.heading_path for p in pieces]
        assert ("Employee Handbook", "Leave", "Annual Leave") in paths

    def test_chunks_never_cross_a_heading_boundary(self, handbook: str) -> None:
        for piece in chunk_text(handbook):
            assert "### Sick Leave" not in piece.content

    def test_sentences_are_not_split_mid_word(self) -> None:
        """The legacy chunker slices at raw character offsets (``services/rag.py:276``)."""
        text = "Alpha beta gamma delta. " * 100
        for piece in chunk_text(text, ChunkingConfig(target_tokens=40, max_tokens=64)):
            assert not piece.content.startswith(" ")
            assert piece.content.rstrip()[-1] in ".!?" or piece.hard_split

    def test_abbreviations_do_not_split_sentences(self) -> None:
        assert split_sentences("Ask Dr. Smith at approx. 9am. Then leave.") == [
            "Ask Dr. Smith at approx. 9am.",
            "Then leave.",
        ]

    def test_list_items_stay_intact(self) -> None:
        sentences = split_sentences("- First item. Still the item.\n- Second item.")
        assert sentences[0].startswith("- First item")

    def test_oversized_units_are_hard_split_and_marked(self) -> None:
        """A minified line or base64 blob cannot be chunked sensibly; failing visibly beats
        silently emitting an unusable chunk."""
        pieces = chunk_text("x" * 20_000, ChunkingConfig(target_tokens=64, max_tokens=128))
        assert len(pieces) > 1
        assert all(p.hard_split for p in pieces)

    def test_documents_without_headings_still_chunk(self) -> None:
        assert chunk_text("Just a paragraph of prose with no structure at all.")

    def test_overlap_is_by_sentence(self) -> None:
        text = " ".join(f"Sentence number {i} carries some content." for i in range(60))
        pieces = chunk_text(text, ChunkingConfig(target_tokens=60, max_tokens=100, overlap_sentences=1))
        assert len(pieces) > 1
        # The last sentence of one chunk opens the next.
        first_tail = split_sentences(pieces[0].content)[-1]
        assert pieces[1].content.startswith(first_tail)


class TestBM25:
    @pytest.fixture
    def index(self) -> BM25Index:
        index = BM25Index()
        index.add("a", "Annual leave is 21 days per year for permanent staff.")
        index.add("b", "Ticket AURA-4419 tracks the reimbursement backlog.")
        index.add("c", "Sick leave requires a medical certificate after three days.")
        return index

    def test_exact_identifiers_are_found(self, index: BM25Index) -> None:
        """The case dense retrieval fails: a rare token with no semantic neighbourhood."""
        assert index.search("AURA-4419")[0][0] == "b"

    def test_identifiers_survive_tokenisation(self) -> None:
        assert "aura-4419" in tokenize("Ticket AURA-4419 is open")
        assert "v2.1.0" in tokenize("Release v2.1.0 shipped")

    def test_common_terms_do_not_score_negatively(self, index: BM25Index) -> None:
        """Without the IDF floor, a term in most documents gets a negative weight and ranking
        inverts."""
        assert all(score >= 0 for _, score in index.search("leave"))

    def test_removal_updates_document_frequencies(self, index: BM25Index) -> None:
        index.remove("b")
        assert index.size == 2
        assert not index.search("AURA-4419")

    def test_empty_query_returns_nothing(self, index: BM25Index) -> None:
        assert index.search("") == []


class TestFusion:
    def test_rrf_rewards_agreement_between_retrievers(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]])
        assert {doc for doc, _ in fused[:2]} == {"a", "b"}

    def test_a_document_found_by_one_retriever_still_ranks(self) -> None:
        """The property that makes hybrid retrieval worth having."""
        fused = dict(reciprocal_rank_fusion([["a", "b"], ["z"]]))
        assert "z" in fused

    def test_rrf_needs_no_score_normalisation(self) -> None:
        """Ranks only -- BM25 scores and cosine similarities are not comparable."""
        assert reciprocal_rank_fusion([["a"], ["a"]])[0][1] == pytest.approx(2 / 61)


class TestHybridRetrieval:
    async def test_exact_identifier_retrieved_via_the_lexical_path(
        self, knowledge, user, handbook
    ) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("handbook"), owner_id=user
        )
        result = await knowledge.retrieve("AURA-4419", owner_id=user)
        assert result.chunks
        assert any(c.lexical_score for c in result.chunks), "lexical retriever contributed nothing"

    async def test_out_of_corpus_query_returns_nothing(self, knowledge, user, handbook) -> None:
        """The single most important correctness property.

        The legacy pipeline always returns top-k and labels it "Relevant Context", so the model
        grounds a confident answer in noise.
        """
        await knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("handbook"), owner_id=user
        )
        result = await knowledge.retrieve("the melting point of tungsten", owner_id=user)
        assert not result.chunks
        assert result.is_empty_but_searched

    async def test_tenant_isolation(self, knowledge, user, handbook) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("handbook"), owner_id=user
        )
        assert not (await knowledge.retrieve("annual leave", owner_id=UserId("other"))).chunks

    async def test_ingestion_is_idempotent_on_content(self, knowledge, user, handbook) -> None:
        """Without this the nightly refresh re-versions every unchanged document each run."""
        first = await knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("h"), owner_id=user
        )
        second = await knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("h"), owner_id=user
        )
        assert first.created and not second.created
        assert second.skipped_reason == "unchanged_content"

    async def test_new_version_supersedes_and_retires_old_chunks(
        self, knowledge, documents, user
    ) -> None:
        """Stale and fresh chunks must never coexist in one result set."""
        await knowledge.ingest(
            content="# Policy\nAnnual leave is 21 days per year for staff.",
            title="Policy", document_id=DocumentId("policy"), owner_id=user,
        )
        await knowledge.ingest(
            content="# Policy\nAnnual leave is 25 days per year for staff.",
            title="Policy", document_id=DocumentId("policy"), owner_id=user,
        )
        result = await knowledge.retrieve("annual leave days", owner_id=user)
        contents = " ".join(c.chunk.content for c in result.chunks)
        assert "25 days" in contents
        assert "21 days" not in contents

    async def test_citations_carry_document_and_version(self, knowledge, user, handbook) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("handbook"), owner_id=user
        )
        result = await knowledge.retrieve("annual leave", owner_id=user)
        assert result.citations()
        assert "@v1" in result.citations()[0]


class TestIndexLifecycle:
    def test_identity_includes_the_embedding_model(self) -> None:
        identity = IndexIdentity("aura_knowledge", 3, "BGE-small-en-v1.5", 384)
        assert identity.physical_name == "aura-knowledge__v3__bge-small-en-v1-5__384"

    def test_different_models_produce_different_indexes(self) -> None:
        """SEC-03: this is what makes silently mixing embedding spaces impossible."""
        a = IndexIdentity("k", 1, "model-a", 384)
        b = IndexIdentity("k", 1, "model-b", 384)
        assert a.physical_name != b.physical_name

    async def test_mismatched_parameters_conflict_rather_than_recreate(self, vectors) -> None:
        """``services/rag.py:69`` deletes the whole collection here. Data loss is not a
        recovery strategy."""
        identity = IndexIdentity("k", 1, "m", 384)
        await vectors.ensure_index(identity)
        with pytest.raises(ConflictError):
            await vectors.ensure_index(IndexIdentity("k", 1, "m", 384, distance="dot"))

    async def test_reading_through_a_foreign_index_raises(
        self, vectors, documents, embedder
    ) -> None:
        """A query vector from model B against vectors from model A is meaningless; an error
        beats confident nonsense."""
        manager = IndexManager(
            vectors=vectors, documents=documents, embedder=embedder, alias="k"
        )
        await vectors.set_alias("k", IndexIdentity("k", 1, "some-other-model", 384))
        with pytest.raises(IndexNotReadyError):
            await manager.resolve_for_read()

    async def test_promotion_is_an_alias_switch(self, vectors, documents, embedder) -> None:
        manager = IndexManager(
            vectors=vectors, documents=documents, embedder=embedder, alias="k"
        )
        await manager.ensure_ready()
        original = await manager.active_identity()

        candidate = IndexIdentity("k", 2, embedder.model_id, embedder.dimension)
        await vectors.ensure_index(candidate)
        await manager.promote(candidate, reason="test")
        assert (await manager.active_identity()).physical_name == candidate.physical_name

        # Rollback needs no gate: reverting to a known-good state is always safe.
        await manager.rollback(original)
        assert (await manager.active_identity()).physical_name == original.physical_name

    async def test_an_index_under_an_alias_cannot_be_dropped(self, vectors) -> None:
        identity = IndexIdentity("k", 1, "m", 384)
        await vectors.ensure_index(identity)
        await vectors.set_alias("k", identity)
        with pytest.raises(ConflictError):
            await vectors.drop_index(identity)


class TestPromotionGate:
    def test_recall_regression_blocks_promotion(self) -> None:
        result = PromotionGate(max_recall_drop=0.02).evaluate(
            candidate_recall=0.70, baseline_recall=0.85, error_rate=0.0,
            candidate_chunks=100, baseline_chunks=100,
        )
        assert not result.passed
        assert any("recall" in f for f in result.failures)

    def test_a_collapsed_corpus_blocks_promotion(self) -> None:
        """Catches the classic "the source started returning an error page" failure."""
        result = PromotionGate().evaluate(
            candidate_recall=None, baseline_recall=None, error_rate=0.0,
            candidate_chunks=4, baseline_chunks=1000,
        )
        assert not result.passed
        assert any("chunk count" in f for f in result.failures)

    def test_a_high_error_rate_blocks_promotion(self) -> None:
        result = PromotionGate(max_error_rate=0.05).evaluate(
            candidate_recall=None, baseline_recall=None, error_rate=0.30,
            candidate_chunks=100, baseline_chunks=100,
        )
        assert not result.passed

    def test_an_empty_index_never_promotes(self) -> None:
        result = PromotionGate().evaluate(
            candidate_recall=None, baseline_recall=None, error_rate=0.0,
            candidate_chunks=0, baseline_chunks=0,
        )
        assert not result.passed

    def test_a_healthy_rebuild_passes(self) -> None:
        result = PromotionGate().evaluate(
            candidate_recall=0.86, baseline_recall=0.85, error_rate=0.01,
            candidate_chunks=1050, baseline_chunks=1000,
        )
        assert result.passed


def _dummy_points(count: int, dimension: int) -> list[VectorPoint]:
    return [
        VectorPoint(id=f"dummy-{i}", vector=[0.1] * dimension, payload={"owner_id": GLOBAL_OWNER})
        for i in range(count)
    ]


class TestIndexStatus:
    """What `/health/ready` reports about the published index (KM-01).

    A published index built by another embedder makes every read refuse and every retrieval
    return nothing; the operator has to be able to see that without running a query.
    """

    async def test_a_matching_index_is_reported_as_matching(self, index_manager) -> None:
        await index_manager.ensure_ready()
        status = await index_manager.status()
        assert status["matches_embedder"] is True
        assert status["published"] == status["required"]

    async def test_an_index_from_another_embedder_is_reported_as_a_mismatch(
        self, vectors, index_manager, embedder
    ) -> None:
        foreign = IndexIdentity("test_knowledge", 1, "legacy-embedder", embedder.dimension)
        await vectors.set_alias("test_knowledge", foreign)
        status = await index_manager.status()
        assert status["matches_embedder"] is False
        assert status["published"] == foreign.physical_name
        assert status["required"] != status["published"]

    async def test_no_published_index_is_reported_as_not_matching(self, index_manager) -> None:
        status = await index_manager.status()
        assert status["published"] is None
        assert status["matches_embedder"] is False
        assert status["up_to_date"] is False

    async def test_a_matching_index_is_up_to_date(self, index_manager) -> None:
        await index_manager.ensure_ready()
        assert (await index_manager.status())["up_to_date"] is True

    async def test_an_index_from_another_embedder_is_not_up_to_date(
        self, vectors, index_manager, embedder
    ) -> None:
        await vectors.set_alias(
            "test_knowledge", IndexIdentity("test_knowledge", 1, "legacy-embedder", embedder.dimension)
        )
        assert (await index_manager.status())["up_to_date"] is False

    async def test_a_schema_version_bump_is_readable_but_not_up_to_date(
        self, vectors, documents, embedder
    ) -> None:
        """Same embedder, new index layout: queries still work against the old index, but the
        published name is not the one the running configuration would build."""
        await vectors.set_alias(
            "k", IndexIdentity("k", 1, embedder.model_id, embedder.dimension)
        )
        manager = IndexManager(
            vectors=vectors, documents=documents, embedder=embedder, alias="k", schema_version=2
        )
        status = await manager.status()
        assert status["matches_embedder"] is True
        assert status["up_to_date"] is False


class TestFirstPublishAfterAnEmbedderChange:
    """KM-01: the rebuild that repairs a stale alias must be able to promote.

    The gate guards a corpus that collapsed, or an empty build replacing a working index. Neither
    applies when the published index was built by another embedder: nothing can read it.
    """

    async def _publish_foreign(self, vectors, embedder, *, points: int) -> IndexIdentity:
        foreign = IndexIdentity("test_knowledge", 1, "legacy-embedder", embedder.dimension)
        await vectors.upsert(foreign, _dummy_points(points, embedder.dimension))
        await vectors.set_alias("test_knowledge", foreign)
        return foreign

    async def test_an_empty_corpus_can_replace_an_index_nothing_can_read(
        self, vectors, index_manager, embedder
    ) -> None:
        await self._publish_foreign(vectors, embedder, points=5)
        target = index_manager.identity_for_current_embedder()
        report = await index_manager.build(identity=target)
        gate = await index_manager.evaluate_promotion(target, report=report)
        assert report.chunks == 0
        assert gate.passed, gate.failures

    async def test_a_chunk_count_change_is_ignored_against_a_foreign_index(
        self, vectors, knowledge, index_manager, embedder, user, handbook
    ) -> None:
        await self._publish_foreign(vectors, embedder, points=500)
        await knowledge.ingest(content=handbook, title="Handbook", owner_id=user)
        target = index_manager.identity_for_current_embedder()
        report = await index_manager.build(identity=target)
        gate = await index_manager.evaluate_promotion(target, report=report)
        assert report.chunks > 0
        assert gate.passed, gate.failures
        await index_manager.promote(target, gate=gate, reason="test")
        assert (await index_manager.status())["matches_embedder"] is True

    async def test_an_empty_build_still_cannot_replace_a_readable_index(
        self, vectors, index_manager, embedder
    ) -> None:
        await index_manager.ensure_ready()
        await vectors.upsert(await index_manager.active_identity(), _dummy_points(10, embedder.dimension))
        candidate = IndexIdentity("test_knowledge", 2, embedder.model_id, embedder.dimension)
        report = await index_manager.build(identity=candidate)
        gate = await index_manager.evaluate_promotion(candidate, report=report)
        assert not gate.passed
        assert any("minimum" in failure for failure in gate.failures)

    async def test_a_collapsed_corpus_still_cannot_replace_a_readable_index(
        self, vectors, knowledge, index_manager, embedder, user, handbook
    ) -> None:
        await index_manager.ensure_ready()
        await vectors.upsert(await index_manager.active_identity(), _dummy_points(500, embedder.dimension))
        await knowledge.ingest(content=handbook, title="Handbook", owner_id=user)
        candidate = IndexIdentity("test_knowledge", 2, embedder.model_id, embedder.dimension)
        report = await index_manager.build(identity=candidate)
        gate = await index_manager.evaluate_promotion(candidate, report=report)
        assert not gate.passed
        assert any("chunk count" in failure for failure in gate.failures)
