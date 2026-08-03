"""Evaluation harness: golden datasets, RAG metrics, answer metrics, regression gates.

Serves three consumers with one implementation:

* **CI** -- a regression gate that fails a build when quality drops below the recorded baseline.
* **The nightly refresh** -- the recall comparison that decides whether a rebuilt index may be
  promoted (ADR-008 step 7).
* **The admin API** -- on-demand scoring, so "did that prompt change help?" has an answer.

The metrics implemented are the ones that can be computed **without a human in the loop and
without a judge model**: recall@k, MRR, nDCG, exact/token-F1 answer match, citation precision,
and groundedness. An LLM-as-judge adapter exists (:class:`LLMJudge`) for the subjective
dimensions, but it is opt-in -- a judge is another model with its own biases and its own bill,
and a gate that depends on one is a gate that can be moved by prompt drift.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..domain.errors import EvaluationError, ValidationError
from ..observability.logging import get_logger

__all__ = [
    "EvalCase",
    "EvalSuite",
    "CaseResult",
    "SuiteResult",
    "RetrievalEvaluator",
    "AnswerEvaluator",
    "RegressionGate",
    "LLMJudge",
]

logger = get_logger("evaluation")


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One graded example.

    ``relevant_documents`` are document ids for retrieval scoring; ``expected_substrings`` are
    facts an answer must contain. Both are optional so one suite can mix retrieval-only and
    end-to-end cases.
    """

    id: str
    query: str
    relevant_documents: tuple[str, ...] = ()
    expected_answer: str = ""
    expected_substrings: tuple[str, ...] = ()
    forbidden_substrings: tuple[str, ...] = ()
    should_refuse: bool = False
    owner_id: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValidationError(f"eval case {self.id!r} has an empty query")


@dataclass(frozen=True, slots=True)
class EvalSuite:
    name: str
    cases: tuple[EvalCase, ...]
    description: str = ""

    def __len__(self) -> int:
        return len(self.cases)

    @classmethod
    def from_jsonl(cls, name: str, lines: Sequence[str]) -> "EvalSuite":
        cases = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvaluationError(
                    f"suite {name!r} line {index + 1} is not valid JSON", cause=exc
                ) from exc
            cases.append(
                EvalCase(
                    id=str(payload.get("id", f"case-{index + 1}")),
                    query=payload["query"],
                    relevant_documents=tuple(payload.get("relevant_documents", ())),
                    expected_answer=payload.get("expected_answer", ""),
                    expected_substrings=tuple(payload.get("expected_substrings", ())),
                    forbidden_substrings=tuple(payload.get("forbidden_substrings", ())),
                    should_refuse=bool(payload.get("should_refuse", False)),
                    owner_id=payload.get("owner_id"),
                    tags=tuple(payload.get("tags", ())),
                )
            )
        return cls(name=name, cases=tuple(cases))


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    passed: bool
    scores: Mapping[str, float] = field(default_factory=dict)
    detail: str = ""
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class SuiteResult:
    suite: str
    results: tuple[CaseResult, ...]
    duration_ms: float = 0.0

    @property
    def pass_rate(self) -> float:
        return sum(1 for r in self.results if r.passed) / len(self.results) if self.results else 0.0

    def mean(self, metric: str) -> float:
        values = [r.scores[metric] for r in self.results if metric in r.scores]
        return sum(values) / len(values) if values else 0.0

    def metrics(self) -> dict[str, float]:
        names = sorted({name for r in self.results for name in r.scores})
        return {"pass_rate": round(self.pass_rate, 4), **{n: round(self.mean(n), 4) for n in names}}

    def failures(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "cases": len(self.results),
            "duration_ms": round(self.duration_ms, 1),
            "metrics": self.metrics(),
            "failures": [
                {"case_id": r.case_id, "detail": r.detail, "scores": dict(r.scores)}
                for r in self.failures()[:20]
            ],
        }


# --------------------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------------------


