"""Project Twin versions, diffs and labeling records (AP4, ADR-020/026).

:class:`~aura_core.domain.ausm.ProjectTwin` is one snapshot. This module adds what makes a
sequence of snapshots useful:

* :class:`TwinVersion` -- the metadata of one stored snapshot: who took it, from which discovery
  reports (by digest), what normalisation could not map, how labels were produced. Versions are
  append-only; a bad version is superseded, never edited.
* :class:`TwinDiff` -- what changed between two snapshots: added, removed and changed elements,
  plus *rename candidates* -- a removed and an added element that look like the same thing under
  a new name. A rename is only ever a candidate: a human decides (ADR-024).
* :class:`LabelRequest` / :class:`LabelProposal` -- the only exchange with a labeling model. A
  model sees identifiers (as fenced DATA) and answers with a display name and description; it has
  no field through which it could change structure, refs or grants.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from .ausm import PROJECT_ID_PATTERN, ElementKind
from .errors import ValidationError
from .untrusted import UntrustedText

__all__ = [
    "TwinIssue",
    "LabelingSummary",
    "TwinVersion",
    "ElementChange",
    "RenameCandidate",
    "TwinDiff",
    "LabelRequest",
    "LabelProposal",
]

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACTOR_MAX = 256


@dataclass(frozen=True, slots=True)
class TwinIssue:
    """Something a snapshot could not represent. ``code`` is a low-cardinality slug."""

    code: str
    source_id: str = ""
    locator: str = ""

    def __post_init__(self) -> None:
        if not _CODE.match(self.code):
            raise ValidationError("issue codes are snake_case slugs", details={"code": self.code})
        for value in (self.source_id, self.locator):
            if value:
                UntrustedText(value)


@dataclass(frozen=True, slots=True)
class LabelingSummary:
    """How the labels of one snapshot were produced.

    ``eligible`` elements could have had a model label; ``model`` got a fresh one, ``reused`` kept
    one from the previous version (its deterministic evidence was unchanged), and ``degraded``
    kept the humanised identifier -- because no labeler is configured, or it failed or timed out.
    """

    labeler: str | None = None
    eligible: int = 0
    model: int = 0
    reused: int = 0
    degraded: int = 0
    seconds: float = 0.0

    def __post_init__(self) -> None:
        if min(self.eligible, self.model, self.reused, self.degraded) < 0 or self.seconds < 0:
            raise ValidationError("labeling counts cannot be negative")
        if self.model + self.reused + self.degraded != self.eligible:
            raise ValidationError("labeling counts must add up to the eligible count")

    @property
    def coverage(self) -> float:
        """Share of eligible elements carrying a model label (fresh or reused)."""
        return (self.model + self.reused) / self.eligible if self.eligible else 0.0


@dataclass(frozen=True, slots=True)
class TwinVersion:
    """Metadata of one stored snapshot. ``created_at`` is ISO-8601 UTC text."""

    project_id: str
    version: int
    digest: str
    created_at: str
    created_by: str
    counts: Mapping[str, int]
    sources: Mapping[str, str]
    issues: tuple[TwinIssue, ...] = ()
    labeling: LabelingSummary = field(default_factory=LabelingSummary)
    parent_version: int | None = None

    def __post_init__(self) -> None:
        if not PROJECT_ID_PATTERN.match(self.project_id):
            raise ValidationError("project_id must be a short slug")
        if self.version < 1:
            raise ValidationError("twin versions start at 1")
        if self.parent_version is not None and not 1 <= self.parent_version < self.version:
            raise ValidationError("a parent version must precede its child")
        if not _SHA256.match(self.digest):
            raise ValidationError("digest must be sha256:<64 hex>")
        if not self.created_by or len(self.created_by) > _ACTOR_MAX:
            raise ValidationError("created_by must be 1-256 characters")
        for kind, count in self.counts.items():
            ElementKind(kind)
            if count < 0:
                raise ValidationError("element counts cannot be negative")
        for source, digest in self.sources.items():
            if not _SHA256.match(digest):
                raise ValidationError("source report digests must be sha256:<64 hex>", details={"source": source})

    @property
    def element_count(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True, slots=True)
class ElementChange:
    """An element present in both snapshots whose content differs. ``fields`` names what."""

    id: str
    kind: ElementKind
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RenameCandidate:
    """A removed and an added element of the same kind that look like one thing renamed."""

    old_id: str
    new_id: str
    kind: ElementKind
    score: float
    reason: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValidationError("rename scores are within [0, 1]")
        if not _CODE.match(self.reason):
            raise ValidationError("rename reasons are snake_case slugs")


@dataclass(frozen=True, slots=True)
class TwinDiff:
    """What changed from ``from_version`` to ``to_version`` (ids sorted, deterministic)."""

    project_id: str
    from_version: int | None
    to_version: int
    from_digest: str | None
    to_digest: str
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[ElementChange, ...] = ()
    renames: tuple[RenameCandidate, ...] = ()

    @property
    def unchanged(self) -> bool:
        return self.from_digest == self.to_digest

    def summary(self) -> dict[str, int]:
        return {
            "added": len(self.added), "removed": len(self.removed),
            "changed": len(self.changed), "renames": len(self.renames),
        }


@dataclass(frozen=True, slots=True)
class LabelRequest:
    """One element to label. Everything a model sees is untrusted text from the target system."""

    element_id: str
    kind: ElementKind
    identifier: UntrustedText
    context: UntrustedText | None = None
    description: UntrustedText | None = None


@dataclass(frozen=True, slots=True)
class LabelProposal:
    """A model's answer for one element: a display name and, optionally, a description.

    Plain strings here, on purpose: the labeling pass re-sanitises and caps them into
    :class:`UntrustedText` before anything stores them -- model output is untrusted too.
    """

    element_id: str
    label: str
    description: str | None = None
