import ast
import re
from typing import Any, Dict, List, Optional


class PeerCodeReviewerAgent:
    """Automated peer code review agent for inspecting diffs, code smells, AST conventions, and generating PR reviews."""

    SMELL_PATTERNS: List[Dict[str, str]] = [
        {
            "code": "REV-DEBUG-PRINT",
            "severity": "MINOR",
            "pattern": r"(?<![\w.])print\s*\(",
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
            "pattern": r"\bexcept\s*(?:\(?\s*(?:Base)?Exception\b|:)",
            "message": "Broad 'except Exception' (or bare 'except:') may hide specific failure modes.",
        },
        {
            "code": "REV-INSECURE-EVAL",
            "severity": "CRITICAL",
            "pattern": r"(?<![\w.])(eval|exec)\s*\(",
            "message": "Dynamic code execution (eval/exec) detected.",
        },
        {
            "code": "REV-DISABLED-SSL",
            "severity": "CRITICAL",
            "pattern": r"\bverify\s*=\s*False\b",
            "message": "Disabled TLS/SSL certificate verification detected.",
        },
    ]

    _HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    _COMMENT_PREFIXES = ("#", "//", "/*", "*", "--")

    @classmethod
    def review_diff(cls, diff_text: str) -> Dict[str, Any]:
        """Inspect added lines in a git diff for common code smells and formatting issues.

        ``+++`` inside a hunk is an added line that starts with ``++``, not a file header. Pure
        comment lines are not scanned for code smells (a commented-out ``print(`` is not a debug
        print), only for task markers. Text that contains no added or removed line is not a diff:
        ``reviewed`` is False and ``approved`` is never True for it.
        """
        if not isinstance(diff_text, str):
            return cls._not_reviewed("diff_text must be text.")

        findings: List[Dict[str, Any]] = []
        current_file = "<unknown>"
        added_lines_count = 0
        removed_lines_count = 0
        in_hunk = False
        saw_hunk = False
        new_line = 0

        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                in_hunk = False
                continue
            hunk = cls._HUNK_RE.match(line)
            if hunk:
                in_hunk = saw_hunk = True
                new_line = int(hunk.group(1))
                continue
            if not in_hunk and line.startswith("+++ "):
                target = line[4:].split("\t")[0].strip()
                current_file = target[2:] if target.startswith("b/") else target
                continue
            if not in_hunk and line.startswith("--- "):
                continue
            if not in_hunk and not line.startswith(("+", "-")):
                continue  # headers / index lines outside any hunk

            if line.startswith("+"):
                added_lines_count += 1
                added_content = line[1:]
                is_comment = added_content.strip().startswith(cls._COMMENT_PREFIXES)
                for rule in cls.SMELL_PATTERNS:
                    if is_comment and rule["code"] != "REV-TODO-MARKER":
                        continue
                    if re.search(rule["pattern"], added_content):
                        findings.append({
                            "code": rule["code"],
                            "severity": rule["severity"],
                            "file": current_file,
                            "line": new_line if in_hunk else None,
                            "message": rule["message"],
                            "snippet": added_content.strip()[:300],
                        })
                new_line += 1
            elif line.startswith("-"):
                removed_lines_count += 1
            elif line.startswith("\\"):
                continue  # "\ No newline at end of file"
            else:
                new_line += 1  # context line

        if not (added_lines_count or removed_lines_count or saw_hunk):
            return cls._not_reviewed("No diff content found: no added or removed lines and no hunk header.")

        has_major_or_crit = any(f["severity"] in ("MAJOR", "CRITICAL") for f in findings)
        return {
            "reviewed": True,
            "approved": not has_major_or_crit,
            "added_lines": added_lines_count,
            "removed_lines": removed_lines_count,
            "findings_count": len(findings),
            "findings": findings,
        }

    @staticmethod
    def _not_reviewed(error: str) -> Dict[str, Any]:
        return {
            "reviewed": False,
            "approved": False,
            "error": error,
            "added_lines": 0,
            "removed_lines": 0,
            "findings_count": 0,
            "findings": [],
        }

    @staticmethod
    def review_python_conventions(source_code: str, filename: str = "<string>") -> Dict[str, Any]:
        """Check basic Python AST conventions such as missing docstrings on public classes/functions."""
        if not isinstance(source_code, str):
            return {"valid_syntax": False, "error": "source_code must be text.", "findings": []}
        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            return {
                "valid_syntax": False,
                "error": f"SyntaxError: {exc}",
                "findings": [],
            }
        except (ValueError, RecursionError, MemoryError) as exc:
            return {"valid_syntax": False, "error": f"{type(exc).__name__}: {exc}", "findings": []}

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
        """Combine diff review and Python convention review into a structured critique report.

        Fails closed: with nothing to review, an input that is not a diff, or source that does not
        parse, ``approved`` is False and ``errors`` says why. "No findings" is only approval when
        everything supplied was actually reviewed.
        """
        all_findings: List[Dict[str, Any]] = []
        diff_res: Optional[Dict[str, Any]] = None
        conv_res: Optional[Dict[str, Any]] = None
        errors: List[str] = []
        reviewed_any = False

        if diff_text is not None:
            diff_res = cls.review_diff(diff_text)
            all_findings.extend(diff_res["findings"])
            if diff_res["reviewed"]:
                reviewed_any = True
            else:
                errors.append(f"diff_text: {diff_res['error']}")

        if source_code is not None:
            conv_res = cls.review_python_conventions(source_code, filename=filename)
            all_findings.extend(conv_res["findings"])
            if not conv_res["valid_syntax"]:
                errors.append(f"source_code: {conv_res['error']}")
            elif not source_code.strip():
                errors.append("source_code: empty, nothing to review.")
            else:
                reviewed_any = True

        if diff_text is None and source_code is None:
            errors.append("Nothing to review: give diff_text and/or source_code.")

        critical_count = sum(1 for f in all_findings if f.get("severity") == "CRITICAL")
        major_count = sum(1 for f in all_findings if f.get("severity") == "MAJOR")

        result: Dict[str, Any] = {
            "reviewed": reviewed_any,
            "approved": reviewed_any and not errors and critical_count == 0 and major_count == 0,
            "critical_issues": critical_count,
            "major_issues": major_count,
            "total_findings": len(all_findings),
            "findings": all_findings,
            "diff_review": diff_res,
            "conventions_review": conv_res,
        }
        if errors:
            result["errors"] = errors
        return result

    @classmethod
    def suggest_inline_patches(cls, diff_text: str) -> List[Dict[str, Any]]:
        """Generate concrete code replacement suggestions for identified code smells."""
        suggestions: List[Dict[str, Any]] = []
        review = cls.review_diff(diff_text)

        for f in review.get("findings", []):
            snippet = f.get("snippet", "")
            code = f.get("code")

            if code == "REV-DEBUG-PRINT":
                # suggest logger.debug(...)
                replacement = re.sub(r"\bprint\s*\(", "logger.debug(", snippet)
                suggestions.append({
                    "file": f.get("file"),
                    "original": snippet,
                    "suggested_patch": replacement,
                    "explanation": "Replace raw print with logger call.",
                })
            elif code == "REV-DISABLED-SSL":
                replacement = re.sub(r"verify\s*=\s*False", "verify=True", snippet)
                suggestions.append({
                    "file": f.get("file"),
                    "original": snippet,
                    "suggested_patch": replacement,
                    "explanation": "Enable TLS verification to prevent Man-in-the-Middle attacks.",
                })

        return suggestions

    @staticmethod
    def _cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    @classmethod
    def generate_pr_review_comment(cls, review_result: Dict[str, Any]) -> str:
        """Format an automated GitHub/GitLab PR review summary in Markdown."""
        if not isinstance(review_result, dict):
            review_result = {}
        approved = review_result.get("approved", False) is True
        if approved:
            status_banner = "\u2705 Approved (Looks Good To Me)"
        elif review_result.get("reviewed") is False or review_result.get("errors"):
            status_banner = "\u274c Not Approved (Review Incomplete)"
        else:
            status_banner = "\u274c Changes Requested (Issues Detected)"

        lines = [
            f"## Peer Code Review: {status_banner}",
            "",
            f"- **Critical Issues:** {review_result.get('critical_issues', 0)}",
            f"- **Major Issues:** {review_result.get('major_issues', 0)}",
            f"- **Total Findings:** {review_result.get('total_findings', 0)}",
            "",
        ]
        for err in review_result.get("errors", []) or []:
            lines.append(f"- :warning: {cls._cell(err)}")
        if review_result.get("errors"):
            lines.append("")

        findings = review_result.get("findings", [])
        if findings:
            lines.extend([
                "| Severity | File | Code | Message |",
                "|---|---|---|---|",
            ])
            for f in findings:
                lines.append(
                    f"| `{cls._cell(f.get('severity'))}` | `{cls._cell(f.get('file'))}` "
                    f"| `{cls._cell(f.get('code'))}` | {cls._cell(f.get('message'))} |"
                )
            lines.append("")

        return "\n".join(lines)
