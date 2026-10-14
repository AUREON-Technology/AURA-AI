"""Deterministic normalisation of discovery reports into AUSM elements (AP4, ADR-020/026).

One pure function, :func:`normalize`, maps the :class:`RawArtifact`\\ s of one or more
:class:`DiscoveryReport`\\ s onto the AUSM vocabulary. No model, no network, no clock: the same
reports always yield the same elements, which is what makes a twin's digest meaningful.

What maps to what:

========================  =====================================================================
Discovered                AUSM
========================  =====================================================================
table, view               ENTITY (``source_type`` says which)
column                    ATTRIBUTE, with its profile's aggregates folded into ``properties``
foreign key               RELATION (``from``/``to`` entities, ``via`` for a one-column key)
primary key, unique       CONSTRAINT on the entity (and a ``primary_key``/``unique`` flag)
check                     CONSTRAINT on the column (one-column check) or the entity
enum labels, check lists  STATE under the attribute, one per declared value
role / grant              ROLE / PERMISSION (``access`` = read, write or read_write)
schema object / property  ENTITY / ATTRIBUTE; a ``$ref`` property is a RELATION
operation                 ACTION -- ``GET`` is ``mode=read``; every other verb is ``mode=write``
                          and ``deferred`` (no write path exists in v1, ADR-021)
security scheme           CONSTRAINT on the SOURCE (``constraint_type=auth_scheme``)
document                  DOCUMENT -- format, size and content hash; its text never enters the twin
timestamp / date column   EVENT candidate on its entity (confidence < 1)
numeric measure column    METRIC candidate on its entity (confidence < 1)
========================  =====================================================================

Every element's ``label`` is its identifier, humanised (``leave_requests`` -> "Leave requests"),
and the raw identifier is kept as the ``identifier`` property. That *is* the degraded mode: with
no labeling model the twin is complete, just plainly named (ADR-026). Declared descriptions
(column comments, OpenAPI descriptions) are carried as untrusted text, never rewritten.

Structural facts come from catalog extractors at confidence 1.0. Inferences this module makes
itself -- events, metrics, name-based references -- carry the ``normalize`` extractor and a
confidence below 1, so later stages can tell observation from guess.

Facts read from source code (AP10's Git connector: extractors named ``code_*``) describe what the
code *declares*, not what the running system holds, so they enter at :data:`CODE_CONFIDENCE`; and a
source discovered from code alone is marked ``queryable: false`` on its SOURCE element, which is
what tells the planner (AP5) that nothing can be read from it -- its elements are evidence and
match candidates, never tool targets. Cross-source entity merges
and workflows are proposed by :mod:`.matching` and :mod:`.process_mining`, not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..domain.ausm import SOURCE_LOCATOR, AusmElement, ElementKind, Provenance, element_id
from ..domain.discovery import ArtifactKind, DiscoveryReport, RawArtifact, quote_identifier
from ..domain.serialization import content_digest
from ..domain.twin import TwinIssue
from ..domain.untrusted import UntrustedText

__all__ = [
    "Normalized",
    "normalize",
    "humanize",
    "name_tokens",
    "NORMALIZE_EXTRACTOR",
    "STATUS_NAMES",
    "MAX_OPTIONAL_QUERY",
    "CODE_CONFIDENCE",
    "CODE_EXTRACTOR_PREFIX",
]

#: Extractor of everything this module infers rather than reads off a catalog.
NORMALIZE_EXTRACTOR = "normalize"
#: Extractors that read source code rather than a running system (AP10).
CODE_EXTRACTOR_PREFIX = "code_"
#: How far a declaration in code is trusted as a fact about the live system.
CODE_CONFIDENCE = 0.8
#: Report extractors whose source can only be described, never queried.
_DESCRIPTIVE_SOURCES = frozenset({"git"})
#: Attribute names (as tokens) that hold a lifecycle state. Generic, not domain nouns.
STATUS_NAMES = frozenset({"status", "state", "stage", "phase", "lifecycle"})

_MAX_PROPERTY = 256
#: Optional query parameters carried per API action (AR-2): each becomes an optional tool
#: parameter, so this also caps how wide one generated tool's declaration can get.
MAX_OPTIONAL_QUERY = 8
_MAX_LOCATOR = 512
_TIMESTAMP_TYPES = re.compile(r"^timestamp\b")
_NUMERIC_TYPES = re.compile(
    r"^(smallint|integer|bigint|numeric|decimal|real|double precision|money)\b"
)
_ARRAY_LITERAL = re.compile(r"ARRAY\[(.*)\]", re.DOTALL)
_QUOTED = re.compile(r"'((?:[^']|'')*)'")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
_HTTP_READ = frozenset({"GET", "HEAD"})
_WRITE_PRIVILEGES = frozenset({"INSERT", "UPDATE", "DELETE", "TRUNCATE"})
_EVENT_SUFFIXES = frozenset({"at", "on", "date", "time", "ts", "timestamp"})


# ------------------------------------------------------------------------------- identifiers


def name_tokens(identifier: str) -> tuple[str, ...]:
    """Lower-case word tokens of an identifier in any common casing.

    ``leave_requests``, ``LeaveRequests``, ``leave-requests`` and ``leaveRequests`` all give
    ``("leave", "requests")`` -- what name matching across sources compares.
    """
    tokens: list[str] = []
    for chunk in re.split(r"[^0-9A-Za-z]+", identifier):
        tokens.extend(t.lower() for t in _CAMEL.findall(chunk))
    return tuple(tokens)


def singular(token: str) -> str:
    """A crude English singular, enough to align ``employees`` with ``Employee``."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("sses", "xes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def humanize(identifier: str) -> str:
    """The degraded-mode display name: words split, first one capitalised."""
    words = name_tokens(identifier)
    if not words:
        return identifier.strip() or "Unnamed"
    text = " ".join(words)
    return text[0].upper() + text[1:]


