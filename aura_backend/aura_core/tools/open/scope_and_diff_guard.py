from pathlib import Path
from typing import Any, Dict, List, Optional


class ScopeAndDiffGuard:
    """Guard for checking git diff changes against allowed file scopes and change-size limits."""

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

        for line in diff_text.splitlines():
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