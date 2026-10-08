"""Postgres implementation of the ``AuditSink`` port.

A separate module from ``postgres_store.py`` so that file does not keep growing.
Selected when the document backend is postgres; shares ``PostgresPool``.
Append-only: this adapter never updates or deletes rows.
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

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_audit_events (
        id              TEXT        PRIMARY KEY,
        at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        actor           TEXT        NOT NULL,
        actor_role      TEXT        NOT NULL DEFAULT '',
        action          TEXT        NOT NULL,
        resource        TEXT        NOT NULL DEFAULT '',
        outcome         TEXT        NOT NULL,
        correlation_id  TEXT        NOT NULL DEFAULT '',
        source_ip       TEXT,
        detail          JSONB       NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_audit_events_at
        ON aura_audit_events (at)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_audit_events_actor_at
        ON aura_audit_events (actor, at)
    """,
)


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
            raise _unavailable(f"could not create the audit schema: {exc}") from exc
        self._schema_ready = True

    async def record(self, event: AuditEvent) -> None:
        await self._ensure_schema()
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
        await self._ensure_schema()
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
