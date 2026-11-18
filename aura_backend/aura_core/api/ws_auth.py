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

import inspect
import time
from typing import Any, Awaitable, Callable

#: How often an open socket's credential is re-checked.
DEFAULT_RECHECK_S = 60.0


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

    async def still_valid(self, accept: Callable[[Any], bool]) -> bool:
        """Re-check if due. False when the token no longer authenticates, now names a
        different user, or ``accept`` refuses the principal it resolves to."""
        if self.seconds_until_due() > 0:
            return True
        try:
            principal = await self._check(self.token)
        except Exception:
            return False
        if principal.user_id != self.principal.user_id:
            return False
        accepted = accept(principal)
        if inspect.isawaitable(accepted):  # e.g. a plan lookup (SaaS P1-E)
            accepted = await accepted
        if not accepted:
            return False
        self.principal = principal
        self._checked_at = self._clock()
        return True
