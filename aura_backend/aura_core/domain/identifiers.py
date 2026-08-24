"""Identifier value objects.

Typed ids rather than bare ``str`` so that passing a user id where a turn id is expected is a
type error rather than a silent data-corruption bug. Every id is a frozen dataclass with a
``value`` field, which keeps them hashable, comparable, and cheap.

``new()`` classmethods use UUID4 rather than a database sequence so ids can be minted
client-side, offline, and in tests without coordination.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import ClassVar

__all__ = [
    "Identifier",
    "UserId",
    "SessionId",
    "TurnId",
    "CorrelationId",
    "DocumentId",
    "ChunkId",
    "MemoryId",
    "PlanId",
    "StepId",
    "ToolCallId",
    "GenerationId",
    "PersonalOsRecordId",
    "deterministic_uuid",
]

_SAFE = re.compile(r"^[A-Za-z0-9_\-.:@]{1,256}$")


@dataclass(frozen=True, slots=True)
class Identifier:
    """Base identifier.

    Validation is intentionally strict: ids end up in log lines, metric exemplars, cache keys,
    and vector-store payload filters. Permitting arbitrary text there invites injection into
    each of those layers.
    """

    value: str

    _allow_arbitrary: ClassVar[bool] = False

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError(f"{type(self).__name__} requires a non-empty string")
        if not self._allow_arbitrary and not _SAFE.match(self.value):
            raise ValueError(
                f"{type(self).__name__} value must match {_SAFE.pattern!r}; got {self.value[:32]!r}"
            )

    @classmethod
    def new(cls) -> "Identifier":
        return cls(str(uuid.uuid4()))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class UserId(Identifier):
    """Stable per-user identity (the auth subject claim).

    Deliberately never used as a Prometheus label -- see ADR-009 on cardinality.
    """


@dataclass(frozen=True, slots=True)
class SessionId(Identifier):
    """One conversation. Scopes episodic and working memory."""


@dataclass(frozen=True, slots=True)
class TurnId(Identifier):
    """One request/response exchange within a session. The unit of telemetry and provenance."""


@dataclass(frozen=True, slots=True)
class CorrelationId(Identifier):
    """Flows through logs, metrics exemplars, traces, and audit events for one turn (ADR-009)."""


@dataclass(frozen=True, slots=True)
class DocumentId(Identifier):
    """Logical document identity, stable across versions (ADR-007)."""

    _allow_arbitrary: ClassVar[bool] = True  # may derive from URIs and filenames


@dataclass(frozen=True, slots=True)
class ChunkId(Identifier):
    _allow_arbitrary: ClassVar[bool] = True


@dataclass(frozen=True, slots=True)
class MemoryId(Identifier):
    pass


@dataclass(frozen=True, slots=True)
class PlanId(Identifier):
    pass


@dataclass(frozen=True, slots=True)
class StepId(Identifier):
    pass


@dataclass(frozen=True, slots=True)
class ToolCallId(Identifier):
    pass


@dataclass(frozen=True, slots=True)
class GenerationId(Identifier):
    """A numbered knowledge-refresh generation (ADR-008). Rollback targets one of these."""


@dataclass(frozen=True, slots=True)
class PersonalOsRecordId(Identifier):
    """A task, calendar event, finance record, or health record the kernel itself wrote

    (Phase C2). Deliberately its own id type, not shared with the legacy encrypted tables of the
    same name: this is a different table with a different visibility contract -- see
    ``aura_core/domain/personal_os.py``.
    """


def deterministic_uuid(*parts: str, namespace: str = "aura") -> uuid.UUID:
    """Stable UUID from content, for idempotent upserts.

    Used for chunk ids so re-ingesting an unchanged document version overwrites the same points
    instead of duplicating them. SHA-256 (not the legacy ``services/deterministic_id.py``
    truncated-hash-to-int approach, which produced a 63-bit space with no namespace separation
    and therefore a real collision surface across document and memory ids sharing one
    collection).
    """
    digest = hashlib.sha256(("\x1f".join((namespace, *parts))).encode("utf-8")).digest()
    return uuid.UUID(bytes=digest[:16], version=5)
