"""Experiment tracking.

A deliberately small, dependency-free tracker: runs, parameters, metrics, artefacts, and
lineage. Not a replacement for MLflow or Weights & Biases -- it is the seam that makes one
unnecessary on a single VPS, and the interface a real tracker would implement.

The property that matters more than features: **a run records the exact inputs that produced
it** -- dataset digest, base model, code version, and parameters. An experiment you cannot
reproduce is an anecdote, and the most common cause of that is a dataset that changed without
anyone noticing, which the digest makes impossible to miss.
"""

from __future__ import annotations

import enum
import json
import statistics
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.errors import ConflictError, NotFoundError
from ..observability.logging import get_logger

__all__ = [
    "RunStatus",
    "Experiment",
    "RunRecord",
    "MetricPoint",
    "ExperimentStore",
    "InMemoryExperimentStore",
    "ExperimentTracker",
]

logger = get_logger("mlops.experiments")


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MetricPoint:
    name: str
    value: float
    step: int = 0
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class Experiment:
    id: str
    name: str
    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: tuple[str, ...] = ()

    @classmethod
    def create(cls, name: str, *, description: str = "", tags: Sequence[str] = ()) -> "Experiment":
        return cls(id=f"exp_{uuid.uuid4().hex[:12]}", name=name, description=description,
                   tags=tuple(tags))


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One execution: what went in, what came out, and whether it worked."""

    id: str
    experiment_id: str
    status: RunStatus = RunStatus.RUNNING
    parameters: Mapping[str, Any] = field(default_factory=dict)
    metrics: tuple[MetricPoint, ...] = ()
    artefacts: Mapping[str, str] = field(default_factory=dict)
    dataset_digest: str = ""
    base_model: str = ""
    code_version: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    error: str | None = None
    notes: str = ""

    @property
    def duration_s(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def latest(self, metric: str) -> float | None:
        points = [p for p in self.metrics if p.name == metric]
        return max(points, key=lambda p: (p.step, p.at)).value if points else None

    def best(self, metric: str, *, maximise: bool = True) -> float | None:
        values = [p.value for p in self.metrics if p.name == metric]
        if not values:
            return None
        return max(values) if maximise else min(values)

    def series(self, metric: str) -> list[tuple[int, float]]:
        return sorted(
            ((p.step, p.value) for p in self.metrics if p.name == metric), key=lambda x: x[0]
        )

    def as_dict(self) -> dict[str, Any]:
        metric_names = sorted({p.name for p in self.metrics})
        return {
            "id": self.id,
            "experiment_id": self.experiment_id,
            "status": str(self.status),
            "parameters": dict(self.parameters),
            "metrics": {name: self.latest(name) for name in metric_names},
            "artefacts": dict(self.artefacts),
            "dataset_digest": self.dataset_digest,
            "base_model": self.base_model,
            "code_version": self.code_version,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_s": self.duration_s,
            "error": self.error,
            "notes": self.notes,
        }


@runtime_checkable
class ExperimentStore(Protocol):
    async def put_experiment(self, experiment: Experiment) -> None:
        ...

    async def get_experiment(self, experiment_id: str) -> Experiment | None:
        ...

    async def find_experiment(self, name: str) -> Experiment | None:
        ...

    async def put_run(self, run: RunRecord) -> None:
        ...

    async def get_run(self, run_id: str) -> RunRecord | None:
        ...

    async def list_runs(self, experiment_id: str, *, limit: int = 100) -> Sequence[RunRecord]:
        ...


class InMemoryExperimentStore:
    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}
        self._runs: dict[str, RunRecord] = {}

    async def put_experiment(self, experiment: Experiment) -> None:
        self._experiments[experiment.id] = experiment

    async def get_experiment(self, experiment_id: str) -> Experiment | None:
        return self._experiments.get(experiment_id)

    async def find_experiment(self, name: str) -> Experiment | None:
        for experiment in self._experiments.values():
            if experiment.name == name:
                return experiment
        return None

    async def put_run(self, run: RunRecord) -> None:
        self._runs[run.id] = run

    async def get_run(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    async def list_runs(self, experiment_id: str, *, limit: int = 100) -> list[RunRecord]:
        runs = [r for r in self._runs.values() if r.experiment_id == experiment_id]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]


class ExperimentTracker:
    def __init__(self, *, store: ExperimentStore, metrics: Any = None) -> None:
        self._store = store
        self._metrics = metrics

    async def experiment(
        self, name: str, *, description: str = "", tags: Sequence[str] = ()
    ) -> Experiment:
        """Get or create by name. Idempotent, so a scheduled job can call it every run."""
        existing = await self._store.find_experiment(name)
        if existing is not None:
            return existing
        experiment = Experiment.create(name, description=description, tags=tags)
        await self._store.put_experiment(experiment)
        logger.info("experiment created", extra={"experiment": name, "id": experiment.id})
        return experiment

    async def start_run(
        self,
        experiment_id: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        dataset_digest: str = "",
        base_model: str = "",
        code_version: str = "",
        notes: str = "",
    ) -> RunRecord:
        if await self._store.get_experiment(experiment_id) is None:
            raise NotFoundError(f"unknown experiment {experiment_id!r}")
        run = RunRecord(
            id=f"run_{uuid.uuid4().hex[:12]}",
            experiment_id=experiment_id,
            parameters=dict(parameters or {}),
            dataset_digest=dataset_digest,
            base_model=base_model,
            code_version=code_version,
            notes=notes,
        )
        await self._store.put_run(run)
        logger.info("run started", extra={"run": run.id, "experiment": experiment_id})
        return run

    async def log_metric(
        self, run_id: str, name: str, value: float, *, step: int = 0
    ) -> RunRecord:
        run = await self._require_run(run_id)
        updated = replace(run, metrics=run.metrics + (MetricPoint(name=name, value=value, step=step),))
        await self._store.put_run(updated)
        if self._metrics is not None:
            self._metrics.eval_score.labels(suite=run.experiment_id, metric=name).set(value)
        return updated

    async def log_metrics(
        self, run_id: str, values: Mapping[str, float], *, step: int = 0
    ) -> RunRecord:
        run = await self._require_run(run_id)
        points = tuple(MetricPoint(name=k, value=float(v), step=step) for k, v in values.items())
        updated = replace(run, metrics=run.metrics + points)
        await self._store.put_run(updated)
        if self._metrics is not None:
            for name, value in values.items():
                self._metrics.eval_score.labels(suite=run.experiment_id, metric=name).set(value)
        return updated

    async def log_artefact(self, run_id: str, name: str, uri: str) -> RunRecord:
        run = await self._require_run(run_id)
        updated = replace(run, artefacts={**run.artefacts, name: uri})
        await self._store.put_run(updated)
        return updated

    async def finish_run(
        self, run_id: str, *, status: RunStatus = RunStatus.COMPLETED, error: str | None = None
    ) -> RunRecord:
        run = await self._require_run(run_id)
        if run.status is not RunStatus.RUNNING:
            raise ConflictError(
                f"run {run_id} already finished with status {run.status}",
            )
        updated = replace(
            run, status=status, finished_at=datetime.now(timezone.utc), error=error
        )
        await self._store.put_run(updated)
        logger.info(
            "run finished",
            extra={"run": run_id, "status": str(status), "duration_s": updated.duration_s},
        )
        return updated

    async def compare(
        self, experiment_id: str, *, metric: str, maximise: bool = True, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Rank an experiment's runs by a metric.

        The comparison a reviewer actually wants before approving a promotion: which
        configuration won, by how much, and on which dataset.
        """
        runs = await self._store.list_runs(experiment_id, limit=limit)
        scored = [
            {
                "run_id": r.id,
                "value": r.best(metric, maximise=maximise),
                "parameters": dict(r.parameters),
                "dataset_digest": r.dataset_digest,
                "status": str(r.status),
            }
            for r in runs
            if r.best(metric, maximise=maximise) is not None
        ]
        scored.sort(key=lambda row: row["value"], reverse=maximise)
        return scored

    async def summary(self, experiment_id: str, *, metric: str) -> dict[str, Any]:
        """Distribution of a metric across runs.

        Variance matters as much as the mean: a configuration that wins on average but has a
        wide spread is not obviously better than a stable second place, and reporting only the
        best run hides that entirely.
        """
        rows = await self.compare(experiment_id, metric=metric, limit=1000)
        values = [row["value"] for row in rows]
        if not values:
            return {"metric": metric, "runs": 0}
        return {
            "metric": metric,
            "runs": len(values),
            "best": max(values),
            "worst": min(values),
            "mean": round(statistics.fmean(values), 6),
            "stdev": round(statistics.pstdev(values), 6) if len(values) > 1 else 0.0,
        }

    async def _require_run(self, run_id: str) -> RunRecord:
        run = await self._store.get_run(run_id)
        if run is None:
            raise NotFoundError(f"unknown run {run_id!r}")
        return run

    @staticmethod
    def export(runs: Sequence[RunRecord]) -> str:
        return "\n".join(json.dumps(r.as_dict(), default=str) for r in runs)


def timed_step() -> float:
    return time.perf_counter()
