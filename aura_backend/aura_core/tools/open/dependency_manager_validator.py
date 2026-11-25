import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from ._input import MAX_TEXT_BYTES, SourceReadError, read_source_bytes, read_source_file
from ._ops_common import (
    DEFAULT_READ_BUDGET_S,
    BudgetExceeded,
    PathRefused,
    clamp_budget,
    confine,
    run_group,
    run_with_budget,
)
from ._process import scrubbed_env

_DIRECT_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def _coerce(content: Any, budget: float = DEFAULT_READ_BUDGET_S) -> Tuple[str, Optional[Dict[str, Any]], List[str], Optional[str]]:
    """``content`` as text, plus coverage info, warnings and an error message.

    ``str`` is used as is. ``bytes`` (a file's contents) go through the shared reader, so a PDF is
    read in full -- every page -- and a binary file is refused instead of being decoded as text.
    """
    if isinstance(content, str):
        if len(content) > MAX_TEXT_BYTES:
            return "", None, [], f"Input is too large to check safely ({len(content)} characters)."
        return content, None, [], None
    if isinstance(content, (bytes, bytearray)):
        try:
            data = bytes(content)
            # A long PDF takes a while to extract: a generous, bounded budget (default 300 s, at most
            # 600 s). Running out is an error -- the text is never cut short to fit.
            read = run_with_budget(lambda: read_source_bytes(data, name="<content>"), clamp_budget(budget),
                                   "Reading the document")
        except (SourceReadError, BudgetExceeded) as exc:
            return "", None, [], str(exc)
        if len(read.text) > MAX_TEXT_BYTES:
            return "", None, [], "Input is too large to check safely."
        return read.text, read.describe(), read.warnings(), None
    return "", None, [], f"Expected text or file bytes, got {type(content).__name__}."


def _attach(result: Dict[str, Any], info: Optional[Dict[str, Any]], warnings: List[str]) -> Dict[str, Any]:
    if info is not None:
        result["input"] = info
    if warnings:
        result["warnings"] = list(result.get("warnings", [])) + warnings
    return result


