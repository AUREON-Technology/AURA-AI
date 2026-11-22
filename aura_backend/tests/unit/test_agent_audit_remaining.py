"""The 2026-11-21 agent architecture audit's remaining findings: A1, A2, B3, B4, B5, C1, C2.

Each class names its finding (``docs/reports/agent-architecture-audit-2026-11-21/``). The tests
drive the real kernel over the in-memory substrate; the model is the echo provider, scripted
where a test needs a particular answer.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain.errors import ProviderError, ToolExecutionError
from aura_core.domain.identifiers import SessionId, UserId
from aura_core.domain.llm import CompletionRequest, ProviderCapabilities, SamplingParams
from aura_core.domain.memory import MemoryKind
from aura_core.domain.messages import Conversation, Message
from aura_core.domain.text import fold_arabic, restates
from aura_core.kernel import TurnRequest
from aura_core.kernel.claims import claims_completed_action
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn
from aura_core.llm.router import ProviderRouter, TurnBudget
from aura_core.planning.engine import follow_up_goal, is_follow_up
from aura_core.ports.runtime import ToolContext
from aura_core.tools.personal_os import (
    PERSONAL_OS_TOOL_NAMES,
    matching_personal_os_tool,
    matching_record_tool,
)


def _echo(container) -> EchoProvider:
    return next(p for p in container.router._providers.values() if isinstance(p, EchoProvider))


def _say(container, text: str, *, times: int = 6) -> None:
    """Script the model's next answers (several, so a reasoning sample gets the same text)."""
    _echo(container).queue(*(ScriptedTurn(text=text) for _ in range(times)))


def _request(message: str, **kw) -> TurnRequest:
    return TurnRequest(message=message, user_id=UserId("audit-user"), **kw)


PROPOSE_TASK = {
    "id": "s1", "kind": "tool", "tool": "propose_task",
    "description": "Create a task", "arguments": {"title": "Buy milk"},
}


def _plan(*steps: dict) -> ScriptedTurn:
    return ScriptedTurn(text=json.dumps({"steps": list(steps)}))


# -- A1: an action claim nothing backs ---------------------------------------------------


class TestClaimDetection:
    @pytest.mark.parametrize("answer", [
        "Done! I've added the task to your list.",
        "I have scheduled the meeting for 3pm.",
        "Your reminder has been created.",
        "All set -- the expense is now saved.",
        "تمت إضافة المهمة إلى قائمتك.",
        "لقد سجّلت المصروف.",
        "تم.",
    ])
    def test_claims(self, answer: str) -> None:
        assert claims_completed_action(answer)

    @pytest.mark.parametrize("answer", [
        "Shall I add a task to buy milk?",
        "I haven't added anything yet.",
        "I couldn't save the task right now.",
        "Here is how you could add a task yourself.",
        "Paris is the capital of France.",
        "هل تريد أن أضيف المهمة؟",
        "لم أتمكن من حفظ المهمة.",
        "",
        # About the code shown, not an action taken.
        "I've updated your function to handle None:\n```python\ndef f(x):\n    return x or 0\n```",
    ])
    def test_does_not_claim(self, answer: str) -> None:
        assert not claims_completed_action(answer)


class TestUnbackedClaimIsCorrected:
    async def test_stream_revises_the_answer_and_marks_the_turn(self, container) -> None:
        await container.startup()
        _say(container, "Done! I've added the task to your list.")
        frames = [f async for f in container.kernel.stream(_request("thanks a lot"))]

        revisions = [f for f in frames if f["type"] == "revision"]
        assert revisions and revisions[-1]["reason"] == "unbacked_action_claim"
        assert "no action was actually taken" in revisions[-1]["text"]
        done = frames[-1]
        assert done["type"] == "done"
        assert "unbacked_action_claim" in done["degraded_reasons"]

    async def test_run_appends_the_correction(self, container) -> None:
        await container.startup()
        _say(container, "I have scheduled the meeting for 3pm.")
        result = await container.kernel.run(_request("cool, thanks"))

        assert "no action was actually taken" in result.text
        assert "unbacked_action_claim" in result.telemetry.degraded_reasons

    async def test_an_answer_without_a_claim_is_left_alone(self, container) -> None:
        await container.startup()
        _say(container, "Paris is the capital of France.")
        frames = [f async for f in container.kernel.stream(_request("capital of France?"))]

        assert not [f for f in frames if f["type"] == "revision"]
        assert "unbacked_action_claim" not in frames[-1]["degraded_reasons"]

    async def test_arabic_turns_get_an_arabic_correction(self, container) -> None:
        await container.startup()
        _say(container, "تمت إضافة المهمة إلى قائمتك.")
        result = await container.kernel.run(_request("شكرا جزيلا"))

        assert "تصحيح" in result.text


