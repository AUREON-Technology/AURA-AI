"""ML Framework adapters for AURA.

This package provides framework-specific implementations behind
framework-neutral interfaces.
"""

from .checkpoint import CheckpointManager, CheckpointMetadata
from .device import DeviceInfo, DeviceManager, DeviceType, get_device_info, get_device_manager
from .pytorch_adapter import (
    PyTorchAdapter,
    TorchCheckpoint,
    TorchInferenceResult,
    TorchModelConfig,
    TorchTrainingResult,
    create_pytorch_adapter,
)
from .service import MLInferenceRequest, MLInferenceResult, MLModelInfo, MLService, create_ml_service
from .tensorflow_adapter import (
    TensorFlowAdapter,
    TFCheckpoint,
    TFInferenceResult,
    TFModelConfig,
    TFTrainingResult,
    create_tensorflow_adapter,
)
from .training import MLTrainingService, TrainingConfig, TrainingJobResult, create_training_config

__all__ = [
    # Device
    "DeviceManager",
    "DeviceInfo",
    "DeviceType",
    "get_device_manager",
    "get_device_info",
    # PyTorch
    "PyTorchAdapter",
    "TorchModelConfig",
    "TorchInferenceResult",
    "TorchTrainingResult",
    "TorchCheckpoint",
    "create_pytorch_adapter",
    # TensorFlow
    "TensorFlowAdapter",
    "TFModelConfig",
    "TFInferenceResult",
    "TFTrainingResult",
    "TFCheckpoint",
    "create_tensorflow_adapter",
    # Checkpoint
    "CheckpointManager",
    "CheckpointMetadata",
    # Training
    "MLTrainingService",
    "TrainingConfig",
    "TrainingJobResult",
    "create_training_config",
    # Service
    "MLService",
    "MLInferenceRequest",
    "MLInferenceResult",
    "MLModelInfo",
    "create_ml_service",
]