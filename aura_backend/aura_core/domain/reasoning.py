"""Reasoning entities: strategies, thoughts, traces, verification, confidence.

Reasoning artefacts are **internal**. A ``ReasoningTrace`` is telemetry and audit material, not
user-facing text: the kernel returns a final answer plus a confidence assessment, and the trace
goes to the telemetry record. Streaming a model's intermediate deliberation to an end user is a
product decision this architecture deliberately does not make for the caller.

Confidence here is *calibrated from observable signals* (groundedness, verification outcome,
retrieval strength, self-consistency across samples), not "ask the model how sure it is" -- which
is well known to be poorly calibrated and trivially gamed by prompt injection.
"""

from __future__ import annotations

import enum
import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

__all__ = [
    "ReasoningStrategy",
    "Thought",
    "ThoughtNode",
    "VerificationOutcome",
    "Verification",
    "ConfidenceAssessment",
    "ReasoningTrace",
]


class ReasoningStrategy(str, enum.Enum):
    """How much deliberation to spend.

    Selection is cost-aware: ``DIRECT`` for simple lookups, ``TREE_OF_THOUGHTS`` only where the
    branching genuinely pays for itself. The reasoning engine picks a default from question
    features; callers may override.
    """

    DIRECT = "direct"
    CHAIN_OF_THOUGHT = "chain_of_thought"
    SELF_CONSISTENCY = "self_consistency"
    TREE_OF_THOUGHTS = "tree_of_thoughts"
    REFLEXION = "reflexion"

    def __str__(self) -> str:
        return self.value

    @property
    def approximate_generations(self) -> int:
        """Rough cost multiplier, used for budgeting before committing to a strategy."""
        return {
            ReasoningStrategy.DIRECT: 1,
            ReasoningStrategy.CHAIN_OF_THOUGHT: 1,
            ReasoningStrategy.SELF_CONSISTENCY: 3,
            ReasoningStrategy.TREE_OF_THOUGHTS: 6,
            ReasoningStrategy.REFLEXION: 3,
        }[self]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Thought:
    """One deliberation step."""

    content: str
    kind: str = "analysis"  # analysis | hypothesis | critique | revision | conclusion
    created_at: datetime = field(default_factory=_utcnow)
    tokens: int = 0


@dataclass(frozen=True, slots=True)
class ThoughtNode:
    """A node in a tree-of-thoughts search.

    ``value`` is the evaluator's score for the *partial* solution; the search expands the most
    promising frontier nodes within a fixed node budget so cost stays bounded.
    """

    id: str
    content: str
    parent_id: str | None = None
    depth: int = 0
    value: float = 0.0
    terminal: bool = False
    children: tuple[str, ...] = ()

    def with_children(self, ids: Sequence[str]) -> "ThoughtNode":
        return replace(self, children=tuple(ids))


class VerificationOutcome(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Verification:
    """The result of checking a candidate answer against explicit criteria."""

    outcome: VerificationOutcome
    checks: tuple[tuple[str, bool, str], ...] = ()  # (criterion, passed, note)
    notes: str = ""
    duration_ms: float = 0.0

    @property
    def pass_rate(self) -> float:
        if not self.checks:
            return 0.0
        return sum(1 for _, ok, _ in self.checks if ok) / len(self.checks)

    @property
    def failed_criteria(self) -> tuple[str, ...]:
        return tuple(name for name, ok, _ in self.checks if not ok)


@dataclass(frozen=True, slots=True)
class ConfidenceAssessment:
    """A calibrated confidence score with its inputs preserved.

    Keeping the components means a low score can be explained ("retrieval was weak") and the
    weights can be re-fit later against user feedback without changing call sites.
    """

    score: float
    groundedness: float | None = None
    retrieval_strength: float | None = None
    verification_rate: float | None = None
    self_consistency: float | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("confidence score must be within [0, 1]")

    @property
    def band(self) -> str:
        if self.score >= 0.75:
            return "high"
        if self.score >= 0.45:
            return "medium"
        return "low"

    @classmethod
    def from_signals(
        cls,
        *,
        groundedness: float | None = None,
        retrieval_strength: float | None = None,
        verification_rate: float | None = None,
        self_consistency: float | None = None,
        weights: Mapping[str, float] | None = None,
    ) -> "ConfidenceAssessment":
        """Weighted mean over whichever signals are available.

        Absent signals are dropped rather than defaulted to zero -- a turn with no retrieval
        should not be penalised for having no retrieval score. If no signal at all is available
        the result is deliberately 0.5 with an explicit rationale, never a confident number
        conjured from nothing.
        """
        w = {
            "groundedness": 0.40,
            "retrieval_strength": 0.20,
            "verification_rate": 0.25,
            "self_consistency": 0.15,
            **(weights or {}),
        }
        signals = {
            "groundedness": groundedness,
            "retrieval_strength": retrieval_strength,
            "verification_rate": verification_rate,
            "self_consistency": self_consistency,
        }
        present = {k: v for k, v in signals.items() if v is not None}
        if not present:
            return cls(0.5, rationale="no confidence signals available")
        total_weight = sum(w[k] for k in present)
        score = sum(w[k] * v for k, v in present.items()) / total_weight
        rationale = ", ".join(f"{k}={v:.2f}" for k, v in sorted(present.items()))
        return cls(
            score=round(min(max(score, 0.0), 1.0), 4),
            groundedness=groundedness,
            retrieval_strength=retrieval_strength,
            verification_rate=verification_rate,
            self_consistency=self_consistency,
            rationale=rationale,
        )


@dataclass(frozen=True, slots=True)
class ReasoningTrace:
    """Internal record of how an answer was reached.

    Never returned to end users. Persisted (content-free by default: counts and scores, with
    full text only when trace sampling is explicitly enabled) so that regressions can be
    diagnosed and so eval runs can compare strategies.
    """

    strategy: ReasoningStrategy
    thoughts: tuple[Thought, ...] = ()
    nodes: tuple[ThoughtNode, ...] = ()
    candidates: tuple[str, ...] = ()
    selected_index: int | None = None
    verification: Verification | None = None
    confidence: ConfidenceAssessment | None = None
    generations_used: int = 0
    duration_ms: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def with_thought(self, thought: Thought) -> "ReasoningTrace":
        return replace(self, thoughts=self.thoughts + (thought,))

    @property
    def agreement(self) -> float | None:
        """Self-consistency: the fraction of sampled candidates matching the modal answer.

        Compares normalised text. This is a coarse proxy -- two differently-worded correct
        answers count as disagreement -- so it is one input to confidence, never the sole one.
        """
        if len(self.candidates) < 2:
            return None
        normalised = [" ".join(c.lower().split()) for c in self.candidates]
        try:
            modal = statistics.mode(normalised)
        except statistics.StatisticsError:  # no unique mode
            return 1.0 / len(normalised)
        return normalised.count(modal) / len(normalised)

    def summary(self) -> dict[str, Any]:
        """Content-free summary suitable for telemetry."""
        return {
            "strategy": str(self.strategy),
            "thoughts": len(self.thoughts),
            "nodes": len(self.nodes),
            "candidates": len(self.candidates),
            "generations_used": self.generations_used,
            "verification": str(self.verification.outcome) if self.verification else None,
            "confidence": self.confidence.score if self.confidence else None,
            "duration_ms": round(self.duration_ms, 2),
        }
