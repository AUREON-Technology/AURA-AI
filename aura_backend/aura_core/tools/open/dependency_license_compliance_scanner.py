from typing import Any, Dict, List, Optional


class DependencyLicenseComplianceScanner:
    """Scanner for checking software package licenses against policy allowlists and denylists."""

    DEFAULT_FORBIDDEN_LICENSES = [
        "AGPL",
        "GPL-3.0",
        "GPL-2.0",
        "SSPL",
        "BSL",
    ]

    @classmethod
    def scan_licenses(
        cls,
        packages_and_licenses: List[Dict[str, str]],
        forbidden_tokens: Optional[List[str]] = None,
        allowed_licenses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Scan a list of {'package': name, 'license': spdx_or_label} items.
        Flags forbidden licenses or licenses not present in allowed_licenses if provided.
        """
        forbidden = forbidden_tokens if forbidden_tokens is not None else cls.DEFAULT_FORBIDDEN_LICENSES
        allowed_set = {x.strip().upper() for x in allowed_licenses} if allowed_licenses is not None else None

        violations: List[Dict[str, Any]] = []
        compliant_packages: List[Dict[str, str]] = []

        for item in packages_and_licenses:
            pkg = str(item.get("package", "<unknown>")).strip()
            lic = str(item.get("license", "UNKNOWN")).strip()
            lic_upper = lic.upper()

            matched_forbidden = [tok for tok in forbidden if tok.upper() in lic_upper]
            not_in_allowlist = allowed_set is not None and lic_upper not in allowed_set

            if matched_forbidden or not_in_allowlist:
                violations.append({
                    "package": pkg,
                    "license": lic,
                    "matched_forbidden": matched_forbidden,
                    "not_in_allowlist": not_in_allowlist,
                })
            else:
                compliant_packages.append({"package": pkg, "license": lic})

        return {
            "compliant": len(violations) == 0,
            "compliant_count": len(compliant_packages),
            "violations_count": len(violations),
            "violations": violations,
            "compliant_packages": compliant_packages,
        }