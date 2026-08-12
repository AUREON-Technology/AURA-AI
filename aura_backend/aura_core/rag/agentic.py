"""Agentic and multimodal retrieval.

:class:`AgenticRAG` lets the model decide what to look up and in what order, for questions that
cannot be answered by one retrieval however well phrased -- "compare our refund policy with the
shipping policy and tell me which is stricter" needs two lookups and a comparison.

:class:`MultimodalRAG` restricts retrieval to content extracted from non-text sources. Read its
docstring before relying on it: the strategy is real, the extraction pipeline behind it is not
finished, and it is written to be honest about that rather than to appear complete.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from ..domain.knowledge import RetrievalResult, SourceType
from .base import (
    BaseStrategy,
    RetrievalOutcome,
    RetrievalRequest,
    StrategyTrace,
    fuse_results,
)
from .expansion import _generate, _split_lines

__all__ = ["AgenticRAG", "MultimodalRAG"]


class AgenticRAG(BaseStrategy):
    """Plan a short sequence of lookups, run them, and fuse the evidence.

    The model is asked to decompose the question into independent sub-questions. Those are retrieved
    concurrently and fused. It is not a general agent loop: no tool selection, no arbitrary
    iteration, no writes.

    That restraint is the design, not a shortcut. An open agent loop over a 7B local model
    produces long chains of low-quality tool calls, each costing seconds, and the tool registry
    already gates side-effecting tools behind capability checks and confirmation
    ([ADR-006](../../docs/architecture/006-safety-pipeline.md)). Retrieval decomposition is the part
    of "agentic" that reliably improves answers; letting the model drive tools during *retrieval*
    would put an injection-reachable planner in front of the guards rather than behind them.

    Bounded at ``max_steps`` sub-questions and one planning generation. Falls back to retrieving the
    original question when planning fails or yields nothing usable.
    """

    name = "agentic"

    def __init__(
        self,
        inner: Any,
        router: Any,
        *,
        max_steps: int = 3,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        self._inner = inner
        self._router = router
        self._max_steps = max_steps

    async def _plan(self, request: RetrievalRequest) -> tuple[list[str], int, list[str]]:
        """Return ``(sub_questions, generations, notes)``."""
        if request.max_generations <= 0:
            return [], 0, ["degraded: no generation budget"]

        prompt = (
            "Break this request into the separate things that must be looked up to answer it. "
            f"At most {self._max_steps}. If it only needs one lookup, reply with the original "
            "request unchanged. One per line, no numbering, no commentary."
            f"\n\nRequest: {request.query}"
        )
        try:
            text = await _generate(self._router, prompt, max_tokens=180, temperature=0.2)
        except Exception as exc:
            return [], 0, [f"planning failed: {type(exc).__name__}"]

        steps = _split_lines(text, limit=self._max_steps)
        if not steps:
            return [], 1, ["no usable plan"]
        return steps, 1, [f"steps={len(steps)}"]

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        steps, generations, notes = await self._plan(request)
        queries = steps or [request.query]
        if not steps:
            notes.append("retrieving the original request")

        outcomes = [await self._inner.retrieve(request.with_query(q)) for q in queries]
        fused = fuse_results([o.result for o in outcomes], limit=request.limit)
        fused = replace(fused, query=request.query, strategy=self.name)

        return RetrievalOutcome(
            result=fused,
            memories=tuple(m for o in outcomes for m in o.memories),
            graph_paths=tuple(p for o in outcomes for p in o.graph_paths),
            trace=StrategyTrace(
                name=self.name,
                generations=generations,
                candidates=fused.candidates_considered,
                returned=len(fused.chunks),
                notes=tuple(notes),
                children=tuple(o.trace for o in outcomes),
            ),
        )


class MultimodalRAG(BaseStrategy):
    """Retrieve only content that came from non-text sources.

    **What this does today.** Filters retrieval to chunks whose document metadata marks them as
    extracted from a non-text source -- a PDF page, a spreadsheet, an image caption -- using the
    ``modality`` metadata key that the ingestion pipeline writes. Where such content exists, this
    correctly finds it and nothing else, which is what a question like "what does the diagram on
    page 4 say" needs.

    **What is not built.** There is no OCR and no vision model in the ingestion path. Chunks
    therefore only carry a ``modality`` other than ``text`` if something else put it there. On a
    corpus ingested by the current pipeline this strategy will find nothing and say so honestly via
    ``is_empty_but_searched``.

    That is deliberate. The alternative -- implementing this over plain text and calling it
    multimodal -- would report a capability the platform does not have, and the first time someone
    asked about an image they would get a confident answer drawn from unrelated prose. The strategy
    is here so that finishing the extraction pipeline is a change to *ingestion* only, with the
    retrieval side already in place and already tested.

    See ``docs/reports/rag-strategies.md`` for what completing it requires.
    """

    name = "multimodal"

    #: Metadata values that mean "this text was extracted from something that was not text".
    NON_TEXT_MODALITIES = ("image", "table", "chart", "diagram", "pdf_page", "audio", "video")

    def __init__(
        self,
        inner: Any,
        *,
        modalities: Sequence[str] | None = None,
        fall_back_to_text: bool = True,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        self._inner = inner
        self._modalities = tuple(modalities or self.NON_TEXT_MODALITIES)
        self._fall_back_to_text = fall_back_to_text

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        notes: list[str] = []
        filters = dict(request.filters or {})
        # A list value means "any of" in both the in-memory and Qdrant adapters, so this is one
        # query rather than one per modality.
        filters["modality"] = list(self._modalities)

        outcome = await self._inner.retrieve(replace(request, filters=filters))
        notes.append(f"modalities={','.join(self._modalities)}")

        if not outcome.result.chunks:
            notes.append("no non-text content indexed")
            if self._fall_back_to_text:
                # Falling back is the right default for a corpus with no extracted media: the user
                # asked a question, and answering it from prose is better than refusing. The note
                # records that the answer is not actually multimodal, so nothing downstream can
                # claim it was.
                notes.append("fell back to text retrieval")
                text_outcome = await self._inner.retrieve(request)
                return RetrievalOutcome(
                    result=replace(text_outcome.result, strategy=self.name),
                    memories=text_outcome.memories,
                    graph_paths=text_outcome.graph_paths,
                    trace=StrategyTrace(
                        name=self.name,
                        candidates=outcome.trace.candidates + text_outcome.trace.candidates,
                        returned=text_outcome.trace.returned,
                        notes=tuple(notes),
                        children=(outcome.trace, text_outcome.trace),
                    ),
                )

        return RetrievalOutcome(
            result=replace(outcome.result, strategy=self.name),
            memories=outcome.memories,
            graph_paths=outcome.graph_paths,
            trace=StrategyTrace(
                name=self.name,
                candidates=outcome.trace.candidates,
                returned=outcome.trace.returned,
                notes=tuple(notes),
                children=(outcome.trace,),
            ),
        )
