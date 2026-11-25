"""Explain source or document content: structure, headings, architecture, study guides.

Hardening notes (review of 2026-11):

* Every method returns a result dict. Source that will not parse (syntax error, NUL byte, absurd
  nesting) is reported under ``parse_error`` instead of looking like "a file with 0 symbols", and
  ``ast.parse`` failures other than ``SyntaxError`` (``ValueError``, ``RecursionError``,
  ``MemoryError``) no longer escape.
* ``file_content`` may be text **or bytes**. A PDF's bytes are read in full (every page) before they
  are explained; :meth:`explain_file` / :meth:`generate_study_guide_file` take a path through
  ``_input`` (size cap, ``base_dir`` confinement, UTF-8/Latin-1) and report coverage under ``input``
  and ``warnings``. A scanned page that needs OCR is named, not silently skipped.
* ``headings`` are Markdown ATX headings outside code fences, and only for prose-like files; a
  ``#`` comment in a Python/shell/YAML file is not a heading.
* ``generate_study_guide``'s "frequent concepts" were the *first* capitalised words; they are now the
  most frequent identifiers.
* ``explain_function`` finds the function you named (not just the first ``def`` in the text), accepts
  an indented method, and lists keyword-only, positional-only, ``*args`` and ``**kwargs`` parameters.
* ``explain_code_architecture`` ignores relative imports in ``external_dependencies`` (reported as
  ``relative_imports``) and matches the layer on whole name tokens (``score.py`` is not "core").
"""

from __future__ import annotations

import ast
import re
import textwrap
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from ._input import SourceReadError, read_source_bytes, read_source_file

#: Source longer than this is not parsed (ast.parse memory is ~50x the text).
MAX_SOURCE_CHARS = 5_000_000
_PROSE_EXTS = (".md", ".markdown", ".mdx", ".txt", ".rst", ".pdf")
_STOP_CAPS = {
    "This", "That", "These", "Those", "There", "Then", "When", "Where", "What", "With", "From", "Into",
    "Each", "Every", "Some", "Note", "True", "False", "None", "Returns", "Args", "Raises", "Example",
    "Examples", "Also", "Only", "Such", "Will", "Should", "Would", "Could", "Must", "Without", "Within",
}
_PARSE_ERRORS = (SyntaxError, ValueError, RecursionError, MemoryError)


def _as_text(content: Any) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """``(text, input_info, error)`` for text or bytes (bytes that are a PDF are read in full)."""
    if isinstance(content, (bytes, bytearray)):
        try:
            read = read_source_bytes(bytes(content))
        except SourceReadError as exc:
            return None, None, str(exc)
        return read.text, {"describe": read.describe(), "warnings": read.warnings(), "kind": read.kind}, None
    if content is None:
        return "", None, None
    if not isinstance(content, str):
        return None, None, f"file_content must be text or bytes, got {type(content).__name__}"
    return content, None, None


def _name(file_name: Any) -> str:
    return file_name if isinstance(file_name, str) else ("" if file_name is None else str(file_name))


def _parse(source: str) -> Tuple[Optional[ast.AST], Optional[str]]:
    """``(tree, None)`` or ``(None, reason)``. Never raises."""
    if len(source) > MAX_SOURCE_CHARS:
        return None, f"source is too large to parse ({len(source)} characters)"
    try:
        return ast.parse(source), None
    except SyntaxError as exc:
        return None, f"SyntaxError: {exc.msg} (line {exc.lineno})"
    except _PARSE_ERRORS as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _lines(text: str) -> List[str]:
    return text.splitlines()


