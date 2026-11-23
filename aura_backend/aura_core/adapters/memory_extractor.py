"""A deterministic, dependency-free :class:`StructuredExtractor` (ADR-038).

Not a mock. It reads ``label: value`` lines the way a person would and runs every answer through the
same :func:`~aura_core.domain.extraction.classify_answer` the Cloudflare adapter uses, so a test
exercises the real confidence rule on real behaviour with no network and no model. It is what lets
the default ``pytest`` run cover the ``extract_structured`` tool.

* A field named ``invoice_number`` is read from a line such as ``Invoice number: INV-2291``
  (``_``, ``-`` and ``.`` in the name match a space, an underscore, a dot or a dash).
* A field with no matching line is a confident ``null``: the text does not say.
* ``fixed`` overrides the reader for named fields with ``(value, confidence)``, which is how a test
  drives a low-confidence or malformed answer through the tool.
* ``fail_next`` makes the next call raise, to exercise the caller's error handling.

``calls`` records only the character count of each request, never its text.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..domain.errors import ExtractionError
from ..domain.extraction import (
    ExtractedField,
    ExtractionRequest,
    ExtractionResult,
    FieldSpec,
    FieldType,
    classify_answer,
)
from ..ports.llm import HealthStatus

__all__ = ["InMemoryStructuredExtractor"]

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_YES = {"yes", "true", "y"}
_NO = {"no", "false", "n"}


def _label(name: str) -> str:
    parts = [re.escape(p) for p in re.split(r"[_\-.]+", name) if p]
    return r"[ _\-.]*".join(parts)


class InMemoryStructuredExtractor:
    def __init__(
        self,
        *,
        confidence: float = 0.95,
        fixed: Mapping[str, tuple[Any, Any]] | None = None,
        model: str = "memory-extractor-1",
    ) -> None:
        self._confidence = confidence
        self._fixed = dict(fixed or {})
        self._model = model
        self.calls: list[int] = []
        self.fail_next: ExtractionError | None = None

    @property
    def name(self) -> str:
        return "memory"

    @property
    def model_id(self) -> str:
        return self._model

    async def health(self) -> HealthStatus:
        return HealthStatus(True, "in-memory")

    async def close(self) -> None:
        return None

    async def extract(self, request: ExtractionRequest, *, min_confidence: float) -> ExtractionResult:
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        self.calls.append(request.character_count)
        fields: dict[str, ExtractedField] = {}
        for spec in request.fields:
            if spec.name in self._fixed:
                value, confidence = self._fixed[spec.name]
                span = None
            else:
                value, confidence, span = self._read(spec, request.text)
            fields[spec.name] = classify_answer(
                spec, value=value, confidence=confidence, min_confidence=min_confidence, span=span
            )
        return ExtractionResult(fields=fields, model=self._model)

    def _read(self, spec: FieldSpec, text: str) -> tuple[Any, float, tuple[int, int] | None]:
        match = re.search(
            rf"(?im)^[ \t]*{_label(spec.name)}[ \t]*[:=][ \t]*(?P<v>[^\r\n]*?)[ \t]*$", text
        )
        if match is None or not match.group("v"):
            return None, 0.99, None
        raw, span = match.group("v"), match.span("v")
        return self._convert(spec, raw), self._confidence, (span if spec.type in (FieldType.TEXT, FieldType.ENUM) else None)

    @staticmethod
    def _convert(spec: FieldSpec, raw: str) -> Any:
        kind = spec.type
        if kind is FieldType.NUMBER:
            found = _NUMBER.search(raw)
            if not found:
                return raw  # unparseable: handed on as-is so the type check reports it
            number = float(found.group().replace(",", ""))
            return int(number) if number == int(number) else number
        if kind is FieldType.DATE:
            found = _ISO_DATE.search(raw)
            return found.group() if found else raw
        if kind is FieldType.BOOLEAN:
            word = raw.strip().lower()
            return True if word in _YES else False if word in _NO else raw
        if kind is FieldType.ENUM:
            lowered = {v.lower(): v for v in spec.enum_values}
            return lowered.get(raw.strip().lower(), raw)
        if kind is FieldType.TEXT_LIST:
            return [item.strip() for item in re.split(r"[,;]", raw) if item.strip()]
        return raw
