"""ADR-042: fourteen pure-computation tools built on ``tools/open`` classes that had no caller.

Same risk profile as ``tools/analysis.py`` (ADR-036 group A): every tool takes text or JSON the
caller supplies and returns JSON text. None reads a path, spawns a process, makes a network call or
keeps state in this process. The two that look stateful are not:

* ``collect_verification_evidence`` returns the whole *dossier* (and its digest) and the caller
  passes it back; nothing is stored here, so one user's dossier cannot be reached by another.
* ``apply_plan_feedback`` takes the plan as a list of step strings and returns the revised list.

Where a class was too thin, the extra capability lives in ``open_pure_logic.py`` and is wrapped
here; ``tools/open`` is unchanged. Results are always JSON that parses (``analysis._dump`` drops
whole list items rather than cutting text mid-structure).
"""

from __future__ import annotations

import ast
import base64
import binascii
import hashlib
import re
from typing import Any

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool
from . import open_pure_logic as L
from .analysis import _MAX_TEXT, _bounded, _items, _need, _objects, _run, _short_lines
from .base import param, tool
from .open.acceptance_criteria_verifier import AcceptanceCriteriaVerifier
from .open.ambiguity_and_clarification_detector import AmbiguityAndClarificationDetector
from .open.architectural_consistency_checker import ArchitecturalConsistencyChecker
from .open.artifact_feedback_ingestor import ArtifactFeedbackIngestor
from .open.artifact_generator import ArtifactGenerator
from .open.automated_quiz_generator import AutomatedQuizGenerator
from .open.file_type_detector_router import FileTypeDetectorRouter
from .open.mobile_artifact_summarizer import MobileArtifactSummarizer
from .open.multi_language_ast_analyzer import MultiLanguageAstAnalyzer
from .open.scope_and_diff_guard import ScopeAndDiffGuard
from .open.source_content_explainer import SourceContentExplainer
from .open.structured_data_parser import StructuredDataParser
from .open.verification_evidence_collector import VerificationEvidenceCollector, _redact

__all__ = ["build_open_pure_tools", "OPEN_PURE_TOOL_NAMES"]

_CODE = dict(category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.PERSONAL, timeout_s=10.0)
_DATA = dict(category=ToolCategory.DATA, sensitivity=ToolSensitivity.PERSONAL, timeout_s=10.0)
#: Characters of a base64 payload ``detect_file_type`` accepts (about 2 MB of bytes).
_MAX_B64 = 2_800_000
_MAX_FILES = 300
_MAX_TASKS = 2_000
_MAX_CRITERIA = 200
_MAX_EVIDENCE = 500
_LIST_ALL = 40
_MAX_PLAN_STEPS = 300


def _fail(name: str, message: str) -> ToolExecutionError:
    return ToolExecutionError(message, tool=name)


# --------------------------------------------------------------------------------------
# Row 7 -- check_architecture
# --------------------------------------------------------------------------------------


def _targets_to_dotted(targets: list[str]) -> list[str]:
    return [re.sub(r"[/:]+", ".", t).strip(".") for t in targets if t]


def _resolve(target: str, modules: set[str], by_suffix: dict[str, list[str]]) -> str | None:
    """The scanned module ``target`` names: an exact match, else the one module whose dotted name ends
    with it (files passed as ``src/pkg/a.py`` are imported as ``pkg.a``). No prefix guessing: an import
    of a module that was not passed is not an edge to its package, or every ``__init__`` re-export
    would look like a cycle."""
    if target in modules:
        return target
    hits = by_suffix.get(target, [])
    return hits[0] if len(hits) == 1 else None


def _architecture(
    files: list[dict], import_map: dict | None, forbidden_rules: dict | None, layer_tiers: dict | None,
    check_conventions: bool, max_line: int,
) -> dict[str, Any]:
    graph: dict[str, list[str]] = {}
    link_graph: dict[str, list[str]] = {}
    parse_errors: list[dict[str, str]] = []
    findings: list[dict[str, Any]] = []
    languages: dict[str, int] = {}
    for entry in files:
        path = str(entry.get("path") or "")
        source = entry.get("source_code", entry.get("content", ""))
        if not path or not isinstance(source, str):
            raise _fail("check_architecture", "every item of 'files' needs a 'path' and a 'source_code' string")
        module = L.module_name_for_path(path)
        if path.endswith((".py", ".pyi")):
            imports, error, links = L.python_imports(source, module, is_package=path.endswith("__init__.py"))
            link_graph.setdefault(module, []).extend(links)
            if error:
                parse_errors.append({"file": path, "error": error})
            languages["python"] = languages.get("python", 0) + 1
        elif re.search(r"\.(js|jsx|ts|tsx|mjs|cjs)$", path):
            imports = L.js_imports(source, module)
            languages["javascript/typescript"] = languages.get("javascript/typescript", 0) + 1
        else:
            lang = L.resolve_language(None, path)
            if lang in L.BRACE_LANGUAGES:
                imports = _targets_to_dotted([i["module"] for i in L.scan_source(source, lang)["imports"]])
                languages[lang] = languages.get(lang, 0) + 1
            else:
                imports = []
                parse_errors.append({"file": path, "error": "language not supported; imports were not read"})
        graph[module] = list(dict.fromkeys([*graph.get(module, []), *imports]))
        if check_conventions:
            findings.extend(L.convention_findings(path, source, max_line=max_line, limit=40))
    for module, targets in (import_map or {}).items():
        if not isinstance(targets, list):
            raise _fail("check_architecture", f"import_map['{module}'] must be a list of module names")
        graph[str(module)] = list(dict.fromkeys([*graph.get(str(module), []), *map(str, targets)]))
    rules = forbidden_rules if forbidden_rules else None
    if rules is not None:
        rules = {str(k): [str(t) for t in v] for k, v in rules.items() if isinstance(v, list)}
    tiers = {str(k): int(v) for k, v in layer_tiers.items()} if layer_tiers else None
    audit = ArchitecturalConsistencyChecker.audit_project_modules(graph, forbidden_rules=rules)
    layering = ArchitecturalConsistencyChecker.detect_layering_violations(graph, layer_tiers=tiers)
    modules = set(graph)
    by_suffix: dict[str, list[str]] = {}
    for m in modules:
        parts = m.split(".")
        for i in range(1, len(parts)):
            by_suffix.setdefault(".".join(parts[i:]), []).append(m)
    internal = {
        m: [r for t in [*targets, *link_graph.get(m, [])] if (r := _resolve(t, modules, by_suffix)) and r != m]
        for m, targets in graph.items()
    }
    internal = {m: list(dict.fromkeys(t)) for m, t in internal.items()}
    cycles = ArchitecturalConsistencyChecker.detect_circular_package_dependencies(internal)
    report = ArchitecturalConsistencyChecker.generate_architecture_compliance_report(audit, layering, cycles)
    summary: dict[str, int] = {}
    for f in findings:
        summary[f["rule"]] = summary.get(f["rule"], 0) + 1
    consistent = bool(graph) and audit.get("is_consistent", False) and not layering and not cycles
    result: dict[str, Any] = {
        "is_consistent": consistent,
        "modules_checked": len(graph),
        "boundary_violations": audit.get("violations", [])[:100],
        "layer_violations": layering[:100],
        "cycles": cycles[:20],
        "unclassified_modules": audit.get("unclassified_modules", [])[:50],
        "rules": "custom" if rules or tiers else "AURA default layers (domain < core < application < adapters/infrastructure < api < ui)",
        "languages": languages,
        "parse_errors": parse_errors[:30],
        "report_markdown": report,
    }
    if check_conventions:
        result["convention_findings"] = findings[:150]
        result["convention_summary"] = summary
        result["conventions_ok"] = not any(f["severity"] in ("error", "warning") for f in findings)
    if audit.get("warnings"):
        result["warnings"] = audit["warnings"]
    return result


@tool(
    "check_architecture",
    "Check source files against layering rules, circular imports and coding conventions. Pass "
    "files [{path, source_code}] (Python, JS/TS and Go/Rust/Java/C#/Kotlin/Dart imports are read for "
    "you) and/or an import_map {module: [imports]}. The default rules are AURA's own layers "
    "(domain < core < application < adapters < api < ui); pass forbidden_rules {layer: [forbidden "
    "layers]} and layer_tiers {layer: number} for another project. Static; nothing is executed.",
    param("files", "array", "Source files: [{path, source_code}]. Paths decide each module's name "
          "and layer (aura_core/domain/x.py -> aura_core.domain.x).", required=False, item_type="object"),
    param("import_map", "object", "Optional {module: [modules it imports]} for code you cannot pass.",
          required=False),
    param("forbidden_rules", "object", "Optional {layer: [layers it must not import]}.", required=False),
    param("layer_tiers", "object", "Optional {layer: tier number}; a lower tier importing a higher "
          "one is a violation.", required=False),
    param("check_conventions", "boolean", "Also report naming, wildcard imports, bare except, mutable "
          "defaults, long lines (default true).", required=False, default=True),
    param("max_line_length", "integer", "Line length limit for the convention check (default 120).",
          required=False, default=120, minimum=40, maximum=400),
    **_CODE,
)
async def check_architecture(
    files: list | None = None, import_map: dict | None = None, forbidden_rules: dict | None = None,
    layer_tiers: dict | None = None, check_conventions: bool = True, max_line_length: int = 120,
) -> str:
    name = "check_architecture"
    _need(name, files=files, import_map=import_map)
    rows = _objects(name, "files", files)
    if len(rows) > _MAX_FILES:
        raise _fail(name, f"'files' has {len(rows)} entries; the limit is {_MAX_FILES}")
    for row in rows:
        _short_lines(name, f"files[{row.get('path')}]", row.get("source_code") if isinstance(row.get("source_code"), str) else None)
    for label, value in (("import_map", import_map), ("forbidden_rules", forbidden_rules), ("layer_tiers", layer_tiers)):
        _bounded(name, label, value)
    if layer_tiers and not all(isinstance(v, int) and not isinstance(v, bool) for v in layer_tiers.values()):
        raise _fail(name, "layer_tiers values must be integers")
    return await _run(_architecture, rows, import_map, forbidden_rules, layer_tiers, check_conventions, max_line_length)


