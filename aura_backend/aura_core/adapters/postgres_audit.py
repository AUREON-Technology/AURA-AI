"""Postgres implementation of the ``AuditSink`` port.

A separate module from ``postgres_store.py`` so that file does not keep growing.
Selected when the document backend is postgres; shares ``PostgresPool``.
Append-only in normal operation; the only writes after insert are the retention purge and the
erasure pseudonymisation (audit FSA-016).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError
from ..ports.runtime import AuditEvent
from .postgres_store import PostgresPool
from .runtime import SystemClock

logger = logging.getLogger(__name__)

__all__ = ["PostgresAuditSink"]


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostgresAuditSink:
    def __init__(self, url: str, *, pool: PostgresPool | None = None, clock=None) -> None:
        self._pool = pool or PostgresPool(url)
        self._clock = clock or SystemClock()

    async def record(self, event: AuditEvent) -> None:
        if event.at is None:
            event.at = self._clock.now()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_audit_events (
                        id, at, actor, actor_role, action, resource, outcome,
                        correlation_id, source_ip, detail
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    """,
                    uuid.uuid4().hex,
                    event.at,
                    event.actor,
                    event.actor_role,
                    event.action,
                    event.resource,
                    event.outcome,
                    event.correlation_id,
                    event.source_ip,
                    json.dumps(dict(event.detail or {})),
                )
        except Exception as exc:
            raise _unavailable(f"could not write audit event: {exc}") from exc

    async def query(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Sequence[AuditEvent]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, at, actor, actor_role, action, resource, outcome,
                           correlation_id, source_ip, detail
                    FROM aura_audit_events
                    WHERE ($1::text IS NULL OR actor = $1)
                      AND ($2::text IS NULL OR action LIKE $2 || '%')
                      AND ($3::timestamptz IS NULL OR at >= $3)
                    ORDER BY at DESC
                    LIMIT $4
                    """,
                    actor,
                    action,
                    since,
                    limit,
                )
        except Exception as exc:
            raise _unavailable(f"could not query audit events: {exc}") from exc
        return [self._event(row) for row in rows]

    async def purge_before(self, cutoff: datetime) -> int:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute("DELETE FROM aura_audit_events WHERE at < $1", _utc(cutoff))
        except Exception as exc:
            raise _unavailable(f"could not purge audit events: {exc}") from exc
        # asyncpg reports the command tag ("DELETE 3"); the count is its last word.
        return int(str(status).rsplit(" ", 1)[-1] or 0)

    async def pseudonymise_actor(self, actor: str, replacement: str) -> int:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    "UPDATE aura_audit_events SET actor = $2, source_ip = NULL WHERE actor = $1",
                    actor, replacement,
                )
        except Exception as exc:
            raise _unavailable(f"could not pseudonymise audit events: {exc}") from exc
        # asyncpg reports the command tag ("UPDATE 3"); the count is its last word.
        return int(str(status).rsplit(" ", 1)[-1] or 0)

    @staticmethod
    def _event(row: Mapping[str, Any]) -> AuditEvent:
        detail = row["detail"]
        if isinstance(detail, str):
            detail = json.loads(detail)
        return AuditEvent(
            action=row["action"],
            actor=row["actor"],
            outcome=row["outcome"],
            resource=row["resource"] or "",
            actor_role=row["actor_role"] or "",
            correlation_id=row["correlation_id"] or "",
            at=_utc(row["at"]),
            detail=dict(detail or {}),
            source_ip=row["source_ip"],
        )
