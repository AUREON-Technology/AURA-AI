import ast
import math
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


class ComplexityAndDuplicationAnalyzer:
    """AST-based analyzer for cyclomatic complexity, nesting depth, function length, and code duplication."""

    # Bug 1 fix: Removed ast.With and ast.AsyncWith — they are context managers, not decision points.
    BRANCH_NODES = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.ExceptHandler,
        ast.IfExp,
    )

    @staticmethod
    def _walk_no_nested_functions(node: ast.AST):
        """Walk AST children but stop descending into nested FunctionDef/AsyncFunctionDef nodes."""
        # Bug 3 fix: custom walk that doesn't recurse into nested function definitions.
        from collections import deque
        queue = deque(ast.iter_child_nodes(node))
        while queue:
            child = queue.popleft()
            yield child
            # Don't descend into nested function/class definitions
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                queue.extend(ast.iter_child_nodes(child))

    @classmethod
    def compute_cyclomatic_complexity(cls, node: ast.AST) -> int:
        """Compute cyclomatic complexity of a function or module AST node."""
        complexity = 1
        # Bug 3 fix: use custom walk to avoid double-counting nested functions' branches.
        for child in cls._walk_no_nested_functions(node):
            if isinstance(child, cls.BRANCH_NODES):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += max(0, len(child.values) - 1)
            elif isinstance(child, ast.comprehension):
                complexity += 1 + len(child.ifs)
            # Bug 2 fix: count match/case branches (Python 3.10+).
            elif sys.version_info >= (3, 10) and isinstance(child, ast.Match):
                # Each case after the first adds 1 (similar to elif).
                num_cases = len(child.cases)
                if num_cases > 1:
                    complexity += num_cases - 1
        return complexity

    @staticmethod
    def compute_max_nesting_depth(node: ast.AST, current_depth: int = 0) -> int:
        """Compute maximum control-flow nesting depth inside an AST node.

        Iterative, so a pathologically deep expression cannot exhaust the interpreter stack.
        """
        # Bug 6 fix: Removed ast.With and ast.AsyncWith — only count actual control-flow nesting.
        nesting_types: Tuple[type, ...] = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try)
        if hasattr(ast, "TryStar"):
            nesting_types += (ast.TryStar,)  # type: ignore[attr-defined]
        max_depth = current_depth
        stack: List[Tuple[ast.AST, int]] = [(node, current_depth)]
        while stack:
            parent, depth = stack.pop()
            if depth > max_depth:
                max_depth = depth
            for child in ast.iter_child_nodes(parent):
                # Bug 4 fix: elif is an ast.If in the orelse list of a parent ast.If.
                # Treat elif at the same nesting level as its parent if.
                is_elif = (
                    isinstance(child, ast.If)
                    and isinstance(parent, ast.If)
                    and child in parent.orelse
                )
                if is_elif:
                    next_depth = depth  # Same level — don't increase depth
                elif isinstance(child, nesting_types):
                    next_depth = depth + 1
                else:
                    next_depth = depth
                stack.append((child, next_depth))
        return max_depth

    @staticmethod
    def _parse(source_code: Any) -> Tuple[Optional[ast.AST], Optional[str]]:
        """``(tree, None)`` or ``(None, error)``; nothing raised for hostile or non-text input."""
        if not isinstance(source_code, str):
            return None, "source_code must be text."
        try:
            return ast.parse(source_code), None
        except SyntaxError as exc:
            return None, f"SyntaxError: {exc}"
        except (ValueError, RecursionError, MemoryError) as exc:
            return None, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _finite_threshold(*values: Any) -> bool:
        return all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values
        )

    @classmethod
    def analyze_functions(cls, source_code: str, max_complexity: int = 10, max_lines: int = 50) -> Dict[str, Any]:
        """Analyze each function and method for complexity, line count, nesting, and maintainability."""
        if not cls._finite_threshold(max_complexity, max_lines):
            return {
                "valid_syntax": False,
                "status": "invalid_input",
                "error": "max_complexity and max_lines must be finite numbers.",
                "functions": [],
            }
        tree, error = cls._parse(source_code)
        if tree is None:
            return {"valid_syntax": False, "error": error, "functions": []}

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

    #: Most duplicate groups / spans per group returned; ``truncated`` is set when more existed.
    MAX_DUPLICATE_GROUPS = 100
    MAX_SPANS_PER_GROUP = 20

    @staticmethod
    def detect_duplicate_blocks(source_code: str, window_size: int = 5) -> Dict[str, Any]:
        """Detect repeated blocks of ``window_size`` or more consecutive (normalised) code lines.

        Blank and ``#`` comment lines are ignored and indentation is stripped. Overlapping
        windows that belong to one repeated block are reported once, as a block with every
        place it occurs (``spans`` are 1-based ``(first_line, last_line)`` pairs). Two copies
        that sit right next to each other are two occurrences, not one long block.
        """
        if not isinstance(source_code, str):
            return {"has_duplication": False, "duplicates": [], "error": "source_code must be text."}
        if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size < 2:
            return {
                "has_duplication": False,
                "duplicates": [],
                "error": "window_size must be an integer >= 2.",
            }

        norm: List[Tuple[int, str]] = []
        for idx, raw in enumerate(source_code.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            norm.append((idx, stripped))

        w = window_size
        if len(norm) < w:
            return {"has_duplication": False, "duplicates_count": 0, "duplicates": []}

        # Intern each distinct line, so a window's key is a short tuple of ints.
        ids: Dict[str, int] = {}
        line_ids = [ids.setdefault(text, len(ids)) for _, text in norm]
        starts_by_key: Dict[Tuple[int, ...], List[int]] = defaultdict(list)
        for i in range(len(norm) - w + 1):
            starts_by_key[tuple(line_ids[i:i + w])].append(i)

        # A window is a duplicate when its content occurs at >= 2 non-overlapping places.
        occurrences: Dict[Tuple[int, ...], Optional[Tuple[int, ...]]] = {}
        for key, starts in starts_by_key.items():
            kept: List[int] = []
            last = -w
            for s in starts:
                if s >= last + w:
                    kept.append(s)
                    last = s
            occurrences[key] = tuple(kept) if len(kept) >= 2 else None
        sigs = [occurrences[tuple(line_ids[i:i + w])] for i in range(len(norm) - w + 1)]

        shift_cache: Dict[Tuple[int, int], bool] = {}

        def continues(prev: Tuple[int, ...], nxt: Optional[Tuple[int, ...]]) -> bool:
            if nxt is None or len(nxt) != len(prev) or nxt[0] != prev[0] + 1 or nxt[-1] != prev[-1] + 1:
                return False
            cached = shift_cache.get((id(prev), id(nxt)))
            if cached is None:
                cached = shift_cache[(id(prev), id(nxt))] = all(b == a + 1 for a, b in zip(prev, nxt))
            return cached

        # Maximal runs of consecutive duplicate windows whose occurrences all shift together.
        runs: Dict[Tuple[Tuple[int, int], ...], int] = {}  # spans -> block length in lines
        seen_runs: set = set()
        i = 0
        while i < len(sigs):
            sig = sigs[i]
            if sig is None:
                i += 1
                continue
            j = i
            while j + 1 < len(sigs) and continues(sigs[j], sigs[j + 1]):
                j += 1
            # The same occurrences extended by the same amount were already reported (a file of
            # repeated lines would otherwise rebuild one huge span list per window).
            if (id(sig), j - i) in seen_runs:
                i = j + 1
                continue
            seen_runs.add((id(sig), j - i))
            length = (j - i) + w
            spans = tuple((norm[s][0], norm[s + length - 1][0]) for s in sig)
            runs.setdefault(spans, length)
            i = j + 1

        # Longest blocks first; drop a block whose every occurrence lies inside already-kept ones.
        ordered = sorted(runs.items(), key=lambda kv: (-kv[1], kv[0][0][0]))
        covered: set = set()
        groups: List[Dict[str, Any]] = []
        total = 0
        first_line_to_idx = {line: idx for idx, (line, _) in enumerate(norm)}
        for spans, length in ordered:
            ranges = [
                range(first_line_to_idx[a], first_line_to_idx[b] + 1) for a, b in spans
            ]
            if all(all(k in covered for k in r) for r in ranges):
                continue
            for r in ranges:
                covered.update(r)
            total += 1
            if len(groups) < ComplexityAndDuplicationAnalyzer.MAX_DUPLICATE_GROUPS:
                groups.append({
                    "occurrences_count": len(spans),
                    "spans": list(spans[:ComplexityAndDuplicationAnalyzer.MAX_SPANS_PER_GROUP]),
                    "lines": length,
                    "preview": norm[first_line_to_idx[spans[0][0]]][1],
                })

        groups.sort(key=lambda g: g["spans"][0][0])
        result: Dict[str, Any] = {
            "has_duplication": total > 0,
            "duplicates_count": total,
            "duplicates": groups,
        }
        if total > len(groups) or any(g["occurrences_count"] > len(g["spans"]) for g in groups):
            result["truncated"] = True
        return result

    @classmethod
    def analyze_source(
        cls,
        source_code: str,
        max_complexity: int = 10,
        max_lines: int = 50,
        dup_window: int = 5,
    ) -> Dict[str, Any]:
        """Unified complexity and duplication report for a Python source string."""
        if not isinstance(source_code, str):
            return {
                "valid_syntax": False,
                "error": "source_code must be text.",
                "effective_lines": 0,
            }
        effective_lines = [
            ln for ln in source_code.splitlines() if ln.strip() and not ln.strip().startswith("#")
        ]
        fn_report = cls.analyze_functions(source_code, max_complexity=max_complexity, max_lines=max_lines)
        if not fn_report["valid_syntax"]:
            report = {
                "valid_syntax": False,
                "error": fn_report["error"],
                "effective_lines": len(effective_lines),
            }
            if "status" in fn_report:
                report["status"] = fn_report["status"]
            return report

        dup_report = cls.detect_duplicate_blocks(source_code, window_size=dup_window)
        max_cc = max([f["cyclomatic_complexity"] for f in fn_report["functions"]], default=1)
        dup_failed = "error" in dup_report
        maintainable = fn_report["flagged_count"] == 0 and not dup_report["has_duplication"] and not dup_failed

        result = {
            "valid_syntax": True,
            "effective_lines": len(effective_lines),
            "max_cyclomatic_complexity": max_cc,
            "maintainable": maintainable,
            "functions_analysis": fn_report,
            "duplication_analysis": dup_report,
        }
        if dup_failed:
            result["error"] = f"duplication check not run: {dup_report['error']}"
        return result
