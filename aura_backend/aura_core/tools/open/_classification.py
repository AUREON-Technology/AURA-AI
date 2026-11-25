"""Where every ``tools/open`` module stands (ADR-036): the one list the architecture tests read.

Each public module under ``aura_core/tools/open/`` must appear here with one of four statuses.
``tests/architecture/test_open_tools_classification.py`` fails when a module is added without an
entry, when an entry names a module that does not exist, when a module the builders import is not
``wrapped``, or when anything under ``aura_core`` imports a module that is not ``wrapped``.

- ``wrapped``: a tool builder (``tools/analysis.py`` group A, ``tools/open_exec.py`` group B,
  ``tools/open_pure.py`` ADR-044, ``tools/open_workspace.py`` ADR-045, and the others
  ``BUILDERS`` in the architecture test lists) imports it; the note names the tool.
- ``superseded``: an engine the platform already has does the job better; the file says which.
- ``deferred``: only safe as an ``aura_hostd`` RPC on the user's machine, which is not built.
- ``not_exposed``: kept as reference code, reachable from no tool and so from no persona; the note
  says why (reads paths on the gateway, spawns processes, reaches the network, duplicates an
  engine, or is not worth the planner prompt it would cost).

Retiring a module is a status here plus a note at the top of its file, not a deletion: several were
written by a teammate and are kept for reference. Data only -- this module imports nothing.
"""

from __future__ import annotations

WRAPPED = "wrapped"
SUPERSEDED = "superseded"
DEFERRED = "deferred"
NOT_EXPOSED = "not_exposed"

STATUSES = frozenset({WRAPPED, SUPERSEDED, DEFERRED, NOT_EXPOSED})

#: Header each non-wrapped module's file starts with (its module docstring's first word(s)).
HEADER_PREFIX = {SUPERSEDED: "Superseded", DEFERRED: "Deferred", NOT_EXPOSED: "Not exposed"}

