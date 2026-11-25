"""Pause, resume and redirect a running agent (ADR-059).

Cooperative control: a running turn registers with ``ActiveTurns``; ``pause`` holds it at the
loop's next checkpoint (never inside a tool call), ``redirect`` hands it a new instruction that it
replans with. Driven with the real ``ActiveTurns`` and the real ``AgenticExecutor`` over scripted
planner/executor fakes, plus the HTTP routes through the real app.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from aura_core.api import Principal, build_app
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.execution.agentic_loop import AgenticExecutor, AgentRunContext
from aura_core.execution.engine import ExecutionOutcome
from aura_core.kernel import active_turns as at
from aura_core.kernel.active_turns import ActiveTurns
from aura_core.ports.runtime import ToolContext
from tests.unit.test_autonomy_profile import CAPS


def _plan() -> Plan:
    step = PlanStep(id=StepId("s1"), kind=StepKind.RESPOND, description="respond")
    return Plan(id=PlanId.new(), goal=Goal(description="do the thing"), steps=(step,))


class _Planner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def can_replan(self, plan, *, limit=None) -> bool:
        return True

    async def replan(self, plan, *, reason, available_tools=(), limit=None, feedback=()):
        self.calls.append((reason, list(feedback)))
        return plan.revised(steps=list(plan.steps), reason=reason)


class _Executor:
    """Pass 1 asks ``hold`` whether to stop (the person sent something); later passes succeed."""

    def __init__(self) -> None:
        self.passes = 0

    async def execute(self, plan, *, context, capabilities=(), confirmations=None, on_event=None, hold=None):
        self.passes += 1
        if self.passes == 1 and hold is not None:
            held = hold()
            if asyncio.iscoroutine(held):
                held = await held
            if held:
                return ExecutionOutcome(plan=plan, results={}, succeeded=False, blocked_reason="redirected")
        steps = tuple(s.succeeded("ok", 1.0) for s in plan.steps)
        return ExecutionOutcome(plan=plan.with_steps(steps), results={}, succeeded=True)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u1"), correlation_id="c1", deadline_s=30.0)


def _run_ctx() -> AgentRunContext:
    return AgentRunContext(user_id=UserId("u1"), agent_id="engineer", turn_id="t1", correlation_id="c1")


class TestActiveTurnsControls:
    async def test_pause_holds_the_checkpoint_until_resume(self) -> None:
        turns = ActiveTurns()
        with turns.track("u1", "t1", kind="run"):
            assert turns.pause_user("u1") == 1 and turns.pause_user("u1") == 0  # idempotent
            waiter = asyncio.create_task(turns.checkpoint("u1", "t1"))
            await asyncio.sleep(0.05)
            assert not waiter.done()
            assert turns.resume_user("u1") == 1
            assert await asyncio.wait_for(waiter, 1) == ([], None)

    async def test_a_redirect_is_delivered_once_and_wakes_a_paused_turn(self) -> None:
        turns = ActiveTurns()
        with turns.track("u1", "t1", kind="run"):
            turns.pause_user("u1")
            assert turns.redirect_user("u1", "  use   the\nstaging db ") == 1
            redirects, note = await asyncio.wait_for(turns.checkpoint("u1", "t1"), 1)
            assert redirects == ["use the staging db"] and note is None
            assert await turns.checkpoint("u1", "t1") == ([], None)

    async def test_it_acts_only_on_the_callers_own_turns_and_is_bounded(self) -> None:
        turns = ActiveTurns()
        with turns.track("u1", "t1", kind="run"), turns.track("u2", "t2", kind="run"):
            assert turns.pause_user("u2") == 1
            assert turns.entry_for("u1", "t1").paused is False
            for _ in range(at.MAX_REDIRECTS + 3):
                turns.redirect_user("u1", "x" * 5000)
            queued = turns.entry_for("u1", "t1").redirects
            assert len(queued) == at.MAX_REDIRECTS and all(len(q) == at.MAX_REDIRECT_CHARS for q in queued)

    async def test_nothing_running_answers_zero_and_an_unknown_turn_is_not_held(self) -> None:
        turns = ActiveTurns()
        assert (turns.pause_user("u1"), turns.resume_user("u1"), turns.redirect_user("u1", "hi")) == (0, 0, 0)
        assert await turns.checkpoint("u1", "nope") == ([], None)
        assert await turns.hold("u1", "nope") is False

    async def test_a_pause_nobody_answers_ends_by_itself_and_says_so(self, monkeypatch) -> None:
        monkeypatch.setattr(at, "MAX_PAUSE_S", 0.05)
        turns = ActiveTurns()
        with turns.track("u1", "t1", kind="run"):
            turns.pause_user("u1")
            redirects, note = await asyncio.wait_for(turns.checkpoint("u1", "t1"), 1)
            assert redirects == [] and "carried on" in note
            assert turns.entry_for("u1", "t1").paused is False


class TestTheLoopHonoursIt:
    async def test_a_mid_pass_redirect_stops_the_pass_and_replans_with_the_instruction(self, ctx) -> None:
        turns = ActiveTurns()
        planner, executor = _Planner(), _Executor()
        agentic = AgenticExecutor(planner=planner, executor=executor, turn_controls=turns)
        with turns.track("u1", "t1", kind="run"):
            turns.redirect_user("u1", "do not touch the migrations")
            outcome = await agentic.run(_plan(), context=ctx, max_iterations=5, deadline_s=10.0,
                                        run_context=_run_ctx())
        assert outcome.succeeded and executor.passes == 2
        reason, feedback = planner.calls[0]
        assert reason == "user_redirect" and "do not touch the migrations" in feedback[0]

    async def test_without_controls_or_a_request_the_loop_is_unchanged(self, ctx) -> None:
        planner, executor = _Planner(), _Executor()
        outcome = await AgenticExecutor(planner=planner, executor=executor).run(
            _plan(), context=ctx, max_iterations=5, deadline_s=10.0, run_context=_run_ctx())
        assert outcome.succeeded and planner.calls == []

    async def test_a_paused_run_holds_before_its_next_step_and_continues_on_resume(self, ctx) -> None:
        turns = ActiveTurns()
        planner, executor = _Planner(), _Executor()
        agentic = AgenticExecutor(planner=planner, executor=executor, turn_controls=turns)
        with turns.track("u1", "t1", kind="run"):
            turns.pause_user("u1")

            async def go():
                return await agentic.run(_plan(), context=ctx, max_iterations=5, deadline_s=10.0,
                                         run_context=_run_ctx())

            task = asyncio.create_task(go())
            await asyncio.sleep(0.05)
            assert not task.done()  # held inside pass 1, at the hold, before anything ran
            turns.resume_user("u1")
            outcome = await asyncio.wait_for(task, 2)
        assert outcome.succeeded and planner.calls == []


class TestRoutes:
    def _client(self, container) -> TestClient:
        async def authenticator(request):
            header = request.headers.get("authorization", "")
            if header != "Bearer alice":
                raise AuthenticationError("unknown principal")
            return Principal(user_id=UserId("alice"), role="Developer", capabilities=CAPS)

        return TestClient(build_app(container, authenticator=authenticator))

    def test_with_nothing_running_each_answers_zero(self, container) -> None:
        client, auth = self._client(container), {"Authorization": "Bearer alice"}
        assert client.post("/api/v3/agents/pause", headers=auth).json()["turns_paused"] == 0
        assert client.post("/api/v3/agents/resume", headers=auth).json()["turns_resumed"] == 0
        r = client.post("/api/v3/agents/redirect", headers=auth, json={"instruction": "stop touching db"})
        assert r.status_code == 200 and r.json()["turns_redirected"] == 0 and r.json()["scope"] == "this_process"

    def test_a_redirect_needs_text_and_a_login(self, container) -> None:
        client = self._client(container)
        assert client.post("/api/v3/agents/redirect", headers={"Authorization": "Bearer alice"},
                           json={"instruction": ""}).status_code == 422
        for path in ("pause", "resume", "redirect"):
            assert client.post(f"/api/v3/agents/{path}", json={"instruction": "x"}).status_code in (401, 403)

    def test_a_running_turn_is_reached(self, container) -> None:
        client, auth = self._client(container), {"Authorization": "Bearer alice"}
        turns = container.kernel.active_turns

        class _Live:
            def done(self) -> bool:
                return False

        entry = at.TrackedTurn(user_id="alice", turn_id="t9", kind="run", task=_Live())
        turns._by_user.setdefault("alice", []).append(entry)
        assert client.post("/api/v3/agents/pause", headers=auth).json()["turns_paused"] == 1
        assert entry.paused is True
        r = client.post("/api/v3/agents/redirect", headers=auth, json={"instruction": "use staging"})
        assert r.json()["turns_redirected"] == 1 and entry.redirects == ["use staging"] and not entry.paused
