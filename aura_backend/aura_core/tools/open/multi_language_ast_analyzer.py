import ast
import re
from typing import Any, Dict, List


class MultiLanguageAstAnalyzer:
    """Structural code analyzer for Python (AST) and regex-based symbol extraction for JS/TS/Dart/Go."""

    SUPPORTED_LANGUAGES = {"python", "javascript", "typescript", "dart", "go"}

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

        classes: List[str] = []
        functions: List[str] = []
        imports: List[str] = []

        if lang == "python":
            try:
                tree = ast.parse(source_code)
            except SyntaxError as exc:
                return {"valid": False, "language": lang, "error": f"SyntaxError: {exc}"}

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    classes.append(node.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(node.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
        else:
            for raw_line in source_code.splitlines():
                line = raw_line.strip()
                cls_match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", line)
                if cls_match:
                    classes.append(cls_match.group(1))

                fn_match = re.search(
                    r"\b(?:async\s+)?(?:function|def|func)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                    line,
                )
                if fn_match and fn_match.group(1) not in {"if", "for", "while", "switch", "catch"}:
                    functions.append(fn_match.group(1))

                if line.startswith(("import ", "from ")) or "require(" in line:
                    imports.append(line)

        return {
            "valid": True,
            "language": lang,
            "classes": list(dict.fromkeys(classes)),
            "functions": list(dict.fromkeys(functions)),
            "imports": list(dict.fromkeys(imports)),
        }