# --------------------------------------------------------------------------------------
# Row 40 -- detect_ambiguity
# --------------------------------------------------------------------------------------


def _ambiguity(text: str, requirements: list[str], known: str, min_words: int) -> dict[str, Any]:
    combined = text if not known else f"{text}\n{known}"
    base = AmbiguityAndClarificationDetector.analyze_task_description(text, min_words=min_words)
    score = AmbiguityAndClarificationDetector.score_ambiguity(text)
    missing = AmbiguityAndClarificationDetector.check_missing_specifications(combined)
    findings = L.ambiguity_findings(text)
    reqs = requirements or [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    conflicts = L.requirement_conflicts(reqs)
    class_conflicts = AmbiguityAndClarificationDetector.detect_conflicting_requirements(reqs)
    for c in class_conflicts:
        conflicts.append({"conflict_type": c["conflict_type"], "requirements": [c["matched_pattern_1"], c["matched_pattern_2"]],
                          "shared_terms": [], "question": c["recommendation"]})
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(kind: str, severity: str, question: str, options: list[str] | None = None) -> None:
        if question not in seen:
            seen.add(question)
            item: dict[str, Any] = {"kind": kind, "severity": severity, "question": question}
            if options:
                item["options"] = options
            questions.append(item)

    for c in conflicts:
        add("conflict", "high", c["question"])
    for f in findings:
        add(f["kind"], f["severity"], f["question"])
    for m in L.missing_aspect_questions(missing["missing_aspects"]):
        add(m["kind"], m["severity"], m["question"], m["options"])
    if len(text.split()) < min_words:
        add("too_brief", "high", "Can you give the expected inputs, outputs and the component this applies to?")
    order = {"high": 0, "medium": 1, "low": 2}
    questions.sort(key=lambda q: order[q["severity"]])
    questions = questions[:15]
    for number, q in enumerate(questions, start=1):
        q["id"] = number
    high = [q for q in questions if q["severity"] == "high"]
    medium = [q for q in questions if q["severity"] == "medium"]
    if high:
        status = "NEEDS_CLARIFICATION"
    elif len(medium) >= 2:
        status = "CLARIFICATION_RECOMMENDED"
    else:
        status = "READY_FOR_EXECUTION"
    return {
        "readiness_status": status,
        "is_ambiguous": status != "READY_FOR_EXECUTION",
        "ambiguity_score": score["ambiguity_score"], "clarity_level": score["clarity_level"],
        "findings": findings, "conflicts": conflicts[:20],
        "present_aspects": missing["present_aspects"], "missing_aspects": missing["missing_aspects"],
        "clarification_questions": questions,
        "legacy_ambiguities": base["ambiguities"],
        "heuristic": True,
        "note": "Pattern-based (English and Arabic). A clean result means no pattern matched, not that the "
                "requirement is complete; each finding names the exact words so a person can judge it.",
    }


@tool(
    "detect_ambiguity",
    "Find vague, hedged, unmeasured, placeholder or conflicting requirements in a task description "
    "and return one targeted clarification question per finding (with the offending sentence), plus "
    "questions for missing aspects (errors, persistence, auth, performance, interface). Use it BEFORE "
    "implementing a request that reads loosely. Pattern-based, English and Arabic.",
    param("text", "string", "The task or requirements text.", max_length=_MAX_TEXT),
    param("requirements", "array", "Optional: the requirements as separate statements (conflicts are "
          "checked between them; default: the sentences of text).", required=False, item_type="string"),
    param("known_context", "string", "Optional known context; aspects it already settles are not asked about.",
          required=False, max_length=_MAX_TEXT),
    param("min_words", "integer", "Fewer words than this is 'too brief' (default 5).", required=False,
          default=5, minimum=1, maximum=100),
    **_CODE,
)
async def detect_ambiguity(
    text: str, requirements: list | None = None, known_context: str = "", min_words: int = 5
) -> str:
    reqs = [str(r) for r in _items("detect_ambiguity", "requirements", requirements)]
    return await _run(_ambiguity, text, reqs, known_context or "", min_words)


# --------------------------------------------------------------------------------------
# Row 41 -- verify_acceptance_criteria
# --------------------------------------------------------------------------------------

_PYTEST_COUNTS = (("passed", re.compile(r"(\d+)\s+passed")), ("failed", re.compile(r"(\d+)\s+failed")),
                  ("errors", re.compile(r"(\d+)\s+errors?\b")))
_EVIDENCE_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:\[(?P<box>[xX ])\]|(?P<tag>(?:PASS(?:ED)?|OK|FAIL(?:ED)?|ERROR)\b|[✅❌✓✗✔✘])\s*[:\-]?)\s*(?P<text>.+?)\s*$")