def _label(identifier: str) -> UntrustedText:
    return UntrustedText.of(humanize(identifier), max_chars=200)


def _bounded(value: str) -> str | None:
    return value if len(value) <= _MAX_PROPERTY else None


def _join(names: Iterable[str]) -> str | None:
    return _bounded(",".join(quote_identifier(n) for n in names))


def _suffix(locator: str) -> str:
    return locator.split(":", 1)[1]


# ------------------------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class Normalized:
    """Elements in canonical order, plus everything that could not be mapped."""

    elements: tuple[AusmElement, ...]
    issues: tuple[TwinIssue, ...]
    sources: Mapping[str, str]  # source_id -> digest of the report it came from


def normalize(reports: Sequence[DiscoveryReport]) -> Normalized:
    """Map discovery reports (one per source) onto AUSM elements. Pure and deterministic."""
    seen: set[str] = set()
    elements: dict[str, AusmElement] = {}
    issues: list[TwinIssue] = []
    sources: dict[str, str] = {}
    for report in sorted(reports, key=lambda r: r.source_id):
        if report.source_id in seen:
            issues.append(TwinIssue("duplicate_source", report.source_id))
            continue
        seen.add(report.source_id)
        sources[report.source_id] = report.digest
        mapper = _SourceMapper(report)
        for element in mapper.run():
            elements[element.id] = element
        issues.extend(mapper.issues)
    ordered = tuple(elements[k] for k in sorted(elements))
    unique_issues = sorted(set(issues), key=lambda i: (i.source_id, i.code, i.locator))
    return Normalized(ordered, tuple(unique_issues), dict(sorted(sources.items())))


# ------------------------------------------------------------------------------- one source


