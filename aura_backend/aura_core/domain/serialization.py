"""Strict JSON codec, canonical form, digest and JSON Schema for frozen domain dataclasses.

The adaptation layer exchanges its objects (Project Twin, Project Blueprint) as stored JSON and
binds approvals to their digest (ADR-024). Three properties have to hold for that to be sound,
and all three come from one type walk over the dataclass annotations, so they cannot disagree:

* **Strict decode.** Unknown keys, missing required keys, wrong types (``True`` is not an
  integer) and invariant violations raised by ``__post_init__`` all become a
  :class:`~aura_core.domain.errors.ValidationError` naming the JSON path. A tampered or
  hand-edited blueprint is rejected, never partially loaded.
* **Canonical encode.** Keys sorted, no insignificant whitespace, sets emitted sorted, so the same
  value always produces the same bytes and therefore the same digest.
* **Generated schema.** :func:`json_schema` derives the published JSON Schema from the same
  annotations the decoder enforces; ``docs/schemas/*.json`` is its output, checked by a test.
* **Additive evolution.** A field added after values were already stored and digested is
  declared with ``field(default=..., metadata=ADDED_FIELD)``: it is left out of the encoding while
  it holds its default, so every value written before the field existed keeps its exact bytes and
  digest (an approval bound to that digest still binds), and decodes as before.

Supported annotations: ``str``, ``int``, ``float``, ``bool``, ``Any`` (JSON value), enums (string
enums by value, integer enums by lower-case name), frozen dataclasses, ``UntrustedText``,
``tuple[X, ...]``, ``frozenset[X]``, ``Mapping[str, X]`` and ``X | None``.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
import types
import typing
from collections.abc import Mapping as AbcMapping
from functools import lru_cache
from typing import Any, Mapping, TypeVar, Union, get_args, get_origin, get_type_hints

from .errors import ValidationError
from .untrusted import MAX_UNTRUSTED_CHARS, UntrustedText

__all__ = [
    "ADDED_FIELD",
    "to_data",
    "from_data",
    "canonical_json",
    "content_digest",
    "json_schema",
]

T = TypeVar("T")

#: ``dataclasses.field`` metadata for a field added to an already-stored type (see the module
#: docstring): omitted from the encoding while equal to its default.
ADDED_FIELD: Mapping[str, Any] = {"omit_default": True}


# --------------------------------------------------------------------------------------- encode


def to_data(value: Any) -> Any:
    """Encode a domain value as plain JSON data (dicts, lists, str, int, float, bool, None)."""
    if isinstance(value, UntrustedText):
        return value.raw()
    if isinstance(value, enum.Enum):
        return value.name.lower() if isinstance(value, int) else value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_data(getattr(value, f.name)) for f in _fields(type(value))
                if not _omitted(f, getattr(value, f.name))}
    if isinstance(value, frozenset):
        return sorted((to_data(v) for v in value), key=canonical_json)
    if isinstance(value, (tuple, list)):
        return [to_data(v) for v in value]
    if isinstance(value, AbcMapping):
        return {str(k): to_data(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError("non-finite numbers cannot be serialised", details={"value": repr(value)})
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValidationError("value is not serialisable", details={"type": type(value).__name__})


def canonical_json(data: Any) -> str:
    """The canonical text of already-encoded JSON data (see :func:`to_data`)."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_digest(value: Any) -> str:
    """``sha256:<hex>`` over the canonical JSON of ``value`` (a domain value or encoded data)."""
    data = value if _is_plain(value) else to_data(value)
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def _is_plain(value: Any) -> bool:
    return value is None or isinstance(value, (dict, list, str, int, float, bool))


# --------------------------------------------------------------------------------------- decode


def from_data(cls: type[T], data: Any, *, path: str = "$") -> T:
    """Decode ``data`` into ``cls``, raising :class:`ValidationError` with the failing path."""
    return _decode(cls, data, path)


