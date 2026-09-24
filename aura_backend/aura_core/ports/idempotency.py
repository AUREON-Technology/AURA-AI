"""The ``IdempotencyStore`` port (roadmap P1.4).

Kept in its own module rather than appended to ``storage.py``: it is not a content store, and
the gateway is its only consumer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from ..domain.identifiers import UserId
from ..domain.idempotency import ClaimResult, StoredResponse

__all__ = ["IdempotencyStore"]


@runtime_checkable
class IdempotencyStore(Protocol):
    """Durable claim/replay bookkeeping for ``Idempotency-Key`` requests.

    Every method is scoped by ``(user_id, operation, key)``; there is deliberately no lookup by
    key alone, so one tenant's key can never resolve to another tenant's record.

    ``claim`` **must be atomic**: N concurrent calls with the same scope and request hash must
    produce exactly one ``CLAIMED`` result, and every other caller must see ``IN_PROGRESS`` (or
    ``REPLAY`` once the winner has completed). Anything weaker lets a double-submitted request run
    twice, which is the one thing this port exists to prevent.

    Claim rules, evaluated in order against the existing record for the scope:

    1. no record, or the record has expired -> a fresh ``in_progress`` record; ``CLAIMED``;
    2. ``request_hash`` differs from the record's -> ``MISMATCH`` (whatever the record's status);
    3. ``completed`` -> ``REPLAY`` (the record carries the stored response);
    4. ``failed``, or ``in_progress`` but older than ``lease`` -> re-claimed; ``CLAIMED``;
    5. otherwise ``in_progress`` -> ``IN_PROGRESS``.

    ``now`` is passed in rather than read from a clock inside the adapter, so tests can drive TTL
    and lease expiry with a frozen clock and both adapters agree on what time it is.
    """

    async def claim(
        self,
        *,
        user_id: UserId,
        operation: str,
        key: str,
        request_hash: str,
        now: datetime,
        ttl: timedelta,
        lease: timedelta,
    ) -> ClaimResult:
        ...

    async def complete(
        self,
        *,
        user_id: UserId,
        operation: str,
        key: str,
        claim_token: str,
        response: StoredResponse,
        now: datetime,
    ) -> bool:
        """Store ``response`` and mark the claim completed. Returns False (and changes nothing)
        when ``claim_token`` no longer owns the record -- its lease lapsed and a retry took it."""
        ...

    async def fail(
        self,
        *,
        user_id: UserId,
        operation: str,
        key: str,
        claim_token: str,
        now: datetime,
    ) -> bool:
        """Mark the claim failed so a retry with the same request may run again."""
        ...

    async def purge_expired(self, *, now: datetime) -> int:
        """Delete every record whose ``expires_at`` has passed. Returns the number removed."""
        ...
