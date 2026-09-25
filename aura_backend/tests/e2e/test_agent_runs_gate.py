"""GAP-024: the run-level agent-loop evaluation, as a pytest gate beside ``evaluation/gate.py``.

Twelve scripted scenarios (``evaluation/suites/agent_runs.jsonl``) drive ``AIKernel.run`` with a
scripted planner, deterministic evaluation tools and a ``FrozenClock``; every metric is read back
from the durable ``AgentRun`` the loop recorded. Each scenario's expected terminal status and
exhausted budget is asserted individually, so a regression names the case rather than just
moving an average.
"""

from __future__ import annotations

import json

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.evaluation.agent_runs import AgentRunEvaluator, load_agent_run_scenarios
from aura_core.evaluation.harness import RegressionGate
from evaluation import gate as eval_gate

SCENARIOS = eval_gate.SUITE_DIR / "agent_runs.jsonl"
BASELINE = eval_gate.SUITE_DIR / "agent_runs_baseline.json"


@pytest.fixture
async def result(container):
    await container.startup()
    try:
        evaluator = AgentRunEvaluator.for_container(container, clock=FrozenClock())
        yield await evaluator.run(load_agent_run_scenarios(SCENARIOS))
    finally:
        await container.shutdown()


async def test_every_scenario_meets_its_expectation(result) -> None:
    assert len(result.results) == 12
    assert not result.failures(), [(r.case_id, r.detail) for r in result.failures()]


async def test_every_budget_kind_is_exercised(result) -> None:
    exhausted = {r.exhausted_budget for r in result.results if r.exhausted_budget}
    assert exhausted == {"iterations", "tokens", "time", "tools", "risk"}


async def test_unsafe_actions_never_ran(result) -> None:
    unsafe = [r for r in result.results if r.unsafe_expected]
    assert unsafe and all(r.unsafe_prevented for r in unsafe)


async def test_the_committed_baseline_holds(result) -> None:
    baseline = RegressionGate.load_baseline(BASELINE.read_text(encoding="utf-8"))
    ok, failures = RegressionGate(tolerances=eval_gate.AGENT_RUN_TOLERANCES).check(result, baseline)
    assert ok, failures
    assert set(baseline) == set(result.metrics())


async def test_the_gate_runs_it_as_its_own_suite() -> None:
    spec = next(s for s in eval_gate.GATES if s.name == "agent_runs")
    outcome = await eval_gate.run_gate(spec, eval_gate.SUITE_DIR)
    assert outcome.ok, outcome.failures
    assert outcome.cases == 12


async def test_the_gate_fails_when_the_baseline_is_out_of_reach(tmp_path) -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    payload["metrics"]["replan_success"] = 0.99
    (tmp_path / "agent_runs_baseline.json").write_text(json.dumps(payload), encoding="utf-8")
    spec = next(s for s in eval_gate.GATES if s.name == "agent_runs")
    outcome = await eval_gate.run_gate(spec, tmp_path)
    assert not outcome.ok
    assert any(f.startswith("replan_success fell") for f in outcome.failures)