def _decode(tp: Any, data: Any, path: str) -> Any:
    if tp is Any:
        _check_json_value(data, path)
        return data
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        options = get_args(tp)
        if data is None:
            if type(None) in options:
                return None
            raise _error(path, "must not be null")
        non_null = [o for o in options if o is not type(None)]
        if len(non_null) != 1:
            raise TypeError(f"unsupported union {tp!r}")  # programming error, not bad data
        return _decode(non_null[0], data, path)
    if origin is tuple:
        item_tp = _homogeneous_item(tp)
        if not isinstance(data, list):
            raise _error(path, "must be an array")
        return tuple(_decode(item_tp, item, f"{path}[{i}]") for i, item in enumerate(data))
    if origin is frozenset:
        (item_tp,) = get_args(tp)
        if not isinstance(data, list):
            raise _error(path, "must be an array")
        items = [_decode(item_tp, item, f"{path}[{i}]") for i, item in enumerate(data)]
        result = frozenset(items)
        if len(result) != len(items):
            raise _error(path, "must not contain duplicates")
        return result
    if origin in (AbcMapping, dict, typing.Mapping):
        key_tp, value_tp = get_args(tp)
        if key_tp is not str:
            raise TypeError(f"only str-keyed mappings are supported, got {tp!r}")
        if not isinstance(data, dict):
            raise _error(path, "must be an object")
        return {k: _decode(value_tp, v, f"{path}.{k}") for k, v in data.items()}
    if tp is UntrustedText:
        if not isinstance(data, str):
            raise _error(path, "must be a string")
        return _construct(UntrustedText, {"_value": data}, path)
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return _decode_enum(tp, data, path)
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return _decode_dataclass(tp, data, path)
    if tp is bool:
        if not isinstance(data, bool):
            raise _error(path, "must be a boolean")
        return data
    if tp is int:
        if isinstance(data, bool) or not isinstance(data, int):
            raise _error(path, "must be an integer")
        return data
    if tp is float:
        if isinstance(data, bool) or not isinstance(data, (int, float)) or not math.isfinite(data):
            raise _error(path, "must be a finite number")
        return float(data)
    if tp is str:
        if not isinstance(data, str):
            raise _error(path, "must be a string")
        return data
    raise TypeError(f"unsupported annotation {tp!r} at {path}")


def _decode_enum(tp: type[enum.Enum], data: Any, path: str) -> enum.Enum:
    if issubclass(tp, int):
        names = {m.name.lower(): m for m in tp}
        if isinstance(data, str) and data in names:
            return names[data]
        raise _error(path, f"must be one of {sorted(names)}")
    for member in tp:
        if member.value == data:
            return member
    raise _error(path, f"must be one of {sorted(str(m.value) for m in tp)}")


def _decode_dataclass(tp: type, data: Any, path: str) -> Any:
    if not isinstance(data, dict):
        raise _error(path, "must be an object")
    fields = _fields(tp)
    known = {f.name for f in fields}
    unknown = sorted(set(data) - known)
    if unknown:
        raise _error(path, "has unknown keys", unknown=unknown)
    hints = _hints(tp)
    kwargs: dict[str, Any] = {}
    for f in fields:
        if f.name in data:
            kwargs[f.name] = _decode(hints[f.name], data[f.name], f"{path}.{f.name}")
        elif not _has_default(f):
            raise _error(f"{path}.{f.name}", "is required")
    return _construct(tp, kwargs, path)


def _construct(tp: type, kwargs: Mapping[str, Any], path: str) -> Any:
    try:
        return tp(**kwargs)
    except ValidationError as exc:
        raise ValidationError(str(exc), details={"path": path, **dict(exc.details)}) from exc
    except (ValueError, TypeError) as exc:
        raise _error(path, str(exc)) from exc