def _evidence_from_text(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in text.splitlines()[:5_000]:
        if len(out) >= _MAX_EVIDENCE:
            break
        m = _EVIDENCE_LINE.match(line)
        if m:
            tag = (m.group("tag") or "").upper()
            ok = (m.group("box") or "").lower() == "x" if m.group("box") is not None else tag in ("PASS", "PASSED", "OK", "✅", "✓", "✔")
            out[m.group("text")] = ok
            continue
        m2 = re.match(r"^(?P<name>\S+?)\s+(?P<res>PASSED|FAILED|ERROR)\b", line)
        if m2:
            out[m2.group("name").replace("::", " ")] = m2.group("res") == "PASSED"
    return out


def _acceptance(criteria: list[str], requirements_text: str, evidence: dict, evidence_text: str,
                test_summary: dict, test_output: str, minimum_coverage: float) -> dict[str, Any]:
    parsed = AcceptanceCriteriaVerifier.parse_criteria_from_markdown(requirements_text) if requirements_text else []
    all_criteria = list(dict.fromkeys([*(c.strip() for c in criteria if c.strip()), *parsed]))[:_MAX_CRITERIA]
    structured = AcceptanceCriteriaVerifier.convert_requirements_to_criteria(all_criteria)
    proof_type = {c["description"]: c["proof_type"] for c in structured}
    merged: dict[str, Any] = dict(evidence)
    merged.update(_evidence_from_text(evidence_text))
    summary = dict(test_summary)
    if test_output:
        for key, rx in _PYTEST_COUNTS:
            m = rx.search(test_output)
            if m and key not in summary:
                summary[key] = int(m.group(1))
    test_used: dict[str, Any] | None = None
    if summary:
        verdict = AcceptanceCriteriaVerifier.verify_against_test_results(all_criteria, summary, minimum_coverage)
        test_used = verdict["test_summary_used"]
        tests_ok = test_used["tests_ok"]
        for crit in all_criteria:
            low = crit.lower()
            if re.search(r"\bcoverage\b", low):
                merged.setdefault(crit, bool(test_used["coverage_percentage"] >= minimum_coverage))
            elif re.search(r"\b(?:tests?|pytest|test suite|unit tests?)\b", low):
                merged.setdefault(crit, tests_ok)
    result = AcceptanceCriteriaVerifier.verify_criteria(all_criteria, merged)
    badge_source = dict(result)
    result["criteria_source"] = {"given": len(criteria), "parsed_from_requirements_text": len(parsed)}
    if test_used is not None:
        result["test_summary_used"] = test_used
    actions: list[str] = []
    for crit in result["unverified_criteria"]:
        actions.append(f"Provide evidence for: {crit} (expected proof: {proof_type.get(crit, 'FUNCTIONAL_OUTPUT')})")
    for crit in result["contradicted_criteria"]:
        actions.append(f"Fix, then re-verify: {crit}")
    result["next_actions"] = actions[:40]
    result["can_mark_complete"] = bool(result["all_satisfied"])
    # a long checklist is reported by its exceptions: the lists name every failing criterion, the table the first 40
    if len(all_criteria) > _LIST_ALL:
        result["breakdown"] = {c: ok for c, ok in list(result["breakdown"].items())[:_LIST_ALL]}
        result["breakdown_truncated"] = True
        badge_source["breakdown"] = dict(list(badge_source["breakdown"].items())[:_LIST_ALL])
    result["unverified_criteria"] = result["unverified_criteria"][:60]
    result["contradicted_criteria"] = result["contradicted_criteria"][:60]
    shown = set(result["unverified_criteria"]) | set(result["contradicted_criteria"]) | set(result["breakdown"])
    result["proof_types"] = {c: t for c, t in proof_type.items() if c in shown}
    result["badge_report_markdown"] = AcceptanceCriteriaVerifier.generate_acceptance_badge_report(badge_source)
    return result


@tool(
    "verify_acceptance_criteria",
    "Turn requirements into testable criteria and check each one against the evidence you supply, "
    "before calling a task done. Criteria come from `criteria` and/or a Markdown requirements text "
    "(checklists, Given/When/Then, numbered or bulleted requirements). Evidence: an object "
    "{criterion: true/false/text}, free text lines such as 'PASS: login works' / '[x] ...', and/or "
    "pytest output or a summary {passed, failed, coverage_percentage}. Only affirmative evidence "
    "passes a criterion; no evidence is 'unverified', conflicting evidence is 'contradicted'.",
    param("criteria", "array", "Criteria, one statement each.", required=False, item_type="string"),
    param("requirements_text", "string", "Markdown with the requirements/acceptance criteria.",
          required=False, max_length=_MAX_TEXT),
    param("evidence", "object", "{criterion: true | false | 'what was observed'}.", required=False),
    param("evidence_text", "string", "Free-form evidence lines (PASS:/FAIL: prefixes, [x] boxes, "
          "'test_x PASSED').", required=False, max_length=_MAX_TEXT),
    param("test_output", "string", "Raw pytest output; counts are read from its summary line.",
          required=False, max_length=_MAX_TEXT),
    param("test_summary", "object", "{passed, failed, errors, coverage_percentage}.", required=False),
    param("minimum_coverage", "number", "Coverage percent a coverage criterion needs (default 80).",
          required=False, default=80.0, minimum=0, maximum=100),
    **_CODE,
)
async def verify_acceptance_criteria(
    criteria: list | None = None, requirements_text: str = "", evidence: dict | None = None,
    evidence_text: str = "", test_output: str = "", test_summary: dict | None = None,
    minimum_coverage: float = 80.0,
) -> str:
    name = "verify_acceptance_criteria"
    crit = [str(c) for c in _items(name, "criteria", criteria)]
    if not crit and not requirements_text.strip():
        raise _fail(name, "provide 'criteria' or a 'requirements_text' containing criteria")
    # the verifier compares every criterion with every piece of evidence, so both are counted
    evidence = _bounded(name, "evidence", evidence) or {}
    pieces = sum(len(v) if isinstance(v, (list, tuple, set)) else 1 for v in evidence.values())
    if len(crit) > _MAX_CRITERIA or pieces > _MAX_EVIDENCE:
        raise _fail(name, f"at most {_MAX_CRITERIA} criteria and {_MAX_EVIDENCE} evidence entries per call "
                          f"(got {len(crit)} and {pieces}); verify in batches")
    return await _run(
        _acceptance, crit, requirements_text, evidence, evidence_text,
        _bounded(name, "test_summary", test_summary) or {}, test_output, float(minimum_coverage),
    )


# --------------------------------------------------------------------------------------
# Row 45 -- check_diff_scope
# --------------------------------------------------------------------------------------


def _diff_scope(diff_text: str, task: str, allowed: list[str], forbidden: list[str], max_lines: int, secrets: bool) -> dict[str, Any]:
    guard = ScopeAndDiffGuard.check_diff(diff_text, allowed_files=allowed or None, max_lines_changed=max_lines)
    modified = guard.get("modified_files", [])
    compliance = ScopeAndDiffGuard.check_scope_compliance(modified, allowed, forbidden or None)
    findings = ScopeAndDiffGuard.detect_accidental_secrets_in_diff(diff_text) if secrets else []
    align = L.task_alignment(task, diff_text, allowed) if task.strip() else {
        "alignment": "NO_TASK_GIVEN", "heuristic": True, "unrelated_candidates": [], "excessive_changes": [],
        "per_file": [], "files_named_in_task": [], "task_words": [],
    }
    violations = list(guard.get("violations", []))
    for v in compliance["violations"]:
        if v.startswith("Forbidden") or v.startswith("Path escapes"):
            if v not in violations:
                violations.append(v)
    if findings:
        violations.append(f"{len(findings)} possible secret(s) in added lines")
    within = not violations
    needs_review = align["alignment"] == "REVIEW_UNRELATED_OR_EXCESSIVE_CHANGES"
    if guard.get("status") == "UNPARSEABLE_DIFF":
        verdict = "UNPARSEABLE_DIFF"
    elif violations:
        verdict = "SCOPE_VIOLATIONS_DETECTED"
    elif needs_review:
        verdict = "REVIEW_UNRELATED_CHANGES"
    elif guard.get("status") == "EMPTY_DIFF":
        verdict = "EMPTY_DIFF"
    else:
        verdict = "WITHIN_SCOPE"
    return {
        "verdict": verdict, "within_scope": within, "needs_human_review": needs_review or not within,
        "violations": violations, "modified_files": modified,
        "lines_added": guard.get("lines_added", 0), "lines_deleted": guard.get("lines_deleted", 0),
        "forbidden_files": compliance["forbidden_files"], "out_of_scope_files": compliance["out_of_scope_files"],
        "secret_findings": findings, "task_alignment": align, "guard_status": guard.get("status"),
        "scope_checked": guard.get("scope_checked", False),
    }


@tool(
    "check_diff_scope",
    "Compare a unified diff with the TASK it was meant to implement and flag changes that are "
    "unrelated, excessive (reformat-only, lock/generated files, one file far larger than the rest), "
    "outside allowed paths, in forbidden files (.env, keys), over a size limit, or that add secrets. "
    "Relevance to the task text is a word-and-path heuristic: it lists candidates for a human, it "
    "does not understand the task.",
    param("diff_text", "string", "A unified diff (e.g. the output of git_diff).", max_length=_MAX_TEXT),
    param("task", "string", "The task or requirement text the diff should implement.", required=False,
          max_length=20_000),
    param("allowed_paths", "array", "Paths or globs the change may touch (default: any).",
          required=False, item_type="string"),
    param("forbidden_patterns", "array", "Extra globs that must not change (.env*, *.pem, *.key are always).",
          required=False, item_type="string"),
    param("max_lines_changed", "integer", "Largest allowed added+deleted line count (default 300).",
          required=False, default=300, minimum=1, maximum=1_000_000),
    param("check_secrets", "boolean", "Scan added lines for credentials (default true).", required=False, default=True),
    **_CODE,
)
async def check_diff_scope(
    diff_text: str, task: str = "", allowed_paths: list | None = None, forbidden_patterns: list | None = None,
    max_lines_changed: int = 300, check_secrets: bool = True,
) -> str:
    name = "check_diff_scope"
    _short_lines(name, "diff_text", diff_text)
    allowed = [str(p) for p in _items(name, "allowed_paths", allowed_paths)]
    forbidden = [str(p) for p in _items(name, "forbidden_patterns", forbidden_patterns)]
    return await _run(_diff_scope, diff_text, task or "", allowed, forbidden, max_lines_changed, check_secrets)


# --------------------------------------------------------------------------------------
# Row 47 -- explain_source
# --------------------------------------------------------------------------------------


def _outline_markdown(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    fence = False
    for line in text.splitlines()[:20_000]:
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            current = {"level": len(m.group(1)), "heading": m.group(2), "summary": ""}
            sections.append(current)
        elif current is not None and not current["summary"] and line.strip() and not line.startswith(("|", "-", "*", ">")):
            current["summary"] = " ".join(line.split())[:240]
    return sections[:80]


def _explain(operation: str, name: str, source: str, function_name: str, detail: str) -> dict[str, Any]:
    lang = L.resolve_language(None, name)
    if operation == "function":
        if not function_name:
            raise _fail("explain_source", "operation 'function' needs function_name")
        result = SourceContentExplainer.explain_function(function_name, source)
        if not result.get("parsed"):
            tree = None
            try:
                tree = ast.parse(source)
            except (SyntaxError, ValueError):
                pass
            if tree is not None:
                result["hint"] = "function_name not found as a def; names present: " + ", ".join(
                    n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))[:300]
        return result
    if operation == "docstrings":
        return SourceContentExplainer.extract_docstring_catalog(source)
    if operation == "architecture":
        result = SourceContentExplainer.explain_code_architecture(name, source)
        if lang in L.BRACE_LANGUAGES:
            scanned = L.scan_source(source, lang)
            result.update({"external_dependencies": sorted({i["module"] for i in scanned["imports"]}),
                           "class_count": sum(1 for s in scanned["symbols"] if s["kind"] in ("class", "struct", "interface", "trait", "enum")),
                           "function_count": sum(1 for s in scanned["symbols"] if s["kind"] in ("function", "method")),
                           "parsed": True, "language": lang})
        return result
    structure = L.python_structure(source) if (lang == "python" or name.endswith(".py")) else (
        L.scan_source(source, lang) if lang in L.BRACE_LANGUAGES else None)
    base = (SourceContentExplainer.generate_study_guide(name, source) if operation == "study_guide"
            else SourceContentExplainer.explain(name, source, detail))
    if structure is not None and structure.get("symbols") is not None:
        internal = {s["name"] for s in structure["symbols"]}
        symbols = []
        for s in structure["symbols"][:80]:
            calls = [c for c in s.get("calls", []) if c in internal and c != s["name"]]
            entry = {"kind": s["kind"], "name": s["name"], "line": s["line"], "signature": s["signature"],
                     "summary": s.get("doc") or ("calls " + ", ".join(calls[:5]) if calls else ""),
                     "parent": s.get("parent"), "calls_in_file": calls[:8]}
            if operation == "study_guide":
                entry["review_question"] = (
                    f"What does `{s['name']}` do, and which of {', '.join(calls[:3])} does it rely on?" if calls
                    else f"What are the inputs, outputs and failure cases of `{s['name']}`?")
            symbols.append(entry)
        base["symbols"] = symbols
        base["imports"] = [i["module"] for i in structure["imports"]][:60]
        # leaf-first reading order: read what a symbol calls before the symbol itself
        graph = {f"{s['name']}": [c for c in s.get("calls", []) if c in internal and c != s["name"]] for s in structure["symbols"] if s["kind"] in ("function", "method")}
        order: list[str] = []
        seen: set[str] = set()

        def visit(n: str, depth: int = 0) -> None:
            if n in seen or depth > 50:
                return
            seen.add(n)
            for c in graph.get(n, []):
                visit(c, depth + 1)
            order.append(n)

        for n in graph:
            visit(n)
        base["reading_order"] = order[:80]
        if operation == "study_guide" and "study_guide_markdown" in base:
            walk = ["", "## Symbol walkthrough"]
            for e in symbols[:40]:
                walk.append(f"- `{e['signature'][:100]}` (line {e['line']}): {e['summary'] or 'no docstring'}")
            walk.extend(["", "## Reading order (dependencies first)", ", ".join(f"`{n}`" for n in order[:40])])
            base["study_guide_markdown"] += "\n" + "\n".join(walk)
            base["review_questions"] = [e["review_question"] for e in symbols[:10]]
    elif name.lower().endswith((".md", ".markdown", ".rst", ".txt")) or structure is None:
        outline = _outline_markdown(source)
        if outline:
            base["outline"] = outline
            if operation == "study_guide" and "study_guide_markdown" in base:
                base["study_guide_markdown"] += "\n\n## Section summaries\n" + "\n".join(
                    f"{'  ' * (o['level'] - 1)}- **{o['heading']}**: {o['summary'] or '(no text)'}" for o in outline[:40])
                base["review_questions"] = [f"What does the section '{o['heading']}' commit the reader to?" for o in outline[:10]]
    return base


@tool(
    "explain_source",
    "Explain source text you pass: operation 'explain' (public symbols with signature, docstring and "
    "calls, headings, summary), 'function' (one function's parameters, branches, complexity), "
    "'architecture' (layer, dependencies, cohesion), 'docstrings' (documentation catalog) or "
    "'study_guide' (symbol walkthrough, dependency-ordered reading list and review questions). "
    "Python is parsed with the AST; JS/TS/Go/Rust/Java/C#/Kotlin/Dart by declaration scanning; "
    "Markdown by headings. Structure and extracted text, not an LLM explanation.",
    param("source_code", "string", "The file's text (read it with read_file first).", max_length=_MAX_TEXT),
    param("file_name", "string", "File name, which decides the language (default 'source.py').",
          required=False, default="source.py", max_length=300),
    param("operation", "string", "What to produce (default explain).", required=False, default="explain",
          enum=("explain", "function", "architecture", "docstrings", "study_guide")),
    param("function_name", "string", "For operation 'function'.", required=False, max_length=200),
    param("detail_level", "string", "brief or comprehensive (default comprehensive).", required=False,
          default="comprehensive", enum=("brief", "comprehensive")),
    **_CODE,
)
async def explain_source(
    source_code: str, file_name: str = "source.py", operation: str = "explain", function_name: str = "",
    detail_level: str = "comprehensive",
) -> str:
    _short_lines("explain_source", "source_code", source_code if not file_name.lower().endswith((".md", ".txt", ".rst")) else None)
    return await _run(_explain, operation, file_name or "source.py", source_code, function_name or "", detail_level)


# --------------------------------------------------------------------------------------
# Row 48 -- generate_quiz
# --------------------------------------------------------------------------------------


def _quiz(operation: str, topic: str, source: str, count: int, types: list[str], signature: str,
          function_name: str, difficulty: str, answers: dict, key: dict) -> dict[str, Any]:
    if operation == "grade":
        if not key:
            raise _fail("generate_quiz", "operation 'grade' needs answer_key (from the quiz result) and user_answers")
        graded = AutomatedQuizGenerator.evaluate_quiz_answers(answers, key)
        missed = [q for q, b in graded.get("breakdown", {}).items() if not b.get("is_correct")]
        graded["review"] = [f"Question {q}: expected {graded['breakdown'][q]['expected']}" for q in missed]
        return graded
    if operation == "coding_challenge":
        sig = signature
        requirements: list[str] = []
        derived = None
        if source and function_name:
            try:
                tree = ast.parse(source)
            except (SyntaxError, ValueError) as exc:
                raise _fail("generate_quiz", f"source_text is not valid Python: {exc}") from exc
            node = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function_name), None)
            if node is None:
                raise _fail("generate_quiz", f"function '{function_name}' not found in source_text")
            kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            sig = f"{kw} {node.name}({ast.unparse(node.args)}){ret}:"
            doc = ast.get_docstring(node) or ""
            requirements = [ln.strip() for ln in doc.splitlines() if ln.strip()][:8]
            derived = {"function": node.name, "line": node.lineno, "reference_solution_withheld": True}
        result = AutomatedQuizGenerator.generate_coding_challenge(topic or function_name or "task", sig or None, difficulty)
        if requirements:
            result["requirements"] = requirements
        if derived:
            result["derived_from"] = derived
        return result
    concepts = AutomatedQuizGenerator.extract_key_concepts(source, limit=14)
    if not source.strip():
        raise _fail("generate_quiz", "operation 'quiz'/'comprehension' needs source_text")
    types = types or (["true_false", "cloze"] if operation == "comprehension" else [])
    quiz = L.build_quiz(topic, source, concepts, count, types)
    if not quiz["questions"]:
        if re.search(r"(?m)^\s*(?:def|class)\s+\w+", source):
            try:
                ast.parse(source)
            except (SyntaxError, ValueError) as exc:
                raise _fail("generate_quiz", f"source_text looks like Python but does not parse ({exc}); fix or trim it and call again") from exc
        raise _fail("generate_quiz", "no quizzable facts were found in source_text (too short, or no definitions/sentences of 6-45 words)")
    if operation == "comprehension":
        quiz["rubric"] = {"passing_score": 75.0, "criteria": [
            "Identifies what each named component is responsible for",
            "Distinguishes statements the source supports from ones it contradicts",
            "Can say where in the source each answer comes from (see source_line / source_excerpt)"]}
    quiz["hide_from_learner"] = ["answer_key", "explanations"]
    return quiz