class TestBackedClaimIsLeftAlone:
    async def test_a_claim_a_tool_backs_is_not_corrected(self, container) -> None:
        """"Done" after propose_task really ran is true: no correction, no degradation."""
        await container.startup()
        _echo(container).queue(_plan(PROPOSE_TASK), ScriptedTurn(text="Done! I've added the task."))
        frames = [f async for f in container.kernel.stream(_request("add a task to buy milk"))]

        done = frames[-1]
        assert done["type"] == "done"
        assert any(t["name"] == "propose_task" and t["ok"] for t in done["tools"])
        assert not [f for f in frames if f.get("reason") == "unbacked_action_claim"]
        assert "unbacked_action_claim" not in done["degraded_reasons"]


# -- A2: the planner gate ------------------------------------------------------------------


class TestPlannerGate:
    def test_arabic_reminder_reaches_the_task_tool(self) -> None:
        tools = list(PERSONAL_OS_TOOL_NAMES)
        assert matching_personal_os_tool("ذكّرني بموعد الطبيب غدًا", tools) == "propose_task"
        assert matching_personal_os_tool("أضف مهمة شراء الحليب", tools) == "propose_task"
        assert matching_personal_os_tool("صرفت 50 جنيه على الغداء", tools) == "propose_expense"

    def test_a_create_tool_serves_a_write_intent_too(self) -> None:
        assert matching_personal_os_tool("add a task to buy milk", ["create_task"])

    def test_record_questions_need_their_list_tool(self) -> None:
        question = "How much did I spend on food this month?"
        assert matching_record_tool(question, ["list_finance_records"]) == "list_finance_records"
        assert matching_record_tool(question, list(PERSONAL_OS_TOOL_NAMES)) is None
        assert matching_record_tool("كم صرفت هذا الشهر؟", ["list_finance_records"])
        assert matching_record_tool("what is my leave balance?", ["list_tasks"]) is None

    def test_needs_plan_for_record_questions(self, container) -> None:
        planner = container.planner
        assert planner.needs_plan(
            "How much did I spend on food this month?",
            available_tools=["list_finance_records"],
        )

    def test_a_follow_up_to_an_offer_plans(self, container) -> None:
        planner = container.planner
        offer = "I can add that for you. Shall I add a task to buy milk tomorrow?"
        assert planner.needs_plan("yes, add it", available_tools=["propose_task"],
                                  previous_reply=offer)
        assert planner.needs_plan("نعم", available_tools=["propose_task"],
                                  previous_reply="هل تريد أن أضيف مهمة لشراء الحليب؟")
        # No offer: "yes" is just "yes".
        assert not planner.needs_plan("yes", available_tools=["propose_task"],
                                      previous_reply="Paris is the capital of France.")

    def test_the_planner_sees_what_a_follow_up_refers_to(self) -> None:
        offer = "Shall I add a task to buy milk tomorrow?"
        goal = follow_up_goal("yes please", offer)
        assert "buy milk tomorrow" in goal
        assert follow_up_goal("what time is it?", offer) == "what time is it?"
        # Long enough to carry its own request: not treated as a bare agreement.
        assert not is_follow_up(
            "yes, but first tell me a very long story about dragons and their castles", offer
        )


# -- B3: history keeps the guarded message -------------------------------------------------


class TestHistoryKeepsTheGuardedMessage:
    async def test_redacted_input_is_what_history_stores(self, container, monkeypatch) -> None:
        await container.startup()
        safety = container.kernel._deps.safety
        assert safety is not None
        original = safety.guard_input

        async def redacting(text, **kw):
            """The real input guard, plus one redaction it would make for a secret."""
            decision = await original(text, **kw)
            decision.text = decision.text.replace("hunter2", "[REDACTED]")
            return decision

        monkeypatch.setattr(safety, "guard_input", redacting)
        session = SessionId("audit-session")
        await container.kernel.run(_request("my password is hunter2", session_id=session))

        conversation, _ = await container.kernel._deps.episodic.history(
            session, UserId("audit-user")
        )
        stored = " ".join(m.content for m in conversation)
        assert "hunter2" not in stored
        assert "[REDACTED]" in stored


