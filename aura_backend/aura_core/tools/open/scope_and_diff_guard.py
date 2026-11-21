"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. It overlaps
``scan_code_security`` (secrets in a diff) and ``analyze_change_impact`` (diff parsing), and its
path normalisation is wrong: ``lstrip("./")`` strips characters, so ``.env`` becomes ``env`` and
the default ``.env*`` pattern misses a root ``.env`` (and ``credentials.json``). Fix before any
exposure.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import fnmatch
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class ScopeAndDiffGuard:
    """Guard for analyzing git diffs against task scope, change budgets, forbidden paths, and secret leaks."""

    SECRET_PATTERNS = [
        (re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH|PRIVATE) KEY-----"), "Private cryptographic key"),
        (re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9_\-\.]{12,}['\"]"), "Hardcoded API secret token"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
        (re.compile(r"ghp_[A-Za-z0-9]{36}"), "GitHub Personal Access Token"),
        (re.compile(r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*"), "Potential JWT token"),
    ]

    DEFAULT_FORBIDDEN_PATTERNS = [
        ".env*",
        "*.pem",
        "*.key",
        "*.p12",
        "id_rsa*",
        "**/credentials.json",
        "**/.secrets*",
    ]

    @staticmethod
    def check_diff(
        diff_text: str,
        allowed_files: Optional[List[str]] = None,
        max_lines_changed: int = 300,
    ) -> Dict[str, Any]:
        """Validate modified paths and total added/deleted line counts against task scope."""
        allowed_norm = [Path(p).as_posix().lstrip("./") for p in (allowed_files or [])]
        modified_files: List[str] = []
        added_lines = 0
        deleted_lines = 0
        violations: List[str] = []

        for raw_line in diff_text.splitlines():
            line = raw_line.strip()
            if line.startswith("+++ b/"):
                fpath = Path(line[6:].strip()).as_posix().lstrip("./")
                if fpath != "/dev/null" and fpath not in modified_files:
                    modified_files.append(fpath)
                    if allowed_norm and not any(
                        fpath == allow or fpath.startswith(allow.rstrip("/") + "/")
                        for allow in allowed_norm
                    ):
                        violations.append(f"Out-of-scope file modified: '{fpath}'")
            elif line.startswith("+") and not line.startswith("+++"):
                added_lines += 1
            elif line.startswith("-") and not line.startswith("---"):
                deleted_lines += 1

        total_changed = added_lines + deleted_lines
        if total_changed > max_lines_changed:
            violations.append(
                f"Diff size ({total_changed} changed lines) exceeds max_lines_changed ({max_lines_changed})."
            )

        within_scope = len(violations) == 0
        return {
            "within_scope": within_scope,
            "violations_count": len(violations),
            "violations": violations,
            "modified_files": modified_files,
            "lines_added": added_lines,
            "lines_deleted": deleted_lines,
            "total_lines_changed": total_changed,
            "status": "APPROVED_SCOPE" if within_scope else "SCOPE_VIOLATIONS_DETECTED",
        }

    @staticmethod
    def parse_diff_files(diff_text: str) -> List[Dict[str, Any]]:
        """Parse git diff text into structured file-level metrics."""
        files: List[Dict[str, Any]] = []
        current_file: Optional[Dict[str, Any]] = None

        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                if current_file:
                    files.append(current_file)
                parts = line.split()
                path = parts[-1].lstrip("b/") if len(parts) >= 4 else "unknown"
                current_file = {
                    "path": path,
                    "is_new": False,
                    "is_deleted": False,
                    "is_rename": False,
                    "lines_added": 0,
                    "lines_deleted": 0,
                    "hunks_count": 0,
                }
            elif current_file is not None:
                if line.startswith("new file mode"):
                    current_file["is_new"] = True
                elif line.startswith("deleted file mode"):
                    current_file["is_deleted"] = True
                elif line.startswith("similarity index"):
                    current_file["is_rename"] = True
                elif line.startswith("+++ b/"):
                    current_file["path"] = line[6:].strip()
                elif line.startswith("@@ "):
                    current_file["hunks_count"] += 1
                elif line.startswith("+") and not line.startswith("+++"):
                    current_file["lines_added"] += 1
                elif line.startswith("-") and not line.startswith("---"):
                    current_file["lines_deleted"] += 1

        if current_file:
            files.append(current_file)

        return files

    @classmethod
    def check_scope_compliance(
        cls,
        modified_files: List[str],
        allowed_patterns: List[str],
        forbidden_patterns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Check list of file paths against allowed whitelist and forbidden blacklist patterns."""
        forbidden = (forbidden_patterns or []) + cls.DEFAULT_FORBIDDEN_PATTERNS
        violations: List[str] = []
        out_of_scope: List[str] = []
        forbidden_hits: List[str] = []

        for f in modified_files:
            norm_f = f.replace("\\", "/").lstrip("./")

            # Check forbidden
            for pat in forbidden:
                if fnmatch.fnmatch(norm_f, pat) or fnmatch.fnmatch(Path(norm_f).name, pat):
                    forbidden_hits.append(norm_f)
                    violations.append(f"Forbidden file modified: '{norm_f}' matches pattern '{pat}'")
                    break

            # Check allowed (if whitelist provided)
            if allowed_patterns:
                matched = False
                for allow in allowed_patterns:
                    norm_allow = allow.replace("\\", "/").lstrip("./")
                    if fnmatch.fnmatch(norm_f, norm_allow) or norm_f.startswith(norm_allow.rstrip("/") + "/"):
                        matched = True
                        break
                if not matched:
                    out_of_scope.append(norm_f)
                    violations.append(f"File '{norm_f}' outside permitted scope")

        return {
            "compliant": len(violations) == 0,
            "violations": violations,
            "out_of_scope_files": out_of_scope,
            "forbidden_files": forbidden_hits,
        }

    @classmethod
    def detect_accidental_secrets_in_diff(cls, diff_text: str) -> List[Dict[str, Any]]:
        """Scan added diff lines for prospective credentials or confidential tokens."""
        findings: List[Dict[str, Any]] = []
        current_file = "unknown"
        line_idx = 0

        for raw_line in diff_text.splitlines():
            line_idx += 1
            line = raw_line.strip()
            if line.startswith("+++ b/"):
                current_file = line[6:].strip()
                continue
            if line.startswith("+") and not line.startswith("+++"):
                content = line[1:].strip()
                for pattern, desc in cls.SECRET_PATTERNS:
                    match = pattern.search(content)
                    if match:
                        matched_snippet = match.group(0)
                        redacted = matched_snippet[:4] + "***" + matched_snippet[-2:] if len(matched_snippet) > 6 else "***"
                        findings.append({
                            "file": current_file,
                            "line_in_diff": line_idx,
                            "type": desc,
                            "snippet": redacted,
                        })

        return findings

    @classmethod
    def check_diff_size_budget(
        cls,
        diff_text: str,
        max_files: int = 15,
        max_lines_per_file: int = 250,
        max_total_lines: int = 600,
    ) -> Dict[str, Any]:
        """Verify whether a diff is atomic and bounded according to best practice size budgets."""
        files = cls.parse_diff_files(diff_text)
        total_files = len(files)
        total_lines = sum(f["lines_added"] + f["lines_deleted"] for f in files)

        warnings: List[str] = []
        blockers: List[str] = []

        if total_files > max_files:
            blockers.append(f"Too many modified files ({total_files} > max {max_files})")
        elif total_files > max_files * 0.8:
            warnings.append(f"File count approaching limit ({total_files}/{max_files})")

        if total_lines > max_total_lines:
            blockers.append(f"Total diff volume too large ({total_lines} lines > max {max_total_lines})")

        for f in files:
            f_changed = f["lines_added"] + f["lines_deleted"]
            if f_changed > max_lines_per_file:
                blockers.append(f"File '{f['path']}' has {f_changed} changes (max per file {max_lines_per_file})")

        return {
            "within_budget": len(blockers) == 0,
            "total_files": total_files,
            "total_lines_changed": total_lines,
            "blockers": blockers,
            "warnings": warnings,
            "files_summary": files,
        }
