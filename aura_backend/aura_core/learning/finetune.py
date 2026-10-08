"""Fine-tune Loop B service: curate, recommend, approve, launch (ADR-008).

The system may prepare datasets and recommend jobs. Launching a trainer and
promoting a model both require a recorded human approval bound to an artefact
digest. Auto-launch is refused at settings validation.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import ConflictError, NotFoundError, ValidationError
from ..mlops.experiments import ExperimentTracker, RunStatus
from ..mlops.model_registry import ModelRegistry, ModelStage, ModelVersion
from ..observability.logging import get_logger
from ..ports.mlops import FineTuneJobSpec, FineTuneTrainer, TrainResult
from .approval import ApprovalKind, ApprovalRequest, ApprovalService
from .curation import (
    CuratedExample,
    DatasetCurator,
    DatasetStatistics,
    FineTuneRecommendation,
    dataset_digest,
)
from .datasets import DatasetStore, StoredDataset
from .feedback import FeedbackStore

__all__ = [
    "FineTuneService",
    "FineTuneJobStatus",
    "FineTuneJob",
    "FineTuneJobEngine",
    "ScriptedTrainer",
    "JobStore",
    "InMemoryJobStore",
]

logger = get_logger("learning.finetune")


class FineTuneJobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    #: Model weights were updated and a digest-identified artefact was saved. Only this status
    #: registers a CANDIDATE model version.
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: The trainer ran to completion but, by its own account, changed no weights (the default
    #: gateway trainer, a dry run). ``FineTuneJob.error`` carries the reason. Terminal, never
    #: registers a model version, so there is nothing to promote (GAP-018).
    NOT_TRAINED = "not_trained"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FineTuneJob:
    id: str
    approval_id: str
    dataset_digest: str
    base_model: str
    status: FineTuneJobStatus = FineTuneJobStatus.QUEUED
    model_id: str = "aura-ft"
    version: str = ""
    artefact_uri: str = ""
    provider_kind: str = "ollama"
    run_id: str | None = None
    error: str | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "approval_id": self.approval_id,
            "dataset_digest": self.dataset_digest,
            "base_model": self.base_model,
            "status": str(self.status),
            "model_id": self.model_id,
            "version": self.version,
            "artefact_uri": self.artefact_uri,
            "provider_kind": self.provider_kind,
            "run_id": self.run_id,
            "error": self.error,
            "metrics": dict(self.metrics),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "parameters": dict(self.parameters),
        }


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, FineTuneJob] = {}

    async def put(self, job: FineTuneJob) -> None:
        self._jobs[job.id] = job

    async def get(self, job_id: str) -> FineTuneJob | None:
        return self._jobs.get(job_id)

    async def list(self, *, limit: int = 50) -> list[FineTuneJob]:
        rows = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return rows[:limit]


#: Protocol-shaped alias used by the engine (duck-typed in practice).
JobStore = InMemoryJobStore


class ScriptedTrainer:
    """The gateway's default trainer: it trains nothing, and says so.

    No torch, no subprocess. Every job it runs ends ``NOT_TRAINED`` with the reason below, and no
    model version is registered — the approved dataset is the only output. It exists so the
    approval → job → audit path works end to end on a host with no training worker, without
    pretending that path produced a model. Real weight updates come only from
    ``LocalLoraTrainer`` (``AURA_FINETUNE_TRAINER=local`` on the ``worker-training`` image).
    """

    REASON = (
        "no trainer configured (AURA_FINETUNE_TRAINER=scripted): the approved dataset was "
        "prepared, but no weights were changed and no model was produced"
    )

    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        return TrainResult.not_trained(
            self.REASON,
            metrics={"examples": float(_count_examples(job.dataset_jsonl))},
        )


def _count_examples(jsonl: str) -> int:
    return sum(1 for line in jsonl.splitlines() if line.strip())


def _spec_hyperparameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Translate a job's ``parameters`` mapping into typed ``FineTuneJobSpec`` fields.

    Accepts the short keys the engine has always stored (``rank``, ``lr``, ``epochs``) and the
    spec's own field names. A value that cannot be coerced is a ``ValidationError`` here, at the
    boundary, rather than an ``AttributeError`` or ``TypeError`` deep inside a trainer.
    """
    aliases = {
        "rank": "lora_rank",
        "alpha": "lora_alpha",
        "dropout": "lora_dropout",
        "lr": "learning_rate",
        "target_modules": "lora_target_modules",
        "revision": "base_model_revision",
    }
    casts: dict[str, Any] = {
        "epochs": int,
        "batch_size": int,
        "learning_rate": float,
        "max_steps": int,
        "lora_rank": int,
        "lora_alpha": int,
        "lora_dropout": float,
        "max_seq_len": int,
        "seed": int,
        "base_model_revision": str,
    }
    out: dict[str, Any] = {}
    for key, value in parameters.items():
        name = aliases.get(key, key)
        if name == "lora_target_modules":
            if isinstance(value, str):
                value = [v for v in (p.strip() for p in value.split(",")) if v]
            out[name] = tuple(str(v) for v in (value or ()))
            continue
        cast = casts.get(name)
        if cast is None or value is None:
            continue
        try:
            out[name] = cast(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"fine-tune parameter {key!r} is not a valid {cast.__name__}",
                details={"parameter": key, "value": repr(value)},
            ) from exc
    for name in ("epochs", "batch_size", "lora_rank", "lora_alpha", "max_seq_len"):
        if name in out and out[name] < 1:
            raise ValidationError(f"fine-tune parameter {name!r} must be >= 1")
    if "max_steps" in out and out["max_steps"] < 1:
        raise ValidationError("fine-tune parameter 'max_steps' must be >= 1")
    if "learning_rate" in out and not out["learning_rate"] > 0:
        raise ValidationError("fine-tune parameter 'learning_rate' must be > 0")
    if "lora_dropout" in out and not 0.0 <= out["lora_dropout"] < 1.0:
        raise ValidationError("fine-tune parameter 'lora_dropout' must be within [0, 1)")
    return out


