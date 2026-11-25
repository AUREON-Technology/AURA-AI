"""Computation behind the ADR-044 tools (``tools/open_pure.py``): pure functions, no I/O, no state.

The ``tools/open`` classes cover the basics of each capability; this module is what the review of
2026-11 found missing around them -- the parts that can honestly be built in Python with the
standard library and PyYAML, and no more:

* ``ambiguity_findings`` / ``requirement_conflicts``: per-sentence findings with one clarification
  question each, and contradiction detection beyond the four fixed keyword pairs.
* ``python_imports`` / ``js_imports`` / ``convention_findings``: what ``check_architecture`` needs
  to read import edges out of source code itself instead of asking the caller for the map.
* ``task_alignment``: compares a diff with the *text of the task* (ADR-044 row 45).
* ``validate_json_schema``: a JSON-Schema subset with nested types, so a contract can be checked
  beyond ``required_keys`` and flat ``{field: "int"}`` pairs (row 80).
* ``scan_source``: declaration, method, import and call extraction for non-Python languages by
  brace-matching, with line numbers (row 81). It is **not** a parser: no Tree-sitter is installed.
* ``build_quiz``: question types that depend on what the source actually says (row 48).
* ``validate_workflow_spec``: ordering, cycles, failure propagation, recovery and deadlines (row 105).
* ``sniff_content``: magic-byte and container sniffing from bytes the caller passes (row 73).

Every function takes bounded text and returns plain dicts/lists; none reads a path, imports user
code, opens a socket or keeps module-level state (``tests/unit/test_open_pure_tools.py`` pins that).
"""

from __future__ import annotations

import ast
import bisect
import csv
import hashlib
import io
import json
import math
import re
import struct
import zipfile
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ambiguity_findings", "requirement_conflicts", "python_imports", "js_imports",
    "module_name_for_path", "convention_findings", "task_alignment", "diff_file_lines",
    "validate_json_schema", "scan_source", "build_quiz", "validate_workflow_spec",
    "sniff_content", "word_set",
]

_STOP = frozenset(
    "a an the and or of to in on for with from by at as is are was were be been it its this that these "
    "those i we you they he she them our your their my me do does did not no yes can could should would "
    "will shall may might must have has had having if then else when while than so such via per into "
    "out up down over under about also just only very more most less new old add make use used using "
    "please need needs want wants get gets set sets change changes update updates fix fixes "
    "src lib app main index init file files code module".split()
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


def word_set(text: str) -> set[str]:
    """Lower-case content words of ``text``; snake_case and camelCase identifiers are split."""
    words: set[str] = set()
    for token in _WORD.findall(text):
        parts = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token).replace("_", " ").lower().split()
        for part in parts:
            if len(part) >= 3 and part not in _STOP:
                words.add(part)
        low = token.lower()
        if len(low) >= 3 and low not in _STOP:
            words.add(low)
    return words


# --------------------------------------------------------------------------------------
# Row 40: ambiguity and clarification
# --------------------------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<=[.!?؟])\s+|\n+")
_NUMBER = re.compile(r"\d")

#: (kind, severity, pattern, question template). ``{m}`` is the matched text.
_AMBIGUITY_RULES: tuple[tuple[str, str, re.Pattern[str], str], ...] = (
    ("unmeasured_comparative", "high", re.compile(
        r"\b(faster|quicker|slower|better|cheaper|simpler|easier|smoother|lighter|smaller|bigger|larger|"
        r"improv(?:e|es|ed|ing)|optimi[sz](?:e|es|ed|ing)|enhanc(?:e|es|ed|ing)|more efficient|"
        r"reduc(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|boost|speed up)\b", re.I),
     "'{m}' compared to what, and by how much? Give the metric, today's baseline and the target "
     "(for example 'p95 under 200 ms, now 450 ms')."),
    ("subjective_quality", "medium", re.compile(
        r"\b(ui|ux|look and feel|polish(?:ed)?|modern|intuitive|clean|pretty|beautiful|nice|good|great|"
        r"robust|seamless|user[- ]friendly|elegant|professional|scalable|reliable|secure)\b", re.I),
     "What observable condition makes the work '{m}' (a checklist, a reference design, a number)?"),
    ("unquantified_quantity", "medium", re.compile(
        r"\b(some|several|many|few|lots of|a lot of|most|large|small|huge|big|high load|heavy traffic|"
        r"quickly|soon|real[- ]?time|fast|slow)\b", re.I),
     "How many / how large / how soon is '{m}', as a number with a unit?"),
    ("hedged_requirement", "medium", re.compile(
        r"\b(maybe|perhaps|possibly|probably|might|could|ideally|if possible|somehow|somewhat|kind of|"
        r"sort of|try to|as needed|as appropriate)\b", re.I),
     "Is '{m}' a requirement or a nice-to-have? State which parts are mandatory."),
    ("open_ended_list", "medium", re.compile(
        r"(\betc\b\.?|\band so on\b|\band more\b|\band the like\b|\band others\b|\bamong others\b|"
        r"\bstuff\b|\bvarious\b|\bthings\b)", re.I),
     "'{m}' leaves the list open. Which items exactly are in scope?"),
    ("placeholder", "high", re.compile(r"(\btbd\b|\btba\b|\btodo\b|\bfixme\b|\bto be (?:decided|determined)\b|\?\?+|\bxxx\b)", re.I),
     "The text still has a placeholder ('{m}'). What is the actual value or decision?"),
    ("universal_quantifier", "low", re.compile(r"\b(everything|anything|always|never|every case|all cases|any input)\b", re.I),
     "Does '{m}' really have no exceptions? Name the boundary cases that are out of scope."),
    ("undefined_reference", "high", re.compile(
        r"^\s*(?:please\s+)?(?:fix|change|update|handle|improve|redo|rewrite|clean up|do|make)\s+(it|this|that|them|those)\b", re.I),
     "What does '{m}' refer to (which file, function, endpoint or behaviour)?"),
    ("missing_actor_or_target", "low", re.compile(
        r"^\s*(?:please\s+)?(?:add|implement|create|build|support)\s+\w+(?:\s+\w+)?\s*[.!]?\s*$", re.I),
     "Where should this live and who or what uses it ('{m}')? Name the component and the caller."),
)
_ARABIC_RULES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("hedged_requirement", "medium", ("ربما", "ممكن", "يمكن", "اذا امكن", "إذا أمكن"),
     "هل '{m}' شرط إلزامي أم اختياري؟"),
    ("open_ended_list", "medium", ("إلخ", "الخ", "وغيرها", "وما شابه"), "'{m}' يترك القائمة مفتوحة. ما العناصر المطلوبة بالضبط؟"),
    ("unquantified_quantity", "medium", ("بعض", "كثير", "كبير", "صغير", "سريع", "بسرعة"), "كم تحديداً يعني '{m}' (رقم ووحدة)؟"),
    ("unmeasured_comparative", "high", ("أسرع", "أفضل", "تحسين", "أحسن"), "'{m}' مقارنة بماذا وبكم؟ حدد المقياس والهدف."),
)
_MISSING_ASPECT_QUESTIONS: dict[str, tuple[str, list[str]]] = {
    "error_handling": ("How should invalid input, failures and edge cases behave?",
                       ["Raise a typed error with a code", "Return a result object with success=false and details",
                        "Log and fall back to a documented default", "Retry with backoff, then fail"]),
    "persistence": ("Where does state live and how long must it last?",
                    ["In memory only (lost on restart)", "SQLite file", "PostgreSQL", "Existing store (name it)"]),
    "authentication": ("Who may call this and how are they identified?",
                       ["Public, no authentication", "Bearer token / JWT", "Session cookie", "Service-to-service key"]),
    "performance_target": ("What response time, throughput or size limit must it meet?",
                           ["p95 latency target (give ms)", "Throughput target (give requests/s)",
                            "Memory/size budget (give MB)", "No performance requirement"]),
    "interface_contract": ("What is the interface (inputs, outputs, calling convention)?",
                           ["Python function (give signature)", "HTTP endpoint (give method, path, schema)",
                            "CLI command (give flags)", "Event / message (give payload)"]),
}
_MAX_SENTENCE_SCAN = 1_500


def ambiguity_findings(text: str, *, limit: int = 40) -> list[dict[str, Any]]:
    """Per-sentence ambiguity findings; each carries the question that would resolve it.

    A sentence that states a number is not flagged for a comparative or an unquantified quantity:
    "p95 under 200 ms, faster than today's 450 ms" is measurable. Everything else is flagged once
    per (kind, matched text) so a repeated word does not drown the list.
    """
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in _SENTENCE.split(text):
        sentence = " ".join(raw.split())
        if not sentence:
            continue
        scan = sentence[:_MAX_SENTENCE_SCAN]
        has_number = bool(_NUMBER.search(scan))
        for kind, severity, pattern, template in _AMBIGUITY_RULES:
            if has_number and kind in ("unmeasured_comparative", "unquantified_quantity", "subjective_quality"):
                continue
            match = pattern.search(scan)
            if not match:
                continue
            evidence = match.group(0).strip()
            key = (kind, evidence.lower())
            if key in seen:
                continue
            seen.add(key)
            findings.append({
                "kind": kind, "severity": severity, "evidence": evidence, "sentence": sentence[:240],
                "question": template.format(m=evidence),
            })
        for kind, severity, words, template in _ARABIC_RULES:
            for word in words:
                if word in scan:
                    key = (kind, word)
                    if key not in seen:
                        seen.add(key)
                        findings.append({
                            "kind": kind, "severity": severity, "evidence": word, "sentence": sentence[:240],
                            "question": template.format(m=word),
                        })
                    break
        if len(findings) >= limit:
            break
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    return findings[:limit]


_MODAL_POS = re.compile(r"\b(must|shall|should|will|always|has to|needs? to)\s+(?!not\b|never\b)([a-z][a-z' -]{3,60})", re.I)
_MODAL_NEG = re.compile(r"\b(?:must not|shall not|should not|must never|never|cannot|can't|do not|don't|may not|won't)\s+([a-z][a-z' -]{3,60})", re.I)
_BOUND = re.compile(
    r"\b(?P<op>under|below|within|less than|at most|no more than|over|above|more than|at least|exactly|equal to)\s+"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>ms|milliseconds?|s|sec|seconds?|min|minutes?|%|mb|gb|kb|users?|requests?|rps|items?)\b", re.I)
_UPPER_OPS = {"under", "below", "within", "less than", "at most", "no more than"}
_LOWER_OPS = {"over", "above", "more than", "at least"}


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _STOP}