class DependencyManagerValidator:
    """Validator for Python and Node dependency manifests, lockfiles, version pinning, and conflicts.

    Text methods accept ``str`` or file ``bytes``; a PDF's bytes are read in full (all pages) through
    the shared reader and its text is checked like any other. Pages that are scans cannot be read:
    they are reported under ``warnings`` and a document with unread pages is never reported as a
    valid lock. A result is never "valid" because nothing was found: an empty requirements list is
    not a pinned lockfile.
    """

    REQ_LINE_RE = re.compile(
        r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)(?:\[[^\]]+\])?\s*(==|===|!=|<=|>=|<|>|~=|@)?\s*([^\s;#]*)"
    )

    _PEP508_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._\-]*[A-Za-z0-9])?$")

    # VCS prefixes that should not be parsed as package names.
    _VCS_PREFIXES = ("git+", "hg+", "svn+", "bzr+")

    @staticmethod
    def _normalize_package_name(name: str) -> str:
        """Normalize a package name per PEP 503: lowercase and collapse ``-``, ``_``, ``.`` to ``-``."""
        return re.sub(r"[-_.]+", "-", name).lower()

    @classmethod
    def _logical_lines(cls, text: str) -> List[Tuple[int, str]]:
        """Lines with backslash continuations joined (``pkg==1 \\`` + ``--hash=...``)."""
        out: List[Tuple[int, str]] = []
        buf = ""
        start = 0
        for lineno, raw in enumerate(text.splitlines(), start=1):
            if not buf:
                start = lineno
            if raw.rstrip().endswith("\\"):
                buf += raw.rstrip()[:-1] + " "
                continue
            out.append((start, (buf + raw).strip()))
            buf = ""
        if buf:
            out.append((start, buf.strip()))
        return out

    @classmethod
    def _parse(cls, content: str) -> Dict[str, Any]:
        packages: List[Dict[str, Any]] = []
        directives: List[str] = []
        invalid_lines: List[Dict[str, Any]] = []
        vcs_references: List[Dict[str, Any]] = []

        for lineno, stripped in cls._logical_lines(content):
            if not stripped or stripped.startswith("#"):
                continue
            # Every option line (-r, -c, -e, -i, -f, --index-url, --hash, ...) is a directive. The
            # old code matched "-i https://..." as a package named "-i".
            if stripped.startswith("-"):
                directives.append(stripped)
                continue

            # Skip VCS / URL / path references so they aren't parsed as package names.
            if (
                any(stripped.startswith(prefix) for prefix in cls._VCS_PREFIXES)
                or _DIRECT_REF_RE.match(stripped)
                or stripped.startswith((".", "/", "~"))
            ):
                vcs_references.append({"line": lineno, "raw": stripped})
                continue

            match = cls.REQ_LINE_RE.match(stripped)
            if not match:
                invalid_lines.append({"line": lineno, "raw": stripped})
                continue

            pkg_name, operator, version = match.groups()
            # PEP 508: a name ends in a letter or digit, and after it comes a version operator, an
            # environment marker or nothing. "badline!!" is neither a package nor "version !!".
            if not cls._PEP508_NAME_RE.match(pkg_name) or (
                not operator and version and not version.startswith("(")
            ):
                invalid_lines.append({"line": lineno, "raw": stripped, "reason": "not a valid requirement (PEP 508)"})
                continue
            if operator and not version:
                # "requests==" names no version: it is not pinned, and pip rejects it.
                invalid_lines.append({"line": lineno, "raw": stripped, "reason": "operator without a version"})
                continue
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
    def parse_requirements_text(
        cls, content: Any, *, read_timeout_seconds: float = DEFAULT_READ_BUDGET_S
    ) -> Dict[str, Any]:
        """Parse requirements.txt content (text or file bytes) into structured entries and directives."""
        text, info, warns, err = _coerce(content, read_timeout_seconds)
        if err:
            return {"packages": [], "directives": [], "invalid_lines": [], "vcs_references": [], "error": err}
        return _attach(cls._parse(text), info, warns)

    @staticmethod
    def _unexamined_includes(directives: List[str]) -> List[str]:
        return [d for d in directives if re.match(r"^(-r|-c|--requirement|--constraint)\b", d)]

    @classmethod
    def _pinning(cls, text: str) -> Dict[str, Any]:
        parsed = cls._parse(text)
        unpinned: List[Dict[str, Any]] = []
        ranged: List[Dict[str, Any]] = []
        exact_pinned: List[str] = []

        for pkg in parsed["packages"]:
            op = pkg["operator"]
            if op in ("==", "===") and "*" not in pkg["version"]:
                exact_pinned.append(pkg["normalized_name"])
            elif op in ("==", "===", ">=", "<=", ">", "<", "~="):
                ranged.append(pkg)  # "==1.*" is a prefix match, not an exact pin
            else:
                unpinned.append(pkg)

        warnings: List[str] = []
        includes = cls._unexamined_includes(parsed["directives"])
        if includes:
            warnings.append(
                "Included files were not examined (" + ", ".join(includes[:5]) + "); "
                "the result covers only the text given."
            )
        nothing = not parsed["packages"] and not parsed["vcs_references"]
        result: Dict[str, Any] = {
            "is_valid_lock": not nothing and len(unpinned) == 0 and len(parsed["invalid_lines"]) == 0,
            "exact_pinned_count": len(exact_pinned),
            "ranged_packages": ranged,
            "unpinned_packages": unpinned,
            "invalid_lines": parsed["invalid_lines"],
            "vcs_references": parsed["vcs_references"],
            "includes_unexamined": includes,
        }
        if nothing:
            result["error"] = "No requirement lines were found, so nothing was verified."
        if warnings:
            result["warnings"] = warnings
        return result

    @classmethod
    def check_version_pinning(
        cls, requirements_content: Any, *, read_timeout_seconds: float = DEFAULT_READ_BUDGET_S
    ) -> Dict[str, Any]:
        """Verify whether packages in requirements content use exact pinning (==) or explicit constraints."""
        text, info, warns, err = _coerce(requirements_content, read_timeout_seconds)
        if err:
            return {"is_valid_lock": False, "exact_pinned_count": 0, "ranged_packages": [],
                    "unpinned_packages": [], "invalid_lines": [], "error": err}
        result = cls._pinning(text)
        if info is not None and not info.get("complete", True):
            result["is_valid_lock"] = False
        return _attach(result, info, warns)

    @classmethod
    def _duplicates(cls, text: str) -> Dict[str, Any]:
        parsed = cls._parse(text)
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
            # Also detect mixed exact-pin + range operators.
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

    @classmethod
    def detect_duplicate_or_conflicting_specs(
        cls, requirements_content: Any, *, read_timeout_seconds: float = DEFAULT_READ_BUDGET_S
    ) -> Dict[str, Any]:
        """Detect duplicate package declarations or potentially conflicting version operators.

        If the input could not be read, ``has_duplicates`` and ``has_conflicts`` are ``None`` (unknown)
        and ``error`` says why -- not ``False``.
        """
        text, info, warns, err = _coerce(requirements_content, read_timeout_seconds)
        if err:
            return {"has_duplicates": None, "has_conflicts": None, "duplicates": {}, "conflicts": [], "error": err}
        return _attach(cls._duplicates(text), info, warns)

    @staticmethod
    def validate_package_json(
        package_json_content: Any, *, read_timeout_seconds: float = DEFAULT_READ_BUDGET_S
    ) -> Dict[str, Any]:
        """Validate package.json structure (text or file bytes) and flag unpinned or wildcard versions."""
        text, info, warns, err = _coerce(package_json_content, read_timeout_seconds)
        if err:
            return {"valid": False, "error": err, "wildcards": [], "ranged": []}
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, RecursionError) as exc:
            return _attach({"valid": False, "error": f"Invalid JSON: {exc}", "wildcards": [], "ranged": []},
                           info, warns)
        if not isinstance(data, dict):
            return _attach({"valid": False, "error": "package.json must be a JSON object.",
                            "wildcards": [], "ranged": []}, info, warns)

        # Version segments containing 'x' or 'X' (e.g. "1.x", "1.2.X").
        _x_range_re = re.compile(r"(?:^|\.)(?:x|X)(?:\.|$)")

        wildcards: List[Dict[str, str]] = []
        ranged: List[Dict[str, str]] = []
        checked = 0
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            deps = data.get(section, {})
            if isinstance(deps, dict):
                for pkg, ver in deps.items():
                    checked += 1
                    ver_str = str(ver).strip()
                    if ver_str in ("*", "latest", ""):
                        wildcards.append({"section": section, "package": pkg, "version": ver_str})
                    elif (
                        ver_str.startswith(("^", "~", ">=", ">", "<", "<="))
                        or "||" in ver_str
                        or " - " in ver_str
                        or _x_range_re.search(ver_str)
                    ):
                        ranged.append({"section": section, "package": pkg, "version": ver_str})

        name = data.get("name")
        return _attach({
            "valid": len(wildcards) == 0 and len(ranged) == 0,
            "name": name if isinstance(name, str) else None,
            "dependencies_checked": checked,
            "wildcards": wildcards,
            "ranged": ranged,
        }, info, warns)

    @staticmethod
    def check_lockfile_presence(project_dir: str, *, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """Check whether lockfiles exist alongside manifest files in a project directory.

        Only file *presence* is inspected; no file is read, except that a regular file given instead
        of a directory is identified (a PDF is read in full to say so).
        """
        try:
            root = confine(project_dir, base_dir)
        except PathRefused as exc:
            return {"exists": False, "error": str(exc)}
        if not root.is_dir():
            detail = ""
            result: Dict[str, Any] = {}
            try:
                read = run_with_budget(lambda: read_source_file(root), DEFAULT_READ_BUDGET_S, "Reading the file")
                if read.kind == "pdf":
                    detail = f" It is a PDF ({read.pages} page(s), read in full), not a project."
                    result = {"input": read.describe(), "warnings": read.warnings()}
            except (SourceReadError, BudgetExceeded):
                pass
            return {"exists": False, "error": f"Not a directory: {project_dir}.{detail}", **result}

        def has(name: str) -> bool:
            return (root / name).is_file()

        has_pyproject = has("pyproject.toml")
        has_poetry_lock = has("poetry.lock")
        has_uv_lock = has("uv.lock")
        has_requirements = has("requirements.txt")
        has_requirements_lock = has("requirements.lock")
        has_pipfile = has("Pipfile")
        has_pipfile_lock = has("Pipfile.lock")
        has_pdm_lock = has("pdm.lock")

        has_package_json = has("package.json")
        has_package_lock = has("package-lock.json")
        has_pnpm_lock = has("pnpm-lock.yaml")
        has_yarn_lock = has("yarn.lock")
        has_bun_lock = has("bun.lockb")

        warnings: List[str] = []
        if not (has_pyproject or has_requirements or has_pipfile or has_package_json):
            warnings.append("No dependency manifest (pyproject.toml, requirements.txt, Pipfile, package.json) found.")
        if has_pyproject and not (has_poetry_lock or has_uv_lock or has_pdm_lock):
            warnings.append("pyproject.toml found without poetry.lock, uv.lock, or pdm.lock.")
        if has_requirements and not has_requirements_lock:
            warnings.append("requirements.txt found without requirements.lock.")
        if has_pipfile and not has_pipfile_lock:
            warnings.append("Pipfile found without Pipfile.lock.")
        if has_package_json and not (has_package_lock or has_pnpm_lock or has_yarn_lock or has_bun_lock):
            warnings.append("package.json found without a Node lockfile.")
        for lock in ("poetry.lock", "uv.lock", "pdm.lock", "Pipfile.lock", "requirements.lock",
                     "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"):
            try:
                if has(lock) and (root / lock).stat().st_size == 0:
                    warnings.append(f"{lock} exists but is empty.")
            except OSError:
                pass

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
        """Run 'pip check' in the *gateway's own* Python environment to detect broken dependencies.

        This inspects the interpreter running this code, not a project's environment. The child gets
        a scrubbed environment and its output is bounded.
        """
        try:
            seconds = max(1.0, min(float(timeout_seconds), 300.0))
        except (TypeError, ValueError):
            seconds = 30.0
        try:
            res = run_group([sys.executable, "-m", "pip", "check"], cwd=None, timeout_s=seconds,
                            env=scrubbed_env(), max_output=20_000)
        except FileNotFoundError:
            return {"available": False, "healthy": None, "error": "pip not found in PATH."}
        except OSError as exc:
            return {"available": False, "healthy": None, "error": f"{type(exc).__name__}: {exc}"}
        if res.timed_out:
            return {"available": True, "healthy": False, "error": "pip check timed out."}
        if "No module named pip" in res.stderr:
            return {"available": False, "healthy": None, "error": "pip is not installed for this interpreter."}
        return {
            "available": True,
            "healthy": res.returncode == 0,
            "exit_code": res.returncode,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
        }

    @classmethod
    def validate_requirements_full(
        cls,
        requirements_content: Any,
        project_dir: Optional[str] = None,
        run_env_check: bool = False,
        *,
        base_dir: Optional[str] = None,
        read_timeout_seconds: float = DEFAULT_READ_BUDGET_S,
    ) -> Dict[str, Any]:
        """Unified validation entry point for requirements pinning, duplicates, lockfiles, and env."""
        text, info, warns, err = _coerce(requirements_content, read_timeout_seconds)
        if err:
            return {"valid": False, "error": err, "pinning": None, "duplicates_and_conflicts": None,
                    "lockfiles": None, "environment": None}
        pin_res = cls._pinning(text)
        dup_res = cls._duplicates(text)
        lock_res = cls.check_lockfile_presence(project_dir, base_dir=base_dir) if project_dir else None
        env_res = cls.run_pip_check() if run_env_check else None

        is_valid = pin_res["is_valid_lock"] and not dup_res["has_conflicts"]
        if info is not None and not info.get("complete", True):
            is_valid = False
        if env_res is not None and env_res.get("healthy") is False:
            is_valid = False

        result: Dict[str, Any] = {
            "valid": is_valid,
            "pinning": pin_res,
            "duplicates_and_conflicts": dup_res,
            "lockfiles": lock_res,
            "environment": env_res,
        }
        all_warnings = list(pin_res.get("warnings", []))
        if lock_res is not None:
            if not lock_res.get("exists"):
                all_warnings.append(f"Lockfile check skipped: {lock_res.get('error')}")
            all_warnings.extend(lock_res.get("warnings", []))
        if all_warnings:
            result["warnings"] = all_warnings
        if pin_res.get("error"):
            result["error"] = pin_res["error"]
        return _attach(result, info, warns)
