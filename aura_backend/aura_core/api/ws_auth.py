"""Re-checking the credential a long-lived WebSocket was opened with.

A WebSocket authenticates once, at its auth frame, and can then stay open for hours. Without a
re-check, nothing that happens to that credential afterwards reaches the socket: the access token
expiring, the paired device being revoked ("revoke this device" did not stop a daemon that was
already connected), or the account losing the role the socket needs. Each socket keeps the token
it was opened with and runs it through the same authenticator HTTP requests use, every
``recheck_s`` and whenever the client sends a ``reauth`` -- so a socket is held to exactly the
rules an HTTP request is, within one interval.
"""

from __future__ import annotations

import enum
import inspect
import time
from typing import Any, Awaitable, Callable

from ..domain.errors import AuthenticationError, AuthorizationError

#: How often an open socket's credential is re-checked.
DEFAULT_RECHECK_S = 60.0


class Recheck(enum.Enum):
    """What a credential recheck found (INT-104).

    ``REVOKED`` is an answer: the token no longer authenticates, names someone else, or the
    account may no longer use the socket -- close it as unauthorized. ``UNAVAILABLE`` is the
    absence of one: the authenticator or the acceptance check failed on the server's side (a
    database or Redis blip). A socket must not tell a client its credential is dead over that;
    close it as a server error so the client reconnects with backoff."""

    VALID = "valid"
    REVOKED = "revoked"
    UNAVAILABLE = "unavailable"


def is_rejection(exc: BaseException) -> bool:
    """Whether an authenticator exception says "this credential is refused", as opposed to "the
    server could not check it": AURA's own authentication/authorization errors, or the 401/403
    ``HTTPException`` the JWT layer raises -- the split the host-agent socket's first
    authentication makes."""
    if isinstance(exc, (AuthenticationError, AuthorizationError)):
        return True
    return getattr(exc, "status_code", None) in (401, 403)


class LiveCredential:
    __slots__ = ("token", "principal", "_check", "_recheck_s", "_clock", "_checked_at")

    def __init__(
        self,
        *,
        token: str,
        principal: Any,
        check: Callable[[str], Awaitable[Any]],
        recheck_s: float = DEFAULT_RECHECK_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``check(token)`` returns the principal the token authenticates as, or raises."""
        self.token = token
        self.principal = principal
        self._check = check
        self._recheck_s = recheck_s
        self._clock = clock
        self._checked_at = clock()

    def seconds_until_due(self) -> float:
        return max(0.0, self._recheck_s - (self._clock() - self._checked_at))

    def renew(self, token: str, principal: Any) -> None:
        self.token = token
        self.principal = principal
        self._checked_at = self._clock()

    async def recheck(self, accept: Callable[[Any], Any]) -> Recheck:
        """Re-check if due. ``REVOKED`` when the token is refused, now names a different user, or
        ``accept`` refuses the principal it resolves to; ``UNAVAILABLE`` when the check itself
        failed (see :class:`Recheck`). An unavailable check confirms nothing, so the credential
        stays due and the last good principal is kept."""
        if self.seconds_until_due() > 0:
            return Recheck.VALID
        try:
            principal = await self._check(self.token)
        except Exception as exc:  # noqa: BLE001 -- classified, not swallowed
            return Recheck.REVOKED if is_rejection(exc) else Recheck.UNAVAILABLE
        if principal.user_id != self.principal.user_id:
            return Recheck.REVOKED
        try:
            accepted = accept(principal)
            if inspect.isawaitable(accepted):  # e.g. a plan lookup (SaaS P1-E)
                accepted = await accepted
        except Exception:  # noqa: BLE001
            return Recheck.UNAVAILABLE
        if not accepted:
            return Recheck.REVOKED
        self.principal = principal
        self._checked_at = self._clock()
        return Recheck.VALID

    async def still_valid(self, accept: Callable[[Any], Any]) -> bool:
        """``recheck`` as a bool: True only for ``VALID``. Kept for sockets that close the same
        way whatever the reason; one that can tell its client to retry uses ``recheck``."""
        return await self.recheck(accept) is Recheck.VALID
