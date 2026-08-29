"""MLOps ports: fine-tune trainers and related artefacts (ADR-008 Loop B).

Training runs outside the gateway process. The port keeps the job engine
independent of torch / PEFT; adapters import heavy deps inside ``train()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

__all__ = [
    "FineTuneJobSpec",
    "TrainResult",
    "FineTuneTrainer",
]


@dataclass(frozen=True, slots=True)
class FineTuneJobSpec:
    """Everything a trainer needs — never a live DB handle."""

    job_id: str
    dataset_digest: str
    dataset_jsonl: str
    base_model: str
    output_tag: str
    method: str = "lora"
    parameters: Mapping[str, Any] = field(default_factory=dict)
    allow_cpu: bool = False


@dataclass(frozen=True, slots=True)
class TrainResult:
    artefact_uri: str
    provider_kind: str  # ollama | openai_compatible
    metrics: Mapping[str, float] = field(default_factory=dict)
    notes: str = ""


@runtime_checkable
class FineTuneTrainer(Protocol):
    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        """Run training. Must not raise for soft failures — return metrics or raise."""
        ...
