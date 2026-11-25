"""Typed field extraction: the value objects behind ``extract_structured`` (ADR-038).

A caller declares fields (:class:`FieldSpec`) and a body of text (:class:`ExtractionRequest`); an
extractor returns one :class:`ExtractedField` per field inside an :class:`ExtractionResult`.

What makes this more than a dict of answers is the rule in :func:`classify_answer`, shared by every
adapter so the Cloudflare one and the in-memory one cannot disagree about it:

* a value is only returned when the service was confident in it (``min_confidence``);
* a low-confidence value is **withheld**, not passed through. Its raw guess is kept beside it,
  clearly labelled, so a caller can show it to a person but cannot mistake it for an answer;
* an answer that does not fit the field's declared type is ``invalid``, never coerced.

The first probe of the real service (2026-11-22) is the reason: it returned ``2026-10-12`` for a
due date that was ``2026-10-02``, at a confidence of 0.096. Four other fields in the same response
were right at 0.98 and above. Confidence is the only thing that separates the two.

Stdlib only, like the rest of this package.
"""

from __future__ import annotations

import datetime as _dt
import enum
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .errors import ValidationError

__all__ = [
    "MAX_FIELDS",
    "MAX_INSTRUCTIONS_CHARS",
    "MAX_ENUM_VALUES",
    "MAX_ENUM_VALUE_CHARS",
    "MAX_LIST_ITEMS",
    "MAX_TEXT_VALUE_CHARS",
    "FieldType",
    "FieldStatus",
    "FieldSpec",
    "ExtractionRequest",
    "ExtractedField",
    "ExtractionResult",
    "check_value",
    "classify_answer",
]

#: The most fields one request may declare. The schema is most of the input cost (1,809 of 2,541
#: input tokens for five fields in the first probe), so this bounds spend as much as it bounds shape.
MAX_FIELDS = 20
#: A longer ``instructions`` string is refused rather than trimmed: silently cutting an instruction
#: changes what the field means.
MAX_INSTRUCTIONS_CHARS = 300
MAX_ENUM_VALUES = 50
MAX_ENUM_VALUE_CHARS = 100
MAX_LIST_ITEMS = 100
#: The longest text value (or list item) accepted back. Longer is ``invalid``: a field is meant to
#: hold a datum, and a paragraph means the service answered something else.
MAX_TEXT_VALUE_CHARS = 2000

# The service's own rule for a field name, so a name that passes here is never refused upstream.
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_DECIMALS = 6
_MAX_UNIT_CHARS = 20
_SPEC_KEYS = frozenset({"type", "instructions", "enum", "integer", "decimals", "unit"})


class FieldType(str, enum.Enum):
    """The field types v1 exposes. ``estimate``, ``forecast`` and ``compute`` are not offered."""

    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    ENUM = "enum"
    TEXT_LIST = "text_list"

    def __str__(self) -> str:
        return self.value


class FieldStatus(str, enum.Enum):
    OK = "ok"                          # a value, and the service was confident in it
    NULL = "null"                      # the input does not determine this field
    LOW_CONFIDENCE = "low_confidence"  # the service answered but was not confident: value withheld
    INVALID = "invalid"                # the answer was malformed or did not fit the field's type

    def __str__(self) -> str:
        return self.value


