import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class DependencyManagerValidator:
    """Validator for Python and Node dependency manifests, lockfiles, version pinning, and conflicts."""

    REQ_LINE_RE = re.compile(
        r"^\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?\s*(==|===|!=|<=|>=|<|>|~=|@)?\s*([^\s;#]*)"
    )

    # VCS prefixes that should not be parsed as package names.
    _VCS_PREFIXES = ("git+", "hg+", "svn+", "bzr+")

    @staticmethod
    def _normalize_package_name(name: str) -> str:
        """Normalize a package name per PEP 503: lowercase and collapse ``-``, ``_``, ``.`` to ``-``."""
        return re.sub(r"[-_.]+", "-", name).lower()

    @classmethod
    def parse_requirements_text(cls, content: str) -> Dict[str, Any]:
        """Parse requirements.txt content into structured entries, comments, and directives."""
        packages: List[Dict[str, Any]] = []
        directives: List[str] = []
        invalid_lines: List[Dict[str, Any]] = []
        vcs_references: List[Dict[str, Any]] = []

        for lineno, raw_line in enumerate(content.splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("-r ") or stripped.startswith("--requirement"):
                directives.append(stripped)
                continue
            if stripped.startswith("-c ") or stripped.startswith("--constraint"):
                directives.append(stripped)
                continue
            if stripped.startswith("--") or stripped.startswith("-e "):
                directives.append(stripped)
                continue

            # Bug 4 fix: skip VCS references so they aren't parsed as package names.
            if any(stripped.startswith(prefix) for prefix in cls._VCS_PREFIXES):
                vcs_references.append({"line": lineno, "raw": stripped})
                continue

            match = cls.REQ_LINE_RE.match(stripped)
            if not match:
                invalid_lines.append({"line": lineno, "raw": stripped})
                continue

            pkg_name, operator, version = match.groups()
            packages.append({
                "name": pkg_name.lower(),
                "normalized_name": cls._normalize_package_name(pkg_name),
                "raw_name": pkg_name,
                "operator": operator or "",
                "version": version or "",
                "line": lineno,
                "raw": stripped,
            })

        return {
            "packages": packages,
            "directives": directives,
            "invalid_lines": invalid_lines,
            "vcs_references": vcs_references,
        }

    @classmethod
    def check_version_pinning(cls, requirements_content: str) -> Dict[str, Any]:
        """Verify whether packages in requirements content use exact pinning (==) or explicit constraints."""
        parsed = cls.parse_requirements_text(requirements_content)
        unpinned: List[Dict[str, Any]] = []
        ranged: List[Dict[str, Any]] = []
        exact_pinned: List[str] = []

        for pkg in parsed["packages"]:
            op = pkg["operator"]
            if op in ("==", "==="):
                exact_pinned.append(pkg["normalized_name"])
            elif op in (">=", "<=", ">", "<", "~="):
                ranged.append(pkg)
            else:
                unpinned.append(pkg)

        return {
            "is_valid_lock": len(unpinned) == 0 and len(parsed["invalid_lines"]) == 0,
            "exact_pinned_count": len(exact_pinned),
            "ranged_packages": ranged,
            "unpinned_packages": unpinned,
            "invalid_lines": parsed["invalid_lines"],
        }

    @classmethod
    def detect_duplicate_or_conflicting_specs(cls, requirements_content: str) -> Dict[str, Any]:
        """Detect duplicate package declarations or potentially conflicting version operators."""
        parsed = cls.parse_requirements_text(requirements_content)
        by_name: Dict[str, List[Dict[str, Any]]] = {}

        for pkg in parsed["packages"]:
            by_name.setdefault(pkg["normalized_name"], []).append(pkg)

        duplicates: Dict[str, List[Dict[str, Any]]] = {
            name: entries for name, entries in by_name.items() if len(entries) > 1
        }
        conflicts: List[Dict[str, Any]] = []

        for name, entries in duplicates.items():
            exact_versions = {
                e["version"] for e in entries if e["operator"] in ("==", "===") and e["version"]
            }
            # Bug 6 fix: also detect mixed exact-pin + range operators.
            range_operators = {
                e["operator"] for e in entries if e["operator"] in (">=", ">", "<=", "<", "~=", "!=")
            }

            if len(exact_versions) > 1:
                conflicts.append({
                    "package": name,
                    "reason": "Multiple exact versions specified",
                    "versions": sorted(exact_versions),
                })
            elif exact_versions and range_operators:
                conflicts.append({
                    "package": name,
                    "reason": "Exact pin mixed with range constraint",
                    "exact_versions": sorted(exact_versions),
                    "range_operators": sorted(range_operators),
                })

        return {
            "has_duplicates": len(duplicates) > 0,
            "has_conflicts": len(conflicts) > 0,
            "duplicates": duplicates,
            "conflicts": conflicts,
        }

    @staticmethod
    def validate_package_json(package_json_content: str) -> Dict[str, Any]:
        """Validate package.json structure and check for unpinned or wildcard versions."""
        try:
            data = json.loads(package_json_content)
        except json.JSONDecodeError as exc:
            return {"valid": False, "error": f"Invalid JSON: {exc}", "wildcards": [], "ranged": []}

        # Regex to detect version segments containing 'x' or 'X' (e.g. "1.x", "1.2.X").
        _x_range_re = re.compile(r"(?:^|\.)(?:x|X)(?:\.|$)")

        wildcards: List[Dict[str, str]] = []
        ranged: List[Dict[str, str]] = []
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            deps = data.get(section, {})
            if isinstance(deps, dict):
                for pkg, ver in deps.items():
                    ver_str = str(ver).strip()
                    if ver_str in ("*", "latest", ""):
                        wildcards.append({"section": section, "package": pkg, "version": ver_str})
                    elif (
                        ver_str.startswith(("^", "~", ">=", ">", "<", "<="))
                        or _x_range_re.search(ver_str)
                    ):
                        ranged.append({"section": section, "package": pkg, "version": ver_str})

        return {
            "valid": len(wildcards) == 0 and len(ranged) == 0,
            "name": data.get("name"),
            "wildcards": wildcards,
            "ranged": ranged,
        }

    @staticmethod
    def check_lockfile_presence(project_dir: str) -> Dict[str, Any]:
        """Check whether lockfiles exist alongside manifest files in a project directory."""
        root = Path(project_dir)
        if not root.exists():
            return {"exists": False, "error": f"Directory not found: {project_dir}"}

        has_pyproject = (root / "pyproject.toml").exists()
        has_poetry_lock = (root / "poetry.lock").exists()
        has_uv_lock = (root / "uv.lock").exists()
        has_requirements = (root / "requirements.txt").exists()
        has_requirements_lock = (root / "requirements.lock").exists()
        has_pipfile = (root / "Pipfile").exists()
        has_pipfile_lock = (root / "Pipfile.lock").exists()
        has_pdm_lock = (root / "pdm.lock").exists()

        has_package_json = (root / "package.json").exists()
        has_package_lock = (root / "package-lock.json").exists()
        has_pnpm_lock = (root / "pnpm-lock.yaml").exists()
        has_yarn_lock = (root / "yarn.lock").exists()
        has_bun_lock = (root / "bun.lockb").exists()

        warnings: List[str] = []
        if has_pyproject and not (has_poetry_lock or has_uv_lock or has_pdm_lock):
            warnings.append("pyproject.toml found without poetry.lock, uv.lock, or pdm.lock.")
        if has_requirements and not has_requirements_lock:
            warnings.append("requirements.txt found without requirements.lock.")
        if has_pipfile and not has_pipfile_lock:
            warnings.append("Pipfile found without Pipfile.lock.")
        if has_package_json and not (has_package_lock or has_pnpm_lock or has_yarn_lock or has_bun_lock):
            warnings.append("package.json found without a Node lockfile.")

        return {
            "exists": True,
            "python": {
                "pyproject_toml": has_pyproject,
                "requirements_txt": has_requirements,
                "requirements_lock": has_requirements_lock,
                "poetry_lock": has_poetry_lock,
                "uv_lock": has_uv_lock,
                "pipfile": has_pipfile,
                "pipfile_lock": has_pipfile_lock,
                "pdm_lock": has_pdm_lock,
            },
            "node": {
                "package_json": has_package_json,
                "package_lock_json": has_package_lock,
                "pnpm_lock_yaml": has_pnpm_lock,
                "yarn_lock": has_yarn_lock,
                "bun_lockb": has_bun_lock,
            },
            "warnings": warnings,
        }

    @staticmethod
    def run_pip_check(timeout_seconds: int = 30) -> Dict[str, Any]:
        """Run 'pip check' in the active Python environment to detect broken dependencies."""
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "check"],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return {
                "available": True,
                "healthy": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
        except FileNotFoundError:
            return {"available": False, "healthy": None, "error": "pip not found in PATH."}
        except subprocess.TimeoutExpired:
            return {"available": True, "healthy": False, "error": "pip check timed out."}

    @classmethod
    def validate_requirements_full(
        cls,
        requirements_content: str,
        project_dir: Optional[str] = None,
        run_env_check: bool = False,
    ) -> Dict[str, Any]:
        """Unified validation entry point for requirements pinning, duplicates, lockfiles, and env."""
        pin_res = cls.check_version_pinning(requirements_content)
        dup_res = cls.detect_duplicate_or_conflicting_specs(requirements_content)
        lock_res = cls.check_lockfile_presence(project_dir) if project_dir else None
        env_res = cls.run_pip_check() if run_env_check else None

        is_valid = pin_res["is_valid_lock"] and not dup_res["has_conflicts"]
        if env_res is not None and env_res.get("healthy") is False:
            is_valid = False

        return {
            "valid": is_valid,
            "pinning": pin_res,
            "duplicates_and_conflicts": dup_res,
            "lockfiles": lock_res,
            "environment": env_res,
        }
