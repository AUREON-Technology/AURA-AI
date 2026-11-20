import re
from typing import Any, Dict, List, Optional, Set, Tuple

_UNKNOWN_LABELS = frozenset({"", "UNKNOWN", "NONE", "NOASSERTION", "N/A", "NA", "OTHER"})
_TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9.+\-]*")


class DependencyLicenseComplianceScanner:
    """Scanner for checking software package licenses against policy allowlists and denylists.

    A licence string is read as a (simplified) SPDX expression: ``OR`` alternatives, ``AND``
    components, ``WITH`` exceptions and parentheses. A package is compliant when at least one
    ``OR`` alternative has no forbidden token, passes the allowlist (when one is given) and is not
    an unknown/blank label. Forbidden tokens match on a *token boundary*, not as a substring, so
    ``GPL-3.0`` does not flag ``LGPL-3.0`` and ``BUSL`` does not flag ``BSL-1.0`` (Boost).
    """

    #: Strong-copyleft and source-available families. ``GPL-3`` / ``GPLV3`` are the spellings
    #: package metadata uses instead of the SPDX id; ``BSL-1.0`` is the permissive Boost licence,
    #: so the Business Source License is listed by its own ids only.
    DEFAULT_FORBIDDEN_LICENSES = [
        "AGPL",
        "GPL-3.0",
        "GPL-3",
        "GPLV3",
        "GPL-2.0",
        "GPL-2",
        "GPLV2",
        "SSPL",
        "BUSL",
        "BSL-1.1",
    ]

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
        """
        Scan a list of {'package': name, 'license': spdx_or_label} items.
        Flags forbidden licenses, licenses not present in allowed_licenses if provided, and
        unknown/blank licenses (a licence nobody can name is not a licence anybody cleared).
        """
        forbidden_list = (
            forbidden_tokens if forbidden_tokens is not None else cls.DEFAULT_FORBIDDEN_LICENSES
        )
        forbidden = {str(x).strip().upper() for x in forbidden_list if str(x).strip()}
        allowed_set = (
            {x.strip().upper() for x in allowed_licenses} if allowed_licenses is not None else None
        )

        violations: List[Dict[str, Any]] = []
        compliant_packages: List[Dict[str, str]] = []

        for item in packages_and_licenses:
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
                    "matched_forbidden": sorted(matched_all),
                    "not_in_allowlist": any_not_allowed,
                    "unknown_license": any_unknown,
                })

        return {
            "compliant": len(violations) == 0,
            "compliant_count": len(compliant_packages),
            "violations_count": len(violations),
            "violations": violations,
            "compliant_packages": compliant_packages,
        }
