import ast
from collections import defaultdict
from typing import Any, Dict, List


class ComplexityAndDuplicationAnalyzer:
    """AST-based analyzer for cyclomatic complexity, nesting depth, function length, and code duplication."""

    BRANCH_NODES = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.ExceptHandler,
        ast.With,
        ast.AsyncWith,
        ast.IfExp,
    )

    @classmethod
    def compute_cyclomatic_complexity(cls, node: ast.AST) -> int:
        """Compute cyclomatic complexity of a function or module AST node."""
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, cls.BRANCH_NODES):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += max(0, len(child.values) - 1)
            elif isinstance(child, ast.comprehension):
                complexity += 1 + len(child.ifs)
        return complexity

    @staticmethod
    def compute_max_nesting_depth(node: ast.AST, current_depth: int = 0) -> int:
        """Compute maximum control-flow nesting depth inside an AST node."""
        max_depth = current_depth
        nesting_types = (
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.With,
            ast.AsyncWith,
            ast.Try,
        )
        for child in ast.iter_child_nodes(node):
            next_depth = current_depth + 1 if isinstance(child, nesting_types) else current_depth
            child_max = ComplexityAndDuplicationAnalyzer.compute_max_nesting_depth(child, next_depth)
            if child_max > max_depth:
                max_depth = child_max
        return max_depth

    @classmethod
    def analyze_functions(cls, source_code: str, max_complexity: int = 10, max_lines: int = 50) -> Dict[str, Any]:
        """Analyze each function and method for complexity, line count, nesting, and maintainability."""
        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            return {"valid_syntax": False, "error": f"SyntaxError: {exc}", "functions": []}

        lines = source_code.splitlines()
        results: List[Dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start_line = getattr(node, "lineno", 1)
                end_line = getattr(node, "end_lineno", start_line)
                length = max(1, end_line - start_line + 1)
                cc = cls.compute_cyclomatic_complexity(node)
                nesting = cls.compute_max_nesting_depth(node)
                flagged = cc > max_complexity or length > max_lines

                results.append({
                    "name": node.name,
                    "start_line": start_line,
                    "end_line": end_line,
                    "line_count": length,
                    "cyclomatic_complexity": cc,
                    "max_nesting_depth": nesting,
                    "flagged": flagged,
                })

        return {
            "valid_syntax": True,
            "functions_count": len(results),
            "flagged_count": sum(1 for r in results if r["flagged"]),
            "functions": results,
        }

    @staticmethod
    def detect_duplicate_blocks(source_code: str, window_size: int = 5) -> Dict[str, Any]:
        """Detect repeated normalized line windows as a signal for code duplication."""
        normalized_lines: List[tuple[int, str]] = []
        for idx, raw in enumerate(source_code.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            normalized_lines.append((idx, stripped))

        if len(normalized_lines) < window_size:
            return {"has_duplication": False, "duplicates": []}

        windows: Dict[str, List[tuple[int, int]]] = defaultdict(list)
        for i in range(len(normalized_lines) - window_size + 1):
            chunk = normalized_lines[i : i + window_size]
            key = "\n".join(text for _, text in chunk)
            start_l = chunk[0][0]
            end_l = chunk[-1][0]
            windows[key].append((start_l, end_l))

        duplicates: List[Dict[str, Any]] = []
        for key, occurrences in windows.items():
            if len(occurrences) > 1:
                duplicates.append({
                    "occurrences_count": len(occurrences),
                    "spans": occurrences,
                    "preview": key.splitlines()[0],
                })

        return {
            "has_duplication": len(duplicates) > 0,
            "duplicates_count": len(duplicates),
            "duplicates": duplicates,
        }

    @classmethod
    def analyze_source(
        cls,
        source_code: str,
        max_complexity: int = 10,
        max_lines: int = 50,
        dup_window: int = 5,
    ) -> Dict[str, Any]:
        """Unified complexity and duplication report for a Python source string."""
        effective_lines = [
            ln for ln in source_code.splitlines() if ln.strip() and not ln.strip().startswith("#")
        ]
        fn_report = cls.analyze_functions(source_code, max_complexity=max_complexity, max_lines=max_lines)
        if not fn_report["valid_syntax"]:
            return {
                "valid_syntax": False,
                "error": fn_report["error"],
                "effective_lines": len(effective_lines),
            }

        dup_report = cls.detect_duplicate_blocks(source_code, window_size=dup_window)
        max_cc = max([f["cyclomatic_complexity"] for f in fn_report["functions"]], default=1)
        maintainable = fn_report["flagged_count"] == 0 and not dup_report["has_duplication"]

        return {
            "valid_syntax": True,
            "effective_lines": len(effective_lines),
            "max_cyclomatic_complexity": max_cc,
            "maintainable": maintainable,
            "functions_analysis": fn_report,
            "duplication_analysis": dup_report,
        }