"""Argument validation against a tool's declared schema.

A focused JSON-Schema subset -- exactly what :class:`~aura_core.domain.tools.ParameterSpec` can
express -- implemented in ~150 lines rather than pulling in ``jsonschema``. The subset is closed
by construction: a ``ParameterSpec`` cannot describe a constraint this validator does not
implement, so there is no silently-unenforced keyword.

Three behaviours that matter for tool calling specifically:

* **Unknown keys are rejected**, not ignored. A hallucinated parameter reaching a callable is
  how ``services/agent.py:282``'s ``search_google_drive(**args)`` raises ``TypeError`` inside a
  stream. Rejection produces a message the model can act on.
* **Coercion is narrow and one-directional.** Models frequently emit ``"5"`` where an integer is
  wanted, and refusing that wastes a generation for no safety benefit. Coercing ``"true"`` to a
  boolean is likewise safe. Coercing a string to an object is not, and is not done.
* **Errors name the field and the expectation**, so the model's next attempt is informed.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..domain.errors import ValidationError
from ..domain.tools import ParameterSpec, ToolSpec

__all__ = ["validate_arguments", "coerce_value"]

_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off"}


def coerce_value(spec: ParameterSpec, value: Any) -> Any:
    """Convert a model-supplied value to the declared type, or raise.

    Coercion only widens what is accepted; it never reinterprets structure. ``"12"`` becomes
    ``12`` because a JSON-emitting model quoting a number is a formatting artefact, not a
    semantic difference. A string is never parsed into an object or array -- that would let a
    crafted string become structured input to a tool.
    """
    expected = spec.type

    if value is None:
        if spec.required:
            raise ValidationError(f"parameter '{spec.name}' is required and cannot be null")
        return spec.default

    if expected == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        raise ValidationError(
            f"parameter '{spec.name}' must be a string, got {type(value).__name__}"
        )

    if expected in {"integer", "number"}:
        if isinstance(value, bool):
            # bool is a subclass of int in Python; accepting it here would let True become 1
            # silently, which is never what a numeric parameter meant.
            raise ValidationError(f"parameter '{spec.name}' must be a number, got boolean")
        if isinstance(value, (int, float)):
            number: Any = value
        elif isinstance(value, str):
            try:
                number = float(value) if expected == "number" else int(value.strip(), 10)
            except ValueError as exc:
                raise ValidationError(
                    f"parameter '{spec.name}' must be {expected}, got {value!r}"
                ) from exc
        else:
            raise ValidationError(
                f"parameter '{spec.name}' must be {expected}, got {type(value).__name__}"
            )
        if expected == "integer":
            if isinstance(number, float) and not number.is_integer():
                raise ValidationError(
                    f"parameter '{spec.name}' must be an integer, got {number}"
                )
            number = int(number)
        return number

    if expected == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _TRUE:
                return True
            if lowered in _FALSE:
                return False
        raise ValidationError(
            f"parameter '{spec.name}' must be a boolean, got {value!r}"
        )

    if expected == "array":
        if not isinstance(value, (list, tuple)):
            raise ValidationError(
                f"parameter '{spec.name}' must be an array, got {type(value).__name__}"
            )
        item_spec = ParameterSpec(
            name=f"{spec.name}[]",
            type=spec.item_type or "string",
            description=spec.description,
            required=True,
        )
        return [coerce_value(item_spec, item) for item in value]

    if expected == "object":
        if not isinstance(value, Mapping):
            raise ValidationError(
                f"parameter '{spec.name}' must be an object, got {type(value).__name__}"
            )
        return dict(value)

    raise ValidationError(f"parameter '{spec.name}' has unsupported type {expected!r}")


def _check_constraints(spec: ParameterSpec, value: Any) -> None:
    if spec.enum is not None and value not in spec.enum:
        raise ValidationError(
            f"parameter '{spec.name}' must be one of {list(spec.enum)}, got {value!r}"
        )
    if spec.minimum is not None and isinstance(value, (int, float)) and value < spec.minimum:
        raise ValidationError(f"parameter '{spec.name}' must be >= {spec.minimum}, got {value}")
    if spec.maximum is not None and isinstance(value, (int, float)) and value > spec.maximum:
        raise ValidationError(f"parameter '{spec.name}' must be <= {spec.maximum}, got {value}")
    if spec.max_length is not None and isinstance(value, str) and len(value) > spec.max_length:
        raise ValidationError(
            f"parameter '{spec.name}' must be at most {spec.max_length} characters, "
            f"got {len(value)}"
        )


def validate_arguments(tool: ToolSpec, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and coerce model-supplied arguments.

    Returns a clean dict containing exactly the declared parameters. Raises
    :class:`ValidationError` with a message specific enough for the model to correct itself on
    the next attempt.
    """
    if not isinstance(arguments, Mapping):
        raise ValidationError(
            f"arguments for '{tool.name}' must be an object, got {type(arguments).__name__}"
        )

    # A provider that could not parse the model's argument JSON marks it this way rather than
    # raising mid-stream (see the adapters). Surfacing it here gives the model a correctable
    # message instead of an opaque failure.
    if "_unparsed" in arguments:
        raise ValidationError(
            f"arguments for '{tool.name}' were not valid JSON; re-emit them as a JSON object"
        )

    declared = {p.name: p for p in tool.parameters}
    unknown = sorted(set(arguments) - set(declared))
    if unknown:
        raise ValidationError(
            f"unknown parameter(s) for '{tool.name}': {unknown}",
            details={"unknown": unknown, "accepted": sorted(declared)},
        )

    missing = [
        name for name, spec in declared.items()
        if spec.required and (name not in arguments or arguments[name] is None)
    ]
    if missing:
        raise ValidationError(
            f"missing required parameter(s) for '{tool.name}': {sorted(missing)}",
            details={"missing": sorted(missing)},
        )

    validated: dict[str, Any] = {}
    for name, spec in declared.items():
        if name not in arguments:
            if spec.default is not None:
                validated[name] = spec.default
            continue
        value = coerce_value(spec, arguments[name])
        if value is None and not spec.required:
            continue
        _check_constraints(spec, value)
        validated[name] = value

    return validated
