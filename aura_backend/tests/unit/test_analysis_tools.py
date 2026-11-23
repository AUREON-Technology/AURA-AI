"""ADR-036 group A: the pure analysis tools.

Every wrapper is exercised through ``Tool.execute`` (what the kernel does), and the group's
invariants -- no capability, no side effects, no I/O, grants that name only real tools -- are pinned
so a later tool cannot quietly join it with a different risk profile.
"""

from __future__ import annotations

import json

import pytest

from aura_core.agents.registry import (
    _CODE_ANALYSIS_TOOLS,
    _DATA_ANALYSIS_TOOLS,
    _ENGINEER_OPEN_TOOLS,
    build_default_agent_registry,
)
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import ToolCallId
from aura_core.domain.messages import ToolCall
from aura_core.ports.runtime import ToolContext
from aura_core.tools.analysis import ANALYSIS_TOOL_NAMES, build_analysis_tools
from aura_core.tools.builtin import default_tools
from aura_core.tools.desktop_ops import DESKTOP_OPS_TOOL_NAMES
from aura_core.tools.open_exec import OPEN_EXEC_TOOL_NAMES
from aura_core.tools.registry import ToolRegistry


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-1", deadline_s=30.0)


_TOOLS = {t.spec.name: t for t in build_analysis_tools()}


async def call(name: str, ctx: ToolContext, **arguments) -> dict:
    return json.loads(await _TOOLS[name].execute(arguments, ctx))


class TestGroupInvariants:
    def test_the_group_is_exactly_these_tools(self) -> None:
        assert ANALYSIS_TOOL_NAMES == {
            "analyze_python_ast", "analyze_code_complexity", "verify_python_symbols",
            "review_code_diff", "scan_code_security", "analyze_change_impact", "generate_diagram",
            "check_requirements", "scan_dependency_licenses", "compare_api_specs", "inspect_sql",
            "analyze_logs", "validate_data_records", "evaluate_quality_gate", "check_model_drift",
            "synthesize_research", "plan_rollout",
        }

    @pytest.mark.parametrize("name", sorted(ANALYSIS_TOOL_NAMES))
    def test_a_pure_tool_has_no_capability_no_side_effect_and_no_taint(self, name: str) -> None:
        spec = _TOOLS[name].spec
        assert spec.side_effecting is False
        assert spec.required_capability is None
        assert spec.requires_confirmation is False
        assert "untrusted_content" not in spec.tags
        assert spec.workspace_local is False

    @pytest.mark.parametrize("name", sorted(ANALYSIS_TOOL_NAMES))
    def test_every_string_parameter_is_bounded(self, name: str) -> None:
        """`asyncio.to_thread` cannot be cancelled, so the input cap is the real CPU bound."""
        for p in _TOOLS[name].spec.parameters:
            if p.type == "string" and p.enum is None:
                assert p.max_length is not None, f"{name}.{p.name} has no max_length"

    def test_the_group_is_opt_in_at_the_assembly_level(self) -> None:
        assert not ANALYSIS_TOOL_NAMES & {t.spec.name for t in default_tools()}
        assert ANALYSIS_TOOL_NAMES <= {t.spec.name for t in default_tools(enable_analysis_tools=True)}

    def test_it_does_not_import_the_process_or_filesystem_machinery_it_forbids_itself(self) -> None:
        from pathlib import Path

        import aura_core.tools.analysis as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for needle in ("import subprocess", "import os", "open(", "importlib", "eval(", "exec("):
            assert needle not in source, f"analysis.py must stay pure; found {needle!r}"


