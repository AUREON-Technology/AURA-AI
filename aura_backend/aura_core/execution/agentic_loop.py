"""The agentic loop: plan, execute, observe, replan -- bounded.

``PlanningEngine.replan`` (``planning/engine.py``) and ``ExecutionEngine.execute``
(``execution/engine.py``) already do everything a Cursor/Antigravity-style agent loop needs; the
one thing missing was an outer loop that calls them in sequence more than once per turn. That is
all this module adds.

Every iteration still goes through the same two engines a single-shot turn uses: a planner
completion producing text-JSON, then registry-mediated tool dispatch. A model's own emitted
``tool_calls`` are never dispatched directly, here or anywhere else (ADR-006) -- looping only
changes how many times the plan/execute pair runs, never how a tool call reaches the registry.

Budgets, all bounded: ``max_iterations`` caps replans (via ``PlanningEngine.can_replan``'s
``limit`` argument), ``deadline_s`` caps the whole loop's wall-clock time independent of any
single ``ExecutionEngine.execute`` call's own ``deadline_s``, and each iteration still respects
the execution engine's own step-count budget.
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from ..domain.planning import Plan
from ..observability.logging import get_logger
from ..ports.runtime import ToolContext
from .engine import ExecutionEngine, ExecutionOutcome, StepEvent

__all__ = ["AgenticExecutor"]

logger = get_logger("agentic_loop")

# Reasons ExecutionOutcome.blocked_reason can carry that mean "budget exhausted for this pass",
# not "something failed that a revised plan could fix" -- replanning again would just spend
# another generation to produce a plan that hits the same wall.
_TERMINAL_BLOCKS = frozenset({"deadline_exceeded", "step_budget_exceeded", "budget_exceeded"})


class AgenticExecutor:
    """Wraps one planner/executor pair in a bounded observe-and-replan loop."""

    def __init__(self, *, planner: Any, executor: ExecutionEngine, metrics: Any = None) -> None:
        self._planner = planner
        self._executor = executor
        self._metrics = metrics

    def _should_continue(
        self, outcome: ExecutionOutcome, *, deadline: float, max_iterations: int
    ) -> bool:
        if outcome.succeeded:
            return False
        if time.monotonic() > deadline:
            return False
        if outcome.blocked_reason in _TERMINAL_BLOCKS:
            return False
        if not outcome.plan.failed_steps and not outcome.plan.blocked_steps():
            # Nothing a revised plan could repair -- e.g. the plan is complete but some steps
            # were merely skipped, which is not a failure to react to.
            return False
        return self._planner.can_replan(outcome.plan, limit=max_iterations)

    async def _next_plan(
        self,
        outcome: ExecutionOutcome,
        *,
        available_tools: Sequence[Mapping[str, Any]],
        max_iterations: int,
    ) -> Plan | None:
        try:
            return await self._planner.replan(
                outcome.plan,
                reason=outcome.blocked_reason or "step_failed",
                available_tools=available_tools,
                limit=max_iterations,
            )
        except Exception:
            logger.warning("replan failed; stopping the agentic loop")
            return None

    async def run(
        self,
        plan: Plan,
        *,
        context: ToolContext,
        capabilities: Sequence[str] = (),
        confirmed_tools: Sequence[str] = (),
        available_tools: Sequence[Mapping[str, Any]] = (),
        max_iterations: int = 1,
        deadline_s: float = 600.0,
        on_event: Callable[[StepEvent], Any] | None = None,
    ) -> ExecutionOutcome:
        """Run ``plan`` to completion, replanning on failure up to ``max_iterations`` times."""
        deadline = time.monotonic() + deadline_s
        outcome = await self._executor.execute(
            plan, context=context, capabilities=capabilities,
            confirmed_tools=confirmed_tools, on_event=on_event,
        )
        while self._should_continue(outcome, deadline=deadline, max_iterations=max_iterations):
            revised = await self._next_plan(
                outcome, available_tools=available_tools, max_iterations=max_iterations
            )
            if revised is None:
                break
            outcome = await self._executor.execute(
                revised, context=context, capabilities=capabilities,
                confirmed_tools=confirmed_tools, on_event=on_event,
            )
        return outcome

    async def stream(
        self,
        plan: Plan,
        *,
        context: ToolContext,
        capabilities: Sequence[str] = (),
        confirmed_tools: Sequence[str] = (),
        available_tools: Sequence[Mapping[str, Any]] = (),
        max_iterations: int = 1,
        deadline_s: float = 600.0,
    ) -> AsyncIterator[StepEvent | ExecutionOutcome]:
        """Stream step progress across every iteration, then yield the final outcome.

        Intermediate outcomes are not yielded -- only :class:`StepEvent`s during each pass and the
        loop's final :class:`ExecutionOutcome` at the end, matching
        :meth:`~aura_core.execution.engine.ExecutionEngine.stream`'s own "progress, then the
        outcome" contract so callers (``AIKernel._iter_execution``) need no special case for the
        looping path.
        """
        deadline = time.monotonic() + deadline_s
        current = plan
        outcome: ExecutionOutcome | None = None
        while True:
            async for item in self._executor.stream(
                current, context=context, capabilities=capabilities,
                confirmed_tools=confirmed_tools,
            ):
                if isinstance(item, StepEvent):
                    yield item
                else:
                    outcome = item
            if outcome is None or not self._should_continue(
                outcome, deadline=deadline, max_iterations=max_iterations
            ):
                break
            revised = await self._next_plan(
                outcome, available_tools=available_tools, max_iterations=max_iterations
            )
            if revised is None:
                break
            current = revised
        if outcome is not None:
            yield outcome
