"""Structural analysis of source code: native AST for Python, line-oriented extractors for JS/TS/Dart, Go, Rust.

Hardening notes (review of 2026-11):

* Every method returns a result dict. ``None`` / non-text source, NUL bytes, absurd nesting and
  oversized input (``MAX_SOURCE_CHARS``) are reported as ``valid: False`` with an error; previously
  ``analyze(None)`` and ``analyze_python(None)`` raised.
* **No catastrophic regex backtracking on minified code.** The JS/TS function pattern was quadratic on
  a single very long line (a 100 KB minified bundle hung the process). Lines longer than
  ``MAX_LINE_CHARS`` are not pattern-matched and are counted under ``skipped_long_lines`` with a
  warning instead of being silently ignored.
* Python: imports, classes, functions and module variables inside top-level ``if`` / ``try`` / ``with``
  blocks (``try: import x except ImportError``, ``if TYPE_CHECKING:``) are found; relative imports keep
  their leading dots; annotated assignments count as variables; function ``args`` include keyword-only,
  ``*args`` and ``**kwargs``.
* JS/TS/Dart: ``//`` and ``/* */`` comments are removed first (a commented-out ``class Foo`` is not a
  class), ``require('x')`` imports are found, and test-framework calls (``it(...) {``, ``describe``) are
  not reported as functions. Go: aliased/dot/blank imports are the package path, generic types and
  functions are found. Rust: generic ``fn``, ``enum`` (new ``enums`` key) are found.
* ``extract_cross_language_symbols`` no longer analyses a ``.md`` / ``.json`` / ``.tsx`` file as Python:
  the language comes from the (case-insensitive) extension, an unsupported file is reported under
  ``skipped_files``, and values may be bytes (a PDF is read in full and reported as not-source).
* :meth:`analyze_file` reads a path through ``_input`` (size cap, ``base_dir`` confinement). A **PDF** is
  read in full, every page; its text is analysed, and a failure says the PDF was read completely but
  extracted text has no code structure. Coverage is under ``input`` / ``warnings``.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import PurePath
from typing import Any, Dict, Iterator, List, Optional, Union

from ._input import SourceReadError, read_source_bytes, read_source_file

#: Source longer than this is refused (ast.parse needs ~50x the text in memory).
MAX_SOURCE_CHARS = 5_000_000
#: The JS/TS/Dart extractor is regex-per-line; its worst case is linear in lines but heavy, so it takes less.
MAX_PATTERN_SOURCE_CHARS = 2_000_000
#: Lines longer than this are not run through the line-oriented regexes (minified code).
MAX_LINE_CHARS = 1000

_ALIASES = {
    "py": "python", "js": "javascript", "ts": "typescript", "golang": "go", "rs": "rust",
    "node": "javascript", "nodejs": "javascript",
}
_PARSE_ERRORS = (SyntaxError, ValueError, RecursionError, MemoryError)
_NOT_FUNCTIONS = {
    "if", "for", "while", "switch", "catch", "return", "function", "with", "else", "do", "try", "typeof",
    "await", "new", "super", "import", "require", "delete", "void", "yield", "it", "describe", "test",
    "beforeEach", "afterEach", "beforeAll", "afterAll",
}
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?m)(^|\s)//.*$")
_JS_FN = re.compile(
    r"\b(?:async\s+)?(?:function\s+)?([A-Za-z_]\w*)\s*\([^)]{0,150}\)\s*(?::\s*[^{]{1,100})?\s*(?:async\s*)?\{"
)
_JS_ARROW = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_]+)\s*=>"
)
_JS_REQUIRE = re.compile(r"\brequire\(\s*['\"][^'\"]+['\"]\s*\)")
_JS_CLASS = re.compile(r"\bclass\s+([A-Za-z_]\w*)")
_DART_CLASS = re.compile(r"\b(?:class|mixin|enum)\s+([A-Za-z_]\w*)")


def _bad_source(language: str, source: Any, limit: int = MAX_SOURCE_CHARS) -> Optional[Dict[str, Any]]:
    if not isinstance(source, str):
        return {
            "valid": False,
            "language": language,
            "error": f"source_code must be text, got {type(source).__name__}",
            "classes": [],
            "functions": [],
            "imports": [],
        }
    if len(source) > limit:
        return {
            "valid": False,
            "language": language,
            "error": f"Source is too large to analyse ({len(source)} characters; limit {limit}).",
            "classes": [],
            "functions": [],
            "imports": [],
        }
    return None


def _walk_toplevel(body: List[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements at module level, descending into if/try/with/for/while but not into defs or classes."""
    for node in body:
        yield node
        if isinstance(node, ast.If):
            yield from _walk_toplevel(node.body)
            yield from _walk_toplevel(node.orelse)
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            yield from _walk_toplevel(node.body)
            for handler in node.handlers:
                yield from _walk_toplevel(handler.body)
            yield from _walk_toplevel(node.orelse)
            yield from _walk_toplevel(node.finalbody)
        elif isinstance(node, (ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
            yield from _walk_toplevel(node.body)
            yield from _walk_toplevel(getattr(node, "orelse", []))


def _py_args(args: ast.arguments) -> List[str]:
    names = [a.arg for a in [*args.posonlyargs, *args.args]]
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    return names


def _strip_comments(source: str) -> str:
    if "*/" in source:  # without a terminator every "/*" would rescan to the end of the text
        source = _BLOCK_COMMENT.sub(lambda m: "\n" * m.group().count("\n"), source)
    return _LINE_COMMENT.sub(r"\1", source)


def _code_lines(source: str, skipped: List[int]) -> Iterator[str]:
    """Stripped, comment-free lines; over-long (minified) lines are counted in ``skipped`` and dropped."""
    for raw in _strip_comments(source).splitlines():
        line = raw.strip()
        if len(line) > MAX_LINE_CHARS:
            skipped.append(1)
            continue
        yield line


def _warn_long(result: Dict[str, Any], skipped: List[int]) -> Dict[str, Any]:
    result["skipped_long_lines"] = len(skipped)
    if skipped:
        result["warnings"] = [
            f"{len(skipped)} line(s) longer than {MAX_LINE_CHARS} characters (minified code?) were not "
            "analysed; symbols on them are missing."
        ]
    return result


class MultiLanguageAstAnalyzer:
    """Structural code analyzer for Python (native AST) and pattern-based analyzers for JS/TS, Go, Rust, and Dart."""

    SUPPORTED_LANGUAGES = {"python", "javascript", "typescript", "dart", "go", "rust"}

    @classmethod
    def analyze(cls, source_code: str, language: str = "python") -> Dict[str, Any]:
        """Extract classes, functions, and imports from source_code for the target language."""
        lang = language.strip().lower() if isinstance(language, str) else ""
        lang = _ALIASES.get(lang, lang)
        if lang not in cls.SUPPORTED_LANGUAGES:
            return {
                "valid": False,
                "language": lang,
                "error": f"Unsupported language '{language}'. Supported: {sorted(cls.SUPPORTED_LANGUAGES)}",
            }

        if lang == "python":
            return cls.analyze_python(source_code)
        elif lang in ("javascript", "typescript", "dart"):
            return cls.analyze_javascript_typescript(source_code, lang=lang)
        elif lang == "go":
            return cls.analyze_go(source_code)
        return cls.analyze_rust(source_code)

    @classmethod
    def analyze_file(
        cls,
        file_path: Union[str, "PurePath"],
        language: Optional[str] = None,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyse a source file. The language defaults to the extension.

        A PDF is read in full (every page) and its text analysed; the result's ``input`` block shows
        the pages read and any scanned page that needs OCR.
        """
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"valid": False, "error": str(exc), "file_path": str(file_path)}

        lang = language or cls._language_for(str(file_path)) or "python"
        result = cls.analyze(read.text, language=lang)
        result["file_path"] = str(file_path)
        result["input"] = read.describe()
        if read.warnings():
            result["warnings"] = list(result.get("warnings", [])) + read.warnings()
        if read.kind == "pdf" and result.get("valid"):
            result["warnings"] = list(result.get("warnings", [])) + [
                "Analysed text extracted from a PDF (all pages); it has no indentation or layout, so an "
                "empty or partial symbol list does not describe any original source file."
            ]
        if read.kind == "pdf" and not result.get("valid"):
            result["error"] = (
                f"{result.get('error')} (the PDF's {read.pages} page(s) were read in full, but text "
                "extracted from a PDF has no code structure; pass the original source file instead)"
            )
        return result

    @staticmethod
    def analyze_python(source_code: str) -> Dict[str, Any]:
        """Perform comprehensive AST analysis on Python source code."""
        bad = _bad_source("python", source_code)
        if bad:
            return bad
        try:
            tree = ast.parse(textwrap.dedent(source_code))
        except SyntaxError as exc:
            return {
                "valid": False,
                "language": "python",
                "error": f"SyntaxError: {exc.msg} at line {exc.lineno}",
                "classes": [],
                "functions": [],
                "imports": [],
            }
        except _PARSE_ERRORS as exc:
            return {
                "valid": False,
                "language": "python",
                "error": f"{type(exc).__name__}: {exc}",
                "classes": [],
                "functions": [],
                "imports": [],
            }

        classes: List[Dict[str, Any]] = []
        functions: List[Dict[str, Any]] = []
        imports: List[str] = []
        global_vars: List[str] = []

        for node in _walk_toplevel(tree.body):
            if isinstance(node, ast.ClassDef):
                bases = [ast.unparse(b) for b in node.bases]
                methods = [m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                classes.append({
                    "name": node.name,
                    "line": node.lineno,
                    "bases": bases,
                    "methods": methods,
                    "docstring": ast.get_docstring(node),
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({
                    "name": node.name,
                    "line": node.lineno,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "args": _py_args(node.args),
                    "docstring": ast.get_docstring(node),
                })
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = "." * node.level + (node.module or "")
                for alias in node.names:
                    imports.append(f"{mod}.{alias.name}" if mod and not mod.endswith(".") else f"{mod}{alias.name}")
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        global_vars.append(tgt.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                global_vars.append(node.target.id)

        return {
            "valid": True,
            "language": "python",
            "classes": [c["name"] for c in classes],
            "classes_detailed": classes,
            "functions": [f["name"] for f in functions],
            "functions_detailed": functions,
            "imports": list(dict.fromkeys(imports)),
            "global_variables": list(dict.fromkeys(global_vars)),
        }

    @staticmethod
    def analyze_javascript_typescript(source_code: str, lang: str = "typescript") -> Dict[str, Any]:
        """Parse JavaScript, TypeScript, or Dart using line-oriented pattern extractors."""
        bad = _bad_source(lang, source_code, MAX_PATTERN_SOURCE_CHARS)
        if bad:
            return bad
        classes: List[str] = []
        interfaces: List[str] = []
        functions: List[str] = []
        imports: List[str] = []
        exports: List[str] = []
        skipped: List[int] = []

        for line in _code_lines(source_code, skipped):
            # Class definitions
            cls_match = (_DART_CLASS if lang == "dart" else _JS_CLASS).search(line)
            if cls_match and not line.startswith(("'", '"', "`")):
                classes.append(cls_match.group(1))

            # Interfaces (TS / Dart)
            if_match = re.search(r"\binterface\s+([A-Za-z_]\w*)", line)
            if if_match:
                interfaces.append(if_match.group(1))

            # Named functions: function foo(), async function bar()
            fn_match = _JS_FN.search(line)
            if fn_match:
                name = fn_match.group(1)
                if name not in _NOT_FUNCTIONS:
                    functions.append(name)

            # Arrow function assigns: const myFunc = (...) =>
            arrow_match = _JS_ARROW.search(line)
            if arrow_match:
                functions.append(arrow_match.group(1))

            # Imports
            if line.startswith(("import ", "require(")) or re.search(r"\bfrom\s+['\"][^'\"]+['\"]", line) \
                    or _JS_REQUIRE.search(line):
                imports.append(line)

            # Exports
            if line.startswith("export "):
                exports.append(line)

        return _warn_long({
            "valid": True,
            "language": lang,
            "classes": list(dict.fromkeys(classes)),
            "interfaces": list(dict.fromkeys(interfaces)),
            "functions": list(dict.fromkeys(functions)),
            "imports": list(dict.fromkeys(imports)),
            "exports": list(dict.fromkeys(exports)),
        }, skipped)

    @staticmethod
    def analyze_go(source_code: str) -> Dict[str, Any]:
        """Extract Go packages, structs, interfaces, and functions."""
        bad = _bad_source("go", source_code)
        if bad:
            return bad
        package_name = "main"
        structs: List[str] = []
        interfaces: List[str] = []
        functions: List[str] = []
        imports: List[str] = []
        skipped: List[int] = []

        in_import_block = False
        quoted = re.compile(r"[\"`]([^\"`]+)[\"`]")

        for line in _code_lines(source_code, skipped):
            if line.startswith("package "):
                parts = line.split()
                if len(parts) > 1:
                    package_name = parts[1].strip()
            elif line.startswith("import ("):
                in_import_block = True
            elif in_import_block:
                if line.startswith(")"):
                    in_import_block = False
                elif line:
                    m = quoted.search(line)
                    if m:
                        imports.append(m.group(1))
            elif line.startswith("import "):
                m = quoted.search(line)
                if m:
                    imports.append(m.group(1))
            else:
                # Struct / Interface (generic types: type Foo[T any] struct)
                type_match = re.match(r"type\s+([A-Za-z0-9_]+)(?:\[[^\]]*\])?\s+(struct|interface)\b", line)
                if type_match:
                    name, kind = type_match.group(1), type_match.group(2)
                    if kind == "struct":
                        structs.append(name)
                    else:
                        interfaces.append(name)

                # Functions & Methods (generic functions: func Map[T any](...))
                fn_match = re.match(r"func\s+(?:\([^)]+\)\s+)?([A-Za-z0-9_]+)\s*(?:\[[^\]]*\])?\s*\(", line)
                if fn_match:
                    functions.append(fn_match.group(1))

        return _warn_long({
            "valid": True,
            "language": "go",
            "package": package_name,
            "classes": structs,
            "interfaces": interfaces,
            "functions": list(dict.fromkeys(functions)),
            "imports": list(dict.fromkeys(imports)),
        }, skipped)

    @staticmethod
    def analyze_rust(source_code: str) -> Dict[str, Any]:
        """Extract Rust structs, enums, traits, and functions."""
        bad = _bad_source("rust", source_code)
        if bad:
            return bad
        structs: List[str] = []
        enums: List[str] = []
        traits: List[str] = []
        functions: List[str] = []
        uses: List[str] = []
        skipped: List[int] = []

        for line in _code_lines(source_code, skipped):
            if line.startswith("use ") or re.match(r"pub(?:\([^)]*\))?\s+use\s", line):
                uses.append(re.sub(r"^(?:pub(?:\([^)]*\))?\s+)?use\s+", "", line).rstrip(";").strip())
            else:
                s_match = re.search(r"\b(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z0-9_]+)", line)
                if s_match:
                    structs.append(s_match.group(1))

                e_match = re.search(r"\b(?:pub(?:\([^)]*\))?\s+)?enum\s+([A-Za-z0-9_]+)", line)
                if e_match:
                    enums.append(e_match.group(1))

                t_match = re.search(r"\b(?:pub(?:\([^)]*\))?\s+)?trait\s+([A-Za-z0-9_]+)", line)
                if t_match:
                    traits.append(t_match.group(1))

                fn_match = re.search(r"\bfn\s+([A-Za-z0-9_]+)\s*(?:<[^>(]*>)?\s*\(", line)
                if fn_match:
                    functions.append(fn_match.group(1))

        return _warn_long({
            "valid": True,
            "language": "rust",
            "classes": structs,
            "enums": enums,
            "traits": traits,
            "functions": list(dict.fromkeys(functions)),
            "imports": list(dict.fromkeys(uses)),
        }, skipped)

    _EXT_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".dart": "dart",
        ".go": "go",
        ".rs": "rust",
    }

    @classmethod
    def _language_for(cls, filepath: str) -> Optional[str]:
        return cls._EXT_MAP.get(PurePath(str(filepath).replace("\\", "/")).suffix.lower())

    @classmethod
    def extract_cross_language_symbols(cls, files: Dict[str, str]) -> Dict[str, Any]:
        """Analyze a collection of files mapped by filename to source content.

        The language comes from the extension; files of an unsupported type are listed under
        ``skipped_files`` and are not analysed as Python. A value may be bytes: a PDF is read in full,
        then reported as not being source code.
        """
        project_summary: Dict[str, Any] = {}
        all_symbols: List[Dict[str, str]] = []
        skipped: List[Dict[str, str]] = []

        if not isinstance(files, dict):
            return {
                "total_files_analyzed": 0,
                "total_symbols_indexed": 0,
                "symbols": [],
                "file_analyses": {},
                "skipped_files": [],
                "error": "files must be a dict mapping file names to source text",
            }

        for filepath, content in files.items():
            name = str(filepath)
            lang = cls._language_for(name)
            if lang is None:
                project_summary[name] = {"valid": False, "language": None,
                                         "error": f"Unsupported file type for '{name}'"}
                skipped.append({"file": name, "reason": "unsupported file type"})
                continue
            if isinstance(content, (bytes, bytearray)):
                try:
                    read = read_source_bytes(bytes(content), name=name)
                except SourceReadError as exc:
                    project_summary[name] = {"valid": False, "language": lang, "error": str(exc)}
                    skipped.append({"file": name, "reason": str(exc)})
                    continue
                content = read.text
                if read.kind == "pdf":
                    project_summary[name] = {
                        "valid": False,
                        "language": lang,
                        "error": (
                            f"The PDF's {read.pages} page(s) were read in full, but a PDF is not source code."
                        ),
                        "input": read.describe(),
                    }
                    skipped.append({"file": name, "reason": "PDF is not source code"})
                    continue
            res = cls.analyze(content, language=lang)
            project_summary[name] = res
            if res.get("valid"):
                for c in res.get("classes", []):
                    all_symbols.append({"symbol": c, "kind": "class", "file": name, "language": lang})
                for f in res.get("functions", []):
                    all_symbols.append({"symbol": f, "kind": "function", "file": name, "language": lang})

        return {
            "total_files_analyzed": len(files) - len(skipped),
            "total_symbols_indexed": len(all_symbols),
            "symbols": all_symbols,
            "file_analyses": project_summary,
            "skipped_files": skipped,
        }