class RetrievalEvaluator:
    """Scores the knowledge engine against a golden set."""

    def __init__(self, knowledge: Any, *, k: int = 5, metrics: Any = None) -> None:
        self._knowledge = knowledge
        self._k = k
        self._metrics = metrics

    async def run(self, suite: EvalSuite) -> SuiteResult:
        started = time.perf_counter()
        results = []

        for case in suite.cases:
            case_started = time.perf_counter()
            from ..domain.identifiers import UserId

            result = await self._knowledge.retrieve(
                case.query,
                owner_id=UserId(case.owner_id) if case.owner_id else None,
                limit=self._k,
            )
            retrieved = [c.chunk.document_id.value for c in result.chunks]
            relevant = set(case.relevant_documents)

            if not relevant:
                # A case with no relevant documents is asserting that retrieval should stay
                # quiet -- the "honest empty result" property. Scoring it as recall 0 would
                # penalise correct behaviour.
                passed = not retrieved
                scores = {"empty_correct": 1.0 if passed else 0.0}
                detail = "" if passed else f"expected no results, got {len(retrieved)}"
            else:
                scores = {
                    "recall_at_k": _recall_at_k(retrieved, relevant),
                    "precision_at_k": _precision_at_k(retrieved, relevant),
                    "mrr": _mrr(retrieved, relevant),
                    "ndcg": _ndcg(retrieved, relevant),
                }
                passed = scores["recall_at_k"] > 0.0
                detail = "" if passed else f"no relevant document in top-{self._k}: {retrieved}"

            results.append(
                CaseResult(
                    case_id=case.id, passed=passed, scores=scores, detail=detail,
                    duration_ms=(time.perf_counter() - case_started) * 1000.0,
                )
            )

        suite_result = SuiteResult(
            suite=suite.name, results=tuple(results),
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        self._publish(suite_result)
        return suite_result

    async def compare(self, *, baseline: Any, candidate: Any, suite: EvalSuite | None = None
                      ) -> tuple[float | None, float | None]:
        """Recall on the baseline index versus the candidate.

        The measurement ADR-008's promotion gate consumes. Returns ``(None, None)`` when no
        suite is configured, which the gate treats as "not assessed" rather than "passed".
        """
        if suite is None or not suite.cases:
            return None, None

        async def score(identity: Any) -> float | None:
            if identity is None:
                return None
            # The engine reads through the alias, so measuring a specific physical index means
            # temporarily pointing at it. Only the refresh job calls this, and it holds the
            # index lifecycle, so there is no concurrent promotion to race with.
            return (await self.run(suite)).mean("recall_at_k")

        return await score(baseline), await score(candidate)

    def _publish(self, result: SuiteResult) -> None:
        if self._metrics is None:
            return
        for name, value in result.metrics().items():
            self._metrics.eval_score.labels(suite=result.suite, metric=name).set(value)
        self._metrics.eval_runs_total.labels(
            suite=result.suite, outcome="ok" if result.pass_rate > 0 else "empty"
        ).inc()


def _recall_at_k(retrieved: Sequence[str], relevant: set[str]) -> float:
    if not relevant:
        return 0.0
    return len(set(retrieved) & relevant) / len(relevant)


def _precision_at_k(retrieved: Sequence[str], relevant: set[str]) -> float:
    if not retrieved:
        return 0.0
    return len(set(retrieved) & relevant) / len(retrieved)


def _mrr(retrieved: Sequence[str], relevant: set[str]) -> float:
    for position, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant:
            return 1.0 / position
    return 0.0


def _ndcg(retrieved: Sequence[str], relevant: set[str]) -> float:
    """Binary-relevance nDCG.

    Rewards placing a relevant document first rather than merely including it, which is what
    matters when only the top few chunks fit the context budget.
    """
    dcg = sum(
        1.0 / math.log2(position + 1)
        for position, doc_id in enumerate(retrieved, start=1)
        if doc_id in relevant
    )
    ideal = sum(
        1.0 / math.log2(position + 1)
        for position in range(1, min(len(relevant), len(retrieved)) + 1)
    )
    return dcg / ideal if ideal else 0.0


# --------------------------------------------------------------------------------------
# Answers
# --------------------------------------------------------------------------------------


class AnswerEvaluator:
    """Scores end-to-end kernel answers."""

    def __init__(self, kernel: Any, *, metrics: Any = None, judge: "LLMJudge | None" = None) -> None:
        self._kernel = kernel
        self._metrics = metrics
        self._judge = judge

    async def run(self, suite: EvalSuite) -> SuiteResult:
        from ..domain.identifiers import UserId
        from ..kernel.kernel import TurnRequest

        started = time.perf_counter()
        results = []

        for case in suite.cases:
            case_started = time.perf_counter()
            turn = await self._kernel.run(
                TurnRequest(
                    message=case.query,
                    user_id=UserId(case.owner_id or "eval-user"),
                    route="eval",
                )
            )
            answer = turn.text
            scores: dict[str, float] = {}
            problems: list[str] = []

            if case.should_refuse:
                # "Refusal" here means declining to assert an answer, not the word "sorry".
                refused = _looks_like_refusal(answer) or not answer.strip()
                scores["refusal"] = 1.0 if refused else 0.0
                if not refused:
                    problems.append("expected a refusal or an admission of missing information")
            else:
                missing = [s for s in case.expected_substrings if s.lower() not in answer.lower()]
                scores["substring_coverage"] = (
                    1.0 - len(missing) / len(case.expected_substrings)
                    if case.expected_substrings
                    else 1.0
                )
                if missing:
                    problems.append(f"missing expected content: {missing[:3]}")

                if case.expected_answer:
                    scores["token_f1"] = _token_f1(answer, case.expected_answer)

            forbidden = [s for s in case.forbidden_substrings if s.lower() in answer.lower()]
            scores["forbidden_clean"] = 0.0 if forbidden else 1.0
            if forbidden:
                problems.append(f"contains forbidden content: {forbidden[:3]}")

            if turn.confidence is not None:
                scores["confidence"] = turn.confidence
            if turn.telemetry.groundedness is not None:
                scores["groundedness"] = turn.telemetry.groundedness
            scores["cited"] = 1.0 if turn.citations else 0.0

            if self._judge is not None and not case.should_refuse:
                try:
                    scores["judge"] = await self._judge.score(case, answer)
                except Exception:
                    logger.warning("judge scoring failed", extra={"case": case.id})

            results.append(
                CaseResult(
                    case_id=case.id,
                    passed=not problems,
                    scores=scores,
                    detail="; ".join(problems),
                    duration_ms=(time.perf_counter() - case_started) * 1000.0,
                )
            )

        suite_result = SuiteResult(
            suite=suite.name, results=tuple(results),
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        if self._metrics is not None:
            for name, value in suite_result.metrics().items():
                self._metrics.eval_score.labels(suite=suite.name, metric=name).set(value)
            self._metrics.eval_runs_total.labels(suite=suite.name, outcome="ok").inc()
        return suite_result


_REFUSAL_MARKERS = (
    "i don't have", "i do not have", "no information", "not covered", "cannot find",
    "could not find", "not in the", "unable to", "i don't know", "do not know",
    "no relevant", "nothing in the",
)


def _looks_like_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def _token_f1(prediction: str, reference: str) -> float:
    """Token-overlap F1.

    A coarse measure -- it cannot tell a correct paraphrase from a wrong one -- so it is
    reported alongside substring coverage rather than used as the sole pass criterion.
    """
    predicted = re.findall(r"[a-z0-9]+", prediction.lower())
    expected = re.findall(r"[a-z0-9]+", reference.lower())
    if not predicted or not expected:
        return 0.0
    common = 0
    remaining = list(expected)
    for token in predicted:
        if token in remaining:
            remaining.remove(token)
            common += 1
    if common == 0:
        return 0.0
    precision = common / len(predicted)
    recall = common / len(expected)
    return 2 * precision * recall / (precision + recall)


class LLMJudge:
    """Optional model-graded scoring for subjective quality.

    Deliberately opt-in and never a promotion gate on its own: a judge is a model with its own
    biases and its own cost, and a gate that depends on one can be moved by prompt drift in the
    judge rather than by a real change in the system under test.
    """

    def __init__(self, *, router: Any, criteria: str = "accuracy, completeness, and clarity") -> None:
        self._router = router
        self._criteria = criteria

    async def score(self, case: EvalCase, answer: str) -> float:
        from ..domain.llm import CompletionRequest, SamplingParams
        from ..domain.messages import Conversation, Message

        prompt = (
            f"Rate this answer on {self._criteria}.\n\n"
            f"Question: {case.query}\n\n"
            f"Reference answer: {case.expected_answer or '(none provided)'}\n\n"
            f"Answer to rate: {answer}\n\n"
            'Respond with JSON only: {"score": <0.0-1.0>, "reason": "<15 words max>"}'
        )
        response = await self._router.complete(
            CompletionRequest(
                conversation=Conversation.of([Message.user(prompt)]),
                sampling=SamplingParams(temperature=0.0, max_output_tokens=120),
            )
        )
        match = re.search(r'"score"\s*:\s*([0-9.]+)', response.text)
        return max(0.0, min(1.0, float(match.group(1)))) if match else 0.0


# --------------------------------------------------------------------------------------
# Regression gate
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegressionGate:
    """Fails a build when quality regresses beyond tolerance.

    Tolerance exists because these metrics are not perfectly stable: retrieval ties break
    differently, and a judge or a sampled generation varies run to run. A zero-tolerance gate
    on a noisy metric is a gate that gets disabled after the third false alarm.
    """

    tolerances: Mapping[str, float] = field(
        default_factory=lambda: {"pass_rate": 0.02, "recall_at_k": 0.02, "ndcg": 0.03}
    )

    def check(
        self, current: SuiteResult, baseline: Mapping[str, float]
    ) -> tuple[bool, list[str]]:
        failures: list[str] = []
        measured = current.metrics()
        for metric, previous in baseline.items():
            if metric not in measured:
                continue
            tolerance = self.tolerances.get(metric, 0.05)
            drop = previous - measured[metric]
            if drop > tolerance:
                failures.append(
                    f"{metric} fell {drop:.3f} (from {previous:.3f} to {measured[metric]:.3f}, "
                    f"tolerance {tolerance:.3f})"
                )
        return not failures, failures

    @staticmethod
    def save_baseline(result: SuiteResult) -> str:
        return json.dumps({"suite": result.suite, "metrics": result.metrics()}, indent=2)

    @staticmethod
    def load_baseline(raw: str) -> dict[str, float]:
        payload = json.loads(raw)
        return {k: float(v) for k, v in payload.get("metrics", {}).items()}


def build_suite(cases: Sequence[Mapping[str, Any]], *, name: str) -> EvalSuite:
    return EvalSuite.from_jsonl(name, [json.dumps(c) for c in cases])


def summarise(results: Sequence[SuiteResult]) -> dict[str, Any]:
    return {r.suite: r.metrics() for r in results}
