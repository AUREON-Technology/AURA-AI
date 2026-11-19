import pytest
from aura_core.tools.open.project_conventions_loader import ProjectConventionsLoader
from aura_core.tools.open.scope_and_diff_guard import ScopeAndDiffGuard
from aura_core.tools.open.multi_language_ast_analyzer import MultiLanguageAstAnalyzer
from aura_core.tools.open.dependency_license_compliance_scanner import DependencyLicenseComplianceScanner
from aura_core.tools.open.ambiguity_and_clarification_detector import AmbiguityAndClarificationDetector
from aura_core.tools.open.skills_rules_workflows_loader import SkillsRulesWorkflowsLoader
from aura_core.tools.open.architectural_consistency_checker import ArchitecturalConsistencyChecker
from aura_core.tools.open.workflow_dependency_validator import WorkflowDependencyValidator
from aura_core.tools.open.push_notification_dispatcher import PushNotificationDispatcher
from aura_core.tools.open.mobile_artifact_summarizer import MobileArtifactSummarizer
from aura_core.tools.open.ui_visual_verifier import UiVisualVerifier
from aura_core.tools.open.repo_semantic_indexer import RepoSemanticIndexer
from aura_core.tools.open.browser_subagent import BrowserSubagent
from aura_core.tools.open.remote_session_controller import RemoteSessionController
from aura_core.tools.open.background_task_manager import BackgroundTaskManager
from aura_core.tools.open.database_intelligence_tool import DatabaseIntelligenceTool
from aura_core.tools.open.observability_incident_analyzer import ObservabilityIncidentAnalyzer
from aura_core.tools.open.autonomous_research_and_synthesis_tool import AutonomousResearchAndSynthesisTool
from aura_core.tools.open.deployment_release_manager import DeploymentReleaseManager
from aura_core.tools.open.quality_gate_aggregator import QualityGateAggregator
from aura_core.tools.open.data_quality_and_validation_engine import DataQualityAndValidationEngine
from aura_core.tools.open.agent_tool_test_harness import AgentToolTestHarness
from aura_core.tools.open.model_drift_and_performance_monitor import ModelDriftAndPerformanceMonitor
from aura_core.tools.open.backup_restore_verifier import BackupRestoreVerifier
from aura_core.tools.open.dev_server_manager import DevServerManager
from aura_core.tools.open.gpu_resource_scheduler import GpuResourceScheduler
from aura_core.tools.open.model_registry_and_evaluation_manager import ModelRegistryAndEvaluationManager
from aura_core.tools.open.data_lineage_provenance_tracker import DataLineageProvenanceTracker
from aura_core.tools.open.knowledge_graph_manager import KnowledgeGraphManager
from aura_core.tools.open.api_contract_validator import ApiContractValidator
from aura_core.tools.open.peer_code_reviewer_agent import PeerCodeReviewerAgent


def test_project_conventions_loader_editorconfig_and_validation():
    config = """
    root = true
    [*]
    indent_style = space
    indent_size = 4
    insert_final_newline = true
    max_line_length = 100
    """
    sections = ProjectConventionsLoader.parse_editorconfig(config)
    assert "*" in sections
    assert sections["*"]["indent_style"] == "space"

    res = ProjectConventionsLoader.validate_file_against_conventions(
        "test.py", "def foo():\n    return 42\n", sections["*"]
    )
    assert res["compliant"] is True


def test_scope_and_diff_guard_secrets_and_budget():
    diff = """
    diff --git a/app.py b/app.py
    --- a/app.py
    +++ b/app.py
    @@ -1,1 +1,2 @@
     def foo(): pass
    +api_key = 'abcdef1234567890_secret'
    """
    findings = ScopeAndDiffGuard.detect_accidental_secrets_in_diff(diff)
    assert len(findings) >= 1
    budget = ScopeAndDiffGuard.check_diff_size_budget(diff, max_files=5, max_total_lines=100)
    assert budget["within_budget"] is True


def test_multi_language_ast_analyzer():
    py_code = """
    class Greeter:
        def greet(self, name: str) -> str:
            return f"Hello, {name}"
    """
    res_py = MultiLanguageAstAnalyzer.analyze_python(py_code)
    assert res_py["valid"] is True
    assert "Greeter" in res_py["classes"]
    # 'greet' is a method inside the class, not a top-level function.
    # analyze_python uses iter_child_nodes(tree) so it only walks the top level;
    # methods appear in classes_detailed[i]["methods"] instead.
    classes_detailed = res_py.get("classes_detailed", [])
    greeter_detail = next((c for c in classes_detailed if c.get("name") == "Greeter"), None)
    assert greeter_detail is not None, "classes_detailed must contain Greeter"
    assert "greet" in greeter_detail.get("methods", []), \
        f"greet not found in Greeter methods: {greeter_detail}"

    ts_code = "export class Service { public execute(): void {} }"
    res_ts = MultiLanguageAstAnalyzer.analyze_javascript_typescript(ts_code)
    assert "Service" in res_ts["classes"]


