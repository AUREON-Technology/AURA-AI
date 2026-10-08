"""``AIKernel._record_engineer_runs``: durable history for engineer-agent tool outcomes.

Exercised directly against the method rather than a full turn, since the interesting behavior --
which reports get recorded, what fields get extracted, that failures don't fail the turn -- lives
entirely in that one method and does not need a real planner/router/generation pipeline to prove.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aura_core.adapters import InMemoryEngineerRunStore
from aura_core.domain.engineering import EngineerRunStatus
from aura_core.domain.identifiers import CorrelationId, PlanId, StepId, TurnId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.domain.tools import ToolCategory, ToolSensitivity, ToolSpec
from aura_core.execution.engine import ExecutionOutcome
from aura_core.kernel.kernel import AIKernel, KernelDependencies, TurnRequest

pytestmark = pytest.mark.asyncio


class _ToolReport:
    def __init__(self, *, call_id, tool, ok, needs_confirmation=False, arguments=None):
        self.call_id = call_id
        self.tool = tool
        self.ok = ok
        self.needs_confirmation = needs_confirmation
        self.arguments = arguments or {}


class _FakeTools:
    """Only the two engineer-agent tools this test cares about are "registered"; anything else
    looks like an unknown tool, matching how a real registry raises for an unregistered name."""

    _SPECS = {
        "github_create_draft_pull_request": ToolSpec(
            name="github_create_draft_pull_request", description="d",
            category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.CONFIDENTIAL,
            side_effecting=True, required_capability="engineer_agent",
        ),
        "github_create_branch": ToolSpec(
            name="github_create_branch", description="d",
            category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.CONFIDENTIAL,
            side_effecting=True, required_capability="engineer_agent",
        ),
        "calculator": ToolSpec(
            name="calculator", description="d", category=ToolCategory.UTILITY,
        ),
    }

    def spec(self, name: str) -> ToolSpec:
        if name not in self._SPECS:
            raise KeyError(name)
        return self._SPECS[name]


def _kernel(engineer_runs) -> AIKernel:
    return AIKernel(KernelDependencies(
        router=None, prompts=None, context=None, tools=_FakeTools(), engineer_runs=engineer_runs,
    ))


def _outcome(steps: tuple[PlanStep, ...], reports: list[_ToolReport]) -> ExecutionOutcome:
    plan = Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps)
    return ExecutionOutcome(plan=plan, results={}, tool_reports=reports, succeeded=True)


class TestRecordEngineerRuns:
    async def test_records_a_successful_pr_with_url_extracted(self) -> None:
        store = InMemoryEngineerRunStore()
        step = PlanStep(
            id=StepId("s1"), kind=StepKind.TOOL, tool="github_create_draft_pull_request",
            description="open PR",
        ).started().succeeded("Opened draft PR #7: https://github.com/o/r/pull/7", 5.0)
        outcome = _outcome(
            (step,),
            [_ToolReport(call_id="step_s1", tool="github_create_draft_pull_request", ok=True)],
        )
        kernel = _kernel(store)

        await kernel._record_engineer_runs(
            TurnRequest(message="m", user_id=UserId("u1")),
            TurnId("t1"), CorrelationId("c1"), "engineer", outcome,
        )

        runs = await store.list_for_user(UserId("u1"))
        assert len(runs) == 1
        assert runs[0].status == EngineerRunStatus.SUCCEEDED
        assert runs[0].pr_url == "https://github.com/o/r/pull/7"
        assert runs[0].persona == "engineer"
        assert runs[0].tool == "github_create_draft_pull_request"

    async def test_records_branch_from_arguments_not_result_text(self) -> None:
        store = InMemoryEngineerRunStore()
        step = PlanStep(
            id=StepId("s1"), kind=StepKind.TOOL, tool="github_create_branch",
            description="branch", arguments={"branch": "feature/x", "from_ref": "main"},
        ).started().succeeded("Created branch 'feature/x' on o/r from 'main' (abc1234)", 5.0)
        outcome = _outcome(
            (step,),
            [_ToolReport(
                call_id="step_s1", tool="github_create_branch", ok=True,
                arguments={"branch": "feature/x", "from_ref": "main"},
            )],
        )
        kernel = _kernel(store)

        await kernel._record_engineer_runs(
            TurnRequest(message="m", user_id=UserId("u1")),
            TurnId("t1"), CorrelationId("c1"), "engineer", outcome,
        )

        runs = await store.list_for_user(UserId("u1"))
        assert runs[0].branch == "feature/x"
        assert runs[0].commit_sha == "abc1234"

    async def test_a_pending_confirmation_records_as_pending_not_failed(self) -> None:
        store = InMemoryEngineerRunStore()
        step = PlanStep(
            id=StepId("s1"), kind=StepKind.TOOL, tool="github_create_draft_pull_request",
            description="open PR",
        ).started().failed("confirmation_required", 1.0)
        outcome = _outcome(
            (step,),
            [_ToolReport(
                call_id="step_s1", tool="github_create_draft_pull_request", ok=False,
                needs_confirmation=True,
            )],
        )
        kernel = _kernel(store)

        await kernel._record_engineer_runs(
            TurnRequest(message="m", user_id=UserId("u1")),
            TurnId("t1"), CorrelationId("c1"), "engineer", outcome,
        )

        runs = await store.list_for_user(UserId("u1"))
        assert runs[0].status == EngineerRunStatus.PENDING

    async def test_non_engineer_tools_are_not_recorded(self) -> None:
        store = InMemoryEngineerRunStore()
        step = PlanStep(
            id=StepId("s1"), kind=StepKind.TOOL, tool="calculator", description="add",
        ).started().succeeded("4", 1.0)
        outcome = _outcome((step,), [_ToolReport(call_id="step_s1", tool="calculator", ok=True)])
        kernel = _kernel(store)

        await kernel._record_engineer_runs(
            TurnRequest(message="m", user_id=UserId("u1")),
            TurnId("t1"), CorrelationId("c1"), "orchestrator", outcome,
        )

        assert await store.list_for_user(UserId("u1")) == []

    async def test_no_store_configured_is_a_silent_no_op(self) -> None:
        step = PlanStep(
            id=StepId("s1"), kind=StepKind.TOOL, tool="github_create_draft_pull_request",
            description="open PR",
        ).started().succeeded("Opened draft PR #1: https://github.com/o/r/pull/1", 1.0)
        outcome = _outcome(
            (step,),
            [_ToolReport(call_id="step_s1", tool="github_create_draft_pull_request", ok=True)],
        )
        kernel = _kernel(engineer_runs=None)

        # Must not raise -- absence of the store means this feature is simply off.
        await kernel._record_engineer_runs(
            TurnRequest(message="m", user_id=UserId("u1")),
            TurnId("t1"), CorrelationId("c1"), "engineer", outcome,
        )

    async def test_a_store_write_failure_does_not_raise(self) -> None:
        class _BoomStore:
            async def record(self, run):
                raise RuntimeError("boom")

        step = PlanStep(
            id=StepId("s1"), kind=StepKind.TOOL, tool="github_create_draft_pull_request",
            description="open PR",
        ).started().succeeded("Opened draft PR #1: https://github.com/o/r/pull/1", 1.0)
        outcome = _outcome(
            (step,),
            [_ToolReport(call_id="step_s1", tool="github_create_draft_pull_request", ok=True)],
        )
        kernel = _kernel(_BoomStore())

        await kernel._record_engineer_runs(
            TurnRequest(message="m", user_id=UserId("u1")),
            TurnId("t1"), CorrelationId("c1"), "engineer", outcome,
        )
