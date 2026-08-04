"""Evaluation harness.

The harness is what decides whether a rebuilt index may be promoted and whether a build
regresses, so its arithmetic is load-bearing: a wrong nDCG silently green-lights a worse index.
These tests pin the metric definitions against hand-computed values rather than against the
implementation's own output.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain import DocumentId
from aura_core.domain.errors import EvaluationError, ValidationError
from aura_core.evaluation.harness import (
    AnswerEvaluator,
    CaseResult,
    EvalCase,
    EvalSuite,
    LLMJudge,
    RegressionGate,
    RetrievalEvaluator,
    SuiteResult,
    build_suite,
    summarise,
)
from aura_core.evaluation.harness import _mrr, _ndcg, _precision_at_k, _recall_at_k, _token_f1


class TestEvalCase:
    def test_empty_query_is_rejected_at_construction(self) -> None:
        """A blank query would score every case as a retrieval miss and look like a regression."""
        with pytest.raises(ValidationError):
            EvalCase(id="c1", query="   ")

    def test_from_jsonl_skips_blank_lines_and_defaults_ids(self) -> None:
        suite = EvalSuite.from_jsonl(
            "smoke",
            [
                json.dumps({"query": "how much leave?", "relevant_documents": ["handbook"]}),
                "",
                "   ",
                json.dumps({"id": "explicit", "query": "who is in finance?", "tags": ["people"]}),
            ],
        )
        assert len(suite) == 2
        assert suite.cases[0].id == "case-1"
        assert suite.cases[1].id == "explicit"
        assert suite.cases[1].tags == ("people",)

    def test_malformed_line_names_the_line_number(self) -> None:
        with pytest.raises(EvaluationError) as excinfo:
            EvalSuite.from_jsonl("broken", ['{"query": "ok"}', "{not json"])
        assert "line 2" in str(excinfo.value)

    def test_build_suite_round_trips_mappings(self) -> None:
        suite = build_suite([{"id": "a", "query": "q"}], name="inline")
        assert suite.name == "inline"
        assert suite.cases[0].id == "a"


class TestMetricDefinitions:
    """Hand-computed expectations. If an implementation detail changes, these must not move."""

    def test_recall_counts_distinct_relevant_documents(self) -> None:
        assert _recall_at_k(["a", "b"], {"a", "b", "c"}) == pytest.approx(2 / 3)
        assert _recall_at_k(["a", "a", "a"], {"a", "b"}) == pytest.approx(0.5)
        assert _recall_at_k(["x"], set()) == 0.0

    def test_precision_is_over_what_was_returned(self) -> None:
        assert _precision_at_k(["a", "x", "y"], {"a"}) == pytest.approx(1 / 3)
        assert _precision_at_k([], {"a"}) == 0.0

    def test_mrr_uses_the_first_relevant_rank(self) -> None:
        assert _mrr(["x", "a", "b"], {"a", "b"}) == pytest.approx(0.5)
        assert _mrr(["x", "y"], {"a"}) == 0.0

    def test_ndcg_rewards_ranking_not_merely_inclusion(self) -> None:
        """The property that makes nDCG worth computing alongside recall."""
        first = _ndcg(["a", "x", "y"], {"a"})
        third = _ndcg(["x", "y", "a"], {"a"})
        assert first == pytest.approx(1.0)
        assert third < first
        assert _ndcg([], {"a"}) == 0.0

    def test_ndcg_ideal_is_capped_by_what_could_be_returned(self) -> None:
        """Two relevant documents but only one retrieval slot is a perfect score, not 0.5."""
        assert _ndcg(["a"], {"a", "b"}) == pytest.approx(1.0)

    def test_token_f1_is_multiset_aware(self) -> None:
        assert _token_f1("21 days of annual leave", "21 days of annual leave") == pytest.approx(1.0)
        assert _token_f1("", "anything") == 0.0
        assert _token_f1("completely different words", "21 days") == 0.0
        # "days" appearing twice in the prediction must not count twice against one reference use.
        partial = _token_f1("days days", "21 days")
        assert 0.0 < partial < 1.0


class TestRetrievalEvaluator:
    async def test_scores_a_hit_and_publishes_metrics(self, knowledge, user, handbook) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        suite = EvalSuite(
            name="retrieval",
            cases=(
                EvalCase(
                    id="leave", query="annual leave days",
                    relevant_documents=("handbook",), owner_id=str(user),
                ),
            ),
        )
        result = await RetrievalEvaluator(knowledge, k=5).run(suite)

        assert result.pass_rate == 1.0
        assert result.mean("recall_at_k") == pytest.approx(1.0)
        assert "mrr" in result.metrics()
        assert result.duration_ms >= 0.0

    async def test_a_case_with_no_relevant_documents_asserts_silence(
        self, knowledge, user, handbook
    ) -> None:
        """Scoring an out-of-corpus question as recall 0 would penalise correct behaviour."""
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        suite = EvalSuite(
            name="silence",
            cases=(
                EvalCase(
                    id="out-of-corpus",
                    query="the melting point of tungsten in kelvin",
                    owner_id=str(user),
                ),
            ),
        )
        result = await RetrievalEvaluator(knowledge, k=5).run(suite)

        assert result.pass_rate == 1.0
        assert result.mean("empty_correct") == 1.0
        assert "recall_at_k" not in result.metrics()

    async def test_a_miss_reports_what_was_retrieved(self, knowledge, user, handbook) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        suite = EvalSuite(
            name="miss",
            cases=(
                EvalCase(
                    id="wrong-doc", query="annual leave days",
                    relevant_documents=("a-document-that-was-never-ingested",),
                    owner_id=str(user),
                ),
            ),
        )
        result = await RetrievalEvaluator(knowledge, k=5).run(suite)

        assert result.pass_rate == 0.0
        assert result.failures()[0].detail.startswith("no relevant document in top-5")

    async def test_compare_returns_not_assessed_without_a_suite(self, knowledge) -> None:
        """The gate must distinguish 'not measured' from 'measured and fine' (ADR-008)."""
        evaluator = RetrievalEvaluator(knowledge)
        assert await evaluator.compare(baseline="a", candidate="b", suite=None) == (None, None)
        assert await evaluator.compare(
            baseline="a", candidate="b", suite=EvalSuite(name="empty", cases=())
        ) == (None, None)

    async def test_compare_scores_only_the_identities_it_is_given(
        self, knowledge, user, handbook
    ) -> None:
        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        suite = EvalSuite(
            name="cmp",
            cases=(EvalCase(id="c", query="annual leave", relevant_documents=("handbook",),
                            owner_id=str(user)),),
        )
        baseline, candidate = await RetrievalEvaluator(knowledge).compare(
            baseline=None, candidate="candidate-index", suite=suite
        )
        assert baseline is None
        assert candidate == pytest.approx(1.0)

    async def test_metrics_are_published_when_a_registry_is_supplied(
        self, knowledge, user, handbook
    ) -> None:
        from aura_core.observability.metrics import Metrics

        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        registry = Metrics(enabled=False)
        suite = EvalSuite(
            name="published",
            cases=(EvalCase(id="c", query="annual leave", relevant_documents=("handbook",),
                            owner_id=str(user)),),
        )
        # The no-op metric path must not raise; publishing is fire-and-forget for the caller.
        result = await RetrievalEvaluator(knowledge, metrics=registry).run(suite)
        assert result.pass_rate == 1.0


class TestSuiteResult:
    def test_metrics_average_only_cases_that_reported_them(self) -> None:
        result = SuiteResult(
            suite="mixed",
            results=(
                CaseResult(case_id="a", passed=True, scores={"recall_at_k": 1.0}),
                CaseResult(case_id="b", passed=False, scores={}),
                CaseResult(case_id="c", passed=True, scores={"recall_at_k": 0.0}),
            ),
        )
        assert result.pass_rate == pytest.approx(2 / 3)
        assert result.mean("recall_at_k") == pytest.approx(0.5)
        assert result.mean("never_reported") == 0.0

    def test_empty_suite_has_zero_pass_rate_rather_than_dividing_by_zero(self) -> None:
        assert SuiteResult(suite="none", results=()).pass_rate == 0.0
        assert SuiteResult(suite="none", results=()).metrics() == {"pass_rate": 0.0}

    def test_as_dict_caps_the_failure_list(self) -> None:
        """A suite where everything fails must not produce an unbounded report payload."""
        results = tuple(
            CaseResult(case_id=f"c{i}", passed=False, detail="boom") for i in range(40)
        )
        payload = SuiteResult(suite="big", results=results).as_dict()
        assert payload["cases"] == 40
        assert len(payload["failures"]) == 20


class TestAnswerEvaluator:
    async def test_scores_substring_coverage_and_citations(
        self, container, user, handbook
    ) -> None:
        from aura_core.domain import DocumentId as DocId

        await container.startup()
        await container.knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocId("handbook"), owner_id=user,
        )
        suite = EvalSuite(
            name="answers",
            cases=(
                EvalCase(
                    id="leave",
                    query="How many days of annual leave?",
                    # EchoProvider replies with the user's message, so the assertion is about
                    # the scorer's arithmetic, not about model quality: one of these two is
                    # present and the other is not, which pins partial coverage at exactly 0.5.
                    expected_substrings=("annual leave", "a phrase that is definitely absent"),
                    owner_id=str(user),
                ),
            ),
        )
        result = await AnswerEvaluator(container.kernel).run(suite)
        case = result.results[0]

        assert case.scores["substring_coverage"] == pytest.approx(0.5)
        assert case.scores["forbidden_clean"] == 1.0
        assert not case.passed
        assert "missing expected content" in case.detail
        # Retrieval succeeded even though the echoed answer does not quote it.
        assert case.scores["cited"] == 1.0
        assert "confidence" in case.scores

    async def test_full_coverage_passes_a_case(self, container, user) -> None:
        await container.startup()
        suite = EvalSuite(
            name="covered",
            cases=(
                EvalCase(
                    id="c", query="How many days of annual leave?",
                    expected_substrings=("annual leave",), owner_id=str(user),
                ),
            ),
        )
        result = await AnswerEvaluator(container.kernel).run(suite)
        assert result.pass_rate == 1.0
        assert result.results[0].scores["substring_coverage"] == pytest.approx(1.0)

    async def test_forbidden_content_fails_the_case(self, container, user, handbook) -> None:
        from aura_core.domain import DocumentId as DocId

        await container.startup()
        await container.knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocId("handbook"), owner_id=user,
        )
        suite = EvalSuite(
            name="forbidden",
            cases=(
                EvalCase(
                    id="no-leak", query="How many days of annual leave?",
                    forbidden_substrings=("annual leave",), owner_id=str(user),
                ),
            ),
        )
        result = await AnswerEvaluator(container.kernel).run(suite)

        assert result.pass_rate == 0.0
        assert result.results[0].scores["forbidden_clean"] == 0.0
        assert "forbidden" in result.failures()[0].detail

    async def test_refusal_cases_score_the_absence_of_an_assertion(
        self, container, user
    ) -> None:
        """A refusal case that gets a confident answer is the failure mode worth catching."""
        await container.startup()
        suite = EvalSuite(
            name="refusals",
            cases=(
                EvalCase(
                    id="unknown", query="What is the melting point of tungsten?",
                    should_refuse=True, owner_id=str(user),
                ),
            ),
        )
        result = await AnswerEvaluator(container.kernel).run(suite)
        assert "refusal" in result.results[0].scores
        assert "substring_coverage" not in result.results[0].scores

    async def test_a_failing_judge_does_not_fail_the_run(self, container, user) -> None:
        """A judge is an external model. Its outage must degrade the report, not the build."""

        class ExplodingJudge:
            async def score(self, case, answer):  # noqa: ANN001
                raise RuntimeError("judge is down")

        await container.startup()
        suite = EvalSuite(name="judged", cases=(EvalCase(id="c", query="hello"),))
        result = await AnswerEvaluator(container.kernel, judge=ExplodingJudge()).run(suite)

        assert len(result.results) == 1
        assert "judge" not in result.results[0].scores

    async def test_a_working_judge_contributes_a_score(self, container, user) -> None:
        class ConstantJudge:
            async def score(self, case, answer):  # noqa: ANN001
                return 0.75

        await container.startup()
        suite = EvalSuite(name="judged", cases=(EvalCase(id="c", query="hello"),))
        result = await AnswerEvaluator(container.kernel, judge=ConstantJudge()).run(suite)
        assert result.results[0].scores["judge"] == 0.75


class TestLLMJudge:
    async def test_parses_a_score_out_of_a_json_response(self, router) -> None:
        class ScoringRouter:
            async def complete(self, request):  # noqa: ANN001
                class Response:
                    text = '{"score": 0.8, "reason": "accurate and complete"}'

                return Response()

        judge = LLMJudge(router=ScoringRouter())
        assert await judge.score(EvalCase(id="c", query="q"), "an answer") == pytest.approx(0.8)

    async def test_clamps_an_out_of_range_score(self) -> None:
        """A judge that returns 5 out of 5 must not silently become a 5.0 metric."""

        class OverconfidentRouter:
            async def complete(self, request):  # noqa: ANN001
                class Response:
                    text = '{"score": 5.0}'

                return Response()

        judge = LLMJudge(router=OverconfidentRouter())
        assert await judge.score(EvalCase(id="c", query="q"), "answer") == 1.0

    async def test_unparseable_response_scores_zero_rather_than_raising(self) -> None:
        class ChattyRouter:
            async def complete(self, request):  # noqa: ANN001
                class Response:
                    text = "Sure! I think this answer is pretty good overall."

                return Response()

        assert await LLMJudge(router=ChattyRouter()).score(
            EvalCase(id="c", query="q"), "answer"
        ) == 0.0


class TestRegressionGate:
    def _result(self, **metrics: float) -> SuiteResult:
        return SuiteResult(
            suite="s", results=(CaseResult(case_id="c", passed=True, scores=dict(metrics)),)
        )

    def test_a_drop_within_tolerance_passes(self) -> None:
        ok, failures = RegressionGate().check(
            self._result(recall_at_k=0.94), {"recall_at_k": 0.95}
        )
        assert ok and not failures

    def test_a_drop_beyond_tolerance_fails_and_names_the_numbers(self) -> None:
        ok, failures = RegressionGate().check(
            self._result(recall_at_k=0.60), {"recall_at_k": 0.95}
        )
        assert not ok
        assert "recall_at_k fell 0.350" in failures[0]

    def test_an_improvement_never_fails(self) -> None:
        ok, _ = RegressionGate().check(self._result(recall_at_k=0.99), {"recall_at_k": 0.50})
        assert ok

    def test_a_metric_absent_from_the_current_run_is_skipped_not_failed(self) -> None:
        """A suite that stopped reporting a metric is a suite change, not a quality regression."""
        ok, failures = RegressionGate().check(
            self._result(recall_at_k=0.95), {"recall_at_k": 0.95, "ndcg": 0.90}
        )
        assert ok and not failures

    def test_an_unlisted_metric_gets_the_default_tolerance(self) -> None:
        gate = RegressionGate()
        ok, _ = gate.check(self._result(groundedness=0.90), {"groundedness": 0.94})
        assert ok  # 0.04 < the 0.05 default
        ok, _ = gate.check(self._result(groundedness=0.80), {"groundedness": 0.94})
        assert not ok

    def test_baseline_round_trips_through_json(self) -> None:
        result = self._result(recall_at_k=0.9, ndcg=0.8)
        loaded = RegressionGate.load_baseline(RegressionGate.save_baseline(result))
        assert loaded["recall_at_k"] == pytest.approx(0.9)
        assert loaded["pass_rate"] == pytest.approx(1.0)

    def test_summarise_keys_by_suite(self) -> None:
        assert set(summarise([self._result(recall_at_k=1.0)])) == {"s"}
