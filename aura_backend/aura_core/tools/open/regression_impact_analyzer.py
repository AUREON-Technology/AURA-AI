import ast
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ._input import SourceReadError, read_source_file
from ._process import scrubbed_env
from .scope_and_diff_guard import ScopeAndDiffGuard

# Allowed characters in a git ref to prevent argument injection.
_SAFE_REF_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "._-/~^@{}"
)


#: Time budgets (seconds). Generous because a large repository or a slow disk is normal; bounded because
#: nothing here may run unattended forever. Overridable per call, never above the hard maximum.
DEFAULT_TIME_BUDGET_S = 180.0
MAX_TIME_BUDGET_S = 600.0


def _budget(value: Any, default: float = DEFAULT_TIME_BUDGET_S) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v <= 0:
        return default
    return min(v, MAX_TIME_BUDGET_S)


class RegressionImpactAnalyzer:
    """Analyzer for identifying changed modules, reverse import dependents, and regression risk.

    Reverse-dependent detection is a *textual import match*, not a call graph: it finds Python files
    that import a module whose dotted name ends in the changed file's path. It cannot see dynamic
    imports, non-Python consumers, or two modules that share a name in different packages. Results
    say so (``heuristic``), and files it could not parse are listed rather than skipped silently.
    """

    @staticmethod
    def parse_changed_files_from_diff(diff_text: Any) -> List[Dict[str, Any]]:
        """Extract modified file paths from a unified git diff string.

        Returns a list of dicts with ``"path"`` and ``"deleted"`` keys so the caller knows whether
        the file was removed. Deleted files, pure renames (both the old and the new path) and
        binary changes are included. Raises ``DiffInputError`` for non-text input (a PDF is read in
        full and then refused: it is not a diff).
        """
        changed: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, bool]] = set()

        def add(path: Optional[str], deleted: bool) -> None:
            if path and path != "unknown" and (path, deleted) not in seen:
                seen.add((path, deleted))
                changed.append({"path": path, "deleted": deleted})

        for f in ScopeAndDiffGuard.parse_diff_files(diff_text):
            if f["is_deleted"]:
                add(f["old_path"] or f["path"], True)
            else:
                add(f["path"], False)
                if f["is_rename"] and f["old_path"] and f["old_path"] != f["path"]:
                    add(f["old_path"], True)
        return changed

    @staticmethod
    def get_git_changed_files(
        repo_dir: str, base_ref: str = "HEAD", timeout_s: float = DEFAULT_TIME_BUDGET_S
    ) -> Dict[str, Any]:
        """Get changed files in a git repository relative to base_ref.

        ``timeout_s`` defaults to 180 s and is capped at 600 s."""
        # base_ref is validated (no leading ``-``, safe characters only) so it cannot be read as an
        # option. The ``--`` goes *after* it: before it, git treats the ref as a pathspec and the
        # diff of the working tree against it is always empty.
        if not isinstance(base_ref, str) or not base_ref or base_ref.startswith("-"):
            return {"success": False, "error": f"Invalid base_ref: {base_ref!r}", "files": []}
        if not all(ch in _SAFE_REF_CHARS for ch in base_ref):
            return {"success": False, "error": f"Invalid characters in base_ref: {base_ref!r}", "files": []}
        try:
            if not repo_dir or not Path(repo_dir).is_dir():
                return {"success": False, "error": f"Not a directory: {repo_dir!r}", "files": []}
        except (OSError, ValueError, TypeError):
            return {"success": False, "error": f"Invalid repo_dir: {repo_dir!r}", "files": []}

        try:
            # ``-z``: NUL-separated, never quoted -- without it git prints non-ASCII names (Arabic,
            # CJK, emoji) as escaped octal and the paths come back unusable.
            proc = subprocess.run(
                ["git", "diff", "--name-only", "-z", base_ref, "--"],
                cwd=repo_dir,
                capture_output=True,
                timeout=_budget(timeout_s),
                env=scrubbed_env(),
            )
        except FileNotFoundError:
            return {"success": False, "error": "git executable not found.", "files": []}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"git diff timed out after {_budget(timeout_s):.0f}s.", "files": []}
        except OSError as exc:
            return {"success": False, "error": f"git could not be started: {exc.strerror or type(exc).__name__}", "files": []}

        if proc.returncode != 0:
            return {"success": False, "error": proc.stderr.decode("utf-8", "replace").strip(), "files": []}
        files = [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p.strip()]
        return {"success": True, "base_ref": base_ref, "files": files}

    @staticmethod
    def inspect_imports(py_file: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """Like :meth:`extract_imported_modules_from_file` but says *why* when nothing came back.

        ``{"modules": [...], "parsed": bool, "error": str | None}``. A file that is missing, too
        large, binary, a PDF, or not valid Python is ``parsed: False`` with the reason.
        """
        try:
            read = read_source_file(py_file, base_dir=base_dir)
        except SourceReadError as exc:
            return {"modules": [], "parsed": False, "error": str(exc)}
        except (OSError, TypeError, ValueError) as exc:
            return {"modules": [], "parsed": False, "error": f"Invalid path: {type(exc).__name__}"}
        if read.kind == "pdf":
            return {
                "modules": [],
                "parsed": False,
                "error": f"{py_file} is a PDF ({read.pages} page(s), read in full), not Python source.",
            }
        try:
            tree = ast.parse(read.text)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            return {"modules": [], "parsed": False, "error": f"Not parseable as Python: {type(exc).__name__}"}

        modules: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                dots = "." * (node.level or 0)
                base = f"{dots}{node.module}" if node.module else dots
                if node.module or node.level:
                    modules.add(base)
                # ``from pkg import router`` imports the module ``pkg.router``: record it, or a
                # dependent that imports the changed file by name is missed.
                for alias in node.names:
                    if alias.name != "*":
                        modules.add(f"{base}.{alias.name}" if node.module else f"{dots}{alias.name}")
        return {"modules": sorted(modules), "parsed": True, "error": None}

    @classmethod
    def extract_imported_modules_from_file(cls, py_file: str, base_dir: Optional[str] = None) -> List[str]:
        """Parse a Python file and return all imported module paths.

        Relative imports are recorded with leading dots (``..llm``). Returns ``[]`` for a file that
        cannot be read or parsed; use :meth:`inspect_imports` to learn why.
        """
        return cls.inspect_imports(py_file, base_dir=base_dir)["modules"]

    @classmethod
    def _module_fragments(cls, file_path: str) -> List[str]:
        """Generate multiple possible module dotted-path fragments for *file_path*.

        For ``aura_backend/aura_core/llm/router.py`` this returns::

            ['aura_backend.aura_core.llm.router',
             'aura_core.llm.router',
             'llm.router',
             'router']

        An ``__init__.py`` stands for its package, so ``pkg/__init__.py`` yields ``pkg``.
        """
        parts = [p for p in Path(file_path).with_suffix("").as_posix().split("/") if p not in ("", ".")]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return [".".join(parts[start:]) for start in range(len(parts))]

    @staticmethod
    def _imports_fragment(imported: List[str], fragments: List[str]) -> bool:
        """True when an import names one of the fragments *on a dotted-component boundary*.

        A bare substring test made ``router`` match ``router_utils`` and ``a`` match nearly every
        import in the repository.
        """
        for mod in imported:
            m = mod.lstrip(".")
            if not m:
                continue
            padded = f".{m}."
            for frag in fragments:
                if frag and f".{frag}." in padded:
                    return True
        return False

    @classmethod
    def _scan_repo(cls, repo_dir: str, max_files: int, time_budget_s: float = DEFAULT_TIME_BUDGET_S) -> Optional[Dict[str, Any]]:
        """Read every ``.py`` file's imports once. ``None`` when ``repo_dir`` is not a directory."""
        root = Path(repo_dir)
        if not root.is_dir():
            return None
        try:
            root_resolved = root.resolve()
        except (OSError, RuntimeError):
            return None
        index: Dict[str, List[str]] = {}
        unparseable: List[str] = []
        count = 0
        truncated = False
        deadline = time.monotonic() + _budget(time_budget_s)
        for py_path in root.rglob("*.py"):
            if time.monotonic() > deadline:
                truncated = True  # budget exhausted: reported as truncated, never as a complete scan
                break
            if any(part in {".git", "__pycache__", "venv", ".venv", "node_modules"} for part in py_path.parts):
                continue
            count += 1
            if count > max_files:
                count -= 1
                truncated = True
                break
            rel = str(py_path.relative_to(root))
            res = cls.inspect_imports(str(py_path), base_dir=str(root_resolved))
            if res["parsed"]:
                index[rel] = res["modules"]
            else:
                unparseable.append(rel)
        return {"index": index, "unparseable": unparseable, "files_scanned": count, "truncated": truncated}

    @classmethod
    def find_reverse_dependents(
        cls,
        repo_dir: str,
        target_module_fragments: List[str],
        max_files: int = 10000,
        time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    ) -> Dict[str, Any]:
        """Scan Python files in repo_dir to find modules that import any of *target_module_fragments*.

        Returns a dict with ``dependents``, ``truncated``, ``files_scanned`` and ``unparseable``
        (files that could not be read as Python, so their imports are unknown) keys.
        """
        scan = cls._scan_repo(repo_dir, max_files, time_budget_s)
        if scan is None:
            return {"dependents": [], "truncated": False, "files_scanned": 0, "unparseable": [],
                    "error": f"Not a directory: {repo_dir!r}"}
        dependents = [
            rel for rel, mods in scan["index"].items() if cls._imports_fragment(mods, list(target_module_fragments))
        ]
        return {
            "dependents": dependents,
            "truncated": scan["truncated"],
            "files_scanned": scan["files_scanned"],
            "unparseable": scan["unparseable"],
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
                try:
                    entry["exists"] = Path(repo_dir, test_path).exists()
                except (OSError, ValueError):
                    entry["exists"] = False
            suggestions.append(entry)
        return suggestions

    @classmethod
    def analyze_impact(
        cls,
        modified_files: List[str],
        repo_dir: Optional[str] = None,
        extra_dependents: Optional[List[str]] = None,
        time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    ) -> Dict[str, Any]:
        """Compute affected modules, suggested tests, and overall regression risk level.

        ``dependents_scanned`` says whether the repository was actually searched for importers.
        Without ``repo_dir`` the risk level reflects only how many files changed and what the caller
        listed in ``extra_dependents``; ``warnings`` says so.
        """
        if isinstance(modified_files, str):
            modified_files = [modified_files]
        if isinstance(extra_dependents, str):
            extra_dependents = [extra_dependents]
        if not isinstance(modified_files, (list, tuple, set)):
            modified_files = []
        if not isinstance(extra_dependents, (list, tuple, set)):
            extra_dependents = []
        modified = list(dict.fromkeys(
            str(f) for f in modified_files if isinstance(f, (str, Path)) and str(f).strip()
        ))
        affected: Set[str] = {str(f) for f in extra_dependents if str(f).strip()}
        warnings: List[str] = []
        total_files_scanned = 0
        any_truncated = False
        unparseable: List[str] = []
        scanned = False

        if repo_dir:
            scan = cls._scan_repo(repo_dir, 10000, time_budget_s)
            if scan is None:
                warnings.append(f"repo_dir is not a directory ({repo_dir!r}); importers were not searched.")
            else:
                scanned = True
                total_files_scanned = scan["files_scanned"]
                any_truncated = scan["truncated"]
                unparseable = scan["unparseable"]
                for mfile in modified:
                    fragments = cls._module_fragments(mfile)
                    affected.update(
                        rel for rel, mods in scan["index"].items() if cls._imports_fragment(mods, fragments)
                    )
                if any_truncated:
                    warnings.append("The repository scan hit its file or time limit; some importers may be missing.")
                if unparseable:
                    warnings.append(
                        f"{len(unparseable)} Python file(s) could not be parsed, so their imports are unknown."
                    )
        else:
            warnings.append(
                "No repository was scanned, so importers of the changed files were not looked for; "
                "risk_level reflects only the changed files and any extra_dependents supplied."
            )
        if not modified:
            warnings.append("No modified files were supplied; there is nothing to assess.")

        affected.difference_update(modified)  # a changed file is not also its own dependent
        affected_list = sorted(affected)
        total_blast_radius = len(modified) + len(affected_list)

        if total_blast_radius > 6:
            risk = "HIGH"
        elif total_blast_radius > 2:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        result: Dict[str, Any] = {
            "modified_files": modified,
            "affected_modules": affected_list,
            "blast_radius_count": total_blast_radius,
            "risk_level": risk,
            "suggested_tests": cls.suggest_related_tests(
                modified + affected_list,
                repo_dir=repo_dir,
            ),
            "scan_truncated": any_truncated,
            "files_scanned": total_files_scanned,
            "dependents_scanned": scanned,
            "unparseable_files": unparseable,
            "heuristic": True,
        }
        if warnings:
            result["warnings"] = warnings
        return result
