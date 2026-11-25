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
import re
import time
from dataclasses import replace
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from ..domain.errors import BudgetExceededError, ExecutionError
from ..domain.failure_kinds import FailureKind
from ..domain.identifiers import ToolCallId
from ..domain.messages import ToolCall
from ..domain.planning import Plan, PlanStep, StepKind, StepStatus
from ..observability.logging import get_logger
from ..ports.runtime import ToolContext
from .confirmations import NEEDS_CONFIRMATION, ConfirmationGrant, PendingCall
from .recovery import RecoveryPolicy

__all__ = ["ExecutionEngine", "ExecutionOutcome", "StepEvent"]

logger = get_logger("execution")

# Returned as the 4th `_run_step` slot when a kind has no handler. Not a tool report.
_NO_HANDLER = object()

# Returned as the 4th `_run_step` slot when a `${step_id.output}` reference in the step's
# arguments could not be resolved against an actually-succeeded step -- the dispatch never
# happened, so this is not a tool report either. See `_resolve_output_tokens`.
_UNRESOLVED_REFERENCE = object()

#: Mirrors `planning/engine.py`'s `_STEP_OUTPUT_TOKEN` -- the same token syntax, resolved here
#: against real step results instead of merely being detected at parse time. Kept as a separate
#: compiled pattern (rather than imported) so `execution/` does not import from `planning/`: the
#: two engines are peers in the layering, and the convention itself is documented in both
#: docstrings rather than shared code.
_OUTPUT_TOKEN = re.compile(r"\$\{\s*([A-Za-z0-9_.\-]+)\.output\s*\}")


