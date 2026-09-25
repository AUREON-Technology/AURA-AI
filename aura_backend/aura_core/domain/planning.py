"""Planning entities: goals, steps, dependency graphs, plan state.

A :class:`Plan` is a DAG of :class:`PlanStep`, not a list. Real assistant work has genuine
parallelism ("check my calendar" and "check the weather" are independent) and genuine ordering
("find the file" before "summarise it"), and a list can express only the second.

Invariants enforced here rather than discovered at runtime:
  * step ids are unique,
  * dependencies reference existing steps,
  * the graph is acyclic,
  * a step becomes runnable only when every dependency has succeeded.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .errors import PlanningError
from .identifiers import PlanId, StepId

__all__ = [
    "StepStatus",
    "StepKind",
    "Priority",
    "Goal",
    "PlanStep",
    "Plan",
    "PlanRevision",
]


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"

    def __str__(self) -> str:
        return self.value

    @property
    def is_terminal(self) -> bool:
        return self in {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED}


class StepKind(str, enum.Enum):
    """What executing a step means. The execution engine dispatches on this."""

    TOOL = "tool"
    RETRIEVE = "retrieve"
    RECALL = "recall"
    GENERATE = "generate"
    REFLECT = "reflect"
    VERIFY = "verify"
    RESPOND = "respond"

    def __str__(self) -> str:
        return self.value


class Priority(int, enum.Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Goal:
    """What the user is trying to achieve, separated from how they phrased it.

    ``success_criteria`` are what the verification stage checks against. Without them,
    "did it work?" has no answer and self-reflection degrades into the model grading its own
    vibes.
    """

    description: str
    success_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    priority: Priority = Priority.NORMAL
    deadline: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Goal.description must be non-empty")


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One node in the plan DAG."""

    id: StepId
    kind: StepKind
    description: str
    depends_on: tuple[StepId, ...] = ()
    tool: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    priority: Priority = Priority.NORMAL
    attempts: int = 0
    max_attempts: int = 2
    result: str | None = None
    error_kind: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.kind is StepKind.TOOL and not self.tool:
            raise ValueError(f"step {self.id} is a TOOL step but names no tool")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

    @property
    def can_retry(self) -> bool:
        return self.status is StepStatus.FAILED and self.attempts < self.max_attempts

    def started(self) -> "PlanStep":
        return replace(
            self, status=StepStatus.RUNNING, attempts=self.attempts + 1, started_at=_utcnow()
        )

    def succeeded(self, result: str, duration_ms: float) -> "PlanStep":
        return replace(
            self,
            status=StepStatus.SUCCEEDED,
            result=result,
            finished_at=_utcnow(),
            duration_ms=duration_ms,
        )

    def failed(self, error_kind: str, duration_ms: float) -> "PlanStep":
        return replace(
            self,
            status=StepStatus.FAILED,
            error_kind=error_kind,
            finished_at=_utcnow(),
            duration_ms=duration_ms,
        )

    def skipped(self, reason: str) -> "PlanStep":
        return replace(self, status=StepStatus.SKIPPED, result=reason, finished_at=_utcnow())

    def blocked(self, reason: str) -> "PlanStep":
        return replace(self, status=StepStatus.BLOCKED, error_kind=reason, finished_at=_utcnow())


@dataclass(frozen=True, slots=True)
class PlanRevision:
    """A record of why a plan changed, so replanning is auditable rather than mysterious."""

    revision: int
    reason: str
    at: datetime = field(default_factory=_utcnow)
    added_steps: tuple[StepId, ...] = ()
    removed_steps: tuple[StepId, ...] = ()