def requirement_conflicts(requirements: Sequence[str]) -> list[dict[str, Any]]:
    """Contradictions between requirements: opposite polarity on the same action, incompatible bounds."""
    out: list[dict[str, Any]] = []
    reqs = [" ".join(r.split())[:400] for r in requirements if r and r.strip()][:200]
    parsed = []
    for i, req in enumerate(reqs):
        pos = [(m.group(2), _content(m.group(2))) for m in _MODAL_POS.finditer(req)]
        neg = [(m.group(1), _content(m.group(1))) for m in _MODAL_NEG.finditer(req)]
        bounds = [(m.group("op").lower(), float(m.group("num")), m.group("unit").lower()) for m in _BOUND.finditer(req)]
        parsed.append((i, req, pos, neg, bounds, _content(req)))
    for a in range(len(parsed)):
        for b in range(a + 1, len(parsed)):
            ia, ra, pa, na, ba, ca = parsed[a]
            ib, rb, pb, nb, bb, cb = parsed[b]
            for (ptext, pset), (ntext, nset) in [(p, n) for p in pa for n in nb] + [(p, n) for p in pb for n in na]:
                shared = pset & nset
                if len(shared) >= 2 and len(shared) >= 0.6 * min(len(pset), len(nset)):
                    out.append({
                        "conflict_type": "Opposite polarity on the same action",
                        "requirements": [ra, rb], "shared_terms": sorted(shared),
                        "question": f"One requirement says the system must '{ptext.strip()}', another says it must not "
                                    f"'{ntext.strip()}'. Which one holds, or under what condition does each?",
                    })
                    break
            overlap = ca & cb
            if len(overlap) >= 2:
                for oa, na_, ua in ba:
                    for ob, nb_, ub in bb:
                        if ua != ub:
                            continue
                        upper_a, upper_b = oa in _UPPER_OPS, ob in _UPPER_OPS
                        lower_a, lower_b = oa in _LOWER_OPS, ob in _LOWER_OPS
                        bad = False
                        if upper_a and lower_b and na_ < nb_:
                            bad = True
                        if upper_b and lower_a and nb_ < na_:
                            bad = True
                        if oa == "exactly" and ob == "exactly" and na_ != nb_:
                            bad = True
                        if bad:
                            out.append({
                                "conflict_type": "Incompatible numeric bounds",
                                "requirements": [ra, rb], "shared_terms": sorted(overlap)[:6],
                                "question": f"'{oa} {na_:g} {ua}' and '{ob} {nb_:g} {ub}' cannot both hold. Which bound applies?",
                            })
    return out[:20]


def missing_aspect_questions(missing: Iterable[str]) -> list[dict[str, Any]]:
    out = []
    for aspect in missing:
        if aspect in _MISSING_ASPECT_QUESTIONS:
            question, options = _MISSING_ASPECT_QUESTIONS[aspect]
            out.append({"kind": f"missing_{aspect}", "severity": "medium", "question": question,
                        "options": options, "is_multi_select": False})
    return out


# --------------------------------------------------------------------------------------
# Row 7: imports and conventions
# --------------------------------------------------------------------------------------


def module_name_for_path(path: str) -> str:
    """``aura_core/domain/x.py`` -> ``aura_core.domain.x``; ``pkg/__init__.py`` -> ``pkg``."""
    clean = path.replace("\\", "/").strip("/")
    clean = re.sub(r"\.(py|pyi|js|jsx|ts|tsx|mjs|cjs|dart|go|rs)$", "", clean)
    parts = [p for p in clean.split("/") if p and p != "."]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def python_imports(source: str, module: str, *, is_package: bool = False) -> tuple[list[str], str | None, list[str]]:
    """``(imports, error, link_candidates)`` of a Python file.

    ``imports`` are absolute module names (relative ones resolved against ``module``), what a layer
    rule is judged on. ``link_candidates`` adds ``package.name`` for every ``from package import name``
    -- ``name`` may be a submodule -- and is only for deciding which scanned module an import points at.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return [], f"{type(exc).__name__}: {exc}", []
    package = module.split(".") if is_package else module.split(".")[:-1]
    found: list[str] = []
    links: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: max(0, len(package) - (node.level - 1))]
                target = ".".join([*base, *(node.module.split(".") if node.module else [])])
                target = target or ".".join(base)
            elif node.module:
                target = node.module
            else:
                continue
            found.append(target)
            links.extend(f"{target}.{a.name}" for a in node.names if a.name != "*")
    return list(dict.fromkeys(f for f in found if f)), None, list(dict.fromkeys(links))


_JS_IMPORT = re.compile(r"""(?:\bimport\s+(?:[^'"\n;]*?\s+from\s+)?|\brequire\(\s*|\bimport\(\s*|\bexport\s+[^'"\n;]*?\s+from\s+)['"]([^'"\n]+)['"]""")


def js_imports(source: str, module: str) -> list[str]:
    """Module specifiers of a JS/TS file; ``./x`` and ``../x`` are resolved against ``module``'s folder."""
    base = module.split(".")[:-1]
    out: list[str] = []
    for line in source.splitlines():
        if len(line) > 2_000:
            continue
        for spec in _JS_IMPORT.findall(line):
            if spec.startswith("."):
                parts = list(base)
                for seg in spec.split("/"):
                    if seg == "..":
                        if parts:
                            parts.pop()
                    elif seg not in (".", ""):
                        parts.append(re.sub(r"\.(js|jsx|ts|tsx|mjs|cjs)$", "", seg))
                out.append(".".join(parts))
            else:
                out.append(spec.replace("/", "."))
    return list(dict.fromkeys(out))


_SNAKE = re.compile(r"^_{0,2}[a-z][a-z0-9_]*_{0,2}$")
_PASCAL = re.compile(r"^_?[A-Z][A-Za-z0-9]*$")
_UPPER = re.compile(r"^_?[A-Z][A-Z0-9_]*$")


def convention_findings(path: str, source: str, *, max_line: int = 120, limit: int = 60) -> list[dict[str, Any]]:
    """Coding-convention findings for one file (Python: AST rules; every language: line length, tabs, markers)."""
    out: list[dict[str, Any]] = []

    def add(line: int, rule: str, message: str, severity: str = "warning") -> None:
        if len(out) < limit:
            out.append({"file": path, "line": line, "rule": rule, "severity": severity, "message": message})

    is_test = bool(re.search(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$", path))
    for number, line in enumerate(source.splitlines(), start=1):
        if len(line) > max_line:
            add(number, "line-too-long", f"line is {len(line)} characters (limit {max_line})", "info")
        if re.search(r"\s+$", line) and line.strip():
            add(number, "trailing-whitespace", "trailing whitespace", "info")
        if len(out) >= limit:
            break
    if not path.endswith(".py"):
        return out
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        add(getattr(exc, "lineno", 1) or 1, "syntax-error", f"{type(exc).__name__}: {exc}", "error")
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            add(node.lineno, "wildcard-import", f"'from {node.module or '.'} import *' hides where names come from")
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            add(node.lineno, "bare-except", "bare 'except:' also catches KeyboardInterrupt and SystemExit")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _SNAKE.match(node.name) and not node.name.startswith("test"):
                add(node.lineno, "function-naming", f"function '{node.name}' is not snake_case")
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    add(default.lineno, "mutable-default", f"'{node.name}' has a mutable default argument")
            if not node.name.startswith("_") and not is_test and ast.get_docstring(node) is None and len(node.body) > 3:
                add(node.lineno, "missing-docstring", f"public function '{node.name}' has no docstring", "info")
        elif isinstance(node, ast.ClassDef):
            if not _PASCAL.match(node.name):
                add(node.lineno, "class-naming", f"class '{node.name}' is not PascalCase")
            if not node.name.startswith("_") and not is_test and ast.get_docstring(node) is None:
                add(node.lineno, "missing-docstring", f"public class '{node.name}' has no docstring", "info")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print" and not is_test:
            add(node.lineno, "print-call", "print() in library code; use the logger", "info")
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper() and not _UPPER.match(target.id):
                    add(node.lineno, "constant-naming", f"constant '{target.id}' is not UPPER_SNAKE_CASE")
    return out


# --------------------------------------------------------------------------------------
# Row 45: diff against the task text
# --------------------------------------------------------------------------------------

_PATH_IN_TEXT = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,6})(?![\w-])")
_GENERATED = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|poetry\.lock|pnpm-lock\.yaml|Cargo\.lock|go\.sum|Pipfile\.lock|"
    r"requirements[\w.-]*\.lock|.*\.min\.(js|css)|.*\.snap|.*\.map)$|(^|/)(dist|build|node_modules|\.next|coverage)/")
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$")


def diff_file_lines(diff_text: str) -> dict[str, dict[str, list[str]]]:
    """``{path: {"added": [...], "removed": [...]}}`` from a unified diff (headers excluded)."""
    files: dict[str, dict[str, list[str]]] = {}
    current: str | None = None
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            current = m.group(2) if m else None
            if current:
                files.setdefault(current, {"added": [], "removed": []})
            in_hunk = False
        elif line.startswith("+++ "):
            path = line[4:].strip().split("\t")[0]
            if path != "/dev/null":
                current = path[2:] if path.startswith("b/") else path
                files.setdefault(current, {"added": [], "removed": []})
            in_hunk = False
        elif line.startswith("--- ") and not in_hunk:
            continue
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and current:
            if line.startswith("+"):
                files[current]["added"].append(line[1:])
            elif line.startswith("-"):
                files[current]["removed"].append(line[1:])
    return files


def task_alignment(task: str, diff_text: str, allowed_paths: Sequence[str] = ()) -> dict[str, Any]:
    """Which changed files the task text does not account for, and which changes are excessive.

    Heuristic and says so: a file is *related* when the task names it, shares a word with its path,
    is a test whose subject is related, is inside an allowed path, or has changed lines that use at
    least two of the task's distinctive words. The rest are ``unrelated_candidates`` for a human to
    judge; nothing here understands what the task *means*.
    """
    task_words = word_set(task)
    mentioned = {m.group(1).lower() for m in _PATH_IN_TEXT.finditer(task)}
    files = diff_file_lines(diff_text)
    allowed = [a.strip("/").lower() for a in allowed_paths if a and a.strip()]

    def in_allowed(path: str) -> bool:
        low = path.lower()
        return any(a in (".", "") or low == a or low.startswith(a.rstrip("/") + "/") for a in allowed)

    def explicit(path: str) -> bool:
        low = path.lower()
        return any(low == m or low.endswith("/" + m) or m.endswith("/" + low) for m in mentioned)

    verdicts: list[dict[str, Any]] = []
    related_names: set[str] = set()
    for path, lines in files.items():
        body_words = word_set(" ".join([*lines["added"][:400], *lines["removed"][:400]]))
        path_words = word_set(path.replace("/", " ").replace(".", " "))
        overlap_path = sorted(task_words & path_words)
        overlap_body = sorted(task_words & body_words)
        reasons: list[str] = []
        if explicit(path):
            reasons.append("the task names this file")
        if allowed and in_allowed(path):
            reasons.append("inside an allowed path")
        if overlap_path:
            reasons.append(f"path shares words with the task: {', '.join(overlap_path[:4])}")
        if len(overlap_body) >= 2:
            reasons.append(f"changed lines use task words: {', '.join(overlap_body[:5])}")
        stripped_added = ["".join(x.split()) for x in lines["added"]]
        stripped_removed = ["".join(x.split()) for x in lines["removed"]]
        reformat_only = bool(lines["added"]) and sorted(stripped_added) == sorted(stripped_removed)
        verdicts.append({
            "file": path, "related": bool(reasons), "reasons": reasons,
            "lines_added": len(lines["added"]), "lines_removed": len(lines["removed"]),
            "generated": bool(_GENERATED.search(path)), "reformat_only": reformat_only,
            "is_test": bool(_TEST_PATH.search(path)),
        })
        if reasons:
            related_names.add(re.sub(r"(^test_|_test$|\.test$|\.spec$)", "", re.sub(r"\.\w+$", "", path.rsplit("/", 1)[-1])))
    for v in verdicts:
        if not v["related"] and v["is_test"]:
            subject = re.sub(r"(^test_|_test$|\.test$|\.spec$)", "", re.sub(r"\.\w+$", "", v["file"].rsplit("/", 1)[-1]))
            if subject in related_names:
                v["related"] = True
                v["reasons"].append("test of a related file")
    unrelated = [v["file"] for v in verdicts if not v["related"]]
    excessive: list[dict[str, str]] = []
    sizes = sorted(v["lines_added"] + v["lines_removed"] for v in verdicts)
    median = sizes[len(sizes) // 2] if sizes else 0
    for v in verdicts:
        size = v["lines_added"] + v["lines_removed"]
        if v["reformat_only"] and size >= 4:
            excessive.append({"file": v["file"], "reason": "only whitespace or reordering changed (reformat noise)"})
        elif v["generated"] and not explicit(v["file"]):
            excessive.append({"file": v["file"], "reason": "generated or lock file the task does not mention"})
        elif size > 150 and size > 5 * max(median, 1) and len(verdicts) > 2:
            excessive.append({"file": v["file"], "reason": f"{size} changed lines, over 5x the median file ({median})"})
    if not task_words and not mentioned:
        alignment = "NO_USABLE_TASK_TEXT"
    elif unrelated or excessive:
        alignment = "REVIEW_UNRELATED_OR_EXCESSIVE_CHANGES"
    else:
        alignment = "ALIGNED"
    return {
        "alignment": alignment, "heuristic": True, "task_words": sorted(task_words)[:40],
        "files_named_in_task": sorted(mentioned), "unrelated_candidates": unrelated,
        "excessive_changes": excessive, "per_file": verdicts,
    }


# --------------------------------------------------------------------------------------
# Row 80: JSON-Schema subset
# --------------------------------------------------------------------------------------

_TYPE_CHECK = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool) or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, (list, tuple)),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}
_FORMAT = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "uuid": re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "date-time": re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([Zz]|[+-]\d{2}:?\d{2})?$"),
    "uri": re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^\s/]+\S*$"),
    "ipv4": re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$"),
}
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*[+*{]")
_MAX_ERRORS = 60
_MAX_SCHEMA_DEPTH = 40
_MAX_PATTERN = 200
_MAX_PATTERN_SUBJECT = 10_000


class _SchemaProblem(Exception):
    pass


def _pointer(path: tuple[Any, ...]) -> str:
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)


