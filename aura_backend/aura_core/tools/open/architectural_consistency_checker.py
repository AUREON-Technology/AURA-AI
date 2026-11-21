"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Pure, but it needs a
module-to-imports map the caller must assemble first, and its default layer names are generic.
AURA's own layering is enforced by ``tests/architecture``. Not worth the planner prompt today.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

from typing import Any, Dict, List, Optional, Set


class ArchitecturalConsistencyChecker:
    """Checker for enforcing architectural layer import rules, clean architecture boundaries, and circular dependencies."""

    DEFAULT_FORBIDDEN_RULES: Dict[str, List[str]] = {
        "domain": ["adapters", "api", "aura_admin", "infrastructure", "ui"],
        "adapters": ["api", "aura_admin", "ui"],
        "core": ["adapters", "api", "ui"],
    }

    DEFAULT_LAYER_TIERS: Dict[str, int] = {
        "domain": 0,
        "core": 1,
        "application": 2,
        "adapters": 3,
        "infrastructure": 3,
        "api": 4,
        "ui": 5,
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

    @classmethod
    def detect_layering_violations(
        cls,
        import_graph: Dict[str, List[str]],
        layer_tiers: Optional[Dict[str, int]] = None,
    ) -> List[Dict[str, Any]]:
        """Verify that dependencies flow strictly from outer layers to inner layers (lower tier cannot import higher tier)."""
        tiers = layer_tiers or cls.DEFAULT_LAYER_TIERS
        violations: List[Dict[str, Any]] = []

        def get_tier(mod: str) -> Optional[int]:
            mod_low = mod.lower()
            for name, rank in tiers.items():
                if name in mod_low:
                    return rank
            return None

        for source_mod, targets in import_graph.items():
            source_tier = get_tier(source_mod)
            if source_tier is None:
                continue

            for target_mod in targets:
                target_tier = get_tier(target_mod)
                if target_tier is None:
                    continue

                if target_tier > source_tier:
                    violations.append({
                        "source": source_mod,
                        "source_tier": source_tier,
                        "target": target_mod,
                        "target_tier": target_tier,
                        "description": f"Inner layer '{source_mod}' (tier {source_tier}) depends on outer layer '{target_mod}' (tier {target_tier}).",
                    })

        return violations

    @staticmethod
    def detect_circular_package_dependencies(
        import_graph: Dict[str, List[str]],
    ) -> List[List[str]]:
        """Identify circular import cycles using DFS traversal."""
        cycles: List[List[str]] = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        path: List[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in import_graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    idx = path.index(neighbor)
                    cycle = path[idx:] + [neighbor]
                    cycles.append(cycle)

            path.pop()
            rec_stack.remove(node)

        for mod in list(import_graph.keys()):
            if mod not in visited:
                dfs(mod)

        return cycles

    @classmethod
    def generate_architecture_compliance_report(
        cls,
        audit_result: Dict[str, Any],
        layer_violations: Optional[List[Dict[str, Any]]] = None,
        cycles: Optional[List[List[str]]] = None,
    ) -> str:
        """Render a comprehensive architecture status report in Markdown."""
        lines = [
            "# Architectural Consistency & Boundary Audit",
            "",
            f"**Consistent:** {'YES' if audit_result.get('is_consistent') and not (layer_violations or cycles) else 'NO'}",
            f"**Modules Audited:** {audit_result.get('modules_checked', 0)}",
            f"**Boundary Violations:** {audit_result.get('total_violations', 0)}",
            f"**Layering Violations:** {len(layer_violations or [])}",
            f"**Circular Cycles:** {len(cycles or [])}",
            "",
        ]

        if audit_result.get("violations"):
            lines.extend([
                "## Layer Boundary Violations",
                "",
            ])
            for v in audit_result["violations"]:
                lines.append(f"- :x: {v}")
            lines.append("")

        if layer_violations:
            lines.extend([
                "## Tier Inversion Violations",
                "",
                "| Source | Tier | Target | Tier | Detail |",
                "|---|---|---|---|---|",
            ])
            for lv in layer_violations:
                lines.append(f"| `{lv['source']}` | {lv['source_tier']} | `{lv['target']}` | {lv['target_tier']} | {lv['description']} |")
            lines.append("")

        if cycles:
            lines.extend([
                "## Circular Dependency Cycles",
                "",
            ])
            for cyc in cycles:
                lines.append(f"- Cycle: `{' -> '.join(cyc)}`")
            lines.append("")

        return "\n".join(lines)
