"""Strategies that judge retrieval and decide what to do about it.

* :class:`CorrectiveRAG` -- grades the evidence; if it is thin, reformulate and retry with a
  different strategy; if it is still thin, say so rather than proceed.
* :class:`SelfRAG` -- decides whether retrieval was needed at all, and afterwards whether the
  evidence actually supports an answer.

Both exist to stop the same failure: a pipeline that always returns its top-k and always presents
it as relevant, so the model grounds a confident answer in whatever happened to rank highest. The
honest outcomes here are "I found nothing that answers this" and "retrieval was not needed", and
both are cheaper than a wrong answer.

**Grading is heuristic first, model second.** A lexical-overlap and score-distribution check costs
microseconds and catches the clear cases; a generation costs seconds on local hardware. The model
is consulted only when the heuristic is genuinely uncertain, which keeps the common path fast.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from ..domain.knowledge import RetrievalResult
from .base import BaseStrategy, RetrievalOutcome, RetrievalRequest, StrategyTrace

__all__ = ["EvidenceGrade", "grade_evidence", "CorrectiveRAG", "SelfRAG"]

_WORD = re.compile(r"[a-z0-9]+")

#: Words too common to indicate topical overlap. Deliberately short -- a long stop list starts
#: removing content words ("no", "not") that change what a passage means.
_STOP = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "were",
    "be", "been", "it", "this", "that", "with", "as", "at", "by", "from", "what", "which",
    "how", "when", "who", "do", "does", "did", "can", "could", "should", "would", "i", "you",
    "we", "they", "my", "our", "your",
})


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


class EvidenceGrade:
    """How well the retrieved evidence covers the question.

    Three outcomes rather than a score, because the caller's decision is three-way: proceed, retry
    differently, or stop. A float would require every caller to re-derive these thresholds.
    """

    SUFFICIENT = "sufficient"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT = "insufficient"


def grade_evidence(
    query: str,
    result: RetrievalResult,
    *,
    min_coverage: float = 0.35,
    strong_score: float = 0.55,
) -> tuple[str, str]:
    """Grade evidence with no model call. Returns ``(grade, reason)``.

    Three signals, in order of how much they can be trusted:

    1. **Nothing returned.** Unambiguous, and the reason depends on whether anything was
       considered: a corpus that was searched and yielded nothing is a different situation from a
       corpus that could not be searched.
    2. **Term coverage.** The fraction of the question's content words appearing anywhere in the
       retrieved text. Cheap and surprisingly discriminating: retrieval that misses the subject of
       the question almost always has low coverage, whatever its cosine score says.
    3. **Top score.** A strong top hit with mediocre coverage is usually a paraphrase, which is
       fine. Weak on both is not.

    Coverage is computed over the union of chunks, not per chunk. A question spanning two topics is
    legitimately answered by two passages that each cover half.
    """
    if not result.chunks:
        if result.candidates_considered > 0:
            return EvidenceGrade.INSUFFICIENT, "nothing cleared the relevance floor"
        return EvidenceGrade.INSUFFICIENT, "no candidates found"

    wanted = _terms(query)
    if not wanted:
        # A query of only stop words carries no retrievable content; the score is all there is.
        grade = (
            EvidenceGrade.SUFFICIENT
            if result.top_score >= strong_score
            else EvidenceGrade.AMBIGUOUS
        )
        return grade, "query has no content terms"

    covered = set()
    for retrieved in result.chunks:
        covered |= _terms(retrieved.chunk.content) & wanted
    coverage = len(covered) / len(wanted)

    if coverage >= min_coverage and result.top_score >= strong_score:
        return EvidenceGrade.SUFFICIENT, f"coverage={coverage:.2f} top={result.top_score:.2f}"
    if coverage < min_coverage and result.top_score < strong_score:
        return EvidenceGrade.INSUFFICIENT, f"coverage={coverage:.2f} top={result.top_score:.2f}"
    return EvidenceGrade.AMBIGUOUS, f"coverage={coverage:.2f} top={result.top_score:.2f}"


class CorrectiveRAG(BaseStrategy):
    """Grade the evidence, and retry differently when it is thin.

    The escalation is ordered by cost, not by preference:

    1. Retrieve with the inner strategy.
    2. Grade it heuristically. Sufficient -> return.
    3. Insufficient or ambiguous -> retry with ``fallback`` (typically Multi-Query or HyDE, which
       attack vocabulary mismatch -- the most common cause of a thin first pass).
    4. Grade the retry. Keep whichever attempt graded better; on a tie keep the retry, since it
       searched more broadly.
    5. Still insufficient -> return the evidence *and* the grade. The caller decides whether to
       answer with a caveat or decline. This strategy never fabricates and never silently drops the
       evidence it did find: a partial answer with a stated gap is more useful than a refusal, and
       the ``insufficient`` note is what lets the generator say so.

    It never loops. A second reformulation of a query the corpus does not cover finds the same
    nothing, having spent another generation to do it.
    """

    name = "corrective"

    def __init__(
        self,
        inner: Any,
        fallback: Any = None,
        *,
        min_coverage: float = 0.35,
        strong_score: float = 0.55,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        self._inner = inner
        self._fallback = fallback
        self._min_coverage = min_coverage
        self._strong_score = strong_score

    def _grade(self, query: str, result: RetrievalResult) -> tuple[str, str]:
        return grade_evidence(
            query,
            result,
            min_coverage=self._min_coverage,
            strong_score=self._strong_score,
        )

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        first = await self._inner.retrieve(request)
        grade, reason = self._grade(request.query, first.result)
        notes = [f"{grade}: {reason}"]
        children = [first.trace]

        if grade == EvidenceGrade.SUFFICIENT or self._fallback is None:
            if self._fallback is None and grade != EvidenceGrade.SUFFICIENT:
                notes.append("no fallback configured")
            return RetrievalOutcome(
                result=replace(first.result, strategy=self.name),
                memories=first.memories,
                graph_paths=first.graph_paths,
                trace=StrategyTrace(
                    name=self.name,
                    candidates=first.trace.candidates,
                    returned=first.trace.returned,
                    notes=tuple(notes),
                    children=tuple(children),
                ),
            )

        notes.append("retrying with fallback")
        second = await self._fallback.retrieve(request)
        children.append(second.trace)
        second_grade, second_reason = self._grade(request.query, second.result)
        notes.append(f"retry {second_grade}: {second_reason}")

        ranking = {
            EvidenceGrade.SUFFICIENT: 2,
            EvidenceGrade.AMBIGUOUS: 1,
            EvidenceGrade.INSUFFICIENT: 0,
        }
        # `>=` keeps the retry on a tie: it searched more phrasings, so its ranking is better
        # supported even when the grade is identical.
        best, best_grade = (
            (second, second_grade)
            if ranking[second_grade] >= ranking[grade]
            else (first, grade)
        )
        if best is second:
            notes.append("kept retry")
        else:
            notes.append("kept first attempt")

        if best_grade == EvidenceGrade.INSUFFICIENT:
            # Said explicitly so the generator can convey it. This is the honest-uncertainty path
            # the brief asks for, and it depends on the evidence coming back rather than being
            # discarded.
            notes.append("insufficient evidence after retry")

        return RetrievalOutcome(
            result=replace(best.result, strategy=self.name),
            memories=best.memories,
            graph_paths=best.graph_paths,
            trace=StrategyTrace(
                name=self.name,
                candidates=first.trace.candidates + second.trace.candidates,
                returned=best.trace.returned,
                notes=tuple(notes),
                children=tuple(children),
            ),
        )


class SelfRAG(BaseStrategy):
    """Decide whether to retrieve at all, then whether the result supports an answer.

    Two reflective decisions around the inner strategy:

    **Before.** Not every question needs the corpus. "Rewrite this paragraph", "what is 17 times
    23", "translate this" are self-contained; retrieving for them wastes latency and, worse, injects
    unrelated passages that the model may try to use. The check is lexical, not a generation --
    spending a model call to decide whether to spend a model call is a poor trade on local hardware.

    **After.** Grade what came back and record whether the answer would be grounded. The grade
    travels in the trace rather than being acted on here, because this strategy's job is to report
    the state of the evidence, not to decide the wording. The generator applies it.

    Deliberately does *not* emit a numeric confidence. The brief is explicit that a confidence
    figure must not be shown unless it is calibrated, and nothing here is calibrated against
    outcomes -- a plausible-looking 0.82 would be worse than no number, because users trust
    decimals.
    """

    name = "self_rag"

    #: Openings and verbs that indicate the request operates on text the user supplied rather than
    #: on knowledge the system holds.
    _SELF_CONTAINED = (
        "rewrite", "translate", "summarise this", "summarize this", "reword", "rephrase",
        "fix this", "format", "convert", "calculate", "compute", "what is the sum",
        "capitalise", "capitalize", "proofread", "shorten", "lengthen", "make this",
    )

    def __init__(
        self,
        inner: Any,
        *,
        skip_retrieval_when_self_contained: bool = True,
        min_coverage: float = 0.35,
        strong_score: float = 0.55,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        self._inner = inner
        self._skip = skip_retrieval_when_self_contained
        self._min_coverage = min_coverage
        self._strong_score = strong_score

    def needs_retrieval(self, query: str) -> tuple[bool, str]:
        lowered = query.lower().strip()
        if not lowered:
            return False, "empty query"
        for marker in self._SELF_CONTAINED:
            if lowered.startswith(marker) or f" {marker}" in lowered:
                return False, f"self-contained: {marker!r}"
        # A purely arithmetic question needs no corpus. Checked after the verb markers so
        # "calculate the refund per the policy" still retrieves.
        if re.fullmatch(r"[\d\s+\-*/().^%=]+\??", lowered):
            return False, "arithmetic only"
        return True, "knowledge question"

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        notes: list[str] = []
        needed, why = self.needs_retrieval(request.query)
        notes.append(why)

        if self._skip and not needed:
            # candidates stays 0, so `is_empty_but_searched` is False -- the caller can tell "we
            # did not look" from "we looked and found nothing", which are different things to say
            # to a user.
            return RetrievalOutcome(
                result=RetrievalResult(query=request.query, strategy=self.name),
                trace=StrategyTrace(name=self.name, notes=tuple(notes + ["retrieval skipped"])),
            )

        inner = await self._inner.retrieve(request)
        grade, reason = grade_evidence(
            request.query,
            inner.result,
            min_coverage=self._min_coverage,
            strong_score=self._strong_score,
        )
        notes.append(f"grounding {grade}: {reason}")
        if grade == EvidenceGrade.INSUFFICIENT and inner.result.chunks:
            # Evidence exists but does not cover the question. Worth distinguishing from "nothing
            # found": the generator should hedge, not decline.
            notes.append("answer would be weakly grounded")

        return RetrievalOutcome(
            result=replace(inner.result, strategy=self.name),
            memories=inner.memories,
            graph_paths=inner.graph_paths,
            trace=StrategyTrace(
                name=self.name,
                candidates=inner.trace.candidates,
                returned=inner.trace.returned,
                notes=tuple(notes),
                children=(inner.trace,),
            ),
        )
