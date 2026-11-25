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
import math
from typing import Any, Callable, Mapping

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool
from .base import param, tool
from .open.api_contract_validator import ApiContractValidator
from .open.api_ext import as_json_text as api_json_text, classify_changes, generate_tests, load_spec, validate_spec
from .open.ast_code_analyzer import ASTCodeAnalyzer
from .open.autonomous_research_and_synthesis_tool import AutonomousResearchAndSynthesisTool
from .open.complexity_and_duplication_analyzer import ComplexityAndDuplicationAnalyzer
from .open.data_quality_and_validation_engine import DataQualityAndValidationEngine
from .open.database_intelligence_tool import DatabaseIntelligenceTool
from .open.dependency_license_compliance_scanner import DependencyLicenseComplianceScanner
from .open.dependency_manager_validator import DependencyManagerValidator
from .open.deployment_release_manager import DeploymentReleaseManager
from .open.gate_ext import evaluate as gate_evaluate
from .open.data_ext import check_formats, check_types, detect_corrupted, detect_outliers, profile
from .open.diagram_ext import (
    architecture_from_modules, er_from_sql, flowchart_from_source, sequence_from_source, validate_deep,
)
from .open.mermaid_diagram_generator import MermaidDiagramGenerator
from .open.model_drift_and_performance_monitor import ModelDriftAndPerformanceMonitor
from .open.observability_incident_analyzer import ObservabilityIncidentAnalyzer
from .open.peer_code_reviewer_agent import PeerCodeReviewerAgent
from .open.quality_gate_aggregator import QualityGateAggregator
from .open.regression_impact_analyzer import RegressionImpactAnalyzer
from .open.release_ext import release_checklist
from .open.telemetry_ext import analyze_incident, parse_structured_logs
from .open.review_ext import deep_review
from .open.sql_schema_ext import generate_select, parse_schema, query_smells, schema_report
from .open.security_vulnerability_scanner import SecurityVulnerabilityScanner
from .open.symbol_and_import_verifier import SymbolAndImportVerifier

__all__ = ["build_analysis_tools", "ANALYSIS_TOOL_NAMES"]

#: Characters of source/diff/spec text one call may carry. Bounds the regex and AST work.
_MAX_TEXT = 200_000
#: Rows/lines/items one call may carry.
_MAX_ITEMS = 20_000

_CODE = dict(category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.PERSONAL, timeout_s=10.0)
_DATA = dict(category=ToolCategory.DATA, sensitivity=ToolSensitivity.PERSONAL, timeout_s=10.0)


#: A result longer than this is shortened by *us*, not by the registry. The registry cuts a long
#: string at ``max_result_chars`` (16,000 for these tools) wherever that falls -- mid-JSON -- and
#: the caller then holds text that no longer parses; here whole list items are dropped instead and
#: the result says so (``truncated``).
_MAX_RESULT = 15_000
#: Longest line ``scan_code_security`` accepts. Its pattern scan grows with the *square* of the number
#: of matches on one line (ten thousand matches on one line: 27 s of CPU on a thread no timeout can stop),
#: and real source has no line near this long.
_MAX_LINE = 2_000


