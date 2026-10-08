"""A planned step that never ran must never be answered around.

Found live, with a screenshot. A user had a folder open in AURA Desktop, paired to the gateway,
and asked the Engineer persona to summarise its `README.md`. What came back was a summary of a
README -- project description, installation instructions, usage examples, contributing
guidelines, license -- and the file it described was not the one on screen. The model said so
itself, in the transcript, in the middle of the answer:

    The command has been executed, but since the output is not shown here, I will assume the
    file exists and proceed with summarizing based on typical content found in a README.md file.

Meanwhile the plan panel showed "Read the contents of README.md" with a tick beside it.

Four separate mechanisms had to line up to produce that, and each looked locally defensible:

1. The model's plan named a tool that is not registered (plausibly `read_file`; the paired
   session's file-reading tool is `run_command`). `_parse_steps` demoted the step to GENERATE
   on the stated grounds that this "lets the plan still make progress".
2. No GENERATE handler exists. Nothing anywhere passes `handlers` to
   `ExecutionEngine.execute`, so *every* non-TOOL step kind is unhandled, in every deployment.
   The step was SKIPPED, and the summarise step depending on it was BLOCKED.
3. `AgenticExecutor` asked whether anything was worth replanning. `failed_steps` was empty --
   nothing failed -- and `blocked_steps()` was empty too, because it reports only steps still
   PENDING and the executor had already moved the dependent to BLOCKED. So: "nothing_to_repair",
   no second attempt.
4. `summary_for_context()` reports SUCCEEDED steps only, so the kernel assembled a context in
   which the read was not mentioned at all -- not as failed, not as missing, not at all.

Step 1's comment is the one worth dwelling on, because it is the exact inverse of what the code
does: demoting the step did not let the plan make progress, it guaranteed the plan could make
none, while removing every signal that would have said so. And the deepest cause is step 4's
shape rather than any single bug: *an absence cannot be reasoned about*. A model handed no tool
output cannot tell "the tool returned nothing" from "no tool ran", and only in the second case
does inventing an answer look locally reasonable.

These tests pin each link, and the end-to-end shape they combine into.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain import ToolCall, ToolCallId
from aura_core.domain.identifiers import PlanId, StepId
from aura_core.domain.planning import Goal, Plan, PlanStep, Priority, StepKind, StepStatus
from aura_core.execution.agentic_loop import AgenticExecutor
from aura_core.execution.engine import ExecutionEngine
from aura_core.kernel.kernel import AIKernel
from aura_core.planning import PlanningEngine
from aura_core.ports.runtime import ToolContext
from aura_core.prompts import build_default_registry
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

REAL_TOOL = "run_command"
GUESSED_TOOL = "read_file"


class _StubRouter:
    """Returns a scripted plan body, so parsing is tested without a model."""

    def __init__(self, body: str) -> None:
        self._body = body

    async def complete(self, request):  # noqa: ANN001
        class Response:
            text = self._body

        Response.text = self._body
        return Response()


def _planner(body: str) -> PlanningEngine:
    return PlanningEngine(router=_StubRouter(body), prompts=build_default_registry())


class _NoTools:
    """A tool registry that would notice any dispatch. Nothing here should reach a tool."""

    def spec(self, name: str):  # noqa: ANN201
        raise KeyError(name)

    async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("no tool should have been dispatched")


@pytest.fixture
def context() -> ToolContext:
    return ToolContext(user_id="u", correlation_id="c", deadline_s=30.0)


def _step(step_id: str, kind: StepKind, description: str, *, depends_on=()) -> PlanStep:
    return PlanStep(
        id=StepId(step_id),
        kind=kind,
        description=description,
        priority=Priority.NORMAL,
        depends_on=tuple(StepId(d) for d in depends_on),
    )


def _plan(*steps: PlanStep) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="summarise README.md"), steps=steps)


# ---------------------------------------------------------------------------------------
# Link 1 -- the planner must not demote a step into a kind nothing can execute
# ---------------------------------------------------------------------------------------


class TestAnUnknownToolStaysAToolStep:
    async def test_the_step_keeps_its_kind_and_its_unknown_name(self) -> None:
        """So the registry can refuse it *informatively*, which is what drives a useful replan.

        `ToolRegistry.execute` answers an unregistered name with ok=False and "Tool 'x' is not
        available. Available tools: ...". That text reaches `_replan_reason` and the replan's
        context block, so a model that guessed `read_file` is told `run_command` exists and can
        repair its own plan. Demoting to GENERATE threw all of that away.
        """
        body = json.dumps({"steps": [
            {"id": "s1", "kind": "tool", "tool": GUESSED_TOOL,
             "arguments": {"path": "README.md"},
             "description": "Read the contents of README.md"},
        ]})
        plan = await _planner(body).plan(
            Goal(description="summarise README.md"),
            available_tools=[{"name": REAL_TOOL, "description": "Run a shell command."}],
        )

        assert plan.steps[0].kind is StepKind.TOOL, (
            "an unknown tool name was demoted to a kind that no handler can execute; the step "
            "will be skipped and its dependents blocked, which is the production defect"
        )
        assert plan.steps[0].tool == GUESSED_TOOL, (
            "the unknown name must survive to dispatch -- it is what the refusal message and "
            "the replan context are built from"
        )

    async def test_a_tool_step_that_names_no_tool_is_dropped(self) -> None:
        """The one case where there is genuinely nothing to dispatch and nothing to report.

        Keeping it would reintroduce the bug by another route: `_run_step` requires both
        `kind is TOOL` *and* a truthy `step.tool`, so a nameless TOOL step falls through to the
        handler lookup and is skipped exactly like the demoted step was.
        """
        body = json.dumps({"steps": [{"kind": "tool", "description": "Do the thing"}]})
        plan = await _planner(body).plan(
            Goal(description="g"), available_tools=[{"name": REAL_TOOL}]
        )

        assert all(
            not (s.kind is StepKind.TOOL and not s.tool) for s in plan.steps
        ), "a TOOL step with no tool name survived; it can only ever be skipped"

    async def test_a_known_tool_is_still_bound_normally(self) -> None:
        """Negative control. A change that made every step a TOOL step would pass the tests
        above and break the product, so the ordinary path is asserted too."""
        body = json.dumps({"steps": [
            {"id": "s1", "kind": "tool", "tool": REAL_TOOL,
             "arguments": {"command": "cat README.md"}, "description": "Read the README"},
        ]})
        plan = await _planner(body).plan(
            Goal(description="g"), available_tools=[{"name": REAL_TOOL}]
        )

        assert plan.steps[0].kind is StepKind.TOOL
        assert plan.steps[0].tool == REAL_TOOL
        assert plan.steps[0].arguments == {"command": "cat README.md"}


class TestTheRegistryRefusalIsUsable:
    """The behaviour the fix above depends on: refusing an unknown tool has to *say something*,
    or keeping the step as a TOOL step would be no better than demoting it."""

    async def test_an_unknown_tool_names_the_ones_that_exist(self, context) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name=GUESSED_TOOL, arguments={}), context
        )

        assert result.ok is False
        assert report.ok is False
        assert GUESSED_TOOL in result.content
        assert "not available" in result.content.lower()


# ---------------------------------------------------------------------------------------
# Link 2 + 3 -- a step that never ran has to be visible to the code deciding what to do next
# ---------------------------------------------------------------------------------------


class TestUnexecutedStepsAreVisible:
    async def test_the_skipped_step_and_its_blocked_dependent_are_both_reported(
        self, context
    ) -> None:
        """The exact shape of the production plan, and the accessor that was missing.

        Before `unexecuted_steps()`, this plan answered "nothing failed" and "nothing blocked"
        while containing a step that did nothing and a step that never ran.
        """
        plan = _plan(
            _step("s1", StepKind.GENERATE, "Read the contents of README.md"),
            _step("s2", StepKind.RESPOND, "Summarize the README.md content", depends_on=["s1"]),
        )
        outcome = await ExecutionEngine(tools=_NoTools()).execute(plan, context=context)

        assert outcome.plan.failed_steps == (), "nothing failed -- that was always the trap"
        assert outcome.plan.blocked_steps() == (), (
            "blocked_steps() is PENDING-only by design (it drives the PENDING->BLOCKED "
            "transition); if this ever becomes non-empty, mark_blocked_steps may not terminate"
        )

        unexecuted = {s.id.value: s.status for s in outcome.plan.unexecuted_steps()}
        assert unexecuted == {"s1": StepStatus.SKIPPED, "s2": StepStatus.BLOCKED}

    async def test_the_turn_reaches_generation_with_no_tool_output(self, context) -> None:
        """The condition under which the model fabricated: context contains nothing about the
        read, because `summary_for_context()` reports only SUCCEEDED steps."""
        plan = _plan(
            _step("s1", StepKind.GENERATE, "Read the contents of README.md"),
            _step("s2", StepKind.RESPOND, "Summarize the README.md content", depends_on=["s1"]),
        )
        outcome = await ExecutionEngine(tools=_NoTools()).execute(plan, context=context)

        assert outcome.summary_for_context() == []
        assert outcome.tool_reports == []

    async def test_a_succeeded_step_is_not_reported_as_unexecuted(self, context) -> None:
        """Negative control: `unexecuted_steps()` must not simply mean "not succeeded"."""
        plan = _plan(_step("s1", StepKind.GENERATE, "Think about it"))
        outcome = await ExecutionEngine(tools=_NoTools()).execute(
            plan, context=context,
            handlers={StepKind.GENERATE: lambda step: "a real result"},
        )

        assert outcome.plan.unexecuted_steps() == ()
        assert outcome.summary_for_context() == [("generate", "a real result")]


class TestTheAgenticLoopReplansOnABlockedStep:
    """Link 3. `_stop_reason` concluded "nothing_to_repair" and returned without a second
    attempt, because both predicates it consulted were empty."""

    def test_a_blocked_step_is_repairable(self) -> None:
        blocked = _plan(
            _step("s1", StepKind.GENERATE, "Read the contents of README.md"),
            _step("s2", StepKind.RESPOND, "Summarize", depends_on=["s1"]),
        )
        executed = blocked.steps[0].skipped("no handler")
        dependent = blocked.steps[1].blocked("upstream_failed")
        plan = Plan(
            id=blocked.id, goal=blocked.goal, steps=(executed, dependent),
            max_steps=blocked.max_steps,
        )

        assert AgenticExecutor._repairable(plan) is True, (
            "a step that lost its input and never ran is exactly what a revised plan can fix"
        )

    def test_a_merely_skipped_plan_is_not_repairable(self) -> None:
        """Deliberate, and the reason `_repairable` is not simply `unexecuted_steps()`.

        Skipping means no handler exists for the kind, which replanning cannot change. Since no
        handler exists for *any* non-TOOL kind, treating a skip as repairable would replan
        almost every plan containing a RESPOND step -- a second planning generation on a
        provider sustaining 5-10 tokens/sec, to reach the same answer.
        """
        plan = _plan(_step("s1", StepKind.GENERATE, "Think about it"))
        skipped = Plan(
            id=plan.id, goal=plan.goal,
            steps=(plan.steps[0].skipped("no handler"),), max_steps=plan.max_steps,
        )

        assert AgenticExecutor._repairable(skipped) is False

    def test_a_fully_succeeded_plan_is_not_repairable(self) -> None:
        plan = _plan(_step("s1", StepKind.GENERATE, "Think about it"))
        done = Plan(
            id=plan.id, goal=plan.goal,
            steps=(plan.steps[0].succeeded("result", 1.0),), max_steps=plan.max_steps,
        )

        assert AgenticExecutor._repairable(done) is False


# ---------------------------------------------------------------------------------------
# Link 4 -- the backstop: generation must be told what produced nothing
# ---------------------------------------------------------------------------------------


class TestTheKernelStatesWhatDidNotRun:
    """Even with links 1-3 fixed, some plan somewhere will contain a step that produces
    nothing. The model must be told, because it cannot infer an absence."""

    @staticmethod
    async def _outcome(context: ToolContext):
        plan = _plan(
            _step("s1", StepKind.GENERATE, "Read the contents of README.md"),
            _step("s2", StepKind.RESPOND, "Summarize the README.md content", depends_on=["s1"]),
        )
        return await ExecutionEngine(tools=_NoTools()).execute(plan, context=context)

    async def test_the_note_names_every_step_that_produced_nothing(self, context) -> None:
        notes = AIKernel._execution_notes(await self._outcome(context))

        assert len(notes) == 1, "one note, or it competes with itself for context budget"
        note = notes[0]
        assert "Read the contents of README.md" in note
        assert "Summarize the README.md content" in note

    async def test_the_note_forbids_substituting_typical_content(self, context) -> None:
        """Aimed at the sentence the model actually wrote: "I will assume the file exists and
        proceed with summarizing based on typical content found in a README.md file"."""
        note = AIKernel._execution_notes(await self._outcome(context))[0]

        assert "have NOT seen" in note
        assert "typical" in note.lower()

    async def test_a_clean_run_adds_no_note(self, context) -> None:
        """Nothing to say costs nothing. A note on every turn would burn prompt budget that
        CLAUDE.md is explicit about (retrieved knowledge gets evicted first)."""
        plan = _plan(_step("s1", StepKind.GENERATE, "Think about it"))
        outcome = await ExecutionEngine(tools=_NoTools()).execute(
            plan, context=context,
            handlers={StepKind.GENERATE: lambda step: "a real result"},
        )

        assert AIKernel._execution_notes(outcome) == []

    async def test_a_failed_step_is_named_too(self, context) -> None:
        """A failure is also an absence of output, and the same fabrication applies."""
        plan = _plan(_step("s1", StepKind.GENERATE, "Read the contents of README.md"))

        def _boom(step):  # noqa: ANN001, ANN202
            raise RuntimeError("nope")

        outcome = await ExecutionEngine(tools=_NoTools()).execute(
            plan, context=context, handlers={StepKind.GENERATE: _boom},
        )

        notes = AIKernel._execution_notes(outcome)
        assert notes and "Read the contents of README.md" in notes[0]


