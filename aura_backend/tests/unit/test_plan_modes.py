"""Planning modes ``auto | plan | fast | review`` (planning/modes.py + the kernel, ADR-040, row 63).

Three levels. The decision table (``decide_plan``) is pure and tested exhaustively. The kernel tests
run real turns through the real container, registry, policy and confirmation store; the only
stand-in is the planner's *plan* (what "the model" proposes), exactly as ``test_confirmation_binding``
does, so what is asserted is what the kernel does with it -- above all that ``review`` runs nothing
until the user approves, and that approval runs the stored plan, not a new one. The HTTP tests go
through the ASGI app.
"""

from __future__ import annotations

import dataclasses

import pytest

from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.identifiers import PlanId, SessionId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
from aura_core.kernel import TurnRequest
from aura_core.planning.modes import PLAN_MODES, decide_plan, has_action_intent, normalise_mode
from aura_core.tools import param, tool
from aura_core.tools.base import FunctionTool

USER = UserId("u-modes")


# -- the decision table ------------------------------------------------------------------------


def _decide(**overrides):
    base = dict(
        requested_mode=None, plan_flag=None, default_mode="", message="what is my leave balance?",
        side_effecting_tools=True, agentic_persona=False, complex_message=False,
        heuristic=lambda force: bool(force),  # "the planner's heuristics never fire on their own"
    )
    base.update(overrides)
    return decide_plan(**base)


class TestIntent:
    @pytest.mark.parametrize("message", [
        "create a task to buy milk", "Delete the old branch", "send the report to Sara",
        "run the tests and push", "ابعت التقرير لسارة", "احذف المهمة دي",
    ])
    def test_requests_to_do_something(self, message) -> None:
        assert has_action_intent(message)

    @pytest.mark.parametrize("message", [
        "what is my leave balance?", "how do I delete a branch?", "explain how migrations work",
        "ليه الاختبار بيفشل؟", "", "tell me about the weather today",
    ])
    def test_questions_and_chat_are_read_only(self, message) -> None:
        assert not has_action_intent(message)

    def test_mode_names(self) -> None:
        assert PLAN_MODES == ("auto", "plan", "fast", "review")
        assert normalise_mode(" Review ") == "review" and normalise_mode("bogus") is None and normalise_mode(None) is None


class TestDefaultIsTodays:
    def test_no_mode_no_flag_is_the_old_rule(self) -> None:
        d = _decide(heuristic=lambda force: force is True)
        assert (d.plan, d.mode, d.requested, d.reason, d.review) == (False, "default", None, "heuristics", False)

    def test_an_agentic_persona_still_plans_by_default(self) -> None:
        d = _decide(agentic_persona=True, heuristic=lambda force: force is True)
        assert (d.plan, d.mode, d.reason) == (True, "default", "agentic_persona")

    def test_the_old_flag_maps_to_plan_and_fast_with_their_old_effect(self) -> None:
        asked = []

        def heuristic(force):
            asked.append(force)
            return bool(force)

        on = _decide(plan_flag=True, heuristic=heuristic)
        off = _decide(plan_flag=False, message="delete the old branch", heuristic=heuristic)
        assert (on.plan, on.mode, on.requested) == (True, "plan", "plan: true")
        assert (off.plan, off.mode, off.requested) == (False, "fast", "plan: false"), (
            "plan: false never plans, even for a request that asks for a side effect"
        )
        assert asked == [True, False]

    def test_plan_mode_wins_over_the_flag(self) -> None:
        d = _decide(requested_mode="fast", plan_flag=True)
        assert d.mode == "fast" and d.requested == "fast" and d.plan is False

    def test_the_deployment_default_applies_only_when_nothing_was_asked(self) -> None:
        assert _decide(default_mode="review").review is True
        assert _decide(default_mode="review", requested_mode="fast").mode == "fast"
        assert _decide(default_mode="review", plan_flag=False).mode == "fast"
        assert _decide(default_mode="").mode == "default"