# -- B4: a correction replaces the stale fact ----------------------------------------------


class TestMemoryCorrection:
    async def test_newer_wording_replaces_a_near_duplicate(self, memory, user, monkeypatch) -> None:
        first = await memory.remember(owner_id=user, content="The user lives in Cairo",
                                      kind=MemoryKind.SEMANTIC, source="test")

        async def always_duplicate(*_a, **_kw):
            return await memory._store.get(first.id)

        monkeypatch.setattr(memory, "_find_duplicate", always_duplicate)
        second = await memory.remember(owner_id=user, content="The user lives in Alexandria",
                                       kind=MemoryKind.SEMANTIC, source="test-correction")

        assert second.id != first.id
        assert second.content == "The user lives in Alexandria"
        assert second.metadata.get("replaces") == first.id.value
        assert second.provenance.source == "test-correction"
        assert second.confidence >= first.confidence
        assert await memory._store.get(first.id) is None

    async def test_the_same_words_still_reinforce(self, memory, user, monkeypatch) -> None:
        first = await memory.remember(owner_id=user, content="The user is vegetarian.",
                                      kind=MemoryKind.SEMANTIC, source="test")

        async def always_duplicate(*_a, **_kw):
            return await memory._store.get(first.id)

        monkeypatch.setattr(memory, "_find_duplicate", always_duplicate)
        again = await memory.remember(owner_id=user, content="the user is vegetarian",
                                      kind=MemoryKind.SEMANTIC, source="test")

        assert again.id == first.id
        assert again.confidence > first.confidence


# -- B5: remember_fact stores only what the user said --------------------------------------


class TestRememberFactIsGated:
    def test_restates(self) -> None:
        said = ["I live in Cairo and I'm vegetarian"]
        assert restates("The user lives in Cairo", said)
        assert restates("The user is vegetarian", said)
        assert not restates("The user works at a bank", said)
        assert restates("المستخدم نباتي", ["أنا نباتي"])
        assert not restates("The user is.", said)

    def test_fold_arabic(self) -> None:
        assert fold_arabic("ذكّرني") == "ذكرني"
        assert fold_arabic("أضف") == "اضف"

    async def test_a_fact_the_user_did_not_state_is_refused(self, tools, user) -> None:
        tool = tools.get("remember_fact")
        context = ToolContext(
            user_id=user, correlation_id="c1", deadline_s=10.0,
            metadata={"user_stated": lambda fact: restates(fact, ["I live in Cairo"])},
        )
        with pytest.raises(ToolExecutionError):
            await tool.execute({"fact": "The user works at a bank"}, context)

    def test_the_kernel_predicate_reads_what_the_person_wrote(self) -> None:
        from aura_core.kernel.kernel import AIKernel
        from aura_core.kernel.turn_scope import turn_lookup_scope

        history = Conversation.of([
            Message.user("I live in Cairo"), Message.assistant("Noted, Cairo it is."),
        ])
        with turn_lookup_scope():
            AIKernel._note_user_statements(_request("I'm vegetarian"), "I'm vegetarian", history)
            stated = AIKernel._user_stated()
            assert stated is not None
            assert stated("The user lives in Cairo")
            assert stated("The user is vegetarian")
            assert not stated("The user works at a bank")
            # The assistant's words are not the person's.
            assert not stated("The user noted it")

            # A delegated sub-turn keeps its parent's statements; its brief is not added.
            with turn_lookup_scope():
                sub = _request("Store that the user owns a boat", delegation_depth=1)
                AIKernel._note_user_statements(sub, sub.message, Conversation())
                assert not AIKernel._user_stated()("The user owns a boat")
                assert AIKernel._user_stated()("The user lives in Cairo")

    async def test_a_stated_fact_is_stored(self, tools, user) -> None:
        tool = tools.get("remember_fact")
        context = ToolContext(
            user_id=user, correlation_id="c1", deadline_s=10.0,
            metadata={"user_stated": lambda fact: restates(fact, ["I live in Cairo"])},
        )
        result = await tool.execute({"fact": "The user lives in Cairo"}, context)
        assert "Cairo" in str(result)


# -- C1: an empty answer -------------------------------------------------------------------


