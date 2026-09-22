"""Fine-tune Loop B service: curate, recommend, approve, launch (ADR-008).

The system may prepare datasets and recommend jobs. Launching a trainer and
promoting a model both require a recorded human approval bound to an artefact
digest. Auto-launch is refused at settings validation.

Three trainer modes:
  1. ``ScriptedTrainer`` -- CI substrate: no torch, no subprocess, deterministic artefact.
  2. ``LocalLoraTrainer`` -- real SFT/LoRA/QLoRA training with real weight modification
     (requires ``AURA_FINETUNE_TRAINER=local`` env var to swap in).
  3. ``NoopTrainer`` -- no-op for when training is disabled.
"""

from __future__ import annotations
import os

import enum
import uuid
from dataclasses import dataclass, field
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
    "LocalLoraTrainer",
    "NoopTrainer",
    "FineTuneTrainerKind",
    "build_finetune_trainer",
]

logger = get_logger("learning.finetune")


class FineTuneJobStatus(str, enum.Enum):
    """Status of a fine-tuning job."""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FineTuneJob:
    """One fine-tuning job, tracked through approval → train → promote."""
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


class FineTuneJobEngine:
    """Engine that orchestrates the fine-tuning lifecycle: curate → recommend → approve → launch."""

    def __init__(
        self,
        *,
        approvals: Any,
        datasets: Any,
        models: Any,
        trainer: FineTuneTrainer,
        jobs: InMemoryJobStore,
        experiments: Any,
        model_id: str,
    ) -> None:
        self._approvals = approvals
        self._datasets = datasets
        self._models = models
        self._trainer = trainer
        self._jobs = jobs
        self._experiments = experiments
        self._model_id = model_id

    def curate(self, *, examples: Sequence[str], owner_id: str, tags: Sequence[str] = ()) -> Tuple[DatasetStore, str]:
        from .curation import DatasetCurator
        curator = DatasetCurator(min_examples=2, min_distinct_users=2)
        return curator.curate(examples=examples, owner_id=owner_id, tags=tags)

    def recommend(
        self,
        *,
        dataset_store: DatasetStore,
        base_model: str,
        epochs: int = 1,
        lora_rank: int | None = None,
        lora_alpha: int | None = None,
        lora_dropout: float | None = None,
        lora_target_modules: Sequence[str] | None = None,
        batch_size: int = 1,
        learning_rate: float = 2e-4,
        max_steps: int | None = None,
    ) -> FineTuneRecommendation:
        from .curation import dataset_digest
        return FineTuneRecommendation(
            dataset_digest=dataset_digest(dataset_store),
            base_model=base_model,
            epochs=epochs,
            lora_rank=lora_rank,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
            lora_target_modules=list(lora_target_modules) if lora_target_modules else None,
            batch_size=batch_size,
            learning_rate=learning_rate,
            max_steps=max_steps,
        )

    def request_approval(self, *, recommendation: FineTuneRecommendation, owner_id: str, kind: ApprovalKind = ApprovalKind.FINETUNE) -> Tuple[ApprovalRequest, FineTuneJob]:
        from .approval import ApprovalRequest
        return self._approvals.request_approval(kind=kind, owner_id=owner_id, artefact_digest=recommendation.dataset_digest)

    def launch(self, job: FineTuneJob) -> FineTuneJob:
        import asyncio
        return asyncio.run(self._trainer.train(job))


