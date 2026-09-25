"""Run-level evaluation of the agent loop (GAP-024): deterministic, model-free.

``AgentEvaluator`` (``harness.py``) scores a turn's *answer*. This scores the *run*: did the
bounded loop complete, how much of its plan did it finish, were its tool calls accurate, how
much of its iteration budget did it burn, did a replan actually recover, and was every unsafe
action it attempted stopped before it acted. Every number comes from the durable
:class:`~aura_core.domain.agent_runs.AgentRun` the loop recorded -- the same record
``GET /api/v3/agents/runs/{id}`` serves -- so the evaluation measures what production persists,
not a parallel bookkeeping.

Determinism, by construction:

* the planner's responses are scripted (:class:`ScenarioProvider`, an ``EchoProvider`` whose
  script is replaced per scenario) -- no model decides anything;
* the tools are a small evaluation-only set (:func:`build_eval_tools`) whose outcomes are pure
  functions of their arguments, registered into the throwaway container the gate builds;
* time is a ``FrozenClock`` that only the ``eval_slow`` tool advances, so the time budget is
  exercised without sleeping.

Each scenario runs through ``AIKernel.run`` -- the only execution path -- so planning, the
execution engine, ``ToolRegistry`` policy, the budget gate and the run recorder are all the real
ones. Scenarios live in ``evaluation/suites/agent_runs.jsonl``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..agents.registry import AgentRegistry, AgentSpec, build_default_agent_registry
from ..domain.agent_runs import AgentRunStatus, run_metrics
from ..domain.errors import ToolExecutionError
from ..domain.identifiers import AgentRunId, UserId
from ..domain.tools import ToolCategory, ToolSensitivity
from ..llm.providers.echo import EchoProvider, ScriptedTurn
from ..ports.runtime import ToolContext
from ..prompts.library import AGENT_PROMPT_IDS
from ..tools.base import param, tool

__all__ = [
    "AgentRunScenario",
    "AgentRunCaseResult",
    "AgentRunSuiteResult",
    "AgentRunEvaluator",
    "ScenarioProvider",
    "build_eval_tools",
    "load_agent_run_scenarios",
    "EVAL_AGENT_ID",
]

EVAL_AGENT_ID = "eval_agent"
EVAL_USER = UserId("eval-agent-runs")


class ScenarioProvider(EchoProvider):
    """``EchoProvider`` whose script is *replaced* per scenario (``EchoProvider.queue`` only
    appends, and ``reset`` replays the old script from the start)."""

    def load(self, turns: Sequence[ScriptedTurn]) -> None:
        self._script = list(turns)
        self._cursor = 0
        self.calls.clear()


def build_eval_tools(*, clock: Any = None, writes: list[str] | None = None) -> list[Any]:
    """Evaluation-only tools with deterministic outcomes.

    ``eval_lookup`` (public read) always succeeds; ``eval_check`` (public read) fails for any
    value starting with ``bad``; ``eval_note`` (personal, side-effecting, risk cost 2) and
    ``eval_publish`` (confidential, side-effecting -- so confirmation-gated by ``ToolSpec`` itself,
    risk cost 3) append to ``writes`` only if they actually run, which is how a scenario proves an
    unsafe action never acted; ``eval_slow`` advances the injected ``FrozenClock``.
    """
    sink = writes if writes is not None else []

    @tool("eval_lookup", "Look a key up (evaluation).", param("key", "string", "Key."),
          category=ToolCategory.UTILITY, sensitivity=ToolSensitivity.PUBLIC)
    async def eval_lookup(key: str) -> str:
        return f"value:{key}"

    @tool("eval_check", "Check a value (evaluation).", param("value", "string", "Value."),
          category=ToolCategory.UTILITY, sensitivity=ToolSensitivity.PUBLIC)
    async def eval_check(value: str) -> str:
        if value.startswith("bad"):
            raise ToolExecutionError("check failed", tool="eval_check")
        return f"ok:{value}"

    @tool("eval_note", "Write a note (evaluation).", param("note", "string", "Note."),
          category=ToolCategory.PRODUCTIVITY, sensitivity=ToolSensitivity.PERSONAL,
          side_effecting=True)
    async def eval_note(note: str) -> str:
        sink.append(f"note:{note}")
        return "noted"

    @tool("eval_publish", "Publish externally (evaluation).", param("note", "string", "Note."),
          category=ToolCategory.COMMUNICATION, sensitivity=ToolSensitivity.CONFIDENTIAL,
          side_effecting=True)
    async def eval_publish(note: str) -> str:
        sink.append(f"publish:{note}")
        return "published"

    @tool("eval_slow", "Take a while (evaluation).", param("seconds", "number", "Seconds."),
          category=ToolCategory.UTILITY, sensitivity=ToolSensitivity.PUBLIC)
    async def eval_slow(seconds: float, context: ToolContext) -> str:
        if clock is not None and hasattr(clock, "advance"):
            clock.advance(float(seconds))
        return "slept"

    return [eval_lookup, eval_check, eval_note, eval_publish, eval_slow]


@dataclass(frozen=True, slots=True)
class AgentRunScenario:
    id: str
    message: str
    tools: tuple[str, ...]
    plans: tuple[Mapping[str, Any], ...]
    max_plan_iterations: int = 1
    token_budget: int | None = None
    time_budget_s: float | None = None
    tool_budget: int | None = None
    risk_budget: int | None = None
    expect_status: str = AgentRunStatus.COMPLETED
    expect_exhausted: str | None = None
    #: Tools this scenario tries to use unsafely: none of them may ever run.
    unsafe_tools: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentRunScenario":
        persona = payload.get("persona", {})
        expect = payload.get("expect", {})
        return cls(
            id=str(payload["id"]),
            message=str(payload["message"]),
            tools=tuple(persona.get("tools", ())),
            plans=tuple(payload.get("plans", ())),
            max_plan_iterations=int(persona.get("max_plan_iterations", 1)),
            token_budget=persona.get("token_budget"),
            time_budget_s=persona.get("time_budget_s"),
            tool_budget=persona.get("tool_budget"),
            risk_budget=persona.get("risk_budget"),
            expect_status=str(expect.get("status", AgentRunStatus.COMPLETED)),
            expect_exhausted=expect.get("exhausted_budget"),
            unsafe_tools=tuple(expect.get("unsafe_tools", ())),
        )

    def spec(self) -> AgentSpec:
        return AgentSpec(
            id=EVAL_AGENT_ID, name="Evaluation Agent",
            prompt_template_id=AGENT_PROMPT_IDS["orchestrator"],
            allowed_tools=frozenset(self.tools), risk_ceiling=ToolSensitivity.SYSTEM,
            max_plan_iterations=self.max_plan_iterations, token_budget=self.token_budget,
            time_budget_s=self.time_budget_s, tool_budget=self.tool_budget,
            risk_budget=self.risk_budget,
        )


def load_agent_run_scenarios(path: str | Path) -> list[AgentRunScenario]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [AgentRunScenario.from_dict(json.loads(line)) for line in lines if line.strip()]


@dataclass(frozen=True, slots=True)
class AgentRunCaseResult:
    case_id: str
    success: bool
    status: str
    exhausted_budget: str | None
    metrics: Mapping[str, Any]
    within_budget: bool
    unsafe_expected: bool
    unsafe_prevented: bool
    duration_ms: float = 0.0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AgentRunSuiteResult:
    """Suite-level metrics, every one "higher is better" so ``RegressionGate`` (which only
    flags drops) can gate them all."""

    suite: str
    results: tuple[AgentRunCaseResult, ...]
    duration_ms: float = 0.0
    replans: int = 0
    replans_recovered: int = 0

    def metrics(self) -> dict[str, float]:
        rs = self.results
        n = len(rs) or 1
        accuracies = [r.metrics["tool_accuracy"] for r in rs if r.metrics.get("tool_accuracy") is not None]
        utilisation = [
            r.metrics["iteration_utilization"] for r in rs
            if r.metrics.get("iteration_utilization") is not None
        ]
        unsafe = [r for r in rs if r.unsafe_expected]
        return {
            "pass_rate": round(sum(r.success for r in rs) / n, 4),
            "completion_rate": round(sum(bool(r.metrics.get("completed")) for r in rs) / n, 4),
            "partial_completion": round(
                sum(float(r.metrics.get("partial_completion") or 0.0) for r in rs) / n, 4
            ),
            "tool_accuracy": round(sum(accuracies) / len(accuracies), 4) if accuracies else 0.0,
            # "iterations vs budget", as headroom: 1.0 means no replan budget was spent.
            "iteration_headroom": (
                round(1.0 - sum(utilisation) / len(utilisation), 4) if utilisation else 1.0
            ),
            "budget_adherence": round(sum(r.within_budget for r in rs) / n, 4),
            "replan_success": (
                round(self.replans_recovered / self.replans, 4) if self.replans else 0.0
            ),
            "unsafe_prevention_rate": (
                round(sum(r.unsafe_prevented for r in unsafe) / len(unsafe), 4) if unsafe else 1.0
            ),
        }

    def failures(self) -> list[AgentRunCaseResult]:
        return [r for r in self.results if not r.success]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "cases": len(self.results),
            "duration_ms": round(self.duration_ms, 1),
            "metrics": self.metrics(),
            "failures": [
                {"case_id": r.case_id, "detail": r.detail, "status": r.status,
                 "exhausted_budget": r.exhausted_budget}
                for r in self.failures()[:20]
            ],
        }


class AgentRunEvaluator:
    """Drives each scenario through ``AIKernel.run`` and scores the recorded run."""

    def __init__(
        self,
        *,
        kernel: Any,
        provider: ScenarioProvider,
        runs: Any,
        writes: list[str],
        base_registry: AgentRegistry | None = None,
    ) -> None:
        self._kernel = kernel
        self._provider = provider
        self._runs = runs
        self._writes = writes
        self._base = base_registry or build_default_agent_registry()

    @classmethod
    def for_container(cls, container: Any, *, clock: Any) -> "AgentRunEvaluator":
        """Wire a throwaway container for evaluation: the scripted provider under the echo
        provider's name, the evaluation tools, and an agent loop on ``clock``. The loop is the
        real ``AgenticExecutor`` over the container's own planner, executor and run store --
        only its clock differs. Never call this on a serving container."""
        from ..execution.agentic_loop import AgenticExecutor

        provider = ScenarioProvider(name="echo", model="echo-1")
        container.router._providers["echo"] = provider
        writes: list[str] = []
        container.tools.register_all(build_eval_tools(clock=clock, writes=writes))
        container.kernel._deps.agentic_executor = AgenticExecutor(
            planner=container.planner, executor=container.executor, metrics=container.metrics,
            clock=clock, runs=container.agent_runs,
        )
        return cls(kernel=container.kernel, provider=provider, runs=container.agent_runs,
                   writes=writes)

    def _registry(self, scenario: AgentRunScenario) -> AgentRegistry:
        return AgentRegistry([*(s for s in self._base if s.id != EVAL_AGENT_ID), scenario.spec()])

    async def run_case(self, scenario: AgentRunScenario) -> AgentRunCaseResult:
        from ..kernel.kernel import TurnRequest

        started = time.perf_counter()
        deps = self._kernel._deps
        previous = deps.agents
        deps.agents = self._registry(scenario)
        self._provider.load([ScriptedTurn(text=json.dumps(p)) for p in scenario.plans])
        writes_before = len(self._writes)
        try:
            result = await self._kernel.run(TurnRequest(
                message=scenario.message, user_id=EVAL_USER, agent=EVAL_AGENT_ID,
                use_knowledge=False, use_memory=False, plan=True,
            ))
        finally:
            deps.agents = previous
        elapsed = (time.perf_counter() - started) * 1000.0

        run = None
        if result.agent_run_id:
            run = await self._runs.get_run(AgentRunId(result.agent_run_id), user_id=EVAL_USER)
        if run is None:
            return AgentRunCaseResult(
                case_id=scenario.id, success=False, status="missing", exhausted_budget=None,
                metrics={}, within_budget=False, unsafe_expected=bool(scenario.unsafe_tools),
                unsafe_prevented=False, duration_ms=elapsed, detail="no agent run was recorded",
            )
        steps = await self._runs.list_steps(run.id, user_id=EVAL_USER)
        new_writes = self._writes[writes_before:]
        unsafe_ran = any(
            s.tool in scenario.unsafe_tools and s.status == "succeeded" for s in steps
        ) or any(w.split(":", 1)[0] in {t.removeprefix("eval_") for t in scenario.unsafe_tools}
                 for w in new_writes)
        unsafe_prevented = bool(scenario.unsafe_tools) and not unsafe_ran and run.unsafe_prevented > 0

        budget = run.budget
        within = (
            run.replans <= budget.max_iterations
            and (budget.tool_budget is None
                 or sum(1 for s in steps if s.kind == "tool_call" and s.status != "refused")
                 + sum(1 for s in steps if s.kind == "approval") <= budget.tool_budget)
            and (budget.risk_budget is None or run.risk_spent <= budget.risk_budget)
        )
        problems = []
        if run.status != scenario.expect_status:
            problems.append(f"status {run.status} != {scenario.expect_status}")
        if run.exhausted_budget != scenario.expect_exhausted:
            problems.append(f"exhausted {run.exhausted_budget} != {scenario.expect_exhausted}")
        if scenario.unsafe_tools and not unsafe_prevented:
            problems.append("an unsafe action was not prevented")
        return AgentRunCaseResult(
            case_id=scenario.id,
            success=not problems,
            status=run.status,
            exhausted_budget=run.exhausted_budget,
            metrics={**run_metrics(run), "replans": run.replans,
                     "replans_recovered": run.replans_recovered},
            within_budget=within,
            unsafe_expected=bool(scenario.unsafe_tools),
            unsafe_prevented=unsafe_prevented,
            duration_ms=elapsed,
            detail="; ".join(problems),
        )

    async def run(self, scenarios: Sequence[AgentRunScenario], *, suite: str = "agent_runs"
                  ) -> AgentRunSuiteResult:
        started = time.perf_counter()
        results = [await self.run_case(s) for s in scenarios]
        return AgentRunSuiteResult(
            suite=suite,
            results=tuple(results),
            duration_ms=(time.perf_counter() - started) * 1000.0,
            replans=sum(int(r.metrics.get("replans") or 0) for r in results),
            replans_recovered=sum(int(r.metrics.get("replans_recovered") or 0) for r in results),
        )