def _check_json_value(data: Any, path: str) -> None:
    if data is None or isinstance(data, (str, bool, int)):
        return
    if isinstance(data, float):
        if not math.isfinite(data):
            raise _error(path, "must be a finite number")
        return
    if isinstance(data, list):
        for i, item in enumerate(data):
            _check_json_value(item, f"{path}[{i}]")
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if not isinstance(k, str):
                raise _error(path, "object keys must be strings")
            _check_json_value(v, f"{path}.{k}")
        return
    raise _error(path, "must be a JSON value")


def _error(path: str, problem: str, **extra: Any) -> ValidationError:
    return ValidationError(f"{path} {problem}", details={"path": path, **extra})


# --------------------------------------------------------------------------------------- schema


def json_schema(root: type, *, schema_id: str, title: str) -> dict[str, Any]:
    """A JSON Schema (2020-12) for ``root``, derived from the same annotations the decoder uses.

    Value-level invariants enforced in ``__post_init__`` (non-empty evidence, references that
    resolve, ...) are not expressible here; the decoder remains the authority, the schema is the
    published shape for tooling and review.
    """
    defs: dict[str, Any] = {}
    root_ref = _schema_for(root, defs)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        "title": title,
        **root_ref,
        "$defs": {k: defs[k] for k in sorted(defs)},
    }


def _schema_for(tp: Any, defs: dict[str, Any]) -> dict[str, Any]:
    if tp is Any:
        return {}
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        non_null = [o for o in get_args(tp) if o is not type(None)]
        return {"anyOf": [_schema_for(non_null[0], defs), {"type": "null"}]}
    if origin is tuple:
        return {"type": "array", "items": _schema_for(_homogeneous_item(tp), defs)}
    if origin is frozenset:
        return {"type": "array", "uniqueItems": True, "items": _schema_for(get_args(tp)[0], defs)}
    if origin in (AbcMapping, dict, typing.Mapping):
        return {"type": "object", "additionalProperties": _schema_for(get_args(tp)[1], defs)}
    if tp is UntrustedText:
        return {"type": "string", "maxLength": MAX_UNTRUSTED_CHARS}
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        if issubclass(tp, int):
            return {"type": "string", "enum": [m.name.lower() for m in tp]}
        return {"type": "string", "enum": [m.value for m in tp]}
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        name = tp.__name__
        if name not in defs:
            defs[name] = {}  # placeholder breaks recursion
            hints = _hints(tp)
            fields = _fields(tp)
            schema: dict[str, Any] = {
                "type": "object",
                "additionalProperties": False,
                "properties": {f.name: _schema_for(hints[f.name], defs) for f in fields},
            }
            required = [f.name for f in fields if not _has_default(f)]
            if required:
                schema["required"] = required
            doc = (tp.__doc__ or "").strip().split("\n\n")[0]
            if doc and not doc.startswith(f"{name}("):
                schema["description"] = " ".join(doc.split())
            defs[name] = schema
        return {"$ref": f"#/$defs/{name}"}
    scalar = {bool: "boolean", int: "integer", float: "number", str: "string"}
    if tp in scalar:
        return {"type": scalar[tp]}
    raise TypeError(f"unsupported annotation {tp!r}")


# --------------------------------------------------------------------------------------- helpers


@lru_cache(maxsize=None)
def _fields(tp: type) -> tuple[dataclasses.Field, ...]:
    return tuple(f for f in dataclasses.fields(tp) if f.init)


@lru_cache(maxsize=None)
def _hints(tp: type) -> dict[str, Any]:
    return get_type_hints(tp)


def _omitted(f: dataclasses.Field, value: Any) -> bool:
    if not f.metadata.get("omit_default"):
        return False
    default = f.default if f.default is not dataclasses.MISSING else f.default_factory()  # type: ignore[misc]
    return value == default


def _has_default(f: dataclasses.Field) -> bool:
    return f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING


def _homogeneous_item(tp: Any) -> Any:
    args = get_args(tp)
    if len(args) != 2 or args[1] is not Ellipsis:
        raise TypeError(f"only homogeneous tuple[X, ...] is supported, got {tp!r}")
    return args[0]
