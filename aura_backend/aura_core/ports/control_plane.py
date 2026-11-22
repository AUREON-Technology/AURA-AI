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
    "SpaceApi",
    "SpaceSecretInfo",
    "SpaceStatus",
    "CloudflareToken",
    "CloudflareTokens",
    "GrafanaStack",
    "ObservabilityStack",
    "VectorAdmin",
    "VectorCollection",
    "VectorSnapshot",
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


@dataclass(frozen=True, slots=True)
class SpaceStatus:
    """A Hugging Face Space's runtime, as the Hub reports it (stage 3)."""

    #: The Hub's stage word: RUNNING, BUILDING, APP_STARTING, RUNTIME_ERROR, PAUSED, ...
    stage: str
    hardware: str | None = None
    #: What was asked for; differs from ``hardware`` while a hardware change is pending.
    requested_hardware: str | None = None
    #: Seconds of inactivity before the Space sleeps; ``None`` or ``-1`` means never.
    sleep_time: int | None = None


@dataclass(frozen=True, slots=True)
class SpaceSecretInfo:
    """A Space secret's name and when it last changed. The Hub never returns the value."""

    name: str
    updated_at: datetime | None = None


@runtime_checkable
class SpaceApi(DeploymentPlatform, Protocol):
    """The inference Space, reached with the stored HF token (stage 3). ``target`` and
    ``space_id`` are the Space's ``owner/name``.

    The Hub restarts a Space whenever a variable or secret changes, so there is no separate
    deploy step: ``restore_variables`` puts values back (and so restarts it), and ``rollback``
    is a plain restart.
    """

    async def status(self, space_id: str) -> SpaceStatus: ...

    async def variables(self, space_id: str) -> Mapping[str, str]: ...

    async def set_variables(self, space_id: str, values: Mapping[str, str | None]) -> None: ...

    async def secrets(self, space_id: str) -> Sequence[SpaceSecretInfo]: ...

    async def set_secrets(self, space_id: str, values: Mapping[str, str | None]) -> None: ...

    async def pause(self, space_id: str) -> None: ...

    async def restart(self, space_id: str, *, factory: bool = False) -> None: ...

    async def request_hardware(self, space_id: str, hardware: str) -> None: ...

    async def set_sleep_time(self, space_id: str, seconds: int) -> None: ...

    async def logs(self, space_id: str, *, kind: str = "run", limit: int = 200) -> Sequence[str]: ...


@dataclass(frozen=True, slots=True)
class CloudflareToken:
    token_id: str
    name: str
    status: str = ""
    issued_on: datetime | None = None


@runtime_checkable
class CloudflareTokens(Protocol):
    """Cloudflare API tokens, reached with the stored management token (stage 4)."""

    async def identify(self, token: str) -> str | None:
        """The id of the token ``token`` is, or ``None`` when Cloudflare does not know it."""
        ...

    async def list(self) -> Sequence[CloudflareToken]: ...

    async def create(self, *, name: str, account_id: str) -> tuple[str, str]:
        """A new token for Workers AI on that account: ``(id, value)``. The value is shown once."""
        ...

    async def revoke(self, token_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class VectorCollection:
    name: str
    status: str = ""
    points_count: int | None = None
    indexed_vectors_count: int | None = None
    segments_count: int | None = None
    vector_size: int | None = None
    distance: str = ""


@dataclass(frozen=True, slots=True)
class VectorSnapshot:
    name: str
    created_at: datetime | None = None
    size: int | None = None


@runtime_checkable
class VectorAdmin(Protocol):
    """The Qdrant cluster the gateway uses, reached with the stored cluster key (stage 5)."""

    async def health(self) -> bool: ...

    async def collections(self) -> Sequence[VectorCollection]: ...

    async def snapshots(self, collection: str) -> Sequence[VectorSnapshot]: ...

    async def create_snapshot(self, collection: str) -> VectorSnapshot: ...

    async def restore_snapshot(self, collection: str, snapshot: str) -> None:
        """Replace the collection's contents with the snapshot's. Irreversible."""
        ...


@dataclass(frozen=True, slots=True)
class GrafanaStack:
    """A Grafana Cloud stack, as its access policy token reaches it."""

    slug: str
    grafana_url: str = ""
    prometheus_url: str = ""
    prometheus_id: str = ""


@runtime_checkable
class ObservabilityStack(Protocol):
    """Grafana Cloud (stage 5): the stack, its hosted Prometheus and its ruler."""

    async def stack(self) -> GrafanaStack: ...

    async def load_rule_group(self, namespace: str, group: Mapping[str, Any]) -> None:
        """Create or replace one Prometheus rule group in ``namespace``."""
        ...

    async def rule_groups(self, namespace: str) -> Sequence[str]:
        """The names of the rule groups loaded in ``namespace``."""
        ...

    async def query(self, promql: str) -> Sequence[Mapping[str, Any]]:
        """An instant query: ``[{"metric": {...labels}, "value": float}]``."""
        ...


#: Free-form, redacted detail a change carries (variable names, secret flags, deployment ids).
ChangeDetail = Mapping[str, Any]