def test_dependency_license_scanner():
    reqs = "fastapi==0.100.0\npydantic==2.0\n"
    res = DependencyLicenseComplianceScanner.scan_requirements_text(reqs)
    assert res["compliant"] is True


def test_ambiguity_detector():
    vague_text = "Make it fast and optimize stuff."
    score = AmbiguityAndClarificationDetector.score_ambiguity(vague_text)
    assert score["ambiguity_score"] > 0.4
    aspects = AmbiguityAndClarificationDetector.check_missing_specifications(vague_text)
    assert "error_handling" in aspects["missing_aspects"]


def test_skills_rules_workflows_loader():
    skill_content = """---
name: python-testing
description: Guidelines and best practices for writing robust pytest test suites.
tags: [python, pytest]
---
# Python Testing Instructions
Always write thorough parametrized unit tests with high assertion coverage.
"""
    parsed = SkillsRulesWorkflowsLoader.parse_skill_yaml_frontmatter(skill_content)
    assert parsed["has_frontmatter"] is True
    assert parsed["metadata"]["name"] == "python-testing"

    matched = SkillsRulesWorkflowsLoader.match_skills_for_task(
        [{"name": "python-testing", "description": "Pytest guidelines", "tags": ["pytest"]}],
        "Run pytest for my backend python service",
    )
    assert len(matched) >= 1


def test_architectural_consistency_layering_and_cycles():
    graph = {
        "aura_backend.core.models": ["aura_backend.domain.entities"],
        "aura_backend.api.routes": ["aura_backend.core.models"],
    }
    violations = ArchitecturalConsistencyChecker.detect_layering_violations(graph)
    assert len(violations) == 0

    cyclic_graph = {"A": ["B"], "B": ["C"], "C": ["A"]}
    cycles = ArchitecturalConsistencyChecker.detect_circular_package_dependencies(cyclic_graph)
    assert len(cycles) >= 1


def test_workflow_dependency_validator():
    graph = {
        "step_1": [],
        "step_2": ["step_1"],
        "step_3": ["step_1"],
        "step_4": ["step_2", "step_3"],
    }
    tiers_res = WorkflowDependencyValidator.get_parallel_execution_tiers(graph)
    assert tiers_res["valid"] is True
    assert tiers_res["total_tiers"] == 3

    crit_res = WorkflowDependencyValidator.calculate_critical_path(graph)
    assert crit_res["valid"] is True
    assert "step_4" in crit_res["critical_path"]


def test_push_notification_dispatcher():
    slack = PushNotificationDispatcher.format_slack_block_kit("Alert", "Build succeeded", "ci", "normal")
    assert "blocks" in slack
    discord = PushNotificationDispatcher.format_discord_embed("Alert", "Message", "ci", "urgent")
    assert "embeds" in discord


def test_mobile_artifact_summarizer():
    diff = "+++ b/router.py\n+a\n+b\n-c\n"
    res = MobileArtifactSummarizer.summarize_diff_compact(diff)
    assert res["total_files"] == 1
    tests_summary = MobileArtifactSummarizer.summarize_test_results("15 passed, 0 failed in 0.5s")
    assert tests_summary["status"] == "PASSED"


def test_ui_visual_verifier():
    data_a = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\x00\x00\x01\x00\x08\x06\x00\x00\x00" + b"\x00" * 20
    dim = UiVisualVerifier.extract_image_dimensions(data_a)
    assert dim is not None
    assert dim[0] == 256 and dim[1] == 256
    phash = UiVisualVerifier.compute_perceptual_hash(data_a)
    assert len(phash) == 16


def test_repo_semantic_indexer():
    content = "\n".join([f"line_{i}" for i in range(100)])
    chunks = RepoSemanticIndexer.chunk_file_for_indexing(content, chunk_size_lines=30, overlap=5)
    assert len(chunks) >= 4


