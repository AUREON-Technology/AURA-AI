"""Presents a :class:`RetrievalStrategy` with the ``KnowledgeEngine.retrieve`` signature.

Why this exists rather than teaching the kernel about strategies: the kernel's context assembly
calls ``knowledge.retrieve(text, owner_id=...)`` and wants a
:class:`~aura_core.domain.knowledge.RetrievalResult`. Strategies return a
:class:`~aura_core.rag.base.RetrievalOutcome`, which is a result plus a trace. Making the kernel
handle both would put strategy selection in the middle of the turn pipeline, where it would be
harder to test and impossible to change without touching generation.

The facade keeps the seam where it already was. With the default ``simple`` strategy the call
reaches ``KnowledgeEngine.retrieve`` with the same arguments it always did, so adopting the strategy
layer is a configuration change rather than a behavioural one.

**The trace is not discarded.** ``last_trace`` holds the most recent one, and ``consume_trace``
takes it. That is deliberately a pull rather than a push: threading a trace through the kernel's
return type is a wider change, and until something surfaces it in telemetry an operator can still
read it from an admin endpoint. It is per-instance state, so a container shared across concurrent
turns will show the trace of whichever finished last -- fine for diagnostics, which is all it
claims to be, and the reason it is not used for anything a request depends on.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..domain.identifiers import UserId
from ..domain.knowledge import RetrievalResult
from .base import RetrievalOutcome, RetrievalRequest, StrategyTrace

__all__ = ["StrategyFacade"]


class StrategyFacade:
    """Adapts a strategy to the shape the kernel already calls."""

    def __init__(self, strategy: Any, *, max_generations: int = 2) -> None:
        self._strategy = strategy
        self._max_generations = max_generations
        self._last: RetrievalOutcome | None = None

    @property
    def name(self) -> str:
        return getattr(self._strategy, "name", "unknown")

    @property
    def last_trace(self) -> StrategyTrace | None:
        return self._last.trace if self._last is not None else None

    @property
    def last_outcome(self) -> RetrievalOutcome | None:
        """Memories and graph paths, for a caller that wants more than chunks.

        The kernel does not read this today -- it recalls memory itself, concurrently with
        retrieval -- so a MemoryRAG strategy behind this facade would duplicate that work. Which is
        exactly why ``simple`` is the default and ``memory`` is selected only by the adaptive router,
        which is not reached through this path.
        """
        return self._last

    def consume_trace(self) -> StrategyTrace | None:
        trace = self.last_trace
        self._last = None
        return trace

    async def retrieve(
        self,
        query: str,
        *,
        owner_id: UserId | None = None,
        limit: int | None = None,
        include_global: bool = True,
        score_threshold: float | None = None,
        filters: Mapping[str, Any] | None = None,
        session_id: Any = None,
        history: tuple[tuple[str, str], ...] = (),
    ) -> RetrievalResult:
        outcome = await self._strategy.retrieve(
            RetrievalRequest(
                query=query,
                owner_id=owner_id,
                limit=limit,
                include_global=include_global,
                score_threshold=score_threshold,
                filters=filters,
                session_id=session_id,
                history=history,
                max_generations=self._max_generations,
            )
        )
        self._last = outcome
        return outcome.result