def _finite(value: Any) -> Any:
    """``value`` with every NaN/Infinity replaced by its name as a string.

    ``json.dumps`` writes them as bare ``NaN``/``Infinity``, which is not JSON: a strict parser (the
    app's, a browser's) rejects the whole result for one overflowed mean.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(k): _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_finite(v) for v in value]
    return value


def _lists(value: Any, found: list[list]) -> None:
    if isinstance(value, dict):
        for v in value.values():
            _lists(v, found)
    elif isinstance(value, list):
        found.append(value)
        for v in value:
            _lists(v, found)


def _strings(value: Any, limit: int) -> bool:
    """Cut every string longer than ``limit`` in place; True if any was cut."""
    cut = False
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str) and len(v) > limit:
                value[k] = v[:limit] + "...[cut]"
                cut = True
            else:
                cut = _strings(v, limit) or cut
    elif isinstance(value, list):
        for i, v in enumerate(value):
            if isinstance(v, str) and len(v) > limit:
                value[i] = v[:limit] + "...[cut]"
                cut = True
            else:
                cut = _strings(v, limit) or cut
    return cut


def _dump(result: Any) -> str:
    """The result as JSON text that always parses and always fits one tool result."""
    safe = _finite(result)
    text = json.dumps(safe, ensure_ascii=False, default=str)
    if len(text) <= _MAX_RESULT:
        return text
    if not isinstance(safe, dict):
        safe = {"result": safe}
    omitted = 0
    limit = 2_000
    for _ in range(200):
        lists: list[list] = []
        _lists(safe, lists)
        longest = max(lists, key=len, default=None)
        if longest is not None and len(longest) > 1:
            keep = len(longest) // 2
            omitted += len(longest) - keep
            del longest[keep:]
        elif limit >= 20:
            _strings(safe, limit)  # nothing left to drop: shorten the long strings instead
            limit //= 2
        else:
            break
        text = json.dumps({**safe, "truncated": True, "omitted_items": omitted}, ensure_ascii=False, default=str)
        if len(text) <= _MAX_RESULT:
            return text
    return json.dumps({"truncated": True, "error": "the result was too large to return"})


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
    # The count alone is no bound: 20,000 items of a megabyte each is twenty gigabytes.
    if sum(len(str(v)) for v in value) > _MAX_TEXT:
        raise ToolExecutionError(f"'{name}' is larger than {_MAX_TEXT:,} characters in all", tool=tool_name)
    return value


def _bounded(tool_name: str, name: str, value: Any) -> Any:
    """``value`` (an object argument) if it is no larger than a text argument may be."""
    if value is not None and len(json.dumps(value, default=str)) > _MAX_TEXT:
        raise ToolExecutionError(f"'{name}' is larger than {_MAX_TEXT:,} characters", tool=tool_name)
    return value


def _short_lines(tool_name: str, name: str, text: str | None) -> None:
    if text is not None:
        for number, line in enumerate(text.split("\n"), start=1):
            if len(line) > _MAX_LINE:
                raise ToolExecutionError(
                    f"'{name}' line {number} is {len(line):,} characters; the limit is {_MAX_LINE:,}. "
                    "Break long lines up (or pass the relevant part) and call again.", tool=tool_name,
                )


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
    if expected_arities is not None:
        problem = SymbolAndImportVerifier.arity_spec_error(expected_arities)
        if problem:
            raise ToolExecutionError(problem, tool="verify_python_symbols")
    return await _run(
        SymbolAndImportVerifier.verify_source,
        source_code,
        check_runtime_imports=False,  # would import modules the source names: never from a tool
        expected_arities=_bounded("verify_python_symbols", "expected_arities", expected_arities),
        check_undefined_names=check_undefined_names,
    )


@tool(
    "review_code_diff",
    "Rule-based review of a unified diff and/or Python source: debug prints, TODO markers, "
    "broad `except Exception`, missing docstrings and (deep, default on) bug patterns found on the "
    "AST (mutable defaults, == None, swallowed exceptions, unreachable code, ...), complexity over "
    "the limits and layer-import violations, each with a line number. A rule engine, not an "
    "independent reviewer: it does not replace reading the change.",
    param("diff_text", "string", "A unified diff (e.g. the output of the diff tool).",
          required=False, max_length=_MAX_TEXT),
    param("source_code", "string", "Python source to check for convention issues.",
          required=False, max_length=_MAX_TEXT),
    param("filename", "string", "Name to report findings against (default '<string>'). A relative "
          "path like aura_core/domain/x.py also names the module for the layer check.",
          required=False, default="<string>", max_length=500),
    param("deep", "boolean", "Add bug-pattern, complexity and architecture findings (default true).",
          required=False, default=True),
    param("module_name", "string", "Dotted module name for the layer check when filename does not "
          "give it.", required=False, max_length=300),
    param("layer_rules", "object", "Forbidden imports per layer, {layer: [layers it may not import]}; "
          "default is the platform's own rules.", required=False),
    param("max_complexity", "integer", "Cyclomatic complexity above which a function is flagged "
          "(default 10).", required=False, default=10, minimum=1, maximum=200),
    **_CODE,
)
async def review_code_diff(
    diff_text: str | None = None,
    source_code: str | None = None,
    filename: str = "<string>",
    deep: bool = True,
    module_name: str | None = None,
    layer_rules: dict | None = None,
    max_complexity: int = 10,
) -> str:
    name = "review_code_diff"
    _need(name, diff_text=diff_text, source_code=source_code)
    rules = _bounded(name, "layer_rules", layer_rules)
    if rules is not None and not (isinstance(rules, dict) and all(
            isinstance(v, list) for v in rules.values())):
        raise ToolExecutionError("layer_rules must be {layer: [forbidden layers]}", tool=name)

    def review() -> dict:
        result = PeerCodeReviewerAgent.full_review(
            diff_text=diff_text, source_code=source_code, filename=filename)
        if deep:
            extra = deep_review(
                source_code=source_code, diff_text=diff_text, filename=filename, module_name=module_name,
                layer_rules={str(k): [str(x) for x in v] for k, v in rules.items()} if rules else None,
                max_complexity=max_complexity)
            known = {(f.get("file"), f.get("line"), f.get("code")) for f in result["findings"]}
            fresh = [f for f in extra["findings"] if (f["file"], f["line"], f["code"]) not in known]
            result["deep_review"] = {**extra, "findings": fresh}
            blocking = sum(1 for f in fresh if f["severity"] in ("MAJOR", "CRITICAL"))
            result["major_issues"] += sum(1 for f in fresh if f["severity"] == "MAJOR")
            result["critical_issues"] += sum(1 for f in fresh if f["severity"] == "CRITICAL")
            result["total_findings"] += len(fresh)
            if blocking:
                result["approved"] = False
            if extra["errors"]:
                errs = result.setdefault("errors", [])
                errs.extend(e for e in extra["errors"] if e not in errs)
        return result

    return await _run(review)


@tool(
    "scan_code_security",
    "Pattern-scan source and/or a git diff for insecure constructs and hardcoded secrets. Languages: "
    "Python, JavaScript/TypeScript, Go, Java/Kotlin, shell, Dockerfile, Kubernetes/Compose/GitHub-Actions "
    "YAML and Terraform (the language is read from the text, or pass `language`). Recognises about fifty "
    "credential formats. Matched secret values are never shown. Static; nothing is executed, and a clean "
    "result is not proof the code is secure (it finds constructs, not data flow).",
    param("source_code", "string", "Source text to scan.", required=False, max_length=_MAX_TEXT),
    param("diff_text", "string", "A unified diff; only added lines are scanned, each file by its own language.",
          required=False, max_length=_MAX_TEXT),
    param("language", "string", "The language of source_code when it cannot be told from the text: python, "
          "javascript, go, java, shell, dockerfile, yaml, terraform.", required=False, max_length=20),
    **_CODE,
)
async def scan_code_security(source_code: str | None = None, diff_text: str | None = None,
                             language: str | None = None) -> str:
    _need("scan_code_security", source_code=source_code, diff_text=diff_text)
    _short_lines("scan_code_security", "source_code", source_code)
    _short_lines("scan_code_security", "diff_text", diff_text)
    return await _run(
        SecurityVulnerabilityScanner.full_scan,
        source_code=source_code,
        diff_text=diff_text,
        use_bandit=False,  # spawns a process
        language=language,
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
    "Produce Mermaid text. diagram_type: class_from_source, er_from_sql (DDL/Alembic in source_code), "
    "flowchart_from_source and sequence_from_source (source_code + function), architecture_from_modules "
    "({path: source} in modules), or from a spec you write: class, flowchart, sequence, er; validate "
    "checks diagram_code.",
    param("diagram_type", "string", "Which diagram to produce.",
          enum=("class_from_source", "er_from_sql", "flowchart_from_source", "sequence_from_source",
                "architecture_from_modules", "class", "flowchart", "sequence", "er", "validate")),
    param("source_code", "string", "Python source, or SQL/Alembic text for er_from_sql.", required=False,
          max_length=_MAX_TEXT),
    param("function", "string", "Function to draw (flowchart/sequence_from_source).", required=False,
          max_length=200),
    param("modules", "object", "{path: Python source} (architecture_from_modules).", required=False),
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
    function: str | None = None,
    modules: dict | None = None,
) -> str:
    name = "generate_diagram"
    spec = _bounded(name, "spec", spec) or {}

    def listed(key: str, of: type = dict) -> list:
        """``spec[key]`` as a list of ``of`` (a bare string would be iterated character by character)."""
        value = spec.get(key)
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(i, of) for i in value):
            raise ToolExecutionError(f"spec.{key} must be an array of {of.__name__}s", tool=name)
        return value

    def keyed(key: str, *required: str, any_of: tuple[str, ...] = ()) -> list:
        """``listed(key)``, but every item must carry the keys the generator reads (no silent drops)."""
        items = listed(key)
        for index, item in enumerate(items):
            for need in required:
                if not item.get(need):
                    raise ToolExecutionError(
                        f"spec.{key}[{index}] is missing '{need}' (got keys: {sorted(item) or 'none'})",
                        tool=name,
                    )
            if any_of and not any(item.get(k) for k in any_of):
                raise ToolExecutionError(
                    f"spec.{key}[{index}] needs one of {' or '.join(repr(k) for k in any_of)} "
                    f"(got keys: {sorted(item) or 'none'})", tool=name,
                )
        return items

    if diagram_type == "class_from_source":
        _need(name, source_code=source_code)
        return await _run(MermaidDiagramGenerator.generate_class_diagram_from_source, source_code)
    if diagram_type == "validate":
        _need(name, diagram_code=diagram_code)
        return await _run(validate_deep, diagram_code)
    if diagram_type in ("er_from_sql", "flowchart_from_source", "sequence_from_source"):
        _need(name, source_code=source_code)
        derive = {"er_from_sql": lambda: er_from_sql(source_code),
                  "flowchart_from_source": lambda: flowchart_from_source(source_code, function),
                  "sequence_from_source": lambda: sequence_from_source(source_code, function)}[diagram_type]
        return await _run(derive)
    if diagram_type == "architecture_from_modules":
        found = _bounded(name, "modules", modules)
        if not isinstance(found, dict) or not found or not all(isinstance(v, str) for v in found.values()):
            raise ToolExecutionError("architecture_from_modules needs modules as {path: source text}", tool=name)
        return await _run(architecture_from_modules, {str(k): v for k, v in found.items()})

    def build() -> dict:
        gen = MermaidDiagramGenerator
        if diagram_type == "class":
            text = gen.generate_class_diagram(
                class_names=listed("class_names", str) or None,
                relations=keyed("relations", "source", "target") or None,
                class_details=keyed("class_details", "name") or None,
            )
        elif diagram_type == "flowchart":
            text = gen.generate_flowchart(
                keyed("nodes", any_of=("id", "label")), keyed("edges", "from", "to"), direction=str(spec.get("direction", "TD"))
            )
        elif diagram_type == "sequence":
            text = gen.generate_sequence_diagram(
                listed("participants", str), keyed("messages", "from", "to")
            )
        else:  # er
            entities = spec.get("entities") or {}
            if not isinstance(entities, dict):
                raise ToolExecutionError("spec.entities must be an object of {Name: [fields]}", tool=name)
            text = gen.generate_er_diagram(entities, keyed("relationships", "left", "right") or None)
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
    entries: list[dict[str, str]] = []
    for index, p in enumerate(_objects(name, "packages", packages)):
        entry = {str(k): str(v) for k, v in p.items()}
        if not entry.get("package", "").strip():
            alias = entry.get("name", "").strip()
            if not alias:
                raise ToolExecutionError(
                    f"packages[{index}] has no 'package' (or 'name'); each item must be "
                    f"{{package, license}} (got keys: {sorted(entry) or 'none'})", tool=name,
                )
            entry["package"] = alias
        entries.append(entry)
    return await _run(
        DependencyLicenseComplianceScanner.scan_licenses,
        entries,
        forbidden_tokens=[str(x) for x in forbidden] if forbidden is not None else None,
        allowed_licenses=[str(x) for x in allowed] if allowed is not None else None,
    )


@tool(
    "compare_api_specs",
    "Work with OpenAPI/contract documents (JSON or YAML). operation: validate_spec (structure, $refs, "
    "path params), breaking_changes (old_spec vs new_spec incl. request/response schema changes, "
    "semver bump), diff (route-level), deprecated_endpoints, generate_tests (pytest integration text), "
    "validate_payload.",
    param("operation", "string", "What to do.",
          enum=("validate_spec", "breaking_changes", "diff", "deprecated_endpoints", "generate_tests",
                "validate_payload")),
    param("spec", "string", "A JSON/YAML spec (validate_spec, deprecated_endpoints, generate_tests).",
          required=False, max_length=_MAX_TEXT),
    param("old_spec", "string", "The earlier spec (diff, breaking_changes).", required=False, max_length=_MAX_TEXT),
    param("new_spec", "string", "The later spec (diff, breaking_changes).", required=False, max_length=_MAX_TEXT),
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
    if operation in ("validate_spec", "generate_tests"):
        _need(name, spec=spec)
        parsed, problem, _fmt = load_spec(spec)
        if parsed is None:
            raise ToolExecutionError(f"spec: {problem}", tool=name)
        return await _run(validate_spec if operation == "validate_spec" else generate_tests, parsed)
    if operation == "breaking_changes":
        if old_spec is None or new_spec is None:
            raise ToolExecutionError("breaking_changes needs both old_spec and new_spec", tool=name)
        old_parsed, old_problem, _ = load_spec(old_spec)
        new_parsed, new_problem, _ = load_spec(new_spec)
        if old_parsed is None or new_parsed is None:
            raise ToolExecutionError(f"{'old_spec' if old_parsed is None else 'new_spec'}: "
                                     f"{old_problem if old_parsed is None else new_problem}", tool=name)
        return await _run(classify_changes, old_parsed, new_parsed)
    if operation == "deprecated_endpoints":
        _need(name, spec=spec)
        return await _run(ApiContractValidator.check_deprecated_endpoints, api_json_text(spec))
    if operation == "diff":
        if old_spec is None or new_spec is None:
            raise ToolExecutionError("diff needs both old_spec and new_spec", tool=name)

        def route_diff() -> dict:
            result = ApiContractValidator.compare_route_sets(api_json_text(old_spec), api_json_text(new_spec))
            result["all_breaking_changes"] = [
                *[f"Removed path '{p}'" for p in result.get("removed_paths", [])],
                *[f"Removed operation '{o}'" for o in result.get("removed_operations", [])],
                *result.get("breaking_changes", []),
            ]
            return result

        return await _run(route_diff)
    if payload is None:
        raise ToolExecutionError("validate_payload needs payload", tool=name)
    return await _run(
        ApiContractValidator.validate_payload_against_schema,
        _bounded(name, "payload", payload),
        [str(f) for f in _items(name, "required_fields", required_fields)],
        _bounded(name, "field_types", field_types),
    )


@tool(
    "inspect_sql",
    "Analyse SQL text only (never connects, so no EXPLAIN plans). operation: lint (default: unsafe "
    "statements, tables, index hints, query smells), schema (parse DDL/Alembic text: tables, "
    "relationships, indexes, missing FK indexes), smells (vs schema_info), generate_select.",
    param("query", "string", "The SQL text (schema: the DDL).", required=False, max_length=_MAX_TEXT),
    param("schema_info", "string", "Schema as SQL DDL or Alembic source.", required=False,
          max_length=_MAX_TEXT),
    param("operation", "string", "What to do (default lint).", required=False, default="lint",
          enum=("lint", "schema", "smells", "generate_select")),
    param("table", "string", "generate_select: table.", required=False, max_length=200),
    param("columns", "array", "generate_select: columns.", required=False, item_type="string"),
    param("filters", "array", "generate_select: equality-filter columns (bound parameters).",
          required=False, item_type="string"),
    param("order_by", "string", "generate_select: order column.", required=False, max_length=200),
    param("limit", "integer", "generate_select: row limit.", required=False, default=100,
          minimum=1, maximum=10000),
    **_DATA,
)
async def inspect_sql(
    query: str | None = None,
    schema_info: str | None = None,
    operation: str = "lint",
    table: str | None = None,
    columns: list | None = None,
    filters: list | None = None,
    order_by: str | None = None,
    limit: int = 100,
) -> str:
    name = "inspect_sql"
    if operation == "schema":
        text = schema_info if schema_info is not None else query
        if text is None:
            raise ToolExecutionError("schema needs the DDL in 'query' or 'schema_info'", tool=name)
        return await _run(schema_report, text)
    if operation == "generate_select":
        if schema_info is None or not table:
            raise ToolExecutionError("generate_select needs schema_info and table", tool=name)
        return await _run(
            lambda: generate_select(
                parse_schema(schema_info), table, [str(c) for c in _items(name, "columns", columns)] or None,
                [str(f) for f in _items(name, "filters", filters)] or None, order_by, limit))
    if query is None:
        raise ToolExecutionError("provide 'query'", tool=name)

    def lint() -> dict:
        schema = parse_schema(schema_info) if schema_info else None
        if operation == "smells":
            smells = query_smells(query, schema if schema and schema["tables"] else None)
            return {"smells": smells, "smells_count": len(smells), "schema_tables": sorted(schema["tables"]) if schema else [],
                    "plan_available": False}
        result = DatabaseIntelligenceTool.inspect_sql(query, schema_info)
        result["all_tables"] = DatabaseIntelligenceTool.extract_table_names(query)
        result["index_hints"] = DatabaseIntelligenceTool.suggest_index_hints(query)
        smells = query_smells(query, schema if schema and schema["tables"] else None)
        result["smells"] = smells
        result["plan_available"] = False
        if any(s["code"] == "explain_analyze_executes" for s in smells):
            result["is_safe_sql"] = False
        return result

    return await _run(lint)


# --------------------------------------------------------------------------------------
# Logs, data and metrics
# --------------------------------------------------------------------------------------


@tool(
    "analyze_logs",
    "Analyse log lines and metrics you pass in (no live telemetry access). operation: incident "
    "(logs and/or Prometheus text: ranked root-cause hypotheses with evidence lines), structured "
    "(JSON/logfmt logs: levels, error signatures, bursts, traces), metrics (Prometheus text anomalies), "
    "errors, root_cause (first incident), latency (outliers).",
    param("operation", "string", "What to do.",
          enum=("incident", "structured", "metrics", "errors", "root_cause", "latency")),
    param("logs", "array", "Log lines.", required=False, item_type="string"),
    param("metrics_text", "string", "Prometheus /metrics text.", required=False, max_length=_MAX_TEXT),
    param("baseline_metrics_text", "string", "Earlier /metrics text to diff against.", required=False,
          max_length=_MAX_TEXT),
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
    metrics_text: str | None = None,
    baseline_metrics_text: str | None = None,
) -> str:
    name = "analyze_logs"
    if operation == "latency":
        _need(name, latencies_ms=latencies_ms)
        return await _run(
            ObservabilityIncidentAnalyzer.extract_latency_outliers,
            [float(v) for v in _items(name, "latencies_ms", latencies_ms)],
            threshold_ms=threshold_ms,
        )
    if operation in ("incident", "metrics"):
        if logs is None and metrics_text is None:
            raise ToolExecutionError("provide at least one of: logs, metrics_text", tool=name)
        lines = [str(line) for line in _items(name, "logs", logs)] if operation == "incident" else None
        if operation == "metrics" and metrics_text is None:
            raise ToolExecutionError("metrics needs metrics_text", tool=name)
        return await _run(analyze_incident, lines, metrics_text, baseline_metrics_text, slow_s=threshold_ms / 1000.0)
    _need(name, logs=logs)
    lines = [str(line) for line in _items(name, "logs", logs)]
    if operation == "structured":
        def structured() -> dict:
            result = parse_structured_logs(lines)
            result.pop("_records", None)
            return result

        return await _run(structured)
    if operation == "root_cause":
        return await _run(ObservabilityIncidentAnalyzer.identify_root_cause, lines)
    return await _run(ObservabilityIncidentAnalyzer.parse_error_logs, lines)


@tool(
    "validate_data_records",
    "Check tabular rows (a list of objects). operation: records (null/blank/NaN in required_fields, "
    "duplicate unique_key), numeric_bounds, formats (inconsistent value shapes, padding, case "
    "variants), corrupted (mojibake, truncated JSON, control chars, ragged rows), outliers, types, "
    "profile (everything, with a score).",
    param("operation", "string", "What to check.",
          enum=("records", "numeric_bounds", "formats", "corrupted", "outliers", "types", "profile")),
    param("records", "array", "The rows.", item_type="object"),
    param("required_fields", "array", "Fields that must be present and non-empty (records).",
          required=False, item_type="string"),
    param("unique_key", "string", "A field whose values must be unique (records).", required=False,
          max_length=200),
    param("field", "string", "The numeric field (numeric_bounds, outliers).", required=False,
          max_length=200),
    param("min_value", "number", "Lower bound (numeric_bounds).", required=False),
    param("max_value", "number", "Upper bound (numeric_bounds).", required=False),
    param("formats", "object", "{column: email|uuid|url|iso_date|iso_datetime|integer|decimal|phone|...}.",
          required=False),
    param("type_contracts", "object", "{column: string|integer|number|boolean|array|object}.", required=False),
    param("method", "string", "Outlier rule (default zscore).", required=False, enum=("zscore", "iqr", "mad")),
    param("threshold", "number", "Outlier threshold (z 3.0, iqr 1.5, mad 3.5).", required=False, minimum=0),
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
    formats: dict | None = None,
    type_contracts: dict | None = None,
    method: str = "zscore",
    threshold: float | None = None,
) -> str:
    name = "validate_data_records"
    rows = _objects(name, "records", records)
    pinned_formats = _bounded(name, "formats", formats)
    contracts = _bounded(name, "type_contracts", type_contracts)
    if operation == "formats":
        return await _run(check_formats, rows, {str(k): str(v) for k, v in (pinned_formats or {}).items()})
    if operation == "corrupted":
        return await _run(detect_corrupted, rows)
    if operation == "outliers":
        if not field:
            raise ToolExecutionError("outliers needs 'field'", tool=name)
        return await _run(detect_outliers, rows, field, method, threshold)
    if operation == "types":
        return await _run(check_types, rows, {str(k): str(v) for k, v in contracts.items()} if contracts else None)
    if operation == "profile":
        return await _run(
            profile, rows, {str(k): str(v) for k, v in (pinned_formats or {}).items()} or None,
            {str(k): str(v) for k, v in contracts.items()} if contracts else None,
            [str(f) for f in _items(name, "required_fields", required_fields)] or None, unique_key)
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
    "Decide whether a change may merge from check results you already have; a check left out is "
    "NOT RUN and a required one fails the gate. Pass booleans, or `results` {check: boolean | the "
    "other tool's result} for security, tests, coverage, complexity, lint, architecture, review, "
    "mutation or a custom name; verdicts then carry evidence. operation: readiness or gate.",
    param("operation", "string", "readiness (default) or gate.", required=False,
          enum=("readiness", "gate"), default="readiness"),
    param("security_pass", "boolean", "Security passed.", required=False),
    param("tests_pass", "boolean", "Tests passed.", required=False),
    param("coverage_pass", "boolean", "Coverage met its threshold.", required=False),
    param("complexity_pass", "boolean", "Complexity within limits.", required=False),
    param("lint_pass", "boolean", "Lint clean.", required=False),
    param("architecture_pass", "boolean", "Architecture rules held.", required=False),
    param("results", "object", "{check: boolean | result object}, e.g. review: review_code_diff output.",
          required=False),
    param("weights", "object", "{check: weight} overriding score weights.", required=False),
    param("min_mutation_score", "number", "Mutation % that passes (default 50).", required=False,
          default=50.0, minimum=0, maximum=100),
    param("required_checks", "array", "Replace the required set (default security, tests, "
          "coverage, complexity).", required=False, item_type="string"),
    param("blocking_checks", "array", "Checks that block regardless of score (default security, "
          "tests).", required=False, item_type="string"),
    param("min_score_threshold", "number", "Minimum score 0-100 (default 80).",
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
    results: dict | None = None,
    weights: dict | None = None,
    min_mutation_score: float = 50.0,
) -> str:
    if not math.isfinite(min_score_threshold):
        # NaN compares false with everything: it answered "approved" with ready_to_merge false.
        raise ToolExecutionError("min_score_threshold must be a finite number", tool="evaluate_quality_gate")
    if results is not None or weights is not None:
        found = _bounded("evaluate_quality_gate", "results", results) or {}
        if not isinstance(found, dict) or not isinstance(weights or {}, dict):
            raise ToolExecutionError("results and weights must be objects", tool="evaluate_quality_gate")
        booleans = {"security": security_pass, "tests": tests_pass, "coverage": coverage_pass,
                    "complexity": complexity_pass, "lint": lint_pass, "architecture": architecture_pass}
        try:
            return await _run(
                gate_evaluate, operation=operation, booleans=booleans, results={str(k): v for k, v in found.items()},
                required=[str(c) for c in required_checks] if required_checks is not None else None,
                blocking=[str(c) for c in blocking_checks] if blocking_checks is not None else None,
                weights=_bounded("evaluate_quality_gate", "weights", weights), min_score=min_score_threshold,
                min_mutation_score=min_mutation_score,
            )
        except ValueError as exc:
            raise ToolExecutionError(str(exc), tool="evaluate_quality_gate") from exc
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
    "Plan a release (deploys nothing): versions, health checks, rollback possibility; give `changelog` "
    "and `migrations` for a go/no-go checklist (changelog entry, semver vs content, destructive or "
    "irreversible migrations, rollback safety, strategy). verify_release_health checks it afterwards.",
    param("service_name", "string", "The service being released.", max_length=200),
    param("target_version", "string", "The version being rolled out.", max_length=100),
    param("previous_version", "string", "The version to roll back to, if any.", required=False,
          max_length=100),
    param("health_checks", "array", "Paths or commands that prove the release is healthy "
          "(default ['/health']).", required=False, item_type="string"),
    param("changelog", "string", "CHANGELOG text (Keep a Changelog style).", required=False,
          max_length=_MAX_TEXT),
    param("migrations", "array", "Migrations in the release: {name, text?} (text is inspected).",
          required=False, item_type="object"),
    param("strategy", "string", "Deployment strategy.", required=False,
          enum=("blue_green", "canary", "rolling")),
    param("changed_files_count", "integer", "Files changed (for the risk score).", required=False, minimum=0),
    param("test_coverage_pct", "number", "Test coverage % (for the risk score).", required=False,
          minimum=0, maximum=100),
    **_CODE,
)
async def plan_rollout(
    service_name: str,
    target_version: str,
    previous_version: str | None = None,
    health_checks: list | None = None,
    changelog: str | None = None,
    migrations: list | None = None,
    strategy: str | None = None,
    changed_files_count: int | None = None,
    test_coverage_pct: float | None = None,
) -> str:
    def plan() -> dict:
        checks = [str(h) for h in _items("plan_rollout", "health_checks", health_checks)] or None
        result = DeploymentReleaseManager.plan_rollout(
            service_name, target_version, previous_version=previous_version, health_checks=checks)
        if previous_version and previous_version.strip() == (target_version or "").strip():
            # "Roll back to the version you are deploying" is a plan with no rollback in it.
            result = {**result, "warnings": ["previous_version equals target_version: there is no "
                                              "earlier version to roll back to."]}
        if changelog is not None or migrations is not None or strategy or changed_files_count is not None:
            items = _items("plan_rollout", "migrations", migrations)
            if not all(isinstance(m, (str, dict)) for m in items):
                raise ToolExecutionError("every migration must be a name or {name, text}", tool="plan_rollout")
            result["release_checklist"] = release_checklist(
                service=service_name, target_version=target_version, previous_version=previous_version,
                changelog=changelog, migrations=items, health_checks=checks or ["/health"],
                changed_files_count=changed_files_count, test_coverage_pct=test_coverage_pct, strategy=strategy)
        return result

    return await _run(plan)


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


def build_analysis_tools(workspace: Any = None) -> list[Tool]:
    """The pure analysis tools: no capability, no I/O, no state.

    With a ``workspace`` (``AURA_TOOLS_FILES_ROOT``) five of them gain optional arguments that read
    files under it (``tools/open_workspace.py``, ADR-043); called without those arguments they behave
    exactly as they do here. Without a workspace nothing changes.
    """
    tools = list(_ANALYSIS_TOOLS)
    if workspace is not None and str(workspace):
        from .open_workspace import extend_analysis_tools

        tools = extend_analysis_tools(tools, workspace)
    return tools
