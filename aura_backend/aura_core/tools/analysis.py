"""Code- and data-analysis tools (ADR-036, group A): the pure half of ``tools/open``.

Every tool here takes *text the caller supplies* and returns JSON text. None reads a path, spawns
a process, imports user code or keeps state, so none needs a capability or a confirmation: the
worst a hostile argument can do is waste a bounded amount of CPU, which is what the input caps and
``timeout_s`` are for. (``asyncio.to_thread`` cannot be cancelled -- a timeout stops waiting, not
the thread -- so the caps, not the timeout, are the actual bound.)

One tool per class, with an ``operation`` enum where a class has several independent checks,
rather than one tool per method: ``PlanningEngine`` renders every available tool into its prompt,
and on the CPU-hosted model that prompt is the budget.

The classes themselves live in :mod:`aura_core.tools.open` and are unchanged by being wrapped here
except where review found them wrong (see ``tests/unit/test_open_tools_fixes.py``).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool
from .base import param, tool
from .open.api_contract_validator import ApiContractValidator
from .open.ast_code_analyzer import ASTCodeAnalyzer
from .open.autonomous_research_and_synthesis_tool import AutonomousResearchAndSynthesisTool
from .open.complexity_and_duplication_analyzer import ComplexityAndDuplicationAnalyzer
from .open.data_quality_and_validation_engine import DataQualityAndValidationEngine
from .open.database_intelligence_tool import DatabaseIntelligenceTool
from .open.dependency_license_compliance_scanner import DependencyLicenseComplianceScanner
from .open.dependency_manager_validator import DependencyManagerValidator
from .open.deployment_release_manager import DeploymentReleaseManager
from .open.mermaid_diagram_generator import MermaidDiagramGenerator
from .open.model_drift_and_performance_monitor import ModelDriftAndPerformanceMonitor
from .open.observability_incident_analyzer import ObservabilityIncidentAnalyzer
from .open.peer_code_reviewer_agent import PeerCodeReviewerAgent
from .open.quality_gate_aggregator import QualityGateAggregator
from .open.regression_impact_analyzer import RegressionImpactAnalyzer
from .open.security_vulnerability_scanner import SecurityVulnerabilityScanner
from .open.structured_data_parser import StructuredDataParser
from .open.symbol_and_import_verifier import SymbolAndImportVerifier

__all__ = ["build_analysis_tools", "ANALYSIS_TOOL_NAMES"]

#: Characters of source/diff/spec text one call may carry. Bounds the regex and AST work.
_MAX_TEXT = 200_000
#: Rows/lines/items one call may carry.
_MAX_ITEMS = 20_000

_CODE = dict(category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.PERSONAL, timeout_s=10.0)
_DATA = dict(category=ToolCategory.DATA, sensitivity=ToolSensitivity.PERSONAL, timeout_s=10.0)


def _dump(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """Run a blocking class method off the event loop and return its result as JSON text."""
    return _dump(await asyncio.to_thread(lambda: fn(*args, **kwargs)))


def _need(tool_name: str, **given: Any) -> None:
    """At least one of the named inputs must be present."""
    if all(v is None for v in given.values()):
        raise ToolExecutionError(f"provide at least one of: {', '.join(given)}", tool=tool_name)


def _items(tool_name: str, name: str, value: list | None) -> list:
    value = value or []
    if len(value) > _MAX_ITEMS:
        raise ToolExecutionError(
            f"'{name}' has {len(value)} items; the limit is {_MAX_ITEMS}", tool=tool_name
        )
    return value


def _objects(tool_name: str, name: str, value: list | None) -> list[dict]:
    items = _items(tool_name, name, value)
    if not all(isinstance(i, dict) for i in items):
        raise ToolExecutionError(f"every item of '{name}' must be an object", tool=tool_name)
    return items


# --------------------------------------------------------------------------------------
# Source code
# --------------------------------------------------------------------------------------


@tool(
    "analyze_python_ast",
    "Parse Python source and report its classes, functions, imports, globals and call sites. "
    "Pure static analysis of the text you pass; nothing is executed.",
    param("source_code", "string", "Python source text.", max_length=_MAX_TEXT),
    param("compact", "boolean", "Return the short summary (default true).", required=False, default=True),
    param("include_calls", "boolean", "Include the calls each function makes (default true).",
          required=False, default=True),
    param("include_methods", "boolean", "List methods among the functions (default false).",
          required=False, default=False),
    **_CODE,
)
async def analyze_python_ast(
    source_code: str, compact: bool = True, include_calls: bool = True, include_methods: bool = False
) -> str:
    return await _run(
        ASTCodeAnalyzer.ast_code_analyzer,
        source_code,
        include_methods_in_functions=include_methods,
        include_calls=include_calls,
        compact=compact,
    )


@tool(
    "analyze_code_complexity",
    "Measure Python source for cyclomatic complexity, nesting depth, function length and "
    "duplicated blocks, and flag the functions over the limits.",
    param("source_code", "string", "Python source text.", max_length=_MAX_TEXT),
    param("max_complexity", "integer", "Complexity above which a function is flagged (default 10).",
          required=False, default=10, minimum=1, maximum=200),
    param("max_lines", "integer", "Line count above which a function is flagged (default 50).",
          required=False, default=50, minimum=1, maximum=5000),
    param("duplicate_window", "integer", "Consecutive identical lines that count as a duplicate "
          "block (default 5).", required=False, default=5, minimum=2, maximum=100),
    **_CODE,
)
async def analyze_code_complexity(
    source_code: str, max_complexity: int = 10, max_lines: int = 50, duplicate_window: int = 5
) -> str:
    return await _run(
        ComplexityAndDuplicationAnalyzer.analyze_source,
        source_code,
        max_complexity=max_complexity,
        max_lines=max_lines,
        dup_window=duplicate_window,
    )


@tool(
    "verify_python_symbols",
    "Statically check Python source for undefined names and, if you pass expected_arities, for "
    "calls with the wrong number of positional arguments. Nothing is imported or run.",
    param("source_code", "string", "Python source text.", max_length=_MAX_TEXT),
    param("check_undefined_names", "boolean", "Report names that are used but never defined "
          "(default true).", required=False, default=True),
    param("expected_arities", "object",
          "Optional. {function_name: {min_args, max_args}} to check direct calls against.",
          required=False),
    **_CODE,
)
async def verify_python_symbols(
    source_code: str, check_undefined_names: bool = True, expected_arities: dict | None = None
) -> str:
    return await _run(
        SymbolAndImportVerifier.verify_source,
        source_code,
        check_runtime_imports=False,  # would import modules the source names: never from a tool
        expected_arities=expected_arities,
        check_undefined_names=check_undefined_names,
    )


@tool(
    "review_code_diff",
    "Rule-based review of a unified git diff and/or Python source: debug prints, TODO markers, "
    "broad `except Exception`, missing docstrings. A checklist pass, not a substitute for reading "
    "the change.",
    param("diff_text", "string", "A unified diff (e.g. the output of git_diff).",
          required=False, max_length=_MAX_TEXT),
    param("source_code", "string", "Python source to check for convention issues.",
          required=False, max_length=_MAX_TEXT),
    param("filename", "string", "Name to report findings against (default '<string>').",
          required=False, default="<string>", max_length=500),
    **_CODE,
)
async def review_code_diff(
    diff_text: str | None = None, source_code: str | None = None, filename: str = "<string>"
) -> str:
    _need("review_code_diff", diff_text=diff_text, source_code=source_code)
    return await _run(
        PeerCodeReviewerAgent.full_review, diff_text=diff_text, source_code=source_code, filename=filename
    )


@tool(
    "scan_code_security",
    "Pattern-scan Python source and/or a git diff for insecure constructs (eval/exec, shell=True, "
    "os.system, unsafe pickle/yaml, weak hashes, bare except) and hardcoded secrets. Matched "
    "secret values are redacted in the output. Static; nothing is executed.",
    param("source_code", "string", "Source text to scan.", required=False, max_length=_MAX_TEXT),
    param("diff_text", "string", "A unified diff; only added lines are scanned.",
          required=False, max_length=_MAX_TEXT),
    **_CODE,
)
async def scan_code_security(source_code: str | None = None, diff_text: str | None = None) -> str:
    _need("scan_code_security", source_code=source_code, diff_text=diff_text)
    return await _run(
        SecurityVulnerabilityScanner.full_scan,
        source_code=source_code,
        diff_text=diff_text,
        use_bandit=False,  # spawns a process
    )


@tool(
    "analyze_change_impact",
    "Estimate regression risk for a change from its diff or file list: blast-radius count, a "
    "LOW/MEDIUM/HIGH level and the test files to run. It cannot scan the repository for reverse "
    "imports, so pass any known dependents as extra_dependents.",
    param("diff_text", "string", "A unified diff to take the changed files from.",
          required=False, max_length=_MAX_TEXT),
    param("modified_files", "array", "Changed file paths.", required=False, item_type="string"),
    param("extra_dependents", "array", "Files you already know import the changed ones.",
          required=False, item_type="string"),
    **_CODE,
)
async def analyze_change_impact(
    diff_text: str | None = None,
    modified_files: list | None = None,
    extra_dependents: list | None = None,
) -> str:
    _need("analyze_change_impact", diff_text=diff_text, modified_files=modified_files)
    files = [str(f) for f in _items("analyze_change_impact", "modified_files", modified_files)]
    if diff_text:
        files += [
            c["path"]
            for c in RegressionImpactAnalyzer.parse_changed_files_from_diff(diff_text)
            if c["path"] not in files
        ]
    extra = [str(f) for f in _items("analyze_change_impact", "extra_dependents", extra_dependents)]
    return await _run(
        RegressionImpactAnalyzer.analyze_impact, files, repo_dir=None, extra_dependents=extra
    )


@tool(
    "generate_diagram",
    "Produce Mermaid diagram text. diagram_type: class_from_source (from Python source), class, "
    "flowchart, sequence, er, or validate (check diagram_code). Identifiers and labels are "
    "sanitised.",
    param("diagram_type", "string", "Which diagram to produce.",
          enum=("class_from_source", "class", "flowchart", "sequence", "er", "validate")),
    param("source_code", "string", "Python source (class_from_source).", required=False,
          max_length=_MAX_TEXT),
    param("spec", "object",
          "class: {class_names?, relations? [{from,to,type}], class_details? [{name,attributes,methods}]}. "
          "flowchart: {nodes [{id,label,shape}], edges [{from,to,label}], direction?}. "
          "sequence: {participants [..], messages [{from,to,text,arrow?}]}. "
          "er: {entities {Name: [{name,type,key?}]}, relationships? [..]}.", required=False),
    param("diagram_code", "string", "Mermaid text to validate (validate).", required=False,
          max_length=_MAX_TEXT),
    category=ToolCategory.UTILITY, sensitivity=ToolSensitivity.PERSONAL, timeout_s=10.0,
)
async def generate_diagram(
    diagram_type: str,
    source_code: str | None = None,
    spec: dict | None = None,
    diagram_code: str | None = None,
) -> str:
    name = "generate_diagram"
    spec = spec or {}
    if diagram_type == "class_from_source":
        _need(name, source_code=source_code)
        return await _run(MermaidDiagramGenerator.generate_class_diagram_from_source, source_code)
    if diagram_type == "validate":
        _need(name, diagram_code=diagram_code)
        return await _run(MermaidDiagramGenerator.validate_mermaid_syntax, diagram_code)

    def build() -> dict:
        gen = MermaidDiagramGenerator
        if diagram_type == "class":
            text = gen.generate_class_diagram(
                class_names=spec.get("class_names"),
                relations=spec.get("relations"),
                class_details=spec.get("class_details"),
            )
        elif diagram_type == "flowchart":
            text = gen.generate_flowchart(
                spec.get("nodes") or [], spec.get("edges") or [], direction=str(spec.get("direction", "TD"))
            )
        elif diagram_type == "sequence":
            text = gen.generate_sequence_diagram(spec.get("participants") or [], spec.get("messages") or [])
        else:  # er
            text = gen.generate_er_diagram(spec.get("entities") or {}, spec.get("relationships"))
        return {"mermaid": text}

    return await _run(build)


# --------------------------------------------------------------------------------------
# Dependencies, APIs and SQL
# --------------------------------------------------------------------------------------


@tool(
    "check_requirements",
    "Check dependency manifests passed as text: requirements.txt pinning, duplicate or conflicting "
    "specs, VCS references, and package.json wildcard/range versions. Does not read the "
    "filesystem or the installed environment.",
    param("requirements_text", "string", "Contents of a requirements.txt.", required=False,
          max_length=_MAX_TEXT),
    param("package_json", "string", "Contents of a package.json.", required=False,
          max_length=_MAX_TEXT),
    **_CODE,
)
async def check_requirements(requirements_text: str | None = None, package_json: str | None = None) -> str:
    _need("check_requirements", requirements_text=requirements_text, package_json=package_json)

    def check() -> dict:
        out: dict[str, Any] = {}
        if requirements_text is not None:
            out["requirements"] = DependencyManagerValidator.validate_requirements_full(requirements_text)
        if package_json is not None:
            out["package_json"] = DependencyManagerValidator.validate_package_json(package_json)
        return out

    return await _run(check)


@tool(
    "scan_dependency_licenses",
    "Check package licences against a policy. Reads SPDX expressions (OR/AND/WITH), flags "
    "strong-copyleft and source-available licences by default, flags unknown or blank licences, "
    "and enforces an allowlist when you give one.",
    param("packages", "array", "Each item: {package, license}.", item_type="object"),
    param("forbidden", "array", "Licence ids to forbid, replacing the default list.",
          required=False, item_type="string"),
    param("allowed", "array", "If given, every licence must be one of these.",
          required=False, item_type="string"),
    **_CODE,
)
async def scan_dependency_licenses(
    packages: list, forbidden: list | None = None, allowed: list | None = None
) -> str:
    name = "scan_dependency_licenses"
    return await _run(
        DependencyLicenseComplianceScanner.scan_licenses,
        [{str(k): str(v) for k, v in p.items()} for p in _objects(name, "packages", packages)],
        forbidden_tokens=[str(x) for x in forbidden] if forbidden is not None else None,
        allowed_licenses=[str(x) for x in allowed] if allowed is not None else None,
    )


@tool(
    "compare_api_specs",
    "Work with JSON OpenAPI/contract documents. operation: deprecated_endpoints (list deprecated "
    "operations in `spec`), diff (paths removed/added between `old_spec` and `new_spec`; flags "
    "removals as breaking), validate_payload (check `payload` has `required_fields` of the right "
    "`field_types`).",
    param("operation", "string", "What to do.", enum=("deprecated_endpoints", "diff", "validate_payload")),
    param("spec", "string", "A JSON spec (deprecated_endpoints).", required=False, max_length=_MAX_TEXT),
    param("old_spec", "string", "The earlier JSON spec (diff).", required=False, max_length=_MAX_TEXT),
    param("new_spec", "string", "The later JSON spec (diff).", required=False, max_length=_MAX_TEXT),
    param("payload", "object", "A request/response body (validate_payload).", required=False),
    param("required_fields", "array", "Keys the payload must have.", required=False, item_type="string"),
    param("field_types", "object", "{field: 'string'|'integer'|'number'|'boolean'|'array'|'object'}.",
          required=False),
    **_CODE,
)
async def compare_api_specs(
    operation: str,
    spec: str | None = None,
    old_spec: str | None = None,
    new_spec: str | None = None,
    payload: dict | None = None,
    required_fields: list | None = None,
    field_types: dict | None = None,
) -> str:
    name = "compare_api_specs"
    if operation == "deprecated_endpoints":
        _need(name, spec=spec)
        return await _run(ApiContractValidator.check_deprecated_endpoints, spec)
    if operation == "diff":
        if old_spec is None or new_spec is None:
            raise ToolExecutionError("diff needs both old_spec and new_spec", tool=name)
        return await _run(ApiContractValidator.compare_route_sets, old_spec, new_spec)
    if payload is None:
        raise ToolExecutionError("validate_payload needs payload", tool=name)
    return await _run(
        ApiContractValidator.validate_payload_against_schema,
        payload,
        [str(f) for f in _items(name, "required_fields", required_fields)],
        field_types,
    )


@tool(
    "validate_structured_text",
    "Check that JSON, YAML, TOML or XML text parses. On failure: the error, line, column and "
    "offending line. On success: the root type and its top-level keys.",
    param("content", "string", "The document text.", max_length=_MAX_TEXT),
    param("data_format", "string", "Its format.", enum=("json", "yaml", "toml", "xml")),
    **_CODE,
)
async def validate_structured_text(content: str, data_format: str) -> str:
    def check() -> dict:
        result = StructuredDataParser.parse_with_line_errors(content, data_format=data_format)
        # Summarise, never echo, the parsed value: the caller already has the text, and a YAML
        # alias bomb is small as references but exponential once serialised.
        parsed = result.pop("parsed_data", None)
        out: dict[str, Any] = {"format": data_format, **result}
        if result.get("valid"):
            if data_format == "xml":
                out.update(root_type="element", root_tag=str(parsed.tag), children_count=len(parsed))
            else:
                out["root_type"] = type(parsed).__name__
                if isinstance(parsed, dict):
                    keys = [str(k) for k in parsed]
                    out.update(top_level_keys=keys[:200], keys_count=len(keys))
                elif isinstance(parsed, list):
                    out["items_count"] = len(parsed)
        return out

    return await _run(check)


@tool(
    "inspect_sql",
    "Lint a SQL statement or script with patterns only (it never connects to a database): flags "
    "DROP/TRUNCATE, DELETE/UPDATE without WHERE and SELECT *, lists referenced tables, and "
    "suggests indexes for WHERE and JOIN columns.",
    param("query", "string", "The SQL text.", max_length=_MAX_TEXT),
    param("schema_info", "string", "Optional schema text (DDL or table list) to match the "
          "referenced tables against.", required=False, max_length=_MAX_TEXT),
    **_DATA,
)
async def inspect_sql(query: str, schema_info: str | None = None) -> str:
    def lint() -> dict:
        result = DatabaseIntelligenceTool.inspect_sql(query, schema_info)
        result["all_tables"] = DatabaseIntelligenceTool.extract_table_names(query)
        result["index_hints"] = DatabaseIntelligenceTool.suggest_index_hints(query)
        return result

    return await _run(lint)


# --------------------------------------------------------------------------------------
# Logs, data and metrics
# --------------------------------------------------------------------------------------


@tool(
    "analyze_logs",
    "Analyse log lines you pass in. operation: errors (lines with ERROR/CRITICAL/FATAL/Traceback "
    "and the most common exception types), root_cause (first incident and the dominant exception), "
    "latency (outliers above threshold_ms in `latencies_ms`).",
    param("operation", "string", "What to do.", enum=("errors", "root_cause", "latency")),
    param("logs", "array", "Log lines (errors, root_cause).", required=False, item_type="string"),
    param("latencies_ms", "array", "Latency samples in ms (latency).", required=False, item_type="number"),
    param("threshold_ms", "number", "Outlier threshold in ms (default 1000).", required=False,
          default=1000.0, minimum=0),
    **_DATA,
)
async def analyze_logs(
    operation: str,
    logs: list | None = None,
    latencies_ms: list | None = None,
    threshold_ms: float = 1000.0,
) -> str:
    name = "analyze_logs"
    if operation == "latency":
        _need(name, latencies_ms=latencies_ms)
        return await _run(
            ObservabilityIncidentAnalyzer.extract_latency_outliers,
            [float(v) for v in _items(name, "latencies_ms", latencies_ms)],
            threshold_ms=threshold_ms,
        )
    _need(name, logs=logs)
    lines = [str(line) for line in _items(name, "logs", logs)]
    if operation == "root_cause":
        return await _run(ObservabilityIncidentAnalyzer.identify_root_cause, lines)
    return await _run(ObservabilityIncidentAnalyzer.parse_error_logs, lines)


@tool(
    "validate_data_records",
    "Check tabular rows (a list of objects). operation: records (null/blank/NaN in required_fields "
    "and duplicate values of unique_key) or numeric_bounds (values of `field` outside "
    "min_value/max_value, non-numeric or NaN).",
    param("operation", "string", "What to check.", enum=("records", "numeric_bounds")),
    param("records", "array", "The rows.", item_type="object"),
    param("required_fields", "array", "Fields that must be present and non-empty (records).",
          required=False, item_type="string"),
    param("unique_key", "string", "A field whose values must be unique (records).", required=False,
          max_length=200),
    param("field", "string", "The numeric field to bound (numeric_bounds).", required=False,
          max_length=200),
    param("min_value", "number", "Lower bound (numeric_bounds).", required=False),
    param("max_value", "number", "Upper bound (numeric_bounds).", required=False),
    **_DATA,
)
async def validate_data_records(
    operation: str,
    records: list,
    required_fields: list | None = None,
    unique_key: str | None = None,
    field: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
) -> str:
    name = "validate_data_records"
    rows = _objects(name, "records", records)
    if operation == "numeric_bounds":
        if not field:
            raise ToolExecutionError("numeric_bounds needs 'field'", tool=name)
        return await _run(
            DataQualityAndValidationEngine.check_numeric_bounds,
            rows, field, min_value=min_value, max_value=max_value,
        )
    return await _run(
        DataQualityAndValidationEngine.validate_records,
        rows,
        required_fields=[str(f) for f in _items(name, "required_fields", required_fields)],
        unique_key=unique_key,
    )


@tool(
    "evaluate_quality_gate",
    "Decide whether a change may merge from check results you already have. A check you leave out "
    "counts as NOT RUN: security, tests, coverage and complexity are required by default and a "
    "missing one fails the gate. operation: readiness (adds a weighted score and blockers) or gate.",
    param("operation", "string", "readiness (default) or gate.", required=False,
          enum=("readiness", "gate"), default="readiness"),
    param("security_pass", "boolean", "Security checks passed.", required=False),
    param("tests_pass", "boolean", "Tests passed.", required=False),
    param("coverage_pass", "boolean", "Coverage met its threshold.", required=False),
    param("complexity_pass", "boolean", "Complexity within limits.", required=False),
    param("lint_pass", "boolean", "Lint clean (optional check).", required=False),
    param("architecture_pass", "boolean", "Architecture rules held (optional check).", required=False),
    param("required_checks", "array", "Replace the required set (names: security, tests, coverage, "
          "complexity, lint, architecture).", required=False, item_type="string"),
    param("blocking_checks", "array", "Checks whose failure blocks regardless of score "
          "(default security, tests).", required=False, item_type="string"),
    param("min_score_threshold", "number", "Minimum weighted score 0-100 (default 80).",
          required=False, default=80.0, minimum=0, maximum=100),
    **_CODE,
)
async def evaluate_quality_gate(
    operation: str = "readiness",
    security_pass: bool | None = None,
    tests_pass: bool | None = None,
    coverage_pass: bool | None = None,
    complexity_pass: bool | None = None,
    lint_pass: bool | None = None,
    architecture_pass: bool | None = None,
    required_checks: list | None = None,
    blocking_checks: list | None = None,
    min_score_threshold: float = 80.0,
) -> str:
    checks = dict(
        security_pass=security_pass, tests_pass=tests_pass, coverage_pass=coverage_pass,
        complexity_pass=complexity_pass, lint_pass=lint_pass, architecture_pass=architecture_pass,
        required_checks=[str(c) for c in required_checks] if required_checks is not None else None,
    )
    try:
        if operation == "gate":
            return await _run(QualityGateAggregator.evaluate_checks, **checks)
        return await _run(
            QualityGateAggregator.build_release_readiness_report,
            min_score_threshold=min_score_threshold,
            blocking_checks=[str(c) for c in blocking_checks] if blocking_checks is not None else None,
            **checks,
        )
    except ValueError as exc:  # unknown check name
        raise ToolExecutionError(str(exc), tool="evaluate_quality_gate") from exc


@tool(
    "check_model_drift",
    "Detect model degradation between a baseline and a current run. operation: accuracy (drop in "
    "an accuracy/F1-style score beyond max_allowed_drop) or mean_shift (relative shift in the mean "
    "of two samples, e.g. confidence or latency). Invalid or non-finite input reports drift "
    "instead of passing silently.",
    param("operation", "string", "What to compare.", enum=("accuracy", "mean_shift")),
    param("baseline_accuracy", "number", "Baseline score (accuracy).", required=False),
    param("current_accuracy", "number", "Current score (accuracy).", required=False),
    param("max_allowed_drop", "number", "Largest acceptable drop (default 0.10).", required=False,
          default=0.10, minimum=0),
    param("baseline_values", "array", "Baseline sample (mean_shift).", required=False, item_type="number"),
    param("current_values", "array", "Current sample (mean_shift).", required=False, item_type="number"),
    param("max_rel_shift", "number", "Largest acceptable relative shift (default 0.20).",
          required=False, default=0.20, minimum=0),
    **_DATA,
)
async def check_model_drift(
    operation: str,
    baseline_accuracy: float | None = None,
    current_accuracy: float | None = None,
    max_allowed_drop: float = 0.10,
    baseline_values: list | None = None,
    current_values: list | None = None,
    max_rel_shift: float = 0.20,
) -> str:
    name = "check_model_drift"
    if operation == "accuracy":
        if baseline_accuracy is None or current_accuracy is None:
            raise ToolExecutionError("accuracy needs baseline_accuracy and current_accuracy", tool=name)
        return await _run(
            ModelDriftAndPerformanceMonitor.check_accuracy_drift,
            baseline_accuracy, current_accuracy, max_allowed_drop=max_allowed_drop,
        )
    return await _run(
        ModelDriftAndPerformanceMonitor.check_mean_shift,
        _items(name, "baseline_values", baseline_values),
        _items(name, "current_values", current_values),
        max_rel_shift=max_rel_shift,
    )


@tool(
    "synthesize_research",
    "Structure research notes you already gathered: de-duplicate sources by URL/title, list key "
    "points, and flag topics where claims disagree (any two different statements on one topic -- "
    "a prompt to look closer, not a semantic contradiction check). It does no searching.",
    param("research_topic", "string", "What the research is about.", max_length=500),
    param("sources", "array", "Each item: {title, url, summary, confidence}.", required=False,
          item_type="object"),
    param("claims", "array", "Each item: {topic, statement, source}.", required=False,
          item_type="object"),
    category=ToolCategory.KNOWLEDGE, sensitivity=ToolSensitivity.PERSONAL, timeout_s=10.0,
)
async def synthesize_research(
    research_topic: str, sources: list | None = None, claims: list | None = None
) -> str:
    name = "synthesize_research"
    return await _run(
        AutonomousResearchAndSynthesisTool.synthesize_report,
        research_topic,
        sources=_objects(name, "sources", sources),
        claims=_objects(name, "claims", claims),
    )


@tool(
    "plan_rollout",
    "Draft a structured deployment-and-rollback plan for a service: target and previous version, "
    "the health checks to run after, and whether rollback is possible. Produces the plan only; "
    "it deploys nothing.",
    param("service_name", "string", "The service being released.", max_length=200),
    param("target_version", "string", "The version being rolled out.", max_length=100),
    param("previous_version", "string", "The version to roll back to, if any.", required=False,
          max_length=100),
    param("health_checks", "array", "Paths or commands that prove the release is healthy "
          "(default ['/health']).", required=False, item_type="string"),
    **_CODE,
)
async def plan_rollout(
    service_name: str,
    target_version: str,
    previous_version: str | None = None,
    health_checks: list | None = None,
) -> str:
    return await _run(
        DeploymentReleaseManager.plan_rollout,
        service_name,
        target_version,
        previous_version=previous_version,
        health_checks=[str(h) for h in _items("plan_rollout", "health_checks", health_checks)] or None,
    )


_ANALYSIS_TOOLS: tuple[Tool, ...] = (
    analyze_python_ast,
    analyze_code_complexity,
    verify_python_symbols,
    review_code_diff,
    scan_code_security,
    analyze_change_impact,
    generate_diagram,
    check_requirements,
    scan_dependency_licenses,
    compare_api_specs,
    validate_structured_text,
    inspect_sql,
    analyze_logs,
    validate_data_records,
    evaluate_quality_gate,
    check_model_drift,
    synthesize_research,
    plan_rollout,
)

#: Names, for persona grants (``agents/registry.py``) and the tests that pin them.
ANALYSIS_TOOL_NAMES: frozenset[str] = frozenset(t.spec.name for t in _ANALYSIS_TOOLS)


def build_analysis_tools() -> list[Tool]:
    """The pure analysis tools: no capability, no I/O, no state."""
    return list(_ANALYSIS_TOOLS)