def _json_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _resolve_ref(root: Any, ref: str) -> Any:
    if not ref.startswith("#"):
        raise _SchemaProblem(f"only local '$ref' (starting with '#') is supported, got {ref!r}")
    node = root
    for part in [p for p in ref[1:].split("/") if p]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise _SchemaProblem(f"'$ref' {ref!r} does not resolve")
    return node


def validate_json_schema(data: Any, schema: Any, *, limit: int = _MAX_ERRORS) -> list[dict[str, Any]]:
    """Errors (``{path, keyword, message}``) of ``data`` against a JSON-Schema subset; ``[]`` if valid.

    Supported: ``type`` (name or list), ``enum``, ``const``, ``properties``, ``required``,
    ``additionalProperties`` (bool or schema), ``items`` (schema or
    tuple form), ``minItems``/``maxItems``/``uniqueItems``, ``minLength``/``maxLength``/``pattern``
    (bounded, nested quantifiers refused), ``minimum``/``maximum``/``exclusiveMinimum``/
    ``exclusiveMaximum``/``multipleOf``, ``format`` (email, uuid, date, date-time, uri, ipv4),
    ``allOf``/``anyOf``/``oneOf``/``not``, local ``$ref`` into ``definitions``/``$defs``. A keyword
    outside this list is reported once under ``unsupported_keyword``, never silently ignored.
    """
    errors: list[dict[str, Any]] = []
    sinks: list[list[dict[str, Any]]] = [errors]
    unsupported: set[str] = set()
    known = {
        "type", "enum", "const", "properties", "required", "additionalProperties", "items", "minItems",
        "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum", "maximum",
        "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "format", "allOf", "anyOf", "oneOf", "not",
        "$ref", "definitions", "$defs", "$schema", "$id", "title", "description", "default", "examples",
        "$comment", "deprecated", "readOnly", "writeOnly",
    }

    def err(path: tuple[Any, ...], keyword: str, message: str) -> None:
        sink = sinks[-1]
        if len(sink) < limit:
            sink.append({"path": _pointer(path), "keyword": keyword, "message": message})

    def _check_into(value: Any, sub: Any, path: tuple[Any, ...], depth: int) -> list[dict[str, Any]]:
        """Errors of ``value`` against ``sub`` alone, without adding them to the report."""
        sinks.append([])
        try:
            check(value, sub, path, depth)
        finally:
            found = sinks.pop()
        return found

    def check(value: Any, sub: Any, path: tuple[Any, ...], depth: int) -> None:
        if depth > _MAX_SCHEMA_DEPTH:
            raise _SchemaProblem("schema nests deeper than 40 levels")
        if sub is True or sub == {}:
            return
        if sub is False:
            err(path, "false", "no value is allowed here")
            return
        if not isinstance(sub, dict):
            raise _SchemaProblem(f"a schema must be an object or boolean, got {type(sub).__name__} at {_pointer(path)}")
        for key in sub:
            if key not in known and not key.startswith("x-"):
                unsupported.add(key)
        if "$ref" in sub:
            check(value, _resolve_ref(schema, str(sub["$ref"])), path, depth + 1)
        expected = sub.get("type")
        if expected is not None:
            names = expected if isinstance(expected, list) else [expected]
            for name in names:
                if name not in _TYPE_CHECK:
                    raise _SchemaProblem(f"unknown type {name!r} in the schema at {_pointer(path)}")
            if not any(_TYPE_CHECK[n](value) for n in names):
                err(path, "type", f"expected {' or '.join(names)}, got {_type_name(value)}")
                return
        if "enum" in sub and isinstance(sub["enum"], list) and not any(_json_equal(value, e) for e in sub["enum"]):
            err(path, "enum", f"{_short(value)} is not one of {_short(sub['enum'])}")
        if "const" in sub and not _json_equal(value, sub["const"]):
            err(path, "const", f"must equal {_short(sub['const'])}")
        if isinstance(value, str):
            if "minLength" in sub and len(value) < sub["minLength"]:
                err(path, "minLength", f"{len(value)} characters; at least {sub['minLength']} required")
            if "maxLength" in sub and len(value) > sub["maxLength"]:
                err(path, "maxLength", f"{len(value)} characters; at most {sub['maxLength']} allowed")
            if "pattern" in sub:
                pat = str(sub["pattern"])
                if len(pat) > _MAX_PATTERN or _NESTED_QUANTIFIER.search(pat):
                    err(path, "pattern", "pattern refused (too long or has a nested quantifier that can backtrack without bound)")
                else:
                    try:
                        if re.search(pat, value[:_MAX_PATTERN_SUBJECT]) is None:
                            err(path, "pattern", f"does not match {pat!r}")
                    except re.error as exc:
                        raise _SchemaProblem(f"invalid pattern {pat!r}: {exc}") from exc
            if "format" in sub and sub["format"] in _FORMAT and not _FORMAT[sub["format"]].match(value[:2_000]):
                err(path, "format", f"not a valid {sub['format']}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            for kw, bad in (("minimum", lambda v, b: v < b), ("maximum", lambda v, b: v > b),
                            ("exclusiveMinimum", lambda v, b: v <= b), ("exclusiveMaximum", lambda v, b: v >= b)):
                if kw in sub and isinstance(sub[kw], (int, float)) and bad(value, sub[kw]):
                    err(path, kw, f"{value} violates {kw} {sub[kw]}")
            step = sub.get("multipleOf")
            if isinstance(step, (int, float)) and step > 0 and not math.isclose(round(value / step) * step, value, rel_tol=1e-9, abs_tol=1e-12):
                err(path, "multipleOf", f"{value} is not a multiple of {step}")
        if isinstance(value, dict):
            props = sub.get("properties") if isinstance(sub.get("properties"), dict) else {}
            for name in sub.get("required", []) if isinstance(sub.get("required"), list) else []:
                if name not in value:
                    err(path + (name,), "required", f"missing required property '{name}'")
            for name, child in props.items():
                if name in value:
                    check(value[name], child, path + (name,), depth + 1)
            extra = sub.get("additionalProperties", True)
            for name in value:
                if name not in props:
                    if extra is False:
                        err(path + (name,), "additionalProperties", f"unexpected property '{name}'")
                    elif isinstance(extra, dict):
                        check(value[name], extra, path + (name,), depth + 1)
        if isinstance(value, (list, tuple)):
            if "minItems" in sub and len(value) < sub["minItems"]:
                err(path, "minItems", f"{len(value)} items; at least {sub['minItems']} required")
            if "maxItems" in sub and len(value) > sub["maxItems"]:
                err(path, "maxItems", f"{len(value)} items; at most {sub['maxItems']} allowed")
            if sub.get("uniqueItems") is True:
                seen: list[Any] = []
                for index, item in enumerate(value[:2_000]):
                    if any(_json_equal(item, s) for s in seen):
                        err(path + (index,), "uniqueItems", "duplicate item")
                        break
                    seen.append(item)
            items = sub.get("items")
            if isinstance(items, dict) or isinstance(items, bool):
                for index, item in enumerate(value[:5_000]):
                    check(item, items, path + (index,), depth + 1)
            elif isinstance(items, list):
                for index, (item, child) in enumerate(zip(value, items)):
                    check(item, child, path + (index,), depth + 1)
        for keyword in ("allOf", "anyOf", "oneOf"):
            branches = sub.get(keyword)
            if not isinstance(branches, list):
                continue
            results = [_check_into(value, b, path, depth + 1) for b in branches]
            ok = [not r for r in results]
            if keyword == "allOf":
                for r in results:
                    for e in r:
                        err(*(_split(e)))
            elif keyword == "anyOf" and not any(ok):
                err(path, "anyOf", "matches none of the alternatives: " + "; ".join(r[0]["message"] for r in results if r)[:300])
            elif keyword == "oneOf" and sum(ok) != 1:
                err(path, "oneOf", f"matches {sum(ok)} alternatives; exactly one required")
        if "not" in sub and not _check_into(value, sub["not"], path, depth + 1):
            err(path, "not", "matches a schema it must not match")

    def _split(e: dict[str, Any]) -> tuple[tuple[Any, ...], str, str]:
        return (_parse_pointer(e["path"]), e["keyword"], e["message"])

    check(data, schema, (), 0)
    if unsupported:
        errors.append({"path": "$", "keyword": "unsupported_keyword",
                       "message": f"schema keyword(s) not checked by this tool: {sorted(unsupported)}"})
    return errors[: limit + 1]


