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

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
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
    "AgentEvaluator",
    "AgentCase",
    "AgentCaseResult",
    "AgentSuiteResult",
    "load_agent_cases",
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
    """Binary-relevance nDCG, with relevance per *document*.

    Rewards placing a relevant document first rather than merely including it, which is what
    matters when only the top few chunks fit the context budget.

    ``retrieved`` holds one document id per returned *chunk*, so a document can appear several
    times. Only its first (highest-ranked) chunk earns gain; later chunks of an already-found
    document keep their slot -- they did use context budget -- but add nothing. The ideal ranking
    is the same unit: one gain per distinct relevant document, up to the slots returned. Counting
    every chunk against a per-document ideal is what let a threshold-0 run report nDCG 1.19.
    """
    seen: set[str] = set()
    dcg = 0.0
    for position, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant and doc_id not in seen:
            seen.add(doc_id)
            dcg += 1.0 / math.log2(position + 1)
    ideal = sum(
        1.0 / math.log2(position + 1)
        for position in range(1, min(len(relevant), len(retrieved)) + 1)
    )
    return dcg / ideal if ideal else 0.0


# --------------------------------------------------------------------------------------
# Answers
# --------------------------------------------------------------------------------------


#: How an evaluator drives the kernel. ``run`` is ``/api/v3/chat``; ``stream`` is
#: ``/api/v3/chat/stream``, the only path the app uses -- and a different pipeline (no reasoner,
#: no cache, revisions after the fact). Measuring only ``run`` measured what users never get
#: (audit C4).
TRANSPORTS = ("run", "stream")


@dataclass(frozen=True, slots=True)
class _EvalTurn:
    """One turn as an evaluator scores it, whichever transport produced it."""

    text: str
    citations: tuple[Any, ...] = ()
    confidence: float | None = None
    #: Only ``run`` reports it: the stream's ``done`` frame carries no groundedness score.
    groundedness: float | None = None
    #: ``(tool name, succeeded)`` for every tool the turn ran.
    tools: tuple[tuple[str, bool], ...] = ()
    degraded_reasons: tuple[str, ...] = ()


async def _take_turn(kernel: Any, request: Any, transport: str) -> _EvalTurn:
    """Drive one turn over ``transport`` and collect what an evaluator scores."""
    if transport == "run":
        result = await kernel.run(request)
        telemetry = result.telemetry
        return _EvalTurn(
            text=result.text,
            citations=tuple(result.citations),
            confidence=result.confidence,
            groundedness=telemetry.groundedness,
            tools=tuple((t.name, bool(t.ok)) for t in telemetry.tools),
            degraded_reasons=tuple(telemetry.degraded_reasons),
        )
    if transport != "stream":
        raise ValidationError(f"unknown eval transport {transport!r}; expected one of {TRANSPORTS}")
    tokens: list[str] = []
    text: str | None = None
    citations: tuple[Any, ...] = ()
    done: Mapping[str, Any] = {}
    async for frame in kernel.stream(request):
        kind = frame.get("type")
        if kind == "token":
            tokens.append(str(frame.get("text", "")))
        elif kind == "revision":
            # A revision replaces everything streamed so far, as it does on screen.
            text = str(frame.get("text", ""))
        elif kind == "citations":
            citations = tuple(frame.get("citations") or ())
        elif kind == "error":
            raise EvaluationError(
                f"streamed turn failed: {frame.get('error')}",
                details={"message": frame.get("message")},
            )
        elif kind == "done":
            done = frame
    return _EvalTurn(
        text=text if text is not None else "".join(tokens),
        citations=tuple(done.get("citations") or citations),
        confidence=done.get("confidence"),
        tools=tuple((str(t.get("name")), bool(t.get("ok"))) for t in done.get("tools") or ()),
        degraded_reasons=tuple(done.get("degraded_reasons") or ()),
    )


