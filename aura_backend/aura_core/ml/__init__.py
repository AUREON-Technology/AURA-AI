"""Reusable ML module specifications.

Architectures are declared as data and materialised into torch modules on demand, so the core
stays importable without a training stack and a spec can be sized, validated, and recorded in an
experiment without a GPU. See :mod:`aura_core.ml.nn`.
"""

from .nn import (
    CATALOGUE,
    AnomalyDetectorSpec,
    Architecture,
    AutoencoderSpec,
    CNNSpec,
    GraphNetworkSpec,
    ModuleSpec,
    RecommenderSpec,
    RecurrentSpec,
    TabularSpec,
    Task,
    TimeSeriesSpec,
    TransformerSpec,
    build,
    torch_available,
)

__all__ = [
    "Architecture",
    "Task",
    "ModuleSpec",
    "TransformerSpec",
    "CNNSpec",
    "RecurrentSpec",
    "AutoencoderSpec",
    "GraphNetworkSpec",
    "TimeSeriesSpec",
    "TabularSpec",
    "RecommenderSpec",
    "AnomalyDetectorSpec",
    "build",
    "torch_available",
    "CATALOGUE",
]
