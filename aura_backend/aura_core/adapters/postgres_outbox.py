"""Postgres implementation of the ``EventOutbox`` port (Phase E1).

A separate module and a separate table from ``postgres_audit.py``: audit is an append-only,
permanent record; an outbox entry is a work item that gets deleted once delivered. Owns its own
table (``CREATE TABLE IF NOT EXISTS``, like every other ``postgres_*.py`` adapter here) --
unlike ``postgres_goals.py``, there is no legacy table to defer to, this one is new.

``claim_batch`` uses ``FOR UPDATE SKIP LOCKED`` so multiple Celery workers draining
concurrently never claim the same row twice, and a ``claimed_at`` lease (not a plain boolean)
so a worker that crashes mid-batch does not strand its claimed rows forever -- they become
reclaimable once the lease expires.
"""

from __future__ import annotations

import json
import logging
import uuid

from ..domain.errors import DependencyUnavailableError
from ..domain.events import DomainEvent
from ..domain.outbox import OutboxEntry
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresEventOutbox"]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_event_outbox (
        id              TEXT        PRIMARY KEY,
        event_name      TEXT        NOT NULL,
        payload         JSONB       NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        claimed_at      TIMESTAMPTZ,
        attempts        INTEGER     NOT NULL DEFAULT 0,
        last_error      TEXT,
        dead_lettered   BOOLEAN     NOT NULL DEFAULT FALSE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_event_outbox_claimable
        ON aura_event_outbox (created_at)
        WHERE NOT dead_lettered
    """,
)

_LEASE_SECONDS = 300


def _unavailable(message: str, **details) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


class PostgresEventOutbox:
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
            raise _unavailable(f"could not create the event outbox schema: {exc}") from exc
        self._schema_ready = True

    async def enqueue(self, event: DomainEvent) -> str:
        await self._ensure_schema()
        entry_id = uuid.uuid4().hex
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_event_outbox (id, event_name, payload, created_at)
                    VALUES ($1, $2, $3::jsonb, $4)
                    """,
                    entry_id, event.name, json.dumps(event.as_dict()), event.occurred_at,
                )
        except Exception as exc:
            raise _unavailable(f"could not enqueue event {event.name}: {exc}") from exc
        return entry_id

    async def claim_batch(self, *, limit: int = 50) -> list[OutboxEntry]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    rows = await conn.fetch(
                        """
                        SELECT id, event_name, payload, created_at, attempts
                        FROM aura_event_outbox
                        WHERE NOT dead_lettered
                          AND (claimed_at IS NULL OR claimed_at < now() - make_interval(secs => $2))
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT $1
                        """,
                        limit, _LEASE_SECONDS,
                    )
                    if rows:
                        ids = [row["id"] for row in rows]
                        await conn.execute(
                            "UPDATE aura_event_outbox SET claimed_at = now() WHERE id = ANY($1::text[])",
                            ids,
                        )
        except Exception as exc:
            raise _unavailable(f"could not claim outbox entries: {exc}") from exc
        return [
            OutboxEntry(
                id=row["id"], event_name=row["event_name"],
                payload=json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
                created_at=row["created_at"], attempts=row["attempts"],
            )
            for row in rows
        ]

    async def mark_processed(self, entry_id: str) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM aura_event_outbox WHERE id = $1", entry_id)
        except Exception as exc:
            raise _unavailable(f"could not mark outbox entry {entry_id} processed: {exc}") from exc

    async def mark_failed(self, entry_id: str, *, error: str, max_attempts: int = 5) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE aura_event_outbox
                    SET attempts = attempts + 1,
                        claimed_at = NULL,
                        last_error = $2,
                        dead_lettered = (attempts + 1 >= $3)
                    WHERE id = $1
                    """,
                    entry_id, error[:2000], max_attempts,
                )
        except Exception as exc:
            raise _unavailable(f"could not record outbox failure for {entry_id}: {exc}") from exc
