"""Issue, verify and revoke service keys (AR-4).

The policy half of :mod:`aura_core.domain.service_keys`: the gateway's authenticator calls
:meth:`ServiceKeyService.authenticate` for any bearer token carrying the service prefix, and
Super Admin routes call ``issue`` / ``revoke`` / ``list``. Every outcome a person would want to
trace -- issue, revoke, a refused token -- is an audit event naming the key, never the token.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..domain.errors import AuthenticationError
from ..domain.service_keys import (
    DEFAULT_SERVICE_ROUTES,
    ServiceKey,
    digests_match,
    new_token,
    parse_token,
    token_digest,
)
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent
from ..ports.service_keys import ServiceKeyStore

__all__ = ["ServiceKeyService"]

logger = get_logger("security.service_keys")


class ServiceKeyService:
    def __init__(self, *, store: ServiceKeyStore, clock: Any, audit: Any = None) -> None:
        self._store = store
        self._clock = clock
        self._audit = audit

    async def issue(self, *, name: str, by: str, routes: Iterable[str] | None = None,
                    rate_per_minute: int = 10) -> tuple[ServiceKey, str]:
        """A new key and its token. The token is returned here once and is never stored."""
        key_id, token = new_token()
        key = ServiceKey(
            key_id=key_id, name=name, digest=token_digest(token), created_at=self._clock.now(),
            created_by=by, routes=frozenset(routes) if routes is not None else DEFAULT_SERVICE_ROUTES,
            rate_per_minute=rate_per_minute,
        )
        await self._store.put(key)
        await self._record("service_key.issue", by, key, "ok")
        return key, token

    async def authenticate(self, token: str) -> ServiceKey:
        """The live key a token belongs to, or :class:`AuthenticationError`.

        One store read per call and no cache: revoking a key refuses its very next request.
        Unknown, malformed and revoked tokens get the same message, so a caller cannot probe
        which key ids exist.
        """
        key_id = parse_token(token)
        key = await self._store.get(key_id) if key_id else None
        if key is None or not digests_match(key.digest, token):
            raise AuthenticationError("invalid service credential")
        if key.revoked:
            await self._record("service_key.refused", key.principal_id, key, "revoked")
            raise AuthenticationError("invalid service credential")
        return key

    async def revoke(self, key_id: str, *, by: str) -> ServiceKey | None:
        key = await self._store.revoke(key_id, at=self._clock.now(), by=by)
        if key is not None:
            await self._record("service_key.revoke", by, key, "ok")
        return key

    async def list(self) -> list[ServiceKey]:
        return await self._store.list()

    async def _record(self, action: str, actor: str, key: ServiceKey, outcome: str) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(AuditEvent(
                action=action, actor=actor, outcome=outcome, resource=f"service_key:{key.key_id}",
                detail={"name": key.name, "routes": sorted(key.routes), "rate_per_minute": key.rate_per_minute},
            ))
        except Exception:  # an audit outage must not lock every service caller out
            logger.warning("service key audit write failed", extra={"action": action}, exc_info=True)