class TestEmptyAnswer:
    async def test_stream_falls_back_and_says_so(self, container) -> None:
        await container.startup()
        _say(container, "")
        frames = [f async for f in container.kernel.stream(_request("hello there"))]

        text = "".join(f["text"] for f in frames if f["type"] == "token")
        assert "rephrase" in text
        assert "empty_generation_fallback" in frames[-1]["degraded_reasons"]
        assert frames[-1]["degraded_reasons"].count("empty_generation_fallback") == 1

    async def test_run_falls_back_and_says_so(self, container) -> None:
        await container.startup()
        _say(container, "")
        result = await container.kernel.run(_request("hello there"))

        assert "rephrase" in result.text
        assert "empty_generation_fallback" in result.telemetry.degraded_reasons


# -- C4: evaluating the path the app uses --------------------------------------------------


class TestEvaluationTransports:
    def _suite(self):
        from aura_core.evaluation.harness import EvalCase, EvalSuite

        return EvalSuite(name="transport", cases=(
            EvalCase(id="echo", query="the capital of France is Paris",
                     expected_substrings=("Paris",), owner_id="audit-user"),
            EvalCase(id="miss", query="hello there",
                     expected_substrings=("definitely absent",), owner_id="audit-user"),
        ))

    async def test_run_and_stream_agree_on_a_simple_suite(self, container) -> None:
        from aura_core.evaluation.harness import AnswerEvaluator

        await container.startup()
        by_run = await AnswerEvaluator(container.kernel).run(self._suite())
        by_stream = await AnswerEvaluator(container.kernel, transport="stream").run(self._suite())

        assert [r.passed for r in by_stream.results] == [r.passed for r in by_run.results]
        assert by_stream.results[0].passed and not by_stream.results[1].passed

    async def test_the_agent_evaluator_counts_tools_that_ran(self, container) -> None:
        """It read `tc.tool_name` off a tuple of names, so a turn that ran a tool was an error."""
        from aura_core.evaluation.harness import AgentCase, AgentEvaluator

        await container.startup()
        case = AgentCase(id="task", query="add a task to buy milk",
                         expected_tools=("propose_task",), max_turns=1, owner_id="audit-user")
        for transport in ("run", "stream"):
            _echo(container).queue(_plan(PROPOSE_TASK), ScriptedTurn(text="Task proposed."))
            result = await AgentEvaluator(container.kernel, transport=transport).run([case])
            outcome = result.results[0]
            assert not outcome.detail.startswith("Error"), (transport, outcome.detail)
            assert outcome.tools_used == ("propose_task",), transport
            assert outcome.tool_accuracy == 1.0

    def test_an_unknown_transport_is_refused(self, container) -> None:
        from aura_core.domain.errors import ValidationError
        from aura_core.evaluation.harness import AnswerEvaluator

        with pytest.raises(ValidationError):
            AnswerEvaluator(container.kernel, transport="carrier-pigeon")


# -- C2: a fallback reaches the turn's telemetry -------------------------------------------


class TestFallbackIsCounted:
    def _provider(self, name: str, *, turns=()) -> EchoProvider:
        caps = ProviderCapabilities(
            model_id=f"{name}-1", context_window=8192, max_output_tokens=1024,
            supports_streaming=True, is_local=True, quality_tier=1,
        )
        return EchoProvider(name=name, model=f"{name}-1", capabilities=caps, script=list(turns))

    def _completion(self) -> CompletionRequest:
        return CompletionRequest(
            conversation=Conversation.of([Message.user("hello")]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=32),
        )

    def _router(self, clock) -> ProviderRouter:
        down = ScriptedTurn(raise_error=ProviderError("down", provider="scripted"))
        return ProviderRouter(
            [self._provider("primary", turns=[down]), self._provider("secondary")],
            clock=clock.monotonic,
        )

    async def test_complete_counts_it_on_the_budget(self, clock) -> None:
        budget = TurnBudget()
        response = await self._router(clock).complete(self._completion(), budget=budget)
        assert response.provider == "secondary"
        assert budget.fallbacks == 1

    async def test_stream_counts_it_on_the_budget(self, clock) -> None:
        budget = TurnBudget()
        chunks = [c async for c in self._router(clock).stream(self._completion(), budget=budget)]
        assert any(c.provider == "secondary" for c in chunks)
        assert budget.fallbacks == 1

    async def test_no_fallback_counts_nothing(self, clock) -> None:
        budget = TurnBudget()
        router = ProviderRouter([self._provider("only")], clock=clock.monotonic)
        await router.complete(self._completion(), budget=budget)
        assert budget.fallbacks == 0