class AnswerEvaluator:
    """Scores end-to-end kernel answers.

    ``transport="stream"`` scores what the app shows (audit C4). The default stays ``run``
    because the recorded gate baselines were measured on it, and groundedness is reported only
    there; a baseline measured on one transport must not gate the other.
    """

    def __init__(
        self,
        kernel: Any,
        *,
        metrics: Any = None,
        judge: "LLMJudge | None" = None,
        transport: str = "run",
    ) -> None:
        if transport not in TRANSPORTS:
            raise ValidationError(f"unknown eval transport {transport!r}")
        self._kernel = kernel
        self._metrics = metrics
        self._judge = judge
        self._transport = transport

    async def run(self, suite: EvalSuite) -> SuiteResult:
        from ..domain.identifiers import UserId
        from ..kernel.kernel import TurnRequest

        started = time.perf_counter()
        results = []

        for case in suite.cases:
            case_started = time.perf_counter()
            turn = await _take_turn(
                self._kernel,
                TurnRequest(
                    message=case.query,
                    user_id=UserId(case.owner_id or "eval-user"),
                    route="eval",
                ),
                self._transport,
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
            if turn.groundedness is not None:
                scores["groundedness"] = turn.groundedness
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


# --------------------------------------------------------------------------------------
# Agent Task Evaluation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentCase:
    """One agent task evaluation case.

    Extends EvalCase with agent-specific expectations: expected tools, success criteria,
    expected substrings, and maximum allowed turns.
    """

    id: str
    query: str
    expected_tools: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    expected_substrings: tuple[str, ...] = ()
    max_turns: int = 10
    owner_id: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValidationError(f"agent case {self.id!r} has an empty query")


@dataclass(frozen=True, slots=True)
class AgentCaseResult:
    case_id: str
    success: bool
    tools_used: tuple[str, ...] = ()
    tool_success_rate: float = 0.0
    #: Fraction of ``case.expected_tools`` actually reached for in ``tools_used``. ``1.0`` when
    #: the case declares no ``expected_tools`` -- a case that never named which tools mattered
    #: cannot be marked down for a "wrong" tool choice it never specified.
    tool_accuracy: float = 1.0
    task_completed: bool = False
    turns: int = 0
    duration_ms: float = 0.0
    detail: str = ""
    trace: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class AgentSuiteResult:
    suite: str
    results: tuple[AgentCaseResult, ...]
    duration_ms: float = 0.0

    @property
    def pass_rate(self) -> float:
        return sum(1 for r in self.results if r.success) / len(self.results) if self.results else 0.0

    @property
    def avg_tool_success_rate(self) -> float:
        rates = [r.tool_success_rate for r in self.results]
        return sum(rates) / len(rates) if rates else 0.0

    @property
    def avg_tool_accuracy(self) -> float:
        rates = [r.tool_accuracy for r in self.results]
        return sum(rates) / len(rates) if rates else 0.0

    @property
    def task_completion_rate(self) -> float:
        completed = sum(1 for r in self.results if r.task_completed)
        return completed / len(self.results) if self.results else 0.0

    def metrics(self) -> dict[str, float]:
        names = {"pass_rate", "tool_success_rate", "task_completion_rate", "avg_turns", "avg_duration_ms"}
        return {
            "pass_rate": round(self.pass_rate, 4),
            "tool_success_rate": round(self.avg_tool_success_rate, 4),
            "tool_accuracy": round(self.avg_tool_accuracy, 4),
            "task_completion_rate": round(self.task_completion_rate, 4),
            "avg_turns": round(sum(r.turns for r in self.results) / len(self.results), 2) if self.results else 0.0,
            "avg_duration_ms": round(sum(r.duration_ms for r in self.results) / len(self.results), 1) if self.results else 0.0,
        }

    def failures(self) -> list[AgentCaseResult]:
        return [r for r in self.results if not r.success]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "cases": len(self.results),
            "duration_ms": round(self.duration_ms, 1),
            "metrics": self.metrics(),
            "failures": [
                {"case_id": r.case_id, "detail": r.detail, "tools_used": list(r.tools_used), "turns": r.turns}
                for r in self.failures()[:20]
            ],
        }


