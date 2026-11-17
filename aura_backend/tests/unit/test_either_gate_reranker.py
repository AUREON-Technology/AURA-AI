"""The combined relevance gate (docs/plans/arabic-language_plan.md, stage 4, decision 17).

A candidate passes if the word-overlap heuristic *or* a multilingual cross-encoder accepts it:
the heuristic cannot see an Arabic question about an English passage, and the cross-encoder
measured too strict on Arabic-only passages, so each covers the other's blind spot.
"""

from __future__ import annotations

import math

import pytest

from aura_core.knowledge.reranking import CrossEncoderReranker, EitherGateReranker, build_reranker
from aura_core.knowledge.retrieval import HeuristicReranker


class _FakeCrossEncoder:
    """Stands in for CrossEncoderReranker.logits(); None models a model that cannot score."""

    def __init__(self, logits: list[float] | None) -> None:
        self._logits = logits

    async def logits(self, query, documents):
        return self._logits


DOCS = [
    "Only Nginx publishes a port in the Stage 0 topology.",  # English, answers the question
    "Annual leave is 21 days for permanent staff.",           # English, unrelated
    "الإجازة السنوية 21 يوم لكل موظف دائم.",                     # Arabic, unrelated
]
ARABIC_QUESTION = "مين الـ component اللي بيعمل publish لـ port في Stage 0؟"


async def test_the_cross_encoder_lets_a_cross_lingual_match_through() -> None:
    heuristic = dict(await HeuristicReranker().rerank(ARABIC_QUESTION, DOCS))
    gate = EitherGateReranker(_FakeCrossEncoder([4.0, -6.0, -6.0]))
    scores = dict(await gate.rerank(ARABIC_QUESTION, DOCS))
    assert scores[0] == pytest.approx(1 / (1 + math.exp(-4.0)))
    assert scores[0] > heuristic[0] and scores[0] > 0.35
    assert (await gate.rerank(ARABIC_QUESTION, DOCS))[0][0] == 0


async def test_the_heuristic_still_passes_what_the_cross_encoder_rejects() -> None:
    query = "الإجازة السنوية كام يوم؟"
    heuristic = dict(await HeuristicReranker().rerank(query, DOCS))
    gate = EitherGateReranker(_FakeCrossEncoder([-6.0, -6.0, -6.0]))
    scores = dict(await gate.rerank(query, DOCS))
    assert scores[2] == pytest.approx(heuristic[2])
    assert all(0.0 <= s <= 1.0 for s in scores.values())


async def test_a_cross_encoder_that_cannot_score_degrades_to_the_heuristic() -> None:
    query = "annual leave days"
    expected = await HeuristicReranker().rerank(query, DOCS)
    assert await EitherGateReranker(_FakeCrossEncoder(None)).rerank(query, DOCS) == expected


async def test_top_k_and_empty_input() -> None:
    gate = EitherGateReranker(_FakeCrossEncoder([1.0, 2.0, 3.0]))
    assert len(await gate.rerank("q", DOCS, top_k=2)) == 2
    assert await gate.rerank("q", []) == []


@pytest.mark.parametrize("name", ["either", "cross_encoder_or_heuristic", "Either"])
def test_the_provider_is_configurable(name: str) -> None:
    reranker = build_reranker(name, model="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
    assert isinstance(reranker, EitherGateReranker)
    assert isinstance(reranker._cross_encoder, CrossEncoderReranker)
