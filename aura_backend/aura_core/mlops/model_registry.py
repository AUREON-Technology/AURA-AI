"""Model registry: versions, stages, promotion, rollback, A/B assignment (Phase 7).

The registry is the authority on which model serves production traffic. Two invariants it
enforces, both from ADR-008:

**Promotion to production requires a linked approval.** The registry refuses the transition
without one, so the human gate cannot be bypassed by calling a lower-level API.

**Rollback is always permitted.** Reverting to a previously-good version needs no new approval,
because reverting is safe by definition -- but only to a version that reached production through
that gate (INT-101), so rollback cannot become a way round it. This asymmetry is the same one the index manager
applies to knowledge generations.

Stages form a deliberate progression:

``candidate`` → ``shadow`` (mirrored traffic, no user impact) → ``canary`` (a traffic percentage,
with automatic rollback on metric regression) → ``production`` → ``archived``.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.errors import ApprovalRequiredError, ConflictError, NotFoundError, ValidationError
from ..domain.events import ModelPromoted
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent

__all__ = ["ModelStage", "ModelVersion", "ModelRegistry", "InMemoryModelStore", "ABAssignment"]

logger = get_logger("mlops.registry")


class ModelStage(str, enum.Enum):
    CANDIDATE = "candidate"
    SHADOW = "shadow"
    CANARY = "canary"
    PRODUCTION = "production"
    ARCHIVED = "archived"

    def __str__(self) -> str:
        return self.value


#: Legal transitions. Skipping straight from candidate to production is possible but requires
#: ``force``, so the shortcut is a deliberate, audited act rather than the default path.
_TRANSITIONS: Mapping[ModelStage, frozenset[ModelStage]] = {
    ModelStage.CANDIDATE: frozenset({ModelStage.SHADOW, ModelStage.CANARY, ModelStage.ARCHIVED}),
    ModelStage.SHADOW: frozenset({ModelStage.CANARY, ModelStage.CANDIDATE, ModelStage.ARCHIVED}),
    ModelStage.CANARY: frozenset({ModelStage.PRODUCTION, ModelStage.CANDIDATE, ModelStage.ARCHIVED}),
    ModelStage.PRODUCTION: frozenset({ModelStage.ARCHIVED}),
    ModelStage.ARCHIVED: frozenset({ModelStage.CANDIDATE}),
}


@dataclass(frozen=True, slots=True)
class ModelVersion:
    """One registered model version."""

    model_id: str
    version: str
    provider_kind: str
    base_model: str = ""
    stage: ModelStage = ModelStage.CANDIDATE
    artefact_uri: str = ""
    dataset_digest: str = ""
    approval_id: str | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)
    canary_percent: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    promoted_at: datetime | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.model_id or not self.version:
            raise ValidationError("model_id and version are both required")
        if not 0 <= self.canary_percent <= 100:
            raise ValidationError("canary_percent must be within [0, 100]")

    @property
    def key(self) -> str:
        return f"{self.model_id}@{self.version}"

    @property
    def serves_traffic(self) -> bool:
        return self.stage in (ModelStage.PRODUCTION, ModelStage.CANARY)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "provider_kind": self.provider_kind,
            "base_model": self.base_model,
            "stage": str(self.stage),
            "artefact_uri": self.artefact_uri,
            "dataset_digest": self.dataset_digest,
            "approval_id": self.approval_id,
            "metrics": dict(self.metrics),
            "canary_percent": self.canary_percent,
            "created_at": self.created_at.isoformat(),
            "promoted_at": self.promoted_at.isoformat() if self.promoted_at else None,
            "notes": self.notes,
        }


@runtime_checkable
class ModelStore(Protocol):
    async def put(self, version: ModelVersion) -> None:
        ...

    async def get(self, model_id: str, version: str) -> ModelVersion | None:
        ...

    async def list(self, *, model_id: str | None = None, stage: ModelStage | None = None
                   ) -> Sequence[ModelVersion]:
        ...


class InMemoryModelStore:
    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], ModelVersion] = {}

    async def put(self, version: ModelVersion) -> None:
        self._versions[(version.model_id, version.version)] = version

    async def get(self, model_id: str, version: str) -> ModelVersion | None:
        return self._versions.get((model_id, version))

    async def list(
        self, *, model_id: str | None = None, stage: ModelStage | None = None
    ) -> list[ModelVersion]:
        matches = [
            v for v in self._versions.values()
            if (model_id is None or v.model_id == model_id) and (stage is None or v.stage is stage)
        ]
        matches.sort(key=lambda v: v.created_at, reverse=True)
        return matches


@dataclass(frozen=True, slots=True)
class ABAssignment:
    """Which version a given user is routed to, and why."""

    model_id: str
    version: str
    stage: ModelStage
    bucket: int


class ModelRegistry:
    def __init__(
        self,
        *,
        store: ModelStore,
        approvals: Any = None,
        audit: Any = None,
        event_bus: Any = None,
        metrics: Any = None,
    ) -> None:
        self._store = store
        self._approvals = approvals
        self._audit = audit
        self._events = event_bus
        self._metrics = metrics

    # -- registration ------------------------------------------------------------------

    async def register(self, version: ModelVersion) -> ModelVersion:
        existing = await self._store.get(version.model_id, version.version)
        if existing is not None:
            raise ConflictError(
                f"{version.key} is already registered",
                details={"stage": str(existing.stage)},
            )
        await self._store.put(version)
        logger.info("model version registered", extra={"model": version.key})
        await self._record(
            action="model.register", actor="system", resource=version.key, outcome="registered",
            detail={"dataset_digest": version.dataset_digest},
        )
        return version

    async def get(self, model_id: str, version: str) -> ModelVersion:
        found = await self._store.get(model_id, version)
        if found is None:
            raise NotFoundError(f"unknown model version {model_id}@{version}")
        return found

    async def production(self, model_id: str) -> ModelVersion | None:
        versions = await self._store.list(model_id=model_id, stage=ModelStage.PRODUCTION)
        return versions[0] if versions else None

    async def list_versions(
        self, *, model_id: str | None = None, stage: ModelStage | None = None
    ) -> Sequence[ModelVersion]:
        return await self._store.list(model_id=model_id, stage=stage)

    # -- transitions -------------------------------------------------------------------

    async def transition(
        self,
        model_id: str,
        version: str,
        *,
        to: ModelStage,
        approval_id: str | None = None,
        canary_percent: int = 0,
        actor: str = "system",
        force: bool = False,
    ) -> ModelVersion:
        """Move a version between stages, enforcing the approval gate on production."""
        current = await self.get(model_id, version)

        if to not in _TRANSITIONS[current.stage] and not force:
            raise ConflictError(
                f"cannot move {current.key} from {current.stage} to {to}",
                details={"allowed": sorted(str(s) for s in _TRANSITIONS[current.stage])},
            )

        if to is ModelStage.PRODUCTION:
            # The gate. Refusing here rather than trusting the caller is what makes ADR-008
            # structural: no code path reaches production without a recorded human decision.
            # Promotion approvals bind to the model version key (model_id@version), not the
            # training dataset digest — a FINETUNE approval must not be reused as a silent
            # promote-to-production token.
            if self._approvals is None:
                raise ApprovalRequiredError(
                    "promotion to production requires an approval service",
                    details={"model": current.key},
                )
            await self._approvals.require(
                approval_id, expected_digest=current.key
            )

        if to is ModelStage.CANARY:
            # Soft eval gate: if prior metrics recorded a coverage drop beyond tolerance,
            # refuse canary. Missing metrics do not block (import paths).
            drop = current.metrics.get("coverage_drop")
            max_drop = current.metrics.get("max_coverage_drop")
            if drop is not None and max_drop is not None and drop > max_drop:
                raise ConflictError(
                    "canary blocked: answer coverage regressed beyond tolerance",
                    details={
                        "coverage_drop": drop,
                        "max_coverage_drop": max_drop,
                        "model": current.key,
                    },
                )

        promoted = replace(
            current,
            stage=to,
            approval_id=approval_id or current.approval_id,
            canary_percent=canary_percent if to is ModelStage.CANARY else 0,
            promoted_at=datetime.now(timezone.utc),
        )
        await self._store.put(promoted)

        # Exactly one production version per model: the previous one is archived in the same
        # operation, so there is never an ambiguous "which model is live?".
        if to is ModelStage.PRODUCTION:
            for other in await self._store.list(model_id=model_id, stage=ModelStage.PRODUCTION):
                if other.version != version:
                    await self._store.put(replace(other, stage=ModelStage.ARCHIVED))

        logger.info(
            "model stage changed",
            extra={"model": promoted.key, "from": str(current.stage), "to": str(to)},
        )
        await self._record(
            action=f"model.transition.{to}", actor=actor, resource=promoted.key,
            outcome="ok", detail={"from": str(current.stage), "approval": approval_id},
        )
        await self._publish(
            ModelPromoted(
                model_id=model_id, version=version, from_stage=str(current.stage),
                to_stage=str(to), approval_id=approval_id, metrics=dict(current.metrics),
            )
        )
        return promoted

    async def rollback(self, model_id: str, *, to_version: str, actor: str = "system"
                       ) -> ModelVersion:
        """Restore a previous version to production.

        Deliberately requires no *new* approval. A rollback is a return to a state that was already
        approved and already ran; blocking it behind a second approval would mean an incident
        waits on a reviewer. But only to such a state (INT-101): the target must have reached
        production through the gate, or "rollback" would promote any candidate around it.
        """
        target = await self.get(model_id, to_version)
        await self._require_was_approved_for_production(target)
        current = await self.production(model_id)

        restored = replace(
            target, stage=ModelStage.PRODUCTION, promoted_at=datetime.now(timezone.utc),
            canary_percent=0,
        )
        await self._store.put(restored)
        if current is not None and current.version != to_version:
            await self._store.put(replace(current, stage=ModelStage.ARCHIVED))

        logger.warning(
            "model rolled back",
            extra={"model": restored.key, "replaced": current.key if current else None},
        )
        await self._record(
            action="model.rollback", actor=actor, resource=restored.key, outcome="ok",
            detail={"replaced": current.key if current else None},
        )
        await self._publish(
            ModelPromoted(
                model_id=model_id, version=to_version,
                from_stage=str(target.stage), to_stage="production", metrics=dict(target.metrics),
            )
        )
        return restored

    async def _require_was_approved_for_production(self, target: ModelVersion) -> None:
        """Refuse a rollback target that never went live through the promotion gate.

        The evidence is what ``transition`` leaves behind when it promotes: the version's
        ``approval_id`` names an *approved* decision bound to exactly this version's key -- not a
        FINETUNE approval bound to a dataset digest (which an imported candidate carries), nor
        an id handed to a canary transition, which nothing checks. Expiry is not checked: the
        approval is the record that a person put this version live, not a fresh permission.
        The version must also have left the candidate track: archived (replaced in production)
        or still production."""
        def refuse(reason: str) -> ConflictError:
            return ConflictError(
                f"cannot roll back to {target.key}: {reason}",
                details={"model": target.key, "stage": str(target.stage),
                         "approval": target.approval_id},
            )

        if target.stage not in (ModelStage.ARCHIVED, ModelStage.PRODUCTION):
            raise refuse("it never served production (promote it through the approval gate)")
        if self._approvals is None or not target.approval_id:
            raise refuse("no production approval is recorded for it")
        approval = await self._approvals.get(target.approval_id)
        if (
            approval is None
            or str(approval.status) != "approved"
            or approval.artefact_digest != target.key
        ):
            raise refuse("it was not approved for production")

    async def record_metrics(
        self, model_id: str, version: str, metrics: Mapping[str, float]
    ) -> ModelVersion:
        current = await self.get(model_id, version)
        merged = {**current.metrics, **metrics}
        updated = replace(current, metrics=merged)
        await self._store.put(updated)
        if self._metrics is not None:
            for name, value in metrics.items():
                self._metrics.eval_score.labels(suite=f"{model_id}@{version}", metric=name).set(value)
        return updated

    # -- traffic assignment ------------------------------------------------------------

    async def assign(self, model_id: str, *, subject: str) -> ABAssignment | None:
        """Route one subject to production or canary.

        Assignment is a stable hash of ``(model_id, subject)``, not a coin flip: a user must see
        the same variant on every request, or an A/B comparison measures variance in routing
        rather than a difference between models.
        """
        production = await self.production(model_id)
        canaries = await self._store.list(model_id=model_id, stage=ModelStage.CANARY)

        if production is None and not canaries:
            return None

        bucket = (
            int(hashlib.sha256(f"{model_id}:{subject}".encode()).hexdigest()[:8], 16) % 100
        )

        cumulative = 0
        for canary in canaries:
            cumulative += canary.canary_percent
            if bucket < cumulative:
                return ABAssignment(
                    model_id=model_id, version=canary.version, stage=ModelStage.CANARY,
                    bucket=bucket,
                )

        if production is None:
            return None
        return ABAssignment(
            model_id=model_id, version=production.version, stage=ModelStage.PRODUCTION,
            bucket=bucket,
        )

    async def shadow_versions(self, model_id: str) -> Sequence[ModelVersion]:
        """Versions receiving mirrored traffic. Their output is never returned to a user."""
        return await self._store.list(model_id=model_id, stage=ModelStage.SHADOW)

    async def inventory(self) -> list[dict[str, Any]]:
        return [v.as_dict() for v in await self._store.list()]

    async def _record(self, **kwargs: Any) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(AuditEvent(**kwargs))
        except Exception:  # pragma: no cover - defensive
            logger.debug("model registry audit write failed", exc_info=True)

    async def _publish(self, event: Any) -> None:
        if self._events is None:
            return
        try:
            await self._events.publish(event)
        except Exception:  # pragma: no cover - defensive
            logger.debug("model registry event publication failed", exc_info=True)
