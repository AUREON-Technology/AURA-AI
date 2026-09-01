"""Dataset curation and fine-tune recommendation (ADR-008, Loop B).

The system may prepare; only a human may promote.

What runs automatically: collecting feedback, filtering it to genuinely high-quality examples,
de-identifying it, computing dataset statistics, and deciding whether the corpus is good enough
to be *worth* a fine-tuning job. What requires a recorded human approval: launching the job and
marking a model version production.

The curation filters here are strict on purpose. Fine-tuning on mediocre data is worse than not
fine-tuning: the model learns the mediocrity, and the damage is slow to notice and expensive to
undo. Concretely, an example survives only if it was positively rated *or* corrected by a human,
is not near-duplicate of another, contains no detectable PII or credentials, and is not
degenerate (empty, truncated, or an error message).

Replaces ``services/learning_pipeline.py``, which appends every record to a JSON file rewritten
in full on each write (O(n) per append, corrupt on concurrent writers, lost on restart) and
filters only on ``rating >= 3``.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from ..domain.errors import ValidationError
from ..observability.logging import get_logger
from ..safety.guards import PIIGuard, SecretGuard
from ..domain.safety import GuardAction, GuardStage

__all__ = [
    "FeedbackRecord",
    "CuratedExample",
    "DatasetStatistics",
    "FineTuneRecommendation",
    "DatasetCurator",
]

logger = get_logger("learning.curation")

# Responses that are failures rather than answers. Training on these teaches the model to
# produce error messages.
_DEGENERATE = (
    re.compile(r"^\s*\[?(error|internal server error|failed|unavailable)\b", re.I),
    re.compile(r"\b(?:I'm sorry, (?:but )?I (?:can't|cannot)|as an AI language model)\b", re.I),
    re.compile(r"^\s*$"),
)


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    """One rated interaction, as collected from the client or the telemetry pipeline."""

    id: str
    prompt: str
    response: str
    rating: int | None = None            # 1-5, or None when not rated
    correction: str | None = None        # human-written replacement
    user_ref: str = ""                   # pseudonymous; never a raw email
    model: str = ""
    agent: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def target(self) -> str:
        """What the model should have said. A human correction always wins over the original."""
        return (self.correction or self.response).strip()


@dataclass(frozen=True, slots=True)
class CuratedExample:
    prompt: str
    completion: str
    source_id: str
    weight: float = 1.0
    agent: str = ""
    was_corrected: bool = False

    def to_chatml(self, system: str) -> dict[str, Any]:
        return {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self.prompt},
                {"role": "assistant", "content": self.completion},
            ]
        }

    def to_alpaca(self, instruction: str) -> dict[str, Any]:
        return {"instruction": instruction, "input": self.prompt, "output": self.completion}


@dataclass(frozen=True, slots=True)
class DatasetStatistics:
    """What a reviewer needs to judge whether a dataset is worth training on."""

    total_candidates: int = 0
    accepted: int = 0
    rejected_low_rating: int = 0
    rejected_duplicate: int = 0
    rejected_pii: int = 0
    rejected_degenerate: int = 0
    rejected_short: int = 0
    distinct_users: int = 0
    corrected_fraction: float = 0.0
    mean_prompt_chars: float = 0.0
    mean_completion_chars: float = 0.0
    agent_distribution: Mapping[str, int] = field(default_factory=dict)
    model_distribution: Mapping[str, int] = field(default_factory=dict)

    @property
    def acceptance_rate(self) -> float:
        return self.accepted / self.total_candidates if self.total_candidates else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_candidates": self.total_candidates,
            "accepted": self.accepted,
            "acceptance_rate": round(self.acceptance_rate, 4),
            "rejected": {
                "low_rating": self.rejected_low_rating,
                "duplicate": self.rejected_duplicate,
                "pii_or_secret": self.rejected_pii,
                "degenerate": self.rejected_degenerate,
                "too_short": self.rejected_short,
            },
            "distinct_users": self.distinct_users,
            "corrected_fraction": round(self.corrected_fraction, 4),
            "mean_prompt_chars": round(self.mean_prompt_chars, 1),
            "mean_completion_chars": round(self.mean_completion_chars, 1),
            "agent_distribution": dict(self.agent_distribution),
            "model_distribution": dict(self.model_distribution),
        }


@dataclass(frozen=True, slots=True)
class FineTuneRecommendation:
    """The packet a human reviews before approving a job.

    Complete enough to approve in minutes: what the dataset contains, why it qualifies (or does
    not), what it would cost, and what the current model scores on the same evaluation.
    """

    recommended: bool
    reasons: tuple[str, ...]
    statistics: DatasetStatistics
    dataset_digest: str
    estimated_examples: int
    estimated_tokens: int
    estimated_cost_usd: float | None = None
    base_model: str = ""
    method: str = "lora"

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommended": self.recommended,
            "reasons": list(self.reasons),
            "statistics": self.statistics.as_dict(),
            "dataset_digest": self.dataset_digest,
            "estimated_examples": self.estimated_examples,
            "estimated_tokens": self.estimated_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "base_model": self.base_model,
            "method": self.method,
        }


class DatasetCurator:
    def __init__(
        self,
        *,
        min_rating: int = 4,
        min_prompt_chars: int = 12,
        min_completion_chars: int = 20,
        near_duplicate_threshold: float = 0.9,
        min_examples: int = 500,
        min_distinct_users: int = 5,
        redact_pii: bool = True,
    ) -> None:
        self._min_rating = min_rating
        self._min_prompt = min_prompt_chars
        self._min_completion = min_completion_chars
        self._duplicate_threshold = near_duplicate_threshold
        self._min_examples = min_examples
        self._min_users = min_distinct_users
        self._redact = redact_pii
        self._pii = PIIGuard(redact=True)
        self._secrets = SecretGuard(block=True)

    # -- curation ----------------------------------------------------------------------

    def curate(self, records: Iterable[FeedbackRecord]) -> tuple[list[CuratedExample], DatasetStatistics]:
        accepted: list[CuratedExample] = []
        seen_shingles: list[set[str]] = []
        counters = {
            "total": 0, "low_rating": 0, "duplicate": 0, "pii": 0,
            "degenerate": 0, "short": 0,
        }
        users: set[str] = set()
        agents: dict[str, int] = {}
        models: dict[str, int] = {}
        prompt_lengths: list[int] = []
        completion_lengths: list[int] = []
        corrected = 0

        for record in records:
            counters["total"] += 1
            prompt = record.prompt.strip()
            completion = record.target

            # 1. Quality gate. A correction is a human explicitly saying "this is the right
            # answer", which is stronger evidence than any rating -- so it bypasses the rating
            # threshold entirely.
            if not record.correction:
                if record.rating is None or record.rating < self._min_rating:
                    counters["low_rating"] += 1
                    continue

            if len(prompt) < self._min_prompt or len(completion) < self._min_completion:
                counters["short"] += 1
                continue

            if any(pattern.search(completion) for pattern in _DEGENERATE):
                counters["degenerate"] += 1
                continue

            # 2. Credentials are a hard reject, not a redaction: a training corpus is copied,
            # shared, and retained, and a redaction bug in one example is unrecoverable.
            if self._secrets.evaluate(
                f"{prompt}\n{completion}", stage=GuardStage.OUTPUT
            ).action is GuardAction.BLOCK:
                counters["pii"] += 1
                continue

            if self._redact:
                prompt = self._redact_text(prompt)
                completion = self._redact_text(completion)

            # 3. Near-duplicate rejection. Repeated examples over-weight whatever they teach,
            # and feedback corpora are full of them (the same question asked many times).
            shingles = _shingles(f"{prompt} {completion}")
            if any(_jaccard(shingles, other) >= self._duplicate_threshold for other in seen_shingles):
                counters["duplicate"] += 1
                continue
            seen_shingles.append(shingles)

            accepted.append(
                CuratedExample(
                    prompt=prompt,
                    completion=completion,
                    source_id=record.id,
                    # Corrections are the highest-signal examples in the corpus.
                    weight=1.5 if record.correction else 1.0,
                    agent=record.agent,
                    was_corrected=bool(record.correction),
                )
            )
            if record.user_ref:
                users.add(record.user_ref)
            agents[record.agent or "unknown"] = agents.get(record.agent or "unknown", 0) + 1
            models[record.model or "unknown"] = models.get(record.model or "unknown", 0) + 1
            prompt_lengths.append(len(prompt))
            completion_lengths.append(len(completion))
            corrected += 1 if record.correction else 0

        statistics_ = DatasetStatistics(
            total_candidates=counters["total"],
            accepted=len(accepted),
            rejected_low_rating=counters["low_rating"],
            rejected_duplicate=counters["duplicate"],
            rejected_pii=counters["pii"],
            rejected_degenerate=counters["degenerate"],
            rejected_short=counters["short"],
            distinct_users=len(users),
            corrected_fraction=corrected / len(accepted) if accepted else 0.0,
            mean_prompt_chars=statistics.fmean(prompt_lengths) if prompt_lengths else 0.0,
            mean_completion_chars=statistics.fmean(completion_lengths) if completion_lengths else 0.0,
            agent_distribution=agents,
            model_distribution=models,
        )
        logger.info("dataset curated", extra=statistics_.as_dict()["rejected"])
        return accepted, statistics_

    def _redact_text(self, text: str) -> str:
        verdict = self._pii.evaluate(text, stage=GuardStage.OUTPUT)
        return verdict.sanitised if verdict.sanitised is not None else text

    # -- recommendation ----------------------------------------------------------------

    def recommend(
        self,
        examples: Sequence[CuratedExample],
        statistics_: DatasetStatistics,
        *,
        base_model: str = "",
        method: str = "lora",
        cost_per_1k_tokens_usd: float | None = None,
    ) -> FineTuneRecommendation:
        """Decide whether a fine-tune is worth proposing, and say why either way.

        A negative recommendation is as useful as a positive one -- it tells the operator what
        is missing ("only 2 distinct users; the corpus reflects one person's preferences").
        """
        reasons: list[str] = []
        recommended = True

        if len(examples) < self._min_examples:
            recommended = False
            reasons.append(
                f"{len(examples)} curated example(s); at least {self._min_examples} are needed "
                "for a fine-tune to generalise rather than memorise"
            )
        if statistics_.distinct_users < self._min_users:
            recommended = False
            reasons.append(
                f"only {statistics_.distinct_users} distinct user(s); the corpus would encode "
                f"one person's preferences as general behaviour (minimum {self._min_users})"
            )
        if statistics_.acceptance_rate < 0.05 and statistics_.total_candidates > 100:
            recommended = False
            reasons.append(
                f"acceptance rate {statistics_.acceptance_rate:.1%} is very low; investigate "
                "feedback quality before training on what survived"
            )
        if len(statistics_.agent_distribution) == 1 and len(examples) > 50:
            reasons.append(
                "all examples come from a single agent persona; the fine-tune will specialise "
                "to it, which may be intended"
            )

        if recommended:
            reasons.append(
                f"{len(examples)} examples from {statistics_.distinct_users} users, "
                f"{statistics_.corrected_fraction:.0%} human-corrected"
            )

        tokens = sum(
            (len(e.prompt) + len(e.completion)) // 4 + 8 for e in examples
        )
        return FineTuneRecommendation(
            recommended=recommended,
            reasons=tuple(reasons),
            statistics=statistics_,
            dataset_digest=dataset_digest(examples) if examples else "",
            estimated_examples=len(examples),
            estimated_tokens=tokens,
            estimated_cost_usd=(
                round(tokens / 1000 * cost_per_1k_tokens_usd, 2)
                if cost_per_1k_tokens_usd is not None
                else None
            ),
            base_model=base_model,
            method=method,
        )

    # -- export ------------------------------------------------------------------------

    @staticmethod
    def export(
        examples: Sequence[CuratedExample],
        *,
        fmt: str = "chatml",
        system_prompt: str = "You are AURA, a personal AI operating system.",
        instruction: str = "Respond to the user's request.",
    ) -> str:
        """Serialise to JSONL in the requested training format."""
        if fmt == "chatml":
            rows = [e.to_chatml(system_prompt) for e in examples]
        elif fmt == "alpaca":
            rows = [e.to_alpaca(instruction) for e in examples]
        elif fmt == "openai":
            rows = [{"prompt": e.prompt, "completion": e.completion} for e in examples]
        else:
            raise ValidationError(
                f"unknown export format {fmt!r}",
                details={"supported": ["chatml", "alpaca", "openai"]},
            )
        return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)

    @staticmethod
    def split(
        examples: Sequence[CuratedExample], *, validation_fraction: float = 0.1
    ) -> tuple[list[CuratedExample], list[CuratedExample]]:
        """Deterministic train/validation split.

        Split by a hash of the source id, not by position or by a random shuffle: the same
        example lands in the same split across runs, so a validation score is comparable between
        two dataset versions rather than being partly a resampling artefact.
        """
        train, validation = [], []
        threshold = int(validation_fraction * 1000)
        for example in examples:
            bucket = int(hashlib.sha256(example.source_id.encode()).hexdigest()[:4], 16) % 1000
            (validation if bucket < threshold else train).append(example)
        return train, validation


def dataset_digest(examples: Sequence[CuratedExample]) -> str:
    """Content hash of a dataset.

    An approval names this digest, so an approved dataset cannot be swapped for a different one
    between approval and training.
    """
    hasher = hashlib.sha256()
    for example in sorted(examples, key=lambda e: e.source_id):
        hasher.update(example.source_id.encode())
        hasher.update(example.prompt.encode())
        hasher.update(example.completion.encode())
    return hasher.hexdigest()[:32]


def _shingles(text: str, size: int = 4) -> set[str]:
    words = text.lower().split()
    if len(words) < size:
        return {" ".join(words)}
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