@tool(
    "generate_quiz",
    "Build learning material from text or Python source you pass. operation 'quiz': varied questions "
    "grounded in the source -- for Python: parameter lists, return types, which function a docstring "
    "describes, which class defines a method, inheritance, imports, branch counts, true/false; for "
    "prose: fill-in-the-blank and true/false from its own sentences. 'grade': score user_answers "
    "against the answer_key a quiz returned. 'coding_challenge': starter code and tests (derived "
    "from a function in source_text when function_name is given). 'comprehension': true/false and "
    "cloze with a rubric. Deterministic; the answer_key is a separate field to keep from the learner.",
    param("operation", "string", "What to produce (default quiz).", required=False, default="quiz",
          enum=("quiz", "grade", "coding_challenge", "comprehension")),
    param("topic", "string", "What the quiz is about.", required=False, default="this material", max_length=300),
    param("source_text", "string", "The text or Python source to draw questions from.", required=False,
          max_length=_MAX_TEXT),
    param("num_questions", "integer", "How many questions (default 5).", required=False, default=5,
          minimum=1, maximum=20),
    param("question_types", "array", "Restrict to: multiple_choice, true_false, cloze, code_reading.",
          required=False, item_type="string"),
    param("function_signature", "string", "coding_challenge: one 'def name(...):' line.", required=False,
          max_length=500),
    param("function_name", "string", "coding_challenge: derive the exercise from this function in source_text.",
          required=False, max_length=200),
    param("difficulty", "string", "easy, medium or hard (coding_challenge).", required=False, default="medium",
          enum=("easy", "medium", "hard")),
    param("user_answers", "object", "grade: {question id: answer}.", required=False),
    param("answer_key", "object", "grade: {question id: correct answer} from a quiz result.", required=False),
    **_CODE,
)
async def generate_quiz(
    operation: str = "quiz", topic: str = "this material", source_text: str = "", num_questions: int = 5,
    question_types: list | None = None, function_signature: str = "", function_name: str = "",
    difficulty: str = "medium", user_answers: dict | None = None, answer_key: dict | None = None,
) -> str:
    name = "generate_quiz"
    types = [str(t) for t in _items(name, "question_types", question_types)]
    return await _run(
        _quiz, operation, topic or "this material", source_text or "", num_questions, types, function_signature or "",
        function_name or "", difficulty, _bounded(name, "user_answers", user_answers) or {},
        _bounded(name, "answer_key", answer_key) or {},
    )


# --------------------------------------------------------------------------------------
# Row 49 -- generate_artifact
# --------------------------------------------------------------------------------------