class TestPersonaGrants:
    def test_every_granted_name_is_a_real_tool(self) -> None:
        """`agents` cannot import `tools`, so the names are literals -- this is what keeps them honest."""
        assert _CODE_ANALYSIS_TOOLS | _DATA_ANALYSIS_TOOLS <= ANALYSIS_TOOL_NAMES
        assert _ENGINEER_OPEN_TOOLS == OPEN_EXEC_TOOL_NAMES | DESKTOP_OPS_TOOL_NAMES

    def test_who_can_see_what(self) -> None:
        registry = build_default_agent_registry()
        coding, research = registry.get("coding_agent"), registry.get("research_agent")
        engineer, decision = registry.get("engineer"), registry.get("decision_agent")
        orchestrator = registry.get("orchestrator")

        assert all(coding.permits_tool(n) for n in _CODE_ANALYSIS_TOOLS | _DATA_ANALYSIS_TOOLS)
        assert research.permits_tool("synthesize_research")
        assert research.permits_tool("validate_data_records")
        assert not research.permits_tool("scan_code_security")
        assert all(engineer.permits_tool(n) for n in ANALYSIS_TOOL_NAMES - {"synthesize_research"})
        assert all(engineer.permits_tool(n) for n in _ENGINEER_OPEN_TOOLS)
        assert orchestrator.allowed_tools is None  # everything the caller's capabilities allow

        # The read-only persona and the personal-data ones get none of it.
        for persona in (decision, registry.get("finance_advisor"), registry.get("health_companion")):
            assert not any(persona.permits_tool(n) for n in ANALYSIS_TOOL_NAMES | _ENGINEER_OPEN_TOOLS)

    def test_code_running_tools_are_granted_only_to_the_capability_gated_persona(self) -> None:
        registry = build_default_agent_registry()
        for persona in registry:
            if persona.id == "orchestrator":
                continue
            has_exec = any(persona.permits_tool(n) for n in _ENGINEER_OPEN_TOOLS)
            assert has_exec == (persona.id == "engineer"), persona.id


class TestPlannerPromptBudget:
    """`PlanningEngine` renders every available tool into its prompt, and the CPU-hosted model
    pays for each token of it. Measured when the group landed: +5.8K characters (~1.5K tokens) for
    17 tools, on a baseline of ~4.6K. This pins that so growth is a decision, not a drift."""

    def test_the_group_costs_no_more_than_the_measured_budget(self) -> None:
        from aura_core.planning.engine import _render_tool_line

        rendered = [_render_tool_line(t.spec.declaration()) for t in build_analysis_tools()]
        total = sum(len(line) + 1 for line in rendered)
        # ADR-044 raised this from 7_000 to 8_500: twelve existing tools gained operations
        # (review depth, SQL schema, diagram derivation, OpenAPI diff, release checklist, metrics and
        # incident analysis, data quality) instead of getting twelve new tool names, which is the
        # cheaper way to add capability to this prompt. Measured at landing: 7_6xx.
        assert total < 8_500, (
            f"the analysis tools now take {total} chars of planner prompt (budget 8500, measured "
            "5837 in ADR-036, about 7600 after ADR-044). Shorten descriptions, drop parameters, or "
            "merge tools -- or raise the budget deliberately and say why in the ADR."
        )

    def test_no_single_tool_line_is_outsized(self) -> None:
        from aura_core.planning.engine import _render_tool_line

        for t in build_analysis_tools():
            assert len(_render_tool_line(t.spec.declaration())) < 700, t.spec.name


class TestThroughTheContainer:
    """The wiring `default_tools` tests cannot see: settings -> `Container._build_tools` -> what a
    caller of each kind is actually offered (the model's tool list is `registry.available(...)`)."""

    def test_a_default_deployment_offers_analysis_to_everyone_and_nothing_that_runs_code(self, container) -> None:
        from aura_core.tools.desktop_ops import DESKTOP_OPS_TOOL_NAMES
        from aura_core.tools.open_exec import OPEN_EXEC_TOOL_NAMES

        plain = {s.name for s in container.tools.available()}
        assert ANALYSIS_TOOL_NAMES <= plain
        assert not (OPEN_EXEC_TOOL_NAMES | DESKTOP_OPS_TOOL_NAMES) & plain  # capability-gated

        engineer = {s.name for s in container.tools.available(capabilities=("engineer_agent",))}
        assert DESKTOP_OPS_TOOL_NAMES <= engineer  # on by default, still needs a paired desktop to run
        assert not OPEN_EXEC_TOOL_NAMES & engineer  # off until an operator turns it on

    def test_the_exec_group_appears_only_when_the_operator_enables_it(self, monkeypatch) -> None:
        from aura_core.config.settings import load_settings
        from aura_core.container import Container
        from aura_core.tools.open_exec import OPEN_EXEC_TOOL_NAMES

        monkeypatch.setenv("AURA_OPEN_EXEC_TOOLS_ENABLED", "true")
        enabled = Container(load_settings(refresh=True, validate=False))
        assert OPEN_EXEC_TOOL_NAMES <= {s.name for s in enabled.tools.available(capabilities=("engineer_agent",))}
        assert not OPEN_EXEC_TOOL_NAMES & {s.name for s in enabled.tools.available()}  # plain user: still hidden
        monkeypatch.delenv("AURA_OPEN_EXEC_TOOLS_ENABLED")
        load_settings(refresh=True, validate=False)

    def test_the_operator_can_turn_the_analysis_group_off(self, monkeypatch) -> None:
        from aura_core.config.settings import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_ANALYSIS_TOOLS_ENABLED", "false")
        off = Container(load_settings(refresh=True, validate=False))
        assert not ANALYSIS_TOOL_NAMES & {s.name for s in off.tools.available()}
        monkeypatch.delenv("AURA_ANALYSIS_TOOLS_ENABLED")
        load_settings(refresh=True, validate=False)

    def test_the_planner_sees_the_analysis_tools_for_the_orchestrator_only_by_grant(self, container) -> None:
        """A persona's grant narrows the list the planner renders: the read-only decision persona
        sees none of the group, the coding persona sees the code ones."""
        registry = build_default_agent_registry()
        offered = {s.name for s in container.tools.available()}
        assert {n for n in offered if registry.get("decision_agent").permits_tool(n)} & ANALYSIS_TOOL_NAMES == set()
        assert {"scan_code_security", "analyze_python_ast"} <= {
            n for n in offered if registry.get("coding_agent").permits_tool(n)
        }


