"""ML Service layer for AURA.

Integrates PyTorch and TensorFlow adapters with the existing AURA
architecture (AIKernel, ProviderRouter, RAG, embeddings, reranking).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .checkpoint import CheckpointManager, CheckpointMetadata
from .device import DeviceInfo, DeviceManager, DeviceType, get_device_info
from .pytorch_adapter import (
    PyTorchAdapter,
    TorchInferenceResult,
    TorchModelConfig,
    create_pytorch_adapter,
)
from .tensorflow_adapter import (
    TensorFlowAdapter,
    TFInferenceResult,
    TFModelConfig,
    create_tensorflow_adapter,
)
from .training import MLTrainingService, TrainingConfig, TrainingJobResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MLInferenceRequest:
    """Framework-agnostic inference request."""

    model_id: str
    inputs: Any
    framework: str = "auto"  # "auto" | "pytorch" | "tensorflow"
    return_logits: bool = False


@dataclass(frozen=True, slots=True)
class MLInferenceResult:
    """Framework-agnostic inference result."""

    model_id: str
    framework: str
    outputs: Any
    latency_ms: float
    device: str
    device_type: str


@dataclass(frozen=True, slots=True)
class MLModelInfo:
    """Information about a loaded ML model."""

    model_id: str
    framework: str
    architecture: str
    device: str
    trainable_params: int
    total_params: int
    loaded: bool = False


class MLService:
    """Unified ML service for AURA.

    Provides a single interface for:
    - Model inference (PyTorch & TensorFlow)
    - Model training (via MLTrainingService)
    - Checkpoint management
    - Device management
    - Integration with existing AURA components
    """

    def __init__(
        self,
        checkpoint_manager: CheckpointManager | None = None,
        training_service: MLTrainingService | None = None,
        device_manager: DeviceManager | None = None,
    ):
        self._checkpoint_manager = checkpoint_manager or CheckpointManager()
        self._training_service = training_service or MLTrainingService(
            checkpoint_manager=self._checkpoint_manager
        )
        self._device_manager = device_manager or DeviceManager()
        self._pytorch_models: dict[str, PyTorchAdapter] = {}
        self._tensorflow_models: dict[str, TensorFlowAdapter] = {}

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_manager.device_info

    @property
    def checkpoint_manager(self) -> CheckpointManager:
        return self._checkpoint_manager

    @property
    def training_service(self) -> MLTrainingService:
        return self._training_service

    def load_pytorch_model(
        self,
        model_id: str,
        architecture: str,
        checkpoint_path: str | None = None,
        **kwargs,
    ) -> PyTorchAdapter:
        """Load a PyTorch model into the service."""
        if model_id in self._pytorch_models:
            logger.warning(f"Model {model_id} already loaded, returning existing instance")
            return self._pytorch_models[model_id]

        adapter = create_pytorch_adapter(
            architecture=architecture,
            model_id=model_id,
            checkpoint_path=checkpoint_path,
            **kwargs,
        )
        adapter.load_model()
        self._pytorch_models[model_id] = adapter
        logger.info(f"Loaded PyTorch model: {model_id}")
        return adapter

    def load_tensorflow_model(
        self,
        model_id: str,
        architecture: str,
        checkpoint_path: str | None = None,
        **kwargs,
    ) -> TensorFlowAdapter:
        """Load a TensorFlow model into the service."""
        if model_id in self._tensorflow_models:
            logger.warning(f"Model {model_id} already loaded, returning existing instance")
            return self._tensorflow_models[model_id]

        adapter = create_tensorflow_adapter(
            architecture=architecture,
            model_id=model_id,
            checkpoint_path=checkpoint_path,
            **kwargs,
        )
        adapter.load_model()
        self._tensorflow_models[model_id] = adapter
        logger.info(f"Loaded TensorFlow model: {model_id}")
        return adapter

    def get_pytorch_model(self, model_id: str) -> PyTorchAdapter | None:
        """Get a loaded PyTorch model."""
        return self._pytorch_models.get(model_id)

    def get_tensorflow_model(self, model_id: str) -> TensorFlowAdapter | None:
        """Get a loaded TensorFlow model."""
        return self._tensorflow_models.get(model_id)

    async def infer(self, request: MLInferenceRequest) -> MLInferenceResult:
        """Run inference using the appropriate framework."""
        framework = request.framework

        # Auto-detect framework if not specified
        if framework == "auto":
            if request.model_id in self._pytorch_models:
                framework = "pytorch"
            elif request.model_id in self._tensorflow_models:
                framework = "tensorflow"
            else:
                # Default to PyTorch for local models
                framework = "pytorch"

        if framework == "pytorch":
            adapter = self._pytorch_models.get(request.model_id)
            if adapter is None:
                raise ValueError(f"PyTorch model {request.model_id} not loaded")
            result: TorchInferenceResult = await adapter.infer(
                request.inputs, return_logits=request.return_logits
            )
            return MLInferenceResult(
                model_id=request.model_id,
                framework="pytorch",
                outputs=result.outputs,
                latency_ms=result.latency_ms,
                device=result.device,
                device_type=self.device_info.type.value,
            )

        elif framework == "tensorflow":
            adapter = self._tensorflow_models.get(request.model_id)
            if adapter is None:
                raise ValueError(f"TensorFlow model {request.model_id} not loaded")
            result: TFInferenceResult = await adapter.infer(request.inputs)
            return MLInferenceResult(
                model_id=request.model_id,
                framework="tensorflow",
                outputs=result.outputs,
                latency_ms=result.latency_ms,
                device=result.device,
                device_type=self.device_info.type.value,
            )

        else:
            raise ValueError(f"Unknown framework: {framework}")

    def get_model_info(self, model_id: str) -> MLModelInfo | None:
        """Get information about a loaded model."""
        if model_id in self._pytorch_models:
            adapter = self._pytorch_models[model_id]
            return MLModelInfo(
                model_id=model_id,
                framework="pytorch",
                architecture=adapter._config.architecture,
                device=str(adapter.device),
                trainable_params=adapter.get_trainable_params(),
                total_params=adapter.get_total_params(),
                loaded=True,
            )
        if model_id in self._tensorflow_models:
            adapter = self._tensorflow_models[model_id]
            return MLModelInfo(
                model_id=model_id,
                framework="tensorflow",
                architecture=adapter._config.architecture,
                device=adapter.device,
                trainable_params=adapter.get_trainable_params(),
                total_params=adapter.get_total_params(),
                loaded=True,
            )
        return None

    def list_loaded_models(self) -> list[MLModelInfo]:
        """List all loaded models."""
        models = []
        for model_id in self._pytorch_models:
            info = self.get_model_info(model_id)
            if info:
                models.append(info)
        for model_id in self._tensorflow_models:
            info = self.get_model_info(model_id)
            if info:
                models.append(info)
        return models

    def unload_model(self, model_id: str) -> bool:
        """Unload a model from memory."""
        unloaded = False
        if model_id in self._pytorch_models:
            del self._pytorch_models[model_id]
            unloaded = True
        if model_id in self._tensorflow_models:
            del self._tensorflow_models[model_id]
            unloaded = True
        if unloaded:
            logger.info(f"Unloaded model: {model_id}")
        return unloaded

    async def train(
        self,
        config: TrainingConfig,
        experiment_tracker: Any | None = None,
        model_registry: Any | None = None,
    ) -> TrainingJobResult:
        """Start a training job."""
        if experiment_tracker:
            self._training_service._experiment_tracker = experiment_tracker
        if model_registry:
            self._training_service._model_registry = model_registry

        return await self._training_service.train(config)

    def get_training_jobs(self) -> Mapping[str, TrainingConfig]:
        """Get active training jobs."""
        return self._training_service.get_active_jobs()

    def cancel_training(self, job_id: str) -> bool:
        """Cancel a training job."""
        return self._training_service.cancel_job(job_id)

    def save_checkpoint(
        self,
        model_id: str,
        framework: str,
        version: str,
        **kwargs,
    ) -> CheckpointMetadata:
        """Save a checkpoint for a loaded model."""
        if framework == "pytorch":
            adapter = self._pytorch_models.get(model_id)
            if not adapter:
                raise ValueError(f"PyTorch model {model_id} not loaded")
            return self._checkpoint_manager.save_pytorch(adapter, version=version, **kwargs)

        elif framework == "tensorflow":
            adapter = self._tensorflow_models.get(model_id)
            if not adapter:
                raise ValueError(f"TensorFlow model {model_id} not loaded")
            return self._checkpoint_manager.save_tensorflow(adapter, version=version, **kwargs)

        else:
            raise ValueError(f"Unknown framework: {framework}")

    def load_checkpoint(
        self,
        model_id: str,
        framework: str,
        path: str,
    ) -> CheckpointMetadata:
        """Load a checkpoint into a model."""
        if framework == "pytorch":
            adapter = self._pytorch_models.get(model_id)
            if not adapter:
                adapter = create_pytorch_adapter(
                    architecture="",  # Will be loaded from checkpoint
                    model_id=model_id,
                )
                self._pytorch_models[model_id] = adapter
            self._checkpoint_manager.load_pytorch(adapter, path)
            return self._checkpoint_manager.get_metadata(path) or CheckpointMetadata(
                model_id=model_id,
                framework="pytorch",
                architecture="",
                version="",
                path=path,
                epoch=0,
                step=0,
                metrics={},
                dataset_digest="",
                optimizer_state=False,
                created_at="",
            )

        elif framework == "tensorflow":
            adapter = self._tensorflow_models.get(model_id)
            if not adapter:
                adapter = create_tensorflow_adapter(
                    architecture="",
                    model_id=model_id,
                )
                self._tensorflow_models[model_id] = adapter
            self._checkpoint_manager.load_tensorflow(adapter, path)
            return self._checkpoint_manager.get_metadata(path) or CheckpointMetadata(
                model_id=model_id,
                framework="tensorflow",
                architecture="",
                version="",
                path=path,
                epoch=0,
                step=0,
                metrics={},
                dataset_digest="",
                optimizer_state=False,
                created_at="",
            )

        else:
            raise ValueError(f"Unknown framework: {framework}")

    def get_device_info(self) -> DeviceInfo:
        """Get current device information."""
        return self._device_manager.device_info

    def set_device_preference(self, device: str) -> DeviceInfo:
        """Set device preference and reinitialize."""
        import os
        os.environ["AURA_DEVICE"] = device
        DeviceManager.reset()
        self._device_manager = DeviceManager()
        return self._device_manager.device_info


def create_ml_service(
    checkpoint_dir: str = "/tmp/aura-checkpoints",
    **kwargs,
) -> MLService:
    """Factory function to create an ML service."""
    checkpoint_manager = CheckpointManager(base_dir=checkpoint_dir)
    training_service = MLTrainingService(checkpoint_manager=checkpoint_manager)
    return MLService(
        checkpoint_manager=checkpoint_manager,
        training_service=training_service,
        **kwargs,
    )