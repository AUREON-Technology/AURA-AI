import ast
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Allowed characters in a git ref to prevent argument injection.
_SAFE_REF_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "._-/~^@{}"
)


class RegressionImpactAnalyzer:
    """Analyzer for identifying changed modules, reverse import dependents, and regression risk."""

    @staticmethod
    def parse_changed_files_from_diff(diff_text: str) -> List[Dict[str, Any]]:
        """Extract modified file paths from a unified git diff string.

        Returns a list of dicts with ``"path"`` and ``"deleted"`` keys so the
        caller knows whether the file was removed.
        """
        changed: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        lines = diff_text.splitlines()
        for idx, line in enumerate(lines):
            if line.startswith("+++ b/"):
                fpath = line[6:].strip()
                if fpath != "/dev/null" and fpath not in seen:
                    seen.add(fpath)
                    changed.append({"path": fpath, "deleted": False})
            elif line.startswith("+++ /dev/null"):
                # The file was deleted – capture the path from the preceding
                # ``--- a/`` line.
                if idx > 0 and lines[idx - 1].startswith("--- a/"):
                    del_path = lines[idx - 1][6:].strip()
                    if del_path not in seen:
                        seen.add(del_path)
                        changed.append({"path": del_path, "deleted": True})

        return changed

    @staticmethod
    def get_git_changed_files(repo_dir: str, base_ref: str = "HEAD") -> Dict[str, Any]:
        """Get changed files in a git repository relative to base_ref."""
        # --- Bug 5 fix: validate base_ref to prevent argument injection ---
        if not base_ref or base_ref.startswith("-"):
            return {
                "success": False,
                "error": f"Invalid base_ref: {base_ref!r}",
                "files": [],
            }
        if not all(ch in _SAFE_REF_CHARS for ch in base_ref):
            return {
                "success": False,
                "error": f"Invalid characters in base_ref: {base_ref!r}",
                "files": [],
            }

        try:
            proc = subprocess.run(
                # Use ``--`` separator so the ref is never parsed as an option.
                ["git", "diff", "--name-only", "--", base_ref],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode != 0:
                return {"success": False, "error": proc.stderr.strip(), "files": []}
            files = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
            return {"success": True, "base_ref": base_ref, "files": files}
        except FileNotFoundError:
            return {"success": False, "error": "git executable not found.", "files": []}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "git diff timed out.", "files": []}

    @staticmethod
    def extract_imported_modules_from_file(py_file: str) -> List[str]:
        """Parse a Python file and return all imported module paths.

        Relative imports are recorded with leading dots so that the
        reverse-dependent scan can attempt to match them (e.g. ``..llm``,
        ``...module``).
        """
        path = Path(py_file)
        if not path.exists() or not path.is_file():
            return []
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            return []

        modules: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    # Relative import – store with leading dots.
                    dots = "." * node.level
                    if node.module:
                        modules.add(f"{dots}{node.module}")
                    else:
                        # ``from . import x`` – record dots alone.
                        modules.add(dots)
                elif node.module:
                    modules.add(node.module)
        return sorted(modules)

    @classmethod
    def _module_fragments(cls, file_path: str) -> List[str]:
        """Generate multiple possible module dotted-path fragments for *file_path*.

        For ``aura_backend/aura_core/llm/router.py`` this returns::

            ['aura_backend.aura_core.llm.router',
             'aura_core.llm.router',
             'llm.router',
             'router']

        The caller should check whether *any* of these fragments appears in an
        import string.
        """
        parts = Path(file_path).with_suffix("").as_posix().split("/")
        fragments: List[str] = []
        for start in range(len(parts)):
            fragments.append(".".join(parts[start:]))
        return fragments

    @classmethod
    def find_reverse_dependents(
        cls,
        repo_dir: str,
        target_module_fragments: List[str],
        max_files: int = 10000,
    ) -> Dict[str, Any]:
        """Scan Python files in repo_dir to find modules that import any of
        *target_module_fragments*.

        Returns a dict with ``dependents``, ``truncated`` and
        ``files_scanned`` keys.
        """
        root = Path(repo_dir)
        if not root.exists():
            return {"dependents": [], "truncated": False, "files_scanned": 0}

        dependents: List[str] = []
        count = 0
        truncated = False
        for py_path in root.rglob("*.py"):
            if any(part in {".git", "__pycache__", "venv", ".venv", "node_modules"} for part in py_path.parts):
                continue
            count += 1
            if count > max_files:
                truncated = True
                break

            imported = cls.extract_imported_modules_from_file(str(py_path))
            if any(
                fragment in mod
                for mod in imported
                for fragment in target_module_fragments
            ):
                rel = str(py_path.relative_to(root))
                dependents.append(rel)

        return {
            "dependents": dependents,
            "truncated": truncated,
            "files_scanned": count,
        }

    @staticmethod
    def suggest_related_tests(
        affected_files: List[str],
        test_root: str = "tests",
        repo_dir: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Suggest test file paths corresponding to modified or affected source files.

        If *repo_dir* is provided, each suggestion includes an ``"exists"``
        flag indicating whether the file actually exists on disk.
        """
        suggestions: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for fpath in affected_files:
            stem = Path(fpath).stem
            if stem.startswith("test_"):
                test_path = fpath
            else:
                test_path = f"{test_root}/test_{stem}.py"
            if test_path in seen:
                continue
            seen.add(test_path)
            entry: Dict[str, Any] = {"path": test_path}
            if repo_dir is not None:
                entry["exists"] = Path(repo_dir, test_path).exists()
            suggestions.append(entry)
        return suggestions

    @classmethod
    def analyze_impact(
        cls,
        modified_files: List[str],
        repo_dir: Optional[str] = None,
        extra_dependents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Compute affected modules, suggested tests, and overall regression risk level."""
        affected: Set[str] = set(extra_dependents or [])
        total_files_scanned = 0
        any_truncated = False

        for mfile in modified_files:
            fragments = cls._module_fragments(mfile)
            if repo_dir:
                rev = cls.find_reverse_dependents(repo_dir, fragments)
                affected.update(rev["dependents"])
                total_files_scanned = max(total_files_scanned, rev["files_scanned"])
                if rev["truncated"]:
                    any_truncated = True

        affected_list = sorted(affected)
        total_blast_radius = len(modified_files) + len(affected_list)

        if total_blast_radius > 6:
            risk = "HIGH"
        elif total_blast_radius > 2:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        return {
            "modified_files": modified_files,
            "affected_modules": affected_list,
            "blast_radius_count": total_blast_radius,
            "risk_level": risk,
            "suggested_tests": cls.suggest_related_tests(
                modified_files + affected_list,
                repo_dir=repo_dir,
            ),
            "scan_truncated": any_truncated,
            "files_scanned": total_files_scanned,
        }