class FineTuneService:
    """Curate feedback, recommend, persist datasets, request FINETUNE approval."""

    def __init__(
        self,
        *,
        feedback: FeedbackStore,
        datasets: DatasetStore,
        approvals: ApprovalService,
        curator: DatasetCurator | None = None,
        base_model: str = "",
        estimated_train_hours_cpu: float | None = None,
    ) -> None:
        self._feedback = feedback
        self._datasets = datasets
        self._approvals = approvals
        self._curator = curator or DatasetCurator()
        self._base_model = base_model
        self._cpu_hours = estimated_train_hours_cpu

    async def curate(
        self, *, since: datetime | None = None, limit: int = 10_000
    ) -> tuple[list[CuratedExample], DatasetStatistics]:
        records = await self._feedback.list_for_curation(since=since, limit=limit)
        return self._curator.curate(records)

    async def recommend(
        self,
        *,
        since: datetime | None = None,
        limit: int = 10_000,
        base_model: str | None = None,
        persist: bool = True,
    ) -> tuple[FineTuneRecommendation, StoredDataset | None]:
        examples, statistics = await self.curate(since=since, limit=limit)
        model = base_model or self._base_model
        recommendation = self._curator.recommend(
            examples, statistics, base_model=model, method="lora"
        )
        payload = recommendation.as_dict()
        if self._cpu_hours is not None:
            payload["estimated_train_hours_cpu"] = self._cpu_hours

        stored: StoredDataset | None = None
        if persist and examples:
            digest = recommendation.dataset_digest or dataset_digest(examples)
            jsonl = DatasetCurator.export(examples, fmt="chatml")
            stored = StoredDataset(
                digest=digest,
                examples=tuple(examples),
                statistics=statistics,
                format="chatml",
                base_model=model,
                jsonl=jsonl,
            )
            await self._datasets.put(stored)
            # Rebuild recommendation so digest matches stored artefact.
            recommendation = FineTuneRecommendation(
                recommended=recommendation.recommended,
                reasons=recommendation.reasons,
                statistics=statistics,
                dataset_digest=digest,
                estimated_examples=recommendation.estimated_examples,
                estimated_tokens=recommendation.estimated_tokens,
                estimated_cost_usd=recommendation.estimated_cost_usd,
                base_model=model,
                method=recommendation.method,
            )
        return recommendation, stored

    async def request_approval(
        self,
        *,
        digest: str | None = None,
        since: datetime | None = None,
        base_model: str | None = None,
    ) -> tuple[ApprovalRequest, FineTuneRecommendation]:
        recommendation, stored = await self.recommend(
            since=since, base_model=base_model, persist=True
        )
        artefact = digest or recommendation.dataset_digest
        if not artefact:
            raise ValidationError("no curated examples to approve")
        if stored is None:
            stored = await self._datasets.get(artefact)
        if stored is None:
            raise NotFoundError(f"dataset {artefact!r} was not persisted")

        payload = recommendation.as_dict()
        if self._cpu_hours is not None:
            payload["estimated_train_hours_cpu"] = self._cpu_hours
        summary = (
            f"Fine-tune {recommendation.base_model or 'base'} on "
            f"{recommendation.estimated_examples} examples "
            f"({'recommended' if recommendation.recommended else 'not recommended'})"
        )
        approval = await self._approvals.request(
            kind=ApprovalKind.FINETUNE,
            summary=summary,
            artefact_digest=artefact,
            payload=payload,
        )
        return approval, recommendation


