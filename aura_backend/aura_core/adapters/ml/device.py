"""Device management for ML frameworks.

Centralises hardware detection and device selection so the rest of the system
never imports torch/tensorflow directly just to find out what device to use.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DeviceType(str, Enum):
    CPU = "cpu"
    CUDA = "cuda"
    MPS = "mps"


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Information about the selected compute device."""

    type: DeviceType
    name: str
    index: int = 0
    memory_total_gb: float | None = None
    memory_free_gb: float | None = None
    framework: str = ""

    @property
    def is_gpu(self) -> bool:
        return self.type in (DeviceType.CUDA, DeviceType.MPS)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "name": self.name,
            "index": self.index,
            "memory_total_gb": self.memory_total_gb,
            "memory_free_gb": self.memory_free_gb,
            "framework": self.framework,
            "is_gpu": self.is_gpu,
        }


class DeviceManager:
    """Manages device selection for ML workloads.

    Detects available hardware once at startup and provides a consistent
    device interface for both PyTorch and TensorFlow workloads.

    The device can be explicitly configured via environment variables:
    - AURA_DEVICE: "cpu", "cuda", or "auto" (default: "auto")
    - AURA_CUDA_DEVICE: specific CUDA device index (default: 0)
    """

    _instance: Optional["DeviceManager"] = None
    _device_info: Optional[DeviceInfo] = None

    def __new__(cls) -> "DeviceManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._device_info is not None:
            return
        self._device_info = self._detect_device()

    @classmethod
    def reset(cls) -> None:
        """Reset singleton for testing."""
        cls._instance = None
        cls._device_info = None

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    def _detect_device(self) -> DeviceInfo:
        """Detect and select the best available device."""
        configured = os.environ.get("AURA_DEVICE", "auto").lower()

        # Try CUDA first if explicitly requested or auto
        if configured in ("cuda", "auto"):
            cuda_info = self._detect_cuda()
            if cuda_info is not None:
                logger.info(
                    "Selected CUDA device",
                    extra={"device": cuda_info.as_dict()},
                )
                return cuda_info

        # Try MPS (Apple Silicon) if explicitly requested or auto
        if configured in ("mps", "auto"):
            mps_info = self._detect_mps()
            if mps_info is not None:
                logger.info(
                    "Selected MPS device",
                    extra={"device": mps_info.as_dict()},
                )
                return mps_info

        # Fall back to CPU
        cpu_info = DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            framework="pytorch+tensorflow",
        )
        logger.info("Selected CPU device", extra={"device": cpu_info.as_dict()})
        return cpu_info

    def _detect_cuda(self) -> DeviceInfo | None:
        """Detect CUDA device via PyTorch."""
        try:
            import torch
        except ImportError:
            return None

        if not torch.cuda.is_available():
            return None

        device_idx = int(os.environ.get("AURA_CUDA_DEVICE", "0"))
        if device_idx >= torch.cuda.device_count():
            logger.warning(
                f"Requested CUDA device {device_idx} but only {torch.cuda.device_count()} available; using device 0"
            )
            device_idx = 0

        props = torch.cuda.get_device_properties(device_idx)
        total_gb = props.total_memory / (1024**3)
        free_gb = (props.total_memory - torch.cuda.memory_allocated(device_idx)) / (1024**3)

        return DeviceInfo(
            type=DeviceType.CUDA,
            name=props.name,
            index=device_idx,
            memory_total_gb=round(total_gb, 2),
            memory_free_gb=round(free_gb, 2),
            framework="pytorch",
        )

    def _detect_mps(self) -> DeviceInfo | None:
        """Detect Apple MPS device via PyTorch."""
        try:
            import torch
        except ImportError:
            return None

        if not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
            return None

        return DeviceInfo(
            type=DeviceType.MPS,
            name="Apple MPS",
            framework="pytorch",
        )

    def get_torch_device(self) -> Any:
        """Get a PyTorch device object."""
        import torch

        if self._device_info.type == DeviceType.CUDA:
            return torch.device(f"cuda:{self._device_info.index}")
        if self._device_info.type == DeviceType.MPS:
            return torch.device("mps")
        return torch.device("cpu")

    def get_tf_device(self) -> str:
        """Get a TensorFlow device string."""
        if self._device_info.type == DeviceType.CUDA:
            return f"/GPU:{self._device_info.index}"
        return "/CPU:0"


def get_device_manager() -> DeviceManager:
    """Get the global device manager instance."""
    return DeviceManager()


def get_device_info() -> DeviceInfo:
    """Get the current device info."""
    return get_device_manager().device_info