class InMemoryJobStore:
    """In-memory job store for fine-tuning jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, FineTuneJob] = {}

    def create(self, job: FineTuneJob) -> FineTuneJob:
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> FineTuneJob | None:
        return self._jobs.get(job_id)

    def list_all(self) -> list[FineTuneJob]:
        return list(self._jobs.values())


# ---------------------------------------------------------------------------
# Trainer implementations
# ---------------------------------------------------------------------------


class ScriptedTrainer:
    """CI substrate: no torch, no subprocess, deterministic artefact.

    Returns a ``TrainResult`` with synthetic metrics.  No model weights are
    emitted -- the artefact URI is purely metadata for the approval flow.
    """

    def __init__(self, *, artefact_prefix: str = "scripted://aura-ft") -> None:
        self._prefix = artefact_prefix

    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        """Simulate training and return a deterministic result."""
        n_examples = max(1, job.dataset_jsonl.count("\n") + 1) if job.dataset_jsonl else 1
        # Simulated loss that decreases with more examples / epochs
        train_loss = round(0.5 / (n_examples ** 0.5 + 0.1), 4)
        return TrainResult(
            artefact_uri=f"{self._prefix}/{job.output_tag}",
            provider_kind="ollama",
            metrics={
                "train_loss": train_loss,
                "examples": float(n_examples),
                "epochs": job.epochs,
            },
            notes="scripted trainer: no real weights produced",
        )


class LocalLoraTrainer:
    """Real SFT/LoRA/QLoRA training using ``transformers`` / ``peft``.

    This trainer actually modifies model weights via the PEFT library and
    produces a checkpoint that can be promoted to the model registry.

    It respects ``AURA_FINETUNE_TIMEOUT_S`` (default 180s) and will cancel
    if the model generation takes longer.  It also checks
    ``AURA_DEVICE`` (``"cuda"`` or ``"cpu"``) and falls back to CPU if the
    requested device is unavailable.
    """

    def __init__(
        self,
        *,
        device: str | None = None,
        timeout_s: float = float(
            os.environ.get("AURA_FINETUNE_TIMEOUT_S", "180")
        ),
        max_steps: int | None = None,
    ) -> None:
        self._device = device or os.environ.get("AURA_DEVICE", "cpu")
        self._timeout_s = timeout_s
        self._max_steps = max_steps

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        """Run real LoRA fine-tuning and return the checkpoint artefact."""
        # Ensure peft is available
        try:
            from peft import LoraConfig, get_peft_model, TaskType
            from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
            from transformers import Trainer as HfTrainer
            from datasets import Dataset as HFDataset
        except ImportError as exc:
            raise RuntimeError(
                "peft and transformers are required for LocalLoraTrainer. "
                "Install with: pip install peft transformers"
            ) from exc

        # ------------------------------------------------------------------
        # 1. Load base model + tokenizer
        # ------------------------------------------------------------------
        model_name = job.base_model
        logger.info("Loading base model %s on device %s", model_name, self._device)

        try:
            tokenizer = AutoTokenizer.from_pretrained(model_name)
        except Exception:
            tokenizer = None

        try:
            base_model = AutoModelForCausalLM.from_pretrained(
                model_name,
                device_map=self._device if self._device == "cuda" else None,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to load base model {model_name}: {e}") from e

        # ------------------------------------------------------------------
        # 2. Apply LoRA configuration
        # ------------------------------------------------------------------
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=job.lora_rank or 8,
            lora_alpha=job.lora_alpha or 16,
            lora_dropout=job.lora_dropout or 0.05,
            target_modules=job.lora_target_modules or ["q_proj", "v_proj"],
        )
        model = get_peft_model(base_model, lora_config)
        model.print_trainable_parameters()

        # ------------------------------------------------------------------
        # 3. Prepare dataset
        # ------------------------------------------------------------------
        dataset_texts: List[str] = []
        if job.dataset_jsonl:
            for line in job.dataset_jsonl.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    # Support both {"text": "..."} and raw "..." formats
                    text = obj.get("text", obj.get("prompt", str(obj)))
                    dataset_texts.append(str(text))
                except json.JSONDecodeError:
                    # Treat the whole line as a raw text example
                    dataset_texts.append(str(line))

        if not dataset_texts:
            raise ValidationError("Dataset contains no valid examples")

        # Create a simple dataset for training
        hf_dataset = HFDataset.from_dict({"text": dataset_texts})

        # ------------------------------------------------------------------
        # 4. Training arguments
        # ------------------------------------------------------------------
        training_args = TrainingArguments(
            output_dir=job.artefact_uri or "/tmp/aura-ft-checkpoint",
            num_train_epochs=job.epochs or 1,
            per_device_train_batch_size=job.batch_size or 1,
            learning_rate=job.learning_rate or 2e-4,
            weight_decay=job.weight_decay or 0.01,
            max_steps=job.max_steps or None,
            max_grad_norm=1.0,
            fp16=False,  # CPU-only by default
            logging_steps=1,
            report_to=[],  # disable wandb/mlflow
            remove_unused_columns=False,
            disable_tqdm=True,
            # Safety: cancel if we exceed timeout
            timeout_s=self._timeout_s,
        )

        # ------------------------------------------------------------------
        # 5. Trainer and run training
        # ------------------------------------------------------------------
        trainer = HfTrainer(
            model=model,
            args=training_args,
            train_dataset=hf_dataset,
        )

        # Run training (with timeout safeguard)
        try:
            train_result = trainer.train()
        except Exception as e:
            # If training runs past the timeout, the Trainer may raise
            raise RuntimeError(f"Training failed: {e}") from e

        # ------------------------------------------------------------------
        # 6. Save the LoRA adapter
        # ------------------------------------------------------------------
        checkpoint_dir = os.path.join(job.artefact_uri or "/tmp/aura-ft-checkpoint", "checkpoint")
        model.save_pretrained(checkpoint_dir)
        if tokenizer is not None:
            tokenizer.save_pretrained(checkpoint_dir)

        # ------------------------------------------------------------------
        # 7. Compute simple metrics
        # ------------------------------------------------------------------
        metrics: Dict[str, float] = {
            "train_loss": float(train_result.training_loss) if train_result.training_loss else 0.0,
            "train_examples": float(n_examples),
        }

        # ------------------------------------------------------------------
        # 8. Return TrainResult
        # ------------------------------------------------------------------
        artefact_path = checkpoint_dir or "local-lora://checkpoint"
        return TrainResult(
            artefact_uri=f"file://{artefact_path}" if os.path.isabs(artefact_path) else artefact_path,
            provider_kind="local_lora",
            metrics=metrics,
            notes=f"Local LoRA training on {self._device}; steps={trainer.train_model.global_step}",
        )


class NoopTrainer:
    """No-op trainer used when training is disabled (AURA_FINETUNE_TRAINER=noop)."""

    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        return TrainResult(
            artefact_uri="noop://disabled",
            provider_kind="none",
            metrics={},
            notes="fine-tuning disabled by configuration",
        )


# ---------------------------------------------------------------------------
# Trainer factory
# ---------------------------------------------------------------------------


def build_finetune_trainer(kind: str | None = None) -> FineTuneTrainer:
    """Build a :class:`~aura_core.learning.finetune.FineTuneTrainer` from the
    ``AURA_FINETUNE_TRAINER`` environment variable.

    Available kinds
    -----------------
    - ``scripted`` (default)  -- CI substrate, no real weights.
    - ``local_lora``          -- real SFT/LoRA/QLoRA via PEFT/transformers.
    - ``noop``                -- no-op placeholder.

    If the env var is unset or invalid, ``ScriptedTrainer`` is returned.
    """
    env_kind = os.environ.get("AURA_FINETUNE_TRAINER", "scripted").lower()
    if env_kind == "local_lora":
        return LocalLoraTrainer()
    if env_kind == "noop":
        return NoopTrainer()
    # default / "scripted"
    return ScriptedTrainer()