@dataclass(frozen=True, slots=True)
class Plan:
    """An immutable plan state. Every transition returns a new ``Plan``.

    Immutability matters here specifically: the execution engine runs steps concurrently, and a
    mutable shared plan would need locking around every status change. Returning new instances
    makes the state machine trivially safe and trivially testable.
    """

    id: PlanId
    goal: Goal
    steps: tuple[PlanStep, ...] = ()
    revisions: tuple[PlanRevision, ...] = ()
    created_at: datetime = field(default_factory=_utcnow)
    max_steps: int = 24
    #: Tokens the planner generation that produced *this revision* spent (prompt + completion),
    #: as the provider reported them; ``0`` when unknown. Per revision, not cumulative --
    #: :meth:`revised` resets it, so an agent loop summing it across revisions never double
    #: counts (``AgenticExecutor``'s token budget, GAP-024).
    generation_tokens: int = 0

    def __post_init__(self) -> None:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise PlanningError("plan contains duplicate step ids")
        if len(self.steps) > self.max_steps:
            raise PlanningError(
                f"plan has {len(self.steps)} steps, exceeding max_steps={self.max_steps}",
                details={"step_count": len(self.steps)},
            )
        known = set(ids)
        for step in self.steps:
            missing = [d.value for d in step.depends_on if d not in known]
            if missing:
                raise PlanningError(
                    f"step {step.id} depends on unknown steps",
                    details={"step": step.id.value, "missing": missing},
                )
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        """Iterative DFS -- recursion would cap plan depth at the interpreter's limit and blow
        the stack on a malicious/degenerate plan rather than raising a domain error."""
        adjacency = {s.id: s.depends_on for s in self.steps}
        WHITE, GREY, BLACK = 0, 1, 2
        colour = {sid: WHITE for sid in adjacency}
        for root in adjacency:
            if colour[root] != WHITE:
                continue
            stack: list[tuple[StepId, bool]] = [(root, False)]
            while stack:
                node, processed = stack.pop()
                if processed:
                    colour[node] = BLACK
                    continue
                if colour[node] == GREY:
                    continue
                colour[node] = GREY
                stack.append((node, True))
                for dep in adjacency[node]:
                    if colour[dep] == GREY:
                        raise PlanningError(
                            "plan contains a dependency cycle",
                            details={"at": node.value, "via": dep.value},
                        )
                    if colour[dep] == WHITE:
                        stack.append((dep, False))

    # -- queries -----------------------------------------------------------------------

    def step(self, step_id: StepId) -> PlanStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise PlanningError(f"unknown step {step_id.value}", details={"step": step_id.value})

    @property
    def is_complete(self) -> bool:
        return all(s.status.is_terminal or s.status is StepStatus.BLOCKED for s in self.steps)

    @property
    def succeeded(self) -> bool:
        return self.is_complete and all(
            s.status in {StepStatus.SUCCEEDED, StepStatus.SKIPPED} for s in self.steps
        )

    @property
    def failed_steps(self) -> tuple[PlanStep, ...]:
        return tuple(s for s in self.steps if s.status is StepStatus.FAILED)

    def ready_steps(self) -> tuple[PlanStep, ...]:
        """Steps whose dependencies have all succeeded.

        A dependency that failed or was skipped does **not** unblock its dependents; they are
        surfaced by :meth:`blocked_steps` instead, so a partial failure produces a clear
        "these could not run because X failed" rather than silently running them without input.
        """
        by_id = {s.id: s for s in self.steps}
        ready = []
        for step in self.steps:
            if step.status is not StepStatus.PENDING:
                continue
            if all(by_id[d].status is StepStatus.SUCCEEDED for d in step.depends_on):
                ready.append(step)
        # Highest priority first, then declaration order for determinism.
        return tuple(sorted(ready, key=lambda s: (-s.priority.value, self.steps.index(s))))

    def blocked_steps(self) -> tuple[PlanStep, ...]:
        by_id = {s.id: s for s in self.steps}
        return tuple(
            s
            for s in self.steps
            if s.status is StepStatus.PENDING
            and any(
                by_id[d].status in {StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.BLOCKED}
                for d in s.depends_on
            )
        )

    def progress(self) -> tuple[int, int]:
        done = sum(1 for s in self.steps if s.status.is_terminal)
        return done, len(self.steps)

    # -- transitions -------------------------------------------------------------------

    def with_step(self, step: PlanStep) -> "Plan":
        return replace(
            self, steps=tuple(step if s.id == step.id else s for s in self.steps)
        )

    def with_steps(self, steps: Iterable[PlanStep]) -> "Plan":
        updated = {s.id: s for s in steps}
        return replace(self, steps=tuple(updated.get(s.id, s) for s in self.steps))

    def revised(
        self,
        *,
        steps: Sequence[PlanStep],
        reason: str,
        generation_tokens: int = 0,
    ) -> "Plan":
        """Produce a replanned version, recording what changed and why (ADR: replanning must be
        auditable; a plan that silently rewrites itself cannot be debugged)."""
        before = {s.id for s in self.steps}
        after = {s.id for s in steps}
        revision = PlanRevision(
            revision=len(self.revisions) + 1,
            reason=reason,
            added_steps=tuple(sorted(after - before, key=lambda s: s.value)),
            removed_steps=tuple(sorted(before - after, key=lambda s: s.value)),
        )
        return replace(
            self,
            steps=tuple(steps),
            revisions=self.revisions + (revision,),
            generation_tokens=generation_tokens,
        )

    def mark_blocked_steps(self) -> "Plan":
        blocked = {
            s.id: s.blocked("upstream_failed") for s in self.blocked_steps()
        }
        if not blocked:
            return self
        return replace(self, steps=tuple(blocked.get(s.id, s) for s in self.steps))
