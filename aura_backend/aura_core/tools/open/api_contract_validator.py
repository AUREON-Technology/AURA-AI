import json
from typing import Any, Dict, List, Optional, Set


class ApiContractValidator:
    """Validator for OpenAPI/JSON API contracts, required fields, breaking change detection, and SemVer recommendations."""

    @staticmethod
    def parse_spec(spec_content: str) -> Dict[str, Any]:
        """Parse a JSON OpenAPI/contract document."""
        try:
            data = json.loads(spec_content)
            if not isinstance(data, dict):
                return {"valid": False, "error": "Spec root must be a JSON object.", "spec": None}
            return {"valid": True, "error": None, "spec": data}
        except json.JSONDecodeError as exc:
            return {"valid": False, "error": f"JSON parse error: {exc}", "spec": None}

    @classmethod
    def check_deprecated_endpoints(cls, spec_content: str) -> Dict[str, Any]:
        """List endpoints marked as deprecated in an OpenAPI JSON specification."""
        parsed = cls.parse_spec(spec_content)
        if not parsed["valid"]:
            return {"valid": False, "error": parsed["error"], "deprecated": []}

        spec = parsed["spec"]
        paths = spec.get("paths", {})
        deprecated: List[Dict[str, str]] = []

        if isinstance(paths, dict):
            for route, methods in paths.items():
                if isinstance(methods, dict):
                    for method, details in methods.items():
                        if isinstance(details, dict) and details.get("deprecated") is True:
                            deprecated.append({"path": route, "method": method.upper()})

        return {
            "valid": True,
            "has_deprecated": len(deprecated) > 0,
            "deprecated": deprecated,
        }

    @staticmethod
    def validate_payload_against_schema(
        payload: Dict[str, Any],
        required_fields: List[str],
        field_types: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Validate a request/response payload dict against required fields and basic types."""
        type_map = {
            "str": str,
            "string": str,
            "int": int,
            "integer": int,
            "float": (int, float),
            "number": (int, float),
            "bool": bool,
            "boolean": bool,
            "list": list,
            "array": list,
            "dict": dict,
            "object": dict,
        }

        missing = [f for f in required_fields if f not in payload]
        type_mismatches: List[Dict[str, Any]] = []

        for key, expected_label in (field_types or {}).items():
            if key in payload:
                py_types = type_map.get(expected_label.lower())
                if py_types is not None and not isinstance(payload[key], py_types):
                    type_mismatches.append({
                        "field": key,
                        "expected": expected_label,
                        "actual": type(payload[key]).__name__,
                    })

        valid = len(missing) == 0 and len(type_mismatches) == 0
        return {
            "valid": valid,
            "missing_fields": missing,
            "type_mismatches": type_mismatches,
        }

    @classmethod
    def compare_route_sets(cls, old_spec_content: str, new_spec_content: str) -> Dict[str, Any]:
        """Compare paths between two OpenAPI JSON specs to flag removed routes."""
        old_p = cls.parse_spec(old_spec_content)
        new_p = cls.parse_spec(new_spec_content)
        if not old_p["valid"] or not new_p["valid"]:
            return {
                "compatible": False,
                "error": old_p.get("error") or new_p.get("error"),
                "removed_paths": [],
            }

        old_paths = set((old_p["spec"].get("paths") or {}).keys())
        new_paths = set((new_p["spec"].get("paths") or {}).keys())
        removed = sorted(old_paths - new_paths)
        added = sorted(new_paths - old_paths)

        return {
            "compatible": len(removed) == 0,
            "removed_paths": removed,
            "added_paths": added,
        }

    @classmethod
    def detect_breaking_changes(
        cls,
        old_spec: Dict[str, Any],
        new_spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Perform deep contract diffing between two OpenAPI schemas to uncover breaking changes."""
        breaking_changes: List[str] = []
        non_breaking_changes: List[str] = []

        old_paths = old_spec.get("paths", {})
        new_paths = new_spec.get("paths", {})

        for path, old_methods in old_paths.items():
            if path not in new_paths:
                breaking_changes.append(f"Removed endpoint: '{path}'")
                continue

            new_methods = new_paths[path]
            for method, old_def in old_methods.items():
                if method.startswith("x-"):
                    continue
                if method not in new_methods:
                    breaking_changes.append(f"Removed HTTP method '{method.upper()}' on '{path}'")
                    continue

                new_def = new_methods[method]

                # Check required parameters
                old_req_params = {p["name"] for p in old_def.get("parameters", []) if p.get("required")}
                new_req_params = {p["name"] for p in new_def.get("parameters", []) if p.get("required")}
                added_req_params = new_req_params - old_req_params
                for p_name in added_req_params:
                    breaking_changes.append(f"Added required parameter '{p_name}' to '{method.upper()} {path}'")

        # Added new endpoints are non-breaking
        for path in new_paths:
            if path not in old_paths:
                non_breaking_changes.append(f"Added new endpoint: '{path}'")

        is_breaking = len(breaking_changes) > 0
        recommended_bump = "MAJOR" if is_breaking else ("MINOR" if non_breaking_changes else "PATCH")

        return {
            "is_breaking": is_breaking,
            "recommended_bump": recommended_bump,
            "breaking_changes_count": len(breaking_changes),
            "breaking_changes": breaking_changes,
            "non_breaking_changes": non_breaking_changes,
        }

    @classmethod
    def generate_api_diff_changelog(cls, breaking_report: Dict[str, Any]) -> str:
        """Format an API release note changelog in Markdown."""
        bump = breaking_report.get("recommended_bump", "PATCH")
        is_break = breaking_report.get("is_breaking", False)

        lines = [
            f"# API Contract Changelog (Recommended Bump: `{bump}`)",
            "",
            f"**Compatibility:** {'⚠️ BREAKING CHANGES DETECTED' if is_break else '✅ BACKWARDS COMPATIBLE'}",
            "",
        ]

        breaking = breaking_report.get("breaking_changes", [])
        if breaking:
            lines.extend([
                "## 🚨 Breaking Changes",
                "",
            ])
            for b in breaking:
                lines.append(f"- :x: {b}")
            lines.append("")

        non_breaking = breaking_report.get("non_breaking_changes", [])
        if non_breaking:
            lines.extend([
                "## 🚀 Additions & Enhancements",
                "",
            ])
            for nb in non_breaking:
                lines.append(f"- :sparkles: {nb}")
            lines.append("")

        return "\n".join(lines)
