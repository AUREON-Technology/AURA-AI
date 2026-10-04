"""Human approval gate for model changes (ADR-008).

The structural boundary between "the system may prepare" and "a person decides". Three rules,
each enforced here rather than by convention:

1. **An approval names a person and a time.** A configuration flag is not an approval; it has no
   accountable party and leaves no record.
2. **An approval is bound to an artefact.** It carries the dataset digest and the evaluation
   report it was granted against, so an approved dataset cannot be swapped for a different one
   between approval and training.
3. **Rollback needs no approval.** Reverting to a known-good state is safe by definition;
   advancing is not. The asymmetry is deliberate and mirrors ADR-007's treatment of indexes.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.errors import ApprovalRequiredError, ConflictError, NotFoundError, ValidationError
from ..domain.events import ApprovalDecided, ApprovalRequested
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent

__all__ = [
    "ApprovalKind",
    "ApprovalStatus",
    "ApprovalRequest",
    "ApprovalStore",
    "InMemoryApprovalStore",
    "ApprovalService",
]

logger = get_logger("learning.approval")


class ApprovalKind(str, enum.Enum):
    FINETUNE = "finetune"
    MODEL_PROMOTION = "model_promotion"
    INDEX_PROMOTION = "index_promotion"
    SOURCE_REGISTRATION = "source_registration"
    #: A Super Admin's decision to let AURA reach a connected system (ADR-021, Track AP2).
    CONNECTOR_REGISTRATION = "connector_registration"
    #: A Super Admin's decision to put one compiled blueprint version live (ADR-024, Track AP7).
    #: Bound to the blueprint's digest: a blueprint changed after review needs a new approval.
    BLUEPRINT_DEPLOYMENT = "blueprint_deployment"

    def __str__(self) -> str:
        return self.value


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """A request for a human decision, with everything needed to make it."""

    id: str
    kind: ApprovalKind
    summary: str
    artefact_digest: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    note: str = ""

    @classmethod
    def create(
        cls,
        *,
        kind: ApprovalKind,
        summary: str,
        artefact_digest: str,
        payload: Mapping[str, Any] | None = None,
        ttl: timedelta = timedelta(days=14),
    ) -> "ApprovalRequest":
        now = datetime.now(timezone.utc)
        return cls(
            id=f"apr_{uuid.uuid4().hex[:16]}",
            kind=kind,
            summary=summary,
            artefact_digest=artefact_digest,
            payload=dict(payload or {}),
            requested_at=now,
            # Approvals expire. A six-month-old approval refers to a dataset and an evaluation
            # that no longer describe the system it would be applied to.
            expires_at=now + ttl if ttl else None,
        )

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and datetime.now(timezone.utc) >= self.expires_at

    @property
    def is_actionable(self) -> bool:
        return self.status is ApprovalStatus.APPROVED and not self.is_expired

    def decided(self, *, approved: bool, by: str, note: str = "") -> "ApprovalRequest":
        return replace(
            self,
            status=ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
            decided_by=by,
            decided_at=datetime.now(timezone.utc),
            note=note,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": str(self.kind),
            "summary": self.summary,
            "artefact_digest": self.artefact_digest,
            "status": str(self.status),
            "requested_at": self.requested_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "note": self.note,
            "payload": dict(self.payload),
        }


@runtime_checkable
class ApprovalStore(Protocol):
    async def put(self, request: ApprovalRequest) -> None:
        ...

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        ...

    async def list(
        self, *, status: ApprovalStatus | None = None, kind: ApprovalKind | None = None,
        limit: int = 50,
    ) -> Sequence[ApprovalRequest]:
        ...


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}

    async def put(self, request: ApprovalRequest) -> None:
        self._requests[request.id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._requests.get(approval_id)

    async def list(
        self, *, status: ApprovalStatus | None = None, kind: ApprovalKind | None = None,
        limit: int = 50,
    ) -> list[ApprovalRequest]:
        matches = [
            r for r in self._requests.values()
            if (status is None or r.status is status) and (kind is None or r.kind is kind)
        ]
        matches.sort(key=lambda r: r.requested_at, reverse=True)
        return matches[:limit]


class ApprovalService:
    """Creates, decides, and consumes approvals -- all audited."""

    def __init__(
        self, *, store: ApprovalStore, audit: Any = None, event_bus: Any = None
    ) -> None:
        self._store = store
        self._audit = audit
        self._events = event_bus

    async def request(
        self,
        *,
        kind: ApprovalKind,
        summary: str,
        artefact_digest: str,
        payload: Mapping[str, Any] | None = None,
        ttl: timedelta = timedelta(days=14),
    ) -> ApprovalRequest:
        request = ApprovalRequest.create(
            kind=kind, summary=summary, artefact_digest=artefact_digest,
            payload=payload, ttl=ttl,
        )
        await self._store.put(request)
        logger.info(
            "approval requested",
            extra={"approval": request.id, "kind": str(kind), "digest": artefact_digest},
        )
        await self._record(
            action=f"approval.request.{kind}", actor="system", resource=request.id,
            outcome="pending", detail={"digest": artefact_digest},
        )
        await self._publish(
            ApprovalRequested(approval_id=request.id, kind=str(kind), summary=summary)
        )
        return request

    async def decide(
        self, approval_id: str, *, approved: bool, by: str, note: str = ""
    ) -> ApprovalRequest:
        """Record a human decision.

        ``by`` is required and must be a real identity -- it is the accountable party, and an
        anonymous approval would defeat the purpose of having one.
        """
        if not by.strip():
            raise ValidationError("an approval decision must name the deciding user")

        request = await self._store.get(approval_id)
        if request is None:
            raise NotFoundError(f"unknown approval {approval_id!r}")
        if request.status is not ApprovalStatus.PENDING:
            raise ConflictError(
                f"approval {approval_id} is already {request.status}",
                details={"status": str(request.status), "decided_by": request.decided_by},
            )
        if request.is_expired:
            expired = replace(request, status=ApprovalStatus.EXPIRED)
            await self._store.put(expired)
            raise ConflictError(
                f"approval {approval_id} expired on "
                f"{request.expires_at.isoformat() if request.expires_at else 'unknown'}"
            )

        decided = request.decided(approved=approved, by=by, note=note)
        await self._store.put(decided)
        logger.info(
            "approval decided",
            extra={"approval": approval_id, "approved": approved, "by": by},
        )
        await self._record(
            action=f"approval.decide.{request.kind}", actor=by, resource=approval_id,
            outcome="approved" if approved else "rejected",
            detail={"digest": request.artefact_digest, "note": note[:200]},
        )
        await self._publish(
            ApprovalDecided(
                approval_id=approval_id, approved=approved, decided_by=by, note=note
            )
        )
        return decided

    async def require(self, approval_id: str | None, *, expected_digest: str) -> ApprovalRequest:
        """Assert that an approved, unexpired approval exists for exactly this artefact.

        The digest check is what makes an approval binding rather than decorative: without it,
        an approval granted for one dataset could be used to train on another.
        """
        if not approval_id:
            raise ApprovalRequiredError(
                "this operation requires a recorded human approval",
                details={"expected_digest": expected_digest},
            )
        request = await self._store.get(approval_id)
        if request is None:
            raise NotFoundError(f"unknown approval {approval_id!r}")
        if not request.is_actionable:
            raise ApprovalRequiredError(
                f"approval {approval_id} is not actionable ({request.status}"
                f"{', expired' if request.is_expired else ''})",
                details={"status": str(request.status)},
            )
        if request.artefact_digest != expected_digest:
            raise ConflictError(
                "the approval was granted for a different artefact",
                details={
                    "approved_digest": request.artefact_digest,
                    "requested_digest": expected_digest,
                },
            )
        return request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        """The approval as recorded, whatever its status. Read-only."""
        return await self._store.get(approval_id)

    async def pending(self, *, kind: ApprovalKind | None = None) -> Sequence[ApprovalRequest]:
        return await self._store.list(status=ApprovalStatus.PENDING, kind=kind)

    async def _record(self, **kwargs: Any) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(AuditEvent(**kwargs))
        except Exception:  # pragma: no cover - defensive
            logger.debug("approval audit write failed", exc_info=True)

    async def _publish(self, event: Any) -> None:
        if self._events is None:
            return
        try:
            await self._events.publish(event)
        except Exception:  # pragma: no cover - defensive
            logger.debug("approval event publication failed", exc_info=True)
