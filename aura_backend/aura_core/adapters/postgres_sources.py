"""Postgres implementation of the knowledge ``SourceRegistry`` port.

A separate module from ``postgres_store.py`` so that file does not keep growing.
Selected when the document backend is postgres; shares ``PostgresPool``.
Without a durable registry the 02:00 refresh is a no-op after every restart.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError, NotFoundError, ValidationError
from ..domain.identifiers import UserId
from ..domain.knowledge import KnowledgeSource, SourceType, TrustTier
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresSourceRegistry"]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_knowledge_sources (
        id                      TEXT        PRIMARY KEY,
        type                    TEXT        NOT NULL,
        uri                     TEXT        NOT NULL,
        owner_id                TEXT,
        trust_tier              INTEGER     NOT NULL DEFAULT 2,
        enabled                 BOOLEAN     NOT NULL DEFAULT TRUE,
        cursor                  TEXT,
        max_documents           INTEGER     NOT NULL DEFAULT 1000,
        max_bytes_per_document  INTEGER     NOT NULL DEFAULT 5242880,
        schedule_cron           TEXT        NOT NULL DEFAULT '0 2 * * *',
        credentials_ref         TEXT,
        last_success_at         TIMESTAMPTZ,
        metadata                JSONB       NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_knowledge_sources_enabled
        ON aura_knowledge_sources (enabled)
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


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class PostgresSourceRegistry:
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
            raise _unavailable(f"could not create the source registry schema: {exc}") from exc
        self._schema_ready = True

    async def list_enabled(self) -> Sequence[KnowledgeSource]:
        return [source for source in await self.list_all() if source.enabled]

    async def list_all(self) -> Sequence[KnowledgeSource]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM aura_knowledge_sources ORDER BY id"
                )
        except Exception as exc:
            raise _unavailable(f"could not list knowledge sources: {exc}") from exc
        return [self._source(row) for row in rows]

    async def get(self, source_id: str) -> KnowledgeSource | None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM aura_knowledge_sources WHERE id = $1", source_id
                )
        except Exception as exc:
            raise _unavailable(f"could not get knowledge source: {exc}") from exc
        return self._source(row) if row is not None else None

    async def register(self, source: KnowledgeSource) -> None:
        await self._ensure_schema()
        if await self.get(source.id) is not None:
            raise ValidationError(
                f"source {source.id!r} is already registered",
                details={"source": source.id},
            )
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_knowledge_sources (
                        id, type, uri, owner_id, trust_tier, enabled, cursor,
                        max_documents, max_bytes_per_document, schedule_cron,
                        credentials_ref, last_success_at, metadata
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    """,
                    source.id,
                    str(source.type),
                    source.uri,
                    source.owner_id.value if source.owner_id else None,
                    int(source.trust_tier),
                    source.enabled,
                    source.last_cursor,
                    source.max_documents,
                    source.max_bytes_per_document,
                    source.schedule_cron,
                    source.credentials_ref,
                    source.last_success_at,
                    json.dumps(dict(source.metadata or {})),
                )
        except Exception as exc:
            raise _unavailable(f"could not register knowledge source: {exc}") from exc
        logger.info(
            "knowledge source registered",
            extra={"source": source.id, "type": str(source.type)},
        )

    async def remove(self, source_id: str) -> bool:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM aura_knowledge_sources WHERE id = $1", source_id
                )
        except Exception as exc:
            raise _unavailable(f"could not remove knowledge source: {exc}") from exc
        return str(result).endswith("1")

    async def set_enabled(self, source_id: str, enabled: bool) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE aura_knowledge_sources SET enabled = $2 WHERE id = $1",
                    source_id,
                    enabled,
                )
        except Exception as exc:
            raise _unavailable(f"could not update knowledge source: {exc}") from exc
        if str(result).endswith("0"):
            raise NotFoundError(f"unknown source {source_id!r}")

    async def record_success(self, source_id: str, *, cursor: str | None, at: datetime) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE aura_knowledge_sources
                    SET cursor = $2, last_success_at = $3
                    WHERE id = $1
                    """,
                    source_id,
                    cursor,
                    at,
                )
        except Exception as exc:
            raise _unavailable(f"could not record source progress: {exc}") from exc

    @staticmethod
    def _source(row: Mapping[str, Any]) -> KnowledgeSource:
        owner_raw = row["owner_id"]
        metadata = _json_value(row["metadata"], {})
        return KnowledgeSource(
            id=row["id"],
            type=SourceType(row["type"]),
            uri=row["uri"],
            enabled=bool(row["enabled"]),
            trust_tier=TrustTier(int(row["trust_tier"])),
            owner_id=UserId(owner_raw) if owner_raw else None,
            schedule_cron=row["schedule_cron"] or "0 2 * * *",
            max_documents=int(row["max_documents"]),
            max_bytes_per_document=int(row["max_bytes_per_document"]),
            credentials_ref=row["credentials_ref"],
            last_success_at=_utc(row["last_success_at"]),
            last_cursor=row["cursor"],
            metadata=dict(metadata or {}),
        )