class TestModes:
    def test_plan(self) -> None:
        d = _decide(requested_mode="plan")
        assert (d.plan, d.mode, d.review, d.reason) == (True, "plan", False, "requested")

    def test_review_plans_and_holds(self) -> None:
        d = _decide(requested_mode="review")
        assert (d.plan, d.mode, d.review) == (True, "review", True)

    def test_review_with_planning_switched_off_cannot_hold_anything(self) -> None:
        d = _decide(requested_mode="review", heuristic=lambda force: False)
        assert (d.plan, d.review, d.reason) == (False, False, "planning_disabled")

    def test_fast_answers_a_read_only_turn_directly(self) -> None:
        d = _decide(requested_mode="fast", message="what is my leave balance?")
        assert (d.plan, d.mode, d.reason, d.escalated_from) == (False, "fast", "read_only", None)

    def test_fast_does_not_silently_drop_a_side_effect_request(self) -> None:
        d = _decide(requested_mode="fast", message="create a task to buy milk")
        assert (d.plan, d.mode, d.reason, d.escalated_from) == (True, "plan", "side_effecting_request", "fast")
        assert d.as_dict()["escalated_from"] == "fast" and d.as_dict()["requested"] == "fast"

    def test_fast_stays_fast_when_no_tool_could_do_it(self) -> None:
        d = _decide(requested_mode="fast", message="create a task to buy milk", side_effecting_tools=False)
        assert (d.plan, d.mode) == (False, "fast")

    @pytest.mark.parametrize(("kwargs", "plans", "reason"), [
        (dict(message="what is my leave balance?"), False, "simple_read_only"),
        (dict(message="create a task to buy milk"), True, "side_effecting_request"),
        (dict(message="create a task to buy milk", side_effecting_tools=False), False, "simple_read_only"),
        (dict(message="what is the plan", complex_message=True), True, "complexity"),
        (dict(message="hi", agentic_persona=True), True, "agentic_persona"),
        (dict(message="first this then that", heuristic=lambda force: True), True, "multi_step"),
    ])
    def test_auto_decides_per_turn(self, kwargs, plans, reason) -> None:
        d = _decide(requested_mode="auto", **kwargs)
        assert (d.plan, d.mode, d.reason) == (plans, "auto", reason)

    def test_the_decision_is_what_the_client_is_shown(self) -> None:
        shown = _decide(requested_mode="review").as_dict(awaiting_approval=True)
        assert shown == {"requested": "review", "effective": "review", "reason": "requested",
                         "planned": True, "awaiting_approval": True}


# -- through the kernel ---------------------------------------------------------------------------


class _Calls:
    def __init__(self) -> None:
        self.ran: list[tuple[str, str]] = []

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.ran if n == name)


class _Planner:
    """The real planner's ``needs_plan`` heuristics, with the *plan* scripted."""

    def __init__(self, real, plan: Plan) -> None:
        self._real, self.plan_to_return, self.asked = real, plan, 0

    def needs_plan(self, message, *, available_tools=(), force=None, previous_reply=None) -> bool:  # noqa: ANN001
        return self._real.needs_plan(message, available_tools=available_tools, force=force)

    def goal_from_message(self, message, *, criteria=()):  # noqa: ANN001
        return Goal(description=message)

    async def plan(self, goal, available_tools=()):  # noqa: ANN001
        self.asked += 1
        return self.plan_to_return


def _confirm_tool(calls: _Calls, name: str) -> FunctionTool:
    async def handler(target: str) -> str:
        calls.ran.append((name, target))
        return f"{name}: {target}"

    spec = ToolSpec(
        name=name, description="A side-effecting, confidential tool -- always requires confirmation.",
        parameters=(ParameterSpec(name="target", type="string", description="target"),),
        category=ToolCategory.UTILITY, sensitivity=ToolSensitivity.CONFIDENTIAL, side_effecting=True,
    )
    return FunctionTool(spec, handler)


def _step(step_id: str, name: str, target: str, *, depends_on: tuple[str, ...] = ()) -> PlanStep:
    return PlanStep(
        id=StepId(step_id), kind=StepKind.TOOL, tool=name, description=f"{name} {target}",
        arguments={"target": target}, depends_on=tuple(StepId(d) for d in depends_on),
    )


def _plan(*steps: PlanStep) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=tuple(steps))