class _SourceMapper:
    def __init__(self, report: DiscoveryReport) -> None:
        self.report = report
        self.src = report.source_id
        self.by_locator = {a.locator: a for a in report.artifacts}
        self.issues: list[TwinIssue] = []
        self.out: dict[str, AusmElement] = {}
        # locator -> mutable pieces of an attribute, finalised once everything is folded in
        self._attrs: dict[str, dict[str, Any]] = {}
        self._hashes: dict[str, str] = {}

    # -- helpers --------------------------------------------------------------------------

    def _hash(self, artifact: RawArtifact) -> str:
        cached = self._hashes.get(artifact.locator)
        if cached is None:
            cached = self._hashes[artifact.locator] = artifact.evidence_hash
        return cached

    def _prov(self, artifact: RawArtifact, *, confidence: float = 1.0, extractor: str | None = None) -> Provenance:
        if artifact.extractor.startswith(CODE_EXTRACTOR_PREFIX):
            confidence = min(confidence, CODE_CONFIDENCE)
        return Provenance(self.src, artifact.locator, extractor or artifact.extractor, confidence, self._hash(artifact))

    def _id(self, locator: str) -> str:
        return element_id(self.src, locator)

    def _issue(self, code: str, locator: str = "") -> None:
        self.issues.append(TwinIssue(code, self.src, locator))

    def _add(self, element: AusmElement) -> None:
        self.out[element.locator] = element

    # -- driver ---------------------------------------------------------------------------

    def run(self) -> list[AusmElement]:
        report = self.report
        if report.truncated:
            self._issue("source_truncated")
        for issue in report.issues:
            self._issue(issue.code, issue.locator)
        self._source()
        kinds = {kind: [a for a in report.artifacts if a.kind is kind] for kind in ArtifactKind}
        for artifact in kinds[ArtifactKind.TABLE] + kinds[ArtifactKind.VIEW]:
            self._entity(artifact, source_type=str(artifact.kind))
        for artifact in kinds[ArtifactKind.SCHEMA_OBJECT]:
            self._entity(artifact, source_type="schema")
        for artifact in kinds[ArtifactKind.COLUMN] + kinds[ArtifactKind.SCHEMA_PROPERTY]:
            self._attribute(artifact)
        for artifact in kinds[ArtifactKind.PRIMARY_KEY] + kinds[ArtifactKind.UNIQUE]:
            self._key(artifact)
        for artifact in kinds[ArtifactKind.CHECK]:
            self._check(artifact)
        for artifact in kinds[ArtifactKind.COLUMN_PROFILE]:
            self._profile(artifact)
        self._value_sets(kinds[ArtifactKind.CHECK])
        for artifact in kinds[ArtifactKind.FOREIGN_KEY]:
            self._foreign_key(artifact)
        for artifact in kinds[ArtifactKind.ROLE]:
            self._add(AusmElement(
                self.src, artifact.locator, ElementKind.ROLE, _label(artifact.name.raw()), (self._prov(artifact),),
                description=artifact.description, properties={"identifier": _bounded(artifact.name.raw())},
            ))
        for artifact in kinds[ArtifactKind.GRANT]:
            self._grant(artifact)
        for artifact in kinds[ArtifactKind.SECURITY_SCHEME]:
            self._security(artifact)
        for artifact in kinds[ArtifactKind.DOCUMENT]:
            self._document(artifact)
        self._finish_attributes()
        for artifact in kinds[ArtifactKind.SCHEMA_PROPERTY]:
            self._property_relation(artifact)
        for artifact in kinds[ArtifactKind.OPERATION]:
            self._operation(artifact)
        self._events_and_metrics()
        return list(self.out.values())

    # -- source ---------------------------------------------------------------------------

    def _source(self) -> None:
        api = self.by_locator.get("api:spec")
        label = UntrustedText.of(api.name.raw(), max_chars=200) if api else _label(self.src)
        props: dict[str, Any] = {
            "extractor": self.report.extractor,
            "artifacts": len(self.report.artifacts),
            "truncated": self.report.truncated,
        }
        if api is not None:
            props["api_version"] = _str_attr(api, "api_version")
            props["openapi"] = _str_attr(api, "openapi")
        if self.report.extractor in _DESCRIPTIVE_SOURCES:
            props["queryable"] = False
        evidence = content_digest({"source_id": self.src, "extractor": self.report.extractor})
        self._add(AusmElement(
            self.src, SOURCE_LOCATOR, ElementKind.SOURCE, label,
            (Provenance(self.src, SOURCE_LOCATOR, self.report.extractor, 1.0, evidence),),
            description=api.description if api else None, properties=props,
        ))

    # -- documents ------------------------------------------------------------------------

    def _document(self, artifact: RawArtifact) -> None:
        """A file for project knowledge. Its label is its file name -- what a citation shows."""
        name = artifact.name.raw()
        bytes_ = artifact.attributes.get("bytes")
        self._add(AusmElement(
            self.src, artifact.locator, ElementKind.DOCUMENT, UntrustedText.of(name, max_chars=200),
            (self._prov(artifact),),
            properties={
                "identifier": _bounded(name), "format": _str_attr(artifact, "format"),
                "bytes": bytes_ if isinstance(bytes_, int) and not isinstance(bytes_, bool) else None,
                "sha256": _str_attr(artifact, "sha256"),
            },
        ))

    # -- entities and attributes ----------------------------------------------------------

    def _entity(self, artifact: RawArtifact, *, source_type: str) -> None:
        attrs = artifact.attributes
        props: dict[str, Any] = {"identifier": _bounded(artifact.name.raw()), "source_type": source_type}
        if "schema" in attrs:
            props["schema"] = _str_attr(artifact, "schema")
        if "readable" in attrs:
            props["readable"] = bool(attrs["readable"])
        if source_type == "schema":
            props["required_count"] = len(attrs.get("required") or ())
        self._add(AusmElement(
            self.src, artifact.locator, ElementKind.ENTITY, _label(artifact.name.raw()), (self._prov(artifact),),
            description=artifact.description, properties=props,
        ))

    def _attribute(self, artifact: RawArtifact) -> None:
        if artifact.parent is None or artifact.parent not in self.out:
            self._issue("orphan_attribute", artifact.locator)
            return
        attrs = artifact.attributes
        props: dict[str, Any] = {"identifier": _bounded(artifact.name.raw())}
        if artifact.kind is ArtifactKind.COLUMN:
            props.update(
                data_type=_str_attr(artifact, "data_type"), nullable=bool(attrs.get("nullable")),
                has_default=bool(attrs.get("has_default")), position=attrs.get("position"),
            )
        else:
            props.update(
                data_type=_str_attr(artifact, "type"), format=_str_attr(artifact, "format"),
                nullable=bool(attrs.get("nullable")), required=bool(attrs.get("required")),
                read_only=bool(attrs.get("read_only")),
            )
        self._attrs[artifact.locator] = {
            "artifact": artifact, "props": props, "prov": [self._prov(artifact)], "values": None,
        }

    def _columns_of(self, artifact: RawArtifact) -> list[str]:
        """Attribute locators for a constraint's ``columns`` (parent = its table or view)."""
        out = []
        for name in artifact.attributes.get("columns") or ():
            locator = f"column:{_suffix(artifact.parent)}.{quote_identifier(name)}" if artifact.parent else ""
            out.append(locator)
        return out

    def _key(self, artifact: RawArtifact) -> None:
        if artifact.parent not in self.out:
            self._issue("orphan_constraint", artifact.locator)
            return
        is_pk = artifact.kind is ArtifactKind.PRIMARY_KEY
        columns = self._columns_of(artifact)
        if len(columns) == 1 and columns[0] in self._attrs:
            self._attrs[columns[0]]["props"]["primary_key" if is_pk else "unique"] = True
        elif is_pk:
            for column in columns:
                if column in self._attrs:
                    self._attrs[column]["props"]["primary_key"] = True
        self._add(AusmElement(
            self.src, artifact.locator, ElementKind.CONSTRAINT, _label(artifact.name.raw()), (self._prov(artifact),),
            parent=self._id(artifact.parent),
            properties={
                "identifier": _bounded(artifact.name.raw()),
                "constraint_type": "primary_key" if is_pk else "unique",
                "columns": _join(artifact.attributes.get("columns") or ()),
            },
        ))

    def _check(self, artifact: RawArtifact) -> None:
        if artifact.parent not in self.out:
            self._issue("orphan_constraint", artifact.locator)
            return
        columns = self._columns_of(artifact)
        parent = columns[0] if len(columns) == 1 and columns[0] in self._attrs else artifact.parent
        definition = _str_attr(artifact, "definition") or ""
        self._add(AusmElement(
            self.src, artifact.locator, ElementKind.CONSTRAINT, _label(artifact.name.raw()), (self._prov(artifact),),
            parent=self._id(parent),
            properties={
                "identifier": _bounded(artifact.name.raw()),
                "constraint_type": "check",
                "columns": _join(artifact.attributes.get("columns") or ()),
                "expression": _bounded(definition),
            },
        ))

    def _profile(self, artifact: RawArtifact) -> None:
        slot = self._attrs.get(artifact.parent or "")
        if slot is None:
            return  # a profile of a column the budget cut: nothing to fold it into
        attrs = artifact.attributes
        for key in ("distinct_ratio", "null_ratio", "pattern", "unique_in_sample", "sampled_rows"):
            if key in attrs:
                slot["props"][key] = attrs[key]
        slot["prov"].append(self._prov(artifact))
        values = attrs.get("values")
        if values and slot["values"] is None and attrs.get("pattern") == "categorical":
            # Opt-in sampled values (DiscoveryBudget.sample_values): observed, not declared.
            slot["values"] = ("sampled", tuple(v for v in values if isinstance(v, str)), artifact, 0.7)

    def _value_sets(self, checks: list[RawArtifact]) -> None:
        """Closed value sets, strongest source first: enum type, check IN-list, OpenAPI enum."""
        for check in checks:
            columns = self._columns_of(check)
            if len(columns) != 1 or columns[0] not in self._attrs:
                continue
            values = _check_values(_str_attr(check, "definition") or "")
            slot = self._attrs[columns[0]]
            if values and (slot["values"] is None or slot["values"][0] == "sampled"):
                slot["values"] = ("check", values, check, 1.0)
        for slot in self._attrs.values():
            artifact = slot["artifact"]
            enum_locator = artifact.attributes.get("enum_type")
            enum_artifact = self.by_locator.get(enum_locator) if isinstance(enum_locator, str) else None
            if enum_artifact is not None:
                labels = tuple(v for v in enum_artifact.attributes.get("labels") or () if isinstance(v, str))
                slot["values"] = ("enum", labels, enum_artifact, 1.0)
                slot["prov"].append(self._prov(enum_artifact))
            elif artifact.kind is ArtifactKind.SCHEMA_PROPERTY and artifact.attributes.get("enum"):
                labels = tuple(v for v in artifact.attributes["enum"] if isinstance(v, str))
                slot["values"] = ("enum", labels, artifact, 1.0)

    def _finish_attributes(self) -> None:
        fk_columns = self._fk_columns()
        for locator, slot in self._attrs.items():
            artifact: RawArtifact = slot["artifact"]
            props = slot["props"]
            if locator in fk_columns:
                props["foreign_key"] = True
            values = slot["values"]
            if values is not None:
                props["value_set"] = values[0]
            props["semantic_type"] = _semantic_type(artifact, props, has_values=bool(values and values[1]))
            self._add(AusmElement(
                self.src, locator, ElementKind.ATTRIBUTE, _label(artifact.name.raw()), tuple(slot["prov"]),
                description=artifact.description, parent=self._id(artifact.parent),
                properties={k: v for k, v in props.items() if v is not None},
            ))
            if values is not None:
                self._states(locator, values)

    def _states(self, attribute: str, values: tuple[str, tuple[str, ...], RawArtifact, float]) -> None:
        origin, labels, evidence, confidence = values
        extractor = evidence.extractor
        for ordinal, value in enumerate(dict.fromkeys(labels)):
            locator = f"state:{_suffix(attribute)}={quote_identifier(value)}"
            if len(value) > _MAX_PROPERTY or len(locator) > _MAX_LOCATOR:
                self._issue("state_value_too_long", attribute)
                continue
            self._add(AusmElement(
                self.src, locator, ElementKind.STATE, UntrustedText.of(humanize(value) if value else "(empty)", max_chars=200),
                (self._prov(evidence, confidence=confidence, extractor=extractor),),
                parent=self._id(attribute),
                properties={"value": value, "ordinal": ordinal, "value_set": origin},
            ))

    def _fk_columns(self) -> set[str]:
        out: set[str] = set()
        for artifact in self.report.artifacts:
            if artifact.kind is ArtifactKind.FOREIGN_KEY:
                out.update(self._columns_of(artifact))
        return out

    # -- relations, grants, security ------------------------------------------------------

    def _foreign_key(self, artifact: RawArtifact) -> None:
        target = artifact.attributes.get("ref_table")
        if artifact.parent not in self.out or not isinstance(target, str) or target not in self.out:
            self._issue("unresolved_reference", artifact.locator)
            return
        columns = tuple(artifact.attributes.get("columns") or ())
        via = self._columns_of(artifact)
        refs = {"from": self._id(artifact.parent), "to": self._id(target)}
        if len(via) == 1 and via[0] in self._attrs:
            refs["via"] = self._id(via[0])
        target_name = self.out[target].properties.get("identifier") or target
        label = f"{humanize(columns[0])} → {humanize(target_name)}" if len(columns) == 1 else humanize(artifact.name.raw())
        self._add(AusmElement(
            self.src, artifact.locator, ElementKind.RELATION, UntrustedText.of(label, max_chars=200),
            (self._prov(artifact),), refs=refs,
            properties={
                "identifier": _bounded(artifact.name.raw()),
                "relation_type": "foreign_key",
                "columns": _join(columns),
                "ref_columns": _join(artifact.attributes.get("ref_columns") or ()),
                "cardinality": "one_to_one" if self._is_key(artifact.parent, columns) else "many_to_one",
                "self_referencing": artifact.parent == target,
            },
        ))

    def _is_key(self, table: str, columns: tuple[str, ...]) -> bool:
        wanted = set(columns)
        return any(
            a.kind in (ArtifactKind.PRIMARY_KEY, ArtifactKind.UNIQUE) and a.parent == table
            and set(a.attributes.get("columns") or ()) == wanted
            for a in self.report.artifacts
        )

    def _grant(self, artifact: RawArtifact) -> None:
        role = artifact.attributes.get("role")
        if not isinstance(role, str) or role not in self.out or artifact.parent not in self.out:
            self._issue("unresolved_reference", artifact.locator)
            return
        privileges = sorted(p for p in artifact.attributes.get("privileges") or () if isinstance(p, str))
        reads = "SELECT" in privileges
        writes = bool(_WRITE_PRIVILEGES.intersection(privileges))
        access = "read_write" if reads and writes else "write" if writes else "read" if reads else "other"
        label = f"{humanize(self.out[role].properties.get('identifier') or role)} on " \
                f"{humanize(self.out[artifact.parent].properties.get('identifier') or artifact.parent)}"
        self._add(AusmElement(
            self.src, artifact.locator, ElementKind.PERMISSION, UntrustedText.of(label, max_chars=200),
            (self._prov(artifact),), refs={"role": self._id(role), "target": self._id(artifact.parent)},
            properties={"privileges": _bounded(",".join(privileges)), "access": access},
        ))

    def _security(self, artifact: RawArtifact) -> None:
        self._add(AusmElement(
            self.src, artifact.locator, ElementKind.CONSTRAINT, _label(artifact.name.raw()), (self._prov(artifact),),
            description=artifact.description, parent=self._id(SOURCE_LOCATOR),
            properties={
                "identifier": _bounded(artifact.name.raw()), "constraint_type": "auth_scheme",
                "scheme_type": _str_attr(artifact, "type"), "scheme": _str_attr(artifact, "scheme"),
                "location": _str_attr(artifact, "in"),
            },
        ))

    def _property_relation(self, artifact: RawArtifact) -> None:
        """An OpenAPI property that references another schema, by ``$ref`` or by name."""
        if artifact.locator not in self.out:
            return
        attrs = artifact.attributes
        target, relation_type, cardinality, confidence = None, None, None, 1.0
        if isinstance(attrs.get("ref"), str):
            target, relation_type, cardinality = attrs["ref"], "reference", "many_to_one"
        elif isinstance(attrs.get("items_ref"), str):
            target, relation_type, cardinality = attrs["items_ref"], "embedded", "one_to_many"
        else:
            target = self._entity_by_reference_name(artifact)
            relation_type, cardinality, confidence = "reference_by_name", "many_to_one", 0.6
        if target is None or target not in self.out or self.out[target].kind is not ElementKind.ENTITY:
            return
        locator = f"rel:{_suffix(artifact.locator)}"
        name = artifact.name.raw()
        target_name = self.out[target].properties.get("identifier") or target
        extractor = artifact.extractor if confidence == 1.0 else NORMALIZE_EXTRACTOR
        self._add(AusmElement(
            self.src, locator, ElementKind.RELATION,
            UntrustedText.of(f"{humanize(name)} → {humanize(target_name)}", max_chars=200),
            (self._prov(artifact, confidence=confidence, extractor=extractor),),
            refs={"from": self._id(artifact.parent), "to": self._id(target), "via": self._id(artifact.locator)},
            properties={
                "relation_type": relation_type, "cardinality": cardinality,
                "self_referencing": artifact.parent == target, "columns": _join((name,)),
            },
        ))

    def _entity_by_reference_name(self, artifact: RawArtifact) -> str | None:
        """``customerId`` / ``customer_id`` -> the one entity in this source named ``Customer``."""
        tokens = name_tokens(artifact.name.raw())
        if len(tokens) < 2 or tokens[-1] != "id":
            return None
        stem = tuple(singular(t) for t in tokens[:-1])
        matches = [
            loc for loc, e in self.out.items()
            if e.kind is ElementKind.ENTITY and loc != artifact.parent
            and tuple(singular(t) for t in name_tokens(e.properties.get("identifier") or "")) == stem
        ]
        return matches[0] if len(matches) == 1 else None

    # -- operations -----------------------------------------------------------------------

    def _operation(self, artifact: RawArtifact) -> None:
        attrs = artifact.attributes
        method = (_str_attr(artifact, "method") or "").upper()
        path = _str_attr(artifact, "path") or ""
        mode = "read" if method in _HTTP_READ else "write"
        entity = None
        for key in ("response_schema", "request_schema"):
            candidate = attrs.get(key)
            if isinstance(candidate, str) and candidate in self.out:
                entity = candidate
                break
        if entity is None:
            entity = self._entity_for_path(path)
        refs = {"entity": self._id(entity)} if entity else {}
        operation_id = _str_attr(artifact, "operation_id")
        # AR-2: optional query parameters ride as one compact descriptor each, numbered in spec
        # order; anything past the cap, or longer than a property may be, is left unreachable.
        optional = [d for d in (attrs.get("query_parameters") or ()) if isinstance(d, str) and _bounded(d)]
        query = {f"query_param_{i}": d for i, d in enumerate(optional[:MAX_OPTIONAL_QUERY], start=1)}
        self._add(AusmElement(
            self.src, artifact.locator, ElementKind.ACTION, _label(artifact.name.raw()), (self._prov(artifact),),
            description=artifact.description, refs=refs,
            properties={
                "identifier": _bounded(artifact.name.raw()),
                "operation_id": operation_id,
                "http_method": method or None,
                "path": _bounded(path),
                "mode": mode,
                # ADR-021: there is no write path in v1. A write action is described, not offered.
                "deferred": mode == "write",
                "deprecated": bool(attrs.get("deprecated")),
                "returns_list": bool(attrs.get("response_is_array")),
                "required_parameters": _bounded(",".join(attrs.get("required_parameters") or ())),
                "security": _bounded(",".join(attrs.get("security") or ())),
                **query,
            },
        ))

    def _entity_for_path(self, path: str) -> str | None:
        segments = [s for s in path.split("/") if s and not s.startswith("{")]
        if not segments:
            return None
        stem = tuple(singular(t) for t in name_tokens(segments[0]))
        # An API description's own schemas first; a source that also declares tables (a repository
        # with both an OpenAPI file and models) falls back to those.
        for source_type in ("schema", "table"):
            matches = [
                loc for loc, e in self.out.items()
                if e.kind is ElementKind.ENTITY and e.properties.get("source_type") == source_type
                and tuple(singular(t) for t in name_tokens(e.properties.get("identifier") or "")) == stem
            ]
            if matches:
                return matches[0] if len(matches) == 1 else None
        return None

    # -- inferred candidates --------------------------------------------------------------

    def _events_and_metrics(self) -> None:
        attributes = [e for e in self.out.values() if e.kind is ElementKind.ATTRIBUTE]
        for attribute in attributes:
            parent = self.out.get(_suffix_id(attribute.parent, self.src))
            if parent is None or parent.properties.get("source_type") == "view":
                continue  # a view restates its tables; candidates come from the base tables
            semantic = attribute.properties.get("semantic_type")
            name = attribute.properties.get("identifier") or ""
            evidence = attribute.provenance[0]
            if semantic in ("timestamp", "date"):
                stem = [t for t in name_tokens(name) if t not in _EVENT_SUFFIXES] or list(name_tokens(name))
                confidence = 0.8 if semantic == "timestamp" else 0.6
                self._add(AusmElement(
                    self.src, f"event:{_suffix(attribute.locator)}", ElementKind.EVENT,
                    UntrustedText.of(humanize("_".join(stem)), max_chars=200),
                    (Provenance(self.src, attribute.locator, NORMALIZE_EXTRACTOR, confidence, evidence.evidence_hash),),
                    refs={"entity": parent.id},
                    properties={
                        "attribute": name, "granularity": semantic,
                        "nullable": bool(attribute.properties.get("nullable")),
                    },
                ))
            elif semantic == "measure":
                self._add(AusmElement(
                    self.src, f"metric:{_suffix(attribute.locator)}", ElementKind.METRIC,
                    UntrustedText.of(f"Total {humanize(name).lower()}", max_chars=200),
                    (Provenance(self.src, attribute.locator, NORMALIZE_EXTRACTOR, 0.5, evidence.evidence_hash),),
                    refs={"entity": parent.id},
                    properties={"measure": name, "aggregation": "sum"},
                ))