def _attach_input(result: Dict[str, Any], info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if info:
        result["input"] = info["describe"]
        if info["warnings"]:
            result["warnings"] = list(info["warnings"])
    return result


def _headings(lines: List[str], file_name: str) -> List[str]:
    lowered = file_name.lower()
    if lowered and "." in lowered.rsplit("/", 1)[-1] and not lowered.endswith(_PROSE_EXTS):
        return []  # a '#' in code, shell or YAML starts a comment
    headings: List[str] = []
    fence: Optional[str] = None
    for ln in lines:
        stripped = ln.strip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            if fence is None:
                fence = marker.group(1)[0]
            elif stripped.startswith(fence * 3):
                fence = None
            continue
        if fence is None:
            match = re.match(r"#{1,6}\s+(\S.*)$", stripped)
            if match:
                headings.append(match.group(1).strip().rstrip("#").strip())
    return headings


def _layer(file_name: str) -> str:
    tokens = set(re.split(r"[\\/._\- ]+", file_name.lower()))
    if tokens & {"tools", "tool"}:
        return "tool / execution"
    if tokens & {"adapters", "adapter", "connector", "connectors"}:
        return "adapter / external boundary"
    if tokens & {"domain", "core"}:
        return "domain / kernel core"
    if tokens & {"api", "service", "services"}:
        return "presentation / API layer"
    return "unknown"


def _params(args: ast.arguments) -> List[str]:
    names = [a.arg for a in [*args.posonlyargs, *args.args] if a.arg not in ("self", "cls")]
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    return names


class SourceContentExplainer:
    """Explainer for extracting structure, headings, architecture, and study notes from source or doc files."""

    @staticmethod
    def explain(
        file_name: str,
        file_content: str,
        detail_level: str = "comprehensive",
    ) -> Dict[str, Any]:
        """Produce a structured summary of symbols, headings, and key sections in a file.

        ``file_content`` may be bytes; a PDF's bytes are read in full first (see ``input``).
        """
        name = _name(file_name)
        text, info, error = _as_text(file_content)
        if text is None:
            return {"file_name": name, "parsed": False, "error": error}

        lines = _lines(text)
        headings = _headings(lines, name)
        symbols: List[str] = []
        parse_error: Optional[str] = None

        if name.lower().endswith(".py"):
            tree, parse_error = _parse(text)
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        symbols.append(f"Class: {node.name}")
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                        symbols.append(f"Function: {node.name}")

        key_topics = headings if headings else symbols[:8]
        result: Dict[str, Any] = {
            "file_name": name,
            "detail_level": detail_level,
            "line_count": len(lines),
            "headings": headings,
            "public_symbols": symbols,
            "key_topics": key_topics,
            "summary": f"Analyzed '{name}' ({len(lines)} lines) with {len(symbols)} public symbols and {len(headings)} headings.",
        }
        if parse_error:
            result["parse_error"] = parse_error
            result["summary"] += f" Python parsing failed ({parse_error}), so symbols are incomplete."
        return _attach_input(result, info)

    @classmethod
    def explain_file(
        cls,
        file_path: str,
        detail_level: str = "comprehensive",
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explain a file on disk. A PDF is read in full (all pages) and explained from its text."""
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"file_name": str(file_path), "parsed": False, "error": str(exc)}
        result = cls.explain(str(file_path), read.text, detail_level)
        result["input"] = read.describe()
        if read.warnings():
            result["warnings"] = read.warnings()
        return result

    @classmethod
    def generate_study_guide(cls, file_name: str, file_content: str) -> Dict[str, Any]:
        """Generate a pedagogical study guide with core concepts, architectural summary, and review takeaways."""
        name = _name(file_name)
        text, info, error = _as_text(file_content)
        if text is None:
            return {"file_name": name, "error": error}
        analysis = cls.explain(name, text)
        lines = _lines(text)

        # Most frequent capitalised identifiers (ties keep first-seen order).
        words = [w for w in re.findall(r"\b[A-Z][a-zA-Z0-9_]{3,}\b", text) if w not in _STOP_CAPS]
        frequent_concepts = [w for w, _ in Counter(words).most_common(6)]

        takeaways = [
            f"Understand the main responsibility boundary established in '{name}'.",
            "Trace how inputs flow through exported classes and public methods.",
            "Verify dependencies and error-handling assumptions across external boundaries.",
        ]

        study_guide_markdown = [
            f"# Study Guide: `{name}`",
            f"**File Scope:** {len(lines)} lines | **Extracted Topics:** {', '.join(analysis['key_topics'][:5])}",
            "",
            "## Core Concepts",
        ]
        for concept in frequent_concepts:
            study_guide_markdown.append(f"- **{concept}**: Key identifier used throughout component logic.")

        study_guide_markdown.extend([
            "",
            "## Architectural Overview",
            f"This module exposes {len(analysis['public_symbols'])} primary interfaces. It manages data transformation and contracts.",
            "",
            "## Review Questions & Key Takeaways",
        ])
        for t in takeaways:
            study_guide_markdown.append(f"1. {t}")

        result: Dict[str, Any] = {
            "file_name": name,
            "key_concepts": frequent_concepts,
            "takeaways": takeaways,
            "study_guide_markdown": "\n".join(study_guide_markdown),
        }
        if "parse_error" in analysis:
            result["parse_error"] = analysis["parse_error"]
        return _attach_input(result, info)

    @classmethod
    def generate_study_guide_file(cls, file_path: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """Study guide for a file on disk. A PDF is read in full (all pages) first."""
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"file_name": str(file_path), "error": str(exc)}
        result = cls.generate_study_guide(str(file_path), read.text)
        result["input"] = read.describe()
        if read.warnings():
            result["warnings"] = read.warnings()
        return result

    @staticmethod
    def explain_code_architecture(file_name: str, file_content: str) -> Dict[str, Any]:
        """Analyze structural design patterns, architectural layer, and dependency boundaries."""
        name = _name(file_name)
        text, info, error = _as_text(file_content)
        if text is None:
            return {"file_name": name, "parsed": False, "error": error}

        layer = _layer(name)
        dependencies: List[str] = []
        relative: List[str] = []
        class_count = 0
        function_count = 0

        tree, parse_error = _parse(text)
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        dependencies.append(a.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        relative.append("." * node.level + (node.module or ""))
                    elif node.module:
                        dependencies.append(node.module)
                elif isinstance(node, ast.ClassDef):
                    class_count += 1
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function_count += 1

        result: Dict[str, Any] = {
            "file_name": name,
            "architectural_layer": layer,
            "class_count": class_count,
            "function_count": function_count,
            "external_dependencies": sorted(set(dependencies)),
            "relative_imports": sorted(set(relative)),
            "is_cohesive": class_count <= 5 and function_count <= 25,
            "design_notes": f"Module structured with {class_count} class(es) and {function_count} function(s) in {layer}.",
            "parsed": tree is not None,
        }
        if parse_error:
            result["parse_error"] = parse_error
            result["design_notes"] = (
                f"Could not parse the source as Python ({parse_error}); counts and dependencies are not available."
            )
        return _attach_input(result, info)

    @staticmethod
    def explain_function(function_name: str, function_source: str) -> Dict[str, Any]:
        """Provide detailed structural and behavioral breakdown of a single Python function."""
        if not isinstance(function_source, str):
            return {
                "function_name": function_name,
                "parsed": False,
                "error": "Could not parse function source code (expected text).",
            }
        tree, parse_error = _parse(textwrap.dedent(function_source))
        fn_node = None
        if tree is not None:
            funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            fn_node = next((n for n in funcs if n.name == function_name), None) or (funcs[0] if funcs else None)

        if not fn_node:
            return {
                "function_name": function_name,
                "parsed": False,
                "error": "Could not parse function source code."
                + (f" ({parse_error})" if parse_error else " (no function definition found)"),
            }

        args = _params(fn_node.args)
        has_docstring = ast.get_docstring(fn_node) is not None
        docstring = ast.get_docstring(fn_node) or "No docstring provided."
        is_async = isinstance(fn_node, ast.AsyncFunctionDef)

        branch_count = 0
        for child in ast.walk(fn_node):
            if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)):
                branch_count += 1

        return {
            "function_name": fn_node.name,
            "parsed": True,
            "is_async": is_async,
            "parameters": args,
            "has_docstring": has_docstring,
            "docstring": docstring.strip(),
            "decision_branches": branch_count,
            "complexity_level": "Simple" if branch_count <= 3 else ("Moderate" if branch_count <= 7 else "Complex"),
        }

    @staticmethod
    def extract_docstring_catalog(file_content: str) -> Dict[str, Any]:
        """Extract documentation catalog of module, classes, and member functions."""
        text, info, error = _as_text(file_content)
        if text is None:
            return {"parsed": False, "error": error, "catalog": []}
        tree, parse_error = _parse(text)
        if tree is None:
            return _attach_input({"parsed": False, "error": parse_error, "catalog": []}, info)

        module_doc = ast.get_docstring(tree) or ""  # type: ignore[arg-type]
        catalog: List[Dict[str, Any]] = []

        for node in tree.body:  # type: ignore[attr-defined]
            if isinstance(node, ast.ClassDef):
                cls_doc = ast.get_docstring(node) or ""
                methods = []
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append({
                            "name": sub.name,
                            "docstring": ast.get_docstring(sub) or "",
                        })
                catalog.append({
                    "type": "class",
                    "name": node.name,
                    "docstring": cls_doc,
                    "methods": methods,
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                catalog.append({
                    "type": "function",
                    "name": node.name,
                    "docstring": ast.get_docstring(node) or "",
                })

        return _attach_input({
            "parsed": True,
            "module_docstring": module_doc,
            "catalog_count": len(catalog),
            "catalog": catalog,
        }, info)