def _container(**reasoning) -> Container:
    base = load_settings(refresh=True, validate=False)
    # A prefill cap no turn reaches (0 means 400 here): the system prompt alone now exceeds the 400-token default (the merged branches'
    # instruction text), and under that cap the context engine clips unpinned scratchpad notes --
    # including the plan-review note these tests read. What the cap does to such a note in
    # production is a separate question (see the merge PR's open items).
    llm = dataclasses.replace(base.llm, max_prompt_tokens=100_000)
    return Container(
        dataclasses.replace(base, llm=llm, reasoning=dataclasses.replace(base.reasoning, **reasoning))
    )


async def _ready(calls: _Calls, *steps: PlanStep, **reasoning):
    container = _container(**reasoning)
    await container.startup()

    @tool("create_note", "Create a note.", param("target", "string", "text"),
          side_effecting=True, sensitivity=ToolSensitivity.PUBLIC)
    async def create_note(target: str) -> str:
        calls.ran.append(("create_note", target))
        return f"note {target}"

    container.tools.register(create_note)
    container.tools.register(_confirm_tool(calls, "send_message"))
    planner = _Planner(container.planner, _plan(*steps))
    container.kernel._deps.planner = planner
    return container, planner


def _turn(message: str = "create a note called draft", **overrides) -> TurnRequest:
    base = dict(message=message, user_id=USER, session_id=SessionId("conv-modes"))
    base.update(overrides)
    return TurnRequest(**base)


NOTE_AND_SEND = (_step("s1", "create_note", "draft"), _step("s2", "send_message", "bob", depends_on=("s1",)))


class TestDefaultThroughTheKernel:
    async def test_no_mode_a_question_is_answered_without_planning_and_says_default(self) -> None:
        calls = _Calls()
        container, planner = await _ready(calls, *NOTE_AND_SEND)
        result = await container.kernel.run(_turn("What is my leave balance?"))
        assert planner.asked == 0 and calls.ran == []
        assert result.plan_mode["effective"] == "default" and result.plan_mode["planned"] is False
        assert result.plan_mode["requested"] is None and result.plan_mode["awaiting_approval"] is False

    async def test_the_old_flag_still_works_both_ways(self) -> None:
        calls = _Calls()
        container, planner = await _ready(calls, _step("s1", "create_note", "draft"))
        on = await container.kernel.run(_turn("a question", plan=True))
        assert planner.asked == 1 and calls.ran == [("create_note", "draft")]
        assert (on.plan_mode["effective"], on.plan_mode["requested"]) == ("plan", "plan: true")

        off = await container.kernel.run(_turn("create a note called other", plan=False))
        assert planner.asked == 1 and calls.count("create_note") == 1, "plan: false never plans"
        assert (off.plan_mode["effective"], off.plan_mode["requested"]) == ("fast", "plan: false")


class TestFastAutoPlan:
    async def test_fast_reads_directly_and_escalates_a_write(self) -> None:
        calls = _Calls()
        container, planner = await _ready(calls, _step("s1", "create_note", "draft"))
        read = await container.kernel.run(_turn("What is my leave balance?", plan_mode="fast"))
        assert planner.asked == 0 and read.plan_mode["effective"] == "fast"

        write = await container.kernel.run(_turn("create a note called draft", plan_mode="fast"))
        assert planner.asked == 1 and calls.ran == [("create_note", "draft")]
        assert write.plan_mode["effective"] == "plan" and write.plan_mode["escalated_from"] == "fast"
        assert write.plan_mode["requested"] == "fast"

    async def test_auto_plans_a_write_and_answers_a_read_directly(self) -> None:
        calls = _Calls()
        container, planner = await _ready(calls, _step("s1", "create_note", "draft"))
        read = await container.kernel.run(_turn("What is my leave balance?", plan_mode="auto"))
        assert planner.asked == 0
        assert (read.plan_mode["effective"], read.plan_mode["reason"]) == ("auto", "simple_read_only")

        write = await container.kernel.run(_turn("create a note called draft", plan_mode="auto"))
        assert planner.asked == 1 and calls.ran == [("create_note", "draft")]
        assert (write.plan_mode["effective"], write.plan_mode["reason"]) == ("auto", "side_effecting_request")

    async def test_plan_always_plans(self) -> None:
        calls = _Calls()
        container, planner = await _ready(calls, _step("s1", "create_note", "draft"))
        result = await container.kernel.run(_turn("hello", plan_mode="plan"))
        assert planner.asked == 1 and result.plan_mode["effective"] == "plan"

    async def test_the_deployment_default_mode_applies_to_a_turn_that_names_none(self) -> None:
        calls = _Calls()
        container, planner = await _ready(calls, _step("s1", "create_note", "draft"),
                                          default_plan_mode="auto")
        result = await container.kernel.run(_turn("create a note called draft"))
        assert planner.asked == 1 and result.plan_mode["effective"] == "auto"
        assert result.plan_mode["requested"] is None

    async def test_the_engineer_always_plans_in_auto_and_by_default(self) -> None:
        from datetime import datetime, timezone

        from aura_core.domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION

        calls = _Calls()
        container, planner = await _ready(calls, _step("s1", "create_note", "draft"))
        await container.engineer_consent.accept(USER, ENGINEER_CONSENT_NOTICE_VERSION, at=datetime.now(timezone.utc))
        for mode, expected in ((None, "default"), ("auto", "auto")):
            before = planner.asked
            result = await container.kernel.run(_turn(
                "what does the readme say?", agent="engineer", capabilities=("engineer_agent",), plan_mode=mode,
            ))
            assert planner.asked == before + 1 and result.plan_mode["effective"] == expected
            assert result.plan_mode["reason"] == "agentic_persona"


