import ast
import re
from typing import Any, Dict, List, Optional


class PeerCodeReviewerAgent:
    """Automated peer code review helper for inspecting diffs, code smells, and review findings."""

    SMELL_PATTERNS: List[Dict[str, str]] = [
        {
            "code": "REV-DEBUG-PRINT",
            "severity": "MINOR",
            "pattern": r"\bprint\s*\(",
            "message": "Debug print() statement found; prefer structured logger if appropriate.",
        },
        {
            "code": "REV-TODO-MARKER",
            "severity": "MINOR",
            "pattern": r"\b(TODO|FIXME|XXX)\b",
            "message": "Unresolved task marker found in modified lines.",
        },
        {
            "code": "REV-BROAD-EXCEPT",
            "severity": "MAJOR",
            "pattern": r"\bexcept\s+Exception\b",
            "message": "Broad 'except Exception' may hide specific failure modes.",
        },
    ]

    @classmethod
    def review_diff(cls, diff_text: str) -> Dict[str, Any]:
        """Inspect added lines in a git diff for common code smells and formatting issues."""
        findings: List[Dict[str, Any]] = []
        current_file = "<unknown>"
        added_lines_count = 0
        removed_lines_count = 0

        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:].strip()
                continue
            if line.startswith("+") and not line.startswith("+++"):
                added_lines_count += 1
                added_content = line[1:]
                for rule in cls.SMELL_PATTERNS:
                    if re.search(rule["pattern"], added_content):
                        findings.append({
                            "code": rule["code"],
                            "severity": rule["severity"],
                            "file": current_file,
                            "message": rule["message"],
                            "snippet": added_content.strip(),
                        })
            elif line.startswith("-") and not line.startswith("---"):
                removed_lines_count += 1

        has_major = any(f["severity"] == "MAJOR" for f in findings)
        return {
            "reviewed": True,
            "approved": not has_major,
            "added_lines": added_lines_count,
            "removed_lines": removed_lines_count,
            "findings_count": len(findings),
            "findings": findings,
        }

    @staticmethod
    def review_python_conventions(source_code: str, filename: str = "<string>") -> Dict[str, Any]:
        """Check basic Python AST conventions such as missing docstrings on public classes/functions."""
        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            return {
                "valid_syntax": False,
                "error": f"SyntaxError: {exc}",
                "findings": [],
            }

        findings: List[Dict[str, Any]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_") and ast.get_docstring(node) is None:
                    findings.append({
                        "code": "REV-MISSING-DOCSTRING",
                        "severity": "MINOR",
                        "file": filename,
                        "line": getattr(node, "lineno", 0),
                        "symbol": node.name,
                        "message": f"Public symbol '{node.name}' lacks a docstring.",
                    })

        return {
            "valid_syntax": True,
            "findings_count": len(findings),
            "findings": findings,
        }

    @classmethod
    def full_review(
        cls,
        diff_text: Optional[str] = None,
        source_code: Optional[str] = None,
        filename: str = "<string>",
    ) -> Dict[str, Any]:
        """Combine diff review and Python convention review into a structured critique report."""
        all_findings: List[Dict[str, Any]] = []
        diff_res: Optional[Dict[str, Any]] = None
        conv_res: Optional[Dict[str, Any]] = None

        if diff_text is not None:
            diff_res = cls.review_diff(diff_text)
            all_findings.extend(diff_res["findings"])

        if source_code is not None:
            conv_res = cls.review_python_conventions(source_code, filename=filename)
            all_findings.extend(conv_res["findings"])

        major_count = sum(1 for f in all_findings if f.get("severity") == "MAJOR")
        return {
            "reviewed": True,
            "approved": major_count == 0,
            "major_issues": major_count,
            "total_findings": len(all_findings),
            "findings": all_findings,
            "diff_review": diff_res,
            "conventions_review": conv_res,
        }