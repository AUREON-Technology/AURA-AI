import re
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# SPDX canonical aliases – map common informal names to proper SPDX IDs
# ---------------------------------------------------------------------------
_SPDX_ALIASES: Dict[str, str] = {
    "GPLV2": "GPL-2.0",
    "GPLV3": "GPL-3.0",
    "LGPLV2": "LGPL-2.0",
    "LGPLV3": "LGPL-3.0",
    "AGPLV3": "AGPL-3.0",
    "APACHE2": "Apache-2.0",
    "APACHE-2": "Apache-2.0",
    "MIT-LICENSE": "MIT",
    "BOOST": "BSL-1.0",   # Boost Software License – permissive
}

_UNKNOWN_LABELS = frozenset({"", "UNKNOWN", "NONE", "NOASSERTION", "N/A", "NA", "OTHER"})
_TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9.+\-]*")
#: Licence families this scanner recognises by name. A licence string matching none of them (and
#: not forbidden) is reported as unrecognised, not silently cleared.
_RECOGNISED_RE = re.compile(
    r"\b(?:MIT|BSD|0BSD|APACHE|ISC|ISCL|PSF|PYTHON|MPL|MOZILLA|EPL|ECLIPSE|CDDL|LGPL|ZLIB|UNLICENSE|"
    r"CC0|CC-BY|WTFPL|ARTISTIC|BOOST|BSL-1\.0|HPND|OFL|SIL|PUBLIC\s+DOMAIN|X11|NCSA|"
    r"OPENSSL|ZPL|AFL|BLUEOAK|UPL)\b"
)

# Exact SPDX IDs that are forbidden by default (used by check_license_compatibility only).
# NOTE: BSL-1.0 (Boost) is NOT forbidden; BSL-1.1 / SSPL-1.0 (MariaDB) are.
_DEFAULT_FORBIDDEN_IDS: frozenset = frozenset({
    "AGPL-3.0",
    "AGPL-3.0-ONLY",
    "AGPL-3.0-OR-LATER",
    "GPL-3.0",
    "GPL-3.0-ONLY",
    "GPL-3.0-OR-LATER",
    "GPL-2.0",
    "GPL-2.0-ONLY",
    "GPL-2.0-OR-LATER",
    "SSPL-1.0",
    "BSL-1.1",           # MariaDB Business Source License (restrictive)
    "PROPRIETARY",
    "UNKNOWN",           # Unknown license must fail by default
})


def _normalise_spdx(raw: str) -> str:
    """Return an upper-cased, alias-resolved SPDX identifier."""
    upper = raw.strip().upper()
    return _SPDX_ALIASES.get(upper, upper)


