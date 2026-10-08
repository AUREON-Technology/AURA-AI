"""The ten retrieval strategies, and the router that chooses between them.

Composition, not a switch statement. Each strategy either fetches evidence (``simple``, ``memory``,
``graph``) or wraps one that does (``multi_query``, ``hyde``, ``corrective``, ``self_rag``,
``agentic``, ``multimodal``), and :class:`AdaptiveRouter` selects per query. Adding an eleventh means
adding a class and a route, not editing a dispatcher.

:func:`build_strategies` is the standard wiring. It is a function rather than container code so the
composition is testable on its own and so a deployment can override one strategy without
reimplementing the rest.
"""

from __future__ import annotations

from typing import Any, Mapping

from .adaptive import AdaptiveRouter, QueryClass, QueryClassification, classify_query
from .agentic import AgenticRAG, MultimodalRAG
from .base import (
    RetrievalOutcome,
    RetrievalRequest,
    RetrievalStrategy,
    StrategyTrace,
    fuse_results,
)
from .expansion import HyDERAG, MultiQueryRAG, QueryRewriter
from .facade import StrategyFacade
from .leaves import GraphRAG, MemoryRAG, SimpleRAG
from .verification import CorrectiveRAG, EvidenceGrade, SelfRAG, grade_evidence

__all__ = [
    "RetrievalRequest",
    "RetrievalOutcome",
    "RetrievalStrategy",
    "StrategyTrace",
    "fuse_results",
    "SimpleRAG",
    "MemoryRAG",
    "GraphRAG",
    "MultiQueryRAG",
    "HyDERAG",
    "QueryRewriter",
    "CorrectiveRAG",
    "SelfRAG",
    "EvidenceGrade",
    "grade_evidence",
    "AgenticRAG",
    "MultimodalRAG",
    "AdaptiveRouter",
    "QueryClass",
    "QueryClassification",
    "classify_query",
    "StrategyFacade",
    "build_strategies",
    "STRATEGY_NAMES",
]

#: Every strategy the platform provides. Asserted against `build_strategies` by the tests, so a
#: strategy cannot be added to one and forgotten in the other.
STRATEGY_NAMES = (
    "simple",
    "memory",
    "multi_query",
    "hyde",
    "adaptive",
    "corrective",
    "self_rag",
    "agentic",
    "multimodal",
    "graph",
)


def build_strategies(
    *,
    knowledge: Any,
    router: Any,
    memory: Any = None,
    graph: Any = None,
    metrics: Any = None,
    variants: int = 3,
    max_parallel: int = 2,
) -> dict[str, Any]:
    """Wire the ten strategies and the adaptive router over the given engines.

    The composition below is the interesting part:

    * ``multi_query`` and ``hyde`` wrap ``simple``, because query expansion is orthogonal to how
      retrieval works.
    * ``corrective`` wraps ``simple`` and falls back to ``multi_query`` -- vocabulary mismatch is
      the most common reason a first pass is thin, and that is precisely what Multi-Query attacks.
    * ``self_rag`` wraps ``corrective`` rather than ``simple``, so that when it does decide
      retrieval is needed, the evidence it grades has already had one corrective attempt. Grading a
      result that was never allowed to retry would report weak grounding it could have fixed.
    * ``memory`` and ``graph`` wrap ``simple`` too: both supplement corpus retrieval rather than
      replacing it, and a question about relationships still usually needs the passages.

    ``memory`` and ``graph`` are optional. Passing ``None`` maps their routes to plain retrieval
    rather than raising, so a deployment without a graph store still routes relational questions
    somewhere sensible.
    """
    simple = SimpleRAG(knowledge, metrics=metrics)
    multi_query = MultiQueryRAG(simple, router, variants=variants, metrics=metrics)
    hyde = HyDERAG(simple, router, metrics=metrics)
    corrective = CorrectiveRAG(simple, multi_query, metrics=metrics)
    self_rag = SelfRAG(corrective, metrics=metrics)
    agentic = AgenticRAG(simple, router, metrics=metrics)
    multimodal = MultimodalRAG(simple, metrics=metrics)

    strategies: dict[str, Any] = {
        "simple": simple,
        "multi_query": multi_query,
        "hyde": hyde,
        "corrective": corrective,
        "self_rag": self_rag,
        "agentic": agentic,
        "multimodal": multimodal,
    }

    strategies["memory"] = (
        MemoryRAG(memory, simple, metrics=metrics) if memory is not None else simple
    )
    strategies["graph"] = (
        GraphRAG(graph, simple, metrics=metrics) if graph is not None else simple
    )

    strategies["adaptive"] = AdaptiveRouter(
        strategies, default="simple", max_parallel=max_parallel, metrics=metrics
    )
    return strategies
