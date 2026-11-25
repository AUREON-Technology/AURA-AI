"""ADR-036 group B: the tools that run project code in a child of the gateway.

These tests are mostly about the *fences* -- capability, confirmation, taint, no paths, no secrets,
bounded concurrency -- because the group's honest risk statement is that there is no container
under it, so each fence has to be shown to hold rather than assumed.
"""

from __future__ import annotations

import json
import threading

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import ToolCallId
from aura_core.domain.messages import ToolCall
from aura_core.domain.tools import ToolSensitivity
from aura_core.ports.runtime import ToolContext
from aura_core.tools import open_exec
from aura_core.tools.builtin import default_tools
from aura_core.tools.open_exec import OPEN_EXEC_TOOL_NAMES, build_open_exec_tools
from aura_core.tools.registry import ToolRegistry

_TOOLS = {t.spec.name: t for t in build_open_exec_tools()}

SOURCE = "def is_on():\n    return True\n"
TESTS = "from module_under_test import is_on\n\n\ndef test_on():\n    assert is_on() is True\n"


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-1", deadline_s=120.0)


class TestFences:
    def test_the_group_is_exactly_these_two(self) -> None:
        assert OPEN_EXEC_TOOL_NAMES == {"generate_and_run_tests", "analyze_test_quality"}

    @pytest.mark.parametrize("name", sorted(OPEN_EXEC_TOOL_NAMES))
    def test_each_tool_is_capability_gated_confirmed_tainting_and_not_workspace_local(self, name) -> None:
        spec = _TOOLS[name].spec
        assert spec.required_capability == "engineer_agent"
        assert spec.side_effecting is True
        assert spec.sensitivity >= ToolSensitivity.CONFIDENTIAL
        assert spec.requires_confirmation is True  # forced by ToolSpec: side-effecting + CONFIDENTIAL
        assert "untrusted_content" in spec.tags
        assert spec.workspace_local is False

    @pytest.mark.parametrize("name", sorted(OPEN_EXEC_TOOL_NAMES))
    def test_no_parameter_is_a_path_or_a_command(self, name) -> None:
        """Inline text only: a caller cannot aim the runner at a file on the gateway."""
        names = {p.name for p in _TOOLS[name].spec.parameters}
        assert not {n for n in names if any(w in n for w in ("path", "file", "dir", "command", "cwd"))} - {
            "module_import_path"  # a dotted module *name*, validated as an identifier
        }

    def test_off_unless_the_operator_enables_it(self) -> None:
        assert not OPEN_EXEC_TOOL_NAMES & {t.spec.name for t in default_tools()}
        assert not OPEN_EXEC_TOOL_NAMES & {t.spec.name for t in default_tools(enable_analysis_tools=True)}
        assert OPEN_EXEC_TOOL_NAMES <= {t.spec.name for t in default_tools(enable_open_exec_tools=True)}

    async def test_the_registry_denies_without_the_capability_and_asks_with_it(self, ctx) -> None:
        registry = ToolRegistry()
        registry.register_all(default_tools(enable_open_exec_tools=True))
        call = ToolCall(id=ToolCallId("c1"), name="generate_and_run_tests", arguments={"source_code": SOURCE})

        denied, _ = await registry.execute(call, ctx)
        assert denied.ok is False and denied.error_kind == "policy_denied"
        assert "missing_capability" in denied.content

        asked, _ = await registry.execute(call, ctx, capabilities=("engineer_agent",))
        assert asked.ok is False and asked.error_kind == "confirmation_required"

    async def test_a_confirmed_call_runs_and_taints_the_turn(self, ctx) -> None:
        registry = ToolRegistry()
        registry.register_all(default_tools(enable_open_exec_tools=True))
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("c2"), name="generate_and_run_tests", arguments={"source_code": SOURCE}),
            ctx, capabilities=("engineer_agent",), confirmed=True,
        )
        assert result.ok, result.content
        assert json.loads(result.content)["verified"] is True
        assert ctx.tainted is True  # pytest output echoes the code it ran