def _norm_items(rows: list[Any], names: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
            for key in ("name", "task", "title", "step", "description", "text"):
                if item.get("name") in (None, "") and item.get(key):
                    item["name"] = item[key]
            if "completed" not in item:
                for key in ("done", "complete", "status"):
                    if key in item:
                        item["completed"] = item[key] in (True, "done", "complete", "completed", "true") if key == "status" else item[key]
            out.append(item)
    out.extend({"name": n} for n in names)
    return out


def _artifact(kind: str, title: str, rows: list[dict], completed: list[str], pending: list[str], blockers: list[str],
              summary: str, steps: list[dict]) -> dict[str, Any]:
    if kind == "task_list":
        if not rows:
            raise _fail("generate_artifact", "task_list needs 'items' or 'item_names'")
        res = ArtifactGenerator.generate_task_list(title, rows)
    elif kind == "implementation_plan":
        if not rows:
            raise _fail("generate_artifact", "implementation_plan needs 'items' (stages: name, description, dependencies, risks)")
        res = ArtifactGenerator.generate_implementation_plan(title, rows)
    elif kind == "progress_report":
        if not (completed or pending or blockers):
            raise _fail("generate_artifact", "progress_report needs completed_items, pending_items or blockers")
        res = ArtifactGenerator.generate_progress_report(title, completed, pending, blockers)
    else:
        if not (summary or steps):
            raise _fail("generate_artifact", "walkthrough needs changes_summary and/or verification_steps")
        res = ArtifactGenerator.generate_walkthrough(title, summary, steps)
    res["artifact_type"] = kind
    res["saved"] = False
    res["note"] = "Nothing was written. Save markdown_content with write_file (e.g. docs/plan.md) if it should persist."
    return res


@tool(
    "generate_artifact",
    "Render a Markdown work document from structured input. artifact_type: 'task_list' (items "
    "[{name, completed, priority}] with progress), 'implementation_plan' (items [{name, description, "
    "dependencies, risks}]), 'progress_report' (completed_items, pending_items, blockers), or "
    "'walkthrough' (changes_summary + verification_steps [{action, command, expected, passed}]; the "
    "closing status is 'verified' only if every step records passed). Returns the Markdown; write it "
    "to a file with write_file if it must persist.",
    param("artifact_type", "string", "Which document.", enum=("task_list", "implementation_plan", "progress_report", "walkthrough")),
    param("title", "string", "Document title.", max_length=300),
    param("items", "array", "Tasks (task_list) or stages (implementation_plan); 'task', 'title' or "
          "'description' also work as the name.", required=False, item_type="object"),
    param("item_names", "array", "Shortcut: plain task names (task_list).", required=False, item_type="string"),
    param("completed_items", "array", "progress_report: what is done.", required=False, item_type="string"),
    param("pending_items", "array", "progress_report: what is left.", required=False, item_type="string"),
    param("blockers", "array", "progress_report: what is stuck.", required=False, item_type="string"),
    param("changes_summary", "string", "walkthrough: what changed and why.", required=False, max_length=_MAX_TEXT),
    param("verification_steps", "array", "walkthrough: [{action, command, expected, passed}].", required=False,
          item_type="object"),
    **_DATA,
)
async def generate_artifact(
    artifact_type: str, title: str, items: list | None = None, item_names: list | None = None,
    completed_items: list | None = None, pending_items: list | None = None, blockers: list | None = None,
    changes_summary: str = "", verification_steps: list | None = None,
) -> str:
    name = "generate_artifact"
    rows = _norm_items(_objects(name, "items", items), [str(n) for n in _items(name, "item_names", item_names)])
    steps = _objects(name, "verification_steps", verification_steps)
    return await _run(
        _artifact, artifact_type, title, rows, [str(c) for c in _items(name, "completed_items", completed_items)],
        [str(c) for c in _items(name, "pending_items", pending_items)], [str(c) for c in _items(name, "blockers", blockers)],
        changes_summary or "", steps,
    )


# --------------------------------------------------------------------------------------
# Row 50 -- collect_verification_evidence
# --------------------------------------------------------------------------------------


def _evidence(operation: str, task_id: str, tests: list[str], commands: list[dict], dossier: dict, expected: str,
              command: str, exit_code: int | None, stdout: str, stderr: str, label: str, content: str,
              content_b64: str, kind: str) -> dict[str, Any]:
    cls = VerificationEvidenceCollector
    name = "collect_verification_evidence"
    if operation == "collect":
        res = cls.collect(task_id, tests, commands, None)
        res["next"] = "Keep 'dossier' and 'evidence_digest'; pass the dossier back to add evidence, verify it or print the certificate."
        return res
    if not dossier:
        raise _fail(name, f"operation '{operation}' needs the 'dossier' returned by a previous call")
    if operation == "add_command":
        if not command:
            raise _fail(name, "add_command needs 'command' and 'exit_code'")
        res = cls.record_command_evidence(dossier, command, exit_code if exit_code is not None else "missing", stdout, stderr)  # type: ignore[arg-type]
        if not res.get("success"):
            raise _fail(name, res.get("error", "could not record the command"))
        verdict = cls._verdict(res["updated_dossier"].get("command_results", []), res["updated_dossier"].get("test_outputs", []))
        return {"dossier": res["updated_dossier"], "evidence_digest": res["new_digest"], "command_entry": res["command_entry"],
                "verdict": verdict["verdict"], "verdict_reasons": verdict["reasons"]}
    if operation == "add_content":
        if not (content or content_b64):
            raise _fail(name, "add_content needs 'content' or 'content_base64'")
        try:
            raw = base64.b64decode(content_b64, validate=True) if content_b64 else content.encode("utf-8", "replace")
        except (binascii.Error, ValueError) as exc:
            raise _fail(name, "content_base64 is not valid base64") from exc
        d = dict(dossier)
        if kind == "test_output":
            text = _redact(raw.decode("utf-8", "replace"))
            d["test_outputs"] = [*list(d.get("test_outputs") or []), text]
            entry: dict[str, Any] = {"label": label or "test output", "kind": "test_output", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        else:
            info = L.sniff_content(raw, label or None)
            digest = hashlib.sha256(raw).hexdigest()
            entry = {"label": label or "artifact", "kind": "artifact", "sha256": digest, "size_bytes": len(raw), "mime_type": info["mime_type"]}
            for k in ("width", "height"):
                if k in info:
                    entry[k] = info[k]
            if info.get("is_text"):
                entry["preview"] = _redact(raw[:300].decode("utf-8", "replace"))
            d["artifacts_paths"] = [*list(d.get("artifacts_paths") or []), f"{label or 'artifact'} (sha256:{digest[:16]})"]
            d["artifact_details"] = [*list(d.get("artifact_details") or []), entry]
        return {"dossier": d, "evidence_digest": cls._compute_digest(d), "recorded": entry}
    if operation == "verify":
        if not expected:
            raise _fail(name, "verify needs 'expected_digest'")
        return cls.verify_dossier_integrity(dossier, expected)
    return {"certificate_markdown": cls.generate_audit_certificate(dossier),
            "evidence_digest": cls._compute_digest(dossier), "verdict": cls._verdict(
                list(dossier.get("command_results") or []), dossier.get("test_outputs") or [])["verdict"]}


@tool(
    "collect_verification_evidence",
    "Bundle proof that a task is done into a fingerprinted dossier. operation 'collect' (test output "
    "text + command results [{command, exit_code, stdout, stderr}]), 'add_command', 'add_content' "
    "(a test log as kind 'test_output', or any artifact -- a screenshot via content_base64 -- hashed, "
    "sized and typed), 'verify' (does the dossier still match expected_digest), 'certificate' "
    "(Markdown summary). Verdict PASSED needs positive evidence (exit code 0 / passing tests) and no "
    "failure; empty evidence is INCONCLUSIVE. Secrets are scrubbed. Nothing is stored: keep the "
    "returned dossier and pass it back. The digest is an unkeyed SHA-256, not a signature.",
    param("operation", "string", "What to do.", enum=("collect", "add_command", "add_content", "verify", "certificate")),
    param("task_id", "string", "Identifier for the task (collect).", required=False, default="task", max_length=200),
    param("test_outputs", "array", "collect: raw test output blocks.", required=False, item_type="string"),
    param("command_results", "array", "collect: [{command, exit_code, stdout, stderr}].", required=False, item_type="object"),
    param("dossier", "object", "The dossier from an earlier call (all operations except collect).", required=False),
    param("expected_digest", "string", "verify: the evidence_digest you were given.", required=False, max_length=128),
    param("command", "string", "add_command: the command line that ran.", required=False, max_length=2_000),
    param("exit_code", "integer", "add_command: its exit code.", required=False, minimum=-1000, maximum=100000),
    param("stdout", "string", "add_command: captured stdout.", required=False, max_length=100_000),
    param("stderr", "string", "add_command: captured stderr.", required=False, max_length=100_000),
    param("label", "string", "add_content: a name for the artifact (e.g. login.png).", required=False, max_length=200),
    param("content", "string", "add_content: text content.", required=False, max_length=_MAX_TEXT),
    param("content_base64", "string", "add_content: binary content (a screenshot) as base64.", required=False,
          max_length=_MAX_B64),
    param("kind", "string", "add_content: 'artifact' (default) or 'test_output'.", required=False, default="artifact",
          enum=("artifact", "test_output")),
    **_CODE,
)
async def collect_verification_evidence(
    operation: str, task_id: str = "task", test_outputs: list | None = None, command_results: list | None = None,
    dossier: dict | None = None, expected_digest: str = "", command: str = "", exit_code: int | None = None,
    stdout: str = "", stderr: str = "", label: str = "", content: str = "", content_base64: str = "",
    kind: str = "artifact",
) -> str:
    name = "collect_verification_evidence"
    tests = [str(t) for t in _items(name, "test_outputs", test_outputs)]
    cmds = _objects(name, "command_results", command_results)
    return await _run(
        _evidence, operation, task_id or "task", tests, cmds, _bounded(name, "dossier", dossier) or {}, expected_digest,
        command, exit_code, stdout, stderr, label, content, content_base64, kind,
    )


# --------------------------------------------------------------------------------------
# Row 51 -- apply_plan_feedback
# --------------------------------------------------------------------------------------

_CLAUSE_SPLIT = re.compile(r"[.;\n]+\s*|\s+(?:and then|then|and also|also,?)\s+|,?\s+and\s+(?=(?:please\s+)?(?:add|remove|delete|drop|move|change|replace|swap|insert|rename|skip)\b)", re.I)
_NUMS = r"#?(\d+(?:\s*(?:,|and|&)\s*#?\d+)*)"


def _numbers(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)]


def _apply_feedback(plan: list[str], feedback: str, revision: int) -> dict[str, Any]:
    cls = ArtifactFeedbackIngestor
    intent = cls.classify_feedback_intent(feedback)
    items: list[dict[str, Any]] = [{"orig": i + 1, "text": s} for i, s in enumerate(plan)]
    actions: list[str] = []
    unhandled: list[str] = []
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(feedback) if c and c.strip()]

    def find(n: int) -> int | None:
        return next((i for i, it in enumerate(items) if it["orig"] == n), None)

    for clause in clauses:
        low = clause.lower().strip()
        low = re.sub(r"^(?:please|also|and|can you|could you)\s+", "", low)
        text = re.sub(r"^(?:please|also|and|can you|could you)\s+", "", clause.strip(), flags=re.I)
        m = re.match(rf"(?:remove|delete|drop|skip)\s+(?:steps?|tasks?|items?)\s+{_NUMS}", low)
        if m:
            for n in _numbers(m.group(1)):
                idx = find(n)
                if idx is None:
                    actions.append(f"Step {n} does not exist in the plan you sent ({len(plan)} steps) or was already changed; nothing removed.")
                else:
                    actions.append(f"Removed step {n}: '{items[idx]['text']}'")
                    del items[idx]
            continue
        m = re.match(r"swap\s+(?:steps?\s+)?(\d+)\s+(?:and|with|&)\s+(?:step\s+)?(\d+)", low)
        if m:
            a, b = find(int(m.group(1))), find(int(m.group(2)))
            if a is None or b is None:
                actions.append("Swap refused: one of the steps does not exist.")
            else:
                items[a], items[b] = items[b], items[a]
                actions.append(f"Swapped steps {m.group(1)} and {m.group(2)}")
            continue
        m = re.match(r"move\s+step\s+(\d+)\s+(?:(before|after)\s+step\s+(\d+)|to\s+(?:position\s+)?(\d+)|to\s+(?:the\s+)?(top|start|beginning|end|bottom|last|first))", low)
        if m:
            src = find(int(m.group(1)))
            if src is None:
                actions.append(f"Step {m.group(1)} does not exist; nothing moved.")
                continue
            item = items.pop(src)
            if m.group(2):
                ref = find(int(m.group(3)))
                if ref is None:
                    items.insert(src, item)
                    actions.append(f"Step {m.group(3)} does not exist; nothing moved.")
                    continue
                items.insert(ref + (1 if m.group(2) == "after" else 0), item)
                actions.append(f"Moved step {m.group(1)} {m.group(2)} step {m.group(3)}")
            elif m.group(4):
                pos = max(1, min(int(m.group(4)), len(items) + 1))
                items.insert(pos - 1, item)
                actions.append(f"Moved step {m.group(1)} to position {pos}")
            else:
                if m.group(5) in ("top", "start", "beginning", "first"):
                    items.insert(0, item)
                else:
                    items.append(item)
                actions.append(f"Moved step {m.group(1)} to the {'start' if m.group(5) in ('top', 'start', 'beginning', 'first') else 'end'}")
            continue
        m = re.match(r"(?:change|replace|modify|update|rewrite|rename)\s+step\s+(\d+)\s*(?:to|with|so that it|so it|as|:)\s*(.+)$", text, re.I)
        if m:
            idx = find(int(m.group(1)))
            new = m.group(2).strip().rstrip(".")
            if idx is None:
                actions.append(f"Step {m.group(1)} does not exist; nothing changed.")
            elif not new:
                unhandled.append(clause)
            else:
                actions.append(f"Changed step {m.group(1)} from '{items[idx]['text']}' to '{new}'")
                items[idx]["text"] = new
            continue
        m = re.match(r"(?:add|insert|include|append)\s+(?:a\s+|an\s+|the\s+|another\s+)?(.+?)(?:\s+(after|before)\s+step\s+(\d+)|\s+(?:at the end|last)|\s+(?:at the (?:start|beginning)|first))?\s*$", text, re.I)
        if m and not re.match(r"(?:add|insert|include|append)\s+(?:a\s+|an\s+|the\s+)?(?:step|task)s?\s+\d", low):
            new = m.group(1).strip().rstrip(".")
            if not new:
                unhandled.append(clause)
                continue
            new = new[0].upper() + new[1:]
            if m.group(2):
                ref = find(int(m.group(3)))
                if ref is None:
                    actions.append(f"Step {m.group(3)} does not exist; added '{new}' at the end instead.")
                    items.append({"orig": None, "text": new})
                else:
                    items.insert(ref + (1 if m.group(2).lower() == "after" else 0), {"orig": None, "text": new})
                    actions.append(f"Added step '{new}' {m.group(2).lower()} step {m.group(3)}")
            elif re.search(r"(?:at the (?:start|beginning)|first)\s*$", text, re.I):
                items.insert(0, {"orig": None, "text": new})
                actions.append(f"Added step '{new}' at the start")
            else:
                items.append({"orig": None, "text": new})
                actions.append(f"Added step '{new}'")
            continue
        m = re.match(r"(?:remove|delete|drop|skip)\s+(?:the\s+)?(.+)$", low)
        if m:
            keywords = L.word_set(m.group(1)) - {"step", "steps", "task", "tasks", "item", "items"}
            hits = [it for it in items if keywords and keywords <= L.word_set(it["text"])]
            if hits:
                for it in hits:
                    actions.append(f"Removed step: '{it['text']}'")
                items = [it for it in items if it not in hits]
            else:
                actions.append(f"No step contained all of: {', '.join(sorted(keywords)) or '(nothing)'}; nothing removed.")
            continue
        unhandled.append(clause)

    revised = [it["text"] for it in items]
    changed = revised != plan
    rejected = intent["intent"] == "REJECT"
    approved = intent["intent"] == "APPROVE" and not changed
    needs_replan = rejected or (bool(unhandled) and intent["intent"] in ("MODIFY_STEP", "ADD_STEP", "REMOVE_STEP"))
    if not actions and not unhandled and not approved:
        actions.append("Recorded feedback without structural plan changes.")
    result: dict[str, Any] = {
        "intent": intent["intent"], "urgency": intent["urgency"], "approved": approved, "changed": changed,
        "actions": actions, "original_plan": plan, "revised_plan": revised, "unhandled_clauses": unhandled,
        "needs_replan": needs_replan, "revision": revision + (1 if changed else 0),
    }
    if needs_replan:
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(revised, start=1))
        result["replan_instruction"] = (
            f"The user gave feedback the step editor could not apply mechanically: {feedback.strip()[:500]!r}. "
            f"Current plan:\n{numbered}\nRevise the plan to satisfy the feedback and show it again for approval."
        )
    return result


