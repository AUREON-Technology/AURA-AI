"""PyTorch model adapter for AURA.

Provides a framework-neutral interface for PyTorch model operations
while keeping torch imports isolated to this module.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..ml.device import get_device_manager

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TorchModelConfig:
    """Configuration for a PyTorch model."""

    architecture: str
    model_id: str
    checkpoint_path: str | None = None
    precision: str = "fp32"  # fp32, fp16, bf16
    compile: bool = False
    device_map: str | None = None


@dataclass(frozen=True, slots=True)
class TorchInferenceResult:
    """Result from PyTorch model inference."""

    outputs: Any
    latency_ms: float
    device: str
    model_id: str


@dataclass(frozen=True, slots=True)
class TorchTrainingResult:
    """Result from PyTorch training."""

    checkpoint_path: str
    metrics: Mapping[str, float]
    epochs_completed: int
    steps_completed: int
    duration_s: float
    device: str
    model_id: str
    dataset_digest: str = ""


@dataclass(frozen=True, slots=True)
class TorchCheckpoint:
    """PyTorch checkpoint metadata."""

    path: str
    model_id: str
    architecture: str
    step: int
    epoch: int
    metrics: Mapping[str, float]
    optimizer_state: bool
    dataset_digest: str = ""
    created_at: str = ""


class PyTorchAdapter:
    """Adapter for PyTorch model operations.

    All torch imports are local to this class to keep the rest of
    the system framework-agnostic.
    """

    def __init__(self, config: TorchModelConfig):
        self._config = config
        self._device_manager = get_device_manager()
        self._model: Any = None
        self._optimizer: Any = None
        self._tokenizer: Any = None
        self._loaded = False

    def _get_torch(self):
        import torch
        return torch

    def _get_nn(self):
        import torch.nn as nn
        return nn

    def _get_optim(self):
        import torch.optim as optim
        return optim

    @property
    def device(self):
        return self._device_manager.get_torch_device()

    @property
    def device_info(self):
        return self._device_manager.device_info

    def load_model(self, tokenizer: Any | None = None) -> None:
        """Load the model from checkpoint or architecture spec."""
        import torch

        if self._loaded:
            return

        torch_device = self.device
        logger.info(
            "Loading PyTorch model",
            extra={"model_id": self._config.model_id, "device": str(torch_device)},
        )

        if self._config.checkpoint_path and os.path.exists(self._config.checkpoint_path):
            # Load from checkpoint
            checkpoint = torch.load(
                self._config.checkpoint_path,
                map_location=torch_device,
                weights_only=False,
            )
            self._model = self._build_model_from_config()
            self._model.load_state_dict(checkpoint.get("model", checkpoint))
            if "optimizer" in checkpoint and self._optimizer is not None:
                self._optimizer.load_state_dict(checkpoint["optimizer"])
        else:
            # Build from architecture spec
            self._model = self._build_model_from_config()

        self._model.to(torch_device)
        self._model.eval()

        if self._config.compile and hasattr(torch, "compile"):
            self._model = torch.compile(self._model)

        if tokenizer is not None:
            self._tokenizer = tokenizer

        self._loaded = True
        logger.info("PyTorch model loaded successfully")

    def _build_model_from_config(self) -> Any:
        """Build a RANDOMLY INITIALISED 2-class classification transformer.

        This never loads ``config.base_model`` or any pretrained weights: whatever it trains is a
        toy classifier, not a language model and not a fine-tune of one (GAP-017). See the
        package docstring.
        """
        # Import the ML spec builder
        from aura_core.ml import build, TransformerSpec, Architecture, Task

        # For now, create a simple default transformer if no spec provided
        spec = TransformerSpec(
            architecture=Architecture.TRANSFORMER,
            task=Task.CLASSIFICATION,
            vocab_size=32000,
            d_model=512,
            n_heads=8,
            n_layers=6,
            d_ff=2048,
            max_positions=512,
            n_classes=2,
        )
        return build(spec)

    async def infer(
        self,
        inputs: Any,
        *,
        return_logits: bool = False,
    ) -> TorchInferenceResult:
        """Run inference on the model."""
        import torch

        if not self._loaded:
            self.load_model()

        start = time.perf_counter()
        torch_device = self.device

        # Move inputs to device
        if isinstance(inputs, dict):
            inputs = {k: v.to(torch_device) if hasattr(v, "to") else v for k, v in inputs.items()}
        elif hasattr(inputs, "to"):
            inputs = inputs.to(torch_device)

        with torch.no_grad():
            if isinstance(inputs, dict):
                outputs = self._model(**inputs)
            else:
                outputs = self._model(inputs)

        if return_logits and hasattr(outputs, "logits"):
            result = outputs.logits
        elif hasattr(outputs, "last_hidden_state"):
            result = outputs.last_hidden_state
        else:
            result = outputs

        latency = (time.perf_counter() - start) * 1000
        return TorchInferenceResult(
            outputs=result,
            latency_ms=latency,
            device=str(torch_device),
            model_id=self._config.model_id,
        )

    def train_step(
        self,
        batch: Any,
        loss_fn: Any,
        optimizer: Any | None = None,
    ) -> Mapping[str, float]:
        """Execute a single training step."""
        import torch

        if not self._loaded:
            self.load_model()

        self._model.train()
        opt = optimizer or self._optimizer

        if opt is None:
            raise RuntimeError("No optimizer configured")

        # Move batch to device
        torch_device = self.device
        if isinstance(batch, dict):
            batch = {k: v.to(torch_device) if hasattr(v, "to") else v for k, v in batch.items()}
        elif isinstance(batch, (tuple, list)):
            batch = tuple(v.to(torch_device) if hasattr(v, "to") else v for v in batch)
        elif hasattr(batch, "to"):
            batch = batch.to(torch_device)

        opt.zero_grad()
        if isinstance(batch, dict):
            outputs = self._model(**batch)
            loss = loss_fn(outputs, batch)
        else:
            outputs = self._model(batch)
            loss = loss_fn(outputs, batch)

        if isinstance(loss, dict):
            total_loss = sum(loss.values())
        else:
            total_loss = loss

        total_loss.backward()
        opt.step()

        metrics = {"loss": float(total_loss.detach().cpu())}
        if isinstance(loss, dict):
            metrics.update({k: float(v.detach().cpu()) for k, v in loss.items()})
        return metrics

    def save_checkpoint(
        self,
        path: str,
        *,
        epoch: int = 0,
        step: int = 0,
        metrics: Mapping[str, float] | None = None,
        optimizer: Any | None = None,
        dataset_digest: str = "",
    ) -> TorchCheckpoint:
        """Save model checkpoint."""
        import torch

        if not self._loaded:
            self.load_model()

        Path(path).parent.mkdir(parents=True, exist_ok=True)

        checkpoint = {
            "model": self._model.state_dict(),
            "architecture": self._config.architecture,
            "model_id": self._config.model_id,
            "epoch": epoch,
            "step": step,
            "metrics": dict(metrics or {}),
            "dataset_digest": dataset_digest,
        }

        if optimizer is not None or self._optimizer is not None:
            checkpoint["optimizer"] = (optimizer or self._optimizer).state_dict()

        torch.save(checkpoint, path)

        return TorchCheckpoint(
            path=path,
            model_id=self._config.model_id,
            architecture=self._config.architecture,
            step=step,
            epoch=epoch,
            metrics=dict(metrics or {}),
            optimizer_state="optimizer" in checkpoint,
            dataset_digest=dataset_digest,
        )

    def load_checkpoint(self, path: str, optimizer: Any | None = None) -> TorchCheckpoint:
        """Load model checkpoint."""
        import torch

        if not os.path.exists(path):
            raise FileNotFoundError(f"Checkpoint not found: {path}")

        torch_device = self.device
        checkpoint = torch.load(path, map_location=torch_device, weights_only=False)

        if not self._loaded:
            self._model = self._build_model_from_config()
            self._model.to(torch_device)

        self._model.load_state_dict(checkpoint.get("model", checkpoint))

        if optimizer is not None and "optimizer" in checkpoint:
            optimizer.load_state_dict(checkpoint["optimizer"])
        elif self._optimizer is not None and "optimizer" in checkpoint:
            self._optimizer.load_state_dict(checkpoint["optimizer"])

        self._loaded = True
        self._model.eval()

        return TorchCheckpoint(
            path=path,
            model_id=checkpoint.get("model_id", self._config.model_id),
            architecture=checkpoint.get("architecture", self._config.architecture),
            step=checkpoint.get("step", 0),
            epoch=checkpoint.get("epoch", 0),
            metrics=checkpoint.get("metrics", {}),
            optimizer_state="optimizer" in checkpoint,
            dataset_digest=checkpoint.get("dataset_digest", ""),
        )

    def setup_optimizer(
        self,
        lr: float = 1e-3,
        weight_decay: float = 0.01,
        optimizer_class: str = "AdamW",
    ) -> Any:
        """Configure optimizer for training."""
        import torch.optim as optim

        if not self._loaded:
            self.load_model()

        optimizer_map = {
            "AdamW": optim.AdamW,
            "Adam": optim.Adam,
            "SGD": optim.SGD,
        }

        opt_cls = optimizer_map.get(optimizer_class, optim.AdamW)
        self._optimizer = opt_cls(
            self._model.parameters(),
            lr=lr,
            weight_decay=weight_decay,
        )
        return self._optimizer

    def get_trainable_params(self) -> int:
        """Get number of trainable parameters."""
        if not self._loaded:
            self.load_model()
        return sum(p.numel() for p in self._model.parameters() if p.requires_grad)

    def get_total_params(self) -> int:
        """Get total number of parameters."""
        if not self._loaded:
            self.load_model()
        return sum(p.numel() for p in self._model.parameters())

    def apply_lora(
        self,
        rank: int = 8,
        alpha: int = 16,
        dropout: float = 0.05,
        target_modules: Sequence[str] | None = None,
    ) -> Any:
        """Apply LoRA adapters to the model using PEFT."""
        try:
            from peft import LoraConfig, get_peft_model, TaskType
        except ImportError as exc:
            raise RuntimeError(
                "PEFT is required for LoRA. Install with: pip install peft"
            ) from exc

        if not self._loaded:
            self.load_model()

        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=rank,
            lora_alpha=alpha,
            lora_dropout=dropout,
            target_modules=target_modules or ["q_proj", "v_proj"],
        )

        self._model = get_peft_model(self._model, lora_config)
        self._model.print_trainable_parameters()
        return self._model

    def set_train_mode(self, training: bool = True) -> None:
        """Set model to train or eval mode."""
        if not self._loaded:
            self.load_model()
        if training:
            self._model.train()
        else:
            self._model.eval()


def create_pytorch_adapter(
    architecture: str,
    model_id: str,
    checkpoint_path: str | None = None,
    **kwargs,
) -> PyTorchAdapter:
    """Factory function to create a PyTorch adapter."""
    config = TorchModelConfig(
        architecture=architecture,
        model_id=model_id,
        checkpoint_path=checkpoint_path,
        **kwargs,
    )
    return PyTorchAdapter(config)