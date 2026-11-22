"""Ports for the Super-Admin control center (ADR-037). Stdlib only, like every port."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.control import ChangeRecord, ChangeStatus, Platform, PlatformCredential, StepUpGrant

__all__ = [
    "ControlStore",
    "DeploymentHealth",
    "DeploymentPlatform",
    "DeploymentSummary",
    "IdentityReverifier",
    "LogLine",
    "PlatformVerifier",
    "RailwayApi",
    "RailwayScope",
    "ReauthClaims",
    "ServiceSummary",
    "SettingsValidator",
]


@runtime_checkable
class ControlStore(Protocol):
    """Platform-credential index, change journal, step-up grants and the reconciler's lease.

    Credentials themselves live in the credential vault; this holds only their ``secret_ref``.
    """

    async def get_credential(self, platform: Platform) -> PlatformCredential | None: ...

    async def list_credentials(self) -> Sequence[PlatformCredential]: ...

    async def put_credential(self, credential: PlatformCredential) -> None: ...

    async def delete_credential(self, platform: Platform) -> bool: ...

    async def record_change(self, change: ChangeRecord) -> None: ...

    async def update_change(
        self,
        change_id: str,
        *,
        status: ChangeStatus,
        updated_at: datetime,
        reason: str | None = None,
        deployment_id: str | None = None,
        restore_ref: str | None = None,
        clear_restore_ref: bool = False,
    ) -> ChangeRecord | None: ...

    async def get_change(self, change_id: str) -> ChangeRecord | None: ...

    async def list_changes(self, *, limit: int = 50) -> Sequence[ChangeRecord]: ...

    async def open_changes(self) -> Sequence[ChangeRecord]: ...

    async def count_changes_since(self, actor: str, since: datetime) -> int: ...

    async def put_grant(self, grant: StepUpGrant) -> None: ...

    async def find_grant(self, digest: str, *, now: datetime) -> StepUpGrant | None:
        """The grant with this digest if it exists and has not expired at ``now``."""
        ...

    async def take_grant(self, digest: str, *, now: datetime) -> StepUpGrant | None:
        """Remove and return the grant if it is live: a one-time challenge can be used once,
        even by two requests racing for it."""
        ...

    async def delete_grants(self, subject: str) -> int: ...

    async def try_lease(self, name: str, holder: str, *, now: datetime, ttl_s: float) -> bool:
        """Take or renew lease ``name`` for ``holder`` until ``now + ttl_s``. Exactly one holder
        across every gateway process gets ``True`` while the lease is live."""
        ...


@dataclass(frozen=True, slots=True)
class ReauthClaims:
    """What a fresh sign-in proved: who, and when they actually authenticated."""

    subject: str
    email: str
    auth_time: datetime
    nonce: str


@runtime_checkable
class IdentityReverifier(Protocol):
    """Checks an identity token from a deliberate re-sign-in (signature, issuer, audience, expiry)
    and returns its claims. Raises ``AuthenticationError`` for anything it cannot vouch for."""

    async def verify(self, id_token: str) -> ReauthClaims: ...


@runtime_checkable
class PlatformVerifier(Protocol):
    """One cheap read call proving a token reaches what it should. Returns a short description
    for the console ("Space owner/name"), or ``None`` when this platform cannot be verified yet;
    raises ``ValidationError`` when the token is refused."""

    async def verify(self, platform: Platform, token: str, *, target: str = "") -> str | None: ...


class DeploymentHealth(str, enum.Enum):
    PENDING = "pending"      # still building or starting
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"  # running, but its health check fails
    FAILED = "failed"        # never came up (build or health-check gate failed), or crashed
    SUPERSEDED = "superseded"  # replaced by a newer deployment before the watch ended

    def __str__(self) -> str:
        return self.value


@runtime_checkable
class DeploymentPlatform(Protocol):
    """What the reconciler needs to watch a change and undo it (Railway from stage 2)."""

    async def deployment_health(self, target: str, deployment_id: str) -> DeploymentHealth: ...

    async def rollback(self, target: str, to_deployment_id: str) -> str:
        """Redeploy ``to_deployment_id``'s build; returns the new deployment's id."""
        ...

    async def restore_variables(self, target: str, values: Mapping[str, str | None]) -> None:
        """Set each variable back (``None`` deletes it), without starting a deploy."""
        ...


@dataclass(frozen=True, slots=True)
class RailwayScope:
    """The one project and environment a Railway project token reaches."""

    project_id: str
    environment_id: str
    project_name: str = ""
    environment_name: str = ""


@dataclass(frozen=True, slots=True)
class ServiceSummary:
    service_id: str
    name: str
    latest_deployment_id: str | None = None
    #: Railway's own status word (SUCCESS, BUILDING, CRASHED, ...); ``None`` before any deploy.
    latest_status: str | None = None
    latest_at: datetime | None = None
    #: The service's health-check path; empty means Railway switches traffic without one.
    healthcheck_path: str = ""


@dataclass(frozen=True, slots=True)
class DeploymentSummary:
    deployment_id: str
    status: str
    created_at: datetime | None = None
    can_rollback: bool = False
    can_redeploy: bool = False
    #: Non-secret build provenance from the deployment's metadata (commit, message, branch).
    commit: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class LogLine:
    timestamp: str
    message: str
    severity: str = ""


@runtime_checkable
class RailwayApi(DeploymentPlatform, Protocol):
    """Railway, as the project token stored in the control center reaches it (stage 2).

    ``target`` in the inherited methods is a service id. ``variables`` returns values only for
    the engine to classify and to keep as restore values; nothing above the engine ever sees a
    secret one.
    """

    async def scope(self) -> RailwayScope: ...

    async def services(self) -> Sequence[ServiceSummary]: ...

    async def variables(self, service_id: str, *, rendered: bool = True) -> Mapping[str, str]:
        """The service's variables. ``rendered=False`` keeps ``${{...}}`` references as written,
        which is what a restore must put back."""
        ...

    async def set_variables(self, service_id: str, values: Mapping[str, str | None]) -> None:
        """Upsert (``None`` deletes) without starting a deploy."""
        ...

    async def deployments(self, service_id: str, *, limit: int = 20) -> Sequence[DeploymentSummary]: ...

    async def redeploy(self, service_id: str) -> str:
        """A new deployment of the service's current build with its current variables; its id."""
        ...

    async def restart(self, deployment_id: str) -> None: ...

    async def logs(self, deployment_id: str, *, kind: str = "deploy", limit: int = 200) -> Sequence[LogLine]: ...


@runtime_checkable
class SettingsValidator(Protocol):
    """Would a gateway boot with this environment? Returns ``None`` if yes, else the reason
    (which names settings, never values)."""

    async def validate(self, environ: Mapping[str, str]) -> str | None: ...


#: Free-form, redacted detail a change carries (variable names, secret flags, deployment ids).
ChangeDetail = Mapping[str, Any]
