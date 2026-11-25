"""The self-reflection critic: a second look at the plan before it runs and at the outcome before it
is claimed (ADR-039; row 42).

The rules are deterministic, so each is tested by building the situation it describes. The loop
behaviour (a blocking finding buys one revision, fed back into the replan prompt; a code change that
nothing verified buys one corrective pass) is tested through the loop, and the whole thing once
through a real Engineer turn.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind, StepStatus
from aura_core.execution.agentic_loop import AgenticExecutor
from aura_core.execution.critic import (
    Critic,
    LLMCritic,
    critique_answer,
    critique_outcome,
    critique_plan,
    feedback_lines,
)
from aura_core.execution.engine import ExecutionOutcome
from aura_core.execution.runner_output import observe_plan
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import _command_result
from tests.unit._loop_harness import (
    BUGGY,
    CRITIC_MARKER,
    FIXED,
    PYTEST,
    TEST_MODULE,
    collect_stream,
    engineer_turn,
    frames_of,
    make_container,
    pytest_step,
    ready_engineer,
    script_model,
    tool_step,
    write_step,
)


class _Spec:
    def __init__(self, side_effecting: bool) -> None:
        self.side_effecting = side_effecting


SPECS = {
    "read_file": _Spec(False), "search_knowledge": _Spec(False), "run_command": _Spec(True),
    "edit_file": _Spec(True), "write_file": _Spec(True), "git_commit": _Spec(True),
    "propose_task": _Spec(True),
}
TOOLS = set(SPECS) | {"generate_and_run_tests"}


def spec_for(name: str):
    if name not in SPECS:
        raise KeyError(name)
    return SPECS[name]


def step(sid, tool, args=None, kind=StepKind.TOOL, deps=()) -> PlanStep:
    return PlanStep(id=StepId(sid), kind=kind, tool=tool if kind is StepKind.TOOL else None,
                    description=f"{tool or kind} {sid}", arguments=args or {},
                    depends_on=tuple(StepId(d) for d in deps))


def plan_of(*steps: PlanStep) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="fix calc.py"), steps=steps)


def codes(findings) -> list[str]:
    return [f.code for f in findings]


class TestPlanChecks:
    def test_a_step_naming_an_unknown_tool_is_an_error_and_blocks(self) -> None:
        findings = critique_plan(plan_of(step("s1", "read_files", {"path": "a.py"})),
                                 available_tools=TOOLS, spec_for=spec_for)
        unknown = [f for f in findings if f.code == "unknown_tool"]
        assert unknown and unknown[0].severity == "error" and unknown[0].blocking
        assert "read_files" in unknown[0].message and unknown[0].step_id == "s1"

    def test_a_known_tool_is_fine(self) -> None:
        findings = critique_plan(plan_of(step("s1", "read_file", {"path": "a.py"})),
                                 available_tools=TOOLS, spec_for=spec_for)
        assert "unknown_tool" not in codes(findings)

    def test_with_no_tool_list_the_unknown_tool_rule_stays_quiet(self) -> None:
        assert critique_plan(plan_of(step("s1", "anything")), available_tools=(), spec_for=spec_for) == []

    def test_editing_without_ever_reading_is_flagged_and_blocks_for_edit_file(self) -> None:
        findings = critique_plan(
            plan_of(step("s1", "edit_file", {"path": "a.txt", "find": "x", "replace": "y"})),
            available_tools=TOOLS, spec_for=spec_for)
        f = next(f for f in findings if f.code == "write_without_read")
        assert f.blocking and f.step_id == "s1"

    def test_a_read_earlier_in_the_plan_satisfies_it(self) -> None:
        plan = plan_of(step("s1", "read_file", {"path": "a.py"}),
                       step("s2", "edit_file", {"path": "a.txt", "find": "x", "replace": "y"}, deps=("s1",)))
        assert "write_without_read" not in codes(
            critique_plan(plan, available_tools=TOOLS, spec_for=spec_for))

    def test_a_run_command_before_counts_as_looking(self) -> None:
        plan = plan_of(step("s1", "run_command", {"command": "cat a.txt"}),
                       step("s2", "edit_file", {"path": "a.txt", "find": "x", "replace": "y"}))
        assert "write_without_read" not in codes(
            critique_plan(plan, available_tools=TOOLS, spec_for=spec_for))

    def test_creating_a_file_needs_no_read(self) -> None:
        plan = plan_of(step("s1", "write_file", {"path": "new.txt", "content": "hi"}))
        assert "write_without_read" not in codes(
            critique_plan(plan, available_tools=TOOLS, spec_for=spec_for))

    def test_other_side_effects_with_no_read_are_a_warning_that_does_not_block(self) -> None:
        findings = critique_plan(plan_of(step("s1", "propose_task", {"title": "x"})),
                                 available_tools=TOOLS, spec_for=spec_for)
        f = next(f for f in findings if f.code == "write_without_read")
        assert f.severity == "warn" and not f.blocking

    def test_changing_code_with_no_verification_step_is_flagged(self) -> None:
        plan = plan_of(step("s1", "write_file", {"path": "calc.py", "content": "x"}))
        f = next(f for f in critique_plan(plan, available_tools=TOOLS, spec_for=spec_for)
                 if f.code == "no_verification")
        assert f.blocking and "tests" in f.message

    @pytest.mark.parametrize("verifier", [
        step("s2", "run_command", {"command": "pytest -q"}),
        step("s2", "generate_and_run_tests", {"source_code": "x"}),
        step("s2", None, kind=StepKind.VERIFY),
    ])
    def test_a_later_verification_step_satisfies_it(self, verifier) -> None:
        plan = plan_of(step("s1", "write_file", {"path": "calc.py", "content": "x"}), verifier)
        assert "no_verification" not in codes(critique_plan(plan, available_tools=TOOLS, spec_for=spec_for))

    def test_a_verification_that_comes_before_the_change_does_not_count(self) -> None:
        plan = plan_of(step("s1", "run_command", {"command": "pytest"}),
                       step("s2", "write_file", {"path": "calc.py", "content": "x"}))
        assert "no_verification" in codes(critique_plan(plan, available_tools=TOOLS, spec_for=spec_for))

    def test_a_prose_edit_needs_no_tests(self) -> None:
        plan = plan_of(step("s1", "write_file", {"path": "README.md", "content": "x"}))
        assert "no_verification" not in codes(critique_plan(plan, available_tools=TOOLS, spec_for=spec_for))

    def test_a_multi_file_write_is_judged_by_its_paths(self) -> None:
        plan = plan_of(step("s1", "write_file", {"files": [{"path": "a.py", "content": "x"}]}))
        assert "no_verification" in codes(critique_plan(plan, available_tools=TOOLS, spec_for=spec_for))

    def test_steps_that_already_ran_are_not_re_criticised(self) -> None:
        done = step("s1", "edit_file", {"path": "a.py"}).started().succeeded("ok", 1.0)
        assert critique_plan(plan_of(done), available_tools=TOOLS, spec_for=spec_for) == []

    def test_a_clean_plan_has_no_findings(self) -> None:
        plan = plan_of(step("s1", "read_file", {"path": "calc.py"}),
                       step("s2", "edit_file", {"path": "calc.py", "find": "a", "replace": "b"}, deps=("s1",)),
                       step("s3", "run_command", {"command": "pytest -q"}, deps=("s2",)))
        assert critique_plan(plan, available_tools=TOOLS, spec_for=spec_for) == []


def done(sid, tool, args, text) -> PlanStep:
    return step(sid, tool, args).started().succeeded(text, 1.0)


class TestOutcomeChecks:
    def test_a_code_change_nobody_verified(self) -> None:
        plan = plan_of(done("s1", "write_file", {"path": "calc.py", "content": "x"}, "Wrote 1 bytes"))
        f = next(f for f in critique_outcome(plan, observe_plan(plan)) if f.code == "unverified_change")
        assert f.blocking

    def test_a_verified_change_is_clean(self) -> None:
        plan = plan_of(done("s1", "write_file", {"path": "calc.py", "content": "x"}, "Wrote"),
                       done("s2", "run_command", {"command": "pytest"}, _command_result(0, "1 passed in 0.1s", "")))
        assert critique_outcome(plan, observe_plan(plan)) == []

    def test_an_unresolved_test_failure_is_an_error_with_the_summary(self) -> None:
        plan = plan_of(done("s1", "run_command", {"command": "pytest"},
                            _command_result(1, "FAILED t.py::test_x\n1 failed in 0.1s", "")))
        f = next(f for f in critique_outcome(plan, observe_plan(plan)) if f.code == "unresolved_tests_failed")
        assert f.severity == "error" and not f.blocking and "test_x" in f.message

    def test_failed_steps_are_reported(self) -> None:
        failing = step("s1", "read_file").started().failed("tool_execution_error", 1.0)
        assert "failed_steps_unresolved" in codes(critique_outcome(plan_of(failing), []))


class TestAnswerChecks:
    def red_plan(self) -> Plan:
        return plan_of(done("s1", "run_command", {"command": "pytest tests/test_calc.py"},
                            _command_result(1, "FAILED tests/test_calc.py::test_add\n1 failed in 0.1s", "")))

    def test_a_success_claim_over_a_red_run_is_an_error(self) -> None:
        plan = self.red_plan()
        findings = critique_answer("I fixed it and all tests pass now.", plan,
                                   observations=observe_plan(plan))
        f = next(f for f in findings if f.code == "success_claim_unsupported")
        assert f.severity == "error" and "last test run failed" in f.message

    def test_a_success_claim_over_a_failed_step_is_an_error(self) -> None:
        plan = plan_of(step("s1", "read_file").started().failed("tool_execution_error", 1.0))
        assert "success_claim_unsupported" in codes(critique_answer("Successfully read the file.", plan))

    def test_an_honest_answer_over_a_red_run_is_left_alone(self) -> None:
        plan = self.red_plan()
        findings = critique_answer("The test run failed: test_add is still red.", plan,
                                   observations=observe_plan(plan))
        assert "success_claim_unsupported" not in codes(findings)

    def test_a_success_claim_over_a_green_run_is_fine(self) -> None:
        plan = plan_of(done("s1", "run_command", {"command": "pytest"},
                            _command_result(0, "3 passed in 0.1s", "")))
        assert critique_answer("All tests pass.", plan, observations=observe_plan(plan)) == []

    def test_a_file_no_tool_mentioned_is_unsupported(self) -> None:
        plan = self.red_plan()
        findings = critique_answer("I edited src/engine.py and checked test_phantom.", plan,
                                   observations=observe_plan(plan))
        f = next(f for f in findings if f.code == "unsupported_reference")
        assert "src/engine.py" in f.message and "test_phantom" in f.message and f.severity == "info"

    def test_a_reference_found_in_a_result_an_argument_or_the_request_is_supported(self) -> None:
        plan = plan_of(done("s1", "write_file", {"path": "pkg/mod.py", "content": "x"}, "Wrote"),
                       done("s2", "run_command", {"command": "pytest"},
                            _command_result(0, "tests/test_calc.py .. 2 passed in 0.1s", "")))
        findings = critique_answer(
            "I changed pkg/mod.py, ran tests/test_calc.py and looked at notes.md.", plan,
            user_message="please also read notes.md", observations=observe_plan(plan))
        assert findings == []

    def test_a_bare_basename_matches_a_path_in_evidence(self) -> None:
        plan = plan_of(done("s1", "read_file", {"path": "src/deep/calc.py"}, "def add(): ..."))
        assert critique_answer("calc.py defines add.", plan) == []

    def test_no_plan_no_findings(self) -> None:
        assert critique_answer("anything.py works", None) == []


class TestTheLLMCritic:
    class Router:
        def __init__(self, text=None, error=None) -> None:
            self.text, self.error, self.prompts = text, error, []

        async def complete(self, request):
            self.prompts.append(request.conversation.last_user_message.content)
            if self.error:
                raise self.error

            class R:
                pass
            R.text = self.text
            return R()

    async def test_findings_are_advisory_and_never_block(self) -> None:
        router = self.Router(json.dumps({"findings": [{"code": "Missing Rollback!", "message": "No rollback step."}]}))
        findings = await LLMCritic(router=router).review("plan", "ship it", ["s1 [tool] run_command deploy"])
        assert [f.source for f in findings] == ["llm"] and not findings[0].blocking
        assert findings[0].code == "missingrollback" and CRITIC_MARKER in router.prompts[0]

    @pytest.mark.parametrize("reply", ["", "not json", '{"findings": "x"}', '{"findings": [{"code": "a"}]}'])
    async def test_unusable_replies_yield_nothing(self, reply) -> None:
        assert await LLMCritic(router=self.Router(reply)).review("plan", "g", []) == []

    async def test_a_failing_provider_yields_nothing(self) -> None:
        assert await LLMCritic(router=self.Router(error=RuntimeError("down"))).review("plan", "g", []) == []

    async def test_the_aggregate_adds_the_model_findings_to_the_rules(self) -> None:
        router = self.Router(json.dumps({"findings": [{"code": "x", "message": "m"}]}))
        critic = Critic(llm=LLMCritic(router=router))
        plan = plan_of(step("s1", "write_file", {"path": "a.py", "content": "x"}))
        found = await critic.plan_findings(plan, available_tools=TOOLS, spec_for=spec_for)
        assert {"rules", "llm"} == {f.source for f in found}

    async def test_a_disabled_critic_says_nothing_and_never_calls_the_model(self) -> None:
        router = self.Router("{}")
        critic = Critic(enabled=False, llm=LLMCritic(router=router))
        plan = plan_of(step("s1", "write_file", {"path": "a.py", "content": "x"}))
        assert await critic.plan_findings(plan, available_tools=TOOLS, spec_for=spec_for) == []
        assert router.prompts == []


# -- in the loop ---------------------------------------------------------------------------------


class _Planner:
    def __init__(self, revision: list[PlanStep] | None = None) -> None:
        self.revision, self.calls = revision, []

    def can_replan(self, plan, *, limit=None) -> bool:
        return len(plan.revisions) < (limit if limit is not None else 2)

    async def replan(self, plan, *, reason, available_tools=(), limit=None, feedback=()):
        self.calls.append({"reason": reason, "feedback": list(feedback)})
        kept = [s for s in plan.steps if s.status is StepStatus.SUCCEEDED]
        return plan.revised(steps=[*kept, *(self.revision or [])], reason=reason)


class _Engine:
    """Every pending step succeeds, with the text ``outputs[tool]`` (default ``ok``)."""

    def __init__(self, outputs=None) -> None:
        self.outputs, self.executed = outputs or {}, []

    def spec_for(self, tool):
        return SPECS.get(tool)

    async def execute(self, plan, *, context, capabilities=(), confirmations=None, on_event=None):
        steps = []
        for s in plan.steps:
            if s.status is StepStatus.PENDING:
                self.executed.append(s.id.value)
                s = s.started().succeeded(self.outputs.get(s.tool, "ok"), 1.0)
            steps.append(s)
        new = plan.with_steps(steps)
        return ExecutionOutcome(plan=new, results={s.id.value: s.result for s in steps if s.result},
                                succeeded=new.succeeded)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)


TOOL_DECLS = [{"name": n} for n in sorted(TOOLS)]


async def run_loop(plan, planner, engine, ctx, *, critic=Critic(), max_iterations=4):
    loop = AgenticExecutor(planner=planner, executor=engine, critic=critic)
    return await loop.run(plan, context=ctx, max_iterations=max_iterations, deadline_s=60.0,
                          available_tools=TOOL_DECLS)


class TestTheLoopActsOnIt:
    async def test_a_blocking_plan_finding_buys_one_revision_before_anything_runs(self, ctx) -> None:
        bad = plan_of(step("s1", "write_file", {"path": "calc.py", "content": "x"}))
        good = [step("s2", "write_file", {"path": "calc.py", "content": "x"}),
                step("s3", "run_command", {"command": "pytest"}, deps=("s2",))]
        planner, engine = _Planner(good), _Engine({"run_command": _command_result(0, "1 passed in 0.1s", "")})
        outcome = await run_loop(bad, planner, engine, ctx)
        assert len(planner.calls) == 1 and planner.calls[0]["reason"] == "critic_review"
        assert any("no_verification" in line for line in planner.calls[0]["feedback"])
        assert engine.executed == ["s2", "s3"], "the unreviewed plan never ran"
        assert outcome.loop.trace[:4] == ["plan", "replan", "execute", "observe"]
        assert outcome.succeeded

    async def test_only_one_such_revision_per_run(self, ctx) -> None:
        bad = plan_of(step("s1", "write_file", {"path": "calc.py", "content": "x"}))
        planner = _Planner([step("s2", "write_file", {"path": "calc.py", "content": "y"})])  # still bad
        await run_loop(bad, planner, _Engine(), ctx)
        assert [c["reason"] for c in planner.calls].count("critic_review") == 1

    async def test_findings_that_do_not_block_do_not_cost_a_revision(self, ctx) -> None:
        plan = plan_of(step("s1", "propose_task", {"title": "x"}))
        planner = _Planner()
        outcome = await run_loop(plan, planner, _Engine(), ctx)
        assert planner.calls == []
        assert [f["code"] for f in outcome.loop.findings] == ["write_without_read"]

    async def test_no_revision_without_budget_to_replan(self, ctx) -> None:
        bad = plan_of(step("s1", "write_file", {"path": "calc.py", "content": "x"}))
        planner = _Planner()
        outcome = await run_loop(bad, planner, _Engine(), ctx, max_iterations=0)
        assert planner.calls == [] and outcome.loop.findings == [], "a single-pass persona has no critic"

    async def test_a_code_change_nothing_verified_buys_one_corrective_pass(self, ctx) -> None:
        """The plan critic is off here so the unverified change reaches the outcome check."""
        plan = plan_of(step("s1", "edit_file", {"path": "calc.py", "find": "a", "replace": "b"}))
        planner = _Planner([step("s2", "run_command", {"command": "pytest -q"})])
        engine = _Engine({"run_command": _command_result(0, "1 passed in 0.1s", "")})

        class OutcomeOnly(Critic):
            async def plan_findings(self, *a, **k):
                return []

        outcome = await run_loop(plan, planner, engine, ctx, critic=OutcomeOnly())
        assert [c["reason"] for c in planner.calls] == ["critic_corrective (unverified_change)"]
        assert any("unverified_change" in line for line in planner.calls[0]["feedback"])
        assert engine.executed == ["s1", "s2"] and outcome.succeeded
        assert outcome.loop.trace == ["plan", "execute", "observe", "verify", "replan", "execute",
                                      "observe", "verify", "done"]

    async def test_the_corrective_pass_happens_once_at_most(self, ctx) -> None:
        plan = plan_of(step("s1", "edit_file", {"path": "calc.py", "find": "a", "replace": "b"}))
        planner = _Planner([step("s2", "edit_file", {"path": "calc.py", "find": "b", "replace": "c"})])

        class OutcomeOnly(Critic):
            async def plan_findings(self, *a, **k):
                return []

        outcome = await run_loop(plan, planner, _Engine(), ctx, critic=OutcomeOnly())
        assert len(planner.calls) == 1 and outcome.loop.stop_reason == "completed"

    async def test_a_loop_without_a_critic_behaves_as_before(self, ctx) -> None:
        bad = plan_of(step("s1", "write_file", {"path": "calc.py", "content": "x"}))
        planner = _Planner()
        loop = AgenticExecutor(planner=planner, executor=_Engine())
        outcome = await loop.run(bad, context=ctx, max_iterations=4, deadline_s=60.0,
                                 available_tools=TOOL_DECLS)
        assert planner.calls == [] and outcome.loop.findings == []

    async def test_the_stream_carries_a_critic_frame(self, ctx) -> None:
        bad = plan_of(step("s1", "read_files", {"path": "a.py"}))
        planner = _Planner([step("s2", "read_file", {"path": "a.py"})])
        loop = AgenticExecutor(planner=planner, executor=_Engine(), critic=Critic())
        items = [i async for i in loop.stream(bad, context=ctx, max_iterations=4, deadline_s=60.0,
                                              available_tools=TOOL_DECLS)]
        frames = [i.as_frame() for i in items if hasattr(i, "as_frame")]
        critic_frames = [f for f in frames if f["type"] == "critic"]
        assert critic_frames[0]["stage"] == "plan"
        assert critic_frames[0]["findings"][0]["code"] == "unknown_tool"


class TestEndToEnd:
    """A real Engineer turn: the model plans a code change with no test step."""

    EDIT_ONLY = [write_step("s1", "calc.py", FIXED)]
    WITH_TESTS = [write_step("s1", "calc.py", FIXED), write_step("s2", "test_calc.py", TEST_MODULE),
                  pytest_step("s3", ["s1", "s2"])]

    async def _turn(self, monkeypatch, plans, *, final="Done.", **env):
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000", **env)
        echo = await ready_engineer(container)
        script = script_model(echo, plans, final_text=final)
        frames = await collect_stream(container, engineer_turn())
        return script, frames

    async def test_the_critic_sends_the_plan_back_once_and_the_revision_is_what_runs(self, monkeypatch) -> None:
        script, frames = await self._turn(monkeypatch, [self.EDIT_ONLY, self.WITH_TESTS])
        assert script.planning_calls == 2
        assert "A review of the previous plan found problems" in script.plan_prompts[1]
        assert "no_verification" in script.plan_prompts[1]
        critic = frames_of(frames, "critic")[0]
        assert critic["stage"] == "plan" and critic["findings"][0]["code"] == "no_verification"
        done = frames_of(frames, "done")[0]
        assert done["agent_loop"]["trace"][:3] == ["plan", "replan", "execute"]
        ran = [s["id"] for s in done["plan_summary"] if s["status"] == "succeeded"]
        assert "s3" in ran, "the revised plan, tests included, is what executed"

    async def test_switched_off_the_unreviewed_plan_runs(self, monkeypatch) -> None:
        script, frames = await self._turn(monkeypatch, [self.EDIT_ONLY, self.WITH_TESTS],
                                          CRITIC_ENABLED="false")
        assert script.planning_calls == 1 and not frames_of(frames, "critic")

    async def test_the_answer_is_checked_against_the_tool_results(self, monkeypatch) -> None:
        red = [write_step("s1", "calc.py", BUGGY), write_step("s2", "test_calc.py", TEST_MODULE),
               pytest_step("s3", ["s1", "s2"])]
        _, frames = await self._turn(
            monkeypatch, [red], final="I fixed calc.py and all the tests pass.",
        )
        text = "".join(f["text"] for f in frames_of(frames, "token"))
        assert text.startswith("I fixed calc.py and all the tests pass.")
        assert "Caution (checked against the tool results)" in text
        answer_frames = [f for f in frames_of(frames, "critic") if f["stage"] == "answer"]
        assert answer_frames and answer_frames[0]["findings"][0]["code"] == "success_claim_unsupported"

    async def test_an_honest_green_answer_gets_no_caveat(self, monkeypatch) -> None:
        _, frames = await self._turn(
            monkeypatch, [self.WITH_TESTS], final="Fixed calc.py; test_calc.py passes (2 passed).",
        )
        text = "".join(f["text"] for f in frames_of(frames, "token"))
        assert "Caution" not in text and "Unverified" not in text

    async def test_a_phantom_file_in_the_answer_is_called_out(self, monkeypatch) -> None:
        _, frames = await self._turn(
            monkeypatch, [self.WITH_TESTS], final="Fixed calc.py and updated docs/phantom_guide.md.",
        )
        text = "".join(f["text"] for f in frames_of(frames, "token"))
        assert "Unverified (checked against the tool results)" in text and "docs/phantom_guide.md" in text

    async def test_the_non_streaming_path_carries_the_caveat_too(self, monkeypatch) -> None:
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000")
        echo = await ready_engineer(container)
        red = [write_step("s1", "calc.py", BUGGY), write_step("s2", "test_calc.py", TEST_MODULE),
               pytest_step("s3", ["s1", "s2"])]
        script_model(echo, [red], final_text="All tests pass.")
        result = await container.kernel.run(engineer_turn())
        assert "Caution (checked against the tool results)" in result.text
        assert result.agent_loop["findings"], "the outcome critic's findings travel on the result"

    async def test_the_llm_critic_is_off_unless_asked_for(self, monkeypatch) -> None:
        script, _ = await self._turn(monkeypatch, [self.WITH_TESTS], critic_json='{"findings": []}')
        assert script.critic_prompts == [], "no model call was made on the critic's behalf"

    async def test_the_llm_critic_adds_advisory_findings_when_enabled(self, monkeypatch) -> None:
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000", CRITIC_LLM_ENABLED="true")
        echo = await ready_engineer(container)
        script = script_model(
            echo, [self.WITH_TESTS], final_text="Done.",
            critic_json=json.dumps({"findings": [{"code": "no_rollback", "message": "No rollback."}]}))
        frames = await collect_stream(container, engineer_turn())
        codes_seen = {f["code"] for fr in frames_of(frames, "critic") for f in fr["findings"]}
        assert "no_rollback" in codes_seen
        assert len(script.critic_prompts) >= 2, "once for the plan, once for the outcome"