class TestReview:
    async def test_review_runs_nothing_and_returns_the_plan_with_approval_ids(self) -> None:
        calls = _Calls()
        container, planner = await _ready(calls, *NOTE_AND_SEND)
        result = await container.kernel.run(_turn(plan_mode="review"))

        assert calls.ran == [], "the review gate must stop everything, including auto-allowed steps"
        assert planner.asked == 1
        assert result.plan_mode == {
            "requested": "review", "effective": "review", "reason": "requested", "planned": True,
            "awaiting_approval": True, "planning_failed": False,
        }
        assert [(s["id"], s["status"], s.get("tool")) for s in result.plan_summary] == [
            ("s1", "pending", "create_note"), ("s2", "pending", "send_message")]
        assert [c["tool"] for c in result.pending_tool_calls] == ["create_note", "send_message"]
        assert all(c["approval_id"] and c["review"] is True for c in result.pending_tool_calls)
        assert result.pending_tool_calls[1]["arguments"] == {"target": "bob"}
        assert result.needs_confirmation == ("create_note", "send_message")
        assert result.telemetry.plan_steps == 2

    async def test_the_model_is_told_nothing_ran(self) -> None:
        calls = _Calls()
        container, _ = await _ready(calls, *NOTE_AND_SEND)
        await container.kernel.run(_turn(plan_mode="review"))
        system = "\n".join(m.content for m in container.providers[0].calls[-1].conversation if m.role.value == "system")
        assert "Plan review" in system and "No step has run" in system

    async def test_approving_runs_the_stored_plan_once_and_never_replans(self) -> None:
        calls = _Calls()
        container, planner = await _ready(calls, *NOTE_AND_SEND)
        held = await container.kernel.run(_turn(plan_mode="review"))
        approvals = tuple(c["approval_id"] for c in held.pending_tool_calls)

        planner.plan_to_return = _plan(_step("s1", "send_message", "mallory"))  # a re-plan would do this
        done = await container.kernel.run(_turn("yes, go ahead", approval_ids=approvals))

        assert planner.asked == 1, "the approved plan runs; the planner is not asked again"
        assert calls.ran == [("create_note", "draft"), ("send_message", "bob")]
        assert done.plan_mode["effective"] == "resumed" and done.plan_mode["reason"] == "approved_plan"
        assert done.pending_tool_calls == ()

    async def test_an_approval_is_single_use_and_bound_to_its_owner_and_session(self) -> None:
        calls = _Calls()
        container, _ = await _ready(calls, *NOTE_AND_SEND)
        held = await container.kernel.run(_turn(plan_mode="review"))
        approvals = tuple(c["approval_id"] for c in held.pending_tool_calls)

        stranger = await container.kernel.run(_turn("go", approval_ids=approvals, user_id=UserId("intruder")))
        elsewhere = await container.kernel.run(_turn("go", approval_ids=approvals, session_id=SessionId("other")))
        assert stranger.telemetry.error_kind == elsewhere.telemetry.error_kind == "confirmation_invalid"
        assert calls.ran == []

        await container.kernel.run(_turn("go", approval_ids=approvals))
        again = await container.kernel.run(_turn("go", approval_ids=approvals))
        assert again.telemetry.error_kind == "confirmation_invalid"
        assert calls.count("send_message") == 1

    async def test_declining_is_just_not_sending_the_ids(self) -> None:
        calls = _Calls()
        container, _ = await _ready(calls, *NOTE_AND_SEND)
        await container.kernel.run(_turn(plan_mode="review"))
        await container.kernel.run(_turn("never mind"))
        assert calls.ran == []

    async def test_a_step_whose_arguments_depend_on_an_earlier_one_is_held_again_when_reached(self) -> None:
        calls = _Calls()
        chained = PlanStep(
            id=StepId("s2"), kind=StepKind.TOOL, tool="send_message", description="send the note",
            arguments={"target": "${s1.output}"}, depends_on=(StepId("s1"),),
        )
        container, _ = await _ready(calls, _step("s1", "create_note", "draft"), chained)
        held = await container.kernel.run(_turn(plan_mode="review"))
        approvals = tuple(c["approval_id"] for c in held.pending_tool_calls)

        done = await container.kernel.run(_turn("go", approval_ids=approvals))
        assert calls.count("create_note") == 1 and calls.count("send_message") == 0
        (again,) = done.pending_tool_calls
        assert again["tool"] == "send_message" and again["arguments"] == {"target": "note draft"}
        assert again["approval_id"] and "review" not in again, "the second gate is the ordinary per-call one"

    async def test_a_plan_with_nothing_to_approve_is_not_held(self) -> None:
        from aura_core.domain.identifiers import TurnId

        container, planner = await _ready(_Calls(), *NOTE_AND_SEND)
        generate = PlanStep(id=StepId("g1"), kind=StepKind.GENERATE, description="write a summary")
        planner.plan_to_return = _plan(generate, dataclasses.replace(generate, id=StepId("g2")))
        hold = await container.kernel._hold_plan_for_review(
            _turn(), "write a summary", [{"name": "create_note"}], False, TurnId.new())
        assert hold.held is False and hold.plan is not None and hold.pending == ()

    async def test_the_taint_of_the_reviewed_turn_is_kept_for_the_resume(self) -> None:
        calls = _Calls()
        container, _ = await _ready(calls, *NOTE_AND_SEND)
        from aura_core.domain.caller_context import CallerContext

        evidence = (CallerContext.of(id="doc1", text="some caller evidence", source="crm"),)
        held = await container.kernel.run(_turn(plan_mode="review", caller_context=evidence))
        assert held.telemetry.tainted is True
        approvals = tuple(c["approval_id"] for c in held.pending_tool_calls)
        done = await container.kernel.run(_turn("go", approval_ids=approvals))
        assert done.telemetry.tainted is True, "the resumed turn starts from the held turn's taint"

    async def test_the_stream_holds_and_resumes_too(self) -> None:
        calls = _Calls()
        container, planner = await _ready(calls, *NOTE_AND_SEND)
        frames = [f async for f in container.kernel.stream(_turn(plan_mode="review"))]
        done = frames[-1]
        assert done["type"] == "done" and calls.ran == []
        assert done["plan_mode"]["effective"] == "review" and done["plan_mode"]["awaiting_approval"] is True
        assert [c["tool"] for c in done["pending_tool_calls"]] == ["create_note", "send_message"]
        assert not any(f["type"] == "step" for f in frames), "nothing ran, so no step progress"

        approvals = tuple(c["approval_id"] for c in done["pending_tool_calls"])
        resumed = [f async for f in container.kernel.stream(_turn("go", approval_ids=approvals))]
        assert resumed[-1]["type"] == "done" and resumed[-1]["plan_mode"]["effective"] == "resumed"
        assert calls.ran == [("create_note", "draft"), ("send_message", "bob")] and planner.asked == 1

    async def test_the_deployment_default_can_be_review(self) -> None:
        calls = _Calls()
        container, _ = await _ready(calls, *NOTE_AND_SEND, default_plan_mode="review")
        held = await container.kernel.run(_turn())
        assert calls.ran == [] and held.plan_mode["effective"] == "review" and held.plan_mode["requested"] is None

    async def test_a_review_that_cannot_be_stored_fails_closed(self) -> None:
        calls = _Calls()
        container, _ = await _ready(calls, *NOTE_AND_SEND)

        async def broken(**kwargs):
            raise RuntimeError("cache down")

        container.kernel._confirmations.issue = broken
        result = await container.kernel.run(_turn(plan_mode="review"))
        assert calls.ran == [], "an unapprovable review must not run anything"
        assert result.pending_tool_calls == () and result.telemetry.planning_failed is True