class AgentEvaluator:
    """Evaluates end-to-end agent task execution through the AI Kernel.

    Measures: task completion, tool selection accuracy, tool success rate,
    recovery from failures, and adherence to expected tool sequences.
    """

    def __init__(
        self,
        kernel: Any,
        *,
        metrics: Any = None,
        max_turns: int = 10,
        timeout_s: float = 120.0,
        transport: str = "run",
    ) -> None:
        if transport not in TRANSPORTS:
            raise ValidationError(f"unknown eval transport {transport!r}")
        self._kernel = kernel
        self._metrics = metrics
        self._max_turns = max_turns
        self._timeout_s = timeout_s
        self._transport = transport

    async def run(self, cases: Sequence[AgentCase]) -> AgentSuiteResult:
        from ..domain.identifiers import UserId
        from ..kernel.kernel import TurnRequest

        started = time.perf_counter()
        results = []

        for case in cases:
            case_started = time.perf_counter()
            tools_used = []
            tool_success_count = 0
            tool_total = 0
            task_completed = False
            trace = []
            turns = 0
            answer_text = ""

            try:
                for turn_num in range(case.max_turns):
                    turns = turn_num + 1
                    turn = await asyncio.wait_for(
                        _take_turn(
                            self._kernel,
                            TurnRequest(
                                message=case.query if turn_num == 0 else "Continue",
                                user_id=UserId(case.owner_id or "eval-user"),
                                route="eval_agent",
                            ),
                            self._transport,
                        ),
                        timeout=self._timeout_s,
                    )
                    answer_text = f"{answer_text} {turn.text}" if answer_text else turn.text

                    # `TurnResult.tool_calls` is a tuple of names; this used to read
                    # `tc.tool_name`/`tc.args` off it, so any turn that ran a tool raised
                    # AttributeError and the case was scored as an error. The per-tool outcome
                    # comes from the turn's own tool telemetry (or the stream's `done.tools`).
                    trace.append({
                        "turn": turn_num + 1,
                        "text": turn.text[:500],
                        "tool_calls": [{"tool": name, "ok": ok} for name, ok in turn.tools],
                    })

                    for name, ok in turn.tools:
                        tools_used.append(name)
                        tool_total += 1
                        if ok:
                            tool_success_count += 1

                    if self._is_task_complete(answer_text, case):
                        task_completed = True
                        break

                duration_ms = (time.perf_counter() - case_started) * 1000.0
                tool_success_rate = tool_success_count / tool_total if tool_total > 0 else 1.0
                tool_accuracy = self._tool_accuracy(case, tools_used)

                success = task_completed and tool_success_rate > 0.8
                detail = "" if success else f"Task not completed or tool success rate {tool_success_rate:.2f}"

                results.append(
                    AgentCaseResult(
                        case_id=case.id,
                        success=success,
                        tools_used=tuple(tools_used),
                        tool_success_rate=tool_success_rate,
                        tool_accuracy=tool_accuracy,
                        task_completed=task_completed,
                        turns=turns,
                        duration_ms=duration_ms,
                        detail=detail,
                        trace=tuple(trace),
                    )
                )

            except asyncio.TimeoutError:
                results.append(
                    AgentCaseResult(
                        case_id=case.id,
                        success=False,
                        tools_used=tuple(tools_used),
                        tool_success_rate=tool_success_count / tool_total if tool_total > 0 else 0.0,
                        tool_accuracy=self._tool_accuracy(case, tools_used),
                        task_completed=False,
                        turns=turns,
                        duration_ms=(time.perf_counter() - case_started) * 1000.0,
                        detail=f"Timeout after {turns} turns",
                        trace=tuple(trace),
                    )
                )
            except Exception as e:
                results.append(
                    AgentCaseResult(
                        case_id=case.id,
                        success=False,
                        tools_used=tuple(tools_used),
                        tool_success_rate=tool_success_count / tool_total if tool_total > 0 else 0.0,
                        tool_accuracy=self._tool_accuracy(case, tools_used),
                        task_completed=False,
                        turns=turns,
                        duration_ms=(time.perf_counter() - case_started) * 1000.0,
                        detail=f"Error: {type(e).__name__}: {e}",
                        trace=tuple(trace),
                    )
                )

        suite_result = AgentSuiteResult(
            suite="agent_tasks", results=tuple(results), duration_ms=(time.perf_counter() - started) * 1000.0
        )
        if self._metrics is not None:
            for name, value in suite_result.metrics().items():
                self._metrics.eval_score.labels(suite=suite_result.suite, metric=name).set(value)
            self._metrics.eval_runs_total.labels(suite=suite_result.suite, outcome="ok").inc()
        return suite_result

    @staticmethod
    def _tool_accuracy(case: AgentCase, tools_used: Sequence[str]) -> float:
        """Fraction of ``case.expected_tools`` the run actually reached for.

        A recall-style measure over the *set* of tools used across every turn, not a sequence
        match -- a case naming ``("search_knowledge", "create_task")`` is checking that both were
        used somewhere in solving the task, not that they ran in that exact order or exclusively.
        ``1.0`` when the case names no expected tools: nothing was specified to get wrong.
        """
        if not case.expected_tools:
            return 1.0
        expected = set(case.expected_tools)
        used = set(tools_used)
        return len(expected & used) / len(expected)

    def _is_task_complete(self, text: str, case: AgentCase) -> bool:
        text_lower = text.lower()
        if case.expected_substrings:
            return all(substring.lower() in text_lower for substring in case.expected_substrings)
        if case.success_criteria:
            return all(criterion.lower() in text_lower for criterion in case.success_criteria)
        return any(marker in text_lower for marker in ("done", "complete", "created", "finished", "success"))


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


def load_agent_cases(path: str | Path) -> list[AgentCase]:
    """Load agent evaluation cases from a JSONL file."""
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for index, line in enumerate(f):
            if not line.strip():
                continue
            payload = json.loads(line)
            cases.append(
                AgentCase(
                    id=str(payload.get("id", f"case-{index + 1}")),
                    query=payload["query"],
                    expected_tools=tuple(payload.get("expected_tools", ())),
                    success_criteria=tuple(payload.get("success_criteria", ())),
                    expected_substrings=tuple(payload.get("expected_substrings", ())),
                    max_turns=int(payload.get("max_turns", 10)),
                    owner_id=payload.get("owner_id"),
                    tags=tuple(payload.get("tags", ())),
                )
            )
    return cases
