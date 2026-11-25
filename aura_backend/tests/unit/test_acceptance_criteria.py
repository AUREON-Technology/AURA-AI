"""Acceptance criteria on a real turn (ADR-047, row 41).

``Goal.success_criteria`` was never set by the kernel. A request can now carry
``success_criteria``; after the plan runs, the run's own tool results are checked against them and
``agent_loop.acceptance`` says ``{status, met, unmet, unverified}``. A run with unmet criteria says
so in its answer. Real container and kernel; only the planner's *plan* is scripted.
"""

from __future__ import annotations

import dataclasses

import pytest

from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.identifiers import PlanId, SessionId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind, StepStatus
from aura_core.execution.acceptance import check_acceptance, normalise_criteria
from aura_core.kernel import TurnRequest
from aura_core.tools import param, tool

USER = UserId("u-accept")
CRITERIA = ("Users can log in with email", "Failed login returns 401", "Response under 200ms")


class _Planner:
    def __init__(self, real, plan: Plan) -> None:
        self._real, self.plan_to_return = real, plan
        self.goals: list[Goal] = []

    def needs_plan(self, message, *, available_tools=(), force=None, previous_reply=None) -> bool:  # noqa: ANN001
        return self._real.needs_plan(message, available_tools=available_tools, force=force)

    def goal_from_message(self, message, *, criteria=()):  # noqa: ANN001
        goal = self._real.goal_from_message(message, criteria=criteria)
        self.goals.append(goal)
        return goal

    async def plan(self, goal, available_tools=()):  # noqa: ANN001
        return self.plan_to_return


def _step(step_id: str, name: str, target: str) -> PlanStep:
    return PlanStep(id=StepId(step_id), kind=StepKind.TOOL, tool=name,
                    description=f"{name} {target}", arguments={"target": target})


async def _ready(*steps: PlanStep):
    base = load_settings(refresh=True, validate=False)
    container = Container(dataclasses.replace(base))
    await container.startup()

    @tool("probe_login", "Probe a behaviour and report PASS or FAILED.", param("target", "string", "what"))
    async def probe_login(target: str) -> str:
        return {
            "login": "Users can log in with email: PASS",
            "401": "Failed login returns 401: FAILED (got 500)",
            "noise": "listing: 3 files",
        }[target]

    container.tools.register(probe_login)
    planner = _Planner(container.planner, Plan(id=PlanId.new(), goal=Goal(description="g"), steps=tuple(steps)))
    container.kernel._deps.planner = planner
    return container, planner


def _turn(**overrides) -> TurnRequest:
    base = dict(message="verify the login behaviour", user_id=USER, session_id=SessionId("conv-accept"),
                plan=True)
    base.update(overrides)
    return TurnRequest(**base)


class TestCheckAcceptance:
    def _plan(self, *results: tuple[str, str, StepStatus]) -> Plan:
        steps = tuple(
            dataclasses.replace(_step(f"s{i}", "probe_login", t), status=status, result=text)
            for i, (t, text, status) in enumerate(results, start=1)
        )
        return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps)

    def test_only_affirmative_evidence_is_met_and_missing_evidence_is_not(self) -> None:
        plan = self._plan(("login", "Users can log in with email: PASS", StepStatus.SUCCEEDED),
                          ("401", "Failed login returns 401: FAILED", StepStatus.SUCCEEDED))
        report = check_acceptance(CRITERIA, plan)
        assert report.met == (CRITERIA[0],)
        assert report.unmet == (CRITERIA[1],)
        assert report.unverified == (CRITERIA[2],)
        assert report.status == "unmet"
        assert "Not met: Failed login returns 401" in report.caveat()

    def test_a_criterion_merely_quoted_in_output_is_not_met(self) -> None:
        plan = self._plan(("x", "the spec lists: Users can log in with email", StepStatus.SUCCEEDED))
        assert check_acceptance(CRITERIA[:1], plan).unverified == (CRITERIA[0],)

    def test_a_failed_step_naming_the_criterion_makes_it_unmet(self) -> None:
        plan = self._plan(("x", "", StepStatus.FAILED))
        step = dataclasses.replace(plan.steps[0], description="check Response under 200ms")
        plan = Plan(id=plan.id, goal=plan.goal, steps=(step,))
        assert check_acceptance(CRITERIA[2:], plan).unmet == (CRITERIA[2],)

    def test_nothing_ran_leaves_everything_unverified(self) -> None:
        report = check_acceptance(CRITERIA, None)
        assert report.unverified == CRITERIA and report.status == "unverified"
        assert "No evidence either way" in report.caveat()

    def test_all_met_is_met_with_no_caveat(self) -> None:
        plan = self._plan(("login", "Users can log in with email: PASS", StepStatus.SUCCEEDED))
        report = check_acceptance(CRITERIA[:1], plan)
        assert report.status == "met" and report.caveat() == ""

    def test_the_input_is_bounded(self) -> None:
        crits = normalise_criteria([f"c{i}" for i in range(50)] + ["x" * 900, "  ", "c1"])
        assert len(crits) == 20 and all(len(c) <= 300 for c in crits)


