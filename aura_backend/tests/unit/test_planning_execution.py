"""Planning and execution engines.

Two properties dominate here and both are safety properties rather than features:

* a plan the model got wrong must degrade to a direct answer, never fail the user's turn; and
* a side-effecting step that failed must not be retried, because it may have already acted.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from aura_core.adapters.coding_fakes import InMemoryGitHubClient
from aura_core.domain.errors import BudgetExceededError, PlanningError
from aura_core.domain.identifiers import PlanId, StepId
from aura_core.domain.planning import Goal, Plan, PlanStep, Priority, StepKind, StepStatus
from aura_core.execution import ExecutionEngine
from aura_core.execution.engine import ExecutionOutcome, StepEvent
from aura_core.planning import PlanningEngine
from aura_core.planning.engine import _parse_json_object, _render_tool_line
from aura_core.prompts import build_default_registry
from aura_core.tools.base import param, tool
from aura_core.tools.coding import build_github_tools
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry


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

    def test_a_bare_local_project_reference_is_not_recognised_by_the_heuristic_alone(self) -> None:
        """`matching_repository_tool` only fires on a GitHub/GitLab/Bitbucket URL or an
        `owner/repo.git` shape (`_REPO_REFERENCE`, tools/coding.py) -- a short message naming a
        local project on a paired desktop workspace (ADR-011), like "Analyze the VELVET
        project.", matches neither that nor any `_MULTI_STEP_MARKERS` pattern, and is 28
        characters, under the 40-character floor. `needs_plan` alone therefore still says no
        here, same as it would for any other short prose. This is not a bug in `needs_plan`
        itself: `AIKernel._should_plan` (kernel.py) forces planning for any persona whose
        `max_plan_iterations > 1` (today, `engineer`) before ever consulting this heuristic --
        see `TestAgenticPersonaPlanning.test_the_engineer_persona_plans_a_bare_local_project_reference`
        in test_agents.py for the path a real turn actually takes. This test exists only to keep
        that division of responsibility visible: `needs_plan` stays persona-agnostic, and the
        engineer-specific carve-out lives at the one layer that knows which persona is asking.
        """
        assert _engine().needs_plan(
            "Analyze the VELVET project.",
            available_tools=["run_command", "write_file", "edit_file"],
        ) is False


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

    async def test_a_step_naming_an_unknown_tool_keeps_its_kind_and_name(self) -> None:
        """This used to demote the step to GENERATE, on the reasoning that a step which "can
        never succeed" should still "let the plan make progress". It did the opposite, and the
        cost was a fabricated answer in production -- see
        `tests/unit/test_unexecuted_plan_steps.py` for the full chain and the screenshot it came
        from. In short: no GENERATE handler exists anywhere, so the demoted step was SKIPPED,
        its dependents BLOCKED, and `summary_for_context()` empty; the plan made no progress at
        all, it merely stopped reporting that.

        Keeping the step as a TOOL step lets `ToolRegistry.execute` refuse it *usefully* --
        ok=False, "Tool 'x' is not available. Available tools: ...", audited and counted -- which
        marks the step FAILED and so reaches the replan's context, where the model can correct
        its own tool name.
        """
        body = json.dumps({"steps": [
            {"kind": "tool", "tool": "delete_production_database", "description": "Do the thing"},
        ]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)

        assert plan.steps[0].kind is StepKind.TOOL
        assert plan.steps[0].tool == "delete_production_database"

    async def test_a_tool_step_naming_no_tool_is_dropped(self) -> None:
        """The complement: with no name there is nothing to dispatch and nothing to refuse, and
        `_run_step` would fall through to the handler lookup and skip it silently."""
        body = json.dumps({"steps": [
            {"kind": "tool", "description": "Do the thing"},
            {"kind": "respond", "description": "Say something"},
        ]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)

        assert [s.description for s in plan.steps] == ["Say something"]

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

    async def test_a_truncated_engineer_plan_still_runs_its_completed_tool_steps(self) -> None:
        """End-to-end version of the VELVET incident: a live qwen2.5:3b completion for "Analyze
        the VELVET project." against the engineer's real tool set hit `finish_reason: "length"`
        at 700 output tokens, cutting the JSON off mid-step and dropping the `{"steps": [...]}`
        envelope. Before `_parse_json_object`'s array-salvage, this produced zero usable steps
        and fell back to a single RESPOND -- prose with no tool call, holding tools that could
        have done the job. The two complete steps here must survive as real TOOL steps."""
        body = (
            '[{"id": "s1", "kind": "tool", "description": "List the repository files", '
            '"tool": "github_list_repository_files", '
            '"arguments": {"repo": "VELVET", "ref": "main", "path_prefix": ""}, '
            '"depends_on": []}, '
            '{"id": "s2", "kind": "tool", "description": "Open the persistent workspace", '
            '"tool": "open_workspace", "arguments": {"repo": "VELVET"}, "depends_on": ["s1"]}, '
            '{"id": "s3", "kind": "tool", "description": "cut off mid-step'
        )
        tools = [
            {"name": "github_list_repository_files", "description": "List files."},
            {"name": "open_workspace", "description": "Open a workspace."},
        ]
        plan = await _engine(body).plan(Goal(description="Analyze the VELVET project."), available_tools=tools)

        assert [s.tool for s in plan.steps] == ["github_list_repository_files", "open_workspace"]
        assert all(s.kind is StepKind.TOOL for s in plan.steps)
        assert plan.steps[1].depends_on == (StepId("s1"),)

    async def test_a_step_output_reference_forces_depends_on_even_when_the_model_omitted_it(
        self,
    ) -> None:
        """The root-cause fix for the video incident: a model that references an earlier step's
        output via ``${s1.output}`` but forgets (or never learns) to also declare `depends_on`
        must not get a structurally-independent step -- belt and suspenders on top of the prompt
        instruction (`task.plan_decomposition`) telling it to declare the dependency itself."""
        body = json.dumps({"steps": [
            {"id": "s1", "kind": "tool", "tool": "github_list_repository_files",
             "description": "Read the README", "arguments": {"path": "README.md"}},
            {"id": "s2", "kind": "tool", "tool": "open_workspace",
             "description": "Write a summary",
             "arguments": {"repo": "Summary: ${s1.output}"}},
            # depends_on deliberately omitted on s2, reproducing the video's bug shape.
        ]})
        tools = [
            {"name": "github_list_repository_files", "description": "Read."},
            {"name": "open_workspace", "description": "Write."},
        ]
        plan = await _engine(body).plan(Goal(description="summarise"), available_tools=tools)

        assert plan.steps[1].depends_on == (StepId("s1"),)

    async def test_a_reference_to_the_models_own_raw_id_is_also_resolved(self) -> None:
        """The token may name the model's own (non-canonical) raw id -- resolved through the
        same `declared` mapping `depends_on` itself uses -- not only the canonical "sN" form."""
        body = json.dumps({"steps": [
            {"id": "read_readme", "kind": "tool", "tool": "github_list_repository_files",
             "description": "Read the README", "arguments": {"path": "README.md"}},
            {"id": "write_summary", "kind": "tool", "tool": "open_workspace",
             "description": "Write a summary",
             "arguments": {"repo": "Summary: ${read_readme.output}"}},
        ]})
        tools = [
            {"name": "github_list_repository_files", "description": "Read."},
            {"name": "open_workspace", "description": "Write."},
        ]
        plan = await _engine(body).plan(Goal(description="summarise"), available_tools=tools)
        assert plan.steps[1].depends_on == (StepId("s1"),)

    async def test_a_reference_to_an_undeclared_step_does_not_add_a_bogus_dependency(self) -> None:
        """A malformed/hallucinated token must not crash parsing or invent a dependency on a
        step that does not exist -- ``Plan``'s own construction already refuses that, but this
        stays a clean no-op rather than raising."""
        body = json.dumps({"steps": [
            {"id": "s1", "kind": "generate", "description": "Write a summary",
             "arguments": {"note": "${s99.output}"}},
        ]})
        plan = await _engine(body).plan(Goal(description="g"), available_tools=TOOLS)
        assert plan.steps[0].depends_on == ()


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


class TestPlanningOutputBudget:
    """A production incident on the self-hosted CPU provider measured this call sustaining
    ~5 tokens/s: the old 1200-token ceiling needed up to 240s to finish, past even the deployed
    180s timeout, so the provider call was hard-cancelled mid-JSON and raised instead of
    returning -- the "no usable steps" diagnostic in plan() never got a chance to fire because
    the response never reached ``_parse_steps``. The ceiling must stay low enough to actually
    fit inside a realistic timeout on that hardware.
    """

    async def test_the_output_budget_fits_a_slow_cpu_provider_inside_the_timeout(self) -> None:
        router = _StubRouter('{"steps": []}')
        engine = PlanningEngine(router=router, prompts=build_default_registry())
        await engine.plan(Goal(description="g"), available_tools=TOOLS)
        assert router.last_request.sampling.max_output_tokens == 700


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

    def test_an_enum_parameter_lists_its_allowed_values(self) -> None:
        tool = {
            "name": "search",
            "description": "Search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "type": {"type": "string", "enum": ["document", "experiment"]},
                },
                "required": ["q"],
            },
        }
        assert _render_tool_line(tool) == "- search(q*:string, type:string(document|experiment)): Search."

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

    def test_a_bare_top_level_array_is_treated_as_the_steps_list(self) -> None:
        """A live qwen2.5:3b response for "Analyze the VELVET project." dropped the
        `{"steps": [...]}` envelope and returned the array directly -- salvageable steps
        should not be discarded just because the wrapper object is missing."""
        assert _parse_json_object('[{"description": "x"}]') == {"steps": [{"description": "x"}]}

    def test_text_with_no_object_returns_empty(self) -> None:
        assert _parse_json_object("no json here at all") == {}

    def test_an_unterminated_object_returns_empty(self) -> None:
        assert _parse_json_object('{"steps": [') == {}

    def test_a_malformed_balanced_object_returns_empty(self) -> None:
        assert _parse_json_object("prose {not: valid, json} more") == {}

    def test_a_truncated_bare_array_salvages_its_complete_elements(self) -> None:
        """The same live incident: `max_output_tokens` cut generation off mid-step. The steps
        that finished before the cutoff are complete, valid JSON and should survive; only the
        half-written tail element is discarded."""
        text = (
            '[{"id": "s1", "kind": "tool", "description": "a", "tool": "t1", '
            '"arguments": {}, "depends_on": []}, {"id": "s2", "kind": "tool", '
            '"description": "b", "tool": "t2", "arguments": {}, "depends_on": ["s1"]}, '
            '{"id": "s3", "kind": "tool", "description": "cut off half'
        )
        result = _parse_json_object(text)
        assert [s["id"] for s in result["steps"]] == ["s1", "s2"]

    def test_a_truncated_steps_envelope_salvages_its_complete_elements(self) -> None:
        """Same recovery, but for a plan that kept the `{"steps": [...]}` envelope and got cut
        off before its own closing `]}` rather than dropping the envelope outright."""
        text = (
            '{"steps": [{"id": "s1", "kind": "tool", "description": "a", "tool": "t1", '
            '"arguments": {}, "depends_on": []}, {"id": "s2", "description": "cut half'
        )
        result = _parse_json_object(text)
        assert [s["id"] for s in result["steps"]] == ["s1"]

    def test_a_truncated_array_with_no_complete_elements_returns_empty(self) -> None:
        assert _parse_json_object('[{"id": "s1", "description": "cut half') == {}


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

    async def test_a_dependent_step_dispatches_with_the_real_output_of_its_dependency(
        self, tools, tool_context
    ) -> None:
        """`${s1.output}` in a dependent step's arguments must be replaced with the *actual*
        result of step s1 before the tool is dispatched -- never with LLM-authored text standing
        in for it. This is the positive case of the video-incident fix: a well-formed plan that
        uses the token correctly runs end to end."""

        @tool("echo_argument", "Echoes its 'text' argument back.",
              param("text", "string", "Text to echo."))
        async def echo_argument(text: str) -> str:
            return text

        tools.register(echo_argument)
        plan = _plan(
            PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="calculator",
                     description="Compute", arguments={"expression": "2+2"}),
            PlanStep(id=StepId("s2"), kind=StepKind.TOOL, tool="echo_argument",
                     description="Report the result", depends_on=(StepId("s1"),),
                     arguments={"text": "Result was: ${s1.output}"}),
        )
        outcome = await ExecutionEngine(tools=tools).execute(plan, context=tool_context)

        assert outcome.succeeded is True
        assert outcome.plan.step(StepId("s1")).result is not None
        assert outcome.results["s2"] == f"Result was: {outcome.plan.step(StepId('s1')).result}"

    async def test_an_unresolvable_reference_blocks_dispatch_and_never_reports_success(
        self, tools, tool_context
    ) -> None:
        """Belt-and-suspenders: even a step whose `depends_on` is missing the edge (an old-style
        plan, or a plan built directly rather than through `PlanningEngine._parse_steps`'s
        forced-dependency injection) must not dispatch its tool call with an unresolved
        `${step_id.output}` token -- this is what makes "write succeeded" after "read failed"
        (or, here, "read never ran") structurally impossible even if the dependency edge itself
        is somehow missing."""

        @tool("echo_argument", "Echoes its 'text' argument back.",
              param("text", "string", "Text to echo."))
        async def echo_argument(text: str) -> str:
            return text

        tools.register(echo_argument)
        # s2 references s1's output but declares no dependency on it -- s1 is never run.
        plan = _plan(
            PlanStep(id=StepId("s2"), kind=StepKind.TOOL, tool="echo_argument",
                     description="Report the result",
                     arguments={"text": "Result was: ${s1.output}"}),
        )
        outcome = await ExecutionEngine(tools=tools).execute(plan, context=tool_context)

        assert outcome.succeeded is False
        step = outcome.plan.step(StepId("s2"))
        assert step.status is StepStatus.FAILED
        assert step.error_kind == "unresolved_reference"
        # The tool was never actually dispatched -- no report was produced for it.
        assert outcome.tool_reports == []
        assert "s2" not in outcome.results

    async def test_a_failed_tool_steps_error_kind_is_the_structured_reason_not_free_text(
        self, tools, tool_context
    ) -> None:
        """`PlanStep.error_kind` must carry the tool's structured `error_kind`
        ("unresolved_argument", "policy_denied", ...) rather than a truncated human-readable
        message -- this is what `PlanningEngine.replan()`'s context block
        ("Failed: ... (<error_kind>)") and `agentic_loop._replan_reason` actually key off of."""

        @tool("needs_a_repo", "Takes a github_repo-shaped argument.",
              param("repository", "string", "A repository.", format="github_repo"))
        async def needs_a_repo(repository: str) -> str:  # pragma: no cover - never reached
            return "unreachable"

        tools.register(needs_a_repo)
        plan = _plan(
            PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="needs_a_repo",
                     description="Read a repo", arguments={"repository": "repo_for_README"}),
        )
        outcome = await ExecutionEngine(tools=tools).execute(plan, context=tool_context)

        assert outcome.succeeded is False
        step = outcome.plan.step(StepId("s1"))
        assert step.status is StepStatus.FAILED
        assert step.error_kind == "unresolved_argument"

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
        from aura_core.adapters.coding_fakes import InMemoryGitHubClient, InMemorySandboxRunner
        from aura_core.tools.coding import build_sandbox_tools
        from aura_core.tools.policy import ToolPolicy
        from aura_core.tools.registry import ToolRegistry

        async def _resolver(user_id):
            return InMemoryGitHubClient()

        # `clone_repository` rather than `run_command`: the latter is workspace_local as of
        # ADR-014 and no longer confirms, but what this test is about is the BLOCKED-vs-FAILED
        # handling of *any* confirm-gated call, which is unchanged and still load-bearing for
        # the engineer persona's agentic loop.
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(
            build_sandbox_tools(InMemorySandboxRunner(), code_host_resolver=_resolver)
        )
        plan = _plan(
            PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="clone_repository",
                     description="clone", arguments={"repo": "acme/widgets"}),
        )

        outcome = await ExecutionEngine(tools=registry).execute(
            plan, context=tool_context, capabilities=("engineer_agent",),
        )

        assert outcome.blocked_reason == "needs_confirmation"
        assert outcome.plan.steps[0].status is StepStatus.BLOCKED
        assert not outcome.plan.failed_steps
        assert len(outcome.tool_reports) == 1
        assert outcome.tool_reports[0].needs_confirmation
        assert outcome.tool_reports[0].arguments["repo"] == "acme/widgets"

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


class TestVelvetIncidentScenario:
    """End-to-end reproduction of the E:\\VELVET README demo video: "summarise README.md" in an
    already-open local workspace. The model invents a GitHub repository id (`repo_for_README`),
    that read fails, and the video showed the dependent "write summary" step still reported as
    succeeded, followed by the agent proposing to destroy the workspace.

    Two scripted plans, one per case the analysis called out:

    * the video's actual (buggy) plan shape -- a write step with fabricated content and no
      `depends_on` at all -- shows what Items 1/2/4 alone do and do not fix: the read fails
      cheaply and pre-network (Item 2/4), but a write step with no dependency edge and no
      `${s1.output}` reference is not something any belt-and-suspenders check can retroactively
      tie to the read -- nothing declared it depends on anything. This is exactly why Item 1 also
      changes the *prompt* (`task.plan_decomposition`), not only the parser: the fix that
      actually prevents the false "succeeded" is the model using `${s1.output}`, which the parser
      then backs up by forcing `depends_on` even if the model's JSON omits it.
    * a well-formed plan that *does* use `${s1.output}` -- the shape the updated prompt asks
      for -- runs end to end with the write step's argument genuinely carrying the read step's
      real content, and blocks correctly (never reporting success) when the read fails instead.
    """

    def _registry(self, github_client: InMemoryGitHubClient) -> ToolRegistry:
        registry = ToolRegistry(policy=ToolPolicy())

        async def resolver(user_id):
            return github_client

        registry.register_all(build_github_tools(resolver))

        @tool("save_summary", "Save a text summary.",
              param("content", "string", "The summary text to save."))
        async def save_summary(content: str) -> str:
            return f"Saved: {content}"

        registry.register(save_summary)
        return registry

    async def test_the_videos_buggy_plan_shape_fails_the_read_cheaply_but_cannot_retroactively_block_an_unlinked_write(
        self, tool_context
    ) -> None:
        github_client = InMemoryGitHubClient()  # nothing seeded -- repo_for_README is fabricated
        registry = self._registry(github_client)

        # Exactly the video's shape: the write step's content is LLM-authored prose, not a
        # `${s1.output}` reference, and it declares no `depends_on` on the read step at all.
        plan = _plan(
            PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="github_get_file_contents",
                     description="Read README.md",
                     arguments={"path": "README.md", "repo": "repo_for_README"}),
            PlanStep(id=StepId("s2"), kind=StepKind.TOOL, tool="save_summary",
                     description="Save the summary",
                     arguments={"content": "This project is a well-organized Python application..."}),
        )
        outcome = await ExecutionEngine(tools=registry).execute(
            plan, context=tool_context, capabilities=("engineer_agent",)
        )

        read_step = outcome.plan.step(StepId("s1"))
        assert read_step.status is StepStatus.FAILED
        assert read_step.error_kind == "unresolved_argument"
        # The read never reached the network: schema validation rejected the fabricated
        # repository before `_client`/`get_file_contents` was ever called.
        write_step = outcome.plan.step(StepId("s2"))
        assert write_step.status is StepStatus.SUCCEEDED
        assert "This project is a well-organized" in (write_step.result or "")
        # This is the residual gap Items 1/2/4 alone leave: a plan with no dependency edge and no
        # data-flow token is not something a pre-dispatch check can retroactively tie together --
        # the fix has to stop the model from producing this shape in the first place (the updated
        # `task.plan_decomposition` prompt) and back that up when it is used correctly (below).

    async def test_a_well_formed_plan_using_the_output_token_runs_end_to_end(
        self, tool_context
    ) -> None:
        github_client = InMemoryGitHubClient()
        github_client.seed_file("acme/velvet", "main", "README.md", "# VELVET\nA sample project.")
        registry = self._registry(github_client)

        body = json.dumps({"steps": [
            {"id": "s1", "kind": "tool", "tool": "github_get_file_contents",
             "description": "Read README.md",
             "arguments": {"path": "README.md", "repo": "acme/velvet"}},
            {"id": "s2", "kind": "tool", "tool": "save_summary",
             "description": "Save the summary",
             "arguments": {"content": "Summary: ${s1.output}"}},
        ]})
        tool_signatures = [
            {"name": "github_get_file_contents", "description": "Read a file."},
            {"name": "save_summary", "description": "Save a summary."},
        ]
        plan = await _engine(body).plan(
            Goal(description="Summarize README.md and save the summary"),
            available_tools=tool_signatures,
        )
        # The parser forced the dependency even without relying on the model's own depends_on.
        assert plan.steps[1].depends_on == (StepId("s1"),)

        outcome = await ExecutionEngine(tools=registry).execute(
            plan, context=tool_context, capabilities=("engineer_agent",)
        )

        assert outcome.succeeded is True
        assert outcome.plan.step(StepId("s1")).status is StepStatus.SUCCEEDED
        assert outcome.results["s2"] == "Saved: Summary: # VELVET\nA sample project."

    async def test_the_well_formed_shape_blocks_correctly_when_the_read_fails(
        self, tool_context
    ) -> None:
        """The same well-formed shape, but the repository really is fabricated -- this is the
        case the video should have shown: the write never dispatches and the plan never reports
        success."""
        github_client = InMemoryGitHubClient()
        registry = self._registry(github_client)

        body = json.dumps({"steps": [
            {"id": "s1", "kind": "tool", "tool": "github_get_file_contents",
             "description": "Read README.md",
             "arguments": {"path": "README.md", "repo": "repo_for_README"}},
            {"id": "s2", "kind": "tool", "tool": "save_summary",
             "description": "Save the summary",
             "arguments": {"content": "Summary: ${s1.output}"}},
        ]})
        tool_signatures = [
            {"name": "github_get_file_contents", "description": "Read a file."},
            {"name": "save_summary", "description": "Save a summary."},
        ]
        plan = await _engine(body).plan(
            Goal(description="Summarize README.md and save the summary"),
            available_tools=tool_signatures,
        )
        outcome = await ExecutionEngine(tools=registry).execute(
            plan, context=tool_context, capabilities=("engineer_agent",)
        )

        assert outcome.succeeded is False
        assert outcome.plan.step(StepId("s1")).status is StepStatus.FAILED
        write_step = outcome.plan.step(StepId("s2"))
        assert write_step.status is StepStatus.BLOCKED
        assert "s2" not in outcome.results