def test_remote_session_controller():
    ctrl = RemoteSessionController()
    s = ctrl.get_or_create_session("sess_1")
    assert s["state"] == "running"
    ctrl.save_session_checkpoint("sess_1", {"step": 1})
    ctrl.apply_action("sess_1", "pause")
    assert ctrl.get_or_create_session("sess_1")["state"] == "paused"
    ctrl.restore_session_checkpoint("sess_1")
    assert ctrl.get_or_create_session("sess_1")["state"] == "running"


def test_database_intelligence_tool():
    res = DatabaseIntelligenceTool.validate_sql_syntax("SELECT id, name FROM users", dialect="sqlite")
    assert res["valid"] is True
    injections = DatabaseIntelligenceTool.detect_injection_risks('query = f"SELECT * FROM u WHERE id={user_id}"')
    assert len(injections) >= 1


def test_observability_incident_analyzer():
    logs = [
        "2026-11-19 ERROR ValueError: invalid literal for int() with base 10: 'abc'",
        "2026-11-19 ERROR ValueError: invalid literal for int() with base 10: 'xyz'",
    ]
    clusters = ObservabilityIncidentAnalyzer.cluster_error_traces(logs)
    assert len(clusters) == 1


def test_deployment_release_manager():
    risk = DeploymentReleaseManager.calculate_deployment_risk(
        changed_files_count=3,
        has_db_migrations=False,
        rollback_supported=True,
        test_coverage_pct=90.0,
    )
    assert risk["risk_level"] == "LOW"
    assert risk["can_deploy_automatically"] is True


def test_quality_gate_aggregator():
    gate = QualityGateAggregator.aggregate_all_checks(
        test_result={"passed": 10, "failed": 0},
        sec_result={"clean": True, "findings": []},
        cov_result={"coverage_ratio": 0.85},
        arch_result={"is_consistent": True, "violations": []},
    )
    assert gate["ready_to_merge"] is True


def test_data_quality_and_validation_engine():
    data = [{"id": i, "val": 10.0 + (i % 3)} for i in range(20)]
    data.append({"id": 99, "val": 1000.0})  # strong statistical outlier
    outliers = DataQualityAndValidationEngine.detect_statistical_outliers(data, "val")
    assert outliers["outliers_count"] >= 1


def test_model_drift_monitor():
    res = ModelDriftAndPerformanceMonitor.calculate_kl_divergence([0.5, 0.5], [0.5, 0.5])
    assert res["kl_divergence"] == 0.0
    assert res["drift_detected"] is False


def test_dev_server_manager_port():
    port = DevServerManager.find_free_port(9100)
    assert 9100 <= port < 9200


def test_gpu_resource_scheduler():
    vram = GpuResourceScheduler.estimate_model_vram_requirement(param_count_billions=7.0, precision_bits=16)
    assert vram["weights_vram_gb"] > 10.0
    offload = GpuResourceScheduler.suggest_offload_strategy(
        model_vram_gb=16.0, available_gpu_vram_gb=12.0
    )
    assert offload["fits_natively"] is False
    assert len(offload["recommendations"]) >= 1


def test_data_lineage_and_knowledge_graph():
    tracker = DataLineageProvenanceTracker()
    tracker.record_transformation("raw_csv", "bronze_table", "ingest")
    tracker.record_transformation("bronze_table", "silver_table", "clean")
    chain = tracker.get_provenance_chain("silver_table")
    assert "bronze_table" in chain
    assert "raw_csv" in chain
    mermaid = tracker.export_to_mermaid()
    assert "flowchart LR" in mermaid

    kg = KnowledgeGraphManager()
    kg.add_relation("AuraAgent", "uses", "PythonTools")
    kg.add_relation("PythonTools", "executed_on", "WindowsSandbox")
    paths = kg.find_paths_between_entities("AuraAgent", "WindowsSandbox")
    assert len(paths) >= 1


def test_api_contract_validator_and_peer_reviewer():
    old_spec = {"paths": {"/users": {"get": {"parameters": []}}}}
    new_spec = {"paths": {"/users": {"get": {"parameters": [{"name": "auth", "required": True}]}}}}
    res = ApiContractValidator.detect_breaking_changes(old_spec, new_spec)
    assert res["is_breaking"] is True
    assert res["recommended_bump"] == "MAJOR"

    diff = "+++ b/test.py\n+print('hello')\n"
    patches = PeerCodeReviewerAgent.suggest_inline_patches(diff)
    assert len(patches) >= 1
    assert "logger.debug" in patches[0]["suggested_patch"]


# ===========================================================================
# Bug-fix regression tests (v2) – 5 tools
# ===========================================================================


# ---------------------------------------------------------------------------
# 1. DependencyLicenseComplianceScanner – SPDX exact matching
# ---------------------------------------------------------------------------