def _parse_pointer(pointer: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for m in re.finditer(r"\[(\d+)\]|\.([^.\[\]]+)", pointer[1:]):
        parts.append(int(m.group(1)) if m.group(1) is not None else m.group(2))
    return tuple(parts)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    return "object" if isinstance(value, dict) else type(value).__name__


def _short(value: Any) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= 80 else text[:77] + "..."


# --------------------------------------------------------------------------------------
# Row 81: non-Python structure extraction
# --------------------------------------------------------------------------------------

_LANG_ALIASES = {
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript", "node": "javascript",
    "ts": "typescript", "tsx": "typescript", "golang": "go", "rs": "rust", "kt": "kotlin", "cs": "csharp",
    "c#": "csharp", "py": "python",
}
_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".dart": "dart", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".cs": "csharp",
}
BRACE_LANGUAGES = frozenset({"javascript", "typescript", "dart", "go", "rust", "java", "kotlin", "csharp"})
_CONTROL = frozenset(
    "if for while switch catch return function with else do try typeof await new super import require "
    "delete void yield it describe test beforeEach afterEach beforeAll afterAll match loop fn where "
    "synchronized when foreach using lock sizeof defer select go case throw assert print".split()
)
_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`[^`]*`')
_RUST_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_LINE_C = re.compile(r"//[^\n]*")


def resolve_language(language: str | None, filename: str | None) -> str | None:
    lang = (language or "").strip().lower()
    lang = _LANG_ALIASES.get(lang, lang)
    if lang and lang != "auto":
        return lang
    if filename:
        m = re.search(r"\.[A-Za-z0-9]+$", filename)
        if m:
            return _EXT_LANG.get(m.group(0).lower())
    return None


#: Where something that needs the tokenizer's attention can start: a comment, a quote, a backtick or a Rust raw string.
_TOKEN_START = re.compile(r"//|/\*|\"|'|`|\br#*\"|@\"|\$@?\"")
_RUST_RAW = re.compile(r'r(#*)"')
_CHAR_LITERAL = re.compile(r"'(?:\\(?:u\{[0-9a-fA-F]+\}|x[0-9a-fA-F]{2}|.)|[^\\'\n])'")
#: A ``/`` right after one of these starts a regular-expression literal (JavaScript/TypeScript), not a division.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = ("return", "typeof", "case", "in", "of", "delete", "void", "throw", "yield", "await")


def _end_of_quoted(source: str, i: int, quote: str, *, multiline: bool = False) -> int:
    """Index just past the string that starts at ``i`` (escapes honoured); an unterminated one ends at its line."""
    n, j = len(source), i + 1
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == quote:
            return j + 1
        if c == "\n" and not multiline:
            return j
        j += 1
    return n


def _end_of_template(source: str, i: int) -> int:
    """Index just past the JavaScript template literal at ``i``, ``${ ... }`` (with nested templates) included."""
    n, j = len(source), i + 1
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
        elif c == "`":
            return j + 1
        elif source.startswith("${", j):
            depth, j = 1, j + 2
            while j < n and depth:
                d = source[j]
                if d == "`":
                    j = _end_of_template(source, j)
                    continue
                if d in "\"'":
                    j = _end_of_quoted(source, j, d)
                    continue
                depth += (d == "{") - (d == "}")
                j += 1
        else:
            j += 1
    return n


def _end_of_regex(source: str, i: int) -> int:
    """Index just past the regex literal at ``i`` (``[...]`` classes may contain ``/``), or ``i`` if it is not one."""
    n, j, in_class = len(source), i + 1, False
    while j < n:
        c = source[j]
        if c == "\n":
            return i
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and (source[j].isalpha()):
                j += 1
            return j
        j += 1
    return i


def _regex_allowed(source: str, i: int) -> bool:
    k = i - 1
    while k >= 0 and source[k] in " \t":
        k -= 1
    if k < 0 or source[k] == "\n" or source[k] in _REGEX_PRECEDERS:
        return True
    word = re.search(r"([A-Za-z_]+)$", source[max(0, k - 12):k + 1])
    return bool(word and word.group(1) in _REGEX_KEYWORDS)


def _blank(source: str, lang: str) -> str:
    """Source with comments, string contents and regex literals blanked, newlines and offsets kept.

    A single-pass tokenizer, not the regex pair it replaced: it understands JavaScript template literals with nested
    ``${...}``, regular-expression literals, Go raw strings, Java/Kotlin text blocks, C# verbatim and interpolated
    strings, Rust raw strings and character literals versus lifetimes, and block comments that nest (Rust, Kotlin).
    A brace or keyword inside any of those no longer reaches the declaration and brace-matching code.
    """
    n = len(source)
    out = list(source)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    nested = lang in ("rust", "kotlin")
    js = lang in ("javascript", "typescript")
    i = 0
    while i < n:
        m = _TOKEN_START.search(source, i)
        j = m.start() if m else n
        if js:  # a regex literal can start before the next token-start candidate
            slash = source.find("/", i, j)
            while slash != -1:
                if source.startswith(("//", "/*"), slash):
                    break
                if _regex_allowed(source, slash):
                    end = _end_of_regex(source, slash)
                    if end > slash:
                        blank(slash, end)
                        i = end
                        break
                slash = source.find("/", slash + 1, j)
            else:
                slash = -1
            if slash != -1 and i > slash:
                continue
        if not m:
            break
        i, token = m.start(), m.group(0)
        if token == "//":
            end = source.find("\n", i)
            end = n if end == -1 else end
            blank(i, end)
            i = end
        elif token == "/*":
            depth, k = 1, i + 2
            while k < n and depth:
                if source.startswith("*/", k):
                    depth -= 1
                    k += 2
                elif nested and source.startswith("/*", k):
                    depth += 1
                    k += 2
                else:
                    k += 1
            blank(i, k)
            i = k
        elif lang == "rust" and token.startswith("r") and token != '"':
            raw = _RUST_RAW.match(source, i)
            closing = '"' + (raw.group(1) if raw else "")
            end = source.find(closing, raw.end()) if raw else -1
            end = n if end == -1 else end + len(closing)
            blank(i, end)
            i = end
        elif token in ('@"', '$@"', '$"') and lang == "csharp":
            quote = i + len(token) - 1
            if "@" in token:  # verbatim: only "" escapes a quote
                k = quote + 1
                while k < n:
                    if source[k] == '"':
                        if source.startswith('""', k):
                            k += 2
                            continue
                        k += 1
                        break
                    k += 1
                end = k
            else:
                end = _end_of_quoted(source, quote, '"')
            blank(i, end)
            i = end
        elif token in ('@"', '$@"', '$"') or (lang != "rust" and token.startswith("r")):
            i += 1  # `$"`/`@"`/`r"` in a language that has no such literal: just an identifier char
        elif token == '"':
            if source.startswith('"""', i) and lang in ("java", "kotlin", "csharp", "dart"):
                end = source.find('"""', i + 3)
                end = n if end == -1 else end + 3
            else:
                end = _end_of_quoted(source, i, '"', multiline=lang in ("javascript", "typescript") and False)
            blank(i, end)
            i = end
        elif token == "'":
            if lang in ("javascript", "typescript", "dart", "kotlin") and lang != "kotlin":
                end = _end_of_quoted(source, i, "'")
            else:
                ch = _CHAR_LITERAL.match(source, i)
                end = ch.end() if ch else (_end_of_quoted(source, i, "'") if lang in ("kotlin",) else i + 1)
            blank(i, end)
            i = end
        elif token == "`":
            end = _end_of_template(source, i) if js else (source.find("`", i + 1) + 1 or n)
            if lang in ("go",) or js:
                blank(i, end)
                i = end
            else:
                i += 1
        else:
            i += 1
    return "".join(out)


_DECISION = re.compile(r"\b(?:if|for|foreach|while|case|catch|when|elif|except)\b|&&|\|\||\?\?|\?(?![.?:])")


def cyclomatic(blanked_body: str) -> int:
    """McCabe-style complexity of a function body (comments and strings already blanked): 1 + decision points.

    Counted by keyword and operator on the token-clean text, not by a control-flow graph, so a ternary and a
    ``catch`` each add one, ``else`` and ``default`` add none, and a nested function's decisions are counted in
    its parent as well as in itself. An estimate that ranks functions; not a certified metric.
    """
    return 1 + len(_DECISION.findall(blanked_body))


