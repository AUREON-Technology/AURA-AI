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
    """Everything a trainer needs — never a live DB handle.

    ``base_model`` is whatever the trainer can load. For ``LocalLoraTrainer`` that is a Hugging
    Face repo id or a local directory holding a transformers checkpoint; an Ollama tag
    (``name:tag``, a quantised GGUF) cannot be fine-tuned and is refused.

    The hyperparameter fields are typed and defaulted here so a trainer never has to guess at a
    field that might not exist (GAP-017: a trainer once read ``job.epochs`` from a spec without
    that field, and the resulting ``AttributeError`` was swallowed into a fake success).
    """

    job_id: str
    dataset_digest: str
    dataset_jsonl: str
    base_model: str
    output_tag: str
    method: str = "lora"
    parameters: Mapping[str, Any] = field(default_factory=dict)
    allow_cpu: bool = False
    #: Pin a base-model revision (git sha / tag on the Hub). Empty = the trainer resolves one and
    #: reports what it actually loaded in ``TrainResult.base_model_revision``.
    base_model_revision: str = ""
    epochs: int = 1
    batch_size: int = 1
    learning_rate: float = 2e-4
    #: Stop after this many optimiser steps even if epochs remain. ``None`` = epochs decide.
    max_steps: int | None = None
    lora_rank: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    #: Module names to wrap with LoRA. Empty = let PEFT pick its per-architecture default.
    lora_target_modules: tuple[str, ...] = ()
    max_seq_len: int = 512
    seed: int = 0


@dataclass(frozen=True, slots=True)
class TrainResult:
    """What a trainer produced.

    ``weights_changed`` defaults to ``False`` on purpose: a trainer has to assert, positively,
    that it changed model weights and name the artefact (``artefact_uri`` + ``artefact_digest``)
    before the job engine will record success or register a model version. Anything else — a
    dry run, a Modelfile, a disabled trainer — is recorded as an explicit non-training outcome
    with ``notes`` as the reason, and never becomes a promotable model version (GAP-018).
    """

    artefact_uri: str
    provider_kind: str  # peft_lora | ollama | openai_compatible | none
    metrics: Mapping[str, float] = field(default_factory=dict)
    notes: str = ""
    weights_changed: bool = False
    #: Content hash of the produced artefact (e.g. sha256 over the saved adapter files).
    artefact_digest: str = ""
    #: The base-model revision actually loaded, when the trainer can resolve it.
    base_model_revision: str = ""

    @classmethod
    def not_trained(cls, reason: str, *, metrics: Mapping[str, float] | None = None
                    ) -> "TrainResult":
        """A result that says, explicitly, that no weights were changed and why."""
        return cls(
            artefact_uri="",
            provider_kind="none",
            metrics=dict(metrics or {}),
            notes=reason,
            weights_changed=False,
        )


@runtime_checkable
class FineTuneTrainer(Protocol):
    async def train(self, job: FineTuneJobSpec) -> TrainResult:
        """Run training.

        Raise on failure (the engine records FAILED). Return ``weights_changed=True`` only when
        model weights were actually updated and saved; any other return is recorded as an
        explicit non-training outcome, never as success.
        """
        ...
