"""Postgres adapter for fine-tune job state (ADR-008 Loop B).

Gateway and Celery workers do not share process memory — queued jobs must
live in Postgres so ``drain_finetune_queue_task`` can find them.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError
from ..learning.finetune import FineTuneJob, FineTuneJobStatus
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresJobStore"]


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostgresJobStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def put(self, job: FineTuneJob) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_finetune_jobs (
                        id, approval_id, dataset_digest, base_model, status,
                        model_id, version, artefact_uri, provider_kind, run_id,
                        error, metrics, parameters, created_at, started_at, finished_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14,$15,$16)
                    ON CONFLICT (id) DO UPDATE SET
                        approval_id = EXCLUDED.approval_id,
                        dataset_digest = EXCLUDED.dataset_digest,
                        base_model = EXCLUDED.base_model,
                        status = EXCLUDED.status,
                        model_id = EXCLUDED.model_id,
                        version = EXCLUDED.version,
                        artefact_uri = EXCLUDED.artefact_uri,
                        provider_kind = EXCLUDED.provider_kind,
                        run_id = EXCLUDED.run_id,
                        error = EXCLUDED.error,
                        metrics = EXCLUDED.metrics,
                        parameters = EXCLUDED.parameters,
                        created_at = EXCLUDED.created_at,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at
                    """,
                    job.id,
                    job.approval_id,
                    job.dataset_digest,
                    job.base_model,
                    str(job.status),
                    job.model_id,
                    job.version,
                    job.artefact_uri,
                    job.provider_kind,
                    job.run_id,
                    job.error,
                    json.dumps({k: float(v) for k, v in job.metrics.items()}),
                    json.dumps(dict(job.parameters)),
                    job.created_at,
                    job.started_at,
                    job.finished_at,
                )
        except Exception as exc:
            raise _unavailable(f"could not write fine-tune job {job.id!r}: {exc}", job=job.id) from exc

    async def get(self, job_id: str) -> FineTuneJob | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, approval_id, dataset_digest, base_model, status,
                           model_id, version, artefact_uri, provider_kind, run_id,
                           error, metrics, parameters, created_at, started_at, finished_at
                    FROM aura_finetune_jobs WHERE id = $1
                    """,
                    job_id,
                )
        except Exception as exc:
            raise _unavailable(f"could not read fine-tune job: {exc}") from exc
        return self._row(row) if row else None

    async def list(self, *, limit: int = 50) -> Sequence[FineTuneJob]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, approval_id, dataset_digest, base_model, status,
                           model_id, version, artefact_uri, provider_kind, run_id,
                           error, metrics, parameters, created_at, started_at, finished_at
                    FROM aura_finetune_jobs
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
        except Exception as exc:
            raise _unavailable(f"could not list fine-tune jobs: {exc}") from exc
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: Mapping[str, Any]) -> FineTuneJob:
        metrics = row["metrics"]
        parameters = row["parameters"]
        if isinstance(metrics, str):
            metrics = json.loads(metrics)
        if isinstance(parameters, str):
            parameters = json.loads(parameters)
        return FineTuneJob(
            id=row["id"],
            approval_id=row["approval_id"],
            dataset_digest=row["dataset_digest"],
            base_model=row["base_model"] or "",
            status=FineTuneJobStatus(row["status"]),
            model_id=row["model_id"] or "aura-ft",
            version=row["version"] or "",
            artefact_uri=row["artefact_uri"] or "",
            provider_kind=row["provider_kind"] or "ollama",
            run_id=row["run_id"],
            error=row["error"],
            metrics={k: float(v) for k, v in dict(metrics or {}).items()},
            created_at=_utc(row["created_at"]) or datetime.now(timezone.utc),
            started_at=_utc(row["started_at"]),
            finished_at=_utc(row["finished_at"]),
            parameters=dict(parameters or {}),
        )
