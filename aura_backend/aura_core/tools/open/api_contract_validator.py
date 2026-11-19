import json
from typing import Any, Dict, List, Optional


class ApiContractValidator:
    """Validator for OpenAPI/JSON API contracts, required fields, and breaking change checks."""

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