class TestSettings:
    def test_an_unknown_default_mode_is_refused_at_startup(self) -> None:
        base = load_settings(refresh=True, validate=False)
        bad = dataclasses.replace(base, reasoning=dataclasses.replace(base.reasoning, default_plan_mode="turbo"))
        with pytest.raises(Exception) as raised:
            bad.validate()
        assert any("AURA_REASONING_DEFAULT_PLAN_MODE" in p for p in raised.value.details["problems"])

    def test_the_default_is_empty(self) -> None:
        assert load_settings(refresh=True, validate=False).reasoning.default_plan_mode == ""


class TestHttp:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app
        from aura_core.domain.errors import AuthenticationError

        calls = _Calls()
        import asyncio

        container, planner = asyncio.run(_ready(calls, *NOTE_AND_SEND))

        async def authenticator(request):
            if request.headers.get("authorization") != f"Bearer {USER.value}":
                raise AuthenticationError("unknown principal")
            return Principal(user_id=USER, email="m@example.com", email_verified=True)

        with TestClient(build_app(container, authenticator=authenticator)) as c:
            c.calls, c.planner = calls, planner
            yield c

    HEADERS = {"Authorization": f"Bearer {USER.value}"}

    def test_chat_in_review_mode_returns_the_plan_and_runs_nothing(self, client) -> None:
        response = client.post("/api/v3/chat", headers=self.HEADERS, json={
            "message": "create a note called draft", "plan_mode": "review", "session_id": "http-s"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["plan_mode"]["effective"] == "review" and body["plan_mode"]["awaiting_approval"] is True
        assert [c["tool"] for c in body["pending_tool_calls"]] == ["create_note", "send_message"]
        assert [s["status"] for s in body["plan"]] == ["pending", "pending"]
        assert client.calls.ran == []

        approvals = [c["approval_id"] for c in body["pending_tool_calls"]]
        done = client.post("/api/v3/chat", headers=self.HEADERS, json={
            "message": "go", "approval_ids": approvals, "session_id": "http-s"})
        assert done.status_code == 200 and done.json()["plan_mode"]["effective"] == "resumed"
        assert client.calls.ran == [("create_note", "draft"), ("send_message", "bob")]

    def test_the_default_response_reports_default(self, client) -> None:
        body = client.post("/api/v3/chat", headers=self.HEADERS, json={"message": "hello"}).json()
        assert body["plan_mode"]["effective"] == "default" and body["plan_mode"]["planned"] is False

    def test_the_old_bool_still_works_over_http(self, client) -> None:
        body = client.post("/api/v3/chat", headers=self.HEADERS,
                           json={"message": "create a note called draft", "plan": True, "session_id": "s2"}).json()
        assert body["plan_mode"]["requested"] == "plan: true" and client.planner.asked >= 1

    def test_an_unknown_mode_is_a_422(self, client) -> None:
        response = client.post("/api/v3/chat", headers=self.HEADERS, json={"message": "hi", "plan_mode": "turbo"})
        assert response.status_code == 422

    def test_the_stream_reports_the_mode_in_its_done_frame(self, client) -> None:
        import json

        with client.stream("POST", "/api/v3/chat/stream", headers=self.HEADERS, json={
                "message": "create a note called draft", "plan_mode": "review", "session_id": "s3"}) as response:
            events = [line for line in response.iter_lines() if line.startswith("data: ")]
        done = json.loads(events[-1][len("data: "):])
        assert done["type"] == "done" and done["plan_mode"]["awaiting_approval"] is True
        assert client.calls.ran == []
