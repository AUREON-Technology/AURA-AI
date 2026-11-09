"""Postgres implementation of the ``IdempotencyStore`` port (roadmap P1.4).

Shares the container's ``PostgresPool`` like every other ``postgres_*.py`` adapter. Its table is owned
by Alembic revision 0006; the adapter issues no DDL (CB-20).

**Atomic claim without a transaction-spanning lock.** The claim is at most three single
statements, each atomic on its own:

1. ``INSERT ... ON CONFLICT DO NOTHING RETURNING`` -- a brand-new key. Postgres serialises
   concurrent inserts of the same primary key, so exactly one caller gets the row back.
2. ``UPDATE ... WHERE <re-claimable> RETURNING`` -- an expired record, a ``failed`` one, or an
   ``in_progress`` one whose lease lapsed. Under READ COMMITTED a second updater blocked on the
   row lock re-evaluates the ``WHERE`` against the winner's committed row (which is now a fresh
   ``in_progress`` claim) and matches nothing, so again exactly one caller wins.
3. ``SELECT`` -- nobody could claim; classify what is there (replay, in progress, mismatch).

A record purged or re-claimed between two of those steps just sends the loop round again.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from ..domain.errors import DependencyUnavailableError
from ..domain.identifiers import UserId
from ..domain.idempotency import (
    ClaimOutcome,
    ClaimResult,
    IdempotencyRecord,
    IdempotencyStatus,
    StoredResponse,
)
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresIdempotencyStore"]


_MAX_CLAIM_ATTEMPTS = 4


def _unavailable(message: str) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _affected(status: str) -> int:
    """asyncpg returns the command tag (``"UPDATE 1"``, ``"DELETE 3"``) from ``execute``."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, AttributeError):
        return 0


class PostgresIdempotencyStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    @staticmethod
    def _record(row: Mapping[str, Any]) -> IdempotencyRecord:
        response = None
        if row["response_status"] is not None:
            headers = row["response_headers"]
            if isinstance(headers, str):
                headers = json.loads(headers)
            response = StoredResponse(
                status_code=int(row["response_status"]),
                body=bytes(row["response_body"] or b""),
                headers=dict(headers or {}),
            )
        return IdempotencyRecord(
            user_id=UserId(row["user_id"]),
            operation=row["operation"],
            key=row["idem_key"],
            request_hash=row["request_hash"],
            status=IdempotencyStatus(row["status"]),
            created_at=_utc(row["created_at"]),
            expires_at=_utc(row["expires_at"]),
            updated_at=_utc(row["updated_at"]),
            claim_token=row["claim_token"],
            response=response,
        )

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
        now = _utc(now)
        expires_at = now + ttl
        stale_before = now - lease
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                for _ in range(_MAX_CLAIM_ATTEMPTS):
                    token = uuid.uuid4().hex
                    row = await conn.fetchrow(
                        """
                        INSERT INTO aura_idempotency_keys (
                            user_id, operation, idem_key, request_hash, status, claim_token,
                            created_at, updated_at, expires_at
                        ) VALUES ($1, $2, $3, $4, 'in_progress', $5, $6, $6, $7)
                        ON CONFLICT (user_id, operation, idem_key) DO NOTHING
                        RETURNING *
                        """,
                        user_id.value, operation, key, request_hash, token, now, expires_at,
                    )
                    if row is not None:
                        return ClaimResult(ClaimOutcome.CLAIMED, self._record(row))

                    # An expired record is a brand-new key (fresh hash, fresh TTL). A failed or
                    # lease-lapsed one is the same request retried: it keeps its TTL window.
                    row = await conn.fetchrow(
                        """
                        UPDATE aura_idempotency_keys SET
                            request_hash = $4,
                            status = 'in_progress',
                            claim_token = $5,
                            response_status = NULL,
                            response_body = NULL,
                            response_headers = NULL,
                            created_at = CASE WHEN expires_at <= $6 THEN $6 ELSE created_at END,
                            expires_at = CASE WHEN expires_at <= $6 THEN $7 ELSE expires_at END,
                            updated_at = $6
                        WHERE user_id = $1 AND operation = $2 AND idem_key = $3
                          AND (
                              expires_at <= $6
                              OR (request_hash = $4 AND status = 'failed')
                              OR (request_hash = $4 AND status = 'in_progress'
                                  AND updated_at <= $8)
                          )
                        RETURNING *
                        """,
                        user_id.value, operation, key, request_hash, token, now, expires_at,
                        stale_before,
                    )
                    if row is not None:
                        return ClaimResult(ClaimOutcome.CLAIMED, self._record(row))

                    row = await conn.fetchrow(
                        """
                        SELECT * FROM aura_idempotency_keys
                        WHERE user_id = $1 AND operation = $2 AND idem_key = $3
                        """,
                        user_id.value, operation, key,
                    )
                    if row is None:
                        continue  # purged between statements; the INSERT will take it now
                    record = self._record(row)
                    if record.is_expired(now):
                        continue
                    if record.request_hash != request_hash:
                        return ClaimResult(ClaimOutcome.MISMATCH, record)
                    if record.status is IdempotencyStatus.COMPLETED:
                        return ClaimResult(ClaimOutcome.REPLAY, record)
                    if record.status is IdempotencyStatus.FAILED or record.is_stale_claim(
                        now, lease
                    ):
                        continue  # became re-claimable after our UPDATE looked; try again
                    return ClaimResult(ClaimOutcome.IN_PROGRESS, record)
        except DependencyUnavailableError:
            raise
        except Exception as exc:
            raise _unavailable(f"could not claim idempotency key: {exc}") from exc
        # Only reachable if the row churned under us on every attempt -- a pathological race.
        # Reporting "in progress" is the safe answer: the client retries, nothing runs twice.
        raise _unavailable("idempotency key is contended; retry the request")

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
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    """
                    UPDATE aura_idempotency_keys SET
                        status = 'completed',
                        response_status = $5,
                        response_body = $6,
                        response_headers = $7::jsonb,
                        updated_at = $8
                    WHERE user_id = $1 AND operation = $2 AND idem_key = $3
                      AND claim_token = $4 AND status = 'in_progress'
                    """,
                    user_id.value, operation, key, claim_token,
                    response.status_code, bytes(response.body),
                    json.dumps(dict(response.headers)), _utc(now),
                )
        except Exception as exc:
            raise _unavailable(f"could not complete idempotency key: {exc}") from exc
        return _affected(status) == 1

    async def fail(
        self,
        *,
        user_id: UserId,
        operation: str,
        key: str,
        claim_token: str,
        now: datetime,
    ) -> bool:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    """
                    UPDATE aura_idempotency_keys SET status = 'failed', updated_at = $5
                    WHERE user_id = $1 AND operation = $2 AND idem_key = $3
                      AND claim_token = $4 AND status = 'in_progress'
                    """,
                    user_id.value, operation, key, claim_token, _utc(now),
                )
        except Exception as exc:
            raise _unavailable(f"could not release idempotency key: {exc}") from exc
        return _affected(status) == 1

    async def purge_expired(self, *, now: datetime) -> int:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    "DELETE FROM aura_idempotency_keys WHERE expires_at <= $1", _utc(now)
                )
        except Exception as exc:
            raise _unavailable(f"could not purge expired idempotency keys: {exc}") from exc
        return _affected(status)
