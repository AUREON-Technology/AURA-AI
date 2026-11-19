import ast
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class RegressionImpactAnalyzer:
    """Analyzer for identifying changed modules, reverse import dependents, and regression risk."""

    @staticmethod
    def parse_changed_files_from_diff(diff_text: str) -> List[str]:
        """Extract modified file paths from a unified git diff string."""
        changed: List[str] = []
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                fpath = line[6:].strip()
                if fpath != "/dev/null" and fpath not in changed:
                    changed.append(fpath)
        return changed

    @staticmethod
    def get_git_changed_files(repo_dir: str, base_ref: str = "HEAD") -> Dict[str, Any]:
        """Get changed files in a git repository relative to base_ref."""
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", base_ref],
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
        """Parse a Python file and return all imported module paths."""
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
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return sorted(modules)

    @classmethod
    def find_reverse_dependents(
        cls,
        repo_dir: str,
        target_module_fragment: str,
        max_files: int = 500,
    ) -> List[str]:
        """Scan Python files in repo_dir to find modules that import target_module_fragment."""
        root = Path(repo_dir)
        if not root.exists():
            return []

        dependents: List[str] = []
        count = 0
        for py_path in root.rglob("*.py"):
            if any(part in {".git", "__pycache__", "venv", ".venv", "node_modules"} for part in py_path.parts):
                continue
            count += 1
            if count > max_files:
                break

            imported = cls.extract_imported_modules_from_file(str(py_path))
            if any(target_module_fragment in mod for mod in imported):
                rel = str(py_path.relative_to(root))
                dependents.append(rel)

        return dependents

    @staticmethod
    def suggest_related_tests(affected_files: List[str], test_root: str = "tests") -> List[str]:
        """Suggest test file paths corresponding to modified or affected source files."""
        suggestions: List[str] = []
        for fpath in affected_files:
            stem = Path(fpath).stem
            if stem.startswith("test_"):
                suggestions.append(fpath)
            else:
                suggestions.append(f"{test_root}/test_{stem}.py")
        return list(dict.fromkeys(suggestions))

    @classmethod
    def analyze_impact(
        cls,
        modified_files: List[str],
        repo_dir: Optional[str] = None,
        extra_dependents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Compute affected modules, suggested tests, and overall regression risk level."""
        affected: Set[str] = set(extra_dependents or [])

        for mfile in modified_files:
            module_key = Path(mfile).with_suffix("").as_posix().replace("/", ".")
            if repo_dir:
                rev = cls.find_reverse_dependents(repo_dir, module_key)
                affected.update(rev)

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
            "suggested_tests": cls.suggest_related_tests(modified_files + affected_list),
        }