class FineTuneJobEngine:
    """Launch a trainer only after ``ApprovalService.require`` succeeds."""

    def __init__(
        self,
        *,
        approvals: ApprovalService,
        datasets: DatasetStore,
        models: ModelRegistry,
        trainer: FineTuneTrainer,
        jobs: JobStore | None = None,
        experiments: ExperimentTracker | None = None,
        model_id: str = "aura-ft",
        allow_cpu: bool = False,
        code_version: str = "",
        default_parameters: Mapping[str, Any] | None = None,
    ) -> None:
        self._approvals = approvals
        self._datasets = datasets
        self._models = models
        self._trainer = trainer
        self._jobs = jobs or InMemoryJobStore()
        self._experiments = experiments
        self._model_id = model_id
        self._allow_cpu = allow_cpu
        self._code_version = code_version
        self._default_parameters = dict(
            default_parameters
            or {"method": "lora", "rank": 16, "lr": 3e-4, "epochs": 3}
        )

    async def get(self, job_id: str) -> FineTuneJob:
        job = await self._jobs.get(job_id)
        if job is None:
            raise NotFoundError(f"unknown fine-tune job {job_id!r}")
        return job

    async def list(self, *, limit: int = 50) -> Sequence[FineTuneJob]:
        return await self._jobs.list(limit=limit)

    async def enqueue(
        self,
        *,
        approval_id: str,
        dataset_digest: str | None = None,
        base_model: str = "",
        version: str | None = None,
    ) -> FineTuneJob:
        """Create a queued job after verifying the approval binds to the dataset."""
        approval = await self._approvals._store.get(approval_id)
        if approval is None:
            raise NotFoundError(f"unknown approval {approval_id!r}")
        digest = dataset_digest or approval.artefact_digest
        await self._approvals.require(approval_id, expected_digest=digest)

        dataset = await self._datasets.get(digest)
        if dataset is None:
            raise NotFoundError(f"dataset {digest!r} is not stored")

        job_version = version or f"v{uuid.uuid4().hex[:8]}"
        job = FineTuneJob(
            id=f"ftj_{uuid.uuid4().hex[:12]}",
            approval_id=approval_id,
            dataset_digest=digest,
            base_model=base_model or dataset.base_model,
            status=FineTuneJobStatus.QUEUED,
            model_id=self._model_id,
            version=job_version,
            parameters=dict(self._default_parameters),
        )
        await self._jobs.put(job)
        logger.info(
            "fine-tune job queued",
            extra={"job": job.id, "digest": digest, "approval": approval_id},
        )
        return job

    async def run(self, job_id: str) -> FineTuneJob:
        """Execute a queued job: require approval again, train, register CANDIDATE."""
        job = await self.get(job_id)
        if job.status is not FineTuneJobStatus.QUEUED:
            raise ConflictError(
                f"job {job_id} is {job.status}, not queued",
                details={"status": str(job.status)},
            )

        await self._approvals.require(job.approval_id, expected_digest=job.dataset_digest)
        dataset = await self._datasets.get(job.dataset_digest)
        if dataset is None:
            raise NotFoundError(f"dataset {job.dataset_digest!r} is not stored")

        started = datetime.now(timezone.utc)
        job = replace(job, status=FineTuneJobStatus.RUNNING, started_at=started)
        await self._jobs.put(job)

        run_id: str | None = None
        if self._experiments is not None:
            experiment = await self._experiments.experiment(
                f"finetune-{job.model_id}",
                description="ADR-008 Loop B fine-tune",
                tags=("finetune", "lora"),
            )
            run = await self._experiments.start_run(
                experiment.id,
                parameters=dict(job.parameters),
                dataset_digest=job.dataset_digest,
                base_model=job.base_model,
                code_version=self._code_version,
            )
            run_id = run.id
            job = replace(job, run_id=run_id)
            await self._jobs.put(job)

        output_tag = f"{job.model_id}:{job.version}"

        try:
            spec = FineTuneJobSpec(
                job_id=job.id,
                dataset_digest=job.dataset_digest,
                dataset_jsonl=dataset.jsonl,
                base_model=job.base_model,
                output_tag=output_tag,
                method=str(job.parameters.get("method", "lora")),
                parameters=dict(job.parameters),
                allow_cpu=self._allow_cpu,
                **_spec_hyperparameters(job.parameters),
            )
            result = await self._trainer.train(spec)
            if result.weights_changed and not (
                result.artefact_uri.strip() and result.artefact_digest.strip()
            ):
                raise ValidationError(
                    "trainer reported changed weights without naming the artefact "
                    "(artefact_uri and artefact_digest are both required)"
                )
        except Exception as exc:
            return await self._finish_failed(job, run_id, f"{type(exc).__name__}: {exc}")

        if not result.weights_changed:
            return await self._finish_not_trained(job, run_id, result)

        notes = result.notes
        extras = [f"artefact_sha256={result.artefact_digest}"]
        if result.base_model_revision:
            extras.append(f"base_revision={result.base_model_revision}")
        notes = (notes + " | " if notes else "") + " ".join(extras)
        version = ModelVersion(
            model_id=job.model_id,
            version=job.version,
            provider_kind=result.provider_kind,
            base_model=job.base_model,
            stage=ModelStage.CANDIDATE,
            artefact_uri=result.artefact_uri,
            dataset_digest=job.dataset_digest,
            approval_id=job.approval_id,
            metrics=dict(result.metrics),
            notes=notes,
        )
        await self._models.register(version)

        if self._experiments is not None and run_id:
            await self._experiments.log_artefact(run_id, "model", result.artefact_uri)
            if result.metrics:
                await self._experiments.log_metrics(run_id, dict(result.metrics))
            await self._experiments.finish_run(run_id, status=RunStatus.COMPLETED)

        succeeded = replace(
            job,
            status=FineTuneJobStatus.SUCCEEDED,
            artefact_uri=result.artefact_uri,
            provider_kind=result.provider_kind,
            metrics=dict(result.metrics),
            finished_at=datetime.now(timezone.utc),
        )
        await self._jobs.put(succeeded)
        logger.info(
            "fine-tune job succeeded",
            extra={
                "job": job.id,
                "artefact": result.artefact_uri,
                "artefact_digest": result.artefact_digest,
            },
        )
        return succeeded

    async def _finish_failed(self, job: FineTuneJob, run_id: str | None, error: str
                             ) -> FineTuneJob:
        failed = replace(
            job,
            status=FineTuneJobStatus.FAILED,
            error=error,
            finished_at=datetime.now(timezone.utc),
        )
        await self._jobs.put(failed)
        if self._experiments is not None and run_id:
            await self._experiments.finish_run(run_id, status=RunStatus.FAILED, error=error)
        logger.warning("fine-tune job failed", extra={"job": job.id, "error": error})
        return failed

    async def _finish_not_trained(
        self, job: FineTuneJob, run_id: str | None, result: TrainResult
    ) -> FineTuneJob:
        """Terminal, honest, and deliberately not a success: nothing is registered."""
        reason = result.notes.strip() or "trainer reported that no weights were changed"
        outcome = replace(
            job,
            status=FineTuneJobStatus.NOT_TRAINED,
            error=reason,
            artefact_uri="",
            provider_kind=result.provider_kind,
            metrics=dict(result.metrics),
            finished_at=datetime.now(timezone.utc),
        )
        await self._jobs.put(outcome)
        if self._experiments is not None and run_id:
            if result.metrics:
                await self._experiments.log_metrics(run_id, dict(result.metrics))
            # The run did not produce a model: an experiment "completed" would read as one.
            await self._experiments.finish_run(
                run_id, status=RunStatus.FAILED, error=f"not trained: {reason}"
            )
        logger.warning(
            "fine-tune job finished without training", extra={"job": job.id, "reason": reason}
        )
        return outcome
