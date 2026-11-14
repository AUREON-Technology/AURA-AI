"""Postgres adapter for the experiment tracker store (ADR-008 Loop B)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError
from ..mlops.experiments import Experiment, MetricPoint, RunRecord, RunStatus
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresExperimentStore"]


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostgresExperimentStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def put_experiment(self, experiment: Experiment) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_experiments (id, name, description, created_at, tags)
                    VALUES ($1,$2,$3,$4,$5::jsonb)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        tags = EXCLUDED.tags
                    """,
                    experiment.id,
                    experiment.name,
                    experiment.description,
                    experiment.created_at,
                    json.dumps(list(experiment.tags)),
                )
        except Exception as exc:
            raise _unavailable(
                f"could not write experiment {experiment.id!r}: {exc}", experiment=experiment.id
            ) from exc

    async def get_experiment(self, experiment_id: str) -> Experiment | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, name, description, created_at, tags FROM aura_experiments WHERE id = $1",
                    experiment_id,
                )
        except Exception as exc:
            raise _unavailable(f"could not read experiment: {exc}") from exc
        return self._experiment(row) if row else None

    async def find_experiment(self, name: str) -> Experiment | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, name, description, created_at, tags FROM aura_experiments WHERE name = $1",
                    name,
                )
        except Exception as exc:
            raise _unavailable(f"could not find experiment: {exc}") from exc
        return self._experiment(row) if row else None

    async def put_run(self, run: RunRecord) -> None:
        pool = await self._pool._get_pool()
        metrics = [
            {
                "name": p.name,
                "value": float(p.value),
                "step": p.step,
                "at": p.at.isoformat(),
            }
            for p in run.metrics
        ]
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_experiment_runs (
                        id, experiment_id, status, parameters, metrics, artefacts,
                        dataset_digest, base_model, code_version, started_at,
                        finished_at, error, notes
                    ) VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        parameters = EXCLUDED.parameters,
                        metrics = EXCLUDED.metrics,
                        artefacts = EXCLUDED.artefacts,
                        dataset_digest = EXCLUDED.dataset_digest,
                        base_model = EXCLUDED.base_model,
                        code_version = EXCLUDED.code_version,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        error = EXCLUDED.error,
                        notes = EXCLUDED.notes
                    """,
                    run.id,
                    run.experiment_id,
                    str(run.status),
                    json.dumps(dict(run.parameters)),
                    json.dumps(metrics),
                    json.dumps(dict(run.artefacts)),
                    run.dataset_digest,
                    run.base_model,
                    run.code_version,
                    run.started_at,
                    run.finished_at,
                    run.error,
                    run.notes,
                )
        except Exception as exc:
            raise _unavailable(f"could not write run {run.id!r}: {exc}", run=run.id) from exc

    async def get_run(self, run_id: str) -> RunRecord | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, experiment_id, status, parameters, metrics, artefacts,
                           dataset_digest, base_model, code_version, started_at,
                           finished_at, error, notes
                    FROM aura_experiment_runs WHERE id = $1
                    """,
                    run_id,
                )
        except Exception as exc:
            raise _unavailable(f"could not read run: {exc}") from exc
        return self._run(row) if row else None

    async def list_runs(self, experiment_id: str, *, limit: int = 100) -> Sequence[RunRecord]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, experiment_id, status, parameters, metrics, artefacts,
                           dataset_digest, base_model, code_version, started_at,
                           finished_at, error, notes
                    FROM aura_experiment_runs
                    WHERE experiment_id = $1
                    ORDER BY started_at DESC
                    LIMIT $2
                    """,
                    experiment_id,
                    limit,
                )
        except Exception as exc:
            raise _unavailable(f"could not list runs: {exc}") from exc
        return [self._run(row) for row in rows]

    @staticmethod
    def _experiment(row: Mapping[str, Any]) -> Experiment:
        tags = row["tags"]
        if isinstance(tags, str):
            tags = json.loads(tags)
        return Experiment(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            created_at=_utc(row["created_at"]) or datetime.now(timezone.utc),
            tags=tuple(tags or ()),
        )

    @staticmethod
    def _run(row: Mapping[str, Any]) -> RunRecord:
        parameters = row["parameters"]
        metrics_raw = row["metrics"]
        artefacts = row["artefacts"]
        if isinstance(parameters, str):
            parameters = json.loads(parameters)
        if isinstance(metrics_raw, str):
            metrics_raw = json.loads(metrics_raw)
        if isinstance(artefacts, str):
            artefacts = json.loads(artefacts)
        points: list[MetricPoint] = []
        for item in metrics_raw or []:
            at = item.get("at")
            points.append(
                MetricPoint(
                    name=item["name"],
                    value=float(item["value"]),
                    step=int(item.get("step", 0)),
                    at=(
                        datetime.fromisoformat(at)
                        if isinstance(at, str)
                        else (_utc(at) or datetime.now(timezone.utc))
                    ),
                )
            )
        return RunRecord(
            id=row["id"],
            experiment_id=row["experiment_id"],
            status=RunStatus(row["status"]),
            parameters=dict(parameters or {}),
            metrics=tuple(points),
            artefacts=dict(artefacts or {}),
            dataset_digest=row["dataset_digest"] or "",
            base_model=row["base_model"] or "",
            code_version=row["code_version"] or "",
            started_at=_utc(row["started_at"]) or datetime.now(timezone.utc),
            finished_at=_utc(row["finished_at"]),
            error=row["error"],
            notes=row["notes"] or "",
        )
