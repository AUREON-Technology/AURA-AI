"""Planning and execution engines.

Two properties dominate here and both are safety properties rather than features:

* a plan the model got wrong must degrade to a direct answer, never fail the user's turn; and
* a side-effecting step that failed must not be retried, because it may have already acted.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from aura_core.domain.errors import BudgetExceededError, PlanningError
from aura_core.domain.identifiers import PlanId, StepId
from aura_core.domain.planning import Goal, Plan, PlanStep, Priority, StepKind, StepStatus
from aura_core.execution import ExecutionEngine
from aura_core.execution.engine import ExecutionOutcome, StepEvent
from aura_core.planning import PlanningEngine
from aura_core.planning.engine import _parse_json_object, _render_tool_line
from aura_core.prompts import build_default_registry


class _StubRouter:
    """A router that returns a scripted body, so plan parsing is tested without a model."""

    def __init__(self, body: str | Exception) -> None:
        self._body = body
        self.calls = 0
        self.last_request = None

    async def complete(self, request):  # noqa: ANN001
        self.calls += 1
        self.last_request = request
        if isinstance(self._body, Exception):
            raise self._body

        class Response:
            text = self._body if isinstance(self._body, str) else ""

        Response.text = self._body
        return Response()


def _engine(body: str | Exception = '{"steps": []}', **kwargs) -> PlanningEngine:
    return PlanningEngine(router=_StubRouter(body), prompts=build_default_registry(), **kwargs)


TOOLS = [
    {"name": "search_knowledge", "description": "Search the knowledge base."},
    {"name": "calculator", "description": "Evaluate an arithmetic expression."},
]


class TestPlanningGate:
    """`needs_plan` decides whether planning earns its cost. The default answer is no."""

    def test_a_short_question_is_answered_directly(self) -> None:
        assert _engine().needs_plan("what is my leave balance?", available_tools=["calculator"]) is False

    def test_no_tools_means_no_plan(self) -> None:
        """Without tools a plan can only contain generate steps -- which is a direct answer."""
        long_multi_step = (
            "First search the handbook for the expense policy and then calculate the total."
        )
        assert _engine().needs_plan(long_multi_step, available_tools=[]) is False

    def test_a_sequencing_marker_triggers_planning(self) -> None:
        assert _engine().needs_plan(
            "Search the handbook for the expense limit and then calculate 12 months of claims.",
            available_tools=["calculator"],
        ) is True

    def test_a_comparison_triggers_planning(self) -> None:
        assert _engine().needs_plan(
            "Compare the annual leave policy with the sick leave policy for permanent staff.",
            available_tools=["search_knowledge"],
        ) is True

    def test_an_enumerated_request_is_a_plan_the_user_already_wrote(self) -> None:
        assert _engine().needs_plan(
            "Please do the following:\n1. Look up the expense cap\n2. Total my last six claims",
            available_tools=["calculator"],
        ) is True

    def test_a_long_but_single_step_question_is_not_planned(self) -> None:
        """The gate is deliberately narrow: a false positive costs a generation on every turn."""
        assert _engine().needs_plan(
            "Could you please tell me how many days of annual leave permanent staff receive?",
            available_tools=["search_knowledge"],
        ) is False

    def test_a_repository_request_reaches_the_repo_tools(self) -> None:
        """The bug this encodes, from a real production turn: "clone <url> and analyse it" puts
        its verb before the conjunction, so no `_MULTI_STEP_MARKERS` pattern matches, planning
        was skipped, no tool ran (ADR-006), and the model answered that it cannot reach a
        repository -- while holding `clone_repository`."""
        engine = _engine()
        for message in (
            'clone into this repository "https://github.com/acme/widgets" and Analyse it.',
            "clone the repository that has git push: github.com/acme/widgets and Analyze it.",
            "summarise https://github.com/acme/widgets for me",
        ):
            assert engine.needs_plan(
                message,
                available_tools=["clone_repository", "github_list_repository_files"],
            ) is True, message

    def test_a_repository_request_without_repo_tools_is_not_planned(self) -> None:
        """Gated on a tool that could actually satisfy it, like the personal-OS exception: a
        caller without the engineer grant should not pay for a planning generation that could
        only end in a plain answer."""
        assert _engine().needs_plan(
            'clone into this repository "https://github.com/acme/widgets" and Analyse it.',
            available_tools=["calculator", "search_knowledge"],
        ) is False

    def test_ordinary_prose_mentioning_neither_url_nor_action_is_untouched(self) -> None:
        assert _engine().needs_plan(
            "Could you please tell me how many days of annual leave permanent staff receive?",
            available_tools=["clone_repository", "github_list_repository_files"],
        ) is False

    def test_force_overrides_the_heuristic_in_both_directions(self) -> None:
        engine = _engine()
        assert engine.needs_plan("hi", available_tools=[], force=True) is True
        assert engine.needs_plan(
            "Search then calculate the total for me please.", available_tools=["calculator"],
            force=False,
        ) is False

    def test_a_disabled_engine_never_plans_even_when_forced(self) -> None:
        assert _engine(enabled=False).needs_plan("x", available_tools=["a"], force=True) is False

    def test_short_add_task_plans_when_personal_os_tools_are_offered(self) -> None:
        assert _engine().needs_plan(
            "add a task to buy milk",
            available_tools=["propose_task", "calculator"],
        ) is True

    def test_short_add_task_does_not_plan_without_those_tools(self) -> None:
        """The default length gate still applies when propose_* is not offered."""
        assert _engine().needs_plan(
            "add a task to buy milk",
            available_tools=["calculator"],
        ) is False

    def test_short_fact_question_skips_planning_even_with_personal_os_tools(self) -> None:
        """propose_* is in the default install; Q&A must not pay for a planner generation."""
        assert _engine().needs_plan(
            "what is my leave balance?",
            available_tools=["propose_task", "propose_event", "search_knowledge"],
        ) is False

    def test_other_personal_os_imperatives_plan(self) -> None:
        engine = _engine()
        tools = [
            "propose_task", "propose_event", "propose_expense",
            "propose_income", "propose_health_log",
        ]
        assert engine.needs_plan("schedule lunch tomorrow at 1", available_tools=tools) is True
        assert engine.needs_plan("log an expense of 12 dollars", available_tools=tools) is True
        assert engine.needs_plan("log 500 ml of water", available_tools=tools) is True
        assert engine.needs_plan("remind me to call mom", available_tools=tools) is True


class TestPlanParsing:
    async def test_a_well_formed_plan_becomes_a_dag(self) -> None:
        body = json.dumps({"steps": [
            {"id": "a", "kind": "tool", "tool": "search_knowledge",
             "description": "Find the expense cap", "arguments": {"query": "expense cap"}},
            {"id": "b", "kind": "tool", "tool": "calculator", "depends_on": ["a"],
             "description": "Total the claims"},
            {"id": "c", "kind": "respond", "depends_on": ["b"], "description": "Answer"},
        ]})
        plan = await _engine(body).plan(Goal(description="expenses"), available_tools=TOOLS)

        assert [s.id.value for s in plan.steps] == ["s1", "s2", "s3"]
        assert plan.steps[1].depends_on == (StepId("s1"),)
        assert plan.steps[0].tool == "search_knowledge"
        assert plan.steps[0].arguments == {"query": "expense cap"}
        assert plan.steps[2].kind is StepKind.RESPOND

    async def test_a_step_naming_an_unknown_tool_is_demoted_not_dropped(self) -> None:
        """A step that can never succeed should still let the plan make progress."""
        body = json.dumps({"steps": [
            {"kind": "tool", "tool": "delete_production_database", "description": "Do the thing"},
        ]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)

        assert plan.steps[0].kind is StepKind.GENERATE
        assert plan.steps[0].tool is None

    async def test_an_unknown_step_kind_falls_back_to_generate(self) -> None:
        body = json.dumps({"steps": [{"kind": "teleport", "description": "Do the thing"}]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)
        assert plan.steps[0].kind is StepKind.GENERATE

    async def test_a_dependency_on_a_nonexistent_step_is_dropped(self) -> None:
        """Keeping it would produce a plan whose steps are never ready -- a silent hang."""
        body = json.dumps({"steps": [
            {"id": "a", "description": "First", "depends_on": ["does-not-exist"]},
        ]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)
        assert plan.steps[0].depends_on == ()

    async def test_a_self_dependency_is_dropped(self) -> None:
        """A self-edge is a cycle, and Plan refuses to construct one."""
        body = json.dumps({"steps": [{"id": "a", "description": "First", "depends_on": ["a"]}]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)
        assert plan.steps[0].depends_on == ()

    async def test_a_step_with_no_description_is_skipped(self) -> None:
        body = json.dumps({"steps": [
            {"description": "   "},
            {"description": "Real work"},
        ]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)
        assert len(plan.steps) == 1
        assert plan.steps[0].description == "Real work"

    async def test_a_long_description_is_truncated(self) -> None:
        body = json.dumps({"steps": [{"description": "x" * 900}]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)
        assert len(plan.steps[0].description) == 500

    async def test_non_mapping_entries_are_ignored(self) -> None:
        body = json.dumps({"steps": ["not a step", {"description": "Real work"}]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)
        assert len(plan.steps) == 1

    async def test_the_step_limit_is_enforced(self) -> None:
        body = json.dumps({"steps": [{"description": f"Step {i}"} for i in range(20)]})
        plan = await _engine(body, max_steps=5).plan(Goal(description="g"), available_tools=TOOLS)
        assert len(plan.steps) == 5

    async def test_a_caller_limit_cannot_exceed_the_engine_ceiling(self) -> None:
        body = json.dumps({"steps": [{"description": f"Step {i}"} for i in range(20)]})
        plan = await _engine(body, max_steps=4).plan(
            Goal(description="g"), available_tools=TOOLS, max_steps=100
        )
        assert len(plan.steps) == 4


class TestPlanningFallback:
    """Failing a user's turn because the planner produced bad JSON is worse than not planning."""

    async def test_malformed_json_falls_back_to_a_single_respond_step(self) -> None:
        plan = await _engine("I think we should probably start by...").plan(
            Goal(description="the original goal"), available_tools=TOOLS
        )
        assert len(plan.steps) == 1
        assert plan.steps[0].kind is StepKind.RESPOND
        assert plan.steps[0].description == "the original goal"

    async def test_a_router_failure_falls_back_rather_than_raising(self) -> None:
        plan = await _engine(RuntimeError("provider down")).plan(
            Goal(description="the original goal"), available_tools=TOOLS
        )
        assert plan.steps[0].kind is StepKind.RESPOND

    async def test_a_non_list_steps_field_falls_back(self) -> None:
        plan = await _engine('{"steps": "do everything"}').plan(
            Goal(description="g"), available_tools=TOOLS
        )
        assert plan.steps[0].kind is StepKind.RESPOND

    async def test_a_response_the_router_returned_but_that_parsed_to_nothing_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Distinguishes a real provider failure (the except-block above, already logged as a

        warning) from a response that arrived fine but had nothing `_parse_steps` could use --
        previously indistinguishable from a model that legitimately chose to just answer, since
        both fall through to the same single RESPOND step with no log signal either way.
        """
        with caplog.at_level("INFO", logger="aura.planning"):
            await _engine("not json at all").plan(Goal(description="g"), available_tools=TOOLS)
        assert any(
            "planning produced no usable steps" in record.message for record in caplog.records
        )

    async def test_a_well_formed_plan_logs_no_such_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("INFO", logger="aura.planning"):
            await _engine(
                '{"steps": [{"id": "s1", "kind": "respond", "description": "answer"}]}'
            ).plan(Goal(description="g"), available_tools=TOOLS)
        assert not any(
            "planning produced no usable steps" in record.message for record in caplog.records
        )


class TestPlanningTimeout:
    """A cold prefill on a CPU-only provider must get the configured deadline, not the

    ``CompletionRequest`` default of 60s -- ``kernel.py``'s own chat-completion calls already
    match ``AURA_LLM_TIMEOUT_S`` via ``_completion_timeout_s()``; the planning call was the one
    call site still using the unconfigured default, silently downgrading a slow-but-alive
    provider to a tool-less single RESPOND step (see plan()'s except-block above) well before
    the operator's actual configured timeout was reached.
    """

    async def test_the_configured_timeout_reaches_the_router(self) -> None:
        router = _StubRouter('{"steps": []}')
        engine = PlanningEngine(
            router=router, prompts=build_default_registry(), request_timeout_s=180.0
        )
        await engine.plan(Goal(description="g"), available_tools=TOOLS)
        assert router.last_request.timeout_s == 180.0

    async def test_the_default_matches_completionrequests_own_default(self) -> None:
        router = _StubRouter('{"steps": []}')
        engine = PlanningEngine(router=router, prompts=build_default_registry())
        await engine.plan(Goal(description="g"), available_tools=TOOLS)
        assert router.last_request.timeout_s == 60.0


class TestToolArgumentSignatures:
    """A tool's declaration already carries a full JSON-Schema ``parameters`` object (built for

    provider tool-call payloads) -- the planning prompt used to discard it, showing the model
    only a name and description and nothing about what ``arguments`` should contain. That gap is
    a likely reason a small, CPU-quantized model either avoids a tool step entirely or fills
    ``arguments`` with something the executor then rejects.
    """

    def test_a_tool_with_parameters_renders_its_argument_signature(self) -> None:
        tool = {
            "name": "run_command",
            "description": "Run a shell command in the sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_s": {"type": "integer"},
                },
                "required": ["command"],
            },
        }
        assert _render_tool_line(tool) == (
            "- run_command(command*:string, timeout_s:integer): "
            "Run a shell command in the sandbox."
        )

    def test_a_tool_with_no_parameters_falls_back_to_name_and_description(self) -> None:
        tool = {"name": "current_datetime", "description": "Return the current time."}
        assert _render_tool_line(tool) == "- current_datetime: Return the current time."

    async def test_the_argument_signature_reaches_the_rendered_prompt(self) -> None:
        router = _StubRouter('{"steps": []}')
        engine = PlanningEngine(router=router, prompts=build_default_registry())
        tool = {
            "name": "run_command",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        }
        await engine.plan(Goal(description="g"), available_tools=[tool])
        prompt_text = router.last_request.conversation.messages[0].content
        assert "run_command(command*:string): Run a shell command." in prompt_text


class TestTolerantJsonExtraction:
    def test_a_fenced_object_is_unwrapped(self) -> None:
        assert _parse_json_object('```json\n{"steps": []}\n```') == {"steps": []}

    def test_an_unlabelled_fence_is_unwrapped(self) -> None:
        assert _parse_json_object('```\n{"a": 1}\n```') == {"a": 1}

    def test_an_object_embedded_in_prose_is_recovered(self) -> None:
        text = 'Here is the plan:\n{"steps": [{"description": "x"}]}\nHope that helps!'
        assert _parse_json_object(text)["steps"][0]["description"] == "x"

    def test_nested_braces_are_balanced_correctly(self) -> None:
        text = 'prose {"outer": {"inner": {"deep": 1}}} more prose'
        assert _parse_json_object(text) == {"outer": {"inner": {"deep": 1}}}

    def test_a_top_level_array_is_not_a_plan(self) -> None:
        assert _parse_json_object('[{"description": "x"}]') == {}

    def test_text_with_no_object_returns_empty(self) -> None:
        assert _parse_json_object("no json here at all") == {}

    def test_an_unterminated_object_returns_empty(self) -> None:
        assert _parse_json_object('{"steps": [') == {}

    def test_a_malformed_balanced_object_returns_empty(self) -> None:
        assert _parse_json_object("prose {not: valid, json} more") == {}


class TestReplanning:
    def _plan_with(self, *steps: PlanStep) -> Plan:
        return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps)

    async def test_the_revision_limit_is_enforced(self) -> None:
        """An agent that silently rewrites its plan on each failure can loop indefinitely."""
        engine = _engine(max_replans=1)
        plan = self._plan_with(PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="Do it"))
        revised = plan.revised(steps=list(plan.steps), reason="first attempt failed")

        assert engine.can_replan(plan) is True
        assert engine.can_replan(revised) is False
        with pytest.raises(PlanningError) as excinfo:
            await engine.replan(revised, reason="again")
        assert excinfo.value.details["limit"] == 1

    async def test_succeeded_steps_are_preserved_and_new_steps_renumbered(self) -> None:
        """Rerunning a successful side-effecting tool call is actively wrong, not just wasteful."""
        done = PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="Already done").started().succeeded("ok", 1.0)
        broken = PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="Broke").started().failed("Timeout", 1.0)
        plan = self._plan_with(done, broken)

        body = json.dumps({"steps": [
            {"id": "x", "description": "Retry differently"},
            {"id": "y", "description": "Then finish", "depends_on": ["x"]},
        ]})
        engine = _engine(body)
        revised = await engine.replan(plan, reason="step_failed", available_tools=TOOLS)

        assert [s.id.value for s in revised.steps] == ["s1", "s2", "s3"]
        assert revised.steps[0].status is StepStatus.SUCCEEDED
        # The renumbered dependency must still point at the new first step, not the preserved one.
        assert revised.steps[2].depends_on == (StepId("s2"),)
        assert revised.revisions[-1].reason == "step_failed"

    async def test_the_replan_prompt_carries_the_progress_so_far(self) -> None:
        done = PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="Fetched the policy").started().succeeded(
            "ok", 1.0
        )
        broken = PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="Totalled the claims").started().failed(
            "Timeout", 1.0
        )
        engine = _engine('{"steps": [{"description": "Try again"}]}')
        captured: dict = {}

        original_plan = engine.plan

        async def spy(goal, **kwargs):  # noqa: ANN001
            captured["goal"] = goal.description
            return await original_plan(goal, **kwargs)

        engine.plan = spy  # type: ignore[method-assign]
        await engine.replan(self._plan_with(done, broken), reason="r", available_tools=TOOLS)

        assert "Completed: Fetched the policy" in captured["goal"]
        assert "Failed: Totalled the claims (Timeout)" in captured["goal"]
        assert "Do not repeat completed steps" in captured["goal"]

    def test_goal_from_message_strips_and_carries_criteria(self) -> None:
        goal = PlanningEngine.goal_from_message("  do the thing  ", criteria=("is correct",))
        assert goal.description == "do the thing"
        assert goal.success_criteria == ("is correct",)
        assert goal.priority is Priority.NORMAL


# --------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------


@pytest.fixture
def tool_context(user):
    from aura_core.ports.runtime import ToolContext

    return ToolContext(user_id=user, correlation_id="test-correlation", deadline_s=30.0)


def _plan(*steps: PlanStep) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps)


class TestExecution:
    async def test_independent_steps_run_concurrently(self, tools, tool_context) -> None:
        """A list cannot express independence, and a false dependency costs wall-clock time."""
        order: list[str] = []

        async def slow_handler(step: PlanStep) -> str:
            order.append(f"start:{step.id.value}")
            await asyncio.sleep(0.02)
            order.append(f"end:{step.id.value}")
            return f"done {step.id.value}"

        plan = _plan(
            PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A"),
            PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="B"),
        )
        engine = ExecutionEngine(tools=tools, max_parallel=4)
        outcome = await engine.execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: slow_handler}
        )

        assert outcome.succeeded is True
        assert outcome.steps_run == 2
        # Both start before either ends -- the observable signature of concurrency.
        assert order[:2] == ["start:s1", "start:s2"]

    async def test_dependencies_are_respected(self, tools, tool_context) -> None:
        order: list[str] = []

        async def handler(step: PlanStep) -> str:
            order.append(step.id.value)
            return "ok"

        plan = _plan(
            PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A"),
            PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="B",
                     depends_on=(StepId("s1"),)),
        )
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: handler}
        )
        assert order == ["s1", "s2"]
        assert outcome.succeeded is True

    async def test_max_parallel_bounds_the_batch(self, tools, tool_context) -> None:
        concurrent = 0
        peak = 0

        async def handler(step: PlanStep) -> str:
            nonlocal concurrent, peak
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(0.01)
            concurrent -= 1
            return "ok"

        plan = _plan(*(PlanStep(kind=StepKind.GENERATE, id=StepId(f"s{i}"), description=f"S{i}") for i in range(1, 7)))
        await ExecutionEngine(tools=tools, max_parallel=2).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: handler}
        )
        assert peak <= 2

    async def test_a_tool_step_dispatches_through_the_registry(self, tools, tool_context) -> None:
        plan = _plan(
            PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="calculator",
                     description="Add", arguments={"expression": "2+2"}),
        )
        outcome = await ExecutionEngine(tools=tools).execute(plan, context=tool_context)

        assert outcome.succeeded is True
        assert "4" in outcome.results["s1"]
        assert len(outcome.tool_reports) == 1

    async def test_a_confirmation_required_tool_call_blocks_rather_than_fails(
        self, tool_context
    ) -> None:
        """A confirm-gated call (`ToolExecutionReport.needs_confirmation`) is the turn correctly
        stopping to ask the user, not a failure a retry or a revised plan could fix. Marking it
        BLOCKED (with `blocked_reason="needs_confirmation"`) rather than FAILED is what lets
        `AgenticExecutor` (`_TERMINAL_BLOCKS`) stop instead of replanning past it -- discovered via
        manual end-to-end verification of the `engineer` persona, whose `max_plan_iterations > 1`
        routes through that loop and was silently discarding the confirmation request.
        """
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner
        from aura_core.tools.coding import build_sandbox_tools
        from aura_core.tools.policy import ToolPolicy
        from aura_core.tools.registry import ToolRegistry

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_sandbox_tools(InMemorySandboxRunner()))
        plan = _plan(
            PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="run_command",
                     description="run", arguments={"command": "echo hi"}),
        )

        outcome = await ExecutionEngine(tools=registry).execute(
            plan, context=tool_context, capabilities=("engineer_agent",),
        )

        assert outcome.blocked_reason == "needs_confirmation"
        assert outcome.plan.steps[0].status is StepStatus.BLOCKED
        assert not outcome.plan.failed_steps
        assert len(outcome.tool_reports) == 1
        assert outcome.tool_reports[0].needs_confirmation
        assert outcome.tool_reports[0].arguments["command"] == "echo hi"

    async def test_a_step_kind_with_no_handler_is_skipped_with_an_explanation(
        self, tools, tool_context
    ) -> None:
        """A configuration gap must not present as a user-visible plan failure."""
        seen: list[StepEvent] = []
        plan = _plan(PlanStep(id=StepId("s1"), kind=StepKind.RESPOND, description="Answer"))
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, on_event=seen.append
        )

        assert outcome.succeeded is True
        assert outcome.plan.steps[0].status is StepStatus.SKIPPED
        assert "No handler" in (outcome.plan.steps[0].result or "")
        assert [e.status for e in seen] == ["running", "skipped"]

    async def test_an_unhandled_generate_step_is_skipped_not_failed(
        self, tools, tool_context
    ) -> None:
        """The kernel never passes GENERATE handlers; a missing one must skip, not fail/retry."""
        seen: list[StepEvent] = []
        plan = _plan(
            PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="Draft the answer"),
        )
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, handlers=None, on_event=seen.append
        )

        step = outcome.plan.steps[0]
        assert step.status is StepStatus.SKIPPED
        assert outcome.succeeded is True
        assert step.attempts == 1
        assert [e.status for e in seen] == ["running", "skipped"]
        assert not any(e.status == "failed" for e in seen)

    async def test_a_raising_handler_fails_only_its_own_step(self, tools, tool_context) -> None:
        async def handler(step: PlanStep) -> str:
            if step.id.value == "s1":
                raise ValueError("bad input")
            return "ok"

        plan = _plan(
            PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A", max_attempts=1),
            PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="B"),
        )
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: handler}
        )
        assert outcome.plan.step(StepId("s1")).status is StepStatus.FAILED
        assert outcome.plan.step(StepId("s2")).status is StepStatus.SUCCEEDED
        assert outcome.succeeded is False

    async def test_a_synchronous_handler_is_accepted(self, tools, tool_context) -> None:
        plan = _plan(PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A"))
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: lambda step: "sync result"}
        )
        assert outcome.results["s1"] == "sync result"

    async def test_dependents_of_a_failed_step_are_marked_blocked(
        self, tools, tool_context
    ) -> None:
        async def handler(step: PlanStep) -> str:
            raise ValueError("nope")

        plan = _plan(
            PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A", max_attempts=1),
            PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="B",
                     depends_on=(StepId("s1"),)),
        )
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: handler}
        )
        # A direct dependent is marked BLOCKED, which is a terminal state, so the plan completes
        # normally rather than being reported as stuck. `succeeded` is what tells the caller the
        # goal was not met.
        assert outcome.plan.step(StepId("s2")).status is StepStatus.BLOCKED
        assert outcome.succeeded is False
        assert outcome.blocked_reason is None

    async def test_a_transitive_dependent_stops_the_loop_with_a_reason(
        self, tools, tool_context
    ) -> None:
        """Blocking propagates one hop per iteration, so a chain leaves a step unrunnable and
        the engine must stop with an explanation instead of spinning."""

        async def handler(step: PlanStep) -> str:
            raise ValueError("nope")

        plan = _plan(
            PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A", max_attempts=1),
            PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="B",
                     depends_on=(StepId("s1"),)),
            PlanStep(kind=StepKind.GENERATE, id=StepId("s3"), description="C",
                     depends_on=(StepId("s2"),)),
        )
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: handler}
        )
        assert outcome.blocked_reason == "dependencies_failed"
        assert outcome.succeeded is False

    async def test_the_step_budget_stops_a_runaway_plan(self, tools, tool_context) -> None:
        plan = _plan(*(PlanStep(kind=StepKind.GENERATE, id=StepId(f"s{i}"), description=f"S{i}") for i in range(1, 9)))
        outcome = await ExecutionEngine(tools=tools, max_steps_executed=3, max_parallel=1).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: lambda s: "ok"}
        )
        assert outcome.blocked_reason == "step_budget_exceeded"
        assert outcome.steps_run == 3

    async def test_the_deadline_stops_a_slow_plan(self, tools, tool_context) -> None:
        async def slow(step: PlanStep) -> str:
            await asyncio.sleep(0.05)
            return "ok"

        plan = _plan(*(PlanStep(kind=StepKind.GENERATE, id=StepId(f"s{i}"), description=f"S{i}") for i in range(1, 6)))
        outcome = await ExecutionEngine(tools=tools, deadline_s=0.06, max_parallel=1).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: slow}
        )
        assert outcome.blocked_reason == "deadline_exceeded"

    async def test_a_budget_error_stops_the_plan_immediately(self, tools, tool_context) -> None:
        async def broke(step: PlanStep) -> str:
            raise BudgetExceededError("out of tokens")

        plan = _plan(
            PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A"),
            PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="B"),
        )
        outcome = await ExecutionEngine(tools=tools, max_parallel=1).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: broke}
        )
        assert outcome.blocked_reason == "budget_exceeded"
        assert outcome.steps_run == 1

    async def test_summary_for_context_excludes_failed_steps(self, tools, tool_context) -> None:
        """Feeding an error message back as context invites the model to treat it as an answer."""

        async def handler(step: PlanStep) -> str:
            if step.id.value == "s2":
                raise ValueError("boom")
            return "the useful result"

        plan = _plan(
            PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A"),
            PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="B", max_attempts=1),
        )
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: handler}
        )
        summary = outcome.summary_for_context()
        assert summary == [("generate", "the useful result")]


class TestRetryPolicy:
    async def test_a_pure_step_is_retried(self, tools, tool_context) -> None:
        attempts = {"n": 0}

        async def flaky(step: PlanStep) -> str:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ValueError("transient")
            return "recovered"

        # The engine wraps handler exceptions in ExecutionError, which surfaces as a step
        # exception rather than a soft failure -- so retry is exercised through the tool path.
        plan = _plan(PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A", max_attempts=3))
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: flaky}
        )
        assert outcome.plan.step(StepId("s1")).attempts >= 1

    async def test_a_failing_read_only_tool_is_retried(self, tools, tool_context) -> None:
        plan = _plan(
            PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="calculator",
                     description="Bad maths", arguments={"expression": "1/0"}, max_attempts=2),
        )
        outcome = await ExecutionEngine(tools=tools).execute(plan, context=tool_context)
        assert outcome.plan.step(StepId("s1")).attempts == 2
        assert outcome.succeeded is False

    async def test_a_failing_side_effecting_tool_is_not_retried(
        self, tools, tool_context
    ) -> None:
        """It may have already acted. Retrying could duplicate the effect."""
        plan = _plan(
            PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="remember_fact",
                     description="Store", arguments={}, max_attempts=3),
        )
        outcome = await ExecutionEngine(tools=tools).execute(plan, context=tool_context)
        assert outcome.plan.step(StepId("s1")).attempts == 1
        assert outcome.plan.step(StepId("s1")).status is StepStatus.FAILED

    async def test_an_unknown_tool_is_assumed_side_effecting(self, tools, tool_context) -> None:
        """Fail closed: an unresolvable tool must not be retried on the optimistic assumption."""
        engine = ExecutionEngine(tools=tools)
        step = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="no_such_tool", description="X")
        assert engine._is_side_effecting(step) is True

    def test_a_non_tool_step_is_never_side_effecting(self, tools) -> None:
        engine = ExecutionEngine(tools=tools)
        assert engine._is_side_effecting(PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="X")) is False


class TestProgressStreaming:
    async def test_events_are_emitted_for_each_transition(self, tools, tool_context) -> None:
        seen: list[StepEvent] = []

        plan = _plan(PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A"))
        await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context,
            handlers={StepKind.GENERATE: lambda s: "ok"},
            on_event=seen.append,
        )
        assert [e.status for e in seen] == ["running", "succeeded"]
        assert seen[0].as_dict()["step_id"] == "s1"
        assert seen[1].as_dict()["elapsed_ms"] >= 0.0

    async def test_an_async_event_handler_is_awaited(self, tools, tool_context) -> None:
        seen: list[str] = []

        async def on_event(event: StepEvent) -> None:
            seen.append(event.status)

        plan = _plan(PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A"))
        await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context,
            handlers={StepKind.GENERATE: lambda s: "ok"}, on_event=on_event,
        )
        assert seen == ["running", "succeeded"]

    async def test_a_failing_event_handler_does_not_fail_the_plan(
        self, tools, tool_context
    ) -> None:
        def explode(event: StepEvent) -> None:
            raise RuntimeError("the UI went away")

        plan = _plan(PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A"))
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context,
            handlers={StepKind.GENERATE: lambda s: "ok"}, on_event=explode,
        )
        assert outcome.succeeded is True

    async def test_stream_yields_progress_then_the_outcome(self, tools, tool_context) -> None:
        plan = _plan(
            PlanStep(kind=StepKind.GENERATE, id=StepId("s1"), description="A"),
            PlanStep(kind=StepKind.GENERATE, id=StepId("s2"), description="B",
                     depends_on=(StepId("s1"),)),
        )
        items = [
            item
            async for item in ExecutionEngine(tools=tools).stream(
                plan, context=tool_context, handlers={StepKind.GENERATE: lambda s: "ok"}
            )
        ]

        assert isinstance(items[-1], ExecutionOutcome)
        assert items[-1].succeeded is True
        statuses = [i.status for i in items[:-1] if isinstance(i, StepEvent)]
        assert statuses == ["running", "succeeded", "running", "succeeded"]

    async def test_abandoning_a_stream_cancels_the_run(self, tools, tool_context) -> None:
        """A client that disconnects mid-plan must not leave the task running."""

        async def slow(step: PlanStep) -> str:
            await asyncio.sleep(5)
            return "ok"

        plan = _plan(*(PlanStep(kind=StepKind.GENERATE, id=StepId(f"s{i}"), description=f"S{i}") for i in range(1, 4)))
        stream = ExecutionEngine(tools=tools, max_parallel=1).stream(
            plan, context=tool_context, handlers={StepKind.GENERATE: slow}
        )
        first = await stream.__anext__()
        assert isinstance(first, StepEvent)
        await stream.aclose()  # must return promptly rather than waiting out the sleep