_DECLS: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "javascript": (
        ("class", re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^>(]*>)?\s*\(")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=]+?)?=>")),
        ("method", re.compile(r"^\s*(?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^>(]*>)?\s*\([^;{]*\)\s*(?::\s*[^{;=]+)?\{")),
    ),
    "go": (
        ("struct", re.compile(r"^\s*type\s+([A-Za-z_]\w*)(?:\[[^\]]*\])?\s+struct\b")),
        ("interface", re.compile(r"^\s*type\s+([A-Za-z_]\w*)(?:\[[^\]]*\])?\s+interface\b")),
        ("method", re.compile(r"^\s*func\s+\(\s*\w*\s*\*?\s*([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*\)\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(")),
        ("function", re.compile(r"^\s*func\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(")),
    ),
    "rust": (
        ("struct", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z_]\w*)")),
        ("enum", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+([A-Za-z_]\w*)")),
        ("trait", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:unsafe\s+)?trait\s+([A-Za-z_]\w*)")),
        ("impl", re.compile(r"^\s*(?:unsafe\s+)?impl(?:<[^>]*>)?\s+(?:[\w:<>,\s&']+?\s+for\s+)?([A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)")),
    ),
    "java": (
        ("class", re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|sealed)\s+)*(?:class|record)\s+([A-Za-z_]\w*)")),
        ("interface", re.compile(r"^\s*(?:(?:public|private|protected|static|sealed)\s+)*@?interface\s+([A-Za-z_]\w*)")),
        ("enum", re.compile(r"^\s*(?:(?:public|private|protected|static)\s+)*enum\s+([A-Za-z_]\w*)")),
        ("method", re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|native|default)\s+)+(?:<[^>]+>\s+)?[\w<>\[\],.? ]+?\s+([A-Za-z_]\w*)\s*\([^;{]*\)\s*(?:throws\s+[\w., ]+)?\s*[{;]")),
        # Package-private (no modifier): `int size() {`. Control keywords are excluded as the first word and as the
        # name, and it must open a body with `{`, so `return f(x);` and `else if (x) {` are not methods.
        ("method", re.compile(r"^\s*(?!(?:return|else|new|throw|case|assert|yield|do|try|await|goto|break|continue)\b)(?:<[^>]+>\s+)?[\w<>\[\],.?]+(?: [\w<>\[\],.?]+)*\s+(?!(?:if|for|while|switch|catch|synchronized|return)\b)([A-Za-z_]\w*)\s*\([^;{=]*\)\s*(?:throws\s+[\w., ]+)?\s*\{")),
    ),
    "csharp": (
        ("class", re.compile(r"^\s*(?:(?:public|private|protected|internal|static|sealed|abstract|partial)\s+)*(?:class|record|struct)\s+([A-Za-z_]\w*)")),
        ("interface", re.compile(r"^\s*(?:(?:public|private|protected|internal|partial)\s+)*interface\s+([A-Za-z_]\w*)")),
        ("enum", re.compile(r"^\s*(?:(?:public|private|protected|internal)\s+)*enum\s+([A-Za-z_]\w*)")),
        ("method", re.compile(r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed|extern)\s+)+[\w<>\[\],.? ]+?\s+([A-Za-z_]\w*)\s*\([^;{]*\)\s*(?:where [^{]+)?\s*(?:\{|=>)")),
        # No modifier (private by default in C#): `int Size() {`. Same keyword exclusions as the Java rule.
        ("method", re.compile(r"^\s*(?!(?:return|else|new|throw|case|yield|do|try|await|goto|break|continue|using|lock)\b)[\w<>\[\],.?]+(?: [\w<>\[\],.?]+)*\s+(?!(?:if|for|foreach|while|switch|catch|lock|using|return)\b)([A-Za-z_]\w*)\s*\([^;{=]*\)\s*(?:where [^{]+)?\s*(?:\{|=>)")),
    ),
    "kotlin": (
        ("class", re.compile(r"^\s*(?:(?:public|private|protected|internal|abstract|open|sealed|data|enum|inner)\s+)*(?:class|object)\s+([A-Za-z_]\w*)")),
        ("interface", re.compile(r"^\s*(?:(?:public|private|protected|internal|sealed|fun)\s+)*interface\s+([A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:(?:public|private|protected|internal|override|open|suspend|inline|operator|infix|tailrec)\s+)*fun\s+(?:<[^>]+>\s+)?(?:[\w.<>?]+\.)?([A-Za-z_]\w*)\s*\(")),
    ),
    "dart": (
        ("class", re.compile(r"^\s*(?:abstract\s+|base\s+|final\s+|sealed\s+|interface\s+)*(?:class|mixin|extension)\s+([A-Za-z_]\w*)")),
        ("enum", re.compile(r"^\s*enum\s+([A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:static\s+)?(?:Future<[^>]*>|Stream<[^>]*>|void|[A-Za-z_][\w<>?,\s]*?)\s+([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\([^;{]*\)\s*(?:async\*?\s*)?(?:\{|=>)")),
    ),
}
_DECLS["typescript"] = (
    ("interface", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?interface\s+([A-Za-z_$][\w$]*)")),
    ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*(?:<[^=]*>)?\s*=")),
    ("enum", re.compile(r"^\s*(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)")),
    *_DECLS["javascript"],
)
_IMPORT_PATTERNS: dict[str, re.Pattern[str]] = {
    "javascript": re.compile(r"""(?:\bimport\s+(?:[^'"\n;]*?\s+from\s+)?|\brequire\(\s*|\bimport\(\s*)['"]([^'"\n]+)['"]"""),
    "go": re.compile(r'^\s*(?:import\s+)?(?:[\w.]+\s+)?"([^"]+)"\s*$'),
    "rust": re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([^;]+);"),
    "java": re.compile(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;"),
    "csharp": re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;"),
    "kotlin": re.compile(r"^\s*import\s+([\w.*]+)"),
    "dart": re.compile(r"""^\s*(?:import|export|part)\s+['"]([^'"]+)['"]"""),
}
_IMPORT_PATTERNS["typescript"] = _IMPORT_PATTERNS["javascript"]
_CALL = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(|\.\s*([A-Za-z_$][\w$]*)\s*\(")
_MAX_SCAN_LINE = 400
_MAX_SYMBOLS = 3_000


def _brace_pairs(blanked: str) -> dict[int, int]:
    """``{index of '{': index of its '}'}`` in one pass (an unclosed brace has no entry)."""
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for i, ch in enumerate(blanked):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            pairs[stack.pop()] = i
    return pairs


def scan_source(source: str, lang: str) -> dict[str, Any]:
    """Symbols (kind, name, line, parent, signature, exported, calls), imports and a call graph.

    Brace matching over comment- and string-blanked text; a heuristic, not a parser. Lines longer
    than 400 characters (minified code) are skipped and counted; at most 3,000 symbols are kept.
    """
    blanked = _blank(source, lang)
    pairs = _brace_pairs(blanked)
    lines = blanked.split("\n")
    raw_lines = source.split("\n")
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    decls = _DECLS.get(lang, ())
    symbols: list[dict[str, Any]] = []
    skipped = 0
    for index, line in enumerate(lines):
        if len(symbols) >= _MAX_SYMBOLS:
            break
        if len(line) > _MAX_SCAN_LINE:
            skipped += 1
            continue
        if not line.strip():
            continue
        for kind, pattern in decls:
            m = pattern.match(line)
            if not m:
                continue
            if lang == "go" and kind == "method":
                parent, name = m.group(1), m.group(2)
            else:
                parent, name = None, m.group(1)
            if name in _CONTROL:
                continue
            window_start = offsets[index] + m.start()
            last = min(index + 3, len(lines) - 1)
            window_end = offsets[last] + len(lines[last])
            window = blanked[window_start:window_end]
            brace = -1
            if kind in ("function", "method"):
                body = re.search(r"\)[^{;]*\{", window) or re.search(r"=>\s*\{", window)
                if body:
                    brace = window_start + body.end() - 1
            else:
                first = window.find("{")
                semi = window.find(";")
                if first != -1 and (semi == -1 or first < semi):
                    brace = window_start + first
            end_line = index + 1
            body_range: tuple[int, int] | None = None
            if brace != -1:
                close = pairs.get(brace, -1)
                if close != -1:
                    end_line = bisect.bisect_right(offsets, close)
                    body_range = (brace, close)
            symbols.append({
                "kind": kind, "name": name, "line": index + 1, "end_line": end_line, "parent": parent,
                "signature": raw_lines[index].strip()[:160],
                "exported": bool(re.match(r"^\s*(?:export\b|pub\b|public\b)", line)) or (lang == "go" and name[:1].isupper()),
                "_body": body_range,
            })
            break
    containers = [s for s in symbols if s["kind"] in ("class", "struct", "interface", "trait", "impl", "enum") and s["_body"]]
    for sym in symbols:
        if sym["parent"] is None:
            inside = [c for c in containers if c is not sym and c["line"] < sym["line"] <= c["end_line"]]
            if inside:
                sym["parent"] = max(inside, key=lambda c: c["line"])["name"]
        if sym["parent"] and sym["kind"] == "function":
            sym["kind"] = "method"
    if lang in ("javascript", "typescript"):
        # `foo(x) {` outside a class is a call in an expression or a nested block, not a declaration
        symbols = [s for s in symbols if not (s["kind"] == "method" and s["parent"] is None)]
    call_graph: dict[str, list[str]] = {}
    budget = 2_000_000  # characters of function bodies scanned for calls in all (nested bodies are scanned again)
    for sym in symbols:
        body = sym.pop("_body")
        if sym["kind"] in ("function", "method") and body and budget > 0:
            text = blanked[body[0] + 1: body[1]][:50_000]
            budget -= len(text)
            names: list[str] = []
            for m in _CALL.finditer(text):
                called = m.group(1) or m.group(2)
                if called and called not in _CONTROL and called != sym["name"] and called not in names:
                    names.append(called)
                if len(names) >= 30:
                    break
            sym["calls"] = names
            sym["complexity"] = cyclomatic(blanked[body[0] + 1: body[1]][:200_000])
            call_graph[f"{sym['parent']}.{sym['name']}" if sym["parent"] else sym["name"]] = names
    imports: list[dict[str, Any]] = []
    pattern = _IMPORT_PATTERNS.get(lang)
    in_go_block = False
    if pattern is not None:
        for index, line in enumerate(lines):
            if len(line) > _MAX_SCAN_LINE:
                continue
            if lang == "go":
                if re.match(r"^\s*import\s*\(", line):
                    in_go_block = True
                    continue
                if in_go_block and line.strip() == ")":
                    in_go_block = False
                    continue
                raw_line = raw_lines[index]
                if in_go_block or re.match(r"^\s*import\s+", raw_line):
                    m = pattern.match(raw_line)
                    if m:
                        imports.append({"module": m.group(1), "line": index + 1})
                continue
            if lang in ("javascript", "typescript"):
                raw_line = raw_lines[index]
                if not re.search(r"\b(?:import|require|export)\b", line):
                    continue  # the keyword is inside a comment or string
                for spec in pattern.findall(raw_line):
                    imports.append({"module": spec, "line": index + 1})
                continue
            m = pattern.match(raw_lines[index] if lang in ("dart", "rust") else line)
            if m:
                imports.append({"module": m.group(1).strip(), "line": index + 1})
    result: dict[str, Any] = {
        "language": lang, "symbols": symbols, "imports": imports, "call_graph": call_graph,
        "skipped_long_lines": skipped,
        "complexity": {
            "method": "McCabe-style count of decision points on tokenizer-clean text (an estimate that ranks "
                      "functions, not a control-flow graph)",
            "functions": sum(1 for s in symbols if "complexity" in s),
            "most_complex": [
                {"name": f"{s['parent']}.{s['name']}" if s["parent"] else s["name"], "line": s["line"],
                 "complexity": s["complexity"]}
                for s in sorted((s for s in symbols if "complexity" in s), key=lambda s: -s["complexity"])[:5]],
        },
    }
    if lang == "go":
        pkg = re.search(r"(?m)^\s*package\s+(\w+)", blanked)
        result["package"] = pkg.group(1) if pkg else None
    return result


def python_structure(source: str) -> dict[str, Any]:
    """The same shape as :func:`scan_source` for Python, from the real AST (calls included)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return {"language": "python", "error": f"{type(exc).__name__}: {exc}", "symbols": [], "imports": [], "call_graph": {}}
    symbols: list[dict[str, Any]] = []
    graph: dict[str, list[str]] = {}

    def calls_of(node: ast.AST) -> list[str]:
        names: list[str] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                f = child.func
                if isinstance(f, ast.Name):
                    name = f.id
                elif isinstance(f, ast.Attribute):
                    own = isinstance(f.value, ast.Name) and f.value.id in ("self", "cls")
                    name = f.attr if own else ast.unparse(f)[:60]
                else:
                    name = None
                if name and name not in names:
                    names.append(name)
            if len(names) >= 30:
                break
        return names

    def visit(body: list[ast.stmt], parent: str | None) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                symbols.append({"kind": "class", "name": node.name, "line": node.lineno, "end_line": node.end_lineno or node.lineno,
                                "parent": parent, "signature": f"class {node.name}({', '.join(ast.unparse(b) for b in node.bases)})",
                                "exported": not node.name.startswith("_"), "doc": (ast.get_docstring(node) or "").split("\n")[0]})
                visit(node.body, node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                calls = calls_of(node)
                symbols.append({"kind": "method" if parent else "function", "name": node.name, "line": node.lineno,
                                "end_line": node.end_lineno or node.lineno, "parent": parent,
                                "signature": f"{prefix} {node.name}({ast.unparse(node.args)}){ret}",
                                "exported": not node.name.startswith("_"), "calls": calls,
                                "doc": (ast.get_docstring(node) or "").split("\n")[0]})
                graph[f"{parent}.{node.name}" if parent else node.name] = calls
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for sub in (getattr(node, "body", []), getattr(node, "orelse", []), getattr(node, "finalbody", [])):
                    visit(sub, parent)
                for handler in getattr(node, "handlers", []):
                    visit(handler.body, parent)

    visit(tree.body, None)
    imports: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend({"module": a.name, "line": node.lineno} for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            imports.extend({"module": f"{mod}.{a.name}" if mod and not mod.endswith(".") else f"{mod}{a.name}",
                            "line": node.lineno} for a in node.names)
    return {"language": "python", "symbols": symbols, "imports": imports, "call_graph": graph, "skipped_long_lines": 0}


# --------------------------------------------------------------------------------------
# Row 48: quizzes from what the source says
# --------------------------------------------------------------------------------------


def _digest(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("\u0000".join(parts).encode("utf-8", "replace")).digest()[:8], "big")


def _options(correct: str, wrong: Sequence[str], seed: int) -> tuple[list[str], str]:
    """Four options with the right one at a seed-chosen slot; returns ``(options, letter)``."""
    pool = [w for w in dict.fromkeys(wrong) if w != correct][:3]
    slot = seed % (len(pool) + 1)
    opts = pool[:slot] + [correct] + pool[slot:]
    return [f"{'ABCD'[i]}) {o}" for i, o in enumerate(opts)], "ABCD"[slot]


def _py_facts(source: str) -> list[dict[str, Any]] | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("__")]
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if not funcs and not classes:
        return None
    facts: list[dict[str, Any]] = []
    sigs = {f.name: ast.unparse(f.args) or "(no parameters)" for f in funcs}
    for f in funcs:
        others = [v for k, v in sigs.items() if k != f.name]
        wrong = list(others)
        params = [a.arg for a in [*f.args.posonlyargs, *f.args.args, *f.args.kwonlyargs]]
        if len(params) >= 2:
            wrong.append(", ".join(reversed(params)))
        if params:
            wrong.append(", ".join(params[:-1]) or "(no parameters)")
        wrong.append(", ".join([*params, "timeout"]))
        facts.append({"type": "signature", "subject": f.name, "line": f.lineno,
                      "q": f"Which parameter list does `{f.name}` declare?", "correct": sigs[f.name], "wrong": wrong,
                      "why": f"`{f.name}` is defined as `def {f.name}({sigs[f.name]})` on line {f.lineno}."})
        if f.returns is not None:
            ret = ast.unparse(f.returns)
            facts.append({"type": "return_type", "subject": f.name, "line": f.lineno,
                          "q": f"What does `{f.name}` declare as its return annotation?", "correct": ret,
                          "wrong": [r for r in ["None", "str", "int", "bool", "dict", "list"] if r != ret],
                          "why": f"The annotation on line {f.lineno} is `-> {ret}`."})
        facts.append({"type": "true_false", "subject": f.name, "line": f.lineno,
                      "q": f"`{f.name}` is declared with `async def`.", "correct": "True" if isinstance(f, ast.AsyncFunctionDef) else "False",
                      "why": f"`{f.name}` is {'an async' if isinstance(f, ast.AsyncFunctionDef) else 'a regular'} function (line {f.lineno})."})
        doc = ast.get_docstring(f)
        if doc:
            first = doc.split("\n")[0].strip()
            if len(first.split()) >= 4:
                facts.append({"type": "docstring_match", "subject": f.name, "line": f.lineno,
                              "q": f'Which function is documented as: "{first[:140]}"?', "correct": f.name,
                              "wrong": [n for n in sigs if n != f.name],
                              "why": f"That is the first docstring line of `{f.name}` (line {f.lineno})."})
        branches = sum(isinstance(c, (ast.If, ast.For, ast.While, ast.Try, ast.IfExp, ast.ExceptHandler, ast.AsyncFor)) for c in ast.walk(f))
        if branches:
            facts.append({"type": "code_reading", "subject": f.name, "line": f.lineno,
                          "q": f"How many branching constructs (if/for/while/try/except/conditional expression) does `{f.name}` contain?",
                          "correct": str(branches), "wrong": [str(branches + d) for d in (1, 2, -1) if branches + d >= 0],
                          "why": f"Counting if/for/while/try/except nodes inside `{f.name}` gives {branches}."})
    for c in classes:
        methods = [m.name for m in c.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("__")]
        for m in methods[:2]:
            others = [o.name for o in classes if o.name != c.name]
            if others:
                facts.append({"type": "which_class", "subject": m, "line": c.lineno,
                              "q": f"Which class defines the method `{m}`?", "correct": c.name, "wrong": others,
                              "why": f"`{m}` is a method of `{c.name}` (line {c.lineno})."})
        if c.bases:
            base = ast.unparse(c.bases[0])
            facts.append({"type": "inheritance", "subject": c.name, "line": c.lineno,
                          "q": f"What does `{c.name}` inherit from?", "correct": base,
                          "wrong": [x for x in ["object", "Exception", "dict", "Protocol", "ABC"] if x != base],
                          "why": f"The class statement on line {c.lineno} lists `{base}` as its first base."})
    imported = sorted({a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                      | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and not n.level})
    if imported:
        decoys = [m for m in ("sqlite3", "socket", "smtplib", "ftplib", "turtle", "tkinter", "curses") if m not in imported]
        facts.append({"type": "imports", "subject": "imports", "line": 1,
                      "q": "Which module does this file import?", "correct": imported[0], "wrong": decoys,
                      "why": f"`{imported[0]}` appears in an import statement; the decoys do not."})
    return facts


def _prose_facts(text: str, concepts: list[str]) -> list[dict[str, Any]]:
    sentences = [" ".join(s.split()) for s in re.split(r"(?<=[.!?])\s+|\n{2,}", text)]
    sentences = [s for s in sentences if 6 <= len(s.split()) <= 45 and len(s) <= 300]
    facts: list[dict[str, Any]] = []
    used: set[str] = set()
    for sent in sentences:
        term = next((c for c in concepts if re.search(rf"\b{re.escape(c)}\b", sent) and c not in used), None)
        if not term:
            continue
        used.add(term)
        masked = re.sub(rf"\b{re.escape(term)}\b", "_____", sent, count=1)
        facts.append({"type": "cloze", "subject": term, "line": 0, "q": f"Fill in the blank: {masked}", "correct": term,
                      "wrong": [c for c in concepts if c != term], "why": f'The source sentence reads: "{sent}"', "excerpt": sent})
        other = next((c for c in concepts if c != term), None)
        if other:
            swapped = re.sub(rf"\b{re.escape(term)}\b", other, sent, count=1)
            is_true = _digest(sent) % 2 == 0
            facts.append({"type": "true_false", "subject": term, "line": 0,
                          "q": f'True or false: "{sent if is_true else swapped}"', "correct": "True" if is_true else "False",
                          "why": f'The source says: "{sent}"', "excerpt": sent})
    return facts


def build_quiz(topic: str, source: str, concepts: list[str], count: int, types: Sequence[str] | None = None) -> dict[str, Any]:
    """A quiz whose questions come from facts in ``source``, interleaving question types."""
    facts = _py_facts(source)
    basis = "python_ast"
    if facts is None:
        facts = _prose_facts(source, concepts)
        basis = "text"
    if types:
        wanted = set(types)
        alias = {"multiple_choice": {"signature", "return_type", "docstring_match", "which_class", "inheritance", "imports", "code_reading", "cloze"},
                 "true_false": {"true_false"}, "cloze": {"cloze"}, "code_reading": {"code_reading"}}
        allowed = set().union(*(alias.get(t, {t}) for t in wanted))
        facts = [f for f in facts if f["type"] in allowed]
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        by_type[fact["type"]].append(fact)
    for group in by_type.values():
        group.sort(key=lambda f: _digest(topic, f["subject"], f["q"]))
    order = sorted(by_type, key=lambda t: _digest(topic, t))
    chosen: list[dict[str, Any]] = []
    while len(chosen) < count and any(by_type.values()):
        for t in order:
            if by_type[t] and len(chosen) < count:
                chosen.append(by_type[t].pop(0))
    questions: list[dict[str, Any]] = []
    key: dict[str, str] = {}
    explanations: dict[str, str] = {}
    for number, fact in enumerate(chosen, start=1):
        q: dict[str, Any] = {"id": number, "type": fact["type"], "question": fact["q"]}
        if fact["type"] == "true_false":
            q["options"] = ["True", "False"]
            key[str(number)] = fact["correct"]
        else:
            opts, letter = _options(fact["correct"], fact["wrong"], _digest(topic, fact["q"]))
            q["options"] = opts
            key[str(number)] = letter
        if fact.get("line"):
            q["source_line"] = fact["line"]
        if fact.get("excerpt"):
            q["source_excerpt"] = fact["excerpt"][:240]
        explanations[str(number)] = fact["why"]
        questions.append(q)
    return {"topic": topic, "basis": basis, "questions_count": len(questions), "questions": questions,
            "answer_key": key, "explanations": explanations,
            "question_types": sorted({q["type"] for q in questions}), "facts_available": len(facts)}


# --------------------------------------------------------------------------------------
# Row 105: workflow validation
# --------------------------------------------------------------------------------------

_DEP_KEYS = ("dependencies", "depends_on", "prerequisites", "deps", "needs", "after", "requires")


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return []


def validate_workflow_spec(
    tasks: Sequence[Mapping[str, Any]], *, deadline_s: float | None = None, simulate_failures: Sequence[str] = (),
    default_duration_s: float = 1.0,
) -> dict[str, Any]:
    """Validate a workflow before it runs: ids, dependencies, cycles, order, deadlines, failure and recovery.

    Task keys: ``id`` (or ``name``), any of ``dependencies``/``depends_on``/``prerequisites``/``deps``/
    ``needs``/``after``/``requires``, ``duration_s``, ``deadline_s`` (latest acceptable finish, from
    the start), ``on_failure`` (``halt`` default, ``continue`` or ``recover``), ``recovery`` (task id
    that runs instead when this one fails; it is scheduled only on failure) and ``optional``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    ids: list[str] = []
    graph: dict[str, list[str]] = {}
    meta: dict[str, dict[str, Any]] = {}
    for position, task in enumerate(tasks, start=1):
        if not isinstance(task, Mapping):
            errors.append(f"task #{position} is not an object")
            continue
        raw = task.get("id", task.get("name"))
        tid = str(raw).strip() if raw is not None and not isinstance(raw, (list, dict)) else ""
        if not tid:
            errors.append(f"task #{position} has no 'id' or 'name'")
            continue
        if tid in graph:
            errors.append(f"duplicate task id '{tid}'")
            continue
        deps: list[str] = []
        for key in _DEP_KEYS:
            deps.extend(_as_list(task.get(key)))
        deps = list(dict.fromkeys(deps))
        if tid in deps:
            errors.append(f"task '{tid}' depends on itself")
            deps = [d for d in deps if d != tid]
        duration = task.get("duration_s", task.get("duration", default_duration_s))
        try:
            duration = float(duration)
            if not math.isfinite(duration) or duration < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"task '{tid}' has an invalid duration {task.get('duration_s', task.get('duration'))!r}; it must be a finite number >= 0")
            duration = default_duration_s
        on_failure = str(task.get("on_failure", "halt")).lower()
        if on_failure not in ("halt", "continue", "recover"):
            errors.append(f"task '{tid}' has on_failure={on_failure!r}; use halt, continue or recover")
            on_failure = "halt"
        recovery = task.get("recovery")
        meta[tid] = {"duration": duration, "deadline": task.get("deadline_s"), "on_failure": on_failure,
                     "recovery": str(recovery) if recovery else None, "optional": bool(task.get("optional"))}
        graph[tid] = deps
        ids.append(tid)
    for tid, deps in graph.items():
        for d in deps:
            if d not in graph:
                errors.append(f"task '{tid}' depends on undefined task '{d}'")
    recovery_targets: set[str] = set()
    for tid, m in meta.items():
        rec = m["recovery"]
        if m["on_failure"] == "recover" and not rec:
            errors.append(f"task '{tid}' has on_failure=recover but no 'recovery' task")
        if rec:
            if rec not in graph:
                errors.append(f"task '{tid}' names undefined recovery task '{rec}'")
            elif rec == tid:
                errors.append(f"task '{tid}' is its own recovery task")
            else:
                recovery_targets.add(rec)
                if m["on_failure"] != "recover":
                    warnings.append(f"task '{tid}' names recovery '{rec}' but on_failure is '{m['on_failure']}', so it is never used")
    # recovery tasks run only on failure: they are not part of the normal order
    normal = [t for t in ids if t not in recovery_targets]
    normal_set = set(normal)
    for tid in normal:
        for d in graph[tid]:
            if d in recovery_targets:
                warnings.append(f"task '{tid}' depends on recovery task '{d}', which only runs when its owner fails")
    cycles = _cycles({t: [d for d in graph[t] if d in graph] for t in ids})
    for cycle in cycles:
        errors.append("dependency cycle: " + " -> ".join(cycle))
    for tid, m in meta.items():
        rec = m["recovery"]
        if rec in graph and rec != tid and _reaches(graph, rec, tid):
            errors.append(f"recovery task '{rec}' depends (transitively) on '{tid}', which it is meant to replace, so it cannot run after '{tid}' fails")
    order: list[str] = []
    tiers: list[list[str]] = []
    if not cycles and not [e for e in errors if "undefined task" in e]:
        indeg = {t: len([d for d in graph[t] if d in normal_set]) for t in normal}
        dependents: dict[str, list[str]] = defaultdict(list)
        for t in normal:
            for d in graph[t]:
                if d in normal_set:
                    dependents[d].append(t)
        ready = sorted(t for t, n in indeg.items() if n == 0)
        while ready:
            tiers.append(ready)
            order.extend(ready)
            nxt: list[str] = []
            for t in ready:
                for child in dependents[t]:
                    indeg[child] -= 1
                    if indeg[child] == 0:
                        nxt.append(child)
            ready = sorted(nxt)
    finish: dict[str, float] = {}
    pred: dict[str, str | None] = {}
    for t in order:
        deps = [d for d in graph[t] if d in finish]
        start = max((finish[d] for d in deps), default=0.0)
        pred[t] = max(deps, key=lambda d: finish[d]) if deps else None
        finish[t] = start + meta[t]["duration"]
    critical: list[str] = []
    makespan = 0.0
    if finish:
        end = max(finish, key=lambda t: finish[t])
        makespan = finish[end]
        cur: str | None = end
        while cur is not None:
            critical.append(cur)
            cur = pred[cur]
        critical.reverse()
    deadline_report: dict[str, Any] = {"checked": False}
    if deadline_s is not None or any(m["deadline"] is not None for m in meta.values()):
        misses: list[dict[str, Any]] = []
        for t in order:
            limit = meta[t]["deadline"]
            try:
                limit = float(limit) if limit is not None else None
            except (TypeError, ValueError):
                errors.append(f"task '{t}' has an invalid deadline_s {meta[t]['deadline']!r}")
                limit = None
            if limit is not None and finish[t] > limit:
                misses.append({"task": t, "earliest_finish_s": round(finish[t], 3), "deadline_s": limit,
                               "over_by_s": round(finish[t] - limit, 3)})
        if deadline_s is not None and makespan > deadline_s:
            misses.append({"task": "(whole workflow)", "earliest_finish_s": round(makespan, 3), "deadline_s": deadline_s,
                           "over_by_s": round(makespan - deadline_s, 3)})
        deadline_report = {"checked": True, "met": not misses, "misses": misses}
        if misses:
            errors.append(f"{len(misses)} deadline(s) cannot be met even with unlimited parallelism")
    all_dependents: dict[str, list[str]] = defaultdict(list)
    for t, deps in graph.items():
        for d in deps:
            all_dependents[d].append(t)
    impact_all: dict[str, list[str]] = {}
    for t in normal:
        impacted = _failure_impact(all_dependents, meta, t, normal_set)
        if impacted:
            impact_all[t] = impacted
    # the largest blast radii first; a long chain would otherwise return n^2 names
    top = sorted(impact_all, key=lambda t: (-len(impact_all[t]), t))[:40]
    blocked_by = {t: {"blocks": len(impact_all[t]), "first": impact_all[t][:10]} for t in top}
    simulation: dict[str, Any] | None = None
    if simulate_failures:
        failed = [f for f in simulate_failures if f in graph]
        unknown = [f for f in simulate_failures if f not in graph]
        if unknown:
            errors.append(f"simulate_failures names undefined task(s): {unknown}")
        blocked: set[str] = set()
        recovered: list[str] = []
        for f in failed:
            if meta[f]["on_failure"] == "recover" and meta[f]["recovery"] in graph:
                recovered.append(f"{f} -> {meta[f]['recovery']}")
            blocked.update(_failure_impact(all_dependents, meta, f, normal_set))
        simulation = {"failed": failed, "blocked": sorted(blocked - set(failed)), "recovered_by": recovered,
                      "still_runs": sorted(t for t in normal if t not in blocked and t not in failed)}
    if len(ids) > 1:
        depended = {d for deps in graph.values() for d in deps}
        for t in ids:
            if not graph[t] and t not in depended and t not in recovery_targets:
                warnings.append(f"task '{t}' has no dependencies and nothing depends on it")
    big = len(ids) > 200
    result: dict[str, Any] = {
        "valid": not errors, "errors": errors[:100], "warnings": warnings[:50], "total_tasks": len(ids),
        "order": order[:100] if big else order, "order_length": len(order),
        "parallel_tiers": [{"tier": i, "count": len(t), "first": t[:5]} for i, t in enumerate(tiers)][:100] if big else tiers,
        "tier_count": len(tiers), "max_concurrency": max((len(t) for t in tiers), default=0),
        "critical_path": critical[:100] if big else critical, "critical_path_length": len(critical),
        "critical_duration_s": round(makespan, 3), "deadlines": deadline_report,
        "failure_impact": blocked_by, "failure_impact_tasks": len(impact_all), "recovery_tasks": sorted(recovery_targets),
        "deadlines_checked": deadline_report["checked"],
    }
    if big:
        result["truncated"] = "order, tiers and critical path are shortened for a workflow of more than 200 tasks; the counts are complete"
    if simulation is not None:
        result["simulation"] = simulation
    return result


def _cycles(graph: Mapping[str, Sequence[str]]) -> list[list[str]]:
    state: dict[str, int] = {}
    found: list[list[str]] = []
    for root in sorted(graph):
        if state.get(root):
            continue
        state[root] = 1
        path = [root]
        stack = [iter(graph.get(root, ()))]
        while stack:
            try:
                child = next(stack[-1])
            except StopIteration:
                state[path.pop()] = 2
                stack.pop()
                continue
            if state.get(child) == 1:
                found.append(path[path.index(child):] + [child])
            elif not state.get(child):
                state[child] = 1
                path.append(child)
                stack.append(iter(graph.get(child, ())))
        if len(found) >= 10:
            break
    return found


def _reaches(graph: Mapping[str, Sequence[str]], start: str, target: str) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(graph.get(cur, ()))
    return False


def _failure_impact(
    dependents: Mapping[str, Sequence[str]], meta: Mapping[str, Mapping[str, Any]], failed: str, normal: set[str],
) -> list[str]:
    """Tasks that cannot run if ``failed`` fails: its dependents, transitively.

    A task that tolerates its own failure (``on_failure`` continue or recover, or ``optional``)
    blocks nobody.
    """
    if meta[failed]["on_failure"] in ("continue", "recover") or meta[failed]["optional"]:
        return []
    out: list[str] = []
    seen = {failed}
    stack = [failed]
    while stack:
        for child in dependents.get(stack.pop(), ()):
            if child not in seen and child in normal:
                seen.add(child)
                out.append(child)
                stack.append(child)
    return sorted(out)


# --------------------------------------------------------------------------------------
# Row 73: content sniffing
# --------------------------------------------------------------------------------------

_MAGIC: tuple[tuple[bytes, int, str, str], ...] = (
    (b"%PDF-", 0, "application/pdf", "document"),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", "image"),
    (b"\xff\xd8\xff", 0, "image/jpeg", "image"),
    (b"GIF87a", 0, "image/gif", "image"),
    (b"GIF89a", 0, "image/gif", "image"),
    (b"II*\x00", 0, "image/tiff", "image"),
    (b"MM\x00*", 0, "image/tiff", "image"),
    (b"\x00\x00\x01\x00", 0, "image/x-icon", "image"),
    (b"SQLite format 3\x00", 0, "application/x-sqlite3", "database"),
    (b"\x1f\x8b\x08", 0, "application/gzip", "archive"),
    (b"\xfd7zXZ\x00", 0, "application/x-xz", "archive"),
    (b"7z\xbc\xaf\x27\x1c", 0, "application/x-7z-compressed", "archive"),
    (b"Rar!\x1a\x07", 0, "application/vnd.rar", "archive"),
    (b"\x7fELF", 0, "application/x-executable", "binary"),
    (b"\xca\xfe\xba\xbe", 0, "application/java-vm", "binary"),
    (b"\xcf\xfa\xed\xfe", 0, "application/x-mach-binary", "binary"),
    (b"\x00asm", 0, "application/wasm", "binary"),
    (b"PAR1", 0, "application/vnd.apache.parquet", "data"),
    (b"OggS", 0, "audio/ogg", "audio"),
    (b"fLaC", 0, "audio/flac", "audio"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0, "application/x-ole-storage", "document"),
)
_OFFICE_PREFIX = {
    "word/": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document", "docx"),
    "xl/": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "spreadsheet", "xlsx"),
    "ppt/": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "presentation", "pptx"),
}
_MAX_ZIP_ENTRIES = 5_000


def _sniff_zip(data: bytes) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()[:_MAX_ZIP_ENTRIES]
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        return None
    for prefix, (mime, category, kind) in _OFFICE_PREFIX.items():
        if any(n.startswith(prefix) for n in names) and "[Content_Types].xml" in names:
            return {"mime_type": mime, "category": category, "format": kind, "entries": len(names)}
    if "mimetype" in names:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                declared = z.read("mimetype")[:100].decode("ascii", "replace").strip()
        except (zipfile.BadZipFile, KeyError, OSError, ValueError, NotImplementedError):
            declared = ""
        if declared.startswith("application/epub"):
            return {"mime_type": declared, "category": "document", "format": "epub", "entries": len(names)}
        if declared.startswith("application/vnd.oasis.opendocument"):
            return {"mime_type": declared, "category": "document", "format": "opendocument", "entries": len(names)}
    if "AndroidManifest.xml" in names:
        return {"mime_type": "application/vnd.android.package-archive", "category": "archive", "format": "apk", "entries": len(names)}
    if "META-INF/MANIFEST.MF" in names:
        return {"mime_type": "application/java-archive", "category": "archive", "format": "jar", "entries": len(names)}
    if any(n.endswith(".dist-info/WHEEL") for n in names):
        return {"mime_type": "application/x-wheel+zip", "category": "archive", "format": "python wheel", "entries": len(names)}
    return {"mime_type": "application/zip", "category": "archive", "format": "zip", "entries": len(names)}


def _image_info(data: bytes, mime: str) -> dict[str, Any]:
    try:
        if mime == "image/png" and len(data) >= 24:
            w, h = struct.unpack(">II", data[16:24])
            return {"width": w, "height": h}
        if mime == "image/gif" and len(data) >= 10:
            w, h = struct.unpack("<HH", data[6:10])
            return {"width": w, "height": h}
        if mime == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return {"width": w, "height": h}
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        pass
    return {}


def _looks_text(data: bytes) -> bool:
    if not data:
        return True
    if data.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True
    if b"\x00" in data[:4096]:
        return False
    sample = data[:4096]
    odd = sum(1 for b in sample if b < 9 or 13 < b < 32 and b != 27)
    return odd / len(sample) < 0.05


def sniff_content(data: bytes, filename: str | None = None) -> dict[str, Any]:
    """Format of ``data`` from its bytes (the file name is only a tie-breaker and a mismatch check)."""
    name = (filename or "").rsplit("/", 1)[-1]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    head = data[:8192]
    info: dict[str, Any] = {"bytes_inspected": len(head), "size_bytes": len(data)}
    mime, category, fmt, confidence = "application/octet-stream", "binary", None, 0.4
    matched = False
    for magic, offset, m, c in _MAGIC:
        if head[offset: offset + len(magic)] == magic:
            mime, category, confidence, matched = m, c, 0.98, True
            break
    if not matched and head[:2] == b"BM" and len(head) >= 30 and head[6:10] == b"\x00\x00\x00\x00":
        mime, category, confidence, matched = "image/bmp", "image", 0.9, True
    if not matched and head[:3] == b"BZh" and head[4:10] == b"1AY&SY":
        mime, category, confidence, matched = "application/x-bzip2", "archive", 0.95, True
    if not matched and head[:3] == b"ID3" and len(head) > 10 and head[3] in (2, 3, 4):
        mime, category, confidence, matched = "audio/mpeg", "audio", 0.9, True
    if not matched and head[:4] == b"RIFF" and len(head) >= 12:
        sub = head[8:12]
        if sub == b"WAVE":
            mime, category = "audio/wav", "audio"
        elif sub == b"WEBP":
            mime, category = "image/webp", "image"
        else:
            mime, category = "video/x-msvideo", "video"
        confidence, matched = 0.97, True
    if not matched and head[4:8] == b"ftyp":
        mime, category, confidence, matched = "video/mp4", "video", 0.95, True
    if not matched and head[:2] == b"MZ" and len(head) > 64:
        mime, category, confidence, matched = "application/vnd.microsoft.portable-executable", "binary", 0.9, True
    if head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06":
        zipped = _sniff_zip(data[:20_000_000])
        if zipped:
            mime, category, fmt, confidence = zipped["mime_type"], zipped["category"], zipped["format"], 0.97
            info["archive_entries"] = zipped["entries"]
    if mime.startswith("image/"):
        info.update(_image_info(head, mime))
    if mime == "application/x-ole-storage":
        fmt = "legacy Office (doc/xls/ppt)"
        ext_map = {".doc": "document", ".xls": "spreadsheet", ".ppt": "presentation"}
        category = ext_map.get(ext, "document")
    if matched or fmt:
        info["is_text"] = False
    if not matched and not fmt:
        text_like = _looks_text(head)
        info["is_text"] = text_like
        if text_like:
            body = head.decode("utf-8", "replace").lstrip("﻿")
            stripped = body.lstrip()
            category, confidence = "text", 0.7
            mime = "text/plain"
            if ext == ".ipynb" or ('"nbformat"' in stripped[:2000] and '"cells"' in stripped[:4000]):
                mime, category, fmt, confidence = "application/x-ipynb+json", "notebook", "jupyter notebook", 0.98
            elif stripped and stripped[0] in "{[" and _json_prefix(stripped):
                mime, category, fmt, confidence = "application/json", "data", "json", 0.9
            elif re.match(r"<\?xml", stripped) or (re.match(r"<[A-Za-z][\w:-]*[\s>/]", stripped) and "</" in stripped):
                if re.match(r"<svg\b", stripped, re.I) or "<svg" in stripped[:400].lower():
                    mime, category, fmt, confidence = "image/svg+xml", "image", "svg", 0.9
                elif re.match(r"<!doctype html|<html\b", stripped, re.I):
                    mime, category, fmt, confidence = "text/html", "markup", "html", 0.9
                else:
                    mime, category, fmt, confidence = "application/xml", "data", "xml", 0.85
            elif re.match(r"<!doctype html|<html\b", stripped, re.I):
                mime, category, fmt, confidence = "text/html", "markup", "html", 0.9
            elif stripped.startswith("#!"):
                shebang = stripped.split("\n", 1)[0]
                lang = "python" if "python" in shebang else "shell" if re.search(r"\b(ba|z|da)?sh\b", shebang) else "node" if "node" in shebang else "script"
                mime, category, fmt, confidence = f"text/x-{lang}", "source", lang, 0.92
            elif re.search(r"(?m)^\s*(?:from\s+[\w.]+\s+import\s|import\s+[\w.]+\s*$|def\s+\w+\s*\(|class\s+\w+\s*[(:])", body) and ext in ("", ".py", ".txt", ".dat", ".bin"):
                mime, category, fmt, confidence = "text/x-python", "source", "python", 0.82
            elif re.search(r"(?mi)^\s*(select\s.+\sfrom\s|create\s+(or\s+replace\s+)?table\s|insert\s+into\s|alter\s+table\s)", body):
                mime, category, fmt, confidence = "application/sql", "source", "sql", 0.8
            elif re.search(r"(?m)^\[[\w.\- ]+\]\s*$", body) and re.search(r"(?m)^[\w.-]+\s*=\s*\S", body):
                mime, category, fmt, confidence = "application/toml", "data", "toml", 0.7
            elif (re.search(r"(?m)^[\w.-]+:\s", body) and not re.search(r"[;{}]", body[:400])) or body.startswith("---\n"):
                mime, category, fmt, confidence = "application/x-yaml", "data", "yaml", 0.6
            else:
                delimiter = _csv_delimiter(body)
                if delimiter:
                    mime = "text/tab-separated-values" if delimiter == "\t" else "text/csv"
                    category, fmt, confidence = "spreadsheet", "tsv" if delimiter == "\t" else "csv", 0.8
                elif re.match(r"\s*(#{1,6}\s|[-*]\s|\d+\.\s)", body) and ext in ("", ".md", ".markdown", ".txt"):
                    mime, category, fmt, confidence = "text/markdown", "text", "markdown", 0.65
                elif re.search(r"(?m)^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}.*\b(INFO|WARN|WARNING|ERROR|DEBUG)\b", body):
                    mime, category, fmt, confidence = "text/x-log", "log", "log", 0.8
            if confidence <= 0.7 and ext in _EXT_MIME:
                mime, category, fmt = _EXT_MIME[ext]
                confidence = 0.75
        else:
            info["is_text"] = False
            if ext in _EXT_MIME:
                mime, category, fmt = _EXT_MIME[ext]
    info.update({"mime_type": mime, "category": category, "format": fmt, "confidence": round(confidence, 2)})
    expected = _EXT_MIME.get(ext)
    if expected and ext != ".txt" and expected[0] != mime and not (expected[1] == category and category in ("source", "data", "text")):
        info["extension_mismatch"] = {
            "declared_extension": ext, "extension_suggests": expected[0], "content_says": mime,
            "message": f"the name says '{ext}' ({expected[0]}) but the content is {mime}; trust the content",
        }
    return info


def _json_prefix(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError as exc:
        # a truncated header still looks like JSON if the failure is near its end
        return exc.pos >= max(0, len(text) - 2) or bool(re.match(r'^\s*[{\[]\s*("|\{|\[|\d|-|t|f|n|\]|\})', text))
    except RecursionError:
        return True


def _csv_delimiter(body: str) -> str | None:
    lines = [ln for ln in body.splitlines()[:30] if ln.strip()]
    if len(lines) < 2:
        return None
    for delimiter in (",", "\t", ";", "|"):
        counts = [ln.count(delimiter) for ln in lines]
        if counts[0] >= 1 and len(set(counts)) == 1:
            try:
                rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
            except csv.Error:
                continue
            if len({len(r) for r in rows}) == 1 and len(rows[0]) >= 2:
                return delimiter
    return None


_EXT_MIME: dict[str, tuple[str, str, str]] = {
    ".py": ("text/x-python", "source", "python"), ".js": ("text/javascript", "source", "javascript"),
    ".jsx": ("text/javascript", "source", "javascript"), ".mjs": ("text/javascript", "source", "javascript"),
    ".ts": ("text/typescript", "source", "typescript"), ".tsx": ("text/typescript", "source", "typescript"),
    ".go": ("text/x-go", "source", "go"), ".rs": ("text/rust", "source", "rust"), ".java": ("text/x-java", "source", "java"),
    ".kt": ("text/x-kotlin", "source", "kotlin"), ".cs": ("text/x-csharp", "source", "csharp"), ".dart": ("text/x-dart", "source", "dart"),
    ".sql": ("application/sql", "source", "sql"), ".json": ("application/json", "data", "json"),
    ".yaml": ("application/x-yaml", "data", "yaml"), ".yml": ("application/x-yaml", "data", "yaml"),
    ".toml": ("application/toml", "data", "toml"), ".xml": ("application/xml", "data", "xml"),
    ".csv": ("text/csv", "spreadsheet", "csv"), ".tsv": ("text/tab-separated-values", "spreadsheet", "tsv"),
    ".md": ("text/markdown", "text", "markdown"), ".html": ("text/html", "markup", "html"), ".htm": ("text/html", "markup", "html"),
    ".log": ("text/x-log", "log", "log"), ".ipynb": ("application/x-ipynb+json", "notebook", "jupyter notebook"),
    ".pdf": ("application/pdf", "document", "pdf"), ".png": ("image/png", "image", "png"), ".jpg": ("image/jpeg", "image", "jpeg"),
    ".jpeg": ("image/jpeg", "image", "jpeg"), ".docx": (_OFFICE_PREFIX["word/"][0], "document", "docx"),
    ".xlsx": (_OFFICE_PREFIX["xl/"][0], "spreadsheet", "xlsx"), ".pptx": (_OFFICE_PREFIX["ppt/"][0], "presentation", "pptx"),
    ".zip": ("application/zip", "archive", "zip"), ".txt": ("text/plain", "text", "text"),
}

#: ``category``/``format`` -> the agent tool that handles it (a name the registry may or may not hold in a deployment).
_ROUTES: dict[str, str] = {
    "python": "analyze_python_ast", "javascript": "analyze_source_structure", "typescript": "analyze_source_structure",
    "go": "analyze_source_structure", "rust": "analyze_source_structure", "java": "analyze_source_structure",
    "kotlin": "analyze_source_structure", "csharp": "analyze_source_structure", "dart": "analyze_source_structure",
    "sql": "inspect_sql", "json": "parse_structured_data", "yaml": "parse_structured_data", "toml": "parse_structured_data",
    "xml": "parse_structured_data", "jupyter notebook": "parse_structured_data", "csv": "inspect_spreadsheet",
    "tsv": "inspect_spreadsheet", "xlsx": "inspect_spreadsheet", "pdf": "parse_document", "docx": "parse_document",
    "pptx": "parse_document", "markdown": "explain_source", "log": "analyze_logs", "html": "explain_source",
    "text": "explain_source",
}
_CATEGORY_ROUTES = {"image": "ocr_image", "audio": "transcribe_audio", "video": "transcribe_audio", "document": "parse_document",
                    "spreadsheet": "inspect_spreadsheet", "presentation": "parse_document"}


def route_for(info: Mapping[str, Any]) -> tuple[str | None, str]:
    """``(agent_tool, note)`` for a sniffed format; ``None`` when no tool reads it."""
    fmt = info.get("format")
    if fmt in _ROUTES:
        tool = _ROUTES[fmt]
        note = "pass the text as its content" if tool in ("parse_structured_data", "analyze_python_ast", "analyze_source_structure", "inspect_sql", "explain_source", "analyze_logs") else "this tool reads the file from the workspace by path"
        return tool, note
    category = info.get("category")
    if category in _CATEGORY_ROUTES:
        return _CATEGORY_ROUTES[category], "this tool reads the file from the workspace by path"
    if category == "archive":
        return None, "an archive: list or extract it with run_command (unzip / tar) in the workspace, then inspect the contents"
    if category == "database":
        return None, "a binary database file: open it with run_command (sqlite3) in the workspace"
    if category == "binary":
        return None, "a compiled or opaque binary: no analysis tool reads it"
    return None, "no specific tool; read it as plain text"