def _feedback_tool(operation: str, plan: list[str], feedback: str, feedbacks: list[str], revision: int, markdown: str,
                   section: str, new_content: str, mode: str, revised_text: str) -> dict[str, Any]:
    cls = ArtifactFeedbackIngestor
    if operation == "classify":
        if not feedback:
            raise _fail("apply_plan_feedback", "classify needs 'feedback'")
        return cls.classify_feedback_intent(feedback)
    if operation == "patch_markdown":
        if not markdown or not section:
            raise _fail("apply_plan_feedback", "patch_markdown needs 'markdown_text' and 'section'")
        return cls.patch_markdown_artifact(markdown, section, new_content, mode)
    if operation == "diff":
        if not markdown or not revised_text:
            raise _fail("apply_plan_feedback", "diff needs 'markdown_text' (original) and 'revised_text'")
        return cls.generate_feedback_diff(markdown, revised_text)
    if not plan:
        raise _fail("apply_plan_feedback", "apply needs 'plan' (the current steps as strings)")
    batch = [f for f in [feedback, *feedbacks] if f and f.strip()]
    if not batch:
        raise _fail("apply_plan_feedback", "apply needs 'feedback' or 'feedbacks'")
    current, history, rev = list(plan), [], revision
    last: dict[str, Any] = {}
    for fb in batch:
        last = _apply_feedback(current, fb, rev)
        current, rev = last["revised_plan"], last["revision"]
        history.append({"feedback": fb, "intent": last["intent"], "actions": last["actions"], "unhandled_clauses": last["unhandled_clauses"]})
    if len(batch) == 1:
        return last
    deduped = list(dict.fromkeys(current))
    return {
        "intent": last["intent"], "approved": all(h["intent"] == "APPROVE" for h in history), "changed": deduped != plan,
        "original_plan": plan, "revised_plan": deduped, "revision": rev, "history": history,
        "needs_replan": any(h["unhandled_clauses"] for h in history) or any(h["intent"] == "REJECT" for h in history),
        "note": "Each feedback comment was applied in order to the plan as it stood; step numbers in a later comment refer to the plan after the earlier ones.",
    }


@tool(
    "apply_plan_feedback",
    "Turn a person's feedback on a plan into a revised plan. operation 'apply': plan = the current "
    "steps as strings, feedback = what they said ('remove step 3', 'move step 4 before step 2', "
    "'change step 2 to run the migration first', 'add a rollback step after step 3', 'swap steps 1 and "
    "2', 'remove the docs step'); step numbers refer to the plan as sent in this call. Returns the "
    "revised plan, what was done, and needs_replan + a replan_instruction when the feedback is a "
    "rejection or cannot be applied mechanically. 'classify' gives the intent only; 'patch_markdown' "
    "replaces or appends a section of a Markdown artifact; 'diff' diffs two versions. Nothing is "
    "stored: pass back the revised plan next time.",
    param("operation", "string", "What to do (default apply).", required=False, default="apply",
          enum=("apply", "classify", "patch_markdown", "diff")),
    param("plan", "array", "apply: the current plan steps.", required=False, item_type="string"),
    param("feedback", "string", "The feedback text.", required=False, max_length=20_000),
    param("feedbacks", "array", "apply: more feedback comments, applied in order.", required=False, item_type="string"),
    param("revision", "integer", "apply: the plan's current revision number (default 0).", required=False, default=0,
          minimum=0, maximum=1_000_000),
    param("markdown_text", "string", "patch_markdown / diff: the Markdown artifact (diff: the original).",
          required=False, max_length=_MAX_TEXT),
    param("section", "string", "patch_markdown: the heading to change (appended if missing).", required=False, max_length=300),
    param("new_content", "string", "patch_markdown: the new section text.", required=False, max_length=_MAX_TEXT),
    param("mode", "string", "patch_markdown: replace or append (default replace).", required=False, default="replace",
          enum=("replace", "append")),
    param("revised_text", "string", "diff: the revised Markdown.", required=False, max_length=_MAX_TEXT),
    **_CODE,
)
async def apply_plan_feedback(
    operation: str = "apply", plan: list | None = None, feedback: str = "", feedbacks: list | None = None,
    revision: int = 0, markdown_text: str = "", section: str = "", new_content: str = "", mode: str = "replace",
    revised_text: str = "",
) -> str:
    name = "apply_plan_feedback"
    steps = [str(s) for s in _items(name, "plan", plan)]
    if len(steps) > _MAX_PLAN_STEPS:
        raise _fail(name, f"'plan' has {len(steps)} steps; the limit is {_MAX_PLAN_STEPS}")
    more = [str(f) for f in _items(name, "feedbacks", feedbacks)]
    return await _run(_feedback_tool, operation, steps, feedback or "", more, revision, markdown_text, section,
                      new_content, mode, revised_text)


# --------------------------------------------------------------------------------------
# Row 69 -- summarize_for_mobile
# --------------------------------------------------------------------------------------

_RISKY_PATH = re.compile(r"(^|/)(\.env[\w.]*|.*\.(pem|key|p12)|id_rsa.*|.*secret.*|.*credential.*)$", re.I)
_MIGRATION_PATH = re.compile(r"(^|/)(migrations?|alembic/versions)/|\.sql$", re.I)


def _mobile(task: str, diff_text: str, test_output: str, test_results: dict, plan_steps: list[str], status: str,
            max_chars: int, command_output: str) -> dict[str, Any]:
    cls = MobileArtifactSummarizer
    parts: dict[str, Any] = {}
    flags: list[str] = []
    diff_info = None
    if diff_text.strip():
        files = ScopeAndDiffGuard.parse_diff_files(diff_text)
        compact = cls.summarize_diff_compact(diff_text, max_files=4)
        counts = cls.count_diff_lines(diff_text)
        diff_info = {"files": compact["total_files"], "added": counts["added"], "removed": counts["removed"], "preview": compact["compact_string"]}
        parts["diff"] = diff_info
        for f in files:
            if _RISKY_PATH.search(f["path"]):
                flags.append(f"touches sensitive file {f['path']}")
            if _MIGRATION_PATH.search(f["path"]):
                flags.append(f"changes schema/migration {f['path']}")
            if f["is_deleted"]:
                flags.append(f"deletes {f['path']}")
        if counts["added"] + counts["removed"] > 500:
            flags.append(f"large change ({counts['added'] + counts['removed']} lines)")
    tests = None
    if test_output.strip() or test_results:
        tests = cls.summarize_test_results(test_results if test_results else test_output)
        parts["tests"] = tests
        if tests["status"] == "FAILED":
            flags.append(f"{tests['failed'] + tests['errors']} test(s) failing")
        elif tests["status"] == "UNKNOWN":
            flags.append("test results not recognised")
    if plan_steps:
        parts["plan"] = {"steps": len(plan_steps), "first": [s[:80] for s in plan_steps[:3]]}
    if command_output.strip():
        tail = [ln.strip() for ln in command_output.strip().splitlines() if ln.strip()][-2:]
        parts["command"] = {"tail": [t[:120] for t in tail]}
        if re.search(r"(?i)\b(error|traceback|fatal|failed)\b", command_output[-2000:]):
            flags.append("command output mentions an error")
    lines: list[str] = []
    if task.strip():
        lines.append(" ".join(task.split())[:120])
    if plan_steps:
        lines.append(f"Plan: {len(plan_steps)} steps, starting with '{plan_steps[0][:60]}'")
    if diff_info:
        lines.append(f"Changes: {diff_info['files']} file(s), +{diff_info['added']}/-{diff_info['removed']} ({diff_info['preview'][:90]})")
    if tests:
        lines.append(f"Tests: {tests['banner']}")
    if "command" in parts:
        lines.append("Last output: " + parts["command"]["tail"][-1])
    if flags:
        lines.append("Check: " + "; ".join(flags[:3]))
    if not lines:
        raise _fail("summarize_for_mobile", "give at least one of task, diff_text, test_output/test_results, plan_steps, command_output")
    headline = status or (("FAILED" if tests and tests["status"] == "FAILED" else "REVIEW" if flags else "READY") if (tests or diff_info) else "INFO")
    text = "\n".join(lines)
    summary_text = text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"
    title = (task.strip().splitlines()[0][:60] if task.strip() else "Task update")
    body = lines[1] if len(lines) > 1 else lines[0]
    notification = cls.format_mobile_notification(title, body, "", 140).strip()
    sections = [{"heading": ln.split(":", 1)[0] if ":" in ln else "Summary", "body": ln.split(":", 1)[-1].strip()} for ln in lines]
    card = cls.generate_mobile_card_json(title, [{"title": s["heading"][:40], "text": s["body"][:200]} for s in sections])
    return {"headline": headline, "summary_text": summary_text, "character_count": len(summary_text),
            "notification": notification, "card": card, "risk_flags": flags, "parts": parts,
            "note": "Text, diff and test inputs only. A screenshot cannot be summarised here (no vision call from a tool)."}


