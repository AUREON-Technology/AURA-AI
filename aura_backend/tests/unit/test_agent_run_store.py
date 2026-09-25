"""The ``AgentRunStore`` contract (GAP-024), run against every adapter.

The in-memory adapter always runs. The Postgres adapter runs only when
``AURA_TEST_POSTGRES_URL`` names a real, **throwaway** database (the agent-run tables are
truncated before each test) -- the default suite needs no infrastructure, but asyncpg's type
mapping (JSONB round trips, DOUBLE PRECISION, TIMESTAMPTZ) and the owner-scoped upsert's
``WHERE`` clause are only demonstrable against a real server:

    initdb -D /tmp/pg && pg_ctl -D /tmp/pg -o "-p 55436 -k /tmp/pg" start
    createdb -h /tmp/pg -p 55436 aura_test
    AURA_TEST_POSTGRES_URL=postgresql://$USER@localhost:55436/aura_test \\
        pytest tests/unit/test_agent_run_store.py

The adapter is the engineer-runs adapter, extended (``PostgresAgentRunStore`` is the same
class), so the engineer-run ledger is exercised alongside to show the two coexist.
"""

from __future__ import annotations

import importlib.util
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aura_core.adapters.memory_stores import InMemoryEngineerRunStore
from aura_core.domain.agent_runs import (
    AgentBudget,
    AgentPhase,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepKind,
    run_metrics,
    safe_detail,
)
from aura_core.domain.engineering import EngineerRun, EngineerRunStatus
from aura_core.domain.identifiers import AgentRunId, AgentStepId, EngineerRunId, UserId
from aura_core.ports.coding import AgentRunStore, EngineerRunStore

PG_URL = os.environ.get("AURA_TEST_POSTGRES_URL", "")
BACKEND = Path(__file__).resolve().parents[2]

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
ALICE = UserId("alice")
BOB = UserId("bob")


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield InMemoryEngineerRunStore()
        return
    if not PG_URL:
        pytest.skip("AURA_TEST_POSTGRES_URL not set (opt-in: needs a throwaway Postgres)")
    from aura_core.adapters.postgres_engineer_runs import PostgresAgentRunStore
    from aura_core.adapters.postgres_store import PostgresPool

    pool = PostgresPool(PG_URL)
    adapter = PostgresAgentRunStore(PG_URL, pool=pool)
    await adapter._ensure_schema()
    raw = await pool._get_pool()
    async with raw.acquire() as conn:
        await conn.execute("TRUNCATE aura_agent_steps, aura_agent_runs, aura_engineer_runs")
    try:
        yield adapter
    finally:
        await pool.close()


def _run(user=ALICE, *, agent="engineer", at=T0, status=AgentRunStatus.RUNNING, **kw) -> AgentRun:
    return AgentRun(
        id=kw.pop("id", None) or AgentRunId.new(), user_id=user, agent_id=agent, turn_id="t1",
        correlation_id="c1", status=status, created_at=at, updated_at=at, **kw,
    )


def _step(run: AgentRun, seq: int, **kw) -> AgentStep:
    return AgentStep(
        id=AgentStepId.new(), run_id=run.id, user_id=run.user_id, seq=seq,
        iteration=kw.pop("iteration", 1), phase=kw.pop("phase", AgentPhase.EXECUTE),
        kind=kw.pop("kind", AgentStepKind.TOOL_CALL), created_at=T0 + timedelta(seconds=seq),
        **kw,
    )


def test_both_adapters_satisfy_the_extended_port() -> None:
    from aura_core.adapters.postgres_engineer_runs import (
        PostgresAgentRunStore,
        PostgresEngineerRunStore,
    )

    assert PostgresAgentRunStore is PostgresEngineerRunStore
    for adapter in (InMemoryEngineerRunStore(), PostgresAgentRunStore("postgresql://u@h/db")):
        assert isinstance(adapter, AgentRunStore)
        assert isinstance(adapter, EngineerRunStore)