#: module name -> (status, note). Wrapped notes name the builder and the tool.
CLASSIFICATION: dict[str, tuple[str, str]] = {
    # --- the 29 modules of PR #271, decided 2026-11-20 -------------------------------------
    "api_contract_validator": (WRAPPED, "analysis.py: compare_api_specs"),
    "ast_code_analyzer": (WRAPPED, "analysis.py: analyze_python_ast"),
    "autonomous_research_and_synthesis_tool": (WRAPPED, "analysis.py: synthesize_research"),
    "complexity_and_duplication_analyzer": (WRAPPED, "analysis.py: analyze_code_complexity"),
    "data_quality_and_validation_engine": (WRAPPED, "analysis.py: validate_data_records"),
    "database_intelligence_tool": (WRAPPED, "analysis.py: inspect_sql"),
    "dependency_license_compliance_scanner": (WRAPPED, "analysis.py: scan_dependency_licenses"),
    "dependency_manager_validator": (WRAPPED, "analysis.py: check_requirements"),
    "deployment_release_manager": (WRAPPED, "analysis.py: plan_rollout"),
    "mermaid_diagram_generator": (WRAPPED, "analysis.py: generate_diagram"),
    "model_drift_and_performance_monitor": (WRAPPED, "analysis.py: check_model_drift"),
    "observability_incident_analyzer": (WRAPPED, "analysis.py: analyze_logs"),
    "peer_code_reviewer_agent": (WRAPPED, "analysis.py: review_code_diff"),
    "quality_gate_aggregator": (WRAPPED, "analysis.py: evaluate_quality_gate"),
    "regression_impact_analyzer": (WRAPPED, "analysis.py: analyze_change_impact"),
    "security_vulnerability_scanner": (WRAPPED, "analysis.py: scan_code_security"),
    "symbol_and_import_verifier": (WRAPPED, "analysis.py: verify_python_symbols"),
    "automated_test_generator_and_verifier": (WRAPPED, "open_exec.py: generate_and_run_tests"),
    "test_coverage_and_mutation_analyzer": (WRAPPED, "open_exec.py: analyze_test_quality"),
    "knowledge_graph_manager": (SUPERSEDED, "knowledge/graph_pipeline.py, adapters/postgres_graph.py"),
    "model_registry_and_evaluation_manager": (SUPERSEDED, "mlops/model_registry.py (ADR-008 gate)"),
    "workflow_dependency_validator": (SUPERSEDED, "domain/planning.py (Plan._assert_acyclic)"),
    "agent_tool_test_harness": (SUPERSEDED, "evaluation/harness.py, ToolRegistry.execute"),
    "data_lineage_provenance_tracker": (SUPERSEDED, "domain/graph.py, domain/ausm.py provenance"),
    "gpu_resource_scheduler": (SUPERSEDED, "observability/metrics.py, adapters/ml/device.py"),
    "dev_server_manager": (DEFERRED, "needs a dev_server.* aura_hostd RPC (shell=True, PID table)"),
    "browser_subagent": (DEFERRED, "needs a browser.run aura_hostd RPC (Playwright, no SSRF policy)"),
    "performance_profiler": (NOT_EXPOSED, "eval/exec removed; desktop_ops.py profile_python replaces it"),
    "backup_restore_verifier": (
        WRAPPED, "open_workspace.py: verify_backup (ADR-045; confined to AURA_TOOLS_FILES_ROOT, verify only)"),
    # --- the 25 modules added 2026-11-19, triaged 2026-11-21 (KAN-32) ----------------------
    "structured_data_parser": (WRAPPED, "analysis.py: validate_structured_text"),
    "acceptance_criteria_verifier": (
        WRAPPED, "open_pure.py: verify_acceptance_criteria (ADR-044; evidence-based: a criterion passes only on evidence that says so)"),
    "ambiguity_and_clarification_detector": (
        WRAPPED, "open_pure.py: detect_ambiguity (ADR-044)"),
    "architectural_consistency_checker": (
        WRAPPED, "open_pure.py: check_architecture (ADR-044; reads imports from the files the caller passes)"),
    "archive_extractor": (NOT_EXPOSED, "reads and writes paths on the gateway"),
    "artifact_feedback_ingestor": (
        WRAPPED, "open_pure.py: apply_plan_feedback (ADR-044; ADR-047 live plan feedback)"),
    "artifact_generator": (
        WRAPPED, "open_pure.py: generate_artifact (ADR-044; writes nothing)"),
    "automated_quiz_generator": (
        WRAPPED, "open_pure.py: generate_quiz (ADR-044; questions built from facts, answer key kept separate)"),
    "background_task_manager": (
        NOT_EXPOSED, "spawns processes in the gateway with os.environ.copy(); no owner scoping"),
    "binary_file_inspector": (
        WRAPPED, "open_workspace.py: inspect_binary_file (ADR-045; confined to AURA_TOOLS_FILES_ROOT)"),
    "file_type_detector_router": (
        WRAPPED, "open_pure.py: detect_file_type (ADR-044; takes bytes, never a path)"),
    "mobile_artifact_summarizer": (
        WRAPPED, "open_pure.py: summarize_for_mobile (ADR-044)"),
    "multi_language_ast_analyzer": (
        WRAPPED, "open_pure.py: analyze_source_structure (ADR-044; Python by AST, other languages by declaration scan)"),
    "notebook_reader": (
        WRAPPED, "open_workspace.py: read_notebook (ADR-045; confined to AURA_TOOLS_FILES_ROOT)"),
    "project_conventions_loader": (
        WRAPPED, "open_workspace.py: load_project_conventions (ADR-045; confined to AURA_TOOLS_FILES_ROOT)"),
    "project_status_qa_tool": (
        WRAPPED, "open_workspace.py: project_status (ADR-045; reads this user's recorded runs, no file)"),
    "push_notification_dispatcher": (
        NOT_EXPOSED, "POSTs to any caller-named URL with no SSRF guard; 'local' reports delivered"),
    "remote_session_controller": (
        NOT_EXPOSED, "in-memory session states not connected to the kernel; reports a pause that "
        "never happens"),
    "repo_semantic_indexer": (
        WRAPPED, "open_workspace.py: index_repository, search_repository (ADR-045; confined to AURA_TOOLS_FILES_ROOT)"),
    "scope_and_diff_guard": (
        WRAPPED, "open_pure.py: check_diff_scope (ADR-044)"),
    "skills_rules_workflows_loader": (
        WRAPPED, "open_workspace.py: load_project_skills (ADR-045; confined to AURA_TOOLS_FILES_ROOT)"),
    "source_content_explainer": (
        WRAPPED, "open_pure.py: explain_source (ADR-044)"),
    "stale_context_detector": (
        WRAPPED, "open_workspace.py: check_file_staleness (ADR-045; confined to AURA_TOOLS_FILES_ROOT)"),
    "ui_visual_verifier": (
        NOT_EXPOSED, "reads any existing path on the gateway; the 'perceptual' hash is over raw bytes"),
    "verification_evidence_collector": (
        WRAPPED, "open_pure.py: collect_verification_evidence (ADR-044; state travels with the caller, not the process)"),
    # --- princess (ADR-044..046): re-decided 2026-11-25 when it was merged into main ---------
    # --- ADR-046: extensions that live beside the tool they extend ----------------------------
    "security_rules": (WRAPPED, "open_workspace.py: scan_code_security (ADR-048: shared credential catalogue and per-language rule packs)"),
    "trace_ext": (WRAPPED, "ops.py: query_telemetry trace analysis (ADR-049; read-only, registered only when AURA_OPS_* is configured)"),
    "api_ext": (WRAPPED, "analysis.py: compare_api_specs (ADR-046)"),
    "data_ext": (WRAPPED, "analysis.py: validate_data_records (ADR-046)"),
    "diagram_ext": (WRAPPED, "analysis.py: generate_diagram (ADR-046)"),
    "gate_ext": (WRAPPED, "analysis.py: evaluate_quality_gate (ADR-046)"),
    "profile_ext": (WRAPPED, "desktop_ops.py: profile_python (ADR-046)"),
    "quality_ext": (WRAPPED, "open_exec.py: analyze_test_quality (ADR-046)"),
    "release_ext": (WRAPPED, "analysis.py: plan_rollout (ADR-046); release.py: verify_release_health"),
    "research_ext": (WRAPPED, "research.py: research_topic (ADR-046)"),
    "review_ext": (WRAPPED, "analysis.py: review_code_diff (ADR-046)"),
    "sql_schema_ext": (WRAPPED, "analysis.py: inspect_sql (ADR-046)"),
    "telemetry_ext": (WRAPPED, "analysis.py: analyze_logs (ADR-046)"),
    "testgen_ext": (WRAPPED, "open_exec.py: generate_and_run_tests (ADR-046)"),
}


def modules_with(status: str) -> frozenset[str]:
    """The module names classified ``status``."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    return frozenset(name for name, (s, _) in CLASSIFICATION.items() if s == status)