@tool(
    "summarize_for_mobile",
    "Condense a task's plan, diff, test output and last command output into a short phone-sized "
    "summary: a headline, a text within max_chars, a push-notification line, a card (JSON) with "
    "approve/reject actions, and risk flags (sensitive files, migrations, deletions, failing tests). "
    "Give any of the inputs. Text only; screenshots are not read.",
    param("task", "string", "One line on what the task is.", required=False, max_length=2_000),
    param("diff_text", "string", "A unified diff.", required=False, max_length=_MAX_TEXT),
    param("test_output", "string", "Raw pytest/test output.", required=False, max_length=_MAX_TEXT),
    param("test_results", "object", "Or a result object {passed, failed, errors, duration}.", required=False),
    param("plan_steps", "array", "The plan's steps.", required=False, item_type="string"),
    param("command_output", "string", "The last command's output.", required=False, max_length=_MAX_TEXT),
    param("status", "string", "Override the headline (e.g. 'NEEDS APPROVAL').", required=False, max_length=40),
    param("max_chars", "integer", "Longest summary_text (default 600).", required=False, default=600, minimum=60, maximum=4000),
    **_CODE,
)
async def summarize_for_mobile(
    task: str = "", diff_text: str = "", test_output: str = "", test_results: dict | None = None,
    plan_steps: list | None = None, command_output: str = "", status: str = "", max_chars: int = 600,
) -> str:
    name = "summarize_for_mobile"
    _short_lines(name, "diff_text", diff_text or None)
    steps = [str(s) for s in _items(name, "plan_steps", plan_steps)]
    return await _run(_mobile, task or "", diff_text or "", test_output or "", _bounded(name, "test_results", test_results) or {},
                      steps, status or "", max_chars, command_output or "")


# --------------------------------------------------------------------------------------
# Row 73 -- detect_file_type
# --------------------------------------------------------------------------------------


def _detect(data: bytes, filename: str) -> dict[str, Any]:
    info = L.sniff_content(data, filename or None)
    if info["mime_type"] == "application/octet-stream":
        legacy = FileTypeDetectorRouter.detect_and_route(header_bytes=data[:1100])
        if legacy.get("mime_type") and legacy["mime_type"] != "application/octet-stream":
            info["mime_type"] = legacy["mime_type"]
    tool_name, note = L.route_for(info)
    info["recommended_tool"] = tool_name
    info["how"] = note
    info["is_text"] = bool(info.get("is_text"))
    return info


@tool(
    "detect_file_type",
    "Identify what a file really is from its CONTENT (magic bytes, zip/Office internals, JSON/XML/CSV/"
    "source/SQL/log sniffing), not its extension, and name the agent tool that reads that format "
    "(parse_document, inspect_spreadsheet, parse_structured_data, analyze_source_structure, ...). "
    "A .docx renamed .dat is still a docx; a PNG named .txt is an image. Pass the first bytes as "
    "content_base64 (up to ~2 MB; zip/Office detection needs the whole file) or the text itself.",
    param("content_base64", "string", "File bytes, base64 (use this or text).", required=False, max_length=_MAX_B64),
    param("text", "string", "The file's text (use this or content_base64).", required=False, max_length=_MAX_TEXT),
    param("filename", "string", "Its name, used only as a hint and for the extension-mismatch check.",
          required=False, max_length=300),
    **_CODE,
)
async def detect_file_type(content_base64: str = "", text: str = "", filename: str = "") -> str:
    name = "detect_file_type"
    if bool(content_base64) == bool(text):
        raise _fail(name, "provide exactly one of content_base64 or text")
    if content_base64:
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise _fail(name, "content_base64 is not valid base64") from exc
    else:
        data = text.encode("utf-8", "replace")
    return await _run(_detect, data, filename or "")


# --------------------------------------------------------------------------------------
# Row 80 -- parse_structured_data
# --------------------------------------------------------------------------------------


