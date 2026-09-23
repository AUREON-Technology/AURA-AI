"""Unified checkpoint management for ML models.

Provides framework-agnostic checkpoint operations with metadata tracking.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from .pytorch_adapter import PyTorchAdapter, TorchCheckpoint
from .tensorflow_adapter import TensorFlowAdapter, TFCheckpoint

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CheckpointMetadata:
    """Framework-agnostic checkpoint metadata."""

    model_id: str
    framework: str  # "pytorch" | "tensorflow"
    architecture: str
    version: str
    path: str
    epoch: int
    step: int
    metrics: Mapping[str, float]
    dataset_digest: str
    optimizer_state: bool
    created_at: str
    code_version: str = ""
    size_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CheckpointMetadata":
        return cls(**data)

    @classmethod
    def from_torch(cls, tc: TorchCheckpoint, code_version: str = "") -> "CheckpointMetadata":
        size = os.path.getsize(tc.path) if os.path.exists(tc.path) else 0
        return cls(
            model_id=tc.model_id,
            framework="pytorch",
            architecture=tc.architecture,
            version=f"epoch_{tc.epoch}_step_{tc.step}",
            path=tc.path,
            epoch=tc.epoch,
            step=tc.step,
            metrics=dict(tc.metrics),
            dataset_digest=tc.dataset_digest,
            optimizer_state=tc.optimizer_state,
            created_at=tc.created_at or datetime.now(timezone.utc).isoformat(),
            code_version=code_version,
            size_bytes=size,
        )

    @classmethod
    def from_tf(cls, tc: TFCheckpoint, code_version: str = "") -> "CheckpointMetadata":
        size = os.path.getsize(tc.path) if os.path.exists(tc.path) else 0
        return cls(
            model_id=tc.model_id,
            framework="tensorflow",
            architecture=tc.architecture,
            version=f"epoch_{tc.epoch}_step_{tc.step}",
            path=tc.path,
            epoch=tc.epoch,
            step=tc.step,
            metrics=dict(tc.metrics),
            dataset_digest=tc.dataset_digest,
            optimizer_state=tc.optimizer_state,
            created_at=tc.created_at or datetime.now(timezone.utc).isoformat(),
            code_version=code_version,
            size_bytes=size,
        )


class CheckpointManager:
    """Manages model checkpoints across frameworks.

    Handles saving, loading, and tracking checkpoints with full metadata
    for reproducibility and model registry integration.
    """

    def __init__(
        self,
        base_dir: str | Path = "/tmp/aura-checkpoints",
        metadata_file: str = "checkpoint_metadata.jsonl",
    ):
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_file = self._base_dir / metadata_file
        self._metadata_cache: dict[str, CheckpointMetadata] = {}
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load existing metadata from file."""
        if not self._metadata_file.exists():
            return
        try:
            with open(self._metadata_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    meta = CheckpointMetadata.from_dict(json.loads(line))
                    self._metadata_cache[meta.path] = meta
        except Exception as e:
            logger.warning(f"Failed to load checkpoint metadata: {e}")

    def _save_metadata(self, metadata: CheckpointMetadata) -> None:
        """Append metadata to file."""
        try:
            with open(self._metadata_file, "a") as f:
                f.write(metadata.to_json() + "\n")
            self._metadata_cache[metadata.path] = metadata
        except Exception as e:
            logger.error(f"Failed to save checkpoint metadata: {e}")

    def save_pytorch(
        self,
        adapter: PyTorchAdapter,
        *,
        version: str,
        epoch: int = 0,
        step: int = 0,
        metrics: Mapping[str, float] | None = None,
        optimizer: Any | None = None,
        dataset_digest: str = "",
        code_version: str = "",
    ) -> CheckpointMetadata:
        """Save PyTorch model checkpoint."""
        filename = f"{adapter._config.model_id}_v{version}_epoch{epoch}_step{step}.pt"
        path = self._base_dir / filename

        tc = adapter.save_checkpoint(
            str(path),
            epoch=epoch,
            step=step,
            metrics=metrics,
            optimizer=optimizer,
            dataset_digest=dataset_digest,
        )

        metadata = CheckpointMetadata.from_torch(tc, code_version=code_version)
        self._save_metadata(metadata)
        logger.info(f"Saved PyTorch checkpoint: {path}")
        return metadata

    def save_tensorflow(
        self,
        adapter: TensorFlowAdapter,
        *,
        version: str,
        epoch: int = 0,
        step: int = 0,
        metrics: Mapping[str, float] | None = None,
        dataset_digest: str = "",
        code_version: str = "",
    ) -> CheckpointMetadata:
        """Save TensorFlow model checkpoint."""
        filename = f"{adapter._config.model_id}_v{version}_epoch{epoch}_step{step}"
        path = self._base_dir / filename

        tc = adapter.save_checkpoint(
            str(path),
            epoch=epoch,
            step=step,
            metrics=metrics,
            dataset_digest=dataset_digest,
        )

        metadata = CheckpointMetadata.from_tf(tc, code_version=code_version)
        self._save_metadata(metadata)
        logger.info(f"Saved TensorFlow checkpoint: {path}")
        return metadata

    def load_pytorch(
        self,
        adapter: PyTorchAdapter,
        path: str,
        optimizer: Any | None = None,
    ) -> TorchCheckpoint:
        """Load PyTorch model checkpoint."""
        return adapter.load_checkpoint(path, optimizer=optimizer)

    def load_tensorflow(
        self,
        adapter: TensorFlowAdapter,
        path: str,
    ) -> TFCheckpoint:
        """Load TensorFlow model checkpoint."""
        return adapter.load_checkpoint(path)

    def get_metadata(self, path: str) -> Optional[CheckpointMetadata]:
        """Get checkpoint metadata by path."""
        return self._metadata_cache.get(path)

    def list_checkpoints(
        self,
        model_id: str | None = None,
        framework: str | None = None,
    ) -> list[CheckpointMetadata]:
        """List all checkpoints, optionally filtered."""
        results = list(self._metadata_cache.values())
        if model_id:
            results = [m for m in results if m.model_id == model_id]
        if framework:
            results = [m for m in results if m.framework == framework]
        results.sort(key=lambda m: m.created_at, reverse=True)
        return results

    def delete_checkpoint(self, path: str) -> bool:
        """Delete a checkpoint and its metadata."""
        try:
            if os.path.exists(path):
                os.remove(path)
            if path in self._metadata_cache:
                del self._metadata_cache[path]
                # Rewrite metadata file
                with open(self._metadata_file, "w") as f:
                    for meta in self._metadata_cache.values():
                        f.write(meta.to_json() + "\n")
            return True
        except Exception as e:
            logger.error(f"Failed to delete checkpoint {path}: {e}")
            return False

    def get_latest_checkpoint(
        self,
        model_id: str,
        framework: str | None = None,
    ) -> Optional[CheckpointMetadata]:
        """Get the most recent checkpoint for a model."""
        checkpoints = self.list_checkpoints(model_id=model_id, framework=framework)
        return checkpoints[0] if checkpoints else None


def get_checkpoint_manager() -> CheckpointManager:
    """Get a default checkpoint manager instance."""
    return CheckpointManager()