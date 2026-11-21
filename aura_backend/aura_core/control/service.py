"""The control center's engine: step-up, platform credentials, the change journal, rollback.

Engine layer: domain and ports only. The vault, store, verifiers and the deployment platform are
handed in by ``container.py``; nothing here imports an adapter.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from ..domain.connectors import Credential
from ..domain.control import (
    PLATFORM_TOKEN_HINTS,
    ChangeRecord,
    ChangeStatus,
    Platform,
    PlatformCredential,
    StepUpGrant,
    grant_digest,
)
from ..domain.errors import (
    AuraError,
    AuthorizationError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from ..observability.logging import get_logger
from ..ports.control_plane import (
    ControlStore,
    DeploymentHealth,
    DeploymentPlatform,
    IdentityReverifier,
    PlatformVerifier,
)

__all__ = ["ControlService", "Reconciler", "StepUpService", "STEP_UP_HEADER"]

logger = get_logger("control")

#: The header a control-center write carries its step-up grant in.
STEP_UP_HEADER = "x-aura-step-up"

_NONCE_PREFIX = "nonce:"
_MAX_TOKEN_CHARS = 8192
_MAX_TARGET_CHARS = 256


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _refused(message: str, reason: str = "step_up_refused") -> AuthorizationError:
    """403, never 401: the console treats a 401 as an expired session (refresh, then the login
    page), while a step-up problem only needs a fresh re-sign-in."""
    return AuthorizationError(message, details={"reason": reason})


@dataclass(frozen=True, slots=True)
class Challenge:
    nonce: str
    expires_at: datetime


class StepUpService:
    """A fresh sign-in buys a five-minute grant; every control-center write needs one.

    1. ``challenge`` issues a one-time nonce bound to the caller (stored as a digest).
    2. The console sends the person to Google with that nonce, ``prompt=login`` and ``max_age``.
    3. ``redeem`` checks the returned ID token: our audience, the same verified email as the
       session, the nonce we issued (taken, so it cannot be replayed), and an ``auth_time`` within
       ``max_age_s``. It returns an opaque grant, stored only as a digest.
    4. ``require`` checks a grant on each write: it must exist, be live and belong to the caller.

    A refresh token can never produce a grant, so a stolen session cookie cannot change anything.
    """

    def __init__(
        self,
        *,
        store: ControlStore,
        reverifier: IdentityReverifier,
        max_age_s: int,
        ttl_s: int,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._reverifier = reverifier
        self._max_age = timedelta(seconds=max_age_s)
        self._ttl = timedelta(seconds=ttl_s)
        self._now = now

    async def challenge(self, *, subject: str, email: str) -> Challenge:
        nonce = secrets.token_urlsafe(24)
        now = self._now()
        expires = now + self._ttl
        await self._store.put_grant(StepUpGrant(
            digest=grant_digest(_NONCE_PREFIX + nonce), subject=subject, email=email,
            created_at=now, expires_at=expires,
        ))
        return Challenge(nonce=nonce, expires_at=expires)

    async def redeem(self, *, subject: str, email: str, id_token: str, nonce: str) -> tuple[str, datetime]:
        if not id_token or len(id_token) > _MAX_TOKEN_CHARS or not nonce:
            raise ValidationError("a re-sign-in token and its challenge are required")
        now = self._now()
        issued = await self._store.take_grant(grant_digest(_NONCE_PREFIX + nonce), now=now)
        if issued is None or issued.subject != subject:
            raise _refused("the re-sign-in challenge is unknown, used or expired; start again")
        try:
            claims = await self._reverifier.verify(id_token)
        except AuraError as exc:
            raise _refused(exc.message) from exc
        if claims.nonce != nonce:
            raise _refused("the re-sign-in token was not issued for this challenge")
        if not email or claims.email.strip().lower() != email.strip().lower():
            raise _refused("re-sign in with the same account you are signed in with")
        if claims.auth_time < now - self._max_age or claims.auth_time > now + timedelta(minutes=2):
            raise _refused("that sign-in was not fresh; sign in again (Google must ask for your password)")
        grant = secrets.token_urlsafe(32)
        expires = now + self._ttl
        await self._store.put_grant(StepUpGrant(
            digest=grant_digest(grant), subject=subject, email=email, created_at=now, expires_at=expires,
        ))
        return grant, expires

    async def require(self, *, subject: str, grant: str | None) -> None:
        if not grant:
            raise _refused("this change needs a fresh sign-in", "step_up_required")
        found = await self._store.find_grant(grant_digest(grant), now=self._now())
        if found is None or found.subject != subject:
            raise _refused("your fresh sign-in has expired; sign in again to continue", "step_up_required")

    async def revoke(self, subject: str) -> int:
        return await self._store.delete_grants(subject)


def _confirm(expected: str, given: str | None) -> None:
    """The typed confirmation, checked on the server so a script cannot skip the dialog."""
    if (given or "").strip() != expected:
        raise ValidationError(
            f"type {expected!r} to confirm", details={"reason": "confirmation_required", "expected": expected}
        )


class ControlService:
    def __init__(
        self,
        *,
        store: ControlStore,
        vault: Any,
        verifier: PlatformVerifier,
        writes_per_hour: int,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._vault = vault
        self._verifier = verifier
        self._writes_per_hour = writes_per_hour
        self._now = now

    @property
    def vault_ready(self) -> bool:
        return bool(getattr(self._vault, "enabled", False))

    async def overview(self) -> dict[str, Any]:
        stored = {c.platform: c for c in await self._store.list_credentials()}
        platforms = []
        for platform in Platform:
            credential = stored.get(platform)
            platforms.append({
                "platform": platform.value,
                "connected": credential is not None,
                "verified": bool(credential and credential.verified_at),
                "verified_at": credential.verified_at.isoformat() if credential and credential.verified_at else None,
                "verified_detail": credential.verified_detail if credential else "",
                "target": credential.target if credential else "",
                "configured_by": credential.configured_by if credential else None,
                "configured_at": credential.configured_at.isoformat() if credential else None,
                "token_hint": PLATFORM_TOKEN_HINTS[platform],
            })
        open_changes = await self._store.open_changes()
        return {
            "platforms": platforms,
            "open_changes": len(open_changes),
            "vault_ready": self.vault_ready,
        }

    async def _check_write_cap(self, actor: str) -> None:
        since = self._now() - timedelta(hours=1)
        if await self._store.count_changes_since(actor, since) >= self._writes_per_hour:
            raise RateLimitedError(
                f"at most {self._writes_per_hour} control-center changes per hour",
                retry_after=600,
            )

    async def _journal(
        self, *, actor: str, platform: Platform, target: str, action: str,
        detail: Mapping[str, Any], status: ChangeStatus = ChangeStatus.RECORDED, reason: str = "",
    ) -> ChangeRecord:
        now = self._now()
        record = ChangeRecord(
            change_id=f"chg_{uuid.uuid4().hex}", actor=actor, platform=platform, target=target,
            action=action, status=status, created_at=now, updated_at=now, detail=dict(detail),
            reason=reason,
        )
        await self._store.record_change(record)
        return record

    async def connect(
        self, *, platform: Platform, token: str, target: str, actor: str, confirm: str | None,
    ) -> dict[str, Any]:
        _confirm(platform.value, confirm)
        token = (token or "").strip()
        target = (target or "").strip()
        if not token or len(token) > _MAX_TOKEN_CHARS:
            raise ValidationError("paste the platform token")
        if len(target) > _MAX_TARGET_CHARS:
            raise ValidationError("the target is too long")
        if not self.vault_ready:
            raise ValidationError(
                "AURA_SECRETS_ENCRYPTION_KEY is not set, so platform tokens cannot be stored",
                details={"reason": "vault_not_configured"},
            )
        await self._check_write_cap(actor)
        detail_text = await self._verifier.verify(platform, token, target=target)
        secret_ref = await self._vault.put(Credential(token))
        previous = await self._store.get_credential(platform)
        now = self._now()
        credential = PlatformCredential(
            platform=platform, secret_ref=secret_ref, configured_by=actor, configured_at=now,
            verified_at=now if detail_text is not None else None,
            verified_detail=detail_text or "", target=target,
        )
        await self._store.put_credential(credential)
        if previous is not None:
            try:
                await self._vault.delete(previous.secret_ref)
            except Exception:
                logger.warning("could not delete a replaced platform token", extra={"platform": platform.value})
        await self._journal(
            actor=actor, platform=platform, target=target or platform.value,
            action="replace_token" if previous else "connect",
            detail={"verified": detail_text is not None},
        )
        return {"platform": platform.value, "verified": detail_text is not None,
                "verified_detail": detail_text or ""}

    async def disconnect(self, *, platform: Platform, actor: str, confirm: str | None) -> None:
        _confirm(platform.value, confirm)
        await self._check_write_cap(actor)
        credential = await self._store.get_credential(platform)
        if credential is None:
            raise NotFoundError(f"{platform.value} is not connected")
        await self._store.delete_credential(platform)
        try:
            await self._vault.delete(credential.secret_ref)
        except Exception:
            logger.warning("could not delete a disconnected platform token", extra={"platform": platform.value})
        await self._journal(
            actor=actor, platform=platform, target=credential.target or platform.value,
            action="disconnect", detail={},
        )

    async def token(self, platform: Platform) -> Credential:
        """The stored token, for the platform adapters of later stages. Never leaves the server."""
        credential = await self._store.get_credential(platform)
        if credential is None:
            raise NotFoundError(f"{platform.value} is not connected; connect it in Settings")
        return await self._vault.get(credential.secret_ref)

    async def changes(self, *, limit: int = 50) -> Sequence[ChangeRecord]:
        return await self._store.list_changes(limit=max(1, min(limit, 200)))

    async def watch(
        self, *, actor: str, platform: Platform, target: str, action: str, detail: Mapping[str, Any],
        deployment_id: str, previous_deployment_id: str | None,
        restore_values: Mapping[str, str | None] | None,
    ) -> ChangeRecord:
        """Journal a change whose deployment the reconciler must watch (stage 2's callers).

        ``restore_values`` (each variable's value before the change, ``None`` if it was unset)
        goes into the vault, never the journal, and is deleted when the change closes.
        """
        restore_ref = None
        if restore_values:
            restore_ref = await self._vault.put(Credential(json.dumps(dict(restore_values))))
        now = self._now()
        record = ChangeRecord(
            change_id=f"chg_{uuid.uuid4().hex}", actor=actor, platform=platform, target=target,
            action=action, status=ChangeStatus.WATCHING, created_at=now, updated_at=now,
            detail=dict(detail), deployment_id=deployment_id,
            previous_deployment_id=previous_deployment_id, restore_ref=restore_ref,
        )
        await self._store.record_change(record)
        return record


class Reconciler:
    """Watches every open change and rolls back the ones whose deployment stays unhealthy.

    Runs in every gateway process; a lease in the store lets exactly one act at a time. Because
    the journal is in Postgres, the loop works in whichever gateway is alive: the old deployment
    when the new one never came up, or the new one when it came up broken.
    """

    LEASE = "control-reconciler"

    def __init__(
        self,
        *,
        store: ControlStore,
        vault: Any,
        platforms: Mapping[Platform, DeploymentPlatform],
        watch_s: int,
        holder: str | None = None,
        now: Callable[[], datetime] = _utcnow,
        interval_s: float = 15.0,
    ) -> None:
        self._store = store
        self._vault = vault
        self._platforms = dict(platforms)
        self._watch = timedelta(seconds=watch_s)
        self._holder = holder or f"gw-{uuid.uuid4().hex[:12]}"
        self._now = now
        self._interval_s = interval_s

    async def tick(self) -> list[ChangeRecord]:
        """One pass. Returns the changes it closed (succeeded, rolled back or failed)."""
        now = self._now()
        if not await self._store.try_lease(self.LEASE, self._holder, now=now, ttl_s=self._interval_s * 4):
            return []
        closed: list[ChangeRecord] = []
        for change in await self._store.open_changes():
            platform = self._platforms.get(change.platform)
            if platform is None or not change.deployment_id:
                continue
            try:
                health = await platform.deployment_health(change.target, change.deployment_id)
            except Exception as exc:
                logger.warning("could not read a deployment's health", extra={"error": type(exc).__name__})
                continue
            watched_long_enough = now - change.created_at >= self._watch
            if health is DeploymentHealth.FAILED:
                closed.append(await self._roll_back(change, platform, "the new deployment failed to start"))
            elif watched_long_enough and health is DeploymentHealth.HEALTHY:
                closed.append(await self._close(change, ChangeStatus.SUCCEEDED, ""))
            elif watched_long_enough:
                closed.append(await self._roll_back(
                    change, platform,
                    f"the deployment was still {health.value} after {int(self._watch.total_seconds() // 60)} minutes",
                ))
        return closed

    async def _close(self, change: ChangeRecord, status: ChangeStatus, reason: str) -> ChangeRecord:
        if change.restore_ref:
            try:
                await self._vault.delete(change.restore_ref)
            except Exception:
                logger.warning("could not delete a change's restore values")
        updated = await self._store.update_change(
            change.change_id, status=status, updated_at=self._now(), reason=reason,
            clear_restore_ref=True,
        )
        return updated or change

    async def _roll_back(self, change: ChangeRecord, platform: DeploymentPlatform, reason: str) -> ChangeRecord:
        await self._store.update_change(
            change.change_id, status=ChangeStatus.ROLLING_BACK, updated_at=self._now(), reason=reason,
        )
        try:
            if change.restore_ref:
                values = json.loads((await self._vault.get(change.restore_ref)).reveal())
                await platform.restore_variables(change.target, values)
            if change.previous_deployment_id:
                await platform.rollback(change.target, change.previous_deployment_id)
        except Exception as exc:
            logger.error("automatic rollback failed", extra={"error": type(exc).__name__})
            return await self._close(
                change, ChangeStatus.FAILED,
                f"{reason}; the automatic rollback also failed ({type(exc).__name__}) -- roll back by hand",
            )
        return await self._close(change, ChangeStatus.ROLLED_BACK, f"{reason}; rolled back automatically")

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("control reconciler pass failed")
            await asyncio.sleep(self._interval_s)
