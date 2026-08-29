"""Postgres adapter for the model registry store (ADR-008 Loop B).

Model stage transitions must survive a restart — otherwise a canary or
production assignment evaporates and the router falls back silently.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError
from ..mlops.model_registry import ModelStage, ModelVersion
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresModelStore"]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_model_versions (
        model_id         TEXT        NOT NULL,
        version          TEXT        NOT NULL,
        provider_kind    TEXT        NOT NULL,
        base_model       TEXT        NOT NULL DEFAULT '',
        stage            TEXT        NOT NULL,
        artefact_uri     TEXT        NOT NULL DEFAULT '',
        dataset_digest   TEXT        NOT NULL DEFAULT '',
        approval_id      TEXT,
        metrics          JSONB       NOT NULL DEFAULT '{}'::jsonb,
        canary_percent   INTEGER     NOT NULL DEFAULT 0,
        created_at       TIMESTAMPTZ NOT NULL,
        promoted_at      TIMESTAMPTZ,
        notes            TEXT        NOT NULL DEFAULT '',
        PRIMARY KEY (model_id, version)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_model_versions_stage
        ON aura_model_versions (model_id, stage, created_at DESC)
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


class PostgresModelStore:
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
            raise _unavailable(f"could not create the model registry schema: {exc}") from exc
        self._schema_ready = True

    async def put(self, version: ModelVersion) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_model_versions (
                        model_id, version, provider_kind, base_model, stage,
                        artefact_uri, dataset_digest, approval_id, metrics,
                        canary_percent, created_at, promoted_at, notes
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13)
                    ON CONFLICT (model_id, version) DO UPDATE SET
                        provider_kind = EXCLUDED.provider_kind,
                        base_model = EXCLUDED.base_model,
                        stage = EXCLUDED.stage,
                        artefact_uri = EXCLUDED.artefact_uri,
                        dataset_digest = EXCLUDED.dataset_digest,
                        approval_id = EXCLUDED.approval_id,
                        metrics = EXCLUDED.metrics,
                        canary_percent = EXCLUDED.canary_percent,
                        created_at = EXCLUDED.created_at,
                        promoted_at = EXCLUDED.promoted_at,
                        notes = EXCLUDED.notes
                    """,
                    version.model_id,
                    version.version,
                    version.provider_kind,
                    version.base_model,
                    str(version.stage),
                    version.artefact_uri,
                    version.dataset_digest,
                    version.approval_id,
                    json.dumps({k: float(v) for k, v in version.metrics.items()}),
                    version.canary_percent,
                    version.created_at,
                    version.promoted_at,
                    version.notes,
                )
        except Exception as exc:
            raise _unavailable(
                f"could not write model version {version.key!r}: {exc}", model=version.key
            ) from exc

    async def get(self, model_id: str, version: str) -> ModelVersion | None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT model_id, version, provider_kind, base_model, stage,
                           artefact_uri, dataset_digest, approval_id, metrics,
                           canary_percent, created_at, promoted_at, notes
                    FROM aura_model_versions
                    WHERE model_id = $1 AND version = $2
                    """,
                    model_id,
                    version,
                )
        except Exception as exc:
            raise _unavailable(f"could not read model version: {exc}") from exc
        return self._row(row) if row else None

    async def list(
        self, *, model_id: str | None = None, stage: ModelStage | None = None
    ) -> Sequence[ModelVersion]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        clauses: list[str] = []
        params: list[Any] = []
        if model_id is not None:
            params.append(model_id)
            clauses.append(f"model_id = ${len(params)}")
        if stage is not None:
            params.append(str(stage))
            clauses.append(f"stage = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT model_id, version, provider_kind, base_model, stage,
                           artefact_uri, dataset_digest, approval_id, metrics,
                           canary_percent, created_at, promoted_at, notes
                    FROM aura_model_versions
                    {where}
                    ORDER BY created_at DESC
                    """,
                    *params,
                )
        except Exception as exc:
            raise _unavailable(f"could not list model versions: {exc}") from exc
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: Mapping[str, Any]) -> ModelVersion:
        metrics = row["metrics"]
        if isinstance(metrics, str):
            metrics = json.loads(metrics)
        return ModelVersion(
            model_id=row["model_id"],
            version=row["version"],
            provider_kind=row["provider_kind"],
            base_model=row["base_model"] or "",
            stage=ModelStage(row["stage"]),
            artefact_uri=row["artefact_uri"] or "",
            dataset_digest=row["dataset_digest"] or "",
            approval_id=row["approval_id"],
            metrics={k: float(v) for k, v in dict(metrics or {}).items()},
            canary_percent=int(row["canary_percent"] or 0),
            created_at=_utc(row["created_at"]) or datetime.now(timezone.utc),
            promoted_at=_utc(row["promoted_at"]),
            notes=row["notes"] or "",
        )
