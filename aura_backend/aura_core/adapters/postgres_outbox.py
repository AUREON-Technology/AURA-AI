"""Postgres implementation of the ``EventOutbox`` port (Phase E1).

A separate module and a separate table from ``postgres_audit.py``: audit is an append-only,
permanent record; an outbox entry is a work item that gets deleted once delivered. Its own table,
created by Alembic (the ``0016`` baseline) -- unlike ``postgres_goals.py``, there is no legacy table
to defer to.

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


_LEASE_SECONDS = 300


def _unavailable(message: str, **details) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


class PostgresEventOutbox:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def enqueue(self, event: DomainEvent) -> str:
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
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM aura_event_outbox WHERE id = $1", entry_id)
        except Exception as exc:
            raise _unavailable(f"could not mark outbox entry {entry_id} processed: {exc}") from exc

    async def stats(self) -> dict[str, float]:
        """Backlog shape for the outbox gauges (F-24), in one indexed scan."""
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        count(*) FILTER (WHERE NOT dead_lettered) AS pending,
                        count(*) FILTER (WHERE dead_lettered) AS dead_lettered,
                        COALESCE(EXTRACT(EPOCH FROM now() - min(created_at)
                            FILTER (WHERE NOT dead_lettered)), 0) AS oldest_age_s
                    FROM aura_event_outbox
                    """
                )
        except Exception as exc:
            raise _unavailable(f"could not read outbox stats: {exc}") from exc
        # EXTRACT returns numeric, which asyncpg maps to Decimal.
        return {
            "pending": int(row["pending"]),
            "dead_lettered": int(row["dead_lettered"]),
            "oldest_age_s": float(row["oldest_age_s"]),
        }

    async def mark_failed(self, entry_id: str, *, error: str, max_attempts: int = 5) -> None:
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
