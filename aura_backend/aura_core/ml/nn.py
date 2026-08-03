"""Reusable neural-network module specifications (Phase 8).

Architectures declared as **data**, not as always-loaded PyTorch modules. A spec describes shape,
hyperparameters, and applicability; :func:`build` materialises a real ``torch.nn.Module`` from it,
and only then is torch imported.

Why specs rather than concrete classes:

* ``aura_core`` stays importable with zero third-party packages -- the property that lets the
  whole cognitive core be unit-tested on bare CPython. A module file with ``import torch`` at the
  top would end that.
* Loading a dozen architectures into a VPS process would consume memory for models that are not
  in use. The brief asks for these to exist as reusable modules, not to run simultaneously.
* A spec can be validated, serialised into an experiment record, diffed between runs, and
  parameter-counted **without a GPU or a training environment**, which is what makes the model
  registry's lineage tracking useful.

Covered: Transformer, CNN, RNN/LSTM/GRU, autoencoder, graph neural network, time-series
forecaster, classifier, regressor, recommender, and anomaly detector.
"""

from __future__ import annotations

import enum
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from ..domain.errors import ConfigurationError, ValidationError

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
]


class Architecture(str, enum.Enum):
    TRANSFORMER = "transformer"
    CNN = "cnn"
    RNN = "rnn"
    LSTM = "lstm"
    GRU = "gru"
    AUTOENCODER = "autoencoder"
    GNN = "gnn"
    TIME_SERIES = "time_series"
    MLP = "mlp"
    RECOMMENDER = "recommender"
    ANOMALY = "anomaly"

    def __str__(self) -> str:
        return self.value


class Task(str, enum.Enum):
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    SEQUENCE = "sequence"
    RECONSTRUCTION = "reconstruction"
    RANKING = "ranking"
    FORECASTING = "forecasting"
    ANOMALY_DETECTION = "anomaly_detection"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """Base specification.

    ``parameter_count`` is an estimate computed from the shape alone -- no torch required. It is
    the number an operator needs before deciding whether a model fits on the hardware, and
    having it available without a training environment is the point.
    """

    architecture: Architecture
    task: Task
    name: str = ""
    dropout: float = 0.1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # NOTE: subclasses call ``ModuleSpec.__post_init__(self)`` explicitly rather than using
    # ``super()``. With ``@dataclass(slots=True)`` the decorator returns a *new* class object,
    # so the zero-argument ``super()``'s implicit ``__class__`` cell still refers to the
    # original and raises TypeError at runtime.
    def __post_init__(self) -> None:
        if not 0.0 <= self.dropout < 1.0:
            raise ValidationError("dropout must be within [0, 1)")

    @property
    def parameter_count(self) -> int:  # pragma: no cover - overridden
        return 0

    @property
    def estimated_fp32_mb(self) -> float:
        """Weight memory only -- optimiser state and activations are typically 3-4x this."""
        return round(self.parameter_count * 4 / (1024**2), 2)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["architecture"] = str(self.architecture)
        payload["task"] = str(self.task)
        payload["parameter_count"] = self.parameter_count
        payload["estimated_fp32_mb"] = self.estimated_fp32_mb
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True)


@dataclass(frozen=True, slots=True)
class TransformerSpec(ModuleSpec):
    """Encoder stack. The workhorse for text classification and sequence labelling on top of
    embeddings the platform already produces."""

    vocab_size: int = 32_000
    d_model: int = 512
    n_heads: int = 8
    n_layers: int = 6
    d_ff: int = 2048
    max_positions: int = 512
    n_classes: int = 2

    def __post_init__(self) -> None:
        ModuleSpec.__post_init__(self)
        if self.d_model % self.n_heads:
            raise ValidationError(
                f"d_model ({self.d_model}) must divide evenly by n_heads ({self.n_heads})"
            )

    @property
    def parameter_count(self) -> int:
        embeddings = self.vocab_size * self.d_model + self.max_positions * self.d_model
        # Per layer: 4 projection matrices for attention, 2 for the feed-forward block.
        attention = 4 * self.d_model * self.d_model
        feedforward = 2 * self.d_model * self.d_ff
        norms = 4 * self.d_model
        head = self.d_model * self.n_classes
        return embeddings + self.n_layers * (attention + feedforward + norms) + head


