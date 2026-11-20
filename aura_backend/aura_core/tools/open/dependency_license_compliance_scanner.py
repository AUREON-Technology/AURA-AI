import re
from typing import Any, Dict, List, Optional


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

# Exact SPDX IDs that are forbidden by default.
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
    """Scanner for checking software package licenses against policy allowlists,
    denylists, and copyleft compatibility.

    Bugs fixed (v2):
    - LGPL-3.0 is no longer flagged by a "GPL-3.0" rule (substring matching removed).
    - BSL-1.0 (Boost, permissive) is NOT forbidden; only BSL-1.1 / SSPL-1.0 are.
    - UNKNOWN licenses fail by default (instead of passing silently).
    - GPLv3 and similar aliases are resolved to canonical SPDX IDs before matching.
    - All forbidden checks use exact SPDX ID membership, not substring search.
    """

    DEFAULT_FORBIDDEN_LICENSES: List[str] = sorted(_DEFAULT_FORBIDDEN_IDS)

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

    @classmethod
    def _alternatives(cls, lic: str) -> List[List[str]]:
        """``"(MIT OR GPL-2.0) AND X"`` -> alternatives, each a list of AND-ed components."""
        text = lic.replace("(", " ").replace(")", " ")
        alternatives: List[List[str]] = []
        for alt in re.split(r"\s+OR\s+", text.strip(), flags=re.IGNORECASE):
            components = [
                re.split(r"\s+WITH\s+", c.strip(), maxsplit=1, flags=re.IGNORECASE)[0].strip()
                for c in re.split(r"\s+AND\s+", alt, flags=re.IGNORECASE)
            ]
            alternatives.append([c for c in components if c] or [""])
        return alternatives

    @classmethod
    def _evaluate_alternative(
        cls,
        components: List[str],
        forbidden: Set[str],
        allowed_set: Optional[Set[str]],
    ) -> Tuple[List[str], bool, bool]:
        tokens = [t for c in components for t in _TOKEN_RE.findall(c.upper())]
        matched = sorted(f for f in forbidden if any(cls._matches_token(t, f) for t in tokens))
        unknown = any(c.upper() in _UNKNOWN_LABELS for c in components)
        not_allowed = allowed_set is not None and any(c.upper() not in allowed_set for c in components)
        return matched, not_allowed, unknown

    @classmethod
    def scan_licenses(
        cls,
        packages_and_licenses: List[Dict[str, str]],
        forbidden_tokens: Optional[List[str]] = None,
        allowed_licenses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Scan a list of {'package': name, 'license': spdx_or_label} items.

        Matching strategy (v2 – exact SPDX IDs):
        - Each raw license string is normalised via alias resolution and upper-casing.
        - A package is a violation when its normalised SPDX ID is in the forbidden set,
          OR when an allowlist is provided and the ID is not in it.
        - No substring matching is performed – 'LGPL-3.0' cannot be matched by 'GPL-3.0'.
        """
        # Build the forbidden set (exact normalised SPDX IDs)
        if forbidden_tokens is not None:
            forbidden_set: frozenset = frozenset(
                _normalise_spdx(t) for t in forbidden_tokens
            )
        else:
            forbidden_set = _DEFAULT_FORBIDDEN_IDS

        allowed_set = (
            frozenset(_normalise_spdx(x) for x in allowed_licenses)
            if allowed_licenses is not None
            else None
        )

        violations: List[Dict[str, Any]] = []
        compliant_packages: List[Dict[str, str]] = []

        for item in packages_and_licenses:
            pkg = str(item.get("package", "<unknown>")).strip()
            raw_lic = str(item.get("license", "UNKNOWN")).strip()
            norm_lic = _normalise_spdx(raw_lic)

            matched_forbidden = [t for t in forbidden_set if t == norm_lic]
            not_in_allowlist = allowed_set is not None and norm_lic not in allowed_set

            ok = not matched_forbidden and not not_in_allowlist
            if ok:
                compliant_packages.append({"package": pkg, "license": lic})
            else:
                violations.append({
                    "package": pkg,
                    "license": raw_lic,
                    "normalised_license": norm_lic,
                    "matched_forbidden": matched_forbidden,
                    "not_in_allowlist": not_in_allowlist,
                })
        return {
            "compliant": len(violations) == 0,
            "compliant_count": len(compliant_packages),
            "violations_count": len(violations),
            "violations": violations,
            "compliant_packages": compliant_packages,
        }

    @classmethod
    def infer_package_license(cls, package_name: str) -> str:
        """Infer license from known repository catalogue or package naming heuristics."""
        clean_name = re.sub(r"[-_.]+", "-", package_name.strip().lower())
        return cls.KNOWN_PACKAGE_LICENSES.get(clean_name, "UNKNOWN")

    @classmethod
    def scan_requirements_text(
        cls,
        requirements_text: str,
        forbidden_licenses: Optional[List[str]] = None,
        allowed_licenses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Parse raw requirements.txt lines, infer licenses, and test against license policies."""
        packages_and_licenses: List[Dict[str, str]] = []

        for line in requirements_text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-r", "-e", "--")):
                continue
            match = re.match(r"^([A-Za-z0-9_\-\.]+)", line)
            if match:
                pkg_name = match.group(1)
                lic = cls.infer_package_license(pkg_name)
                packages_and_licenses.append({"package": pkg_name, "license": lic})

        return cls.scan_licenses(
            packages_and_licenses=packages_and_licenses,
            forbidden_tokens=forbidden_licenses,
            allowed_licenses=allowed_licenses,
        )

    @classmethod
    def check_license_compatibility(
        cls,
        target_project_license: str,
        dependency_licenses: List[str],
    ) -> Dict[str, Any]:
        """Assess compatibility between host project license (e.g. MIT/Proprietary) and dependency licenses."""
        host_lic = _normalise_spdx(target_project_license)
        conflicts: List[Dict[str, str]] = []

        for lic in dependency_licenses:
            norm = _normalise_spdx(lic)
            if "AGPL" in norm:
                conflicts.append({
                    "dependency_license": lic,
                    "severity": "CRITICAL",
                    "reason": "AGPL imposes network copyleft requirements on entire application.",
                })
            elif norm.startswith("GPL-") and not norm.startswith("LGPL-"):
                if host_lic in cls.PERMISSIVE_LICENSES or host_lic == "PROPRIETARY":
                    conflicts.append({
                        "dependency_license": lic,
                        "severity": "HIGH",
                        "reason": f"Strong copyleft GPL license conflicts with {host_lic} host distribution.",
                    })

        return {
            "compatible": len(conflicts) == 0,
            "host_license": host_lic,
            "conflicts_count": len(conflicts),
            "conflicts": conflicts,
        }

    @classmethod
    def generate_compliance_report(cls, scan_result: Dict[str, Any]) -> str:
        """Format an audit-ready Markdown compliance report."""
        lines = [
            "# Dependency License Compliance Audit",
            "",
            f"**Status:** {'PASSED (COMPLIANT)' if scan_result.get('compliant') else 'FAILED (NON-COMPLIANT)'}",
            f"**Total Compliant:** {scan_result.get('compliant_count', 0)}",
            f"**Total Violations:** {scan_result.get('violations_count', 0)}",
            "",
        ]

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
                    f"| `{v.get('package')}` | `{v.get('license')}` | `{v.get('normalised_license', '')}` "
                    f"| `{', '.join(v.get('matched_forbidden', [])) or '-'}` | `{v.get('not_in_allowlist')}` |"
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
                lines.append(f"| `{c.get('package')}` | `{c.get('license')}` |")

        return "\n".join(lines)
