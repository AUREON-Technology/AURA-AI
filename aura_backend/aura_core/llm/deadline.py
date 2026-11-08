"""A wall-clock budget for all of one turn's generations (PP-06).

Each router call already has a deadline per attempt (``CompletionRequest.timeout_s``), and the
router tries up to ``max_fallbacks + 1`` providers in turn. A chat turn makes more than one call
-- the planner's, then the answer's -- so the worst case before a user sees an error is the
product of all of them: on Railway's documented settings, a 60s and a 45s provider, twice, is
about 210s. Nothing bounded the sum.

A :class:`GenerationBudget` is that bound. The kernel opens one for an interactive turn (chat,
voice), and the router reads it before every attempt: an attempt is given the smaller of its own
deadline and what is left of the budget, and no attempt starts once the budget is spent. The
budget starts on the first generation that reads it, so retrieval and context assembly before it
do not count -- it bounds model time, which is what the provider settings size.

It is ambient (a ``ContextVar``), like the data classification and the workload hint, so every
generation made on the turn's behalf shares one budget without each caller passing it along.
Engineer turns do not open one: their agentic loop has its own, much longer deadline
(``engineer_loop_deadline_s``). A nested scope never replaces the one already open, so a
sub-turn delegated from a chat turn can only use what its parent has left -- the person is
still waiting on the parent.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Callable, Iterator

__all__ = [
    "GenerationBudget",
    "current_generation_budget",
    "generation_budget",
    "no_generation_budget",
    "time_left",
]


class GenerationBudget:
    """``seconds`` of generation time, counted from the first :meth:`remaining` call."""

    __slots__ = ("seconds", "_expires_at")

    def __init__(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("a generation budget must be positive")
        self.seconds = seconds
        self._expires_at: float | None = None

    def remaining(self, now: float) -> float:
        """Seconds left at monotonic time ``now`` (negative once spent). Starts the clock."""
        if self._expires_at is None:
            self._expires_at = now + self.seconds
        return self._expires_at - now


_AMBIENT: ContextVar[GenerationBudget | None] = ContextVar("aura_generation_budget", default=None)


def current_generation_budget() -> GenerationBudget | None:
    return _AMBIENT.get()


@contextlib.contextmanager
def generation_budget(seconds: float | None) -> Iterator[GenerationBudget | None]:
    """Make a ``seconds`` budget ambient for the block; a no-op for ``None`` or ``<= 0``.

    An already-open budget is kept rather than replaced, so a nested turn cannot extend it.
    """
    existing = _AMBIENT.get()
    if existing is not None or not seconds or seconds <= 0:
        yield existing
        return
    budget = GenerationBudget(seconds)
    token = _AMBIENT.set(budget)
    try:
        yield budget
    finally:
        try:
            _AMBIENT.reset(token)
        except ValueError:  # pragma: no cover - finalised from another context (see workload)
            pass


@contextlib.contextmanager
def no_generation_budget() -> Iterator[None]:
    """Run the block with no budget, whatever is ambient.

    For work started on a turn's behalf that outlives it -- a background task copies the turn's
    context, budget included, and would otherwise be cut short by a budget the turn has spent.
    """
    token = _AMBIENT.set(None)
    try:
        yield
    finally:
        try:
            _AMBIENT.reset(token)
        except ValueError:  # pragma: no cover - see `generation_budget`
            pass


def time_left(clock: Callable[[], float]) -> float | None:
    """What is left of the ambient budget at ``clock()``, or ``None`` when there is none."""
    budget = _AMBIENT.get()
    return None if budget is None else budget.remaining(clock())
