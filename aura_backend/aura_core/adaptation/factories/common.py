"""Shared helpers for the blueprint factories (AP6): twin lookups, naming and JSON types."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from ...domain.ausm import AusmElement, ElementKind, ProjectTwin
from ...domain.connectors import ConnectorKind

__all__ = ["TwinView", "slug", "unique_slug", "json_type", "param_type", "MAX_COLUMNS"]

#: Projected columns per generated tool: enough for a record view, small enough for a prompt.
MAX_COLUMNS = 24
_NUMERIC = re.compile(r"^(numeric|decimal|real|double precision|money)\b")
_INTEGER = re.compile(r"^(smallint|integer|bigint)\b")
_TEXTUAL = re.compile(r"^(text|character|varchar|char|citext|uuid|name)\b")


def slug(*parts: str, limit: int = 48) -> str:
    """Lower-case snake_case from arbitrary identifiers; long names keep a stable hash suffix."""
    raw = "_".join(p for p in parts if p)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"
    if not text[0].isalpha():
        text = f"x_{text}"
    if len(text) <= limit:
        return text
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]
    return f"{text[:limit - 7].rstrip('_')}_{digest}"


def unique_slug(base: str, taken: set[str], *, limit: int = 48) -> str:
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"_{n}"
        candidate = f"{base[:limit - len(suffix)]}{suffix}"
        n += 1
    taken.add(candidate)
    return candidate


def json_type(attribute: AusmElement) -> dict[str, Any]:
    """The output-schema node for one attribute's values (as the read path will return them)."""
    data_type = str(attribute.properties.get("data_type") or "").lower()
    fmt = str(attribute.properties.get("format") or "")
    if _INTEGER.match(data_type) or data_type == "integer":
        node: dict[str, Any] = {"type": "integer"}
    elif _NUMERIC.match(data_type) or data_type == "number":
        node = {"type": "number"}
    elif data_type in ("boolean", "bool"):
        node = {"type": "boolean"}
    elif data_type.startswith("timestamp") or fmt == "date-time":
        node = {"type": "string", "format": "date-time"}
    elif data_type == "date" or fmt == "date":
        node = {"type": "string", "format": "date"}
    elif data_type == "array":
        node = {"type": "array"}
    elif data_type == "object":
        node = {"type": "object"}
    else:
        node = {"type": "string"}
    if attribute.properties.get("nullable", True) or not attribute.properties.get("required", True):
        node["type"] = [node["type"], "null"]
    return node


def param_type(attribute: AusmElement) -> str:
    data_type = str(attribute.properties.get("data_type") or "").lower()
    if _INTEGER.match(data_type) or data_type == "integer":
        return "integer"
    if _NUMERIC.match(data_type) or data_type == "number":
        return "number"
    if data_type in ("boolean", "bool"):
        return "boolean"
    return "string"


def is_textual(attribute: AusmElement) -> bool:
    data_type = str(attribute.properties.get("data_type") or "").lower()
    return bool(_TEXTUAL.match(data_type)) or data_type == "string" or "enum" in str(attribute.properties.get("value_set"))


@dataclass
class TwinView:
    """Indexes over one twin, shared by the factories."""

    twin: ProjectTwin
    kinds: dict[str, ConnectorKind]

    def __post_init__(self) -> None:
        self.by_id = {e.id: e for e in self.twin.elements}
        self.children: dict[str, list[AusmElement]] = {}
        for element in self.twin.elements:
            if element.parent:
                self.children.setdefault(element.parent, []).append(element)

    def get(self, element_id: str) -> AusmElement | None:
        return self.by_id.get(element_id)

    def kind_of(self, element: AusmElement) -> ConnectorKind | None:
        return self.kinds.get(element.source_id)

    def identifier(self, element: AusmElement) -> str:
        value = element.properties.get("identifier")
        return value if isinstance(value, str) else element.locator

    def attributes(self, entity_id: str) -> list[AusmElement]:
        found = [e for e in self.children.get(entity_id, []) if e.kind is ElementKind.ATTRIBUTE]
        return sorted(found, key=lambda e: (e.properties.get("position") or 0, e.id))

    def primary_key(self, entity_id: str) -> list[AusmElement]:
        return [a for a in self.attributes(entity_id) if a.properties.get("primary_key")]

    def states(self, attribute_id: str) -> list[AusmElement]:
        found = [e for e in self.children.get(attribute_id, []) if e.kind is ElementKind.STATE]
        return sorted(found, key=lambda e: e.properties.get("ordinal") or 0)

    def of_kind(self, kind: ElementKind) -> Iterable[AusmElement]:
        return self.twin.of_kind(kind)