class TestGenerateAndRunTests:
    async def test_generated_structural_checks_pass_on_good_source(self, ctx) -> None:
        out = json.loads(await _TOOLS["generate_and_run_tests"].execute({"source_code": SOURCE}, ctx))
        assert out["verified"] is True
        assert out["verification_level"] == "structural"
        assert out["execution"]["counts"]["passed"] >= 2

    async def test_custom_tests_that_fail_are_reported_as_failing(self, ctx) -> None:
        out = json.loads(await _TOOLS["generate_and_run_tests"].execute(
            {"source_code": SOURCE,
             "custom_test_code": "from module_under_test import is_on\n\ndef test_x():\n    assert is_on() is False\n"},
            ctx,
        ))
        assert out["verified"] is False
        assert out["verification_level"] == "custom"
        assert out["execution"]["counts"]["failed"] == 1

    async def test_a_hostile_module_path_is_refused_before_anything_runs(self, ctx) -> None:
        for bad in ("../../etc/passwd", "a b", "x;import os"):
            with pytest.raises(ToolExecutionError, match="dotted Python identifier"):
                await _TOOLS["generate_and_run_tests"].execute(
                    {"source_code": SOURCE, "module_import_path": bad}, ctx
                )

    async def test_the_gateways_secrets_are_not_visible_to_the_code_under_test(self, ctx, monkeypatch) -> None:
        monkeypatch.setenv("AURA_JWT_SECRET", "super-secret-value")
        monkeypatch.setenv("AURA_PROVIDER_HOSTED1_API_KEY", "sk-test-0123456789abcdef")
        leaky_source = "import os\n\nSEEN = sorted(k for k in os.environ if k.startswith('AURA'))\n"
        out = json.loads(await _TOOLS["generate_and_run_tests"].execute(
            {"source_code": leaky_source,
             "custom_test_code": "from module_under_test import SEEN\n\ndef test_none():\n    assert SEEN == []\n"},
            ctx,
        ))
        assert out["verified"] is True, out["execution"]

    async def test_output_is_capped_but_stays_valid_json(self, ctx) -> None:
        noisy = "print('x' * 50000)\n"
        raw = await _TOOLS["generate_and_run_tests"].execute(
            {"source_code": noisy,
             "custom_test_code": "import module_under_test\n\ndef test_ok():\n    assert True\n"},
            ctx,
        )
        json.loads(raw)  # would raise if truncated mid-token
        assert len(raw) < 16_000

    async def test_a_busy_runner_says_so_instead_of_queueing_forever(self, ctx, monkeypatch) -> None:
        monkeypatch.setattr(open_exec, "_SLOT_WAIT_S", 0.1)
        held = [open_exec._SLOTS.acquire(timeout=1), open_exec._SLOTS.acquire(timeout=1)]
        try:
            assert all(held)
            with pytest.raises(ToolExecutionError, match="busy"):
                await _TOOLS["generate_and_run_tests"].execute({"source_code": SOURCE}, ctx)
        finally:
            for ok in held:
                if ok:
                    open_exec._SLOTS.release()

    async def test_slots_are_released_after_a_failure(self, ctx) -> None:
        """Three more calls than there are slots still complete: nothing leaked a slot."""
        for _ in range(4):
            await _TOOLS["generate_and_run_tests"].execute({"source_code": "def (:\n"}, ctx)  # syntax error
        assert threading.active_count() < 50


class TestAnalyzeTestQuality:
    async def test_coverage_and_mutation_on_tests_that_catch_the_mutation(self, ctx) -> None:
        out = json.loads(await _TOOLS["analyze_test_quality"].execute(
            {"source_code": SOURCE, "test_code": TESTS, "timeout_s": 20}, ctx
        ))
        assert out["coverage"]["available"] is True, out["coverage"]
        assert out["coverage"]["tests_passed"] is True
        assert out["coverage"]["coverage_percentage"] == 100.0
        assert out["mutation"]["evaluated"] is True
        assert out["mutation"]["mutants_total"] == 1 and out["mutation"]["mutants_killed"] == 1

    async def test_tests_that_miss_the_mutation_score_zero(self, ctx) -> None:
        weak = "from module_under_test import is_on\n\n\ndef test_runs():\n    is_on()\n"
        out = json.loads(await _TOOLS["analyze_test_quality"].execute(
            {"source_code": SOURCE, "test_code": weak, "operations": "mutation", "timeout_s": 20}, ctx
        ))
        assert out["mutation"]["mutation_score"] == 0.0
        assert "coverage" not in out

    async def test_a_red_baseline_is_refused_with_the_reason(self, ctx) -> None:
        failing = "from module_under_test import is_on\n\n\ndef test_bad():\n    assert is_on() is False\n"
        out = json.loads(await _TOOLS["analyze_test_quality"].execute(
            {"source_code": SOURCE, "test_code": failing, "operations": "mutation", "timeout_s": 20}, ctx
        ))
        assert out["mutation"]["evaluated"] is False
        assert "does not pass" in out["mutation"]["error"]

    @pytest.mark.parametrize("name", ["a.b", "../x", "x y", "1x", ""])
    async def test_module_name_must_be_a_single_identifier(self, ctx, name) -> None:
        with pytest.raises(ToolExecutionError):
            await _TOOLS["analyze_test_quality"].execute(
                {"source_code": SOURCE, "test_code": TESTS, "module_name": name}, ctx
            )
