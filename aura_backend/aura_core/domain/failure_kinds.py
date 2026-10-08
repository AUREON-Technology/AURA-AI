"""``FailureKind``: the closed set of machine-readable ``error_kind`` strings a tool dispatch,
plan step, or step-argument resolution can produce.

Before this, ``error_kind`` was ad hoc strings scattered across ``tools/registry.py`` (mirroring
``AuraError.kind`` values), ``execution/engine.py`` (exception class names), and now the two new
values this engineer-orchestration hardening adds (``unresolved_argument``,
``unresolved_reference``). Collecting them here does not force a rewrite of every call site --
most still spell the literal string, and that is fine, since a ``str`` enum member compares equal
to its value -- it gives ``PlanningEngine.replan()``'s context-building and any future recovery
logic one place to see the whole vocabulary instead of grepping for it.

Kept deliberately low-cardinality and stdlib-only (domain/ layering rule): this is metric-label
and prompt-context material, not a place for free-form text.
"""

from __future__ import annotations

import enum

__all__ = ["FailureKind"]


class FailureKind(str, enum.Enum):
    # Pre-dispatch, no tool body reached, no network call attempted -- the two kinds this
    # engineer-orchestration hardening adds (see tools/schema.py, execution/engine.py).
    UNRESOLVED_ARGUMENT = "unresolved_argument"
    """A ``${...}`` template token survived to schema validation, or a ``format``-hinted
    argument (e.g. ``github_repo``) does not parse as what it claims to be -- an LLM-fabricated
    identifier standing in for real data, caught before any network call."""

    UNRESOLVED_REFERENCE = "unresolved_reference"
    """A step's ``${step_id.output}`` argument names a step that never ran, has not SUCCEEDED, or
    produced no result -- the dependent tool was never dispatched."""

    # Mirrors AuraError.kind values already produced by ToolRegistry.execute (domain/errors.py).
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    TOOL_TIMEOUT = "tool_timeout"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"

    # Policy/confirmation outcomes (tools/registry.py, tools/policy.py).
    POLICY_DENIED = "policy_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"

    # Plan/execution-loop-level outcomes (execution/engine.py, execution/agentic_loop.py) --
    # these are `ExecutionOutcome.blocked_reason` values, not per-step `error_kind`, but they
    # flow into the same replan-context vocabulary (Item 5).
    DEPENDENCIES_FAILED = "dependencies_failed"
    STEP_BUDGET_EXCEEDED = "step_budget_exceeded"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    NEEDS_CONFIRMATION = "needs_confirmation"
    UPSTREAM_FAILED = "upstream_failed"