def _bad(message: str, **details: Any) -> ValidationError:
    return ValidationError(message, details=details)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One field to extract. Validated on construction, so a spec that exists is one the service
    will accept."""

    name: str
    type: FieldType
    instructions: str = ""
    enum_values: tuple[str, ...] = ()
    integer: bool = False
    decimals: int | None = None
    unit: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.match(self.name):
            raise _bad(
                "a field name must start with a letter or underscore and use only letters, digits, "
                "'_', '.' or '-' (at most 64 characters)",
                field=str(self.name)[:64],
            )
        try:
            object.__setattr__(self, "type", FieldType(self.type))
        except ValueError:
            raise _bad(
                f"field {self.name!r}: unsupported type {str(self.type)[:30]!r}",
                field=self.name, supported=[t.value for t in FieldType],
            ) from None
        if not isinstance(self.instructions, str) or len(self.instructions) > MAX_INSTRUCTIONS_CHARS:
            raise _bad(
                f"field {self.name!r}: instructions must be text of at most "
                f"{MAX_INSTRUCTIONS_CHARS} characters",
                field=self.name,
            )
        values = tuple(self.enum_values)
        object.__setattr__(self, "enum_values", values)
        if self.type is FieldType.ENUM:
            if not 1 <= len(values) <= MAX_ENUM_VALUES:
                raise _bad(
                    f"field {self.name!r}: an enum needs 1 to {MAX_ENUM_VALUES} allowed values",
                    field=self.name,
                )
            if len(set(values)) != len(values) or not all(
                isinstance(v, str) and v and len(v) <= MAX_ENUM_VALUE_CHARS for v in values
            ):
                raise _bad(
                    f"field {self.name!r}: enum values must be distinct, non-empty text of at most "
                    f"{MAX_ENUM_VALUE_CHARS} characters",
                    field=self.name,
                )
        elif values:
            raise _bad(f"field {self.name!r}: only an enum field takes enum values", field=self.name)
        if self.type is not FieldType.NUMBER:
            if self.integer or self.decimals is not None or self.unit:
                raise _bad(
                    f"field {self.name!r}: integer, decimals and unit apply to number fields only",
                    field=self.name,
                )
        else:
            if not isinstance(self.integer, bool):
                raise _bad(f"field {self.name!r}: integer must be true or false", field=self.name)
            if self.decimals is not None:
                if (
                    isinstance(self.decimals, bool)
                    or not isinstance(self.decimals, int)
                    or not 0 <= self.decimals <= _MAX_DECIMALS
                    or self.integer
                ):
                    raise _bad(
                        f"field {self.name!r}: decimals must be 0 to {_MAX_DECIMALS}, and cannot be "
                        "combined with integer",
                        field=self.name,
                    )
            if not isinstance(self.unit, str) or len(self.unit) > _MAX_UNIT_CHARS:
                raise _bad(
                    f"field {self.name!r}: unit must be text of at most {_MAX_UNIT_CHARS} characters",
                    field=self.name,
                )

    @classmethod
    def from_mapping(cls, name: str, raw: Mapping[str, Any]) -> "FieldSpec":
        """Build a spec from the JSON a caller (often a model) supplied.

        Unknown keys are refused rather than ignored: a misspelt ``instructions`` silently dropped
        would return a plausible, wrong answer.
        """
        if not isinstance(raw, Mapping):
            raise _bad(f"field {str(name)[:64]!r}: the spec must be an object", field=str(name)[:64])
        unknown = sorted(str(k) for k in raw if k not in _SPEC_KEYS)
        if unknown:
            raise _bad(
                f"field {str(name)[:64]!r}: unknown key(s) {unknown[:5]}",
                field=str(name)[:64], allowed=sorted(_SPEC_KEYS),
            )
        if "type" not in raw:
            raise _bad(f"field {str(name)[:64]!r}: a type is required", field=str(name)[:64])
        enum_values = raw.get("enum") or ()
        if not isinstance(enum_values, (list, tuple)):
            raise _bad(f"field {str(name)[:64]!r}: enum must be a list", field=str(name)[:64])
        return cls(
            name=name,
            type=raw["type"],
            instructions=raw.get("instructions") or "",
            enum_values=tuple(enum_values),
            integer=bool(raw.get("integer", False)),
            decimals=raw.get("decimals"),
            unit=raw.get("unit") or "",
        )


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    """The text to read and the fields to fill. The length cap on ``text`` is a deployment setting
    (``AURA_EXTRACT_MAX_CHARS``) enforced where it is known, not a domain constant."""

    text: str
    fields: tuple[FieldSpec, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise _bad("text must be non-empty")
        specs = tuple(self.fields)
        object.__setattr__(self, "fields", specs)
        if not 1 <= len(specs) <= MAX_FIELDS:
            raise _bad(f"declare between 1 and {MAX_FIELDS} fields", count=len(specs))
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise _bad("field names must be unique")

    @property
    def character_count(self) -> int:
        return len(self.text)

    def spec_for(self, name: str) -> FieldSpec:
        for spec in self.fields:
            if spec.name == name:
                return spec
        raise KeyError(name)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_value(spec: FieldSpec, value: Any) -> str | None:
    """Why ``value`` does not fit ``spec``, as a low-cardinality reason, or ``None`` if it does.

    Nothing is coerced. ``"1080"`` for a number field is a mismatch, not 1080: the service is asked
    for typed answers, so a string where a number belongs means something upstream went wrong.
    """
    kind = spec.type
    if kind is FieldType.TEXT:
        if not isinstance(value, str):
            return "type_mismatch"
        return "too_long" if len(value) > MAX_TEXT_VALUE_CHARS else None
    if kind is FieldType.NUMBER:
        if not _finite_number(value):
            return "not_finite" if isinstance(value, float) else "type_mismatch"
        if spec.integer and value != int(value):
            return "not_integer"
        return None
    if kind is FieldType.DATE:
        if not isinstance(value, str) or not _ISO_DATE.match(value):
            return "not_a_date"
        try:
            _dt.date.fromisoformat(value)
        except ValueError:
            return "not_a_date"
        return None
    if kind is FieldType.BOOLEAN:
        return None if isinstance(value, bool) else "type_mismatch"
    if kind is FieldType.ENUM:
        if not isinstance(value, str):
            return "type_mismatch"
        return None if value in spec.enum_values else "not_in_enum"
    # TEXT_LIST
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        return "type_mismatch"
    if len(value) > MAX_LIST_ITEMS:
        return "too_many_items"
    if any(len(v) > MAX_TEXT_VALUE_CHARS for v in value):
        return "too_long"
    return None


@dataclass(frozen=True, slots=True)
class ExtractedField:
    name: str
    status: FieldStatus
    value: Any = None
    confidence: float | None = None
    span: tuple[int, int] | None = None
    guess: Any = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "value": self.value,
            "status": self.status.value,
            "confidence": None if self.confidence is None else round(self.confidence, 4),
            "span": list(self.span) if self.span else None,
        }
        if self.status is FieldStatus.LOW_CONFIDENCE:
            out["guess"] = self.guess
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    fields: Mapping[str, ExtractedField]
    truncated: bool = False
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "truncated": self.truncated,
            "answers": {name: f.as_dict() for name, f in self.fields.items()},
            "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens},
        }


def _clean_confidence(confidence: Any) -> float | None:
    if not _finite_number(confidence) or not 0.0 <= float(confidence) <= 1.0:
        return None
    return float(confidence)


def _clean_span(span: Any) -> tuple[int, int] | None:
    if (
        isinstance(span, (list, tuple))
        and len(span) == 2
        and all(isinstance(i, int) and not isinstance(i, bool) and i >= 0 for i in span)
        and span[0] <= span[1]
    ):
        return (span[0], span[1])
    return None


def classify_answer(
    spec: FieldSpec,
    *,
    value: Any,
    confidence: Any,
    min_confidence: float,
    valid: bool = True,
    span: Any = None,
) -> ExtractedField:
    """Turn one raw answer into an :class:`ExtractedField`, applying ADR-038 §4.

    Order matters: an invalid answer is invalid whatever its confidence; a null is a null; a value
    that does not fit the type is invalid and its content is not echoed back; only then does
    confidence decide between ``ok`` and ``low_confidence``. A missing, non-numeric, non-finite or
    out-of-range confidence counts as low, because an unverifiable answer is not a confident one.

    A confidence exactly equal to ``min_confidence`` is accepted.
    """
    if not _finite_number(min_confidence) or not 0.0 <= float(min_confidence) <= 1.0:
        raise _bad("min_confidence must be a number between 0 and 1")
    conf = _clean_confidence(confidence)
    if not valid:
        return ExtractedField(spec.name, FieldStatus.INVALID, reason="invalid_output")
    if value is None:
        return ExtractedField(spec.name, FieldStatus.NULL, confidence=conf)
    problem = check_value(spec, value)
    if problem:
        return ExtractedField(spec.name, FieldStatus.INVALID, confidence=conf, reason=problem)
    clean_span = _clean_span(span)
    if conf is None:
        return ExtractedField(
            spec.name, FieldStatus.LOW_CONFIDENCE, span=clean_span, guess=value, reason="no_confidence"
        )
    if conf < float(min_confidence):
        return ExtractedField(
            spec.name, FieldStatus.LOW_CONFIDENCE, confidence=conf, span=clean_span, guess=value,
            reason="below_threshold",
        )
    return ExtractedField(spec.name, FieldStatus.OK, value=value, confidence=conf, span=clean_span)
