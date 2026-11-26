"""A pass costs a fixed, small number of awaited store writes, however many tools it ran
(ticket problem 7). Before: about k + 5 per pass for k tool calls."""

from __future__ import annotations

import pytest

from aura_core.adapters.memory_stores import InMemoryEngineerRunStore
from aura_core.domain.agent_runs import AgentBudget
from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.execution import ExecutionEngine
from aura_core.execution.agentic_loop import AgenticExecutor, AgentRunContext
from aura_core.ports.runtime import ToolContext


class _CountingStore(InMemoryEngineerRunStore):
    """The real in-memory store, counting every awaited write."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0

    async def save_run(self, run):
        self.writes += 1
        return await super().save_run(run)

    async def append_step(self, step):
        self.writes += 1
        return await super().append_step(step)

    async def append_steps(self, steps):
        self.writes += 1
        return await super().append_steps(steps)


def _plan(k: int) -> Plan:
    steps = tuple(
        PlanStep(
            id=StepId(f"s{i}"), kind=StepKind.TOOL, tool="calculator",
            description=f"add {i}", arguments={"expression": f"{i}+{i}"},
        )
        for i in range(1, k + 1)
    )
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps)


async def _run(tools, user, k: int) -> tuple[_CountingStore, object]:
    store = _CountingStore()
    loop = AgenticExecutor(
        planner=None, executor=ExecutionEngine(tools=tools, max_parallel=4), runs=store
    )
    context = ToolContext(user_id=user, correlation_id="c", deadline_s=30.0)
    outcome = await loop.run(
        _plan(k), context=context, max_iterations=0, deadline_s=30.0,
        budget=AgentBudget(max_iterations=0),
        run_context=AgentRunContext(
            user_id=user, agent_id="orchestrator", turn_id="t", correlation_id="c", goal="g",
        ),
    )
    return store, outcome


class TestWriteCount:
    async def test_a_single_pass_costs_the_same_writes_for_one_tool_or_eight(
        self, tools, user
    ) -> None:
        store_1, out_1 = await _run(tools, user, 1)
        store_8, out_8 = await _run(tools, user, 8)
        assert out_1 is not None and out_1.succeeded and out_8 is not None and out_8.succeeded
        assert store_1.writes == store_8.writes

    async def test_at_most_three_awaited_writes_per_pass(self, tools, user) -> None:
        store, _ = await _run(tools, user, 6)
        # start (run + step) + one pass (run + batch of steps) + finish (run + batch): the pass
        # itself is two writes however many tools it ran, so the whole one-pass run is six.
        assert store.writes <= 6

    async def test_every_step_is_still_recorded_in_order(self, tools, user) -> None:
        store, outcome = await _run(tools, user, 3)
        run_id = outcome.run_id
        assert run_id is not None
        from aura_core.domain.identifiers import AgentRunId

        steps = await store.list_steps(AgentRunId(run_id), user_id=user)
        seqs = [s.seq for s in steps]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert sum(1 for s in steps if s.kind == "tool_call") == 3
        run = await store.get_run(AgentRunId(run_id), user_id=user)
        assert run is not None and run.tool_calls == 3