class DependencyLicenseComplianceScanner:
    """Scanner for checking software package licenses against policy allowlists and denylists.

    A licence string is read as a (simplified) SPDX expression: ``OR`` alternatives, ``AND``
    components, ``WITH`` exceptions and parentheses. A package is compliant when at least one
    ``OR`` alternative has no forbidden token, passes the allowlist (when one is given) and is not
    an unknown/blank label. Forbidden tokens match on a *token boundary*, not as a substring, so
    ``GPL-3.0`` does not flag ``LGPL-3.0`` and ``BUSL`` does not flag ``BSL-1.0`` (Boost).

    ADR-036: this token-boundary behaviour was reverted once by a merge (a0024eae) to exact-ID
    matching, which let ``GPL-3.0+`` and ``BUSL-1.1`` through and passed ``""`` and
    ``NOASSERTION``. ``tests/unit/test_open_tools_fixes.py::TestLicenseScanner`` pins it.
    """

    #: Strong-copyleft and source-available families. ``GPL-3`` / ``GPLV3`` are the spellings
    #: package metadata uses instead of the SPDX id; ``BSL-1.0`` is the permissive Boost licence,
    #: so the Business Source License is listed by its own ids only.
    DEFAULT_FORBIDDEN_LICENSES: List[str] = [
        "AGPL",
        "GPL",
        "GPL-3.0",
        "GPL-3",
        "GPLV3",
        "GPL-2.0",
        "GPL-2",
        "GPLV2",
        "SSPL",
        "BUSL",
        "BSL-1.1",
        "PROPRIETARY",
    ]

    PERMISSIVE_LICENSES = {
        "MIT",
        "APACHE-2.0",
        "BSD-2-CLAUSE",
        "BSD-3-CLAUSE",
        "ISC",
        "UNLICENSE",
        "0BSD",
        "PSF-2.0",
        "CC0-1.0",
        "BSL-1.0",   # Boost Software License – explicitly permissive
    }

    KNOWN_PACKAGE_LICENSES: Dict[str, str] = {
        "fastapi": "MIT",
        "pydantic": "MIT",
        "pytest": "MIT",
        "sqlalchemy": "MIT",
        "starlette": "BSD-3-Clause",
        "alembic": "MIT",
        "asyncpg": "Apache-2.0",
        "requests": "Apache-2.0",
        "httpx": "BSD-3-Clause",
        "click": "BSD-3-Clause",
        "rich": "MIT",
        "typer": "MIT",
        "numpy": "BSD-3-Clause",
        "pandas": "BSD-3-Clause",
        "scipy": "BSD-3-Clause",
        "flask": "BSD-3-Clause",
        "django": "BSD-3-Clause",
        "celery": "BSD-3-Clause",
        "redis": "MIT",
        "aiohttp": "Apache-2.0",
        "uvicorn": "BSD-3-Clause",
        "gunicorn": "MIT",
        "black": "MIT",
        "ruff": "MIT",
        "mypy": "MIT",
    }

    @staticmethod
    def _matches_token(token: str, forbidden: str) -> bool:
        if token == forbidden:
            return True
        return token.startswith(forbidden) and token[len(forbidden)] in "-+."

    _NAME_PATTERNS = (
        (re.compile(r"GNU\s+AFFERO\s+GENERAL\s+PUBLIC\s+LICENSE"), "AGPL"),
        (re.compile(r"GNU\s+LESSER\s+GENERAL\s+PUBLIC\s+LICENSE|GNU\s+LIBRARY\s+GENERAL\s+PUBLIC\s+LICENSE"), "LGPL"),
        (re.compile(r"GNU\s+GENERAL\s+PUBLIC\s+LICENSE|GNU\s+GPL"), "GPL"),
    )
    _VERSIONED_RE = re.compile(
        r"\b(AGPL|LGPL|GPL)(?:\s*-?\s*(?:V|VERSION\s*)?(\d+)(?:\.(\d+))?)?(?![A-Z0-9])"
    )
    _OPERATOR_RE = re.compile(r"\(|\)|\bAND\b|\bOR\b|\bWITH\b|[^\s()]+")
    #: Parenthesis nesting deeper than this is not a licence expression; it is treated as unparseable.
    _MAX_DEPTH = 32

    @classmethod
    def _canonical(cls, text: str) -> str:
        """Upper-case ``text`` with long GNU names and ``GPL v3`` / ``GPLv3`` / ``GPL-3`` spellings
        rewritten to the SPDX form (``GPL-3.0``), so they are matched, not missed."""
        upper = " ".join(str(text).upper().split())
        for pattern, short in cls._NAME_PATTERNS:
            upper = pattern.sub(short, upper)

        def version(m: "re.Match[str]") -> str:
            name, major, minor = m.group(1), m.group(2), m.group(3)
            return f"{name}-{major}.{minor or '0'}" if major else name

        return cls._VERSIONED_RE.sub(version, upper)

    @classmethod
    def _alternatives(cls, lic: str) -> List[List[str]]:
        """``"(MIT OR GPL-2.0) AND X"`` -> alternatives, each a list of AND-ed components.

        The expression is parsed with its parentheses (AND binds tighter than OR) and expanded to
        disjunctive normal form, so ``GPL-3.0 AND (MIT OR BSD)`` is ``GPL-3.0 AND MIT`` or
        ``GPL-3.0 AND BSD``, not "BSD". An unbalanced or absurdly nested expression is returned as
        one alternative holding the raw text, which then fails as unknown instead of passing.
        """
        tokens = cls._OPERATOR_RE.findall(str(lic))
        pos = 0

        class _Bad(Exception):
            pass

        def parse_or(depth: int) -> List[List[str]]:
            nonlocal pos
            if depth > cls._MAX_DEPTH:
                raise _Bad
            result = parse_and(depth)
            while pos < len(tokens) and tokens[pos].upper() == "OR":
                pos += 1
                result = result + parse_and(depth)
            return result

        def parse_and(depth: int) -> List[List[str]]:
            nonlocal pos
            result = parse_atom(depth)
            while pos < len(tokens) and tokens[pos].upper() == "AND":
                pos += 1
                right = parse_atom(depth)
                result = [left + r for left in result for r in right]
                if len(result) > 256:
                    raise _Bad
            return result

        def parse_atom(depth: int) -> List[List[str]]:
            nonlocal pos
            if pos < len(tokens) and tokens[pos] == "(":
                pos += 1
                inner = parse_or(depth + 1)
                if pos >= len(tokens) or tokens[pos] != ")":
                    raise _Bad
                pos += 1
                return inner
            words: List[str] = []
            while pos < len(tokens) and tokens[pos] not in ("(", ")") and tokens[pos].upper() not in ("AND", "OR"):
                if tokens[pos].upper() == "WITH":
                    pos += 2  # "WITH <exception id>": the exception does not change which licence it is
                    continue
                words.append(tokens[pos])
                pos += 1
                if pos < len(tokens) and tokens[pos] == "(" and words:
                    # "GNU General Public License v3 (GPLv3)": a parenthetical straight after a
                    # licence name is part of the name, not a sub-expression.
                    depth_open = 0
                    while pos < len(tokens):
                        words.append(tokens[pos])
                        depth_open += (tokens[pos] == "(") - (tokens[pos] == ")")
                        pos += 1
                        if depth_open == 0:
                            break
                    if depth_open != 0:
                        raise _Bad
            return [[" ".join(words)]]

        try:
            alternatives = parse_or(0)
            if pos != len(tokens):
                raise _Bad
        except (_Bad, IndexError):
            return [[str(lic).strip() or "", "UNKNOWN"]]
        return [[c for c in alt if c is not None] or [""] for alt in alternatives]

    @classmethod
    def _evaluate_alternative(
        cls,
        components: List[str],
        forbidden: Set[str],
        allowed_set: Optional[Set[str]],
    ) -> Tuple[List[str], bool, bool]:
        canon = [cls._canonical(c) for c in components]
        tokens = [t for c in canon for t in _TOKEN_RE.findall(c)]
        matched = sorted(f for f in forbidden if any(cls._matches_token(t, f) for t in tokens))
        unknown = False
        for c in canon:
            c_tokens = _TOKEN_RE.findall(c)
            if c.strip() in _UNKNOWN_LABELS or any(t in ("UNKNOWN", "NOASSERTION") for t in c_tokens):
                unknown = True
            elif not any(cls._matches_token(t, f) for t in c_tokens for f in forbidden):
                # Neither forbidden nor a licence family we know: nobody has cleared it.
                if not _RECOGNISED_RE.search(c):
                    unknown = True
        not_allowed = allowed_set is not None and any(c.strip() not in allowed_set for c in canon)
        return matched, not_allowed, unknown

    @classmethod
    def scan_licenses(
        cls,
        packages_and_licenses: List[Dict[str, str]],
        forbidden_tokens: Optional[List[str]] = None,
        allowed_licenses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Scan a list of {'package': name, 'license': spdx_or_label} items.
        Flags forbidden licenses, licenses not present in allowed_licenses if provided, and
        unknown/blank licenses (a licence nobody can name is not a licence anybody cleared).

        Licence expressions are parsed with their parentheses, and long GNU names and
        ``GPL v3`` / ``GPLv3`` spellings are normalised to SPDX ids before matching. An empty
        list, or an entry that is not an object, is not "compliant": nothing was cleared.
        """
        if not isinstance(packages_and_licenses, (list, tuple)) or not packages_and_licenses:
            return {
                "compliant": False,
                "error": "No packages were supplied, so nothing was scanned.",
                "packages_scanned": 0,
                "compliant_count": 0,
                "violations_count": 0,
                "violations": [],
                "compliant_packages": [],
            }

        forbidden_list = (
            forbidden_tokens if forbidden_tokens is not None else cls.DEFAULT_FORBIDDEN_LICENSES
        )
        forbidden = {cls._canonical(x).strip() for x in forbidden_list if str(x).strip()}
        allowed_set = (
            {cls._canonical(x).strip() for x in allowed_licenses} if allowed_licenses is not None else None
        )

        violations: List[Dict[str, Any]] = []
        compliant_packages: List[Dict[str, str]] = []

        for item in packages_and_licenses:
            if not isinstance(item, dict):
                violations.append({
                    "package": "<invalid entry>",
                    "license": str(item)[:200],
                    "normalised_license": "",
                    "matched_forbidden": [],
                    "not_in_allowlist": False,
                    "unknown_license": True,
                })
                continue
            pkg = str(item.get("package", "<unknown>")).strip()
            lic = str(item.get("license", "UNKNOWN")).strip()

            matched_all: Set[str] = set()
            any_not_allowed = False
            any_unknown = False
            ok = False
            for components in cls._alternatives(lic):
                matched, not_allowed, unknown = cls._evaluate_alternative(
                    components, forbidden, allowed_set
                )
                matched_all.update(matched)
                any_not_allowed = any_not_allowed or not_allowed
                any_unknown = any_unknown or unknown
                if not matched and not not_allowed and not unknown:
                    ok = True
                    break

            if ok:
                compliant_packages.append({"package": pkg, "license": lic})
            else:
                violations.append({
                    "package": pkg,
                    "license": lic,
                    "normalised_license": cls._canonical(lic),
                    "matched_forbidden": sorted(matched_all),
                    "not_in_allowlist": any_not_allowed,
                    "unknown_license": any_unknown,
                })

        return {
            "compliant": len(violations) == 0,
            "packages_scanned": len(packages_and_licenses),
            "compliant_count": len(compliant_packages),
            "violations_count": len(violations),
            "violations": violations,
            "compliant_packages": compliant_packages,
        }

    @classmethod
    def infer_package_license(cls, package_name: str) -> str:
        """Infer license from known repository catalogue or package naming heuristics."""
        clean_name = re.sub(r"[-_.]+", "-", str(package_name).strip().lower())
        return cls.KNOWN_PACKAGE_LICENSES.get(clean_name, "UNKNOWN")

    @classmethod
    def scan_requirements_text(
        cls,
        requirements_text: str,
        forbidden_licenses: Optional[List[str]] = None,
        allowed_licenses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Parse raw requirements.txt lines, infer licenses, and test against license policies.

        Licences come from a small built-in table of well-known packages, not from package
        metadata (``licenses_inferred: True``); anything not in it is ``UNKNOWN`` and fails. Option
        lines (``-r``, ``-c``, ``-i``, ``--hash`` ...) are skipped. A URL / VCS requirement is a
        package that cannot be looked up, so it is reported as ``UNKNOWN`` rather than skipped.
        Text with no requirement in it is not "compliant".
        """
        packages_and_licenses: List[Dict[str, str]] = []
        text = requirements_text if isinstance(requirements_text, str) else ""

        for raw in text.splitlines():
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith(("#", "-")):
                continue
            if "://" in line or line.lower().startswith("git+"):
                egg = re.search(r"#egg=([A-Za-z0-9_.\-]+)", line)
                named = re.match(r"^([A-Za-z0-9_.\-]+)\s*@", line)
                name = (egg.group(1) if egg else named.group(1) if named else line)[:200]
                packages_and_licenses.append({"package": name, "license": "UNKNOWN"})
                continue
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_\-\.]*)", line)
            if match:
                pkg_name = match.group(1)
                lic = cls.infer_package_license(pkg_name)
                packages_and_licenses.append({"package": pkg_name, "license": lic})

        result = cls.scan_licenses(
            packages_and_licenses=packages_and_licenses,
            forbidden_tokens=forbidden_licenses,
            allowed_licenses=allowed_licenses,
        )
        result["licenses_inferred"] = True
        if not packages_and_licenses:
            result["error"] = "No requirements were found in the text, so nothing was scanned."
        return result

    @classmethod
    def check_license_compatibility(
        cls,
        target_project_license: str,
        dependency_licenses: List[str],
    ) -> Dict[str, Any]:
        """Assess compatibility between host project license (e.g. MIT/Proprietary) and dependency licenses.

        Dependency expressions are evaluated with ``OR`` choices (``MIT OR GPL-3.0`` is usable under
        MIT), ``AND`` obligations (``MIT AND GPL-3.0`` is not) and parentheses. A dependency whose
        licence is unknown is a conflict (it cannot be shown compatible), as is a host licence that
        is unknown; an empty dependency list checks nothing and is not "compatible".
        """
        host_lic = cls._canonical(_normalise_spdx(str(target_project_license or "")))
        deps = [d for d in dependency_licenses] if isinstance(dependency_licenses, (list, tuple)) else []
        if host_lic in _UNKNOWN_LABELS or not deps:
            reason = (
                "The project licence is unknown, so compatibility cannot be assessed."
                if host_lic in _UNKNOWN_LABELS
                else "No dependency licences were supplied, so nothing was checked."
            )
            return {
                "compatible": False,
                "error": reason,
                "host_license": host_lic,
                "conflicts_count": 0,
                "conflicts": [],
            }

        restrictive_host = host_lic in cls.PERMISSIVE_LICENSES or host_lic == "PROPRIETARY"
        severity_rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}

        def atom_issue(component: str) -> Optional[Tuple[str, str]]:
            canon = cls._canonical(_normalise_spdx(component)).strip()
            toks = _TOKEN_RE.findall(canon)
            if canon in _UNKNOWN_LABELS or any(t in ("UNKNOWN", "NOASSERTION") for t in toks):
                return "MEDIUM", "License could not be determined, so compatibility cannot be shown."
            if any(t.startswith("AGPL") for t in toks):
                return "CRITICAL", "AGPL imposes network copyleft requirements on entire application."
            if restrictive_host and any(t == "GPL" or t.startswith("GPL-") for t in toks):
                return "HIGH", f"Strong copyleft GPL license conflicts with {host_lic} host distribution."
            return None

        conflicts: List[Dict[str, str]] = []
        for lic in deps:
            worst: Optional[Tuple[str, str]] = None
            clear = False
            for components in cls._alternatives(str(lic)):
                issues = [i for i in (atom_issue(c) for c in components) if i]
                if not issues:
                    clear = True
                    break
                top = max(issues, key=lambda i: severity_rank[i[0]])
                if worst is None or severity_rank[top[0]] > severity_rank[worst[0]]:
                    worst = top
            if not clear and worst is not None:
                conflicts.append({
                    "dependency_license": str(lic),
                    "severity": worst[0],
                    "reason": worst[1],
                })

        return {
            "compatible": len(conflicts) == 0,
            "host_license": host_lic,
            "dependencies_checked": len(deps),
            "conflicts_count": len(conflicts),
            "conflicts": conflicts,
        }

    @staticmethod
    def _cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").replace("`", "'")

    @classmethod
    def generate_compliance_report(cls, scan_result: Dict[str, Any]) -> str:
        """Format an audit-ready Markdown compliance report."""
        if not isinstance(scan_result, dict):
            scan_result = {}
        passed = scan_result.get("compliant") is True
        lines = [
            "# Dependency License Compliance Audit",
            "",
            f"**Status:** {'PASSED (COMPLIANT)' if passed else 'FAILED (NON-COMPLIANT)'}",
            f"**Total Compliant:** {scan_result.get('compliant_count', 0)}",
            f"**Total Violations:** {scan_result.get('violations_count', 0)}",
            "",
        ]
        if scan_result.get("error"):
            lines.extend([f"> {cls._cell(scan_result['error'])}", ""])
        if scan_result.get("licenses_inferred"):
            lines.extend(["> Licences were inferred from a built-in package table, not read from package metadata.", ""])

        violations = scan_result.get("violations", [])
        if violations:
            lines.extend([
                "## Violations & Incompatible Dependencies",
                "",
                "| Package | License | Normalised ID | Matched Forbidden | Not In Allowlist |",
                "|---|---|---|---|---|",
            ])
            for v in violations:
                lines.append(
                    f"| `{cls._cell(v.get('package'))}` | `{cls._cell(v.get('license'))}` "
                    f"| `{cls._cell(v.get('normalised_license', ''))}` "
                    f"| `{cls._cell(', '.join(v.get('matched_forbidden', [])) or '-')}` | `{v.get('not_in_allowlist')}` |"
                )
            lines.append("")

        compliant = scan_result.get("compliant_packages", [])
        if compliant:
            lines.extend([
                "## Compliant Packages",
                "",
                "| Package | License |",
                "|---|---|",
            ])
            for c in compliant[:50]:
                lines.append(f"| `{cls._cell(c.get('package'))}` | `{cls._cell(c.get('license'))}` |")
            if len(compliant) > 50:
                lines.append("")
                lines.append(f"_{len(compliant) - 50} more compliant package(s) not listed._")

        return "\n".join(lines)
