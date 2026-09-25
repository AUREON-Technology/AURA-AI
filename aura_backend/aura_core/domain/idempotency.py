"""Idempotency-Key records (roadmap P1.4).

The value objects behind the optional ``Idempotency-Key`` request header, following the
semantics of the IETF draft ``draft-ietf-httpapi-idempotency-key-header``:

* the **first** request carrying a key claims it and runs;
* a **retry** with the same key and the same request fingerprint gets the first request's stored
  response back instead of running again;
* a retry that arrives **while the first is still running** is refused (409) rather than queued
  or executed a second time;
* the same key reused with a **different** request is a client bug and is refused (422).

A key is scoped by ``(user_id, operation, key)``, never by the key alone: two users who happen to
pick the same key must not collide with -- or read -- each other's responses, and one user's key
on ``POST /memory`` has nothing to do with the same string on ``DELETE /memory/{id}``.

Standard library only (ADR-001): the HTTP translation lives in ``aura_core/api/idempotency.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Mapping

from .errors import ConflictError, ValidationError
from .identifiers import UserId

__all__ = [
    "IdempotencyKeyInProgressError",
    "IdempotencyKeyReusedError",
    "IdempotencyStatus",
    "StoredResponse",
    "IdempotencyRecord",
    "ClaimOutcome",
    "ClaimResult",
    "MAX_IDEMPOTENCY_KEY_LENGTH",
]

#: The draft does not bound the key; this does, so a key cannot be used to bloat the store or a
#: log line. 255 comfortably fits a UUID (what every sane client sends) with room to spare.
MAX_IDEMPOTENCY_KEY_LENGTH = 255


class IdempotencyStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    #: The first attempt raised or returned a 5xx. Nothing was stored to replay, so a retry with
    #: the same key and request re-claims and runs again -- a failure must stay retryable.
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class StoredResponse:
    """What a completed request answered, captured so a retry can be answered identically."""

    status_code: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    user_id: UserId
    operation: str
    key: str
    request_hash: str
    status: IdempotencyStatus
    created_at: datetime
    expires_at: datetime
    #: When the record last changed state. For an ``in_progress`` record this is the claim time,
    #: which is what the in-progress lease is measured from.
    updated_at: datetime
    #: Minted per claim. ``complete``/``fail`` must present it, so a request that outlived its
    #: lease -- and whose key was re-claimed by a retry -- cannot overwrite the retry's record.
    claim_token: str
    response: StoredResponse | None = None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at <= now

    def is_stale_claim(self, now: datetime, lease: timedelta) -> bool:
        """An ``in_progress`` record whose holder has presumably died (process crash, OOM kill)
        without ever reaching ``complete``/``fail``. Without a lease that key would answer 409
        until it expired -- a whole TTL of a client being unable to retry its own request."""
        return self.status is IdempotencyStatus.IN_PROGRESS and self.updated_at + lease <= now


class ClaimOutcome(str, Enum):
    #: This request owns the key now; run the operation, then complete or fail the claim.
    CLAIMED = "claimed"
    #: A previous request with the same key and fingerprint completed; replay its response.
    REPLAY = "replay"
    #: A request with the same key is still running (409).
    IN_PROGRESS = "in_progress"
    #: The key was used before with a different request fingerprint (422).
    MISMATCH = "mismatch"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ClaimResult:
    outcome: ClaimOutcome
    record: IdempotencyRecord

    @property
    def claim_token(self) -> str:
        return self.record.claim_token


class IdempotencyKeyInProgressError(ConflictError):
    """409: a request with this key is still running. Retryable -- by the time the client tries
    again the first request has usually finished, and the retry is then answered by replay."""

    kind = "idempotency_key_in_progress"
    retryable = True


class IdempotencyKeyReusedError(ValidationError):
    """422: the key was already used for a *different* request. Not retryable: the client has a
    key-generation bug, and repeating the same call cannot fix it."""

    kind = "idempotency_key_reused"
