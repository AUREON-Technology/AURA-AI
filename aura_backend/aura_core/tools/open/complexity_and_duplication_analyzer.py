import ast
import sys
from collections import defaultdict
from typing import Any, Dict, List


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
        """Compute maximum control-flow nesting depth inside an AST node."""
        max_depth = current_depth
        # Bug 6 fix: Removed ast.With and ast.AsyncWith — only count actual control-flow nesting.
        nesting_types = (
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.Try,
        )
        for child in ast.iter_child_nodes(node):
            # Bug 4 fix: elif is an ast.If in the orelse list of a parent ast.If.
            # Treat elif at the same nesting level as its parent if.
            is_elif = (
                isinstance(child, ast.If)
                and isinstance(node, ast.If)
                and child in node.orelse
            )
            if is_elif:
                next_depth = current_depth  # Same level — don't increase depth
            elif isinstance(child, nesting_types):
                next_depth = current_depth + 1
            else:
                next_depth = current_depth
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
    def _merge_overlapping_spans(spans: List[tuple[int, int]]) -> List[tuple[int, int]]:
        """Merge overlapping or adjacent (start, end) line spans into non-overlapping regions."""
        if not spans:
            return []
        sorted_spans = sorted(spans, key=lambda s: (s[0], s[1]))
        merged = [sorted_spans[0]]
        for start, end in sorted_spans[1:]:
            prev_start, prev_end = merged[-1]
            # Overlapping or contiguous (adjacent means start <= prev_end + 1)
            if start <= prev_end + 1:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged

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

        # Bug 5 fix: Merge overlapping/adjacent spans per duplicate key before reporting.
        duplicates: List[Dict[str, Any]] = []
        seen_merged_spans: set = set()
        # Group all duplicate window spans by their content key, then merge.
        all_dup_spans: Dict[str, List[tuple[int, int]]] = {}
        for key, occurrences in windows.items():
            if len(occurrences) > 1:
                # Collect spans per key — but we need to merge across keys too,
                # since overlapping windows of the same content produce multiple keys.
                for span in occurrences:
                    all_dup_spans.setdefault(key, []).append(span)

        # Now, group spans by their location — multiple window keys can contribute
        # spans at the same location. We want to merge overlapping spans that come
        # from the same duplicated content region.
        # Strategy: for each set of occurrences with the same key, merge their spans.
        # Then, across all keys, group by merged spans to avoid double-reporting.
        merged_groups: Dict[tuple, Dict[str, Any]] = {}
        for key, spans in all_dup_spans.items():
            merged = ComplexityAndDuplicationAnalyzer._merge_overlapping_spans(spans)
            merged_key = tuple(sorted(merged))
            if merged_key not in merged_groups:
                merged_groups[merged_key] = {
                    "occurrences_count": len(merged),
                    "spans": merged,
                    "preview": key.splitlines()[0],
                }
            else:
                # Update: prefer the group with larger spans (more context).
                existing = merged_groups[merged_key]
                if len(merged) > existing["occurrences_count"]:
                    merged_groups[merged_key] = {
                        "occurrences_count": len(merged),
                        "spans": merged,
                        "preview": key.splitlines()[0],
                    }

        # Further merge: group entries whose span sets overlap.
        # Build final list by merging groups that share any span region.
        final_groups: List[Dict[str, Any]] = []
        used_span_sets: set = set()
        for merged_key, info in merged_groups.items():
            frozen = frozenset(merged_key)
            if frozen in used_span_sets:
                continue
            # Find all groups whose spans overlap with this one.
            combined_spans = list(merged_key)
            changed = True
            while changed:
                changed = False
                for other_key, other_info in merged_groups.items():
                    other_frozen = frozenset(other_key)
                    if other_frozen in used_span_sets:
                        continue
                    # Check overlap
                    for s1 in combined_spans:
                        for s2 in other_key:
                            if s1[0] <= s2[1] + 1 and s2[0] <= s1[1] + 1:
                                combined_spans.extend(other_key)
                                combined_spans = ComplexityAndDuplicationAnalyzer._merge_overlapping_spans(combined_spans)
                                used_span_sets.add(other_frozen)
                                changed = True
                                break
                        if changed:
                            break
                    if changed:
                        break
            used_span_sets.add(frozen)
            if len(combined_spans) > 1:
                final_groups.append({
                    "occurrences_count": len(combined_spans),
                    "spans": combined_spans,
                    "preview": info["preview"],
                })

        return {
            "has_duplication": len(final_groups) > 0,
            "duplicates_count": len(final_groups),
            "duplicates": final_groups,
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
