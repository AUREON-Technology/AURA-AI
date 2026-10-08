"""Postgres adapter for the feedback store (ADR-008 Loop B).

A separate module from ``postgres_store.py`` so that file does not keep growing.
Shares ``PostgresPool`` with the document and memory stores when the container
wires one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError
from ..domain.identifiers import UserId
from ..learning.curation import FeedbackRecord
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresFeedbackStore"]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_feedback (
        id          TEXT        PRIMARY KEY,
        owner_id    TEXT        NOT NULL,
        turn_id     TEXT        NOT NULL DEFAULT '',
        rating      INTEGER     NOT NULL,
        correction  TEXT,
        comment     TEXT,
        prompt      TEXT        NOT NULL DEFAULT '',
        response    TEXT        NOT NULL DEFAULT '',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_feedback_owner
        ON aura_feedback (owner_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_feedback_created
        ON aura_feedback (created_at DESC)
    """,
)


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostgresFeedbackStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                for statement in _SCHEMA:
                    await conn.execute(statement)
        except Exception as exc:
            raise _unavailable(f"could not create the feedback schema: {exc}") from exc
        self._schema_ready = True

    async def put(self, record: FeedbackRecord, *, owner_id: UserId) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        turn_id = str(record.metadata.get("turn_id") or "")
        comment = str(record.metadata.get("comment") or "") or None
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_feedback (
                        id, owner_id, turn_id, rating, correction, comment,
                        prompt, response, created_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT (id) DO UPDATE SET
                        rating = EXCLUDED.rating,
                        correction = EXCLUDED.correction,
                        comment = EXCLUDED.comment,
                        prompt = EXCLUDED.prompt,
                        response = EXCLUDED.response
                    """,
                    record.id,
                    owner_id.value,
                    turn_id,
                    record.rating,
                    record.correction,
                    comment,
                    record.prompt,
                    record.response,
                    record.created_at,
                )
        except Exception as exc:
            raise _unavailable(
                f"could not write feedback {record.id!r}: {exc}", feedback=record.id
            ) from exc

    async def list_for_owner(
        self, owner_id: UserId, *, limit: int = 200
    ) -> Sequence[FeedbackRecord]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, owner_id, turn_id, rating, correction, comment,
                           prompt, response, created_at
                    FROM aura_feedback
                    WHERE owner_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    owner_id.value,
                    limit,
                )
        except Exception as exc:
            raise _unavailable(f"could not list feedback: {exc}") from exc
        return [self._record(row) for row in rows]

    async def list_for_curation(
        self, *, since: datetime | None = None, limit: int = 10_000
    ) -> Sequence[FeedbackRecord]:
        """Cross-tenant feed for Loop B. Admin/curator paths only."""
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if since is not None:
                    rows = await conn.fetch(
                        """
                        SELECT id, owner_id, turn_id, rating, correction, comment,
                               prompt, response, created_at
                        FROM aura_feedback
                        WHERE created_at >= $1
                        ORDER BY created_at DESC
                        LIMIT $2
                        """,
                        since,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT id, owner_id, turn_id, rating, correction, comment,
                               prompt, response, created_at
                        FROM aura_feedback
                        ORDER BY created_at DESC
                        LIMIT $1
                        """,
                        limit,
                    )
        except Exception as exc:
            raise _unavailable(f"could not list feedback for curation: {exc}") from exc
        return [self._record(row) for row in rows]

    @staticmethod
    def _record(row: Mapping[str, Any]) -> FeedbackRecord:
        return FeedbackRecord(
            id=row["id"],
            prompt=row["prompt"] or "",
            response=row["response"] or "",
            rating=row["rating"],
            correction=row["correction"],
            user_ref=row["owner_id"] or "",
            created_at=_utc(row["created_at"]) or datetime.now(timezone.utc),
            metadata={
                "turn_id": row["turn_id"] or "",
                "comment": row["comment"] or "",
            },
        )
