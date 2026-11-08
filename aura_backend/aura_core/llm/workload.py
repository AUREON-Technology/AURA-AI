"""Workload hints: which pool a generation wants and how urgently (roadmap P2.3/P2.4).

The classification lives with the caller, not in a classifier: the kernel knows a turn is an
Engineer turn, the voice session knows it is voice, the ingestion pipeline knows it is indexing.
This module holds

* :func:`workload_for_turn` -- the one table mapping a turn's route and resolved persona to a
  :class:`~aura_core.domain.llm.WorkloadHint` (Engineer -> ``coder`` pool, priority 2; voice ->
  ``general-fast``, priority 1; ...), and
* an *ambient* hint (:func:`workload_scope` / :func:`current_workload`), a ``ContextVar`` the kernel
  sets once per turn so every generation made on that turn's behalf -- the planner's, the
  reasoner's self-consistency samples, the session summariser's -- carries the turn's priority
  and user without threading a parameter through a dozen engine signatures.

A request's own ``CompletionRequest.workload`` always wins over the ambient one; with neither,
the router treats the request as unhinted, which routes exactly as it did before hints existed.
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from typing import Iterator

from ..domain.llm import CompletionRequest, ModelPool, WorkloadClass, WorkloadHint
from .complexity import assess_complexity

logger = logging.getLogger("aura_core.llm.workload")

__all__ = [
    "DEFAULT_WORKLOAD",
    "current_workload",
    "effective_workload",
    "workload_for_turn",
    "workload_scope",
]

#: What an unhinted request is treated as: interactive priority, no pool, no user.
DEFAULT_WORKLOAD = WorkloadHint()

_AMBIENT: ContextVar[WorkloadHint | None] = ContextVar("aura_workload_hint", default=None)

#: Which pool each workload class prefers. A preference, not a requirement: with no provider
#: declaring the pool, the router falls back to generalists (see ``ProviderRouter.plan``).
_POOL_FOR_WORKLOAD = {
    WorkloadClass.INTERACTIVE: ModelPool.GENERAL_FAST,
    WorkloadClass.VOICE: ModelPool.GENERAL_FAST,
    WorkloadClass.ENGINEER: ModelPool.CODER,
    WorkloadClass.BACKGROUND_INSIGHT: ModelPool.GENERAL_QUALITY,
    WorkloadClass.RAG_INDEXING: ModelPool.GENERAL_FAST,
    WorkloadClass.TRAINING: ModelPool.GENERAL_QUALITY,
}

#: The persona id whose turns are code work. Mirrors ``agents/registry.py``'s ``engineer`` spec.
_ENGINEER_AGENT = "engineer"


def workload_class_for_turn(route: str, agent: str | None) -> WorkloadClass:
    route = (route or "").lower()
    if agent == _ENGINEER_AGENT:
        return WorkloadClass.ENGINEER
    if route.startswith("voice"):
        return WorkloadClass.VOICE
    if route.startswith("eval"):
        return WorkloadClass.BACKGROUND_INSIGHT
    if route in {"insight", "insights", "proactive", "briefing"}:
        return WorkloadClass.BACKGROUND_INSIGHT
    return WorkloadClass.INTERACTIVE


def workload_for_turn(
    *,
    route: str,
    agent: str | None,
    user_key: str | None,
    workload: WorkloadClass | None = None,
    message: str | None = None,
    complexity_threshold: float | None = None,
) -> WorkloadHint:
    """The hint for one kernel turn. ``workload`` overrides the route/persona derivation.

    With ``complexity_threshold`` set (``AURA_LLM_COMPLEXITY_ROUTING``, ADR-033), an
    *interactive* turn whose ``message`` scores at or above it asks for the ``reasoning`` pool
    instead of ``general-fast``. Only interactive turns: voice is latency-bound, Engineer turns
    already ask for ``coder``, background work already asks for ``general-quality``, and a
    caller that set ``workload`` explicitly has already said what it wants. The pool is a
    preference, never ``strict_pool``: with no ``reasoning`` provider eligible (none
    configured, circuit open, quota spent, or the turn's content too private for it), the
    router falls back to generalist and ``general-*`` providers, ordered by the routing policy.
    """
    cls = workload or workload_class_for_turn(route, agent)
    pool = _POOL_FOR_WORKLOAD[cls]
    if (
        complexity_threshold is not None
        and workload is None
        and cls is WorkloadClass.INTERACTIVE
        and message
    ):
        assessment = assess_complexity(message)
        if assessment.is_complex(complexity_threshold):
            pool = ModelPool.REASONING
            logger.info(
                "complex turn routed to the reasoning pool",
                extra={"complexity_score": assessment.score, "signals": list(assessment.signals)},
            )
    return WorkloadHint(workload=cls, pool=pool, user_key=user_key)


def hint_for(workload: WorkloadClass, *, user_key: str | None = None) -> WorkloadHint:
    """A hint for non-turn callers (ingestion, insights, training)."""
    return WorkloadHint(workload=workload, pool=_POOL_FOR_WORKLOAD[workload], user_key=user_key)


def current_workload() -> WorkloadHint | None:
    return _AMBIENT.get()


def effective_workload(request: CompletionRequest) -> WorkloadHint:
    """The request's own hint, else the ambient one, else :data:`DEFAULT_WORKLOAD`."""
    return request.workload or _AMBIENT.get() or DEFAULT_WORKLOAD


@contextlib.contextmanager
def workload_scope(hint: WorkloadHint | None) -> Iterator[None]:
    """Make ``hint`` the ambient hint for everything awaited inside the block.

    Safe inside an async generator (the kernel's ``stream``) as long as the generator is
    consumed by one task, which is how every caller consumes it; the reset is defensive about
    the one case where it is not (a generator finalised from a different context), where the
    only correct thing left to do is not raise from ``finally``.
    """
    if hint is None:
        yield
        return
    token = _AMBIENT.set(hint)
    try:
        yield
    finally:
        try:
            _AMBIENT.reset(token)
        except ValueError:  # pragma: no cover - finalised in a foreign context
            _AMBIENT.set(None)
