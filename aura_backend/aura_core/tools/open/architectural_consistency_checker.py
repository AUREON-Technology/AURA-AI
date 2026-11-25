import re
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

    @staticmethod
    def _segments(name: Any) -> List[str]:
        """``aura_core.domain/x`` -> ``["aura_core", "domain", "x"]`` (lower-case)."""
        return [seg for seg in re.split(r"[./\\:]+", str(name).lower()) if seg]

    @classmethod
    def _names_layer(cls, module: Any, layer: str, known: Set[str]) -> bool:
        """Does ``module`` belong to / point at ``layer``?

        Matching is on whole dotted segments (``aura_backend.api.routes`` is in ``api``;
        ``rapid_client`` and ``building`` are not in ``api`` / ``ui``). Only when no segment of the
        name is any known layer (``known``) do its ``_``/``-`` tokens count, so ``domain_models``
        is ``domain`` but ``aura_core.domain.x`` is not also ``core``.
        """
        layer = str(layer).lower()
        segments = cls._segments(module)
        if layer in segments:
            return True
        if any(seg in known for seg in segments):
            return False
        return layer in {tok for seg in segments for tok in re.split(r"[_\-]+", seg) if tok}

    @classmethod
    def check_module_imports(
        cls,
        module_name: str,
        imported_modules: List[str],
        forbidden_rules: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """Verify that imported_modules do not violate layer boundaries for module_name.

        ``rules_applied`` says how many layer rules matched ``module_name``; when it is 0 the module
        is in no known layer and nothing was actually checked (``layer_known`` is False).
        """
        rules = forbidden_rules if forbidden_rules is not None else cls.DEFAULT_FORBIDDEN_RULES
        violations: List[str] = []
        imports = [i for i in imported_modules if isinstance(i, str)] if isinstance(imported_modules, (list, tuple)) else []
        rules_applied = 0
        known = {str(k).lower() for k in rules} | {
            str(t).lower() for targets in rules.values() for t in targets if t
        }

        for layer, forbidden_targets in rules.items():
            if layer and cls._names_layer(module_name, layer, known):
                rules_applied += 1
                for imp in imports:
                    for forbidden in forbidden_targets:
                        if forbidden and cls._names_layer(imp, forbidden, known):
                            violations.append(
                                f"Layer violation: '{module_name}' ({layer}) cannot import '{imp}' ({forbidden})."
                            )

        return {
            "module_name": module_name,
            "is_consistent": len(violations) == 0,
            "violations_count": len(violations),
            "violations": violations,
            "rules_applied": rules_applied,
            "layer_known": rules_applied > 0,
        }

    @classmethod
    def audit_project_modules(
        cls,
        module_import_map: Dict[str, List[str]],
        forbidden_rules: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """Audit multiple modules and return an aggregate architecture report.

        An empty (or non-mapping) input audited nothing, so it is never ``is_consistent``.
        Modules that fall in no known layer are listed in ``unclassified_modules``: they were not
        checked, whatever the verdict says about the others.
        """
        if not isinstance(module_import_map, dict) or not module_import_map:
            return {
                "is_consistent": False,
                "error": "No modules were supplied, so nothing was audited.",
                "modules_checked": 0,
                "total_violations": 0,
                "violations": [],
                "unclassified_modules": [],
                "module_reports": [],
            }

        reports: List[Dict[str, Any]] = []
        all_violations: List[str] = []

        for mod_name, imports in module_import_map.items():
            res = cls.check_module_imports(mod_name, imports, forbidden_rules=forbidden_rules)
            reports.append(res)
            all_violations.extend(res["violations"])

        unclassified = [r["module_name"] for r in reports if not r["layer_known"]]
        result: Dict[str, Any] = {
            "is_consistent": len(all_violations) == 0,
            "modules_checked": len(module_import_map),
            "total_violations": len(all_violations),
            "violations": all_violations,
            "unclassified_modules": unclassified,
            "module_reports": reports,
        }
        if unclassified:
            result["warnings"] = [
                f"{len(unclassified)} module(s) belong to no known layer and were not checked."
            ]
        return result

    @classmethod
    def detect_layering_violations(
        cls,
        import_graph: Dict[str, List[str]],
        layer_tiers: Optional[Dict[str, int]] = None,
    ) -> List[Dict[str, Any]]:
        """Verify that dependencies flow strictly from outer layers to inner layers (lower tier cannot import higher tier)."""
        tiers = layer_tiers or cls.DEFAULT_LAYER_TIERS
        violations: List[Dict[str, Any]] = []
        if not isinstance(import_graph, dict):
            return violations

        def get_tier(mod: str) -> Optional[int]:
            # Whole dotted segments first, in path order (``aura_core.api.x`` is ``api``, not the
            # ``core`` that merely appears in the package name); ``_``/``-`` tokens as a fallback.
            segments = cls._segments(mod)
            ranked = {str(name).lower(): rank for name, rank in tiers.items()}
            for seg in segments:
                if seg in ranked:
                    return ranked[seg]
            for seg in segments:
                for tok in re.split(r"[_\-]+", seg):
                    if tok in ranked:
                        return ranked[tok]
            return None

        for source_mod, targets in import_graph.items():
            source_tier = get_tier(source_mod)
            if source_tier is None:
                continue

            for target_mod in targets if isinstance(targets, (list, tuple)) else []:
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
        """Identify circular import cycles using an iterative DFS (no recursion limit on deep graphs).

        Each cycle is a path that starts and ends on the same node, e.g. ``["A", "B", "C", "A"]``.
        """
        cycles: List[List[str]] = []
        if not isinstance(import_graph, dict):
            return cycles

        def neighbours(node: Any) -> List[Any]:
            targets = import_graph.get(node, [])
            return list(targets) if isinstance(targets, (list, tuple)) else []

        visited: Set[Any] = set()
        rec_stack: Set[Any] = set()
        path: List[Any] = []

        for root in list(import_graph.keys()):
            if root in visited:
                continue
            visited.add(root)
            rec_stack.add(root)
            path.append(root)
            stack = [iter(neighbours(root))]
            while stack:
                try:
                    neighbor = next(stack[-1])
                except StopIteration:
                    stack.pop()
                    rec_stack.discard(path.pop())
                    continue
                if neighbor not in visited:
                    visited.add(neighbor)
                    rec_stack.add(neighbor)
                    path.append(neighbor)
                    stack.append(iter(neighbours(neighbor)))
                elif neighbor in rec_stack:
                    cycles.append(path[path.index(neighbor):] + [neighbor])

        return cycles

    @classmethod
    def generate_architecture_compliance_report(
        cls,
        audit_result: Dict[str, Any],
        layer_violations: Optional[List[Dict[str, Any]]] = None,
        cycles: Optional[List[List[str]]] = None,
    ) -> str:
        """Render a comprehensive architecture status report in Markdown."""
        if not isinstance(audit_result, dict):
            audit_result = {}
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
                detail = str(lv["description"]).replace("|", "\\|")
                lines.append(f"| `{lv['source']}` | {lv['source_tier']} | `{lv['target']}` | {lv['target_tier']} | {detail} |")
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