class TestThroughTheRegistry:
    async def test_a_plain_user_can_call_an_analysis_tool_without_any_capability(self, ctx) -> None:
        registry = ToolRegistry()
        registry.register_all(default_tools(enable_analysis_tools=True))
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("c1"), name="scan_code_security", arguments={"source_code": "eval(x)\n"}), ctx
        )
        assert result.ok, result.content
        assert json.loads(result.content)["secure"] is False

    async def test_an_oversized_argument_is_rejected_before_any_work_is_done(self, ctx) -> None:
        registry = ToolRegistry()
        registry.register_all(default_tools(enable_analysis_tools=True))
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("c2"), name="analyze_python_ast", arguments={"source_code": "x" * 200_001}), ctx
        )
        assert result.ok is False
        assert "at most 200000 characters" in result.content


class TestSourceCodeTools:
    async def test_ast_summary(self, ctx) -> None:
        out = await call(
            "analyze_python_ast", ctx,
            source_code="import os\n\nclass A:\n    def f(self):\n        return 1\n\ndef g(x):\n    return A().f()\n",
        )
        assert [c["name"] for c in out["classes"]] == ["A"]
        assert any(f.startswith("def g(") for f in out["functions"])

    async def test_ast_syntax_error_is_a_result_not_a_crash(self, ctx) -> None:
        out = await call("analyze_python_ast", ctx, source_code="def (:\n")
        assert out["status"] == "error"

    async def test_complexity_flags_a_branchy_function(self, ctx) -> None:
        source = (
            "def f(x):\n"
            "    if x > 1:\n        return 1\n"
            "    elif x > 2:\n        return 2\n"
            "    for i in range(x):\n        if i:\n            return i\n"
            "    return 0\n"
        )
        out = await call("analyze_code_complexity", ctx, source_code=source, max_complexity=2)
        assert out["functions_analysis"]["flagged_count"] == 1

    async def test_undefined_names_are_found_and_modules_are_never_imported(self, ctx) -> None:
        out = await call("verify_python_symbols", ctx, source_code="def f():\n    return nope\n")
        assert out["valid"] is False
        assert out["undefined_names_check"]["undefined_names"][0]["name"] == "nope"

        # A module that does not exist is not looked up: runtime imports are forced off.
        out = await call("verify_python_symbols", ctx, source_code="import surely_not_a_real_module_xyz\n")
        assert out["valid"] is True
        assert out["runtime_imports"] is None

    async def test_review_of_a_diff(self, ctx) -> None:
        diff = "+++ b/a.py\n@@ -0,0 +1,2 @@\n+print('x')\n+try:\n+    pass\n+except Exception:\n+    pass\n"
        out = await call("review_code_diff", ctx, diff_text=diff)
        codes = {f["code"] for f in out["findings"]}
        assert {"REV-DEBUG-PRINT", "REV-BROAD-EXCEPT"} <= codes
        assert out["approved"] is False

    async def test_review_needs_something_to_review(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="at least one"):
            await _TOOLS["review_code_diff"].execute({}, ctx)

    async def test_security_scan_finds_patterns_and_never_echoes_a_secret(self, ctx) -> None:
        source = 'import hashlib\neval(user)\napi_key = "abcd1234efgh5678"\nh = hashlib.md5(b"x")\n'
        raw = await _TOOLS["scan_code_security"].execute({"source_code": source}, ctx)
        assert "abcd1234efgh5678" not in raw
        rules = {f["rule_id"] for f in json.loads(raw)["findings"]}
        assert {"SEC-EVAL-001", "SEC-SECRET-ASSIGN", "SEC-HASH-007"} <= rules

    async def test_security_scan_never_runs_bandit(self, ctx) -> None:
        out = await call("scan_code_security", ctx, source_code="x = 1\n")
        assert out["bandit"] is None

    async def test_impact_from_a_diff(self, ctx) -> None:
        diff = "--- a/aura_core/x.py\n+++ b/aura_core/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        out = await call("analyze_change_impact", ctx, diff_text=diff, extra_dependents=["aura_core/y.py"])
        assert out["modified_files"] == ["aura_core/x.py"]
        assert out["blast_radius_count"] == 2
        assert {s["path"] for s in out["suggested_tests"]} >= {"tests/test_x.py"}

    async def test_diagrams(self, ctx) -> None:
        flow = await call(
            "generate_diagram", ctx, diagram_type="flowchart",
            spec={"nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}], "edges": [{"from": "a", "to": "b"}]},
        )
        assert flow["mermaid"].startswith("flowchart TD")
        cls = await call("generate_diagram", ctx, diagram_type="class_from_source",
                         source_code="class A:\n    pass\n\nclass B(A):\n    pass\n")
        assert "classDiagram" in cls["mermaid"]
        assert (await call("generate_diagram", ctx, diagram_type="validate", diagram_code="graph TD\n  A-->B"))["valid"]

    async def test_a_diagram_label_cannot_inject_mermaid_directives(self, ctx) -> None:
        out = await call(
            "generate_diagram", ctx, diagram_type="sequence",
            spec={"participants": ["a", "b"], "messages": [{"from": "a", "to": "b", "text": "hi\nnote over a: pwn"}]},
        )
        assert "\nnote over" not in out["mermaid"]

    async def test_a_missing_input_is_a_clear_error(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="source_code"):
            await _TOOLS["generate_diagram"].execute({"diagram_type": "class_from_source"}, ctx)


class TestDependencyApiAndSqlTools:
    async def test_requirements_and_package_json(self, ctx) -> None:
        out = await call(
            "check_requirements", ctx,
            requirements_text="requests\nflask==2.0.0\nflask==3.0.0\n",
            package_json='{"name": "x", "dependencies": {"left-pad": "*"}}',
        )
        assert [p["name"] for p in out["requirements"]["pinning"]["unpinned_packages"]] == ["requests"]
        assert out["requirements"]["duplicates_and_conflicts"]["has_conflicts"] is True
        assert out["requirements"]["environment"] is None  # pip check is never run
        assert out["requirements"]["lockfiles"] is None  # no filesystem access
        assert out["package_json"]["wildcards"][0]["package"] == "left-pad"

    async def test_licences_are_judged_by_token_not_substring(self, ctx) -> None:
        out = await call(
            "scan_dependency_licenses", ctx,
            packages=[
                {"package": "a", "license": "LGPL-3.0"},
                {"package": "b", "license": "GPL-3.0"},
                {"package": "c", "license": "BSL-1.0"},
                {"package": "d", "license": "UNKNOWN"},
            ],
        )
        assert sorted(v["package"] for v in out["violations"]) == ["b", "d"]

    async def test_api_spec_operations(self, ctx) -> None:
        old = json.dumps({"paths": {"/a": {"get": {}}, "/b": {"get": {"deprecated": True}}}})
        new = json.dumps({"paths": {"/b": {"get": {"deprecated": True}}, "/c": {"get": {}}}})
        diff = await call("compare_api_specs", ctx, operation="diff", old_spec=old, new_spec=new)
        assert diff["compatible"] is False and diff["removed_paths"] == ["/a"]
        dep = await call("compare_api_specs", ctx, operation="deprecated_endpoints", spec=old)
        assert dep["deprecated"] == [{"path": "/b", "method": "GET"}]
        payload = await call(
            "compare_api_specs", ctx, operation="validate_payload",
            payload={"id": "x"}, required_fields=["id", "name"], field_types={"id": "integer"},
        )
        assert payload["valid"] is False and payload["missing_fields"] == ["name"]

    async def test_diff_needs_both_specs(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="old_spec and new_spec"):
            await _TOOLS["compare_api_specs"].execute({"operation": "diff", "old_spec": "{}"}, ctx)

    async def test_sql_lint_never_touches_a_database(self, ctx) -> None:
        out = await call("inspect_sql", ctx, query="DELETE FROM users")
        assert out["unbounded_write"] is True and out["is_safe_sql"] is False
        out = await call("inspect_sql", ctx, query="SELECT * FROM a JOIN b ON a.id = b.id WHERE a.x = 1")
        assert any("SELECT *" in w for w in out["warnings"])
        assert out["all_tables"] == ["a", "b"]
        assert out["index_hints"]


class TestLogDataAndMetricTools:
    async def test_log_operations(self, ctx) -> None:
        logs = ["ok", "ERROR: ValueError while parsing", "Traceback (most recent call last)"]
        errors = await call("analyze_logs", ctx, operation="errors", logs=logs)
        assert errors["incidents_count"] == 2
        assert (await call("analyze_logs", ctx, operation="root_cause", logs=logs))["incidents_count"] == 2
        latency = await call("analyze_logs", ctx, operation="latency", latencies_ms=[10, 20, 2500], threshold_ms=1000)
        assert latency["outliers_count"] == 1

    async def test_record_validation(self, ctx) -> None:
        rows = [{"id": 1, "n": None}, {"id": 1, "n": 2}, {"id": 2, "n": 99}]
        out = await call("validate_data_records", ctx, operation="records", records=rows,
                         required_fields=["n"], unique_key="id")
        assert out["null_violations_count"] == 1 and out["duplicate_count"] == 1
        bounds = await call("validate_data_records", ctx, operation="numeric_bounds", records=rows,
                            field="n", max_value=10)
        assert bounds["violations_count"] == 1

    async def test_records_must_be_objects(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="must be an object"):
            await _TOOLS["validate_data_records"].execute({"operation": "records", "records": [1, 2]}, ctx)

    async def test_the_quality_gate_fails_closed(self, ctx) -> None:
        empty = await call("evaluate_quality_gate", ctx)
        assert empty["ready_to_merge"] is False and "security" in empty["blocker_failures"]
        ok = await call("evaluate_quality_gate", ctx, security_pass=True, tests_pass=True,
                        coverage_pass=True, complexity_pass=True)
        assert ok["ready_to_merge"] is True
        assert (await call("evaluate_quality_gate", ctx, operation="gate"))["status"] == "FAILED"

    async def test_a_misspelt_check_name_is_an_error_not_a_pass(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="Unknown check"):
            await _TOOLS["evaluate_quality_gate"].execute(
                {"security_pass": True, "tests_pass": True, "coverage_pass": True,
                 "complexity_pass": True, "blocking_checks": ["securty"]}, ctx,
            )

    async def test_drift_reports_a_drop_and_refuses_to_bless_garbage(self, ctx) -> None:
        assert (await call("check_model_drift", ctx, operation="accuracy",
                           baseline_accuracy=0.9, current_accuracy=0.6))["drift_detected"] is True
        empty = await call("check_model_drift", ctx, operation="mean_shift",
                           baseline_values=[], current_values=[1.0])
        assert empty["drift_detected"] is True and empty["status"] == "invalid_input"

    async def test_accuracy_drift_needs_both_scores(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="baseline_accuracy and current_accuracy"):
            await _TOOLS["check_model_drift"].execute({"operation": "accuracy", "baseline_accuracy": 0.9}, ctx)

    async def test_research_synthesis_dedupes_and_flags_conflicts(self, ctx) -> None:
        out = await call(
            "synthesize_research", ctx, research_topic="x",
            sources=[{"title": "A", "url": "http://e.com/a", "summary": "s"},
                     {"title": "A again", "url": "HTTP://E.COM/A", "summary": "s"}],
            claims=[{"topic": "speed", "statement": "fast", "source": "1"},
                    {"topic": "speed", "statement": "slow", "source": "2"}],
        )
        assert out["sources_count"] == 1 and out["conflicts_count"] == 1 and out["ready"] is True

    async def test_rollout_plan(self, ctx) -> None:
        out = await call("plan_rollout", ctx, service_name="api", target_version="2", previous_version="1")
        assert out["rollback_supported"] is True and out["health_checks"] == ["/health"]
        assert (await call("plan_rollout", ctx, service_name="api", target_version="2"))["rollback_supported"] is False
