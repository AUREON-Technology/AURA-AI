"""Generic PyTorch/TensorFlow scaffolding. NOT language-model fine-tuning.

Read this before wiring anything here into Loop B (ADR-008):

* ``PyTorchAdapter._build_model_from_config`` ignores the base model entirely and builds a
  **randomly initialised** 6-layer, 2-class *classification* transformer from
  ``aura_core.ml.TransformerSpec`` (vocab 32000, d_model 512). Nothing in this package calls
  ``from_pretrained`` on a model, so no base LLM is ever loaded; ``apply_lora`` wraps that
  random classifier, and ``MLTrainingService.train_pytorch`` trains it with a cross-entropy
  loss on character codes when no tokenizer resolves. Its checkpoints are not LLM weights and
  not LoRA adapters for any served model.
* ``MLTrainingService`` registers what it trains straight into the ``ModelRegistry`` as a
  CANDIDATE when handed a registry -- bypassing the job engine's approval binding. Do not pass
  it the container's registry.

Status (2026-09-25, GAP-017): no production code path imports this package. Its only former
caller, ``adapters/local_lora_trainer.py``, now does real PEFT LoRA with transformers directly;
the only remaining importer is ``tests/unit/test_ml_integration.py``. Kept, not deleted, pending
a decision on whether generic torch/TF scaffolding has a use; it must not be presented as
AURA fine-tuning. The real trainer is ``aura_core.adapters.local_lora_trainer.LocalLoraTrainer``.
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