@dataclass(frozen=True, slots=True)
class CNNSpec(ModuleSpec):
    """1D or 2D convolutional stack.

    1D is the useful case here: convolutions over embedding sequences are a cheap, strong
    baseline for intent classification, and they train on CPU in seconds.
    """

    in_channels: int = 1
    channels: tuple[int, ...] = (32, 64, 128)
    kernel_size: int = 3
    dimensions: int = 1
    n_classes: int = 2
    input_length: int = 128

    def __post_init__(self) -> None:
        ModuleSpec.__post_init__(self)
        if self.dimensions not in (1, 2):
            raise ValidationError("CNN dimensions must be 1 or 2")
        if not self.channels:
            raise ValidationError("at least one channel stage is required")

    @property
    def parameter_count(self) -> int:
        kernel_elements = self.kernel_size ** self.dimensions
        total = 0
        previous = self.in_channels
        for channel in self.channels:
            total += previous * channel * kernel_elements + channel
            previous = channel
        pooled = max(1, self.input_length // (2 ** len(self.channels)))
        total += previous * pooled * self.n_classes
        return total


@dataclass(frozen=True, slots=True)
class RecurrentSpec(ModuleSpec):
    """RNN, LSTM, or GRU. ``architecture`` selects the cell."""

    input_size: int = 128
    hidden_size: int = 256
    n_layers: int = 2
    bidirectional: bool = False
    n_classes: int = 2

    @property
    def _gate_multiplier(self) -> int:
        return {
            Architecture.LSTM: 4,   # input, forget, cell, output
            Architecture.GRU: 3,    # reset, update, candidate
            Architecture.RNN: 1,
        }.get(self.architecture, 1)

    @property
    def parameter_count(self) -> int:
        directions = 2 if self.bidirectional else 1
        gates = self._gate_multiplier
        total = 0
        input_size = self.input_size
        for _ in range(self.n_layers):
            per_direction = gates * (
                input_size * self.hidden_size + self.hidden_size * self.hidden_size
                + 2 * self.hidden_size
            )
            total += per_direction * directions
            input_size = self.hidden_size * directions
        return total + self.hidden_size * directions * self.n_classes


@dataclass(frozen=True, slots=True)
class AutoencoderSpec(ModuleSpec):
    """Symmetric encoder/decoder. Used for anomaly detection via reconstruction error and for
    dimensionality reduction over embeddings."""

    input_dim: int = 384
    hidden_dims: tuple[int, ...] = (256, 128)
    latent_dim: int = 32
    variational: bool = False

    @property
    def parameter_count(self) -> int:
        dims = [self.input_dim, *self.hidden_dims, self.latent_dim]
        encoder = sum(dims[i] * dims[i + 1] + dims[i + 1] for i in range(len(dims) - 1))
        # A VAE's encoder emits mean and log-variance, doubling the final projection.
        if self.variational:
            encoder += dims[-2] * self.latent_dim + self.latent_dim
        return encoder * 2  # decoder mirrors the encoder


@dataclass(frozen=True, slots=True)
class GraphNetworkSpec(ModuleSpec):
    """Message-passing network over the knowledge graph.

    The natural application in this platform: link prediction and entity classification over the
    graph the knowledge engine already builds.
    """

    node_features: int = 128
    hidden_dim: int = 128
    n_layers: int = 3
    n_classes: int = 2
    conv_type: str = "gcn"  # gcn | gat | sage
    heads: int = 1

    def __post_init__(self) -> None:
        ModuleSpec.__post_init__(self)
        if self.conv_type not in {"gcn", "gat", "sage"}:
            raise ValidationError(f"unsupported graph convolution {self.conv_type!r}")

    @property
    def parameter_count(self) -> int:
        total = 0
        in_dim = self.node_features
        for _ in range(self.n_layers):
            # GraphSAGE concatenates self and neighbour representations, doubling the input.
            multiplier = 2 if self.conv_type == "sage" else 1
            per_head = in_dim * multiplier * self.hidden_dim + self.hidden_dim
            total += per_head * (self.heads if self.conv_type == "gat" else 1)
            in_dim = self.hidden_dim * (self.heads if self.conv_type == "gat" else 1)
        return total + in_dim * self.n_classes


@dataclass(frozen=True, slots=True)
class TimeSeriesSpec(ModuleSpec):
    """Forecasting over the user's own series: expenses, sleep, hydration, task completion."""

    input_features: int = 1
    context_length: int = 48
    horizon: int = 12
    hidden_dim: int = 128
    n_layers: int = 2
    backbone: str = "lstm"  # lstm | tcn | transformer

    _BACKBONES = ("lstm", "tcn", "transformer")

    def __post_init__(self) -> None:
        ModuleSpec.__post_init__(self)
        if self.backbone not in self._BACKBONES:
            # Without this, `parameter_count` falls through to the transformer branch, so a
            # typo'd backbone silently trains and sizes as a different architecture entirely.
            raise ValidationError(
                f"backbone must be one of {list(self._BACKBONES)}, got {self.backbone!r}"
            )
        if self.horizon < 1 or self.context_length < 1:
            raise ValidationError("context_length and horizon must be positive")
        if self.horizon > self.context_length:
            # Forecasting further than you can see is possible, but it is almost always a
            # configuration mistake rather than an intent.
            raise ValidationError("horizon should not exceed context_length")

    @property
    def parameter_count(self) -> int:
        if self.backbone == "lstm":
            recurrent = RecurrentSpec(
                architecture=Architecture.LSTM, task=Task.FORECASTING,
                input_size=self.input_features, hidden_size=self.hidden_dim,
                n_layers=self.n_layers, n_classes=self.horizon,
            )
            return recurrent.parameter_count
        if self.backbone == "tcn":
            kernel = 3
            total, in_channels = 0, self.input_features
            for _ in range(self.n_layers):
                total += in_channels * self.hidden_dim * kernel + self.hidden_dim
                in_channels = self.hidden_dim
            return total + self.hidden_dim * self.horizon
        transformer = TransformerSpec(
            architecture=Architecture.TRANSFORMER, task=Task.FORECASTING,
            vocab_size=1, d_model=self.hidden_dim, n_heads=4, n_layers=self.n_layers,
            d_ff=self.hidden_dim * 4, max_positions=self.context_length, n_classes=self.horizon,
        )
        return transformer.parameter_count


@dataclass(frozen=True, slots=True)
class TabularSpec(ModuleSpec):
    """Plain MLP for classification or regression over engineered features."""

    input_dim: int = 64
    hidden_dims: tuple[int, ...] = (128, 64)
    output_dim: int = 1
    batch_norm: bool = True

    @property
    def parameter_count(self) -> int:
        dims = [self.input_dim, *self.hidden_dims, self.output_dim]
        total = sum(dims[i] * dims[i + 1] + dims[i + 1] for i in range(len(dims) - 1))
        if self.batch_norm:
            total += sum(2 * d for d in self.hidden_dims)
        return total


@dataclass(frozen=True, slots=True)
class RecommenderSpec(ModuleSpec):
    """Two-tower retrieval model.

    Two-tower rather than matrix factorisation because the towers can consume the content
    embeddings this platform already computes, so a new item is recommendable immediately
    instead of waiting for interaction data.
    """

    n_users: int = 10_000
    n_items: int = 10_000
    embedding_dim: int = 64
    tower_dims: tuple[int, ...] = (128, 64)
    use_content_features: bool = True
    content_dim: int = 384

    @property
    def parameter_count(self) -> int:
        embeddings = (self.n_users + self.n_items) * self.embedding_dim
        tower_input = self.embedding_dim + (self.content_dim if self.use_content_features else 0)
        dims = [tower_input, *self.tower_dims]
        tower = sum(dims[i] * dims[i + 1] + dims[i + 1] for i in range(len(dims) - 1))
        return embeddings + tower * 2


@dataclass(frozen=True, slots=True)
class AnomalyDetectorSpec(ModuleSpec):
    """Anomaly detection over operational or behavioural series.

    The platform's own use: drift detection on embedding centroids, refusal rate, and latency --
    the ``aura_drift_score`` signals in ADR-009.
    """

    input_dim: int = 64
    method: str = "autoencoder"  # autoencoder | isolation_forest | one_class_svm
    latent_dim: int = 16
    threshold_percentile: float = 99.0

    def __post_init__(self) -> None:
        ModuleSpec.__post_init__(self)
        if self.method not in {"autoencoder", "isolation_forest", "one_class_svm"}:
            raise ValidationError(f"unsupported anomaly method {self.method!r}")
        if not 50.0 <= self.threshold_percentile < 100.0:
            raise ValidationError("threshold_percentile must be within [50, 100)")

    @property
    def parameter_count(self) -> int:
        if self.method != "autoencoder":
            return 0  # classical methods have no learned weight tensor
        return AutoencoderSpec(
            architecture=Architecture.AUTOENCODER, task=Task.RECONSTRUCTION,
            input_dim=self.input_dim, hidden_dims=(self.input_dim // 2,),
            latent_dim=self.latent_dim,
        ).parameter_count


# --------------------------------------------------------------------------------------
# Materialisation
# --------------------------------------------------------------------------------------


def torch_available() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def build(spec: ModuleSpec) -> Any:
    """Materialise a spec into a ``torch.nn.Module``.

    Torch is imported here and nowhere else in the package. A deployment that never trains never
    installs it, and the specs above remain fully usable for sizing, validation, and lineage.
    """
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise ConfigurationError(
            "PyTorch is required to materialise a model specification; install it in the "
            "training image (specs themselves need no dependency)",
            details={"architecture": str(spec.architecture)},
        ) from exc

    if isinstance(spec, TransformerSpec):
        layer = nn.TransformerEncoderLayer(
            d_model=spec.d_model, nhead=spec.n_heads, dim_feedforward=spec.d_ff,
            dropout=spec.dropout, batch_first=True,
        )
        return nn.Sequential(
            nn.Embedding(spec.vocab_size, spec.d_model),
            nn.TransformerEncoder(layer, num_layers=spec.n_layers),
            _Pool(),
            nn.Linear(spec.d_model, spec.n_classes),
        )

    if isinstance(spec, CNNSpec):
        conv = nn.Conv1d if spec.dimensions == 1 else nn.Conv2d
        pool = nn.MaxPool1d if spec.dimensions == 1 else nn.MaxPool2d
        layers: list[Any] = []
        previous = spec.in_channels
        for channel in spec.channels:
            layers += [
                conv(previous, channel, spec.kernel_size, padding=spec.kernel_size // 2),
                nn.ReLU(),
                pool(2),
                nn.Dropout(spec.dropout),
            ]
            previous = channel
        layers += [nn.Flatten(), nn.LazyLinear(spec.n_classes)]
        return nn.Sequential(*layers)

    if isinstance(spec, RecurrentSpec):
        cell = {
            Architecture.LSTM: nn.LSTM, Architecture.GRU: nn.GRU, Architecture.RNN: nn.RNN
        }[spec.architecture]
        return _RecurrentClassifier(
            cell(
                spec.input_size, spec.hidden_size, num_layers=spec.n_layers,
                batch_first=True, bidirectional=spec.bidirectional,
                dropout=spec.dropout if spec.n_layers > 1 else 0.0,
            ),
            nn.Linear(spec.hidden_size * (2 if spec.bidirectional else 1), spec.n_classes),
        )

    if isinstance(spec, AutoencoderSpec):
        def stack(dims: Sequence[int]) -> Any:
            layers: list[Any] = []
            for i in range(len(dims) - 1):
                layers.append(nn.Linear(dims[i], dims[i + 1]))
                if i < len(dims) - 2:
                    layers += [nn.ReLU(), nn.Dropout(spec.dropout)]
            return nn.Sequential(*layers)

        dims = [spec.input_dim, *spec.hidden_dims, spec.latent_dim]
        return nn.Sequential(stack(dims), stack(list(reversed(dims))))

    if isinstance(spec, (TabularSpec, AnomalyDetectorSpec)):
        if isinstance(spec, AnomalyDetectorSpec):
            return build(
                AutoencoderSpec(
                    architecture=Architecture.AUTOENCODER, task=Task.RECONSTRUCTION,
                    input_dim=spec.input_dim, hidden_dims=(spec.input_dim // 2,),
                    latent_dim=spec.latent_dim, dropout=spec.dropout,
                )
            )
        layers: list[Any] = []
        dims = [spec.input_dim, *spec.hidden_dims]
        for i in range(len(dims) - 1):
            layers.append(nn.Linear(dims[i], dims[i + 1]))
            if spec.batch_norm:
                layers.append(nn.BatchNorm1d(dims[i + 1]))
            layers += [nn.ReLU(), nn.Dropout(spec.dropout)]
        layers.append(nn.Linear(dims[-1], spec.output_dim))
        return nn.Sequential(*layers)

    if isinstance(spec, TimeSeriesSpec):
        return build(
            RecurrentSpec(
                architecture=Architecture.LSTM, task=Task.FORECASTING,
                input_size=spec.input_features, hidden_size=spec.hidden_dim,
                n_layers=spec.n_layers, n_classes=spec.horizon, dropout=spec.dropout,
            )
        )

    if isinstance(spec, GraphNetworkSpec):
        # Deliberately not silently substituting a dense approximation: a caller asking for a
        # GNN needs message passing, and quietly giving them an MLP would produce results that
        # look plausible and mean nothing.
        raise ConfigurationError(
            "graph networks require torch-geometric; install it in the training image",
            details={"conv_type": spec.conv_type},
        )

    if isinstance(spec, RecommenderSpec):
        return _TwoTower(spec, torch, nn)

    raise ConfigurationError(f"no builder for {type(spec).__name__}")


def _make_helpers():  # pragma: no cover - only reachable when torch is installed
    from torch import nn

    class _Pool(nn.Module):
        """Mean-pool a transformer's sequence output to a single vector."""

        def forward(self, x):  # type: ignore[no-untyped-def]
            return x.mean(dim=1)

    class _RecurrentClassifier(nn.Module):
        def __init__(self, recurrent, head):  # type: ignore[no-untyped-def]
            super().__init__()
            self.recurrent, self.head = recurrent, head

        def forward(self, x):  # type: ignore[no-untyped-def]
            output, _ = self.recurrent(x)
            return self.head(output[:, -1, :])

    class _TwoTower(nn.Module):
        def __init__(self, spec, torch_module, nn_module):  # type: ignore[no-untyped-def]
            super().__init__()
            self.user_embedding = nn_module.Embedding(spec.n_users, spec.embedding_dim)
            self.item_embedding = nn_module.Embedding(spec.n_items, spec.embedding_dim)
            width = spec.embedding_dim + (spec.content_dim if spec.use_content_features else 0)

            def tower() -> Any:
                dims = [width, *spec.tower_dims]
                layers: list[Any] = []
                for i in range(len(dims) - 1):
                    layers += [nn_module.Linear(dims[i], dims[i + 1]), nn_module.ReLU()]
                return nn_module.Sequential(*layers)

            self.user_tower, self.item_tower = tower(), tower()

        def forward(self, user_ids, item_ids, user_content=None, item_content=None):  # type: ignore[no-untyped-def]
            import torch as _torch

            user = self.user_embedding(user_ids)
            item = self.item_embedding(item_ids)
            if user_content is not None:
                user = _torch.cat([user, user_content], dim=-1)
            if item_content is not None:
                item = _torch.cat([item, item_content], dim=-1)
            return (self.user_tower(user) * self.item_tower(item)).sum(dim=-1)

    return _Pool, _RecurrentClassifier, _TwoTower


if torch_available():  # pragma: no cover - environment dependent
    _Pool, _RecurrentClassifier, _TwoTower = _make_helpers()
else:
    _Pool = _RecurrentClassifier = _TwoTower = None  # type: ignore[assignment]


#: Every architecture the platform knows how to describe, for the admin inventory endpoint.
CATALOGUE: tuple[type[ModuleSpec], ...] = (
    TransformerSpec,
    CNNSpec,
    RecurrentSpec,
    AutoencoderSpec,
    GraphNetworkSpec,
    TimeSeriesSpec,
    TabularSpec,
    RecommenderSpec,
    AnomalyDetectorSpec,
)