class TestCompositionStepsAfterSuccessfulToolsAreLeftToTheAnswer:
    """Seen live: a search tool step succeeded, but the plan's "select one result" and "quote the
    id" steps have no executor. Saying the model "did NOT see" their results made it answer "I
    cannot provide an evidence_id" with the evidence in front of it."""

    @staticmethod
    def _outcome(tool_status: str):
        from types import SimpleNamespace

        search = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="search", description="Search for heptane").started()
        search = search.succeeded("results", 1.0) if tool_status == "ok" else search.failed("Timeout", 1.0)
        pick = _step("s2", StepKind.RETRIEVE, "Select one result", depends_on=["s1"]).skipped("no handler")
        quote = _step("s3", StepKind.GENERATE, "Quote the evidence_id", depends_on=["s2"]).blocked("dep")
        return SimpleNamespace(plan=_plan(search, pick, quote))

    def test_the_note_tells_the_model_to_do_the_remaining_steps_itself(self) -> None:
        notes = AIKernel._execution_notes(self._outcome("ok"))

        assert len(notes) == 1
        assert "Select one result" in notes[0] and "Quote the evidence_id" in notes[0]
        assert "do them yourself" in notes[0]
        assert "Do not call any tool" in notes[0]
        assert "exactly as they appear" in notes[0]
        assert "NOT seen" not in notes[0]

    def test_a_failed_tool_step_keeps_the_strict_warning(self) -> None:
        note = AIKernel._execution_notes(self._outcome("failed"))[0]

        assert "have NOT seen" in note
        assert "Search for heptane" in note

    def test_with_no_successful_tool_step_the_strict_warning_stays(self) -> None:
        from types import SimpleNamespace

        plan = _plan(_step("s1", StepKind.GENERATE, "Read README.md").skipped("no handler"))

        assert "have NOT seen" in AIKernel._execution_notes(SimpleNamespace(plan=plan))[0]


class TestTheNoteTravelsOnTheSystemChannel:
    """ADR-006. The note is the kernel's own account of what it ran, not content a tool
    returned, so fencing it as untrusted DATA alongside real tool output would both weaken it
    and misstate where it came from."""

    def test_the_kernel_passes_notes_as_scratchpad_not_tool_results(self) -> None:
        import inspect

        source = inspect.getsource(AIKernel._run_turn)
        assert "scratchpad=execution_notes" in source, (
            "execution notes must reach ContextRequest.scratchpad (TrustChannel.SYSTEM); "
            "tool_results renders on TrustChannel.DATA"
        )

    def test_the_context_engine_still_puts_scratchpad_on_the_system_channel(self) -> None:
        """Pins the property the choice above depends on, in the file that decides it."""
        import inspect

        from aura_core.context.engine import ContextEngine

        source = inspect.getsource(ContextEngine._scratchpad_items)
        assert "TrustChannel.SYSTEM" in source
