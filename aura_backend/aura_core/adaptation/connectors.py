"""The connector registry: register, approve, activate, check and retire connectors (AP2).

The lifecycle is the authority boundary for Track AP, so every step is explicit:

1. **Register** (Super Admin). The credential, if any, goes straight into the vault; the
   descriptor keeps only the vault's ``secret_ref``. A ``CONNECTOR_REGISTRATION`` approval is
   requested against the descriptor's digest and the connector waits in ``pending_approval``.
2. **Decide** (any admin, through the existing approvals flow). A decision names a person.
3. **Activate** (Super Admin). Refused unless the approval is approved, unexpired, of the right
   kind, for this connector, and bound to the descriptor's *current* digest -- so a descriptor (or
   its ``secret_ref``) changed after review cannot ride the old approval. The connector must also
   be healthy and its credential provably unable to write (ADR-021, read-only v1).
4. **Disable / remove** (Super Admin). Removal also deletes the vaulted credential.

Nothing outside this module hands out an open connector: :meth:`ConnectorRegistry.open_active`
refuses anything that is not ``active``.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Any, Mapping, Sequence

from ..domain.connectors import (
    ConnectorDescriptor,
    ConnectorHealth,
    ConnectorKind,
    ConnectorRecord,
    ConnectorState,
    Credential,
    HealthStatus,
    IdentityMode,
)
from ..domain.errors import AuraError, ConflictError, NotFoundError, ValidationError
from ..learning.approval import ApprovalKind, ApprovalRequest, ApprovalService
from ..observability.logging import get_logger
from ..ports.connectors import Connector, ConnectorFactory, ConnectorStore, CredentialVault
from ..ports.runtime import AuditEvent, Clock

__all__ = ["ConnectorRegistry", "REGISTRATION_APPROVAL_TTL"]

logger = get_logger("adaptation.connectors")

#: Long enough for a review, short enough that a forgotten request does not stay grantable.
REGISTRATION_APPROVAL_TTL = timedelta(days=7)


class ConnectorRegistry:
    def __init__(
        self,
        *,
        store: ConnectorStore,
        vault: CredentialVault,
        approvals: ApprovalService,
        factories: Mapping[ConnectorKind, ConnectorFactory],
        clock: Clock,
        audit: Any = None,
        metrics: Any = None,
    ) -> None:
        self._store = store
        self._vault = vault
        self._approvals = approvals
        self._factories = dict(factories)
        self._clock = clock
        self._audit = audit
        self._metrics = metrics
        # One registration/activation per connector id at a time; the store is not transactional.
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def supported_kinds(self) -> frozenset[ConnectorKind]:
        return frozenset(self._factories)

    def register_factory(self, kind: ConnectorKind, factory: ConnectorFactory) -> None:
        """Add (or replace) the factory for one connector kind -- the extension point later
        phases use for MySQL, SQL Server, OpenAPI, files and Git (AP3, AP10)."""
        self._factories[kind] = factory

    # ---------------------------------------------------------------------------------- reads

    async def get(self, connector_id: str) -> ConnectorRecord:
        record = await self._store.get(connector_id)
        if record is None:
            raise NotFoundError(f"unknown connector {connector_id!r}")
        return record

    async def list(self, *, state: ConnectorState | None = None) -> Sequence[ConnectorRecord]:
        return await self._store.list(state=state)

    # ------------------------------------------------------------------------------ lifecycle

    async def register(
        self,
        descriptor: ConnectorDescriptor,
        *,
        secret: Credential | None,
        requested_by: str,
    ) -> tuple[ConnectorRecord, ApprovalRequest]:
        """Store the credential, record the connector and request its approval."""
        if not requested_by.strip():
            raise ValidationError("a registration must name who requested it")
        if descriptor.secret_ref is not None:
            raise ValidationError("secret_ref is assigned by the vault, not supplied")
        if descriptor.kind not in self._factories:
            raise ValidationError(
                f"no {descriptor.kind} connector is available in this build",
                details={"kind": str(descriptor.kind), "supported": sorted(str(k) for k in self._factories)},
            )
        if descriptor.identity_mode is IdentityMode.DELEGATED and secret is not None:
            raise ValidationError("a delegated connector uses each caller's own token and stores no credential")
        if descriptor.needs_credential and secret is None:
            raise ValidationError(f"a {descriptor.kind} connector needs a credential")

        async with self._lock(descriptor.id):
            if await self._store.get(descriptor.id) is not None:
                raise ConflictError(f"connector {descriptor.id!r} already exists")
            secret_ref = await self._vault.put(secret) if secret is not None else None
            try:
                descriptor = replace(descriptor, secret_ref=secret_ref)
                approval = await self._approvals.request(
                    kind=ApprovalKind.CONNECTOR_REGISTRATION,
                    summary=f"Connect {descriptor.kind} source {descriptor.id!r} ({descriptor.display_name})",
                    artefact_digest=descriptor.digest,
                    payload=_approval_payload(descriptor),
                    ttl=REGISTRATION_APPROVAL_TTL,
                )
                record = ConnectorRecord(
                    descriptor=descriptor,
                    state=ConnectorState.PENDING_APPROVAL,
                    registered_by=requested_by,
                    registered_at=self._clock.now(),
                    approval_id=approval.id,
                )
                await self._store.put(record)
            except BaseException:
                # Never leave an orphaned credential behind a failed registration.
                if secret_ref is not None:
                    await self._vault.delete(secret_ref)
                raise
        await self._record("adaptation.connector.register", requested_by, record, "pending",
                           {"approval": approval.id, "digest": descriptor.digest})
        logger.info("connector registered", extra={"connector": record.id, "kind": str(descriptor.kind)})
        return record, approval

    async def activate(self, connector_id: str, *, approval_id: str, by: str) -> ConnectorRecord:
        """Bring an approved, healthy, read-only connector into service."""
        async with self._lock(connector_id):
            record = await self.get(connector_id)
            if record.state is ConnectorState.ACTIVE:
                raise ConflictError(f"connector {connector_id!r} is already active")
            approval = await self._approvals.require(approval_id, expected_digest=record.descriptor.digest)
            if approval.kind is not ApprovalKind.CONNECTOR_REGISTRATION or approval.payload.get(
                "connector_id"
            ) != connector_id:
                raise ConflictError(
                    "that approval was not granted for this connector",
                    details={"approval": approval_id, "kind": str(approval.kind)},
                )
            health = await self._check(record)
            if not health.ok:
                await self._record("adaptation.connector.activate", by, record, "refused_unhealthy",
                                   {"reason": health.reason})
                raise ConflictError(
                    "connector is not healthy; fix it and check again before activating",
                    details={"reason": health.reason},
                )
            if health.write_capable is not False:
                await self._record("adaptation.connector.activate", by, record, "refused_write_capable",
                                   {"findings": list(health.findings)})
                raise ConflictError(
                    "the connector's credential can change the target system; v1 connectors must "
                    "use a read-only credential",
                    details={"findings": list(health.findings), "write_capable": health.write_capable},
                )
            activated = replace(
                record,
                state=ConnectorState.ACTIVE,
                approval_id=approval.id,
                activated_by=by,
                activated_at=self._clock.now(),
            )
            await self._store.put(activated)
        await self._record("adaptation.connector.activate", by, activated, "ok", {"approval": approval.id})
        return activated

    async def disable(self, connector_id: str, *, by: str) -> ConnectorRecord:
        """Take a connector out of service. Bringing it back needs a fresh approval."""
        async with self._lock(connector_id):
            record = await self.get(connector_id)
            disabled = replace(record, state=ConnectorState.DISABLED)
            await self._store.put(disabled)
        await self._record("adaptation.connector.disable", by, disabled, "ok", {})
        return disabled

    async def remove(self, connector_id: str, *, by: str) -> bool:
        """Delete the connector and its vaulted credential."""
        async with self._lock(connector_id):
            record = await self._store.get(connector_id)
            if record is None:
                return False
            if record.descriptor.secret_ref is not None:
                await self._vault.delete(record.descriptor.secret_ref)
            await self._store.delete(connector_id)
        await self._record("adaptation.connector.remove", by, record, "ok", {})
        return True

    # --------------------------------------------------------------------------------- health

    async def health(self, connector_id: str) -> ConnectorHealth:
        """Check any registered connector, whatever its state (the Console's "test connection")."""
        return await self._check(await self.get(connector_id))

    async def open_active(self, connector_id: str) -> Connector:
        """An open connector for later phases (discovery, reads). Active connectors only."""
        record = await self.get(connector_id)
        if record.state is not ConnectorState.ACTIVE:
            raise ConflictError(
                f"connector {connector_id!r} is {record.state}, not active",
                details={"state": str(record.state)},
            )
        return await self._open(record)

    async def _open(self, record: ConnectorRecord) -> Connector:
        credential = None
        if record.descriptor.secret_ref is not None:
            credential = await self._vault.get(record.descriptor.secret_ref)
        return await self._factories[record.descriptor.kind].open(record.descriptor, credential)

    async def _check(self, record: ConnectorRecord) -> ConnectorHealth:
        descriptor = record.descriptor
        if descriptor.kind not in self._factories:
            return self._observe(record, ConnectorHealth(HealthStatus.UNHEALTHY, "unsupported_kind"))
        started = self._clock.monotonic()
        try:
            connector = await self._open(record)
        except AuraError as exc:
            # Vault unavailable, key rotated away, tampered ciphertext: unhealthy, never a
            # fallback to anything stored unencrypted.
            logger.warning("connector credential unavailable", extra={"connector": record.id, "kind": exc.kind})
            return self._observe(record, ConnectorHealth(HealthStatus.UNHEALTHY, "credential_unavailable"))
        try:
            result = await asyncio.wait_for(connector.health(), timeout=descriptor.timeout_s + 1.0)
        except asyncio.TimeoutError:
            result = ConnectorHealth(HealthStatus.UNHEALTHY, "timeout")
        except Exception as exc:  # a connector must not raise; if one does, report its type only
            logger.warning("connector health raised", extra={"connector": record.id, "error": type(exc).__name__})
            result = ConnectorHealth(HealthStatus.UNHEALTHY, "connector_error")
        finally:
            try:
                await connector.aclose()
            except Exception:  # pragma: no cover - defensive
                logger.debug("connector close failed", exc_info=True)
        if not result.latency_ms:
            result = replace(result, latency_ms=round((self._clock.monotonic() - started) * 1000.0, 1))
        return self._observe(record, result)

    def _observe(self, record: ConnectorRecord, health: ConnectorHealth) -> ConnectorHealth:
        if self._metrics is not None:
            self._metrics.set_connector_health(record.id, str(record.descriptor.kind), str(health.status))
        return health

    # -------------------------------------------------------------------------------- helpers

    def _lock(self, connector_id: str) -> asyncio.Lock:
        return self._locks.setdefault(connector_id, asyncio.Lock())

    async def _record(
        self, action: str, actor: str, record: ConnectorRecord, outcome: str, detail: Mapping[str, Any]
    ) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(
                AuditEvent(
                    action=action,
                    actor=actor,
                    resource=f"connector:{record.id}",
                    outcome=outcome,
                    detail={"kind": str(record.descriptor.kind), **detail},
                )
            )
        except Exception:  # pragma: no cover - defensive, same as ApprovalService
            logger.debug("connector audit write failed", exc_info=True)


def _approval_payload(descriptor: ConnectorDescriptor) -> dict[str, Any]:
    """What the approver sees. Everything here is non-secret by construction of the descriptor."""
    return {
        "connector_id": descriptor.id,
        "kind": str(descriptor.kind),
        "identity_mode": str(descriptor.identity_mode),
        "display_name": descriptor.display_name,
        "endpoint": descriptor.endpoint,
        "options": dict(descriptor.options),
        "has_credential": descriptor.secret_ref is not None,
    }