def _guess_format(content: str, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mapped = {"json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml", "xml": "xml", "svg": "xml", "html": "xml"}.get(ext)
    if mapped:
        return mapped
    s = content.lstrip("﻿ \t\r\n")
    if re.match(r"\[[\w.\- \"']+\][ \t]*\r?\n(?:[ \t]*(?:#[^\n]*)?\r?\n)*[ \t]*[\w.\-\"']+[ \t]*=", s):
        return "toml"  # a table header followed by key = value, before '[' is taken for a JSON array
    if s[:1] in ("{", "["):
        return "json"
    if s.startswith("<"):
        return "xml"
    if re.search(r"(?m)^\[[\w.\- \"']+\]\s*$", s) and re.search(r"(?m)^[\w.\-\"']+\s*=\s*\S", s):
        return "toml"
    if re.search(r"(?m)^[\w.\-\"']+\s*=\s*\S", s) and not re.search(r"(?m)^\s*[\w.-]+:\s", s):
        return "toml"
    return "yaml"


def _line_index(content: str, errors: list[dict[str, Any]], fmt: str) -> None:
    """Add ``line``/``column`` to schema errors for JSON and YAML (best effort; never raises)."""
    if fmt not in ("json", "yaml") or len(content) > 400_000 or not errors:
        return
    try:
        import yaml

        root = yaml.compose(content.replace("\t", " "), Loader=yaml.SafeLoader)
    except Exception:  # noqa: BLE001 -- a composer failure only means no line numbers
        return
    for error in errors:
        node = root
        path = L._parse_pointer(error["path"])
        resolved = 0
        for part in path:
            if node is None:
                break
            value = getattr(node, "value", None)
            nxt = None
            if isinstance(part, int) and isinstance(value, list) and 0 <= part < len(value) and not isinstance(value[part], tuple):
                nxt = value[part]
            elif isinstance(part, str) and isinstance(value, list):
                for pair in value:
                    if isinstance(pair, tuple) and getattr(pair[0], "value", None) == part:
                        nxt = pair[1]
                        break
            if nxt is None:
                break
            node = nxt
            resolved += 1
        if node is not None:
            error["line"] = node.start_mark.line + 1
            error["column"] = node.start_mark.column + 1
            if resolved < len(path):
                error["line_note"] = "line of the nearest enclosing value (the property is missing)"


def _structured(content: str, data_format: str, filename: str, schema: dict | None, required_keys: list[str],
                type_contracts: dict, flatten: bool, convert_to: str, include_parsed: bool) -> dict[str, Any]:
    fmt = _guess_format(content, filename) if data_format == "auto" else data_format
    parsed = StructuredDataParser.parse_with_line_errors(content, fmt)
    if not parsed.get("valid"):
        return {"valid": False, "format": fmt, **{k: v for k, v in parsed.items() if k not in ("valid", "parsed_data")}}
    data = parsed.get("parsed_data")
    result: dict[str, Any] = {"valid": True, "format": fmt,
                              "root_type": L._type_name(data), "items_count": len(data) if isinstance(data, (dict, list)) else 1}
    if isinstance(data, dict):
        result["top_level_keys"] = list(data)[:100]
    if include_parsed:
        result["parsed_data"] = data
    problems = 0
    if schema is not None:
        try:
            errors = L.validate_json_schema(data, schema)
        except L._SchemaProblem as exc:
            raise _fail("parse_structured_data", f"the schema itself is unusable: {exc}") from exc
        _line_index(content, errors, fmt)
        result["schema_valid"] = not [e for e in errors if e["keyword"] != "unsupported_keyword"]
        result["schema_errors"] = errors
        problems += len(errors)
    if required_keys or type_contracts:
        contract = StructuredDataParser.validate_schema(data, required_keys or None, type_contracts or None)
        result["contract_valid"] = contract["valid"]
        result["contract_errors"] = contract["errors"]
    if flatten and isinstance(data, dict):
        flat = StructuredDataParser.flatten_nested_dict(data)
        result["flattened"] = dict(list(flat.items())[:2_000])
        result["flattened_truncated"] = len(flat) > 2_000
    if convert_to != "none":
        converted = StructuredDataParser.convert_format(content, fmt, convert_to)
        if converted.get("converted"):
            result["converted_text"] = converted["output_text"][:100_000]
            result["converted_to"] = convert_to
            if converted.get("warnings"):
                result["conversion_warnings"] = converted["warnings"]
        else:
            result["conversion_error"] = converted.get("error")
    return result


@tool(
    "parse_structured_data",
    "Parse JSON, YAML, TOML or XML text (format auto-detected) and validate it. Parse errors come "
    "back with line, column and the offending line. Optional `schema` is a JSON Schema (type incl. "
    "nested objects/arrays, required, properties, additionalProperties, items, enum, const, "
    "min/max, pattern, format, allOf/anyOf/oneOf/not, local $ref); every schema error carries its "
    "path and, for JSON/YAML, the line in your text. The legacy required_keys / type_contracts "
    "{field: 'int'|'str'|'float'|'bool'|'list'|'dict'} still work. Can also flatten to dotted keys "
    "or convert to JSON/YAML. Entity and alias bombs are refused. Nothing is executed.",
    param("content", "string", "The text to parse.", max_length=_MAX_TEXT),
    param("data_format", "string", "Format (default auto).", required=False, default="auto",
          enum=("auto", "json", "yaml", "toml", "xml")),
    param("filename", "string", "Optional name; its extension decides an 'auto' format.", required=False, max_length=300),
    param("schema", "object", "Optional JSON Schema to validate the parsed value against.", required=False),
    param("required_keys", "array", "Legacy: keys the root object must have.", required=False, item_type="string"),
    param("type_contracts", "object", "Legacy: {field: type name}.", required=False),
    param("flatten", "boolean", "Also return dotted-key flattening of an object root.", required=False, default=False),
    param("convert_to", "string", "Also convert to json or yaml (default none).", required=False, default="none",
          enum=("none", "json", "yaml")),
    param("include_parsed", "boolean", "Include the parsed value (default true).", required=False, default=True),
    **_DATA,
)
async def parse_structured_data(
    content: str, data_format: str = "auto", filename: str = "", schema: dict | None = None,
    required_keys: list | None = None, type_contracts: dict | None = None, flatten: bool = False,
    convert_to: str = "none", include_parsed: bool = True,
) -> str:
    name = "parse_structured_data"
    return await _run(
        _structured, content, data_format, filename or "", _bounded(name, "schema", schema),
        [str(k) for k in _items(name, "required_keys", required_keys)], _bounded(name, "type_contracts", type_contracts) or {},
        flatten, convert_to, include_parsed,
    )


# --------------------------------------------------------------------------------------
# Row 81 -- analyze_source_structure
# --------------------------------------------------------------------------------------

_SNIFF_LANG = {"python": "python", "javascript": "javascript", "typescript": "typescript", "go": "go", "rust": "rust",
               "java": "java", "kotlin": "kotlin", "csharp": "csharp", "dart": "dart"}


def _guess_language(source: str) -> str | None:
    if re.search(r"(?m)^\s*package\s+\w+\s*$", source) and re.search(r"(?m)^\s*func\s", source):
        return "go"
    if re.search(r"(?m)^\s*(?:pub\s+)?fn\s+\w+", source) and re.search(r"\b(let|impl|use)\b", source):
        return "rust"
    if re.search(r"(?m)^\s*(?:def|class)\s+\w+.*:\s*$|^\s*import\s+\w+\s*$|^\s*from\s+[\w.]+\s+import\s", source):
        return "python"
    if re.search(r"(?m)^\s*(?:public\s+)?(?:static\s+)?class\s+\w+", source) and re.search(r"\bnamespace\b|\busing\s+System", source):
        return "csharp"
    if re.search(r"(?m)^\s*package\s+[\w.]+;", source) or re.search(r"(?m)^\s*import\s+java\.", source):
        return "java"
    if re.search(r"(?m)^\s*fun\s+\w+\(", source):
        return "kotlin"
    if re.search(r"(?m)^\s*(?:interface|type)\s+\w+|:\s*(?:string|number|boolean)\b", source):
        return "typescript"
    if re.search(r"\b(?:function|const|let|var)\b|=>|require\(", source):
        return "javascript"
    return None


def _structure_one(source: str, lang: str | None, filename: str, include_calls: bool) -> dict[str, Any]:
    resolved = L.resolve_language(lang, filename) or _guess_language(source)
    if resolved is None:
        raise _fail("analyze_source_structure", "cannot tell the language; pass language or a filename with an extension")
    if resolved == "python":
        result = L.python_structure(source)
        if "error" not in result:
            legacy = MultiLanguageAstAnalyzer.analyze_python(source)
            if legacy.get("valid"):
                result["global_variables"] = legacy.get("global_variables", [])
    elif resolved in L.BRACE_LANGUAGES:
        result = L.scan_source(source, resolved)
        result["method"] = "declaration scanning with brace matching (not a parser)"
    else:
        raise _fail("analyze_source_structure", f"unsupported language {resolved!r}")
    if not include_calls:
        result.pop("call_graph", None)
        for s in result.get("symbols", []):
            s.pop("calls", None)
    kinds: dict[str, int] = {}
    for s in result.get("symbols", []):
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    result["counts"] = kinds
    return result


def _structure(source: str, lang: str | None, filename: str, files: dict, include_calls: bool) -> dict[str, Any]:
    if source:
        return _structure_one(source, lang, filename, include_calls)
    index: list[dict[str, Any]] = []
    per_file: dict[str, Any] = {}
    skipped: list[dict[str, str]] = []
    for path, text in files.items():
        if not isinstance(text, str):
            skipped.append({"file": str(path), "reason": "content is not text"})
            continue
        resolved = L.resolve_language(None, str(path))
        if resolved is None:
            skipped.append({"file": str(path), "reason": "unsupported file type"})
            continue
        res = _structure_one(text, resolved, str(path), include_calls)
        per_file[str(path)] = {"language": resolved, "counts": res["counts"], "imports": [i["module"] for i in res["imports"]][:40],
                               "error": res.get("error")}
        index.extend({"symbol": s["name"], "kind": s["kind"], "file": str(path), "line": s["line"], "language": resolved}
                     for s in res["symbols"] if s["kind"] != "impl")
    return {"files_analyzed": len(per_file), "symbols_indexed": len(index), "symbols": index[:1_500], "files": per_file, "skipped_files": skipped}


@tool(
    "analyze_source_structure",
    "Extract the structure of source code in Python, JavaScript, TypeScript, Go, Rust, Java, "
    "Kotlin, C# or Dart: classes/structs/interfaces/enums/traits, functions and methods (with "
    "parent, line range, signature, exported flag, the names each one calls), imports with line "
    "numbers, and a call graph. Python uses the real AST; the others use declaration scanning with "
    "brace matching (no Tree-sitter), so exotic syntax can be missed. Pass source_code (+ language or "
    "filename) or files {name: source} for a cross-language symbol index.",
    param("source_code", "string", "One file's text.", required=False, max_length=_MAX_TEXT),
    param("language", "string", "Language (default auto: from filename, then content).", required=False, default="auto",
          enum=("auto", "python", "javascript", "typescript", "go", "rust", "java", "kotlin", "csharp", "dart")),
    param("filename", "string", "File name; its extension decides the language.", required=False, max_length=300),
    param("files", "object", "Alternative: {file name: source text} for several files.", required=False),
    param("include_calls", "boolean", "Include call sites / call graph (default true).", required=False, default=True),
    **_CODE,
)
async def analyze_source_structure(
    source_code: str = "", language: str = "auto", filename: str = "", files: dict | None = None,
    include_calls: bool = True,
) -> str:
    name = "analyze_source_structure"
    if bool(source_code) == bool(files):
        raise _fail(name, "provide exactly one of source_code or files")
    bundle = _bounded(name, "files", files) or {}
    if len(bundle) > 100:
        raise _fail(name, f"'files' has {len(bundle)} entries; the limit is 100")
    _short_lines(name, "source_code", source_code or None)
    return await _run(_structure, source_code, language, filename or "", bundle, include_calls)


# --------------------------------------------------------------------------------------
# Row 105 -- validate_workflow
# --------------------------------------------------------------------------------------


@tool(
    "validate_workflow",
    "Validate a workflow BEFORE running it. tasks = [{id, depends_on, duration_s, deadline_s, "
    "on_failure: halt|continue|recover, recovery: <task id>, optional}] (dependencies / "
    "prerequisites / deps / needs / after / requires also work). Checks duplicate ids, undefined or "
    "self dependencies, cycles (with the cycle path), execution order and parallel tiers, the critical "
    "path against per-task and overall deadlines, what is blocked when each task fails, and that every "
    "recovery task exists and can actually run after its owner fails. simulate_failures names tasks to "
    "fail and shows what still runs.",
    param("tasks", "array", "The workflow's tasks.", item_type="object"),
    param("deadline_s", "number", "Whole-workflow time budget in seconds.", required=False, minimum=0),
    param("simulate_failures", "array", "Task ids to treat as failed.", required=False, item_type="string"),
    param("default_duration_s", "number", "Duration of a task with no duration_s (default 1).", required=False,
          default=1.0, minimum=0, maximum=1_000_000),
    **_CODE,
)
async def validate_workflow(
    tasks: list, deadline_s: float | None = None, simulate_failures: list | None = None, default_duration_s: float = 1.0,
) -> str:
    name = "validate_workflow"
    rows = _objects(name, "tasks", tasks)
    if not rows:
        raise _fail(name, "'tasks' is empty; there is nothing to validate")
    if len(rows) > _MAX_TASKS:
        raise _fail(name, f"'tasks' has {len(rows)} entries; the limit is {_MAX_TASKS}")
    return await _run(
        L.validate_workflow_spec, rows, deadline_s=deadline_s,
        simulate_failures=[str(s) for s in _items(name, "simulate_failures", simulate_failures)],
        default_duration_s=float(default_duration_s),
    )


_OPEN_PURE_TOOLS: tuple[Tool, ...] = (
    check_architecture, detect_ambiguity, verify_acceptance_criteria, check_diff_scope, explain_source,
    generate_quiz, generate_artifact, collect_verification_evidence, apply_plan_feedback,
    summarize_for_mobile, detect_file_type, parse_structured_data, analyze_source_structure, validate_workflow,
)

#: Names, for persona grants (``agents/registry.py``) and the tests that pin them.
OPEN_PURE_TOOL_NAMES: frozenset[str] = frozenset(t.spec.name for t in _OPEN_PURE_TOOLS)


def build_open_pure_tools() -> list[Tool]:
    """The ADR-042 pure tools: no capability, no I/O, no state in this process."""
    return list(_OPEN_PURE_TOOLS)
