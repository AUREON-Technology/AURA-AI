"""Blueprint lifecycle: evaluate, approve, deploy, roll back (AP7, ADR-024).

Modelled on ``IndexManager``'s promote/rollback: one active-version pointer per project, moved
atomically by the store, and every step recorded in the audit log.

The order of a deploy is what keeps it safe:

1. the stored version must be ``approved`` -- i.e. a ``BLUEPRINT_DEPLOYMENT`` approval bound to
   **its digest** was granted (:meth:`approve`); a blueprint edited after review would carry a
   different digest and is refused;
2. its tools are registered under ``bp<version>_*`` -- beside the old ones, not over them;
3. the store moves the pointer (new version ``active``, old ``superseded``) in one transaction;
   if that fails the new tools are unregistered and nothing else changed;
4. the agent snapshot is swapped in one assignment;
5. the old version's tools are retired after a grace period, so a turn that resolved them just
   before the swap can finish;
6. project knowledge (AP10) is synced to the live version's ``knowledge.sources`` -- after the
   pointer moved, so an unreadable document is a reported failure, not a blocked deploy.

Rollback is the same path aimed at a ``superseded`` version, under the approval that version
already carried (its digest is unchanged, so that approval still binds it).

:meth:`evaluate` runs the AP8 gate (``adaptation/gate.py``) against the twin the version was
compiled from and stores the report on the record whatever the outcome. In ``enforce`` mode a
version whose gating suites fail stays ``proposed`` -- there is nothing to approve -- and in
``advisory`` mode it advances with the failure recorded (``gate="ap8:advisory"``). Either way the
approval request carries the gate's result, so the person deciding sees it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from datetime import timezone
from typing import Any

from ..domain.blueprint import BlueprintState
from ..domain.compilation import ActivePointer, BlueprintRecord
from ..domain.errors import ConflictError, NotFoundError, ValidationError
from ..domain.gate import GateMode
from ..learning.approval import ApprovalKind
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent
from .access import expand_capabilities, grants_by_aura_role
from .runtime import ActiveBlueprint, BlueprintToolMaterializer, generated_agent_spec

__all__ = ["BlueprintDeployment", "DEPLOYMENT_APPROVAL_KIND"]

logger = get_logger("adaptation.deployment")

DEPLOYMENT_APPROVAL_KIND = "blueprint_deployment"


class BlueprintDeployment:
    def __init__(
        self,
        *,
        project_id: str,
        store: Any,
        approvals: Any,
        agents: ActiveBlueprint,
        materializer: BlueprintToolMaterializer,
        clock: Any,
        gate: Any = None,
        twins: Any = None,
        audit: Any = None,
        metrics: Any = None,
        knowledge: Any = None,
    ) -> None:
        self._project_id = project_id
        self._store = store
        self._approvals = approvals
        self._agents = agents
        #: AURA role -> role-derived capabilities under the loaded blueprint (access.py).
        self._grants: dict[str, frozenset[str]] = {}
        self._materializer = materializer
        self._clock = clock
        self._gate = gate
        self._twins = twins
        self._audit = audit
        self._metrics = metrics
        self._knowledge = knowledge
        self._status: dict[str, Any] = {"state": "core_only"}
        # One go-live at a time in this process, so agent swaps land in pointer order.
        self._lock = asyncio.Lock()
        self._loaded_version: int | None = None

    @property
    def project_id(self) -> str:
        return self._project_id

    # -- lifecycle --------------------------------------------------------------------------

    async def evaluate(self, version: int, *, by: str) -> BlueprintRecord:
        """Run the gate. Passed (or advisory): ``proposed`` -> ``evaluated``. Failed and enforced:
        stays ``proposed``, with the report stored so the reviewer can see what blocked it."""
        record = await self._require(version, BlueprintState.PROPOSED)
        if self._gate is None or self._twins is None:
            raise ConflictError("no evaluation gate is configured on this install", details={"version": version})
        found = await self._twins.get(record.blueprint.project.twin_version)
        if found is None or found[1].digest != record.blueprint.project.twin_digest:
            raise ConflictError("the twin this blueprint was compiled from is no longer stored",
                                details={"twin_version": record.blueprint.project.twin_version})
        report = await self._gate.evaluate(record, found[1], by=by)
        advances = report.passed or report.mode is GateMode.ADVISORY
        if advances:
            updated = dataclasses.replace(record, state=BlueprintState.EVALUATED, gate=f"ap8:{report.mode.value}",
                                          evaluation=report)
        else:
            updated = dataclasses.replace(record, evaluation=report)
        await self._store.transition(updated, expected=BlueprintState.PROPOSED)
        outcome = "passed" if report.passed else ("failed_advisory" if advances else "failed")
        await self._record("evaluate", by, updated, outcome, {"blocking": list(report.blocking())})
        return updated

    async def request_approval(self, version: int, *, by: str) -> Any:
        """Ask for a ``BLUEPRINT_DEPLOYMENT`` decision bound to this version's digest."""
        record = await self._require(version, BlueprintState.EVALUATED)
        bp = record.blueprint
        elevated = [a.id for a in bp.elevated_agents()]
        report = record.evaluation
        if report is None:
            verdict = f"gate: {record.gate}"
        elif report.passed:
            verdict = "evaluation gate passed"
        else:
            verdict = f"evaluation gate FAILED ({', '.join(report.blocking())}), advisory mode"
        request = await self._approvals.request(
            kind=ApprovalKind.BLUEPRINT_DEPLOYMENT,
            summary=(f"Deploy blueprint v{version}: {len(bp.tools)} read-only tools, "
                     f"{len(bp.agents)} agents" + (f", elevated: {', '.join(elevated)}" if elevated else "")
                     + f"; {verdict}"),
            artefact_digest=record.digest,
            payload={"project_id": self._project_id, "version": version, "gate": record.gate,
                     "twin_version": bp.project.twin_version,
                     "evaluation": report.summary() if report is not None else None},
            requested_by=by,
        )
        await self._record("request_approval", by, record, "pending", {"approval": request.id})
        return request

    async def approve(self, version: int, *, approval_id: str, by: str) -> BlueprintRecord:
        """``evaluated`` -> ``approved``, consuming a granted approval for exactly this digest."""
        record = await self._require(version, BlueprintState.EVALUATED)
        approval = await self._approvals.require(approval_id, expected_digest=record.digest)
        self._check_approval(approval, version)
        approved = dataclasses.replace(record, state=BlueprintState.APPROVED, approval_id=approval.id)
        await self._store.transition(approved, expected=BlueprintState.EVALUATED)
        await self._record("approve", by, approved, "approved", {"approval": approval.id})
        return approved

    async def deploy(self, version: int, *, by: str) -> ActivePointer:
        """Make an ``approved`` version live (see the module docstring for the order)."""
        record = await self._require(version, BlueprintState.APPROVED)
        await self._recheck_approval(record)
        return await self._go_live(record, by=by, action="deploy")

    async def rollback(self, *, by: str, version: int | None = None) -> ActivePointer:
        """Return to ``version`` (default: the previously active one), which must be superseded."""
        pointer = await self._store.active(self._project_id)
        if version is None:
            if pointer is None or pointer.previous_version is None:
                raise ConflictError("there is no previous blueprint version to roll back to")
            version = pointer.previous_version
        record = await self._require(version, BlueprintState.SUPERSEDED)
        await self._recheck_approval(record, allow_expired=True)
        return await self._go_live(record, by=by, action="rollback")

    async def active(self) -> ActivePointer | None:
        return await self._store.active(self._project_id)

    def caller_capabilities(self, capabilities: tuple[str, ...], aura_role: str) -> tuple[str, ...]:
        """``capabilities`` plus what the live blueprint binds ``aura_role`` to (AP10).

        Read from this process's loaded snapshot, the same one its agents and tools come from,
        so a caller is never granted against a version this worker is not serving."""
        return expand_capabilities(capabilities, aura_role, self._grants)

    # -- startup and readiness --------------------------------------------------------------

    async def startup(self) -> None:
        """Load the active version, or fall back to core personas only and say why."""
        async with self._lock:
            await self._load()

    async def sync(self) -> bool:
        """Reconcile this process with the store's pointer; True if anything changed.

        A deploy swaps the snapshot in the process that ran it. Every other gateway worker
        catches up here (the container runs it periodically), so all workers converge on the
        pointer within one sync interval.
        """
        async with self._lock:
            try:
                pointer = await self._store.active(self._project_id)
            except Exception:  # store briefly unreachable: keep serving what is loaded
                logger.debug("blueprint sync skipped", exc_info=True)
                return False
            wanted = pointer.version if pointer is not None else None
            if wanted == self._loaded_version and self._status.get("state") != "degraded":
                return False
            previous = self._loaded_version
            await self._load()
            if previous is not None and previous != self._loaded_version:
                self._materializer.retire(previous)
            return True

    async def _load(self) -> None:
        try:
            pointer = await self._store.active(self._project_id)
            if pointer is None:
                self._agents.reset()
                self._grants = {}
                self._loaded_version = None
                self._status = {"state": "core_only"}
                return
            record = await self._store.get(self._project_id, pointer.version)
            if record is None or record.digest != pointer.digest or record.state is not BlueprintState.ACTIVE:
                raise ValidationError("the active pointer does not match a live blueprint")
            names = [] if self._materializer.names_of(record.version) else self._materializer.register(record.blueprint)
            try:
                self._swap_agents(record)
            except Exception:
                self._materializer.unregister(names)
                raise
            self._loaded_version = record.version
            self._status = {"state": "active", "version": record.version, "digest": record.digest}
            self._observe_active(record.version)
        except Exception as exc:
            # A corrupt or unreadable blueprint must not take the assistant down: core-only, and
            # readiness reports the degradation so an operator notices.
            logger.warning("active blueprint failed to load; running core-only",
                           extra={"error": type(exc).__name__})
            self._agents.reset()
            self._grants = {}
            self._loaded_version = None
            self._status = {"state": "degraded", "reason": type(exc).__name__}
            self._observe_active(0)

    async def readiness(self) -> dict[str, Any]:
        """For ``register_readiness_check``: healthy unless nothing works; degradation reported."""
        return {"healthy": True, **self._status}

    # -- internals --------------------------------------------------------------------------

    async def _go_live(self, record: BlueprintRecord, *, by: str, action: str) -> ActivePointer:
        async with self._lock:
            return await self._go_live_locked(record, by=by, action=action)

    async def _go_live_locked(self, record: BlueprintRecord, *, by: str, action: str) -> ActivePointer:
        started = time.perf_counter()
        previous = await self._store.active(self._project_id)
        names = self._materializer.register(record.blueprint)
        try:
            pointer = await self._store.activate(self._project_id, record.version, by=by, at=self._now())
        except Exception:
            self._materializer.unregister(names)
            raise
        self._swap_agents(record)
        self._loaded_version = record.version
        if previous is not None and previous.version != record.version:
            self._materializer.retire(previous.version)
        self._status = {"state": "active", "version": record.version, "digest": record.digest}
        seconds = time.perf_counter() - started
        if self._metrics is not None:
            self._metrics.observe_blueprint_swap(seconds)
        self._observe_active(record.version)
        detail: dict[str, Any] = {"previous": previous.version if previous else None, "seconds": round(seconds, 3)}
        knowledge = await self._sync_knowledge(record)
        if knowledge is not None:
            detail["knowledge"] = knowledge
            self._status["knowledge"] = knowledge
        await self._record(action, by, record, "active", detail)
        return pointer

    async def _sync_knowledge(self, record: BlueprintRecord) -> dict[str, Any] | None:
        """Bring project knowledge in line with the version now live (AP10).

        After the pointer moved, never before it: a document that cannot be read leaves the
        blueprint live with that source reported as failed, rather than blocking the deploy -- and
        a rollback re-syncs to the restored version's sources the same way.
        """
        if self._knowledge is None:
            return None
        try:
            twin = None
            if self._twins is not None:
                found = await self._twins.get(record.blueprint.project.twin_version)
                twin = found[1] if found is not None else None
            report = await self._knowledge.sync(record.blueprint, twin)
        except Exception as exc:
            logger.warning("project knowledge sync failed", extra={"error": type(exc).__name__})
            return {"version": record.version, "error": type(exc).__name__}
        return report.summary()

    def _swap_agents(self, record: BlueprintRecord) -> None:
        bp = record.blueprint
        names = self._materializer.registry_names(bp)
        specs = [generated_agent_spec(agent, names, blueprint=bp) for agent in bp.agents]
        self._agents.swap(specs, version=record.version, digest=record.digest)
        self._grants = grants_by_aura_role(bp)

    async def _require(self, version: int, state: BlueprintState) -> BlueprintRecord:
        record = await self._store.get(self._project_id, version)
        if record is None:
            raise NotFoundError("blueprint version not found", details={"version": version})
        if record.state is not state:
            raise ConflictError(
                f"blueprint version {version} is {record.state.value}, not {state.value}",
                details={"version": version, "state": record.state.value},
            )
        return record

    def _check_approval(self, approval: Any, version: int) -> None:
        if str(approval.kind) != DEPLOYMENT_APPROVAL_KIND or approval.payload.get("version") != version \
                or approval.payload.get("project_id") != self._project_id:
            raise ConflictError("that approval was not granted for this blueprint version",
                                details={"approval": approval.id})

    async def _recheck_approval(self, record: BlueprintRecord, *, allow_expired: bool = False) -> None:
        """The recorded approval still exists, was granted, and binds this exact digest."""
        if not allow_expired:
            approval = await self._approvals.require(record.approval_id, expected_digest=record.digest)
        else:
            # A rollback restores a version that was already approved and live; its approval
            # window having closed since must not block the safety path. Status and digest still
            # must match.
            approval = await self._approvals.get(record.approval_id) if record.approval_id else None
            if approval is None or str(approval.status) != "approved" or approval.artefact_digest != record.digest:
                raise ConflictError("this version's approval no longer binds its digest",
                                    details={"version": record.version})
        self._check_approval(approval, record.version)

    def _observe_active(self, version: int) -> None:
        if self._metrics is not None:
            self._metrics.set_blueprint_active(version)

    def _now(self) -> str:
        return self._clock.now().astimezone(timezone.utc).isoformat()

    async def _record(self, action: str, by: str, record: BlueprintRecord, outcome: str,
                      extra: dict[str, Any] | None = None) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(AuditEvent(
                action=f"adaptation.blueprint.{action}", actor=by,
                resource=f"blueprint:{record.project_id}:{record.version}", outcome=outcome,
                detail={"digest": record.digest, **(extra or {})},
            ))
        except Exception:  # pragma: no cover - defensive
            logger.debug("blueprint audit write failed", exc_info=True)