def _suffix_id(element_ref: str | None, source_id: str) -> str:
    """The locator part of an element id in ``source_id``."""
    if not element_ref:
        return ""
    prefix = f"{source_id}:"
    return element_ref[len(prefix):] if element_ref.startswith(prefix) else ""


def _str_attr(artifact: RawArtifact, key: str) -> str | None:
    value = artifact.attributes.get(key)
    return _bounded(value) if isinstance(value, str) else None


def _check_values(definition: str) -> tuple[str, ...]:
    """The literal list of a one-column ``CHECK (col = ANY (ARRAY[...]))`` / ``IN (...)``.

    Deliberately narrow: anything with boolean structure (AND/OR) is not a closed value set.
    """
    upper = definition.upper()
    if " AND " in upper or " OR " in upper:
        return ()
    match = _ARRAY_LITERAL.search(definition)
    if match and "= ANY" in upper:
        body = match.group(1)
    else:
        found = re.search(r"\bIN\s*\((.*)\)", definition, re.DOTALL | re.IGNORECASE)
        if not found:
            return ()
        body = found.group(1)
    return tuple(v.replace("''", "'") for v in _QUOTED.findall(body))


def _semantic_type(artifact: RawArtifact, props: Mapping[str, Any], *, has_values: bool) -> str:
    """A coarse, domain-agnostic role for an attribute -- what later stages plan from."""
    name = artifact.name.raw()
    tokens = name_tokens(name)
    data_type = (props.get("data_type") or "").lower()
    fmt = (props.get("format") or "").lower()
    if (props.get("primary_key") and not props.get("foreign_key")) or (
        artifact.kind is ArtifactKind.SCHEMA_PROPERTY and tokens == ("id",)
    ):
        return "identifier"
    if props.get("foreign_key") or artifact.attributes.get("ref") or artifact.attributes.get("items_ref"):
        return "reference"
    if artifact.kind is ArtifactKind.SCHEMA_PROPERTY and len(tokens) > 1 and tokens[-1] == "id":
        return "reference"
    if _TIMESTAMP_TYPES.match(data_type) or fmt == "date-time":
        return "timestamp"
    if data_type == "date" or fmt == "date":
        return "date"
    if has_values and tokens and tokens[-1] in STATUS_NAMES:
        return "status"
    if has_values or props.get("pattern") == "categorical":
        return "category"
    pattern = props.get("pattern")
    if pattern in ("email", "url", "uuid"):
        return str(pattern)
    if fmt in ("email", "uuid"):
        return fmt
    if fmt in ("uri", "url"):
        return "url"
    if data_type in ("boolean", "bool"):
        return "flag"
    if _NUMERIC_TYPES.match(data_type) or data_type in ("number", "integer"):
        return "measure"
    return "text"
