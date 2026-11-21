"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Regex line matching for
JS/TS/Dart/Go/Rust is imprecise, and its function pattern backtracks quadratically on a long line
(40K characters of ``a(`` took 1.3 s; the 200K group-A cap would take ~30 s, uncancellable
under ``asyncio.to_thread``). The Python half duplicates ``analyze_python_ast``. A candidate only
after a per-line length cap and a better extractor.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import ast
import re
import textwrap
from typing import Any, Dict, List, Optional


class MultiLanguageAstAnalyzer:
    """Structural code analyzer for Python (native AST) and pattern-based analyzers for JS/TS, Go, Rust, and Dart."""

    SUPPORTED_LANGUAGES = {"python", "javascript", "typescript", "dart", "go", "rust"}

    @classmethod
    def analyze(cls, source_code: str, language: str = "python") -> Dict[str, Any]:
        """Extract classes, functions, and imports from source_code for the target language."""
        lang = language.strip().lower()
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
        elif lang == "rust":
            return cls.analyze_rust(source_code)

        return {"valid": False, "error": f"Unhandled language: {lang}"}

    @staticmethod
    def analyze_python(source_code: str) -> Dict[str, Any]:
        """Perform comprehensive AST analysis on Python source code."""
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

        classes: List[Dict[str, Any]] = []
        functions: List[Dict[str, Any]] = []
        imports: List[str] = []
        global_vars: List[str] = []

        for node in ast.iter_child_nodes(tree):
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
                args = [a.arg for a in node.args.args]
                functions.append({
                    "name": node.name,
                    "line": node.lineno,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "args": args,
                    "docstring": ast.get_docstring(node),
                })
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    imports.append(f"{mod}.{alias.name}" if mod else alias.name)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        global_vars.append(tgt.id)

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
        """Parse JavaScript, TypeScript, or Dart using regex pattern extractors."""
        classes: List[str] = []
        interfaces: List[str] = []
        functions: List[str] = []
        imports: List[str] = []
        exports: List[str] = []

        lines = source_code.splitlines()
        for raw_line in lines:
            line = raw_line.strip()

            # Class definitions
            cls_match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if cls_match:
                classes.append(cls_match.group(1))

            # Interfaces (TS / Dart)
            if_match = re.search(r"\binterface\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if if_match:
                interfaces.append(if_match.group(1))

            # Named functions: function foo(), async function bar()
            fn_match = re.search(
                r"\b(?:async\s+)?(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{",
                line,
            )
            if fn_match:
                name = fn_match.group(1)
                if name not in ("if", "for", "while", "switch", "catch", "return", "function"):
                    functions.append(name)

            # Arrow function assigns: const myFunc = (...) =>
            arrow_match = re.search(
                r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_]+)\s*=>",
                line,
            )
            if arrow_match:
                functions.append(arrow_match.group(1))

            # Imports
            if line.startswith(("import ", "require(")) or re.search(r"\bfrom\s+['\"][^'\"]+['\"]", line):
                imports.append(line)

            # Exports
            if line.startswith("export "):
                exports.append(line)

        return {
            "valid": True,
            "language": lang,
            "classes": list(dict.fromkeys(classes)),
            "interfaces": list(dict.fromkeys(interfaces)),
            "functions": list(dict.fromkeys(functions)),
            "imports": list(dict.fromkeys(imports)),
            "exports": list(dict.fromkeys(exports)),
        }

    @staticmethod
    def analyze_go(source_code: str) -> Dict[str, Any]:
        """Extract Go packages, structs, interfaces, and functions."""
        package_name = "main"
        structs: List[str] = []
        interfaces: List[str] = []
        functions: List[str] = []
        imports: List[str] = []

        in_import_block = False

        for raw_line in source_code.splitlines():
            line = raw_line.strip()
            if line.startswith("package "):
                package_name = line.split()[1].strip()
            elif line.startswith("import ("):
                in_import_block = True
            elif in_import_block:
                if line.startswith(")"):
                    in_import_block = False
                elif line:
                    imports.append(line.strip('"\t '))
            elif line.startswith("import "):
                imports.append(line[7:].strip('"\t '))
            else:
                # Struct / Interface
                type_match = re.search(r"type\s+([A-Za-z0-9_]+)\s+(struct|interface)", line)
                if type_match:
                    name, kind = type_match.group(1), type_match.group(2)
                    if kind == "struct":
                        structs.append(name)
                    else:
                        interfaces.append(name)

                # Functions & Methods
                fn_match = re.search(r"func\s+(?:\([^)]+\)\s+)?([A-Za-z0-9_]+)\s*\(", line)
                if fn_match:
                    functions.append(fn_match.group(1))

        return {
            "valid": True,
            "language": "go",
            "package": package_name,
            "classes": structs,
            "interfaces": interfaces,
            "functions": list(dict.fromkeys(functions)),
            "imports": list(dict.fromkeys(imports)),
        }

    @staticmethod
    def analyze_rust(source_code: str) -> Dict[str, Any]:
        """Extract Rust structs, enums, traits, and functions."""
        structs: List[str] = []
        traits: List[str] = []
        functions: List[str] = []
        uses: List[str] = []

        for raw_line in source_code.splitlines():
            line = raw_line.strip()
            if line.startswith("use "):
                uses.append(line[4:].rstrip(";").strip())
            else:
                s_match = re.search(r"\b(?:pub\s+)?struct\s+([A-Za-z0-9_]+)", line)
                if s_match:
                    structs.append(s_match.group(1))

                t_match = re.search(r"\b(?:pub\s+)?trait\s+([A-Za-z0-9_]+)", line)
                if t_match:
                    traits.append(t_match.group(1))

                fn_match = re.search(r"\b(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)\s*\(", line)
                if fn_match:
                    functions.append(fn_match.group(1))

        return {
            "valid": True,
            "language": "rust",
            "classes": structs,
            "traits": traits,
            "functions": list(dict.fromkeys(functions)),
            "imports": list(dict.fromkeys(uses)),
        }

    @classmethod
    def extract_cross_language_symbols(cls, files: Dict[str, str]) -> Dict[str, Any]:
        """Analyze a collection of files mapped by filename to source content."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".dart": "dart",
            ".go": "go",
            ".rs": "rust",
        }

        project_summary: Dict[str, Any] = {}
        all_symbols: List[Dict[str, str]] = []

        for filepath, content in files.items():
            ext = "." + filepath.split(".")[-1] if "." in filepath else ""
            lang = ext_map.get(ext, "python")
            res = cls.analyze(content, language=lang)
            project_summary[filepath] = res
            if res.get("valid"):
                for c in res.get("classes", []):
                    all_symbols.append({"symbol": c, "kind": "class", "file": filepath, "language": lang})
                for f in res.get("functions", []):
                    all_symbols.append({"symbol": f, "kind": "function", "file": filepath, "language": lang})

        return {
            "total_files_analyzed": len(files),
            "total_symbols_indexed": len(all_symbols),
            "symbols": all_symbols,
            "file_analyses": project_summary,
        }