def test_license_scanner_lgpl_not_flagged_by_gpl_rule():
    """LGPL-3.0 must NOT be flagged when only GPL-3.0 is in the forbidden set."""
    packages = [{"package": "some-lib", "license": "LGPL-3.0"}]
    res = DependencyLicenseComplianceScanner.scan_licenses(packages, forbidden_tokens=["GPL-3.0"])
    assert res["compliant"] is True, f"LGPL-3.0 should be compliant when only GPL-3.0 is forbidden: {res}"


def test_license_scanner_bsl_1_0_not_forbidden_by_default():
    """BSL-1.0 (Boost Software License – permissive) must NOT be forbidden by default."""
    packages = [{"package": "boost-lib", "license": "BSL-1.0"}]
    res = DependencyLicenseComplianceScanner.scan_licenses(packages)
    assert res["compliant"] is True, f"BSL-1.0 (Boost) should pass by default: {res}"


def test_license_scanner_unknown_fails_by_default():
    """UNKNOWN license must fail when no allowlist is supplied."""
    packages = [{"package": "mystery-lib", "license": "UNKNOWN"}]
    res = DependencyLicenseComplianceScanner.scan_licenses(packages)
    assert res["compliant"] is False, f"UNKNOWN license should fail by default: {res}"
    assert res["violations_count"] == 1


def test_license_scanner_gplv3_alias_caught():
    """GPLv3 (alias) must be resolved to GPL-3.0 and caught by the default forbidden set."""
    packages = [{"package": "copyleft-lib", "license": "GPLv3"}]
    res = DependencyLicenseComplianceScanner.scan_licenses(packages)
    assert res["compliant"] is False, f"GPLv3 alias should be caught: {res}"


def test_license_scanner_lgpl_allowed_via_allowlist():
    """LGPL-3.0 is accepted when it is in the allowed_licenses list."""
    packages = [{"package": "lgpl-lib", "license": "LGPL-3.0"}]
    res = DependencyLicenseComplianceScanner.scan_licenses(packages, allowed_licenses=["LGPL-3.0", "MIT"])
    assert res["compliant"] is True


# ---------------------------------------------------------------------------
# 2. AgentToolTestHarness – async tool detection
# ---------------------------------------------------------------------------

def test_harness_async_tool_that_raises_reports_failed():
    """An async tool that raises must be reported as passed=False, not passed=True."""
    import asyncio

    async def bad_async_tool(x: int):
        raise ValueError("intentional async failure")

    result = AgentToolTestHarness.run_tool_case(
        tool_name="bad_async",
        tool_callable=bad_async_tool,
        payload={"x": 1},
    )
    assert result["passed"] is False, f"async tool that raises should fail: {result}"
    assert "ValueError" in result.get("error", "")


def test_harness_async_tool_that_succeeds_reports_passed():
    """An async tool that returns a dict must be run and reported as passed=True."""
    import asyncio

    async def good_async_tool(name: str):
        return {"greeting": f"hello {name}"}

    result = AgentToolTestHarness.run_tool_case(
        tool_name="good_async",
        tool_callable=good_async_tool,
        payload={"name": "world"},
        expected_return_type=dict,
    )
    assert result["passed"] is True, f"async tool that returns dict should pass: {result}"


def test_harness_output_preview_truncated():
    """output_preview must be truncated when the tool returns a very large string."""
    def big_output_tool():
        return "X" * 10_000

    result = AgentToolTestHarness.run_tool_case(
        tool_name="big_output",
        tool_callable=big_output_tool,
        payload={},
    )
    preview = result.get("output_preview", "")
    assert isinstance(preview, str)
    assert len(preview) <= 2048 + 60  # allow for the truncation suffix


# ---------------------------------------------------------------------------
# 3. ModelRegistryAndEvaluationManager – single production version + deep copy
# ---------------------------------------------------------------------------

def test_model_registry_only_one_production_version():
    """After promoting v2, v1 must be demoted from production automatically."""
    mgr = ModelRegistryAndEvaluationManager()
    mgr.register_model_version("m1", "v1", {"accuracy": 0.90})
    mgr.register_model_version("m1", "v2", {"accuracy": 0.92})
    mgr.promote_if_eligible("m1", "v1", threshold=0.85)
    mgr.promote_if_eligible("m1", "v2", threshold=0.85)

    versions = mgr.list_versions("m1")
    production_versions = [v for v in versions if v["stage"] == "production"]
    assert len(production_versions) == 1, f"Expected exactly 1 production version, got: {production_versions}"
    assert production_versions[0]["version"] == "v2"


