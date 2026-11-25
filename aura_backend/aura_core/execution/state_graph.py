"""The agent loop's control flow, as data (ADR-039).

``AgenticExecutor`` used to carry its transitions in an ``if`` ladder inside ``_evaluate``. The
ladder worked, but the *shape* of the loop -- what states exist, what can follow what, which inputs
decide it -- was visible only by reading the code top to bottom, and "test results are an input"
had no place to go. This module makes the shape explicit and testable on its own:

    PLAN ──planned──▶ EXECUTE ──executed──▶ OBSERVE ──┬─success──▶ VERIFY ──verified──▶ DONE
      │                  ▲                              ├─replan───▶ REPLAN ─replanned─▶ EXECUTE
      └─critic_revise─▶ REPLAN ◀─────corrective─────────┤                   └replan_failed─▶ ESCALATE
                                                        ├─stuck────▶ STUCK
                                                        └─escalate─▶ ESCALATE

* :data:`TRANSITIONS` is the whole graph, as a table; :func:`transition` is the only way to move
  and raises on an edge that does not exist, so a loop change that invents a transition fails a
  test instead of drifting.
* :func:`classify` is the OBSERVE decision: it takes a :class:`Signals` snapshot (plain values,
  nothing from the engines) and returns which edge to take and why. It is the old ``_evaluate``
  ladder in the same order, with the new inputs slotted in where they belong -- so with those
  inputs off, it answers exactly as before.

``DONE``, ``STUCK`` and ``ESCALATE`` are terminal. ``ESCALATE`` is "stop and tell the user":
a pending confirmation, an exhausted budget, a failure that should not be retried, a replan that
could not be produced. The ``stop_reason`` strings are the ones the run recorder, metrics and
tests already know; this module only adds ``stuck``, ``tests_failed``-driven replans and the
recovery stops.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Mapping

__all__ = [
    "LoopState",
    "TRANSITIONS",
    "TERMINAL_STATES",
    "transition",
    "Signals",
    "Verdict",
    "classify",
    "IllegalTransition",
]


class LoopState(str, enum.Enum):
    PLAN = "plan"
    EXECUTE = "execute"
    OBSERVE = "observe"
    REPLAN = "replan"
    VERIFY = "verify"
    DONE = "done"
    STUCK = "stuck"
    ESCALATE = "escalate"

    def __str__(self) -> str:
        return self.value


TERMINAL_STATES = frozenset({LoopState.DONE, LoopState.STUCK, LoopState.ESCALATE})

#: ``(state, event) -> next state``. The complete graph; nothing else is a legal move.
TRANSITIONS: Mapping[tuple[LoopState, str], LoopState] = {
    (LoopState.PLAN, "planned"): LoopState.EXECUTE,
    (LoopState.PLAN, "critic_revise"): LoopState.REPLAN,
    (LoopState.EXECUTE, "executed"): LoopState.OBSERVE,
    (LoopState.OBSERVE, "success"): LoopState.VERIFY,
    (LoopState.OBSERVE, "replan"): LoopState.REPLAN,
    (LoopState.OBSERVE, "stuck"): LoopState.STUCK,
    (LoopState.OBSERVE, "escalate"): LoopState.ESCALATE,
    (LoopState.VERIFY, "verified"): LoopState.DONE,
    (LoopState.VERIFY, "corrective"): LoopState.REPLAN,
    (LoopState.REPLAN, "replanned"): LoopState.EXECUTE,
    (LoopState.REPLAN, "replan_failed"): LoopState.ESCALATE,
}


class IllegalTransition(ValueError):
    pass


def transition(state: LoopState, event: str) -> LoopState:
    """The state ``event`` leads to from ``state``; :class:`IllegalTransition` if there is no such edge."""
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise IllegalTransition(f"no transition from {state.value!r} on {event!r}") from None


@dataclass(frozen=True, slots=True)
class Signals:
    """Everything OBSERVE decides from, as plain values.

    ``unresolved_tests`` is the new input: a test run (or a command that exited non-zero) whose
    failure no later step has resolved. It is only ever ``True`` when the loop was told to treat
    test results as an input; the ladder below then handles it like any failure worth a replan.
    """

    succeeded: bool                      # every plan step succeeded / was skipped
    past_deadline: bool = False
    blocked_reason: str | None = None    # the pass's own terminal block, if any
    terminal_blocks: frozenset[str] = frozenset()
    block_budgets: Mapping[str, str | None] | None = None
    unresolved_tests: bool = False       # a failing test/command observation nobody fixed
    repairable: bool = False             # a failed step, or a blocked one a revised plan could run
    max_iterations: int = 0
    stuck: bool = False
    recovery_stop: str | None = None     # a failure class that forbids any further attempt
    recovery_surface: str | None = None  # every failure is a refusal: tell the user, do not retry
    recovery_escalate: str | None = None  # an unrecognised failure that came back after a replan
    tokens_exhausted: bool = False
    can_replan: bool = True


@dataclass(frozen=True, slots=True)
class Verdict:
    """OBSERVE's answer: the edge to take, and (for a stop) the reason and exhausted budget."""

    event: str                      # success | replan | stuck | escalate
    stop_reason: str | None = None  # None for a replan
    exhausted_budget: str | None = None


def classify(signals: Signals) -> Verdict:
    """Which edge OBSERVE takes. Order is the contract -- earlier rules win."""
    s = signals
    if s.succeeded and not s.unresolved_tests:
        return Verdict("success", "completed")
    if s.past_deadline:
        return Verdict("escalate", "time_budget_exhausted", "time")
    if s.blocked_reason in s.terminal_blocks:
        budget = (s.block_budgets or {}).get(s.blocked_reason or "")
        return Verdict("escalate", s.blocked_reason, budget)
    if s.recovery_stop is not None:
        return Verdict("escalate", f"unrecoverable:{s.recovery_stop}")
    if s.recovery_surface is not None:
        return Verdict("escalate", f"not_retried:{s.recovery_surface}")
    if not s.repairable and not s.unresolved_tests:
        return Verdict("escalate", "nothing_to_repair")
    if s.max_iterations <= 0:
        return Verdict("escalate", "single_pass")
    if s.stuck:
        return Verdict("stuck", "stuck")
    if s.recovery_escalate is not None:
        return Verdict("escalate", f"escalated:{s.recovery_escalate}")
    if s.tokens_exhausted:
        return Verdict("escalate", "token_budget_exhausted", "tokens")
    if not s.can_replan:
        return Verdict("escalate", "iteration_budget_exhausted", "iterations")
    return Verdict("replan")
