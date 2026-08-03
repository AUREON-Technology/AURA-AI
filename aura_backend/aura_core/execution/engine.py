"""Execution engine: runs a plan's steps under budget, with retries and progress events.

Executes the DAG produced by the planning engine. Ready steps -- those whose dependencies have
all succeeded -- run concurrently; the engine loops until the plan is complete, blocked, or out
of budget.

Three properties that make this safe to run unattended:

**Bounded.** Every loop has a wall-clock deadline, a step-execution ceiling, and a generation
budget shared with the rest of the turn. An agent loop without all three is how a runaway task
consumes a day's API quota in ten minutes.

**Idempotent on retry only where safe.** A failed step retries up to ``max_attempts`` -- but a
side-effecting tool that failed *after* acting would be re-run, so retry is refused for
side-effecting steps unless the failure happened before dispatch.

**Progress is observable.** Each transition emits an event, so a long-running plan can be
streamed to the client rather than appearing to hang.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from ..domain.errors import BudgetExceededError, ExecutionError
from ..domain.identifiers import ToolCallId
from ..domain.messages import ToolCall
from ..domain.planning import Plan, PlanStep, StepKind, StepStatus
from ..observability.logging import get_logger
from ..ports.runtime import ToolContext

__all__ = ["ExecutionEngine", "ExecutionOutcome", "StepEvent"]

logger = get_logger("execution")


class StepEvent:
    """A plan-progress notification, streamed to the caller."""

    __slots__ = ("step_id", "kind", "status", "description", "detail", "elapsed_ms")

    def __init__(
        self, *, step_id: str, kind: str, status: str, description: str,
        detail: str = "", elapsed_ms: float = 0.0,
    ) -> None:
        self.step_id = step_id
        self.kind = kind
        self.status = status
        self.description = description
        self.detail = detail
        self.elapsed_ms = elapsed_ms

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "kind": self.kind,
            "status": self.status,
            "description": self.description,
            "detail": self.detail,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


class ExecutionOutcome:
    __slots__ = ("plan", "results", "tool_reports", "succeeded", "duration_ms", "steps_run",
                 "blocked_reason")

    def __init__(
        self,
        *,
        plan: Plan,
        results: Mapping[str, str],
        tool_reports: Sequence[Any] = (),
        succeeded: bool = False,
        duration_ms: float = 0.0,
        steps_run: int = 0,
        blocked_reason: str | None = None,
    ) -> None:
        self.plan = plan
        self.results = dict(results)
        self.tool_reports = list(tool_reports)
        self.succeeded = succeeded
        self.duration_ms = duration_ms
        self.steps_run = steps_run
        self.blocked_reason = blocked_reason

    def summary_for_context(self) -> list[tuple[str, str]]:
        """Step results as ``(label, text)`` pairs for the context engine.

        Only successful steps: feeding a failed step's error text back as context invites the
        model to treat the error message as information about the user's question.
        """
        return [
            (step.tool or step.kind.value, self.results[step.id.value])
            for step in self.plan.steps
            if step.status is StepStatus.SUCCEEDED and step.id.value in self.results
        ]


class ExecutionEngine:
    def __init__(
        self,
        *,
        tools: Any,
        max_parallel: int = 4,
        max_steps_executed: int = 24,
        deadline_s: float = 120.0,
        metrics: Any = None,
        event_bus: Any = None,
    ) -> None:
        self._tools = tools
        self._max_parallel = max(1, max_parallel)
        self._max_steps = max_steps_executed
        self._deadline_s = deadline_s
        self._metrics = metrics
        self._events = event_bus

    async def execute(
        self,
        plan: Plan,
        *,
        context: ToolContext,
        capabilities: Sequence[str] = (),
        confirmed_tools: Sequence[str] = (),
        handlers: Mapping[StepKind, Callable[[PlanStep], Any]] | None = None,
        on_event: Callable[[StepEvent], Any] | None = None,
    ) -> ExecutionOutcome:
        """Run the plan to completion, blockage, or budget exhaustion."""
        started = time.perf_counter()
        deadline = started + self._deadline_s
        results: dict[str, str] = {}
        tool_reports: list[Any] = []
        steps_run = 0
        blocked_reason: str | None = None
        current = plan

        while not current.is_complete:
            if time.perf_counter() > deadline:
                blocked_reason = "deadline_exceeded"
                break
            if steps_run >= self._max_steps:
                blocked_reason = "step_budget_exceeded"
                break

            ready = current.ready_steps()
            if not ready:
                # Nothing runnable and the plan is not complete: every remaining step depends on
                # something that failed. Mark them explicitly rather than spinning.
                current = current.mark_blocked_steps()
                if not current.ready_steps() and not current.is_complete:
                    blocked_reason = "dependencies_failed"
                    break
                continue

            batch = ready[: self._max_parallel]
            running = [step.started() for step in batch]
            current = current.with_steps(running)

            for step in running:
                await self._emit(
                    on_event,
                    StepEvent(
                        step_id=step.id.value, kind=str(step.kind), status="running",
                        description=step.description,
                    ),
                )

            outcomes = await asyncio.gather(
                *(
                    self._run_step(
                        step, context=context, capabilities=capabilities,
                        confirmed_tools=confirmed_tools, handlers=handlers or {},
                    )
                    for step in running
                ),
                return_exceptions=True,
            )

            updated: list[PlanStep] = []
            for step, outcome in zip(running, outcomes):
                steps_run += 1
                if isinstance(outcome, BudgetExceededError):
                    blocked_reason = "budget_exceeded"
                    updated.append(step.failed("budget_exceeded", 0.0))
                    continue
                if isinstance(outcome, BaseException):
                    logger.warning(
                        "step raised",
                        extra={"step": step.id.value, "error": type(outcome).__name__},
                    )
                    updated.append(step.failed(type(outcome).__name__, 0.0))
                    await self._emit(
                        on_event,
                        StepEvent(
                            step_id=step.id.value, kind=str(step.kind), status="failed",
                            description=step.description, detail=type(outcome).__name__,
                        ),
                    )
                    continue

                text, ok, elapsed, report = outcome
                if report is not None:
                    tool_reports.append(report)
                if ok:
                    results[step.id.value] = text
                    updated.append(step.succeeded(text, elapsed))
                    await self._emit(
                        on_event,
                        StepEvent(
                            step_id=step.id.value, kind=str(step.kind), status="succeeded",
                            description=step.description, elapsed_ms=elapsed,
                        ),
                    )
                else:
                    failed = step.failed(text[:120], elapsed)
                    # Retry only when the step can be safely repeated. A side-effecting tool that
                    # failed may have already acted, so retrying could duplicate the effect.
                    if failed.can_retry and not self._is_side_effecting(step):
                        # Back to PENDING so the next loop picks it up; `attempts` is already
                        # incremented, so `can_retry` will stop it at max_attempts.
                        failed = replace(
                            failed, status=StepStatus.PENDING, error_kind=None, finished_at=None
                        )
                    updated.append(failed)
                    await self._emit(
                        on_event,
                        StepEvent(
                            step_id=step.id.value, kind=str(step.kind), status="failed",
                            description=step.description, detail=text[:200], elapsed_ms=elapsed,
                        ),
                    )

            current = current.with_steps(updated)
            if blocked_reason:
                break

        duration = (time.perf_counter() - started) * 1000.0
        return ExecutionOutcome(
            plan=current,
            results=results,
            tool_reports=tool_reports,
            succeeded=current.succeeded,
            duration_ms=duration,
            steps_run=steps_run,
            blocked_reason=blocked_reason,
        )

    def _is_side_effecting(self, step: PlanStep) -> bool:
        if step.kind is not StepKind.TOOL or not step.tool:
            return False
        try:
            return self._tools.spec(step.tool).side_effecting
        except Exception:
            # Unknown tool: assume the worst and do not retry.
            return True

    async def _run_step(
        self,
        step: PlanStep,
        *,
        context: ToolContext,
        capabilities: Sequence[str],
        confirmed_tools: Sequence[str],
        handlers: Mapping[StepKind, Callable[[PlanStep], Any]],
    ) -> tuple[str, bool, float, Any]:
        started = time.perf_counter()

        if step.kind is StepKind.TOOL and step.tool:
            call = ToolCall(
                id=ToolCallId(f"step_{step.id.value}"),
                name=step.tool,
                arguments=dict(step.arguments),
            )
            result, report = await self._tools.execute(
                call,
                context,
                capabilities=capabilities,
                confirmed=step.tool in confirmed_tools,
            )
            return result.content, result.ok, (time.perf_counter() - started) * 1000.0, report

        handler = handlers.get(step.kind)
        if handler is None:
            # A step kind with no handler is a configuration gap, not a user-visible failure --
            # skip it with an explanation rather than failing the whole plan.
            return (
                f"No handler is configured for {step.kind} steps.",
                False,
                (time.perf_counter() - started) * 1000.0,
                None,
            )

        try:
            output = handler(step)
            if asyncio.iscoroutine(output):
                output = await output
            return str(output), True, (time.perf_counter() - started) * 1000.0, None
        except BudgetExceededError:
            raise
        except Exception as exc:
            raise ExecutionError(
                f"step {step.id.value} failed: {type(exc).__name__}",
                details={"step": step.id.value, "kind": str(step.kind)},
                cause=exc,
            ) from exc

    async def stream(
        self,
        plan: Plan,
        *,
        context: ToolContext,
        capabilities: Sequence[str] = (),
        confirmed_tools: Sequence[str] = (),
        handlers: Mapping[StepKind, Callable[[PlanStep], Any]] | None = None,
    ) -> AsyncIterator[StepEvent | ExecutionOutcome]:
        """Execute while yielding progress, then yield the final outcome.

        A bounded queue bridges the callback-style event emission to an async iterator: the
        alternative -- buffering every event until completion -- defeats the purpose, since the
        point is to show progress during a long plan.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        sentinel = object()

        async def emit(event: StepEvent) -> None:
            await queue.put(event)

        async def run() -> None:
            try:
                outcome = await self.execute(
                    plan,
                    context=context,
                    capabilities=capabilities,
                    confirmed_tools=confirmed_tools,
                    handlers=handlers,
                    on_event=emit,
                )
                await queue.put(outcome)
            finally:
                await queue.put(sentinel)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _emit(self, on_event: Callable[[StepEvent], Any] | None, event: StepEvent) -> None:
        if on_event is None:
            return
        try:
            result = on_event(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # pragma: no cover - defensive
            logger.debug("step event handler failed", exc_info=True)
