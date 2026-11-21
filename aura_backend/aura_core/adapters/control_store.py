"""``ControlStore`` adapters: in memory (the default suite) and Postgres (migration 0031).

The in-memory store keeps real state and enforces the same rules, so tests exercise the control
engine exactly as production does. The Postgres store issues no DDL: Alembic owns the tables
(CB-20), and a missing table surfaces as ``DependencyUnavailableError`` naming the fix.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from ..domain.control import ChangeRecord, ChangeStatus, Platform, PlatformCredential, StepUpGrant
from ..domain.errors import DependencyUnavailableError, NotFoundError
from ..ports.control_plane import DeploymentHealth
from .postgres_store import PostgresPool

__all__ = ["InMemoryControlStore", "InMemoryDeploymentPlatform", "PostgresControlStore"]


class InMemoryControlStore:
    def __init__(self) -> None:
        self._credentials: dict[Platform, PlatformCredential] = {}
        self._changes: dict[str, ChangeRecord] = {}
        self._grants: dict[str, StepUpGrant] = {}
        self._leases: dict[str, tuple[str, datetime]] = {}

    async def get_credential(self, platform: Platform) -> PlatformCredential | None:
        return self._credentials.get(platform)

    async def list_credentials(self) -> Sequence[PlatformCredential]:
        return sorted(self._credentials.values(), key=lambda c: c.platform.value)

    async def put_credential(self, credential: PlatformCredential) -> None:
        self._credentials[credential.platform] = credential

    async def delete_credential(self, platform: Platform) -> bool:
        return self._credentials.pop(platform, None) is not None

    async def record_change(self, change: ChangeRecord) -> None:
        self._changes[change.change_id] = change

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
    ) -> ChangeRecord | None:
        current = self._changes.get(change_id)
        if current is None:
            return None
        updated = replace(
            current,
            status=status,
            updated_at=updated_at,
            reason=current.reason if reason is None else reason,
            deployment_id=deployment_id or current.deployment_id,
            restore_ref=None if clear_restore_ref else (restore_ref or current.restore_ref),
        )
        self._changes[change_id] = updated
        return updated

    async def get_change(self, change_id: str) -> ChangeRecord | None:
        return self._changes.get(change_id)

    async def list_changes(self, *, limit: int = 50) -> Sequence[ChangeRecord]:
        ordered = sorted(self._changes.values(), key=lambda c: c.created_at, reverse=True)
        return ordered[:limit]

    async def open_changes(self) -> Sequence[ChangeRecord]:
        return sorted(
            (c for c in self._changes.values() if c.status.open), key=lambda c: c.created_at
        )

    async def count_changes_since(self, actor: str, since: datetime) -> int:
        return sum(1 for c in self._changes.values() if c.actor == actor and c.created_at >= since)

    async def put_grant(self, grant: StepUpGrant) -> None:
        self._grants[grant.digest] = grant

    async def find_grant(self, digest: str, *, now: datetime) -> StepUpGrant | None:
        grant = self._grants.get(digest)
        if grant is None or grant.expires_at <= now:
            return None
        return grant

    async def take_grant(self, digest: str, *, now: datetime) -> StepUpGrant | None:
        grant = self._grants.pop(digest, None)
        if grant is None or grant.expires_at <= now:
            return None
        return grant

    async def delete_grants(self, subject: str) -> int:
        doomed = [d for d, g in self._grants.items() if g.subject == subject]
        for digest in doomed:
            del self._grants[digest]
        return len(doomed)

    async def try_lease(self, name: str, holder: str, *, now: datetime, ttl_s: float) -> bool:
        current = self._leases.get(name)
        if current is not None and current[0] != holder and current[1] > now:
            return False
        self._leases[name] = (holder, now + timedelta(seconds=ttl_s))
        return True


class InMemoryDeploymentPlatform:
    """A deployment platform with real state: services, their variables and deployments.

    The substrate for the reconciler's tests until stage 2 brings Railway. A deployment's health
    is set by the test (``set_health``); ``rollback`` creates a new deployment, as Railway's does.
    """

    def __init__(self) -> None:
        self.variables: dict[str, dict[str, str]] = {}
        self.health: dict[str, DeploymentHealth] = {}
        self.deployments: dict[str, list[str]] = {}
        self.rollbacks: list[tuple[str, str]] = []
        self.fail_rollback = False

    def deploy(self, target: str, deployment_id: str, health: DeploymentHealth = DeploymentHealth.PENDING) -> None:
        self.deployments.setdefault(target, []).append(deployment_id)
        self.health[deployment_id] = health

    def set_health(self, deployment_id: str, health: DeploymentHealth) -> None:
        self.health[deployment_id] = health

    async def deployment_health(self, target: str, deployment_id: str) -> DeploymentHealth:
        if deployment_id not in self.health:
            raise NotFoundError(f"no deployment {deployment_id!r}")
        return self.health[deployment_id]

    async def rollback(self, target: str, to_deployment_id: str) -> str:
        if self.fail_rollback:
            raise DependencyUnavailableError("the platform refused the rollback", dependency="platform")
        new_id = f"{to_deployment_id}-redeploy{len(self.rollbacks) + 1}"
        self.rollbacks.append((target, to_deployment_id))
        self.deploy(target, new_id, DeploymentHealth.HEALTHY)
        return new_id

    async def restore_variables(self, target: str, values: Any) -> None:
        current = self.variables.setdefault(target, {})
        for name, value in values.items():
            if value is None:
                current.pop(name, None)
            else:
                current[name] = value


_MISSING_TABLE = "42P01"


def _unavailable(action: str, exc: Exception) -> DependencyUnavailableError:
    if getattr(exc, "sqlstate", None) == _MISSING_TABLE:
        message = f"could not {action}: control-center tables are missing -- run `alembic upgrade head`"
    else:
        message = f"could not {action}"  # never the vendor message: it can echo parameters
    return DependencyUnavailableError(message, dependency="postgres", details={"error": type(exc).__name__})


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


_CHANGE_COLUMNS = (
    "change_id, actor, platform, target, action, status, detail, deployment_id, "
    "previous_deployment_id, restore_ref, reason, created_at, updated_at"
)


def _change(row: Any) -> ChangeRecord:
    detail = row["detail"]
    if isinstance(detail, str):
        detail = json.loads(detail)
    return ChangeRecord(
        change_id=row["change_id"],
        actor=row["actor"],
        platform=Platform(row["platform"]),
        target=row["target"],
        action=row["action"],
        status=ChangeStatus(row["status"]),
        detail=detail or {},
        deployment_id=row["deployment_id"],
        previous_deployment_id=row["previous_deployment_id"],
        restore_ref=row["restore_ref"],
        reason=row["reason"],
        created_at=_utc(row["created_at"]),
        updated_at=_utc(row["updated_at"]),
    )


def _credential(row: Any) -> PlatformCredential:
    return PlatformCredential(
        platform=Platform(row["platform"]),
        secret_ref=row["secret_ref"],
        configured_by=row["configured_by"],
        configured_at=_utc(row["configured_at"]),
        verified_at=_utc(row["verified_at"]),
        verified_detail=row["verified_detail"],
        target=row["target"],
    )


class PostgresControlStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def _fetch(self, action: str, query: str, *args: Any) -> list[Any]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                return list(await conn.fetch(query, *args))
        except Exception as exc:
            raise _unavailable(action, exc) from exc

    async def _execute(self, action: str, query: str, *args: Any) -> str:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                return await conn.execute(query, *args)
        except Exception as exc:
            raise _unavailable(action, exc) from exc

    # -- platform credentials ------------------------------------------------------------

    async def get_credential(self, platform: Platform) -> PlatformCredential | None:
        rows = await self._fetch(
            "read a platform credential",
            "SELECT * FROM aura_control_platforms WHERE platform = $1", platform.value,
        )
        return _credential(rows[0]) if rows else None

    async def list_credentials(self) -> Sequence[PlatformCredential]:
        rows = await self._fetch(
            "list platform credentials", "SELECT * FROM aura_control_platforms ORDER BY platform"
        )
        return [_credential(r) for r in rows]

    async def put_credential(self, credential: PlatformCredential) -> None:
        await self._execute(
            "store a platform credential",
            """
            INSERT INTO aura_control_platforms
                (platform, secret_ref, configured_by, configured_at, verified_at, verified_detail,
                 target)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (platform) DO UPDATE SET
                secret_ref = EXCLUDED.secret_ref, configured_by = EXCLUDED.configured_by,
                configured_at = EXCLUDED.configured_at, verified_at = EXCLUDED.verified_at,
                verified_detail = EXCLUDED.verified_detail, target = EXCLUDED.target
            """,
            credential.platform.value, credential.secret_ref, credential.configured_by,
            credential.configured_at, credential.verified_at, credential.verified_detail,
            credential.target,
        )

    async def delete_credential(self, platform: Platform) -> bool:
        status = await self._execute(
            "delete a platform credential",
            "DELETE FROM aura_control_platforms WHERE platform = $1", platform.value,
        )
        return status.endswith(" 1")

    # -- change journal --------------------------------------------------------------------

    async def record_change(self, change: ChangeRecord) -> None:
        await self._execute(
            "record a change",
            f"""
            INSERT INTO aura_control_changes ({_CHANGE_COLUMNS})
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13)
            """,
            change.change_id, change.actor, change.platform.value, change.target, change.action,
            change.status.value, json.dumps(dict(change.detail)), change.deployment_id,
            change.previous_deployment_id, change.restore_ref, change.reason,
            change.created_at, change.updated_at,
        )

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
    ) -> ChangeRecord | None:
        rows = await self._fetch(
            "update a change",
            f"""
            UPDATE aura_control_changes SET
                status = $2, updated_at = $3,
                reason = COALESCE($4, reason),
                deployment_id = COALESCE($5, deployment_id),
                restore_ref = CASE WHEN $7 THEN NULL ELSE COALESCE($6, restore_ref) END
            WHERE change_id = $1
            RETURNING {_CHANGE_COLUMNS}
            """,
            change_id, status.value, updated_at, reason, deployment_id, restore_ref, clear_restore_ref,
        )
        return _change(rows[0]) if rows else None

    async def get_change(self, change_id: str) -> ChangeRecord | None:
        rows = await self._fetch(
            "read a change",
            f"SELECT {_CHANGE_COLUMNS} FROM aura_control_changes WHERE change_id = $1", change_id,
        )
        return _change(rows[0]) if rows else None

    async def list_changes(self, *, limit: int = 50) -> Sequence[ChangeRecord]:
        rows = await self._fetch(
            "list changes",
            f"SELECT {_CHANGE_COLUMNS} FROM aura_control_changes ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return [_change(r) for r in rows]

    async def open_changes(self) -> Sequence[ChangeRecord]:
        rows = await self._fetch(
            "list open changes",
            f"""
            SELECT {_CHANGE_COLUMNS} FROM aura_control_changes
            WHERE status IN ('watching', 'rolling_back') ORDER BY created_at
            """,
        )
        return [_change(r) for r in rows]

    async def count_changes_since(self, actor: str, since: datetime) -> int:
        rows = await self._fetch(
            "count changes",
            "SELECT count(*) AS n FROM aura_control_changes WHERE actor = $1 AND created_at >= $2",
            actor, since,
        )
        return int(rows[0]["n"]) if rows else 0

    # -- step-up grants ----------------------------------------------------------------------

    async def put_grant(self, grant: StepUpGrant) -> None:
        await self._execute(
            "store a step-up grant",
            """
            INSERT INTO aura_control_step_up (digest, subject, email, created_at, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            grant.digest, grant.subject, grant.email, grant.created_at, grant.expires_at,
        )

    async def find_grant(self, digest: str, *, now: datetime) -> StepUpGrant | None:
        rows = await self._fetch(
            "read a step-up grant",
            "SELECT * FROM aura_control_step_up WHERE digest = $1 AND expires_at > $2", digest, now,
        )
        if not rows:
            return None
        row = rows[0]
        return StepUpGrant(
            digest=row["digest"], subject=row["subject"], email=row["email"],
            created_at=_utc(row["created_at"]), expires_at=_utc(row["expires_at"]),
        )

    async def take_grant(self, digest: str, *, now: datetime) -> StepUpGrant | None:
        rows = await self._fetch(
            "take a step-up challenge",
            "DELETE FROM aura_control_step_up WHERE digest = $1 RETURNING *", digest,
        )
        if not rows or _utc(rows[0]["expires_at"]) <= now:
            return None
        row = rows[0]
        return StepUpGrant(
            digest=row["digest"], subject=row["subject"], email=row["email"],
            created_at=_utc(row["created_at"]), expires_at=_utc(row["expires_at"]),
        )

    async def delete_grants(self, subject: str) -> int:
        status = await self._execute(
            "delete step-up grants", "DELETE FROM aura_control_step_up WHERE subject = $1", subject
        )
        try:
            return int(status.rsplit(" ", 1)[-1])
        except ValueError:
            return 0

    # -- leases ------------------------------------------------------------------------------

    async def try_lease(self, name: str, holder: str, *, now: datetime, ttl_s: float) -> bool:
        """One statement, so two processes racing for an expired lease cannot both win: the
        conflicting row is updated only when it is ours or has expired, and ``RETURNING`` reports
        whether that happened."""
        rows = await self._fetch(
            "take a lease",
            """
            INSERT INTO aura_control_leases (name, holder, expires_at) VALUES ($1, $2, $4)
            ON CONFLICT (name) DO UPDATE SET holder = EXCLUDED.holder, expires_at = EXCLUDED.expires_at
                WHERE aura_control_leases.holder = EXCLUDED.holder
                   OR aura_control_leases.expires_at <= $3
            RETURNING holder
            """,
            name, holder, now, now + timedelta(seconds=ttl_s),
        )
        return bool(rows)
