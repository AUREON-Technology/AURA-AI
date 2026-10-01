"""Discovery results: what a connector found in a connected system, before normalisation (AP3).

Discovery is deterministic (ADR-026): catalogs, constraints, grants and API documents, plus
aggregate statistics from a bounded sample. Its output is a :class:`DiscoveryReport` of
:class:`RawArtifact`\\ s, which AP4 normalises into the AUSM. Three rules:

* **Everything human-readable is untrusted.** Names and descriptions come from whoever administers
  the target system, so they are :class:`~aura_core.domain.untrusted.UntrustedText` (ADR-022).
  Structural attribute strings (a data type, a referenced table) are bounded and checked the same
  way, and are never rendered into a prompt except fenced.
* **No row values by default.** Profiles carry aggregates (counts, ratios, the dominant pattern).
  Values appear only where the *schema* declares them (enum labels, check constraints) or when a
  Super Admin opts in for one run with ``DiscoveryBudget.sample_values``.
* **Bounded and honest.** A budget caps objects, sampled rows and time; hitting it sets
  ``truncated`` and records an issue rather than silently returning less. A permission refusal on
  one object is an issue, not a failed run.

Locators are stable and unambiguous: identifiers are double-quoted unless they are simple
lower-case names (``table:public.employee``, ``column:public."Order Lines".qty``), so
re-discovering an unchanged system produces identical locators -- and an identical digest.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .errors import ValidationError
from .serialization import content_digest
from .untrusted import UntrustedText

__all__ = [
    "ArtifactKind",
    "RawArtifact",
    "DiscoveryBudget",
    "DiscoveryIssue",
    "DiscoveryReport",
    "quote_identifier",
]

_SIMPLE_IDENT = re.compile(r"^[a-z_][a-z0-9_$]*$")
_ATTR_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ISSUE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_ATTR_STR = 512
_MAX_ATTR_ITEMS = 256
_MAX_LOCATOR = 512


def quote_identifier(name: str) -> str:
    """A catalog identifier as it appears in a locator: bare if simple, else double-quoted
    with embedded quotes doubled (SQL's own rule, so the form is familiar and unambiguous)."""
    if _SIMPLE_IDENT.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


class ArtifactKind(str, enum.Enum):
    # Relational catalog
    TABLE = "table"
    VIEW = "view"
    COLUMN = "column"
    PRIMARY_KEY = "primary_key"
    FOREIGN_KEY = "foreign_key"
    UNIQUE = "unique"
    CHECK = "check"
    ENUM_TYPE = "enum_type"
    ROLE = "role"
    GRANT = "grant"
    COLUMN_PROFILE = "column_profile"
    # API description
    API = "api"
    OPERATION = "operation"
    SCHEMA_OBJECT = "schema_object"
    SCHEMA_PROPERTY = "schema_property"
    SECURITY_SCHEME = "security_scheme"

    def __str__(self) -> str:
        return self.value


def _check_attr_str(owner: str, key: str, value: str) -> None:
    if len(value) > _MAX_ATTR_STR:
        raise ValidationError("attribute value too long", details={"locator": owner, "key": key})
    UntrustedText(value)  # same character rules as every other discovered string


@dataclass(frozen=True, slots=True)
class RawArtifact:
    """One thing discovered. ``attributes`` holds JSON scalars or tuples of scalars."""

    kind: ArtifactKind
    locator: str
    name: UntrustedText
    extractor: str
    description: UntrustedText | None = None
    parent: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for what, value in (("locator", self.locator), ("parent", self.parent)):
            if value is None and what == "parent":
                continue
            if not isinstance(value, str) or not value or len(value) > _MAX_LOCATOR:
                raise ValidationError(f"{what} must be 1-{_MAX_LOCATOR} characters")
            UntrustedText(value)
        clean: dict[str, Any] = {}
        for key, value in self.attributes.items():
            if not isinstance(key, str) or not _ATTR_KEY.match(key):
                raise ValidationError("attribute keys must be snake_case", details={"key": key})
            if isinstance(value, list):
                value = tuple(value)
            if isinstance(value, tuple):
                if len(value) > _MAX_ATTR_ITEMS:
                    raise ValidationError("attribute list too long", details={"locator": self.locator, "key": key})
                for item in value:
                    if not (item is None or isinstance(item, (str, int, float, bool))):
                        raise ValidationError("attribute lists hold scalars only", details={"key": key})
                    if isinstance(item, str):
                        _check_attr_str(self.locator, key, item)
            elif isinstance(value, str):
                _check_attr_str(self.locator, key, value)
            elif not (value is None or isinstance(value, (bool, int, float))):
                raise ValidationError("attribute values must be scalars or lists", details={"key": key})
            clean[key] = value
        object.__setattr__(self, "attributes", MappingProxyType(clean))

    @property
    def evidence_hash(self) -> str:
        """``sha256:<hex>`` of this artifact -- what AUSM provenance cites (AP4)."""
        return content_digest(self)


@dataclass(frozen=True, slots=True)
class DiscoveryBudget:
    """Caps for one run. Defaults suit a typical line-of-business database."""

    max_objects: int = 5000
    sample_rows: int = 1000
    max_profiled_columns: int = 64
    max_seconds: float = 120.0
    #: Opt-in, per run: record the distinct values of low-cardinality text columns that the
    #: schema does not already declare. Off by default -- profiles carry aggregates only.
    sample_values: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.max_objects <= 100_000:
            raise ValidationError("max_objects must be 1..100000")
        if not 0 <= self.sample_rows <= 100_000:
            raise ValidationError("sample_rows must be 0..100000 (0 disables profiling)")
        if not 0 <= self.max_profiled_columns <= 512:
            raise ValidationError("max_profiled_columns must be 0..512")
        if not 1.0 <= self.max_seconds <= 3600.0:
            raise ValidationError("max_seconds must be 1..3600")


@dataclass(frozen=True, slots=True)
class DiscoveryIssue:
    """Something discovery could not do. ``code`` is low-cardinality; never vendor text."""

    code: str
    locator: str = ""

    def __post_init__(self) -> None:
        if not _ISSUE_CODE.match(self.code):
            raise ValidationError("issue codes are snake_case slugs", details={"code": self.code})
        if self.locator:
            UntrustedText(self.locator)


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """One discovery run of one source. ``artifacts`` are in canonical (locator) order."""

    source_id: str
    extractor: str
    artifacts: tuple[RawArtifact, ...]
    issues: tuple[DiscoveryIssue, ...] = ()
    truncated: bool = False

    def __post_init__(self) -> None:
        locators = [a.locator for a in self.artifacts]
        if locators != sorted(locators):
            raise ValidationError("artifacts must be sorted by locator; use DiscoveryReport.build")
        if len(set(locators)) != len(locators):
            raise ValidationError("duplicate artifact locators")

    @classmethod
    def build(
        cls,
        source_id: str,
        extractor: str,
        artifacts: list[RawArtifact] | tuple[RawArtifact, ...],
        *,
        issues: list[DiscoveryIssue] | tuple[DiscoveryIssue, ...] = (),
        truncated: bool = False,
    ) -> "DiscoveryReport":
        ordered_issues = tuple(sorted(set(issues), key=lambda i: (i.code, i.locator)))
        return cls(source_id, extractor, tuple(sorted(artifacts, key=lambda a: a.locator)),
                   ordered_issues, truncated)

    @property
    def digest(self) -> str:
        return content_digest(self)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for artifact in self.artifacts:
            out[str(artifact.kind)] = out.get(str(artifact.kind), 0) + 1
        return dict(sorted(out.items()))

    def get(self, locator: str) -> RawArtifact | None:
        return next((a for a in self.artifacts if a.locator == locator), None)
