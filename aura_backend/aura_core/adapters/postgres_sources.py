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
from ..domain.knowledge import KnowledgeSource, LicenseStatus, SourceType, TrustTier
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresSourceRegistry"]


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

    async def list_enabled(self) -> Sequence[KnowledgeSource]:
        """Enabled sources only, filtered in SQL rather than in Python.

        The predicate belongs in the query: ingestion calls this every cycle, and reading every
        disabled row back only to drop it costs more the more sources are registered. Filtering
        here also keeps the disabled rows off the wire, which a comprehension over ``list_all``
        cannot do however cheap it looks.
        """
        return await self._fetch_sources(
            "SELECT * FROM aura_knowledge_sources WHERE enabled = TRUE ORDER BY id"
        )

    async def list_all(self) -> Sequence[KnowledgeSource]:
        return await self._fetch_sources("SELECT * FROM aura_knowledge_sources ORDER BY id")

    async def _fetch_sources(self, sql: str) -> Sequence[KnowledgeSource]:
        """Shared read path. ``sql`` is always a literal from this module, never caller input."""
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql)
        except Exception as exc:
            raise _unavailable(f"could not list knowledge sources: {exc}") from exc
        return [self._source(row) for row in rows]

    async def get(self, source_id: str) -> KnowledgeSource | None:
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
                        credentials_ref, last_success_at, metadata,
                        name, publisher, domains, language, license_spdx, license_status,
                        attribution
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                              $14,$15,$16,$17,$18,$19,$20)
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
                    source.name,
                    source.publisher,
                    json.dumps(list(source.domains)),
                    source.language,
                    source.license_spdx,
                    str(source.license_status),
                    source.attribution,
                )
        except Exception as exc:
            raise _unavailable(f"could not register knowledge source: {exc}") from exc
        logger.info(
            "knowledge source registered",
            extra={"source": source.id, "type": str(source.type)},
        )

    async def remove(self, source_id: str) -> bool:
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

    async def update_provenance(
        self,
        source_id: str,
        *,
        name: str | None = None,
        publisher: str | None = None,
        domains: Sequence[str] | None = None,
        language: str | None = None,
        license_spdx: str | None = None,
        license_status: LicenseStatus | None = None,
        attribution: str | None = None,
    ) -> None:
        # One fixed statement: COALESCE keeps a column when its argument is NULL, so a partial
        # update needs no SQL assembled from the argument names.
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE aura_knowledge_sources SET
                        name = COALESCE($2, name),
                        publisher = COALESCE($3, publisher),
                        domains = COALESCE($4::jsonb, domains),
                        language = COALESCE($5, language),
                        license_spdx = COALESCE($6, license_spdx),
                        license_status = COALESCE($7, license_status),
                        attribution = COALESCE($8, attribution)
                    WHERE id = $1
                    """,
                    source_id,
                    name,
                    publisher,
                    json.dumps(list(domains)) if domains is not None else None,
                    language,
                    license_spdx,
                    str(license_status) if license_status is not None else None,
                    attribution,
                )
        except Exception as exc:
            raise _unavailable(f"could not update knowledge source provenance: {exc}") from exc
        if str(result).endswith("0"):
            raise NotFoundError(f"unknown source {source_id!r}")

    @staticmethod
    def _column(row: Mapping[str, Any], key: str, default: Any) -> Any:
        """A column added after 0016, tolerated as absent so an older row shape still reads."""
        try:
            value = row[key]
        except (KeyError, IndexError):
            return default
        return default if value is None else value

    @classmethod
    def _source(cls, row: Mapping[str, Any]) -> KnowledgeSource:
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
            name=cls._column(row, "name", ""),
            publisher=cls._column(row, "publisher", ""),
            domains=tuple(_json_value(cls._column(row, "domains", None), []) or ()),
            language=cls._column(row, "language", None),
            license_spdx=cls._column(row, "license_spdx", None),
            license_status=LicenseStatus(cls._column(row, "license_status", "unknown")),
            attribution=cls._column(row, "attribution", ""),
        )