def test_model_registry_deep_copy_isolation():
    """Mutating metrics on a returned list_versions entry must not corrupt the registry."""
    mgr = ModelRegistryAndEvaluationManager()
    mgr.register_model_version("m2", "v1", {"accuracy": 0.9})

    snapshot = mgr.list_versions("m2")
    snapshot[0]["metrics"]["accuracy"] = 0.0  # mutate the returned copy

    # Internal value must be unchanged
    fresh = mgr.list_versions("m2")
    assert fresh[0]["metrics"]["accuracy"] == 0.9, "Deep copy isolation failed"


def test_model_registry_zero_threshold_rejected():
    """promote_if_eligible with threshold <= 0 must return promoted=False."""
    mgr = ModelRegistryAndEvaluationManager()
    mgr.register_model_version("m3", "v1", {"accuracy": 0.5})
    result = mgr.promote_if_eligible("m3", "v1", threshold=0.0)
    assert result["promoted"] is False
    assert "threshold" in result.get("error", "").lower()


def test_model_registry_re_register_production_demotes():
    """Re-registering a production version must demote it back to staging."""
    mgr = ModelRegistryAndEvaluationManager()
    mgr.register_model_version("m4", "v1", {"accuracy": 0.95})
    mgr.promote_if_eligible("m4", "v1", threshold=0.85)

    # Should be in production now
    assert mgr.list_versions("m4")[0]["stage"] == "production"

    # Re-register with new metrics → must be demoted to staging
    mgr.register_model_version("m4", "v1", {"accuracy": 0.50})
    versions = mgr.list_versions("m4")
    assert versions[0]["stage"] == "staging", "Re-registered production version should be demoted to staging"


# ---------------------------------------------------------------------------
# 4. WorkflowDependencyValidator – 3000-task chain without RecursionError
# ---------------------------------------------------------------------------

def test_workflow_long_chain_no_recursion_error():
    """A 3,000-task linear chain must not raise RecursionError."""
    n = 3000
    # task_i depends on task_{i-1}
    graph = {f"t{i}": [f"t{i - 1}"] for i in range(1, n)}
    graph["t0"] = []  # root

    result = WorkflowDependencyValidator.detect_cycles(graph)
    assert result["workflow_valid"] is True
    assert result["circular_dependencies_detected"] is False


def test_workflow_cycle_still_detected_after_iterative_fix():
    """Cycles must still be detected correctly with the iterative implementation."""
    graph = {"A": ["B"], "B": ["C"], "C": ["A"]}
    result = WorkflowDependencyValidator.detect_cycles(graph)
    assert result["circular_dependencies_detected"] is True


# ---------------------------------------------------------------------------
# 5. ModelDriftAndPerformanceMonitor – fail-open on NaN and empty inputs
# ---------------------------------------------------------------------------

def test_drift_nan_accuracy_reports_drift():
    """NaN current_accuracy must produce drift_detected=True (fail-open)."""
    import math
    res = ModelDriftAndPerformanceMonitor.check_accuracy_drift(
        baseline_accuracy=0.90,
        current_accuracy=float("nan"),
    )
    assert res["drift_detected"] is True
    assert "error" in res


def test_drift_nan_baseline_reports_drift():
    """NaN baseline_accuracy must also produce drift_detected=True."""
    import math
    res = ModelDriftAndPerformanceMonitor.check_accuracy_drift(
        baseline_accuracy=float("nan"),
        current_accuracy=0.80,
    )
    assert res["drift_detected"] is True


def test_mean_shift_empty_baseline_reports_drift():
    """Empty baseline_values must produce drift_detected=True (fail-open)."""
    res = ModelDriftAndPerformanceMonitor.check_mean_shift(
        baseline_values=[],
        current_values=[1.0, 2.0, 3.0],
    )
    assert res["drift_detected"] is True
    assert "error" in res


def test_mean_shift_empty_current_reports_drift():
    """Empty current_values must produce drift_detected=True (fail-open)."""
    res = ModelDriftAndPerformanceMonitor.check_mean_shift(
        baseline_values=[1.0, 2.0, 3.0],
        current_values=[],
    )
    assert res["drift_detected"] is True
    assert "error" in res


def test_mean_shift_both_empty_reports_drift():
    """Both lists empty must also produce drift_detected=True."""
    res = ModelDriftAndPerformanceMonitor.check_mean_shift(
        baseline_values=[],
        current_values=[],
    )
    assert res["drift_detected"] is True