def _resolve_output_tokens(
    arguments: Mapping[str, Any], plan: Plan
) -> tuple[dict[str, Any], list[str]]:
    """Replace every ``${step_id.output}`` token in ``arguments`` with that step's actual result.

    This is what stops a dependent step from ever calling its tool with LLM-fabricated content
    standing in for "whatever the earlier step produced": a token that names a step which never
    ran, is not yet SUCCEEDED, or produced no result is left unresolved and reported back, so the
    caller can fail the step instead of dispatching it. Non-string argument values pass through
    unchanged -- a token can only appear inside text a model wrote.
    """
    unresolved: list[str] = []
    by_id = {s.id.value: s for s in plan.steps}

    def resolve(value: Any) -> Any:
        if not isinstance(value, str):
            return value

        def repl(match: "re.Match[str]") -> str:
            ref = match.group(1)
            dep = by_id.get(ref)
            if dep is None or dep.status is not StepStatus.SUCCEEDED or dep.result is None:
                unresolved.append(ref)
                return match.group(0)
            return dep.result

        return _OUTPUT_TOKEN.sub(repl, value)

    resolved = {key: resolve(value) for key, value in arguments.items()}
    return resolved, unresolved


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
 claude/exec-lifecycle-telemetry
                 "blocked_reason", "run_id", "pending_calls", "tainted", "tools_ms", "replan_ms")

                 "blocked_reason", "run_id", "pending_calls", "tainted", "failure_messages",
                 "loop")
 main

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
        run_id: str | None = None,
        pending_calls: Sequence[PendingCall] = (),
        tainted: bool = False,
 claude/exec-lifecycle-telemetry
        tools_ms: float | None = None,
        replan_ms: float = 0.0,

        failure_messages: Mapping[str, str] | None = None,
 main
    ) -> None:
        self.plan = plan
        self.results = dict(results)
        self.tool_reports = list(tool_reports)
        self.succeeded = succeeded
        self.duration_ms = duration_ms
        self.steps_run = steps_run
        self.blocked_reason = blocked_reason
        #: The durable ``AgentRun`` this outcome belongs to, when the agent loop recorded one
        #: (``execution/agentic_loop.py``); ``None`` otherwise.
        self.run_id = run_id
        #: Each confirm-gated call exactly as it would have been dispatched (resolved arguments),
        #: which is what a confirmation is bound to (``execution/confirmations.py``).
        self.pending_calls = list(pending_calls)
        #: Whether the tool context ended this execution tainted -- an untrusted tool that
        #: already ran taints it, and a resumed turn must start from that, not from clean.
        self.tainted = tainted
 claude/exec-lifecycle-telemetry
        #: Wall-clock milliseconds the engine spent executing, summed over every pass of an agent
        #: loop (``AgenticExecutor`` accumulates it); one pass's ``duration_ms`` otherwise.
        self.tools_ms = float(duration_ms if tools_ms is None else tools_ms)
        #: Milliseconds the agent loop spent generating revised plans.
        self.replan_ms = float(replan_ms)

        #: Each failed step's tool message (cut to 400 characters), by step id. ``PlanStep`` keeps
        #: only the machine-readable ``error_kind``; the message is what a validator said about a
        #: bad argument, and is what a replan needs to fix it (``RecoveryPolicy``, ADR-039).
        self.failure_messages = dict(failure_messages or {})
        #: The agent loop's account of itself (``execution/loop_events.py::LoopReport``): why it
        #: stopped, the states it visited, any stuck verdict, critic findings. ``None`` for an
        #: outcome that did not come through ``AgenticExecutor``.
        self.loop = None
 main

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
        recovery: RecoveryPolicy | None = None,
        sleeper: Callable[[float], Any] | None = None,
    ) -> None:
        self._tools = tools
        self._max_parallel = max(1, max_parallel)
        self._max_steps = max_steps_executed
        self._deadline_s = deadline_s
        self._metrics = metrics
        self._events = event_bus
        #: ``None`` keeps the original rule (retry any failed read-only step up to its
        #: ``max_attempts``); a policy decides per failure kind (ADR-039, ``recovery.py``).
        self._recovery = recovery
        self._sleep = sleeper or asyncio.sleep

    async def execute(
        self,
        plan: Plan,
        *,
        context: ToolContext,
        capabilities: Sequence[str] = (),
        confirmations: ConfirmationGrant | None = None,
        handlers: Mapping[StepKind, Callable[[PlanStep], Any]] | None = None,
        on_event: Callable[[StepEvent], Any] | None = None,
        gate: Any = None,
    ) -> ExecutionOutcome:
        """Run the plan to completion, blockage, or budget exhaustion.

        ``gate`` (optional; ``execution/agentic_loop.py``'s ``RunBudgetGate``) is asked
        ``admit(step, spec)`` before each tool step is dispatched and returns ``None`` to let it
        through or a reason string (``"tool_budget_exceeded"``, ``"risk_budget_exceeded"``) to
        refuse it. It only ever *removes* a dispatch: an admitted step still goes through
        ``ToolRegistry.execute`` and its policy exactly as it would without a gate.

        ``confirmations`` is the only way a call reaches the registry as user-confirmed: a
        :class:`~aura_core.execution.confirmations.ConfirmationGrant` for a resumed turn, which
        admits a call only if it is identical to the one the user approved, and only once. There
        is no name- or position-based confirmation (F-04).
        """
        started = time.perf_counter()
        deadline = started + self._deadline_s
        results: dict[str, str] = {}
        tool_reports: list[Any] = []
        pending_calls: list[PendingCall] = []
        failure_messages: dict[str, str] = {}
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

            batch = self._taint_ordered(ready[: self._max_parallel], context)
            if gate is not None:
                admitted: list[PlanStep] = []
                for step in batch:
                    reason = (
                        gate.admit(step, self._tool_spec(step))
                        if step.kind is StepKind.TOOL and step.tool else None
                    )
                    if reason is None:
                        admitted.append(step)
                        continue
                    # Refused before dispatch: the registry never sees it, so nothing acted.
                    # BLOCKED (not FAILED) with the budget as the reason, and a terminal
                    # `blocked_reason` so the agent loop stops rather than replanning into the
                    # same wall.
                    blocked_reason = reason
                    current = current.with_steps([step.blocked(reason)])
                    await self._emit(
                        on_event,
                        StepEvent(
                            step_id=step.id.value, kind=str(step.kind), status="blocked",
                            description=step.description, detail=reason,
                        ),
                    )
                batch = admitted
                if not batch:
                    break
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
                        confirmations=confirmations,
                        handlers=handlers or {}, plan=current,
                    )
                    for step in running
                ),
                return_exceptions=True,
            )

            updated: list[PlanStep] = []
            backoff_s = 0.0
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
                if report is _UNRESOLVED_REFERENCE:
                    # The step's arguments named an earlier step's output via `${step_id.output}`
                    # and it could not be resolved -- the tool was never dispatched. FAILED (not
                    # BLOCKED) because this is the step's own argument that is bad, not a
                    # dependency that failed; `error_kind="unresolved_reference"` is what item 5's
                    # richer replan context and any test asserting "no false success" key off of.
                    updated.append(step.failed(FailureKind.UNRESOLVED_REFERENCE, elapsed))
                    await self._emit(
                        on_event,
                        StepEvent(
                            step_id=step.id.value, kind=str(step.kind), status="failed",
                            description=step.description, detail=text[:200], elapsed_ms=elapsed,
                        ),
                    )
                    continue
                if report is _NO_HANDLER:
                    # Configuration gap: skip, do not fail or retry, and do not count as a
                    # tool report. SKIPPED is terminal and treated as success for the plan.
                    updated.append(step.skipped(text))
                    await self._emit(
                        on_event,
                        StepEvent(
                            step_id=step.id.value, kind=str(step.kind), status="skipped",
                            description=step.description, detail=text[:200],
                            elapsed_ms=elapsed,
                        ),
                    )
                    continue
                if report is not None:
                    tool_reports.append(report)
                if report is not None and getattr(report, "needs_confirmation", False):
                    # A confirm-gated call is not a failure a replan could work around -- it is
                    # the turn correctly stopping to ask the user. Without this branch it falls
                    # into the `else` below indistinguishably from a real error: marked FAILED,
                    # which (for the agentic loop specifically, `AgenticExecutor`'s
                    # `max_plan_iterations > 1` personas like `engineer`) triggers a replan that
                    # overwrites this outcome -- silently discarding the very report the kernel
                    # needs to surface `needs_confirmation`/`pending_tool_calls` to the caller.
                    # BLOCKED plus `blocked_reason="needs_confirmation"` (in `_TERMINAL_BLOCKS`,
                    # `agentic_loop.py`) stops the loop here instead, so this outcome -- and the
                    # report on it -- is what the kernel actually sees.
                    blocked_reason = NEEDS_CONFIRMATION
                    updated.append(step.blocked(NEEDS_CONFIRMATION))
                    # What the user will be asked to approve, exactly as it would be dispatched:
                    # the same resolution `_run_step` just did, against the same plan state.
                    resolved, _ = _resolve_output_tokens(step.arguments, current)
                    pending_calls.append(PendingCall(
                        step_id=step.id.value, call_id=getattr(report, "call_id", None)
                        or f"step_{step.id.value}",
                        tool=step.tool or "", arguments=resolved,
                    ))
                    await self._emit(
                        on_event,
                        StepEvent(
                            step_id=step.id.value, kind=str(step.kind), status="blocked",
                            description=step.description, detail=text[:200], elapsed_ms=elapsed,
                        ),
                    )
                    continue
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
                    # Prefer the tool's own structured `error_kind` (e.g. "unresolved_argument",
                    # "policy_denied", "timeout" -- see `domain/failure_kinds.py`) over the
                    # truncated human-readable message: this `PlanStep.error_kind` is what
                    # `PlanningEngine.replan()`'s context block surfaces to the model
                    # ("Failed: ... (unresolved_argument)"), and a structured reason is something
                    # a replanned attempt can actually act on, where free text is not.
                    error_kind = getattr(report, "error_kind", None) or text[:120]
                    failed = step.failed(error_kind, elapsed)
                    failure_messages[step.id.value] = text[:400]
                    # Retry only when the step can be safely repeated. A side-effecting tool that
                    # failed may have already acted, so retrying could duplicate the effect.
                    side_effecting = self._is_side_effecting(step)
                    retry = failed.can_retry and not side_effecting
                    if retry and self._recovery is not None:
                        # ADR-039: the failure's kind decides. `max_attempts` stays the ceiling
                        # (`can_retry` above); the policy can only refuse a retry or delay it.
                        decision = self._recovery.decide(
                            error_kind, message=text, attempt=failed.attempts,
                            max_attempts=failed.max_attempts, side_effecting=side_effecting,
                        )
                        retry = decision.retry
                        if retry:
                            backoff_s = max(backoff_s, decision.backoff_s)
                    if retry:
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
            if backoff_s > 0:
                # One wait for the whole batch (not one per step), and never past the deadline.
                await self._sleep(max(0.0, min(backoff_s, deadline - time.perf_counter())))

        duration = (time.perf_counter() - started) * 1000.0
        return ExecutionOutcome(
            plan=current,
            results=results,
            tool_reports=tool_reports,
            succeeded=current.succeeded,
            duration_ms=duration,
            steps_run=steps_run,
            blocked_reason=blocked_reason,
            pending_calls=pending_calls,
            tainted=bool(getattr(context, "tainted", False)),
            failure_messages=failure_messages,
        )

    def spec_for(self, tool: str) -> Any:
        """The registered spec for a tool name, or ``None``. Public so the agent loop's critic
        can ask the same registry the executor dispatches through."""
        try:
            return self._tools.spec(tool)
        except Exception:
            return None

    def _tool_spec(self, step: PlanStep) -> Any:
        if step.kind is not StepKind.TOOL or not step.tool:
            return None
        try:
            return self._tools.spec(step.tool)
        except Exception:
            # Unknown tool: the registry refuses it at dispatch; it neither taints nor is gated.
            return None

    def _taint_ordered(
        self, batch: Sequence[PlanStep], context: ToolContext
    ) -> Sequence[PlanStep]:
        """Keep a taint-gated step out of a batch that contains a tainting step.

        The registry taints the shared context when an ``untrusted_content`` tool *returns*. A
        side-effecting step dispatched concurrently in the same batch runs its policy check
        before that, against a still-clean context, and auto-approves -- so whether a step in a
        turn with untrusted content in scope needed confirmation depended on scheduling, not on
        policy (the residual gap the architecture audit recorded). Deferring the gated step to
        the next iteration makes its policy check observe the taint, which is the same answer a
        sequential run of the plan gives.

        Only the mix is split: a batch with no tainting step, or no step the taint rule can gate
        (side-effecting and not ``workspace_local``), runs exactly as before. If every step in
        the batch is gated and at least one is tainting (e.g. two MCP tools), the first tainting
        step runs alone.
        """
        if context.tainted or len(batch) < 2:
            return batch
        specs = [self._tool_spec(step) for step in batch]
        tainting = [
            i for i, spec in enumerate(specs)
            if spec is not None and "untrusted_content" in spec.tags
        ]
        if not tainting:
            return batch
        gated = {
            i for i, spec in enumerate(specs)
            if spec is not None and spec.side_effecting and not spec.workspace_local
        }
        if not gated:
            return batch
        ungated = [step for i, step in enumerate(batch) if i not in gated]
        if ungated:
            return ungated
        return [batch[tainting[0]]]

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
        confirmations: ConfirmationGrant | None,
        handlers: Mapping[StepKind, Callable[[PlanStep], Any]],
        plan: Plan,
    ) -> tuple[str, bool, float, Any]:
        started = time.perf_counter()

        if step.kind is StepKind.TOOL and step.tool:
            arguments, unresolved = _resolve_output_tokens(step.arguments, plan)
            if unresolved:
                return (
                    f"could not resolve reference(s) to an earlier step's output: "
                    f"{', '.join(sorted(set(unresolved)))}",
                    False,
                    (time.perf_counter() - started) * 1000.0,
                    _UNRESOLVED_REFERENCE,
                )
            call_id = f"step_{step.id.value}"
            call = ToolCall(
                id=ToolCallId(call_id),
                name=step.tool,
                arguments=arguments,
            )
            # Confirmed only by a grant for this exact call: same call id, same tool, same
            # resolved arguments as the user approved, consumed on first use. A different call
            # -- a re-planned target, changed arguments, a second dispatch -- is held again.
            confirmed = confirmations is not None and confirmations.admits(
                call_id, step.tool, arguments
            )
            result, report = await self._tools.execute(
                call,
                context,
                capabilities=capabilities,
                confirmed=confirmed,
            )
            return result.content, result.ok, (time.perf_counter() - started) * 1000.0, report

        handler = handlers.get(step.kind)
        if handler is None:
            # A step kind with no handler is a configuration gap, not a user-visible failure --
            # skip it with an explanation rather than failing the whole plan.
            return (
                f"No handler is configured for {step.kind} steps.",
                True,
                (time.perf_counter() - started) * 1000.0,
                _NO_HANDLER,
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
        confirmations: ConfirmationGrant | None = None,
        handlers: Mapping[StepKind, Callable[[PlanStep], Any]] | None = None,
        gate: Any = None,
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
                    confirmations=confirmations,
                    handlers=handlers,
                    on_event=emit,
                    gate=gate,
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
