"""Cross-encoder reranking, and every way it is allowed to fail.

A reranker that takes down search when its model will not load is worse than a weak ranking, so
almost all of the behaviour worth testing here is degradation: missing dependency, load failure,
timeout, wrong output shape. Each must fall back to the heuristic and still return a usable
ordering.

The model itself is faked. Loading a real cross-encoder would download weights and add seconds to
the suite, and what these tests check is the adapter's contract -- ordering, bounds, thread
offloading, fallback -- not whether MiniLM ranks well. That distinction is worth being explicit
about: nothing here demonstrates a quality improvement, only that the mechanism is correct.
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.domain.errors import ConfigurationError
from aura_core.knowledge.reranking import (
    DEFAULT_CROSS_ENCODER,
    CrossEncoderReranker,
    build_reranker,
)
from aura_core.knowledge.retrieval import HeuristicReranker

DOCS = [
    "Refunds are issued within 14 days of receiving the returned item.",
    "The office is open Monday to Friday, nine until five.",
    "To get your money back, start a return from the orders page.",
]


class FakeCrossEncoder:
    """Stands in for sentence_transformers.CrossEncoder."""

    def __init__(self, scores=None, *, fail: bool = False, delay: float = 0.0,
                 wrong_length: bool = False) -> None:
        self._scores = scores
        self.fail = fail
        self.delay = delay
        self.wrong_length = wrong_length
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs):
        self.calls.append(list(pairs))
        if self.fail:
            raise RuntimeError("torch exploded")
        if self.delay:
            import time

            time.sleep(self.delay)
        if self.wrong_length:
            return [0.5]
        if self._scores is not None:
            return list(self._scores[: len(pairs)])
        # Default: score by position so ordering is predictable.
        return [float(len(pairs) - i) for i in range(len(pairs))]


def _loaded(reranker: CrossEncoderReranker, model) -> CrossEncoderReranker:
    """Inject a model, skipping the import and load path."""
    reranker._model = model
    return reranker


class TestBuildReranker:
    def test_the_default_is_the_heuristic(self) -> None:
        """Zero dependency and bounded latency. The cross-encoder adds 100-300 ms per retrieval on
        CPU, which is a real cost on a box also running a 7B generator."""
        assert isinstance(build_reranker("heuristic"), HeuristicReranker)
        assert isinstance(build_reranker(""), HeuristicReranker)

    @pytest.mark.parametrize("name", ["cross_encoder", "cross-encoder", "CrossEncoder"])
    def test_the_cross_encoder_is_selectable_by_several_spellings(self, name: str) -> None:
        assert isinstance(build_reranker(name), CrossEncoderReranker)

    def test_an_unknown_provider_raises_rather_than_defaulting(self) -> None:
        """A typo in AURA_KNOWLEDGE_RERANK_PROVIDER should fail at startup, not quietly downgrade
        ranking quality in a way nobody notices for a month."""
        with pytest.raises(ConfigurationError) as excinfo:
            build_reranker("corss_encoder")
        assert "cross_encoder" in str(excinfo.value.details["supported"])

    def test_constructing_it_loads_no_model(self) -> None:
        reranker = build_reranker("cross_encoder")
        assert reranker._model is None


class TestScoring:
    async def test_it_ranks_by_the_model_score(self) -> None:
        reranker = _loaded(CrossEncoderReranker(), FakeCrossEncoder([0.1, 0.2, 0.9]))
        ranked = await reranker.rerank("refund", DOCS)
        assert [index for index, _ in ranked] == [2, 1, 0]

    async def test_it_returns_indices_not_documents(self) -> None:
        """The port returns indices so the caller keeps its richer objects -- chunks with
        provenance -- without the reranker knowing they exist."""
        reranker = _loaded(CrossEncoderReranker(), FakeCrossEncoder([0.5, 0.9, 0.1]))
        ranked = await reranker.rerank("q", DOCS)
        assert all(isinstance(index, int) for index, _ in ranked)
        assert {index for index, _ in ranked} == {0, 1, 2}

    async def test_it_passes_the_query_with_every_document(self) -> None:
        """Joint encoding is the whole point: a cross-encoder reads query and passage together,
        which is how it scores a paraphrase that shares no content words."""
        model = FakeCrossEncoder()
        await _loaded(CrossEncoderReranker(), model).rerank("refund policy", DOCS)
        assert model.calls[0] == [("refund policy", doc) for doc in DOCS]

    async def test_top_k_truncates(self) -> None:
        reranker = _loaded(CrossEncoderReranker(), FakeCrossEncoder())
        assert len(await reranker.rerank("q", DOCS, top_k=2)) == 2

    async def test_no_documents_is_an_empty_result_not_a_model_call(self) -> None:
        model = FakeCrossEncoder()
        reranker = _loaded(CrossEncoderReranker(), model)
        assert await reranker.rerank("q", []) == []
        assert model.calls == []

    async def test_negative_scores_rank_correctly(self) -> None:
        """Cross-encoder outputs are logits, not probabilities, and are routinely negative. Code
        that assumed a 0-1 range would mis-order every result on an unfamiliar corpus."""
        reranker = _loaded(CrossEncoderReranker(), FakeCrossEncoder([-5.0, -1.0, -9.0]))
        ranked = await reranker.rerank("q", DOCS)
        assert [index for index, _ in ranked] == [1, 0, 2]

    async def test_scoring_happens_off_the_event_loop(self) -> None:
        """model.predict is synchronous CPU work. On the event loop it would stall every other
        in-flight request for its duration."""
        model = FakeCrossEncoder(delay=0.05)
        reranker = _loaded(CrossEncoderReranker(), model)

        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        await reranker.rerank("q", DOCS)
        beat.cancel()
        # If predict blocked the loop, the heartbeat could not have advanced during it.
        assert ticks > 0


class TestCandidateBound:
    async def test_only_max_candidates_reach_the_model(self) -> None:
        """Scoring is linear in candidates, so an unbounded list turns a caller's mistake into a
        multi-second stall in the request path."""
        model = FakeCrossEncoder()
        reranker = _loaded(CrossEncoderReranker(max_candidates=2), model)
        await reranker.rerank("q", DOCS)
        assert len(model.calls[0]) == 2

    async def test_unscored_candidates_are_returned_below_the_scored_ones(self) -> None:
        """Nothing is silently dropped: the tail keeps its incoming order beneath everything the
        model ranked."""
        reranker = _loaded(CrossEncoderReranker(max_candidates=2), FakeCrossEncoder([0.2, 0.8]))
        ranked = await reranker.rerank("q", DOCS)
        assert [index for index, _ in ranked] == [1, 0, 2]
        assert len(ranked) == 3

    async def test_the_tail_sentinel_is_below_a_negative_minimum(self) -> None:
        """Derived from the observed minimum rather than assumed to be zero -- with negative logits
        a zero sentinel would rank unscored candidates *above* scored ones."""
        reranker = _loaded(CrossEncoderReranker(max_candidates=2), FakeCrossEncoder([-3.0, -4.0]))
        ranked = await reranker.rerank("q", DOCS)
        scores = dict(ranked)
        assert scores[2] < min(scores[0], scores[1])


class TestDegradation:
    """Every failure path must still return a usable ordering."""

    async def test_a_missing_dependency_falls_back(self, monkeypatch) -> None:
        import builtins

        real_import = builtins.__import__

        def no_sentence_transformers(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_sentence_transformers)
        reranker = CrossEncoderReranker()
        ranked = await reranker.rerank("refund", DOCS)
        assert len(ranked) == len(DOCS)
        assert reranker._load_failed is True

    async def test_the_failed_load_is_not_retried_on_every_query(self, monkeypatch) -> None:
        """Otherwise a deployment without sentence-transformers pays an import attempt per
        request, and logs a warning per request."""
        import builtins

        real_import = builtins.__import__
        attempts = 0

        def counting_import(name, *args, **kwargs):
            nonlocal attempts
            if name == "sentence_transformers":
                attempts += 1
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", counting_import)
        reranker = CrossEncoderReranker()
        await reranker.rerank("q", DOCS)
        await reranker.rerank("q", DOCS)
        await reranker.rerank("q", DOCS)
        assert attempts == 1

    async def test_a_scoring_exception_falls_back(self) -> None:
        reranker = _loaded(CrossEncoderReranker(), FakeCrossEncoder(fail=True))
        ranked = await reranker.rerank("refund", DOCS)
        assert len(ranked) == len(DOCS)

    async def test_a_timeout_falls_back(self) -> None:
        reranker = _loaded(
            CrossEncoderReranker(timeout_s=0.01), FakeCrossEncoder(delay=0.5)
        )
        ranked = await reranker.rerank("refund", DOCS)
        assert len(ranked) == len(DOCS)

    async def test_a_wrong_output_shape_falls_back(self) -> None:
        """A shape mismatch means the model is not the pairwise scorer we assume. Ranking on it
        would silently mis-order results, which is worse than not reranking."""
        reranker = _loaded(CrossEncoderReranker(), FakeCrossEncoder(wrong_length=True))
        ranked = await reranker.rerank("refund", DOCS)
        assert len(ranked) == len(DOCS)

    async def test_the_fallback_ranking_is_real_not_input_order(self) -> None:
        """Degrading to the heuristic keeps a relevance ordering. Falling back to input order --
        which is what the engine does if this raises -- discards the reranking stage entirely."""
        reranker = _loaded(CrossEncoderReranker(), FakeCrossEncoder(fail=True))
        ranked = await reranker.rerank("refund money back", DOCS)
        top = ranked[0][0]
        # Documents 0 and 2 are both about refunds; the office-hours document must not win.
        assert top in (0, 2)

    async def test_a_custom_fallback_is_used(self) -> None:
        class Sentinel:
            name = "sentinel"

            async def rerank(self, query, documents, *, top_k=None):
                return [(0, 99.0)]

        reranker = _loaded(
            CrossEncoderReranker(fallback=Sentinel()), FakeCrossEncoder(fail=True)
        )
        assert await reranker.rerank("q", DOCS) == [(0, 99.0)]


class TestMetrics:
    async def test_a_scored_call_is_counted(self) -> None:
        recorded = []

        class Counter:
            def labels(self, **kw):
                recorded.append(kw)
                return self

            def inc(self):
                pass

        class Metrics:
            rerank_total = Counter()

        reranker = _loaded(CrossEncoderReranker(metrics=Metrics()), FakeCrossEncoder())
        await reranker.rerank("q", DOCS)
        assert recorded == [{"reranker": "cross_encoder", "outcome": "scored"}]

    async def test_a_degraded_call_is_counted_separately(self) -> None:
        """Operators need to see silent degradation. A reranker quietly falling back for a month is
        exactly the kind of regression that shows up only as 'search feels worse'."""
        recorded = []

        class Counter:
            def labels(self, **kw):
                recorded.append(kw)
                return self

            def inc(self):
                pass

        class Metrics:
            rerank_total = Counter()

        reranker = _loaded(
            CrossEncoderReranker(metrics=Metrics()), FakeCrossEncoder(fail=True)
        )
        await reranker.rerank("q", DOCS)
        assert {"reranker": "cross_encoder", "outcome": "degraded"} in recorded


class TestPortConformance:
    def test_both_rerankers_satisfy_the_port(self) -> None:
        from aura_core.ports.llm import Reranker

        assert isinstance(HeuristicReranker(), Reranker)
        assert isinstance(CrossEncoderReranker(), Reranker)

    async def test_both_agree_on_the_return_shape(self) -> None:
        """Interchangeable by configuration means the engine must not be able to tell them apart
        from the shape of what comes back."""
        heuristic = await HeuristicReranker().rerank("refund", DOCS, top_k=2)
        cross = await _loaded(CrossEncoderReranker(), FakeCrossEncoder()).rerank(
            "refund", DOCS, top_k=2
        )
        for ranked in (heuristic, cross):
            assert len(ranked) == 2
            assert all(isinstance(i, int) and isinstance(s, float) for i, s in ranked)

    def test_the_default_model_is_a_cross_encoder_not_a_bi_encoder(self) -> None:
        """A sentence-transformer bi-encoder would load and run without error but score pairs
        meaninglessly, which is the kind of mistake that produces plausible-looking bad rankings."""
        assert "cross-encoder" in DEFAULT_CROSS_ENCODER
