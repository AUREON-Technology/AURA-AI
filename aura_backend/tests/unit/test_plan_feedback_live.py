"""Free-text feedback on a live plan held for approval (ADR-047, row 51).

Real container, kernel, confirmation store, tool registry and the real ``PlanningEngine.replan``
(renumbering, revision record, allowance). The only stand-in is what "the model" writes: the
planner's ``plan`` step is scripted, so what is asserted is what the gate does with a comment --
above all that nothing runs until the (new) approval, that the old approval stops working, and
that a comment is bounded, owner-scoped and audited.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.errors import ConfirmationInvalidError, PlanningError, ValidationError
from aura_core.domain.identifiers import PlanId, SessionId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
from aura_core.kernel import TurnRequest
from aura_core.tools import param, tool
from aura_core.tools.base import FunctionTool

USER = UserId("u-feedback")
SESSION = SessionId("conv-feedback")


class _Ran:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []


def _step(step_id: str, name: str, target: str, *, depends_on: tuple[str, ...] = ()) -> PlanStep:
    return PlanStep(
        id=StepId(step_id), kind=StepKind.TOOL, tool=name, description=f"{name} {target}",
        arguments={"target": target}, depends_on=tuple(StepId(d) for d in depends_on),
    )


def _plan(*steps: PlanStep) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=tuple(steps))


class _Script:
    """What the planner model writes: the first call composes, later calls are revisions."""

    def __init__(self, *plans: Plan) -> None:
        self.plans = list(plans)
        self.goals: list[str] = []

    async def plan(self, goal, available_tools=(), max_steps=None):  # noqa: ANN001
        self.goals.append(goal.description)
        return self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]


async def _ready(ran: _Ran, script: _Script, **planning):
    base = load_settings(refresh=True, validate=False)
    settings = dataclasses.replace(base, reasoning=dataclasses.replace(base.reasoning, **planning))
    container = Container(settings)
    await container.startup()

    @tool("create_note", "Create a note.", param("target", "string", "text"),
          side_effecting=True, sensitivity=ToolSensitivity.PUBLIC)
    async def create_note(target: str) -> str:
        ran.calls.append(("create_note", target))
        return f"note {target}"

    async def send(target: str) -> str:
        ran.calls.append(("send_message", target))
        return f"sent {target}"

    send_spec = ToolSpec(
        name="send_message", description="A confirm-gated side effect.",
        parameters=(ParameterSpec(name="target", type="string", description="target"),),
        category=ToolCategory.UTILITY, sensitivity=ToolSensitivity.CONFIDENTIAL, side_effecting=True,
    )
    container.tools.register(create_note)
    container.tools.register(FunctionTool(send_spec, send))
    # The *real* planner (needs_plan heuristics, goal_from_message, replan) with only the model's
    # plan scripted.
    container.planner.plan = script.plan
    return container


def _turn(message: str = "create a note called draft", **overrides) -> TurnRequest:
    base = dict(message=message, user_id=USER, session_id=SESSION)
    base.update(overrides)
    return TurnRequest(**base)


NOTE_AND_SEND = (_step("s1", "create_note", "draft"), _step("s2", "send_message", "bob", depends_on=("s1",)))
NOTE_ONLY = (_step("s1", "create_note", "draft"),)


async def _hold(container):
    held = await container.kernel.run(_turn(plan_mode="review"))
    assert held.plan_mode["awaiting_approval"] is True
    return held, tuple(c["approval_id"] for c in held.pending_tool_calls)


async def _feedback(container, ids, text, **overrides):
    return await container.kernel.revise_held_plan(
        _turn(text, approval_ids=ids, **overrides)
    )


class TestRevision:
    async def test_feedback_replans_the_held_plan_and_nothing_runs(self) -> None:
        ran = _Ran()
        script = _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY))
        container = await _ready(ran, script)
        _held, ids = await _hold(container)

        result = await _feedback(container, ids, "Remove step 2, I do not want to message anyone.")

        assert ran.calls == [], "feedback revises the plan; it never runs it"
        assert (result.intent, result.revised, result.approved) == ("REMOVE_STEP", True, False)
        assert [s["tool"] for s in result.plan_summary] == ["create_note"]
        assert result.revision == 1
        assert [c["tool"] for c in result.pending_tool_calls] == ["create_note"]
        assert result.actions == ("Removed step 2: 'send_message bob'",)
        # The replan prompt carried the current plan, the person's words and the edited outline.
        prompt = script.goals[-1]
        assert "1. create_note draft" in prompt and "2. send_message bob" in prompt
        assert "Remove step 2" in prompt

    async def test_the_old_approval_stops_working_and_the_new_one_runs_the_revised_plan(self) -> None:
        ran = _Ran()
        container = await _ready(ran, _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY)))
        _held, old_ids = await _hold(container)
        result = await _feedback(container, old_ids, "remove step 2")
        new_ids = tuple(c["approval_id"] for c in result.pending_tool_calls)
        assert set(new_ids).isdisjoint(old_ids)

        stale = await container.kernel.run(_turn("go", approval_ids=old_ids))
        assert stale.telemetry.error_kind == "confirmation_invalid" and ran.calls == []

        done = await container.kernel.run(_turn("go", approval_ids=new_ids))
        assert done.telemetry.outcome.value == "success"
        assert ran.calls == [("create_note", "draft")], "the message the person removed never ran"

    async def test_feedback_can_be_given_again_on_the_revised_plan(self) -> None:
        ran = _Ran()
        container = await _ready(ran, _Script(
            _plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY),
            _plan(_step("s1", "create_note", "draft"), _step("s2", "create_note", "second")),
        ))
        _held, ids = await _hold(container)
        first = await _feedback(container, ids, "remove step 2")
        second = await _feedback(
            container, tuple(c["approval_id"] for c in first.pending_tool_calls), "add a second note")
        assert second.revision == 2 and [s["tool"] for s in second.plan_summary] == ["create_note"] * 2
        assert ran.calls == []

    async def test_an_approval_or_a_question_leaves_the_plan_and_its_ids_alone(self) -> None:
        ran = _Ran()
        script = _Script(_plan(*NOTE_AND_SEND))
        container = await _ready(ran, script)
        _held, ids = await _hold(container)
        before = len(script.goals)

        approved = await _feedback(container, ids, "looks good, approve")
        asked = await _feedback(container, ids, "why is the second part needed?")
        assert (approved.intent, approved.revised, approved.approved) == ("APPROVE", False, True)
        assert (asked.intent, asked.revised) == ("CLARIFICATION_QUESTION", False)
        assert len(script.goals) == before, "no replan for either"
        assert approved.extra["approval_ids"] == list(ids)

        done = await container.kernel.run(_turn("go", approval_ids=ids))
        assert ran.calls == [("create_note", "draft"), ("send_message", "bob")], "the original ids still work"

    async def test_feedback_that_names_no_steps_is_still_a_revision(self) -> None:
        ran = _Ran()
        script = _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY))
        container = await _ready(ran, script)
        _held, ids = await _hold(container)
        result = await _feedback(container, ids, "I don't approve of this plan, only keep the note")
        assert result.intent == "REJECT" and result.revised is True
        assert "only keep the note" in script.goals[-1]


class TestBoundsAndSafety:
    async def test_feedback_is_bounded_by_the_revision_allowance(self) -> None:
        ran = _Ran()
        container = await _ready(ran, _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY)), max_replans=1)
        _held, ids = await _hold(container)
        first = await _feedback(container, ids, "remove step 2")
        ids = tuple(c["approval_id"] for c in first.pending_tool_calls)
        with pytest.raises(PlanningError, match="replanning limit"):
            await _feedback(container, ids, "add another note")
        done = await container.kernel.run(_turn("go", approval_ids=ids))
        assert done.telemetry.outcome.value == "success" and ran.calls == [("create_note", "draft")], (
            "a refused revision leaves the held plan approvable"
        )

    async def test_a_failed_replan_never_replaces_the_plan_with_a_bare_answer(self) -> None:
        ran = _Ran()
        fallback = _plan(PlanStep(id=StepId("s1"), kind=StepKind.RESPOND, description="answer directly"))
        container = await _ready(ran, _Script(_plan(*NOTE_AND_SEND), fallback))
        _held, ids = await _hold(container)
        with pytest.raises(PlanningError, match="could not revise"):
            await _feedback(container, ids, "remove step 2")
        await container.kernel.run(_turn("go", approval_ids=ids))
        assert ran.calls == [("create_note", "draft"), ("send_message", "bob")]

    async def test_a_stranger_or_another_session_cannot_revise_or_learn_the_plan(self) -> None:
        container = await _ready(_Ran(), _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY)))
        _held, ids = await _hold(container)
        with pytest.raises(ConfirmationInvalidError):
            await _feedback(container, ids, "remove step 2", user_id=UserId("intruder"))
        with pytest.raises(ConfirmationInvalidError):
            await _feedback(container, ids, "remove step 2", session_id=SessionId("elsewhere"))
        mine = await _feedback(container, ids, "remove step 2")
        assert mine.revised is True, "the refusals consumed nothing"

    async def test_an_old_or_made_up_id_is_refused(self) -> None:
        container = await _ready(_Ran(), _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY)))
        _held, ids = await _hold(container)
        await _feedback(container, ids, "remove step 2")
        with pytest.raises(ConfirmationInvalidError):
            await _feedback(container, ids, "remove step 2 again")
        with pytest.raises(ConfirmationInvalidError):
            await _feedback(container, ("apr.nope.nope",), "remove step 2")

    async def test_two_comments_at_once_revise_the_plan_once(self) -> None:
        container = await _ready(_Ran(), _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY)))
        _held, ids = await _hold(container)
        results = await asyncio.gather(
            _feedback(container, ids, "remove step 2"), _feedback(container, ids, "remove step 1"),
            return_exceptions=True,
        )
        assert sum(isinstance(r, ConfirmationInvalidError) for r in results) == 1
        assert sum(not isinstance(r, Exception) for r in results) == 1

    async def test_empty_and_oversized_feedback_are_refused_before_anything_is_read(self) -> None:
        container = await _ready(_Ran(), _Script(_plan(*NOTE_AND_SEND)))
        _held, ids = await _hold(container)
        with pytest.raises(ValidationError):
            await _feedback(container, ids, "   ")
        with pytest.raises(ValidationError):
            await _feedback(container, ids, "x" * 2001)

    async def test_a_revision_is_audited_without_the_plan_or_the_comment(self) -> None:
        container = await _ready(_Ran(), _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY)))
        _held, ids = await _hold(container)
        await _feedback(container, ids, "remove step 2 please")
        (event,) = await container.audit.query(actor=USER.value, action="plan.feedback")
        assert event.outcome == "revised" and event.detail == {"intent": "REMOVE_STEP", "revisions": 1}
        assert "remove step 2" not in repr(event.detail)

    async def test_the_taint_of_the_held_turn_survives_a_revision(self) -> None:
        from aura_core.domain.caller_context import CallerContext

        container = await _ready(_Ran(), _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY)))
        evidence = (CallerContext.of(id="doc1", text="some caller evidence", source="crm"),)
        held = await container.kernel.run(_turn(plan_mode="review", caller_context=evidence))
        ids = tuple(c["approval_id"] for c in held.pending_tool_calls)
        revised = await _feedback(container, ids, "remove step 2")
        done = await container.kernel.run(_turn("go", approval_ids=tuple(
            c["approval_id"] for c in revised.pending_tool_calls)))
        assert done.telemetry.tainted is True


class TestHttp:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app
        from aura_core.domain.errors import AuthenticationError

        ran = _Ran()
        container = asyncio.run(_ready(ran, _Script(_plan(*NOTE_AND_SEND), _plan(*NOTE_ONLY))))

        async def authenticator(request):
            who = request.headers.get("authorization", "").removeprefix("Bearer ")
            if who not in (USER.value, "u-other"):
                raise AuthenticationError("unknown principal")
            return Principal(user_id=UserId(who), email=f"{who}@example.com", email_verified=True)

        with TestClient(build_app(container, authenticator=authenticator)) as c:
            c.ran = ran
            yield c

    HEADERS = {"Authorization": f"Bearer {USER.value}"}

    def test_chat_review_then_feedback_then_approve(self, client) -> None:
        held = client.post("/api/v3/chat", headers=self.HEADERS, json={
            "message": "create a note called draft", "plan_mode": "review", "session_id": "http-fb"}).json()
        ids = [c["approval_id"] for c in held["pending_tool_calls"]]
        assert len(ids) == 2

        response = client.post("/api/v3/chat/plan-feedback", headers=self.HEADERS, json={
            "approval_ids": ids, "feedback": "remove step 2", "session_id": "http-fb"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["revised"] is True and [c["tool"] for c in body["pending_tool_calls"]] == ["create_note"]
        assert client.ran.calls == []

        new_ids = [c["approval_id"] for c in body["pending_tool_calls"]]
        done = client.post("/api/v3/chat", headers=self.HEADERS, json={
            "message": "go", "approval_ids": new_ids, "session_id": "http-fb"})
        assert done.status_code == 200 and client.ran.calls == [("create_note", "draft")]

    def test_a_stranger_gets_the_409_an_approval_would(self, client) -> None:
        held = client.post("/api/v3/chat", headers=self.HEADERS, json={
            "message": "create a note called draft", "plan_mode": "review", "session_id": "http-fb2"}).json()
        ids = [c["approval_id"] for c in held["pending_tool_calls"]]
        response = client.post("/api/v3/chat/plan-feedback", headers={"Authorization": "Bearer u-other"}, json={
            "approval_ids": ids, "feedback": "remove step 2", "session_id": "http-fb2"})
        assert response.status_code == 409 and response.json()["error"] == "confirmation_invalid"

    def test_the_body_is_validated(self, client) -> None:
        for body in ({"approval_ids": [], "feedback": "x"}, {"approval_ids": ["a"], "feedback": ""},
                     {"approval_ids": ["a"], "feedback": "y" * 2001}, {"feedback": "x"}):
            assert client.post("/api/v3/chat/plan-feedback", headers=self.HEADERS, json=body).status_code == 422
