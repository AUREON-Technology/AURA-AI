"""Postgres implementation of the ``EventOutbox`` port (Phase E1).

A separate module and a separate table from ``postgres_audit.py``: audit is an append-only,
permanent record; an outbox entry is a work item that gets deleted once delivered. Its own table,
created by Alembic (the ``0016`` baseline) -- unlike ``postgres_goals.py``, there is no legacy table
to defer to.

``claim_batch`` uses ``FOR UPDATE SKIP LOCKED`` so multiple Celery workers draining
concurrently never claim the same row twice, and a ``claimed_at`` lease (not a plain boolean)
so a worker that crashes mid-batch does not strand its claimed rows forever -- they become
reclaimable once the lease expires.

**The lease is renewed per entry (F33).** The drain dispatches a claimed batch one entry at a
time, so the last entry of a batch may start long after the claim. With one lease for the whole
batch, a batch slower than ``_LEASE_SECONDS`` in total had its unstarted tail reclaimed by another
worker while this one was still working through it, and those entries were dispatched twice. Now
each ``mark_processed`` / ``mark_failed`` renews the lease on the rest of the claiming worker's
batch, so the lease has to outlast one dispatch rather than a whole batch. The renewal is fenced
on the latest ``claimed_at`` this worker wrote: an entry another worker has since reclaimed
(because this one hung on a single dispatch past the lease) carries a later claim time, is that
worker's, and is left alone.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..domain.errors import DependencyUnavailableError
from ..domain.events import DomainEvent
from ..domain.outbox import OutboxEntry
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresEventOutbox"]


_LEASE_SECONDS = 300


@dataclass
class _Claim:
    """One ``claim_batch`` call's entries not yet finished, and the lease time this worker wrote."""

    claimed_at: datetime
    ids: set[str] = field(default_factory=set)


def _unavailable(message: str, **details) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


class PostgresEventOutbox:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)
        #: Entry id -> the claim it belongs to, for renewing the rest of its batch (F33).
        self._claims: dict[str, _Claim] = {}

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
                    claimed_at = None
                    if rows:
                        ids = [row["id"] for row in rows]
                        # now() is the transaction's start time: one value for the whole batch.
                        claimed_at = await conn.fetchval(
                            "UPDATE aura_event_outbox SET claimed_at = now() WHERE id = ANY($1::text[]) "
                            "RETURNING claimed_at",
                            ids,
                        )
        except Exception as exc:
            raise _unavailable(f"could not claim outbox entries: {exc}") from exc
        self._forget_expired(claimed_at)
        if rows:
            claim = _Claim(claimed_at=claimed_at, ids={row["id"] for row in rows})
            for entry_id in claim.ids:
                self._claims[entry_id] = claim
        return [
            OutboxEntry(
                id=row["id"], event_name=row["event_name"],
                payload=json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
                created_at=row["created_at"], attempts=row["attempts"],
            )
            for row in rows
        ]

    def _forget_expired(self, now: datetime | None) -> None:
        """Drop claims whose lease has run out. An entry whose dispatch or ``mark_*`` raised is
        never finished here, so without this its claim would stay in memory for the life of the
        worker; once the lease is gone any worker may reclaim it, and renewing it would be fenced
        off anyway."""
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=_LEASE_SECONDS)
        for entry_id in [e for e, claim in self._claims.items() if claim.claimed_at < cutoff]:
            del self._claims[entry_id]

    async def _renew_rest(self, conn, entry_id: str) -> None:
        """Restart the lease on the entries of ``entry_id``'s batch not finished yet (F33).

        Fenced on ``claimed_at``: another worker can only reclaim an entry after this worker's
        lease on it ran out, so its claim time is later than any this worker wrote. Such an entry
        matches nothing here and drops out of this worker's claim; its lease stays the other
        worker's."""
        claim = self._claims.pop(entry_id, None)
        if claim is None:
            return
        claim.ids.discard(entry_id)
        if not claim.ids:
            return
        rows = await conn.fetch(
            "UPDATE aura_event_outbox SET claimed_at = now() "
            "WHERE id = ANY($1::text[]) AND claimed_at <= $2 RETURNING id, claimed_at",
            sorted(claim.ids), claim.claimed_at,
        )
        kept = {row["id"] for row in rows}
        for lost in claim.ids - kept:
            self._claims.pop(lost, None)
        claim.ids = kept
        if rows:
            claim.claimed_at = rows[0]["claimed_at"]

    async def mark_processed(self, entry_id: str) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM aura_event_outbox WHERE id = $1", entry_id)
                await self._renew_rest(conn, entry_id)
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
                await self._renew_rest(conn, entry_id)
        except Exception as exc:
            raise _unavailable(f"could not record outbox failure for {entry_id}: {exc}") from exc
