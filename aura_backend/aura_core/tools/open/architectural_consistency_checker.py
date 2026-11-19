from typing import Any, Dict, List, Optional


class ArchitecturalConsistencyChecker:
    """Checker for enforcing architectural layer import rules and dependency boundaries."""

    DEFAULT_FORBIDDEN_RULES: Dict[str, List[str]] = {
        "domain": ["adapters", "api", "aura_admin", "infrastructure", "ui"],
        "adapters": ["api", "aura_admin", "ui"],
        "core": ["adapters", "api", "ui"],
    }

    @classmethod
    def check_module_imports(
        cls,
        module_name: str,
        imported_modules: List[str],
        forbidden_rules: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """Verify that imported_modules do not violate layer boundaries for module_name."""
        rules = forbidden_rules if forbidden_rules is not None else cls.DEFAULT_FORBIDDEN_RULES
        violations: List[str] = []

        for layer, forbidden_targets in rules.items():
            if layer and layer in module_name:
                for imp in imported_modules:
                    for forbidden in forbidden_targets:
                        if forbidden and forbidden in imp:
                            violations.append(
                                f"Layer violation: '{module_name}' ({layer}) cannot import '{imp}' ({forbidden})."
                            )

        return {
            "module_name": module_name,
            "is_consistent": len(violations) == 0,
            "violations_count": len(violations),
            "violations": violations,
        }

    @classmethod
    def audit_project_modules(
        cls,
        module_import_map: Dict[str, List[str]],
        forbidden_rules: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """Audit multiple modules and return an aggregate architecture report."""
        reports: List[Dict[str, Any]] = []
        all_violations: List[str] = []

        for mod_name, imports in module_import_map.items():
            res = cls.check_module_imports(mod_name, imports, forbidden_rules=forbidden_rules)
            reports.append(res)
            all_violations.extend(res["violations"])

        return {
            "is_consistent": len(all_violations) == 0,
            "modules_checked": len(module_import_map),
            "total_violations": len(all_violations),
            "violations": all_violations,
            "module_reports": reports,
        }