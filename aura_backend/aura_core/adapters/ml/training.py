"""ML Training service for AURA.

Orchestrates training workflows across PyTorch and TensorFlow frameworks,
integrating with the existing AURA learning subsystem.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .checkpoint import CheckpointManager, CheckpointMetadata
from .pytorch_adapter import (
    PyTorchAdapter,
    TorchInferenceResult,
    TorchModelConfig,
    TorchTrainingResult,
    create_pytorch_adapter,
)
from .tensorflow_adapter import (
    TensorFlowAdapter,
    TFInferenceResult,
    TFModelConfig,
    TFTrainingResult,
    create_tensorflow_adapter,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    """Configuration for a training job."""

    job_id: str
    model_id: str
    framework: str  # "pytorch" | "tensorflow"
    architecture: str
    base_model: str
    dataset_path: str
    epochs: int = 1
    batch_size: int = 16
    learning_rate: float = 2e-4
    weight_decay: float = 0.01
    max_steps: int | None = None
    lora_rank: int | None = None
    lora_alpha: int | None = None
    lora_dropout: float | None = None
    lora_target_modules: Sequence[str] | None = None
    mixed_precision: bool = False
    gradient_accumulation_steps: int = 1
    checkpoint_dir: str = "/tmp/aura-checkpoints"
    experiment_name: str = ""
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class TrainingJobResult:
    """Result of a training job."""

    job_id: str
    status: str  # "completed" | "failed" | "cancelled"
    checkpoint: CheckpointMetadata | None
    metrics: Mapping[str, float]
    error: str | None = None
    duration_s: float = 0.0
    epochs_completed: int = 0
    steps_completed: int = 0


class MLTrainingService:
    """High-level training service that coordinates framework-specific training."""

    def __init__(
        self,
        checkpoint_manager: CheckpointManager | None = None,
        experiment_tracker: Any | None = None,
        model_registry: Any | None = None,
    ):
        self._checkpoint_manager = checkpoint_manager or CheckpointManager()
        self._experiment_tracker = experiment_tracker
        self._model_registry = model_registry
        self._active_jobs: dict[str, TrainingConfig] = {}

    def _create_pytorch_adapter(self, config: TrainingConfig) -> PyTorchAdapter:
        """Create PyTorch adapter from training config."""
        adapter = create_pytorch_adapter(
            architecture=config.architecture,
            model_id=config.model_id,
            checkpoint_path=None,  # Will load base model
        )
        return adapter

    def _create_tensorflow_adapter(self, config: TrainingConfig) -> TensorFlowAdapter:
        """Create TensorFlow adapter from training config."""
        adapter = create_tensorflow_adapter(
            architecture=config.architecture,
            model_id=config.model_id,
            checkpoint_path=None,
        )
        return adapter

    def _load_dataset(self, dataset_path: str) -> list[dict[str, Any]]:
        """Load training dataset from JSONL file."""
        import json

        data = []
        with open(dataset_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return data

    def _compute_dataset_digest(self, dataset_path: str) -> str:
        """Compute SHA256 digest of dataset for reproducibility."""
        hasher = hashlib.sha256()
        with open(dataset_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()[:16]

    async def train_pytorch(self, config: TrainingConfig) -> TrainingJobResult:
        """Run PyTorch training job."""
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, Dataset

        job_id = config.job_id
        start_time = time.perf_counter()
        self._active_jobs[job_id] = config

        try:
            logger.info(f"Starting PyTorch training job {job_id}")

            # Create adapter
            adapter = self._create_pytorch_adapter(config)
            adapter.load_model()

            # Apply LoRA if configured
            if config.lora_rank is not None:
                adapter.apply_lora(
                    rank=config.lora_rank,
                    alpha=config.lora_alpha or 16,
                    dropout=config.lora_dropout or 0.05,
                    target_modules=config.lora_target_modules,
                )

            # Setup optimizer
            adapter.setup_optimizer(
                lr=config.learning_rate,
                weight_decay=config.weight_decay,
            )

            # Load dataset
            dataset_data = self._load_dataset(config.dataset_path)
            if not dataset_data:
                raise ValueError("Dataset is empty")

            # Create PyTorch dataset
            class TextDataset(Dataset):
                def __init__(self, data: list[dict], tokenizer: Any = None, max_length: int = 512):
                    self.data = data
                    self.tokenizer = tokenizer
                    self.max_length = max_length

                def __len__(self):
                    return len(self.data)

                def __getitem__(self, idx):
                    item = self.data[idx]
                    text = item.get("text", item.get("prompt", str(item)))

                    if self.tokenizer:
                        encoding = self.tokenizer(
                            text,
                            truncation=True,
                            max_length=self.max_length,
                            padding="max_length",
                            return_tensors="pt",
                        )
                        return {
                            "input_ids": encoding["input_ids"].squeeze(0),
                            "attention_mask": encoding["attention_mask"].squeeze(0),
                            "labels": encoding["input_ids"].squeeze(0),
                        }
                    else:
                        # Fallback: simple tensor
                        return {
                            "input_ids": torch.tensor([ord(c) for c in text[:self.max_length]]),
                            "labels": torch.tensor([ord(c) for c in text[:self.max_length]]),
                        }

            # Use tokenizer if available
            tokenizer = None
            try:
                from transformers import AutoTokenizer
                tokenizer = AutoTokenizer.from_pretrained(config.base_model)
                tokenizer.pad_token = tokenizer.eos_token
            except Exception:
                pass

            dataset = TextDataset(dataset_data, tokenizer=tokenizer)
            dataloader = DataLoader(dataset, batch_size=config.batch_size, shuffle=True)

            # Loss function
            loss_fn = nn.CrossEntropyLoss()

            # Training loop
            epoch_metrics = []
            steps = 0

            for epoch in range(config.epochs):
                epoch_loss = 0.0
                epoch_steps = 0

                for batch in dataloader:
                    metrics = adapter.train_step(batch, loss_fn)
                    epoch_loss += metrics.get("loss", 0.0)
                    epoch_steps += 1
                    steps += 1

                    # Log metrics to experiment tracker
                    if self._experiment_tracker and config.run_id:
                        await self._experiment_tracker.log_metrics(
                            config.run_id, {f"train/{k}": v for k, v in metrics.items()}, step=steps
                        )

                    if config.max_steps and steps >= config.max_steps:
                        break

                avg_loss = epoch_loss / epoch_steps if epoch_steps > 0 else 0.0
                epoch_metrics.append({"epoch": epoch, "loss": avg_loss, "steps": epoch_steps})

                logger.info(f"Epoch {epoch+1}/{config.epochs} - Loss: {avg_loss:.4f}")

                if config.max_steps and steps >= config.max_steps:
                    break

            # Save checkpoint
            dataset_digest = self._compute_dataset_digest(config.dataset_path)
            checkpoint_meta = self._checkpoint_manager.save_pytorch(
                adapter,
                version=f"epoch{config.epochs}",
                epoch=config.epochs,
                step=steps,
                metrics={"train_loss": epoch_metrics[-1]["loss"] if epoch_metrics else 0.0},
                dataset_digest=dataset_digest,
            )

            duration = time.perf_counter() - start_time

            # Register with model registry if available
            if self._model_registry:
                from aura_core.mlops.model_registry import ModelVersion, ModelStage
                version = ModelVersion(
                    model_id=config.model_id,
                    version=checkpoint_meta.version,
                    provider_kind="pytorch",
                    base_model=config.base_model,
                    stage=ModelStage.CANDIDATE,
                    artefact_uri=checkpoint_meta.path,
                    dataset_digest=dataset_digest,
                    metrics={"train_loss": epoch_metrics[-1]["loss"] if epoch_metrics else 0.0},
                )
                await self._model_registry.register(version)

            return TrainingJobResult(
                job_id=job_id,
                status="completed",
                checkpoint=checkpoint_meta,
                metrics={"train_loss": epoch_metrics[-1]["loss"] if epoch_metrics else 0.0, "epochs": config.epochs},
                duration_s=duration,
                epochs_completed=config.epochs,
                steps_completed=steps,
            )

        except Exception as e:
            logger.exception(f"Training job {job_id} failed")
            return TrainingJobResult(
                job_id=job_id,
                status="failed",
                checkpoint=None,
                metrics={},
                error=str(e),
                duration_s=time.perf_counter() - start_time,
            )
        finally:
            self._active_jobs.pop(job_id, None)

    async def train_tensorflow(self, config: TrainingConfig) -> TrainingJobResult:
        """Run TensorFlow training job."""
        import tensorflow as tf

        job_id = config.job_id
        start_time = time.perf_counter()
        self._active_jobs[job_id] = config

        try:
            logger.info(f"Starting TensorFlow training job {job_id}")

            # Create adapter
            adapter = self._create_tensorflow_adapter(config)
            adapter.load_model()

            if config.mixed_precision:
                adapter.enable_mixed_precision()

            # Setup optimizer
            adapter.setup_optimizer(
                lr=config.learning_rate,
                weight_decay=config.weight_decay,
            )

            # Load dataset
            dataset_data = self._load_dataset(config.dataset_path)
            if not dataset_data:
                raise ValueError("Dataset is empty")

            # Create TensorFlow dataset
            texts = [item.get("text", item.get("prompt", str(item))) for item in dataset_data]
            labels = [hash(text) % 10 for text in texts]  # Simple label generation

            tf_dataset = tf.data.Dataset.from_tensor_slices((texts, labels))
            tf_dataset = tf_dataset.shuffle(len(dataset_data)).batch(config.batch_size)

            # Train using Keras fit
            result = adapter.fit(tf_dataset, epochs=config.epochs)

            # Save checkpoint
            dataset_digest = self._compute_dataset_digest(config.dataset_path)
            checkpoint_meta = self._checkpoint_manager.save_tensorflow(
                adapter,
                version=f"epoch{config.epochs}",
                epoch=config.epochs,
                step=result.steps_completed,
                metrics=dict(result.metrics),
                dataset_digest=dataset_digest,
            )

            duration = time.perf_counter() - start_time

            # Register with model registry if available
            if self._model_registry:
                from aura_core.mlops.model_registry import ModelVersion, ModelStage
                version = ModelVersion(
                    model_id=config.model_id,
                    version=checkpoint_meta.version,
                    provider_kind="tensorflow",
                    base_model=config.base_model,
                    stage=ModelStage.CANDIDATE,
                    artefact_uri=checkpoint_meta.path,
                    dataset_digest=dataset_digest,
                    metrics=dict(result.metrics),
                )
                await self._model_registry.register(version)

            return TrainingJobResult(
                job_id=job_id,
                status="completed",
                checkpoint=checkpoint_meta,
                metrics=dict(result.metrics),
                duration_s=duration,
                epochs_completed=config.epochs,
                steps_completed=result.steps_completed,
            )

        except Exception as e:
            logger.exception(f"Training job {job_id} failed")
            return TrainingJobResult(
                job_id=job_id,
                status="failed",
                checkpoint=None,
                metrics={},
                error=str(e),
                duration_s=time.perf_counter() - start_time,
            )
        finally:
            self._active_jobs.pop(job_id, None)

    async def train(self, config: TrainingConfig) -> TrainingJobResult:
        """Run training job with the appropriate framework."""
        if config.framework == "pytorch":
            return await self.train_pytorch(config)
        elif config.framework == "tensorflow":
            return await self.train_tensorflow(config)
        else:
            raise ValueError(f"Unknown framework: {config.framework}")

    def get_active_jobs(self) -> Mapping[str, TrainingConfig]:
        """Get currently active training jobs."""
        return dict(self._active_jobs)

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a training job (cooperative cancellation)."""
        if job_id in self._active_jobs:
            # In a real implementation, you'd signal the training loop
            # For now, just remove from active
            self._active_jobs.pop(job_id, None)
            return True
        return False


def create_training_config(
    model_id: str,
    framework: str,
    architecture: str,
    base_model: str,
    dataset_path: str,
    **kwargs,
) -> TrainingConfig:
    """Factory function to create a training config."""
    job_id = kwargs.get("job_id") or f"train_{uuid.uuid4().hex[:8]}"
    return TrainingConfig(
        job_id=job_id,
        model_id=model_id,
        framework=framework,
        architecture=architecture,
        base_model=base_model,
        dataset_path=dataset_path,
        **{k: v for k, v in kwargs.items() if k != "job_id"},
    )