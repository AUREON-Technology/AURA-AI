"""The relevance threshold filters on a real score on every rerank path (audit F-30 / AR-14).

When reranking was disabled, or the reranker raised, ``KnowledgeEngine._rerank`` used to give each
candidate ``1 - i/n`` -- a score derived from its *rank*. On those paths the top candidate always
scored 1.0 and passed the floor however unrelated it was, the bottom third was always cut however
relevant, and ``searched_but_empty`` could never be true. These tests pin the opposite: the score
the floor sees measures the chunk against the query.

The floor here is the production default (0.35), not the conftest's lowered 0.15: on these paths
the gate is the heuristic relevance, which the production floor is calibrated for, so there is
no hash-embedder caveat to work around.
"""

from __future__ import annotations

from typing import Sequence

import pytest

from aura_core.domain import DocumentId, UserId
from aura_core.knowledge import KnowledgeEngine

PRODUCTION_FLOOR = 0.35

LEAVE_POLICY = (
    "# Leave policy\n"
    "Annual leave is 21 days per year for permanent staff. Unused annual leave carries over "
    "for one quarter and is then forfeited."
)
UNRELATED_QUERY = "melting point of tungsten under vacuum"


class _BrokenReranker:
    name = "broken"

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_k: int | None = None
    ) -> list[tuple[int, float]]:
        raise RuntimeError("model crashed")


def _engine(vectors, documents, embedder, index_manager, **overrides) -> KnowledgeEngine:
    options = {"score_threshold": PRODUCTION_FLOOR, "top_k": 5}
    options.update(overrides)
    return KnowledgeEngine(
        documents=documents, vectors=vectors, embedder=embedder,
        index_manager=index_manager, **options,
    )


@pytest.fixture(params=["disabled", "raising"])
def degraded(request, vectors, documents, embedder, index_manager) -> KnowledgeEngine:
    """Both paths that used to fall back to a rank pseudo-score."""
    if request.param == "disabled":
        return _engine(vectors, documents, embedder, index_manager, rerank_enabled=False)
    return _engine(vectors, documents, embedder, index_manager, reranker=_BrokenReranker())


async def _ingest(engine: KnowledgeEngine, user: UserId, doc: str, content: str) -> None:
    await engine.ingest(content=content, title=doc, document_id=DocumentId(doc), owner_id=user)


class TestThresholdWithoutAWorkingReranker:
    async def test_an_unrelated_query_returns_nothing(self, degraded, user) -> None:
        """One-document corpus, so the only candidate is the top candidate: under the rank
        pseudo-score it scored 1.0 and was returned as relevant context."""
        await _ingest(degraded, user, "leave", LEAVE_POLICY)

        result = await degraded.retrieve(UNRELATED_QUERY, owner_id=user)

        assert result.candidates_considered > 0, "the dense retriever always offers a candidate"
        assert not result.chunks
        assert result.is_empty_but_searched
        assert all(c.score < PRODUCTION_FLOOR for c in result.below_threshold)

    async def test_a_relevant_query_still_returns_its_chunk(self, degraded, user) -> None:
        await _ingest(degraded, user, "leave", LEAVE_POLICY)

        result = await degraded.retrieve("how many days of annual leave", owner_id=user)

        assert result.chunks
        assert "21 days" in result.chunks[0].chunk.content
        assert result.chunks[0].score >= PRODUCTION_FLOOR

    async def test_relevance_not_rank_decides_who_passes(self, degraded, user) -> None:
        """Three chunks that all answer the query. The pseudo-score gave the third 1 - 2/3 = 0.33,
        below the 0.35 floor, so it was cut for being third rather than for being irrelevant."""
        await _ingest(degraded, user, "staff", "Annual leave for staff accrues monthly from the start date.")
        await _ingest(degraded, user, "contractors", "Contractors get annual leave only if their agreement says so.")
        await _ingest(degraded, user, "managers", "Managers approve annual leave requests within five days.")

        result = await degraded.retrieve("annual leave", owner_id=user)

        returned = {c.chunk.document_id.value for c in result.chunks}
        assert returned == {"staff", "contractors", "managers"}


class TestDisabledRerankingKeepsFusionOrder:
    async def test_order_is_fusion_order_and_no_rerank_score_is_claimed(
        self, vectors, documents, embedder, index_manager, user
    ) -> None:
        engine = _engine(vectors, documents, embedder, index_manager, rerank_enabled=False)
        await _ingest(engine, user, "leave", LEAVE_POLICY)

        result = await engine.retrieve("annual leave", owner_id=user)

        assert result.chunks
        assert all(c.rerank_score is None for c in result.chunks)


class TestRaisingRerankerDegradesToHeuristic:
    async def test_scores_come_from_the_heuristic_reranker(
        self, vectors, documents, embedder, index_manager, user
    ) -> None:
        from aura_core.knowledge import HeuristicReranker

        engine = _engine(vectors, documents, embedder, index_manager, reranker=_BrokenReranker())
        await _ingest(engine, user, "leave", LEAVE_POLICY)

        result = await engine.retrieve("annual leave carries over", owner_id=user)
        expected = await HeuristicReranker().rerank(
            "annual leave carries over", [result.chunks[0].chunk.content]
        )

        assert result.chunks[0].score == pytest.approx(expected[0][1])
        assert result.chunks[0].score != 1.0
