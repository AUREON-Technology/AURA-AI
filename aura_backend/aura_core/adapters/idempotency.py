"""In-memory ``IdempotencyStore`` (roadmap P1.4).

A real implementation, not a mock: the claim rules are the port's, and one ``asyncio.Lock`` makes
``claim`` atomic within the process -- which is the whole of "atomic" for a single-process
deployment and for the default test suite. It is not shared between gateway workers or across a
restart, which is why a Postgres-backed deployment uses ``postgres_idempotency.py`` instead.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timedelta

from ..domain.identifiers import UserId
from ..domain.idempotency import (
    ClaimOutcome,
    ClaimResult,
    IdempotencyRecord,
    IdempotencyStatus,
    StoredResponse,
)

__all__ = ["InMemoryIdempotencyStore"]

_Scope = tuple[str, str, str]


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._records: dict[_Scope, IdempotencyRecord] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _scope(user_id: UserId, operation: str, key: str) -> _Scope:
        return (user_id.value, operation, key)

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
        scope = self._scope(user_id, operation, key)
        async with self._lock:
            existing = self._records.get(scope)
            if existing is None or existing.is_expired(now):
                record = IdempotencyRecord(
                    user_id=user_id, operation=operation, key=key, request_hash=request_hash,
                    status=IdempotencyStatus.IN_PROGRESS, created_at=now, expires_at=now + ttl,
                    updated_at=now, claim_token=uuid.uuid4().hex,
                )
                self._records[scope] = record
                return ClaimResult(ClaimOutcome.CLAIMED, record)
            if existing.request_hash != request_hash:
                return ClaimResult(ClaimOutcome.MISMATCH, existing)
            if existing.status is IdempotencyStatus.COMPLETED:
                return ClaimResult(ClaimOutcome.REPLAY, existing)
            if existing.status is IdempotencyStatus.FAILED or existing.is_stale_claim(now, lease):
                record = replace(
                    existing, status=IdempotencyStatus.IN_PROGRESS, updated_at=now,
                    claim_token=uuid.uuid4().hex, response=None,
                )
                self._records[scope] = record
                return ClaimResult(ClaimOutcome.CLAIMED, record)
            return ClaimResult(ClaimOutcome.IN_PROGRESS, existing)

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
        return await self._finish(
            self._scope(user_id, operation, key), claim_token,
            status=IdempotencyStatus.COMPLETED, response=response, now=now,
        )

    async def fail(
        self,
        *,
        user_id: UserId,
        operation: str,
        key: str,
        claim_token: str,
        now: datetime,
    ) -> bool:
        return await self._finish(
            self._scope(user_id, operation, key), claim_token,
            status=IdempotencyStatus.FAILED, response=None, now=now,
        )

    async def _finish(
        self,
        scope: _Scope,
        claim_token: str,
        *,
        status: IdempotencyStatus,
        response: StoredResponse | None,
        now: datetime,
    ) -> bool:
        async with self._lock:
            existing = self._records.get(scope)
            if (
                existing is None
                or existing.claim_token != claim_token
                or existing.status is not IdempotencyStatus.IN_PROGRESS
            ):
                return False
            self._records[scope] = replace(
                existing, status=status, response=response, updated_at=now
            )
            return True

    async def purge_expired(self, *, now: datetime) -> int:
        async with self._lock:
            expired = [scope for scope, rec in self._records.items() if rec.is_expired(now)]
            for scope in expired:
                del self._records[scope]
            return len(expired)

    def __len__(self) -> int:
        return len(self._records)
