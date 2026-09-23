"""TensorFlow/Keras model adapter for AURA.

Provides a framework-neutral interface for TensorFlow model operations
while keeping tensorflow imports isolated to this module.
"""

from __future__ import annotations

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
class TFModelConfig:
    """Configuration for a TensorFlow model."""

    architecture: str
    model_id: str
    checkpoint_path: str | None = None
    mixed_precision: bool = False
    compile_model: bool = True


@dataclass(frozen=True, slots=True)
class TFInferenceResult:
    """Result from TensorFlow model inference."""

    outputs: Any
    latency_ms: float
    device: str
    model_id: str


@dataclass(frozen=True, slots=True)
class TFTrainingResult:
    """Result from TensorFlow training."""

    checkpoint_path: str
    metrics: Mapping[str, float]
    epochs_completed: int
    steps_completed: int
    duration_s: float
    device: str
    model_id: str
    dataset_digest: str = ""


@dataclass(frozen=True, slots=True)
class TFCheckpoint:
    """TensorFlow checkpoint metadata."""

    path: str
    model_id: str
    architecture: str
    step: int
    epoch: int
    metrics: Mapping[str, float]
    optimizer_state: bool
    dataset_digest: str = ""
    created_at: str = ""


class TensorFlowAdapter:
    """Adapter for TensorFlow/Keras model operations.

    All tensorflow imports are local to this class to keep the rest of
    the system framework-agnostic.
    """

    def __init__(self, config: TFModelConfig):
        self._config = config
        self._device_manager = get_device_manager()
        self._model: Any = None
        self._optimizer: Any = None
        self._loaded = False
        self._mixed_precision_policy = None

    def _get_tf(self):
        import tensorflow as tf
        return tf

    def _get_keras(self):
        import tensorflow.keras as keras
        return keras

    @property
    def device(self) -> str:
        return self._device_manager.get_tf_device()

    @property
    def device_info(self):
        return self._device_manager.device_info

    def load_model(self) -> None:
        """Load the model from checkpoint or architecture spec."""
        import tensorflow as tf

        if self._loaded:
            return

        tf_device = self.device
        logger.info(
            "Loading TensorFlow model",
            extra={"model_id": self._config.model_id, "device": tf_device},
        )

        # Set device
        with tf.device(tf_device):
            if self._config.checkpoint_path and os.path.exists(self._config.checkpoint_path):
                # Load from checkpoint
                self._model = tf.keras.models.load_model(self._config.checkpoint_path)
            else:
                # Build from architecture spec
                self._model = self._build_model_from_config()

            if self._config.compile_model:
                self._model.compile(
                    optimizer="adam",
                    loss="sparse_categorical_crossentropy",
                    metrics=["accuracy"],
                )

        self._loaded = True
        logger.info("TensorFlow model loaded successfully")

    def _build_model_from_config(self) -> Any:
        """Build model from architecture configuration."""
        import tensorflow as tf
        from tensorflow.keras import layers

        # Simple default model - in practice use ML specs
        model = tf.keras.Sequential([
            layers.Dense(256, activation="relu", input_shape=(128,)),
            layers.Dropout(0.1),
            layers.Dense(128, activation="relu"),
            layers.Dropout(0.1),
            layers.Dense(10, activation="softmax"),
        ])
        return model

    async def infer(self, inputs: Any, **kwargs) -> TFInferenceResult:
        """Run inference on the model."""
        import tensorflow as tf

        if not self._loaded:
            self.load_model()

        start = time.perf_counter()
        tf_device = self.device

        with tf.device(tf_device):
            # Convert inputs to tensor if needed
            if isinstance(inputs, dict):
                inputs = {k: tf.convert_to_tensor(v) for k, v in inputs.items()}
            elif not isinstance(inputs, tf.Tensor):
                inputs = tf.convert_to_tensor(inputs)

            outputs = self._model(inputs, training=False, **kwargs)

        latency = (time.perf_counter() - start) * 1000
        return TFInferenceResult(
            outputs=outputs,
            latency_ms=latency,
            device=tf_device,
            model_id=self._config.model_id,
        )

    def train_step(
        self,
        batch: Any,
        loss_fn: Any,
        optimizer: Any | None = None,
    ) -> Mapping[str, float]:
        """Execute a single training step using GradientTape."""
        import tensorflow as tf

        if not self._loaded:
            self.load_model()

        opt = optimizer or self._optimizer
        if opt is None:
            raise RuntimeError("No optimizer configured")

        tf_device = self.device

        with tf.device(tf_device):
            # Convert batch to tensors
            if isinstance(batch, dict):
                batch = {k: tf.convert_to_tensor(v) for k, v in batch.items()}
            elif isinstance(batch, (tuple, list)):
                batch = tuple(tf.convert_to_tensor(v) for v in batch)
            elif not isinstance(batch, tf.Tensor):
                batch = tf.convert_to_tensor(batch)

            with tf.GradientTape() as tape:
                if isinstance(batch, dict):
                    outputs = self._model(batch, training=True)
                    # Assume dict has 'labels' or 'target' key
                    labels = batch.get("labels", batch.get("target", None))
                    if labels is not None:
                        loss = loss_fn(labels, outputs)
                    else:
                        loss = loss_fn(outputs, batch)
                elif isinstance(batch, (tuple, list)) and len(batch) == 2:
                    # Tuple of (inputs, labels)
                    inputs, labels = batch
                    outputs = self._model(inputs, training=True)
                    loss = loss_fn(labels, outputs)
                else:
                    outputs = self._model(batch, training=True)
                    loss = loss_fn(outputs, batch)

            variables = self._model.trainable_variables
            gradients = tape.gradient(loss, variables)
            opt.apply_gradients(zip(gradients, variables))

        metrics = {"loss": float(loss.numpy())}
        return metrics

    def fit(
        self,
        dataset: Any,
        epochs: int = 1,
        validation_data: Any | None = None,
        callbacks: Sequence[Any] | None = None,
    ) -> TFTrainingResult:
        """Train the model using Keras fit()."""
        import tensorflow as tf

        if not self._loaded:
            self.load_model()

        tf_device = self.device

        with tf.device(tf_device):
            start = time.perf_counter()
            history = self._model.fit(
                dataset,
                epochs=epochs,
                validation_data=validation_data,
                callbacks=list(callbacks) if callbacks else None,
                verbose=1,
            )
            duration = time.perf_counter() - start

        # Extract final metrics
        metrics = {}
        if history.history:
            for key, values in history.history.items():
                metrics[f"final_{key}"] = float(values[-1]) if values else 0.0

        return TFTrainingResult(
            checkpoint_path="",  # Will be set by save_checkpoint
            metrics=metrics,
            epochs_completed=epochs,
            steps_completed=epochs * (len(dataset) if hasattr(dataset, "__len__") else 1),
            duration_s=duration,
            device=tf_device,
            model_id=self._config.model_id,
        )

    def save_checkpoint(
        self,
        path: str,
        *,
        epoch: int = 0,
        step: int = 0,
        metrics: Mapping[str, float] | None = None,
        optimizer: Any | None = None,
        dataset_digest: str = "",
    ) -> TFCheckpoint:
        """Save model checkpoint in Keras format."""
        import tensorflow as tf

        if not self._loaded:
            self.load_model()

        Path(path).parent.mkdir(parents=True, exist_ok=True)

        # Save in Keras format (.keras extension required)
        if not path.endswith(".keras"):
            path = path + ".keras"

        self._model.save(path)

        return TFCheckpoint(
            path=path,
            model_id=self._config.model_id,
            architecture=self._config.architecture,
            step=step,
            epoch=epoch,
            metrics=dict(metrics or {}),
            optimizer_state=True,  # Keras saves optimizer by default
            dataset_digest=dataset_digest,
        )

    def load_checkpoint(self, path: str) -> TFCheckpoint:
        """Load model checkpoint."""
        import tensorflow as tf

        if not os.path.exists(path):
            raise FileNotFoundError(f"Checkpoint not found: {path}")

        tf_device = self.device

        with tf.device(tf_device):
            self._model = tf.keras.models.load_model(path)

        self._loaded = True

        return TFCheckpoint(
            path=path,
            model_id=self._config.model_id,
            architecture=self._config.architecture,
            step=0,
            epoch=0,
            metrics={},
            optimizer_state=True,
            dataset_digest="",
        )

    def setup_optimizer(
        self,
        lr: float = 1e-3,
        weight_decay: float = 0.01,
        optimizer_class: str = "AdamW",
    ) -> Any:
        """Configure optimizer for training."""
        import tensorflow as tf

        if not self._loaded:
            self.load_model()

        optimizer_map = {
            "AdamW": lambda: tf.keras.optimizers.AdamW(
                learning_rate=lr, weight_decay=weight_decay
            ),
            "Adam": lambda: tf.keras.optimizers.Adam(learning_rate=lr),
            "SGD": lambda: tf.keras.optimizers.SGD(learning_rate=lr),
        }

        opt_factory = optimizer_map.get(optimizer_class, optimizer_map["AdamW"])
        self._optimizer = opt_factory()
        return self._optimizer

    def get_trainable_params(self) -> int:
        """Get number of trainable parameters."""
        import tensorflow as tf
        if not self._loaded:
            self.load_model()
        return sum(
            tf.keras.backend.count_params(w)
            for w in self._model.trainable_variables
        )

    def get_total_params(self) -> int:
        """Get total number of parameters."""
        if not self._loaded:
            self.load_model()
        return self._model.count_params()

    def enable_mixed_precision(self) -> None:
        """Enable mixed precision training."""
        import tensorflow as tf

        if self._mixed_precision_policy is None:
            policy = tf.keras.mixed_precision.Policy("mixed_float16")
            tf.keras.mixed_precision.set_global_policy(policy)
            self._mixed_precision_policy = policy
            logger.info("Enabled mixed precision (float16)")

    def set_train_mode(self, training: bool = True) -> None:
        """Set model to train or eval mode (no-op in Keras, kept for API compatibility)."""
        # Keras models don't have a global train/eval mode - it's per-call
        pass


def create_tensorflow_adapter(
    architecture: str,
    model_id: str,
    checkpoint_path: str | None = None,
    **kwargs,
) -> TensorFlowAdapter:
    """Factory function to create a TensorFlow adapter."""
    config = TFModelConfig(
        architecture=architecture,
        model_id=model_id,
        checkpoint_path=checkpoint_path,
        **kwargs,
    )
    return TensorFlowAdapter(config)