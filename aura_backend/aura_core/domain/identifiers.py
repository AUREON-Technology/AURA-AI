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
    "EngineerRunId",
    "WorkspaceId",
    "AgentRunId",
    "AgentStepId",
    "deterministic_uuid",
    "RESERVED_PRINCIPAL_PREFIXES",
    "is_reserved_principal",
]

_SAFE = re.compile(r"^[A-Za-z0-9_\-.:@]{1,256}$")

#: Owner namespaces that are not people: a service key acts as ``service:<name>`` (AR-4) and a
#: project's knowledge is owned by ``project:<id>`` (AP10). With ``"global"`` -- the vector stores'
#: shared-content sentinel (``ports.storage.GLOBAL_OWNER``) -- these are owner values a person's
#: authenticated subject must never take, or that person would read and write as the project, the
#: service, or everyone.
RESERVED_PRINCIPAL_PREFIXES = ("service:", "project:")
_RESERVED_PRINCIPALS = frozenset({"global"})


def is_reserved_principal(subject: str) -> bool:
    """Whether ``subject`` names a non-person owner and so cannot be a person's user id."""
    return subject in _RESERVED_PRINCIPALS or subject.startswith(RESERVED_PRINCIPAL_PREFIXES)


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
    """A task, calendar event, finance record, or health record (Phase C2).

    Since ADR-032 this is the synced row's own UUID, so an id the agent reports is the id the app
    holds -- see ``aura_core/domain/personal_os.py``.
    """


@dataclass(frozen=True, slots=True)
class EngineerRunId(Identifier):
    """One recorded outcome of an engineer-agent tool call (sandbox or GitHub) -- see
    ``aura_core/domain/engineering.py``. Its own id type: a different table with a different
    visibility contract, not to be confused with a `ToolCallId`."""


@dataclass(frozen=True, slots=True)
class WorkspaceId(Identifier):
    """One persistent engineer-agent workspace -- a durable checkout plus the container that
    works on it, scoped to one (user, repository) pair (see
    ``aura_core/domain/engineering.py``).

    Distinct from :class:`EngineerRunId`, which identifies one recorded *tool call*: a workspace
    outlives many runs, and unlike a run it is mutable, live state rather than a ledger row.
    """


@dataclass(frozen=True, slots=True)
class AgentRunId(Identifier):
    """One bounded agent run (``domain/agent_runs.py``): every plan-execute-observe-replan pass a
    single turn's persona made, for any persona -- not only ``engineer``. Distinct from
    :class:`EngineerRunId`, which identifies one engineer *tool call* inside such a run."""


@dataclass(frozen=True, slots=True)
class AgentStepId(Identifier):
    """One recorded step (observation, decision, tool call, approval, evaluation, failure,
    completion) of an :class:`AgentRunId`'s run."""


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