class TestContract:
    async def test_round_trips_every_field(self, store) -> None:
        run = _run(
            goal_digest="d" * 32, goal_chars=42,
            budget=AgentBudget(max_iterations=3, token_budget=900, time_budget_s=12.5,
                               tool_budget=7, risk_budget=11),
            iterations=2, replans=1, replans_recovered=1, tokens_used=321, tool_calls=4,
            tool_calls_ok=3, risk_spent=6, elapsed_s=1.25, unsafe_prevented=1,
            plan_steps_total=5, plan_steps_succeeded=4, stop_reason="tool_budget_exceeded",
            exhausted_budget="tools", status=AgentRunStatus.BUDGET_EXHAUSTED,
            finished_at=T0 + timedelta(seconds=2),
        )
        await store.save_run(run)
        got = await store.get_run(run.id, user_id=ALICE)
        assert got == run
        assert isinstance(got.elapsed_s, float)

    async def test_upsert_updates_live_state(self, store) -> None:
        run = _run()
        await store.save_run(run)
        done = run.with_(status=AgentRunStatus.COMPLETED, iterations=3,
                         updated_at=T0 + timedelta(seconds=5), finished_at=T0 + timedelta(seconds=5))
        await store.save_run(done)
        got = await store.get_run(run.id, user_id=ALICE)
        assert got.status == AgentRunStatus.COMPLETED and got.iterations == 3
        assert got.created_at == T0

    async def test_steps_are_ordered_and_detail_round_trips(self, store) -> None:
        run = _run()
        await store.save_run(run)
        await store.append_step(_step(run, 2, tool="git_diff", status="succeeded",
                                      arguments_digest="abcd1234abcd1234", risk_cost=2,
                                      detail={"allowed": True, "duration_ms": 1.5,
                                              "tools": ["a", "b"]}))
        await store.append_step(_step(run, 1, kind=AgentStepKind.DECISION, phase=AgentPhase.PLAN))
        steps = await store.list_steps(run.id, user_id=ALICE)
        assert [s.seq for s in steps] == [1, 2]
        assert steps[1].detail == {"allowed": True, "duration_ms": 1.5, "tools": ["a", "b"]}
        assert steps[1].risk_cost == 2 and steps[1].tool == "git_diff"

    async def test_list_is_newest_first_and_filters(self, store) -> None:
        a = _run(at=T0, agent="engineer", status=AgentRunStatus.COMPLETED)
        b = _run(at=T0 + timedelta(minutes=1), agent="task_planner", status=AgentRunStatus.FAILED)
        c = _run(at=T0 + timedelta(minutes=2), agent="engineer", status=AgentRunStatus.FAILED)
        for r in (a, b, c):
            await store.save_run(r)
        assert [r.id for r in await store.list_runs(ALICE)] == [c.id, b.id, a.id]
        assert [r.id for r in await store.list_runs(ALICE, agent_id="engineer")] == [c.id, a.id]
        assert [r.id for r in await store.list_runs(ALICE, status="failed")] == [c.id, b.id]
        assert [r.id for r in await store.list_runs(ALICE, limit=1)] == [c.id]


class TestOwnerScoping:
    async def test_another_users_run_reads_as_absent(self, store) -> None:
        run = _run()
        await store.save_run(run)
        await store.append_step(_step(run, 1))
        assert await store.get_run(run.id, user_id=BOB) is None
        assert await store.list_runs(BOB) == []
        assert await store.list_steps(run.id, user_id=BOB) == []

    async def test_an_upsert_never_re_owns_a_run(self, store) -> None:
        run = _run()
        await store.save_run(run)
        with pytest.raises(ValueError):
            await store.save_run(run.with_(user_id=BOB, status=AgentRunStatus.FAILED))
        got = await store.get_run(run.id, user_id=ALICE)
        assert got.status == AgentRunStatus.RUNNING

    async def test_engineer_runs_still_work_beside_agent_runs(self, store) -> None:
        await store.record(EngineerRun(
            id=EngineerRunId.new(), user_id=ALICE, turn_id="t1", correlation_id="c1",
            persona="engineer", tool="git_status", call_id="step_s1",
            status=EngineerRunStatus.SUCCEEDED, created_at=T0, updated_at=T0,
        ))
        await store.save_run(_run())
        assert len(await store.list_for_user(ALICE)) == 1
        assert await store.list_for_user(BOB) == []


class TestDomain:
    def test_safe_detail_drops_anything_that_could_be_content(self) -> None:
        detail = safe_detail({
            "steps": 3, "ok": True, "reason": "policy_denied", "tools": ["a", "x" * 500, 3],
            "prompt": "p" * 500, "nested": {"a": 1},
        })
        assert detail == {"steps": 3, "ok": True, "reason": "policy_denied", "tools": ["a", 3]}

    def test_budget_rejects_negative_values(self) -> None:
        with pytest.raises(ValueError):
            AgentBudget(tool_budget=-1)
        with pytest.raises(ValueError):
            AgentBudget(max_iterations=-1)

    def test_run_metrics(self) -> None:
        run = _run(
            status=AgentRunStatus.PARTIAL, budget=AgentBudget(max_iterations=4),
            plan_steps_total=4, plan_steps_succeeded=3, tool_calls=4, tool_calls_ok=3,
            replans=2, replans_recovered=1, iterations=3, unsafe_prevented=1,
        )
        m = run_metrics(run)
        assert m["completed"] is False
        assert m["partial_completion"] == 0.75
        assert m["tool_accuracy"] == 0.75
        assert m["iteration_utilization"] == 0.5
        assert m["replan_success"] == 0.5
        assert m["unsafe_prevented"] == 1

    def test_single_pass_run_has_no_iteration_utilization(self) -> None:
        m = run_metrics(_run(status=AgentRunStatus.COMPLETED))
        assert m["iteration_utilization"] is None and m["partial_completion"] == 1.0


def _normalise(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_the_migration_mirrors_the_adapter_schema() -> None:
    """``0008_agent_runs`` and the adapter's lazy bootstrap must create the same tables, or a
    migrated database and a first-use database would differ."""
    from aura_core.adapters.postgres_engineer_runs import AGENT_RUNS_SCHEMA

    spec = importlib.util.spec_from_file_location(
        "mig_0008", BACKEND / "alembic" / "versions" / "0008_agent_runs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0008"
    assert module.down_revision == "0006"
    assert [_normalise(s) for s in module.UPGRADE_STATEMENTS] == [
        _normalise(s) for s in AGENT_RUNS_SCHEMA
    ]
