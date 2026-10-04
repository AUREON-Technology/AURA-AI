"""The JSON-Schema subset tools declare for their results (ADR-025, AP6).

A tool's ``output_schema`` says what its result looks like, so a generated tool's result can be
checked after it runs and a UI view can bind to named fields. Only a small, closed subset of
JSON Schema is accepted -- enough to describe rows and records, small enough to validate here
without a dependency (the domain layer is stdlib-only, ADR-001):

* ``type``: one of ``object array string integer number boolean null``, or a list of them
  (``["string", "null"]`` for a nullable column);
* ``properties`` / ``required`` / ``additionalProperties`` (``bool``) on objects;
* ``items`` and ``maxItems`` on arrays;
* ``enum`` (scalars), ``format`` (informational) and ``description`` anywhere.

Anything else -- ``$ref``, ``oneOf``, ``pattern``, conditionals -- is refused at declaration
time rather than ignored, so a schema never claims a constraint nobody checks.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .errors import ValidationError

__all__ = ["check_output_schema", "output_errors", "SCHEMA_TYPES"]

SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "maxItems", "enum",
    "format", "description",
})
_MAX_DEPTH = 6
_MAX_ERRORS = 5


def _types(schema: Mapping[str, Any]) -> tuple[str, ...]:
    declared = schema.get("type")
    return tuple(declared) if isinstance(declared, (list, tuple)) else (declared,)


def check_output_schema(schema: Any, *, path: str = "$", depth: int = 0) -> None:
    """Raise :class:`ValidationError` unless ``schema`` is inside the supported subset."""
    if depth > _MAX_DEPTH:
        raise ValidationError("output schema is nested too deeply", details={"path": path})
    if not isinstance(schema, Mapping):
        raise ValidationError("an output schema node must be an object", details={"path": path})
    unknown = sorted(set(schema) - _KEYWORDS)
    if unknown:
        raise ValidationError("unsupported output schema keywords", details={"path": path, "keywords": unknown})
    types = _types(schema)
    if not types or any(t not in SCHEMA_TYPES for t in types) or len(set(types)) != len(types):
        raise ValidationError("output schema type must be a JSON type or a list of them", details={"path": path})
    if "properties" in schema:
        if "object" not in types or not isinstance(schema["properties"], Mapping):
            raise ValidationError("properties need type object", details={"path": path})
        for name, sub in schema["properties"].items():
            check_output_schema(sub, path=f"{path}.{name}", depth=depth + 1)
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, (list, tuple)) or not all(isinstance(r, str) for r in required):
            raise ValidationError("required must be a list of names", details={"path": path})
        missing = sorted(set(required) - set(schema.get("properties") or {}))
        if missing:
            raise ValidationError("required names undeclared properties", details={"path": path, "names": missing})
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise ValidationError("additionalProperties must be a boolean", details={"path": path})
    if "items" in schema:
        if "array" not in types:
            raise ValidationError("items need type array", details={"path": path})
        check_output_schema(schema["items"], path=f"{path}[]", depth=depth + 1)
    if "maxItems" in schema:
        limit = schema["maxItems"]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValidationError("maxItems must be a non-negative integer", details={"path": path})
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, (list, tuple)) or not values or not all(
            v is None or isinstance(v, (str, int, float, bool)) for v in values
        ):
            raise ValidationError("enum must be a non-empty list of scalars", details={"path": path})
    for key in ("format", "description"):
        if key in schema and not isinstance(schema[key], str):
            raise ValidationError(f"{key} must be a string", details={"path": path})


def _matches_type(value: Any, kind: str) -> bool:
    if kind == "null":
        return value is None
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if kind == "string":
        return isinstance(value, str)
    if kind == "array":
        return isinstance(value, list)
    return isinstance(value, dict)


def output_errors(schema: Mapping[str, Any], value: Any) -> list[str]:
    """Where ``value`` breaks ``schema`` (already checked by :func:`check_output_schema`).

    Returns at most a handful of paths with a short reason; never the offending values, which
    are tool output and may be sensitive.
    """
    errors: list[str] = []

    def walk(node: Mapping[str, Any], item: Any, path: str) -> None:
        if len(errors) >= _MAX_ERRORS:
            return
        types = _types(node)
        if not any(_matches_type(item, t) for t in types):
            errors.append(f"{path}: expected {'|'.join(types)}")
            return
        if "enum" in node and item not in node["enum"]:
            errors.append(f"{path}: not one of the declared values")
            return
        if isinstance(item, dict):
            props = node.get("properties") or {}
            for name in node.get("required") or ():
                if name not in item:
                    errors.append(f"{path}.{name}: required")
            if node.get("additionalProperties") is False:
                for name in sorted(set(item) - set(props)):
                    errors.append(f"{path}.{name}: not declared")
            for name, sub in props.items():
                if name in item:
                    walk(sub, item[name], f"{path}.{name}")
        elif isinstance(item, list):
            limit = node.get("maxItems")
            if isinstance(limit, int) and len(item) > limit:
                errors.append(f"{path}: more than {limit} items")
            sub = node.get("items")
            if isinstance(sub, Mapping):
                for index, element in enumerate(item):
                    walk(sub, element, f"{path}[{index}]")

    walk(schema, value, "$")
    return errors[:_MAX_ERRORS]
