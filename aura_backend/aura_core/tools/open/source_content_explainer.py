import ast
from typing import Any, Dict, List


class SourceContentExplainer:
    """Explainer for extracting structure, headings, and study notes from source or doc files."""

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