class TestThroughTheKernel:
    async def test_unmet_and_unverified_criteria_are_reported_and_said_in_the_answer(self) -> None:
        container, planner = await _ready(_step("s1", "probe_login", "login"), _step("s2", "probe_login", "401"))
        result = await container.kernel.run(_turn(success_criteria=CRITERIA))

        assert planner.goals[-1].success_criteria == CRITERIA, "the criteria reach the Goal"
        acceptance = result.agent_loop["acceptance"]
        assert acceptance["status"] == "unmet"
        assert acceptance["met"] == [CRITERIA[0]]
        assert acceptance["unmet"] == [CRITERIA[1]]
        assert acceptance["unverified"] == [CRITERIA[2]]
        assert "Acceptance check" in result.text and "Failed login returns 401" in result.text

    async def test_all_criteria_met_adds_no_caveat(self) -> None:
        container, _ = await _ready(_step("s1", "probe_login", "login"))
        result = await container.kernel.run(_turn(success_criteria=CRITERIA[:1]))
        assert result.agent_loop["acceptance"]["status"] == "met"
        assert "Acceptance check" not in result.text

    async def test_a_turn_that_planned_nothing_does_not_silently_succeed(self) -> None:
        container, _ = await _ready(_step("s1", "probe_login", "login"))
        result = await container.kernel.run(
            _turn(plan=False, plan_mode="fast", message="what is 2+2", success_criteria=CRITERIA[:1]))
        assert result.agent_loop["acceptance"]["status"] == "unverified"
        assert "Acceptance check" in result.text

    async def test_without_criteria_nothing_changes(self) -> None:
        container, _ = await _ready(_step("s1", "probe_login", "login"), _step("s2", "probe_login", "401"))
        result = await container.kernel.run(_turn())
        assert "acceptance" not in (result.agent_loop or {})
        assert "Acceptance check" not in result.text

    async def test_the_stream_reports_it_in_the_done_frame(self) -> None:
        container, _ = await _ready(_step("s1", "probe_login", "login"), _step("s2", "probe_login", "401"))
        frames = [f async for f in container.kernel.stream(_turn(success_criteria=CRITERIA))]
        done = frames[-1]
        assert done["type"] == "done" and done["agent_loop"]["acceptance"]["unmet"] == [CRITERIA[1]]
        assert "Acceptance check" in "".join(f.get("text", "") for f in frames if f["type"] == "token")

    async def test_a_held_review_plan_is_not_judged_before_it_runs(self) -> None:
        container, _ = await _ready(_step("s1", "probe_login", "login"))
        result = await container.kernel.run(_turn(plan_mode="review", success_criteria=CRITERIA))
        assert "Acceptance check" not in result.text


class TestHttp:
    @pytest.fixture
    def client(self):
        import asyncio

        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app
        from aura_core.domain.errors import AuthenticationError

        container, planner = asyncio.run(_ready(_step("s1", "probe_login", "login"), _step("s2", "probe_login", "401")))

        async def authenticator(request):
            if request.headers.get("authorization") != f"Bearer {USER.value}":
                raise AuthenticationError("unknown principal")
            return Principal(user_id=USER, email="a@example.com", email_verified=True)

        with TestClient(build_app(container, authenticator=authenticator)) as c:
            c.planner = planner
            yield c

    HEADERS = {"Authorization": f"Bearer {USER.value}"}

    def test_chat_returns_the_acceptance_report(self, client) -> None:
        response = client.post("/api/v3/chat", headers=self.HEADERS, json={
            "message": "verify", "plan": True, "success_criteria": list(CRITERIA)})
        assert response.status_code == 200, response.text
        assert response.json()["agent_loop"]["acceptance"]["unmet"] == [CRITERIA[1]]

    def test_chat_without_criteria_keeps_its_old_shape(self, client) -> None:
        body = client.post("/api/v3/chat", headers=self.HEADERS, json={"message": "verify", "plan": True}).json()
        assert "agent_loop" not in body

    def test_too_many_or_too_long_criteria_are_a_422(self, client) -> None:
        many = client.post("/api/v3/chat", headers=self.HEADERS, json={
            "message": "x", "success_criteria": [f"c{i}" for i in range(21)]})
        long = client.post("/api/v3/chat", headers=self.HEADERS, json={
            "message": "x", "success_criteria": ["y" * 301]})
        assert many.status_code == 422 and long.status_code == 422

    def test_the_stream_done_frame_carries_it(self, client) -> None:
        import json

        with client.stream("POST", "/api/v3/chat/stream", headers=self.HEADERS, json={
                "message": "verify", "plan": True, "success_criteria": list(CRITERIA)}) as response:
            events = [line for line in response.iter_lines() if line.startswith("data: ")]
        done = json.loads(events[-1][len("data: "):])
        assert done["agent_loop"]["acceptance"]["status"] == "unmet"


class TestTaskRoute:
    def test_the_criteria_survive_the_task_spec_round_trip(self) -> None:
        from aura_core.tasks.runner import spec_to_turn, turn_to_spec

        spec = turn_to_spec(_turn(success_criteria=CRITERIA))
        assert spec["success_criteria"] == list(CRITERIA)
        task = _task("t1")
        assert spec_to_turn(spec, task=task).success_criteria == CRITERIA
        old = {k: v for k, v in spec.items() if k != "success_criteria"}
        assert spec_to_turn(old, task=task).success_criteria == ()

    async def test_a_task_runs_with_criteria_and_its_result_carries_the_verdict(self) -> None:
        from aura_core.tasks.runner import KernelTaskRunner, turn_to_spec

        container, _ = await _ready(_step("s1", "probe_login", "login"), _step("s2", "probe_login", "401"))
        runner = KernelTaskRunner(container.kernel)
        outcome = await runner(_task("t2"), turn_to_spec(_turn(success_criteria=CRITERIA)))
        assert outcome.result["agent_loop"]["acceptance"]["status"] == "unmet"
        plain = await runner(_task("t3"), turn_to_spec(_turn()))
        assert "agent_loop" not in plain.result


def _task(task_id: str):
    from datetime import datetime, timezone

    from aura_core.tasks.models import AgentTask, TaskStatus

    now = datetime.now(timezone.utc)
    return AgentTask(id=task_id, owner=USER.value, goal="g", persona="orchestrator",
                     status=TaskStatus.RUNNING, priority="normal", created_at=now, updated_at=now)
