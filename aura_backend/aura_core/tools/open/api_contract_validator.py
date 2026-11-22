import json
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple


class ApiContractValidator:
    """Validator for OpenAPI/JSON API contracts, required fields, breaking change detection, and SemVer recommendations."""

    #: HTTP operations an OpenAPI path item can hold. Other keys (``parameters``, ``summary``,
    #: ``servers``, ``x-*``) describe the path, not an operation.
    HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})

    @staticmethod
    def parse_spec(spec_content: str) -> Dict[str, Any]:
        """Parse a JSON OpenAPI/contract document."""
        if not isinstance(spec_content, str):
            return {"valid": False, "error": "Spec must be given as JSON text.", "spec": None}
        text = spec_content.lstrip("\ufeff").strip()
        if not text:
            return {"valid": False, "error": "Spec is empty.", "spec": None}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            hint = ""
            if re.match(r"(?:openapi|swagger)\s*:", text):
                hint = " (this looks like YAML; only JSON specs are supported -- convert it to JSON first)"
            return {"valid": False, "error": f"JSON parse error: {exc}{hint}", "spec": None}
        except RecursionError:
            return {"valid": False, "error": "Spec is nested too deeply to parse.", "spec": None}
        if not isinstance(data, dict):
            return {"valid": False, "error": "Spec root must be a JSON object.", "spec": None}
        return {"valid": True, "error": None, "spec": data}

    @staticmethod
    def _paths(spec: Any) -> Optional[Dict[str, Any]]:
        paths = spec.get("paths") if isinstance(spec, dict) else None
        return paths if isinstance(paths, dict) else None

    @classmethod
    def _operations(cls, path_item: Any) -> Dict[str, Dict[str, Any]]:
        if not isinstance(path_item, dict):
            return {}
        return {
            str(m).lower(): d
            for m, d in path_item.items()
            if str(m).lower() in cls.HTTP_METHODS and isinstance(d, dict)
        }

    @staticmethod
    def _required_params(path_item: Any, operation: Dict[str, Any]) -> Set[Tuple[str, str]]:
        """Names of required parameters of ``operation``, including ones declared on the path item."""
        found: Set[Tuple[str, str]] = set()
        sources: List[Any] = []
        if isinstance(path_item, dict):
            sources.append(path_item.get("parameters"))
        sources.append(operation.get("parameters"))
        for params in sources:
            for param in params if isinstance(params, list) else []:
                if not isinstance(param, dict) or param.get("required") is not True:
                    continue
                name = param.get("name")
                if name is None:
                    name = param.get("$ref")
                if name is not None:
                    found.add((str(param.get("in", "")), str(name)))
        return found

    @classmethod
    def _contract_breaks(cls, old_paths: Dict[str, Any], new_paths: Dict[str, Any]) -> List[str]:
        """Operation-level breaking changes for paths and operations present in both specs."""
        breaks: List[str] = []
        for path in sorted(set(old_paths) & set(new_paths)):
            old_item, new_item = old_paths[path], new_paths[path]
            old_ops, new_ops = cls._operations(old_item), cls._operations(new_item)
            for method in sorted(old_ops.keys() & new_ops.keys()):
                old_op, new_op = old_ops[method], new_ops[method]
                added = cls._required_params(new_item, new_op) - cls._required_params(old_item, old_op)
                for _location, name in sorted(added):
                    breaks.append(f"Added required parameter '{name}' to '{method.upper()} {path}'")
                old_body, new_body = old_op.get("requestBody"), new_op.get("requestBody")
                old_req = isinstance(old_body, dict) and old_body.get("required") is True
                new_req = isinstance(new_body, dict) and new_body.get("required") is True
                if new_req and not old_req:
                    breaks.append(f"Request body became required on '{method.upper()} {path}'")
        return breaks

    @classmethod
    def check_deprecated_endpoints(cls, spec_content: str) -> Dict[str, Any]:
        """List operations marked ``deprecated: true`` in an OpenAPI JSON specification.

        A document without a ``paths`` object has no endpoints to check, so it is reported as
        invalid input rather than as "nothing deprecated".
        """
        parsed = cls.parse_spec(spec_content)
        if not parsed["valid"]:
            return {"valid": False, "error": parsed["error"], "deprecated": []}

        paths = cls._paths(parsed["spec"])
        if paths is None:
            return {
                "valid": False,
                "error": "Spec has no 'paths' object, so no endpoints could be checked.",
                "deprecated": [],
            }

        deprecated: List[Dict[str, str]] = []
        checked = 0
        for route, item in paths.items():
            for method, details in cls._operations(item).items():
                checked += 1
                if details.get("deprecated") is True:
                    deprecated.append({"path": str(route), "method": method.upper()})

        result: Dict[str, Any] = {
            "valid": True,
            "has_deprecated": len(deprecated) > 0,
            "deprecated": deprecated,
            "endpoints_checked": checked,
        }
        if checked == 0:
            result["warnings"] = ["The spec declares no operations, so there was nothing to check."]
        return result

    @staticmethod
    def validate_payload_against_schema(
        payload: Dict[str, Any],
        required_fields: List[str],
        field_types: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Validate a request/response payload dict against required fields and basic types.

        Nothing to validate (no ``required_fields`` and no ``field_types``), a payload that is not
        an object, and a type label this function does not know are all reported as invalid; none of
        them passes silently.
        """
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
            "null": type(None),
        }

        if not isinstance(payload, dict):
            return {
                "valid": False,
                "error": "Payload must be a JSON object.",
                "missing_fields": [],
                "type_mismatches": [],
                "unknown_types": [],
            }
        required = [str(f) for f in required_fields] if isinstance(required_fields, (list, tuple)) else []
        contracts = field_types if isinstance(field_types, dict) else {}
        if not required and not contracts:
            return {
                "valid": False,
                "error": "Nothing to validate: give required_fields and/or field_types.",
                "missing_fields": [],
                "type_mismatches": [],
                "unknown_types": [],
            }

        missing = [f for f in required if f not in payload]
        type_mismatches: List[Dict[str, Any]] = []
        unknown_types: List[Dict[str, str]] = []

        for key, expected_label in contracts.items():
            label = str(expected_label)
            py_types = type_map.get(label.strip().lower())
            if py_types is None:
                unknown_types.append({"field": str(key), "type": label})
                continue
            if key not in payload:
                continue
            value = payload[key]
            # bool is a subclass of int, and NaN/inf are floats: neither is a valid number here.
            numeric = py_types in (int, (int, float))
            wrong = (
                not isinstance(value, py_types)
                or (numeric and isinstance(value, bool))
                or (numeric and isinstance(value, float) and not math.isfinite(value))
            )
            if wrong:
                type_mismatches.append({
                    "field": str(key),
                    "expected": label,
                    "actual": type(value).__name__,
                })

        valid = not missing and not type_mismatches and not unknown_types
        result: Dict[str, Any] = {
            "valid": valid,
            "missing_fields": missing,
            "type_mismatches": type_mismatches,
        }
        if unknown_types:
            result["unknown_types"] = unknown_types
            result["error"] = "Unknown type label(s); those fields were not checked."
        return result

    @classmethod
    def compare_route_sets(cls, old_spec_content: str, new_spec_content: str) -> Dict[str, Any]:
        """Compare two OpenAPI JSON specs: removed routes/operations and operation-level breaks.

        ``compatible`` is False when a path or HTTP operation disappeared, a required parameter was
        added, or a request body became required. If either spec cannot be parsed or has no
        ``paths`` object nothing was compared, so ``compatible`` is False with an ``error``.
        """
        old_p = cls.parse_spec(old_spec_content)
        new_p = cls.parse_spec(new_spec_content)
        failure = None
        if not old_p["valid"]:
            failure = f"old_spec: {old_p['error']}"
        elif not new_p["valid"]:
            failure = f"new_spec: {new_p['error']}"
        else:
            old_map, new_map = cls._paths(old_p["spec"]), cls._paths(new_p["spec"])
            if old_map is None:
                failure = "old_spec has no 'paths' object, so nothing could be compared."
            elif new_map is None:
                failure = "new_spec has no 'paths' object, so nothing could be compared."
        if failure:
            return {"compatible": False, "error": failure, "removed_paths": []}

        old_paths = set(old_map.keys())
        new_paths = set(new_map.keys())
        removed = sorted(old_paths - new_paths)
        added = sorted(new_paths - old_paths)

        # Dropping one HTTP method from a path that still exists breaks callers just as much.
        removed_operations = sorted(
            f"{m.upper()} {p}"
            for p in old_paths & new_paths
            for m in set(cls._operations(old_map[p])) - set(cls._operations(new_map[p]))
        )
        contract_breaks = cls._contract_breaks(old_map, new_map)

        return {
            "compatible": not removed and not removed_operations and not contract_breaks,
            "removed_paths": removed,
            "removed_operations": removed_operations,
            "added_paths": added,
            "breaking_changes": contract_breaks,
        }

    @classmethod
    def detect_breaking_changes(
        cls,
        old_spec: Dict[str, Any],
        new_spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Perform contract diffing between two OpenAPI specs (as dicts) to uncover breaking changes.

        Specs that are not objects with a ``paths`` object cannot be compared: the result is
        fail-closed (``is_breaking: True``, ``status: "invalid_input"``) instead of "no changes".
        """
        old_paths, new_paths = cls._paths(old_spec), cls._paths(new_spec)
        if old_paths is None or new_paths is None:
            return {
                "status": "invalid_input",
                "is_breaking": True,
                "recommended_bump": "MAJOR",
                "error": "Both specs must be objects with a 'paths' object; nothing was compared.",
                "breaking_changes_count": 0,
                "breaking_changes": [],
                "non_breaking_changes": [],
            }

        breaking_changes: List[str] = []
        non_breaking_changes: List[str] = []

        for path, old_item in old_paths.items():
            if path not in new_paths:
                breaking_changes.append(f"Removed endpoint: '{path}'")
                continue
            new_ops = cls._operations(new_paths[path])
            for method in cls._operations(old_item):
                if method not in new_ops:
                    breaking_changes.append(f"Removed HTTP method '{method.upper()}' on '{path}'")
        breaking_changes.extend(cls._contract_breaks(old_paths, new_paths))

        # Added new endpoints are non-breaking
        for path in new_paths:
            if path not in old_paths:
                non_breaking_changes.append(f"Added new endpoint: '{path}'")

        is_breaking = len(breaking_changes) > 0
        recommended_bump = "MAJOR" if is_breaking else ("MINOR" if non_breaking_changes else "PATCH")

        return {
            "status": "ok",
            "is_breaking": is_breaking,
            "recommended_bump": recommended_bump,
            "breaking_changes_count": len(breaking_changes),
            "breaking_changes": breaking_changes,
            "non_breaking_changes": non_breaking_changes,
        }

    @classmethod
    def generate_api_diff_changelog(cls, breaking_report: Dict[str, Any]) -> str:
        """Format an API release note changelog in Markdown.

        A report that is missing, malformed or flagged ``invalid_input`` yields an explicit
        "compatibility unknown" note, never a "backwards compatible" claim.
        """
        report = breaking_report if isinstance(breaking_report, dict) else {}
        usable = report.get("status", "ok") == "ok" and isinstance(report.get("is_breaking"), bool)
        bump = report.get("recommended_bump", "PATCH") if usable else "UNKNOWN"
        is_break = report.get("is_breaking", False)

        if not usable:
            compat = "\u2753 COMPATIBILITY UNKNOWN (no valid comparison was supplied)"
        elif is_break:
            compat = "\u26a0\ufe0f BREAKING CHANGES DETECTED"
        else:
            compat = "\u2705 BACKWARDS COMPATIBLE"
        lines = [
            f"# API Contract Changelog (Recommended Bump: `{bump}`)",
            "",
            f"**Compatibility:** {compat}",
            "",
        ]
        if report.get("error"):
            lines.extend([f"> {report['error']}", ""])

        breaking = report.get("breaking_changes", [])
        if breaking:
            lines.extend([
                "## \U0001f6a8 Breaking Changes",
                "",
            ])
            for b in breaking:
                lines.append(f"- :x: {b}")
            lines.append("")

        non_breaking = report.get("non_breaking_changes", [])
        if non_breaking:
            lines.extend([
                "## \U0001f680 Additions & Enhancements",
                "",
            ])
            for nb in non_breaking:
                lines.append(f"- :sparkles: {nb}")
            lines.append("")

        return "\n".join(lines)
