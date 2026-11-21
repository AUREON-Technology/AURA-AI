"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Its AST summary
duplicates ``analyze_python_ast``; the rest is heading extraction the model does by reading.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import ast
import re
from typing import Any, Dict, List, Optional


class SourceContentExplainer:
    """Explainer for extracting structure, headings, architecture, and study notes from source or doc files."""

    @staticmethod
    def explain(
        file_name: str,
        file_content: str,
        detail_level: str = "comprehensive",
    ) -> Dict[str, Any]:
        """Produce a structured summary of symbols, headings, and key sections in a file."""
        lines = file_content.splitlines()
        headings = [ln.lstrip("#").strip() for ln in lines if ln.lstrip().startswith("#")]
        symbols: List[str] = []

        if file_name.lower().endswith(".py"):
            try:
                tree = ast.parse(file_content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        symbols.append(f"Class: {node.name}")
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                        symbols.append(f"Function: {node.name}")
            except SyntaxError:
                pass

        key_topics = headings if headings else symbols[:8]
        return {
            "file_name": file_name,
            "detail_level": detail_level,
            "line_count": len(lines),
            "headings": headings,
            "public_symbols": symbols,
            "key_topics": key_topics,
            "summary": f"Analyzed '{file_name}' ({len(lines)} lines) with {len(symbols)} public symbols and {len(headings)} headings.",
        }

    @classmethod
    def generate_study_guide(cls, file_name: str, file_content: str) -> Dict[str, Any]:
        """Generate a pedagogical study guide with core concepts, architectural summary, and review takeaways."""
        analysis = cls.explain(file_name, file_content)
        lines = file_content.splitlines()

        # Identify key terminology and concepts
        words = re.findall(r"\b[A-Z][a-zA-Z0-9_]{3,}\b", file_content)
        frequent_concepts = list(dict.fromkeys(words))[:6]

        takeaways = [
            f"Understand the main responsibility boundary established in '{file_name}'.",
            "Trace how inputs flow through exported classes and public methods.",
            "Verify dependencies and error-handling assumptions across external boundaries.",
        ]

        study_guide_markdown = [
            f"# Study Guide: `{file_name}`",
            f"**File Scope:** {len(lines)} lines | **Extracted Topics:** {', '.join(analysis['key_topics'][:5])}",
            "",
            "## 📌 Core Concepts",
        ]
        for concept in frequent_concepts:
            study_guide_markdown.append(f"- **{concept}**: Key identifier used throughout component logic.")

        study_guide_markdown.extend([
            "",
            "## 💡 Architectural Overview",
            f"This module exposes {len(analysis['public_symbols'])} primary interfaces. It manages data transformation and contracts.",
            "",
            "## 🎯 Review Questions & Key Takeaways",
        ])
        for t in takeaways:
            study_guide_markdown.append(f"1. {t}")

        return {
            "file_name": file_name,
            "key_concepts": frequent_concepts,
            "takeaways": takeaways,
            "study_guide_markdown": "\n".join(study_guide_markdown),
        }

    @staticmethod
    def explain_code_architecture(file_name: str, file_content: str) -> Dict[str, Any]:
        """Analyze structural design patterns, architectural layer, and dependency boundaries."""
        layer = "unknown"
        if "tools" in file_name or "tool" in file_name:
            layer = "tool / execution"
        elif "adapters" in file_name or "connector" in file_name:
            layer = "adapter / external boundary"
        elif "domain" in file_name or "core" in file_name:
            layer = "domain / kernel core"
        elif "api" in file_name or "service" in file_name:
            layer = "presentation / API layer"

        dependencies = []
        class_count = 0
        function_count = 0

        try:
            tree = ast.parse(file_content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        dependencies.append(a.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    dependencies.append(node.module)
                elif isinstance(node, ast.ClassDef):
                    class_count += 1
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function_count += 1
        except SyntaxError:
            pass

        return {
            "file_name": file_name,
            "architectural_layer": layer,
            "class_count": class_count,
            "function_count": function_count,
            "external_dependencies": sorted(list(set(dependencies))),
            "is_cohesive": class_count <= 5 and function_count <= 25,
            "design_notes": f"Module structured with {class_count} class(es) and {function_count} function(s) in {layer}.",
        }

    @staticmethod
    def explain_function(function_name: str, function_source: str) -> Dict[str, Any]:
        """Provide detailed structural and behavioral breakdown of a single Python function."""
        try:
            tree = ast.parse(function_source)
            fn_node = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
        except SyntaxError:
            fn_node = None

        if not fn_node:
            return {
                "function_name": function_name,
                "parsed": False,
                "error": "Could not parse function source code.",
            }

        args = [a.arg for a in fn_node.args.args if a.arg != "self"]
        has_docstring = ast.get_docstring(fn_node) is not None
        docstring = ast.get_docstring(fn_node) or "No docstring provided."
        is_async = isinstance(fn_node, ast.AsyncFunctionDef)

        branch_count = 0
        for child in ast.walk(fn_node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.IfExp)):
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
        try:
            tree = ast.parse(file_content)
        except SyntaxError as e:
            return {"parsed": False, "error": str(e), "catalog": []}

        module_doc = ast.get_docstring(tree) or ""
        catalog: List[Dict[str, Any]] = []

        for node in tree.body:
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

        return {
            "parsed": True,
            "module_docstring": module_doc,
            "catalog_count": len(catalog),
            "catalog": catalog,
        }
