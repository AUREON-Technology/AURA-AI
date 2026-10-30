"""Turn telemetry (ADR-009).

``TurnTelemetry`` is a domain object returned alongside every kernel result, not a logging
side-effect. Making it part of the return value means it cannot be forgotten, and means the
kernel needs no metrics dependency -- keeping the core stdlib-only.

**No content.** Prompts, documents, memories, and outputs never appear here. Lengths, hashes,
counts, and scores do.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from .identifiers import CorrelationId, SessionId, TurnId

__all__ = [
    "StageTiming",
    "CacheOutcome",
    "RetrievalTelemetry",
    "ToolTelemetry",
    "TurnOutcome",
    "TurnTelemetry",
]


class TurnOutcome(str, enum.Enum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXCEEDED = "budget_exceeded"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class StageTiming:
    """Per-stage durations in milliseconds.

    Kept as an explicit record rather than a free-form dict so a new stage cannot silently
    appear in dashboards under a typo'd key.
    """

    guards_in: float = 0.0
    prompt: float = 0.0
    memory: float = 0.0
    retrieval: float = 0.0
    context: float = 0.0
    planning: float = 0.0
    reasoning: float = 0.0
    tools: float = 0.0
    generation: float = 0.0
    guards_out: float = 0.0

    @property
    def overhead(self) -> float:
        """Everything except model time -- the number the p95 target in ADR-000 refers to."""
        return (
            self.guards_in
            + self.prompt
            + self.memory
            + self.retrieval
            + self.context
            + self.planning
            + self.tools
            + self.guards_out
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "guards_in": self.guards_in,
            "prompt": self.prompt,
            "memory": self.memory,
            "retrieval": self.retrieval,
            "context": self.context,
            "planning": self.planning,
            "reasoning": self.reasoning,
            "tools": self.tools,
            "generation": self.generation,
            "guards_out": self.guards_out,
        }


@dataclass(frozen=True, slots=True)
class CacheOutcome:
    hit: bool = False
    tier: str | None = None  # "exact" | "semantic"
    similarity: float | None = None


@dataclass(frozen=True, slots=True)
class RetrievalTelemetry:
    candidates: int = 0
    returned: int = 0
    top_score: float = 0.0
    below_threshold: int = 0
    quarantined: int = 0
    citations: int = 0
    strategy: str = "none"

    @property
    def returned_nothing(self) -> bool:
        """Distinguishes "searched and found nothing" from "did not search"."""
        return self.candidates > 0 and self.returned == 0


@dataclass(frozen=True, slots=True)
class ToolTelemetry:
    name: str
    ok: bool
    duration_ms: float
    error_kind: str | None = None
    allowed: bool = True


@dataclass(frozen=True, slots=True)
class TurnTelemetry:
    """One turn's complete, content-free record."""

    correlation_id: CorrelationId
    turn_id: TurnId
    session_id: SessionId | None = None
    route: str = "chat"
    agent: str = "orchestrator"
    strategy: str = "direct"
    outcome: TurnOutcome = TurnOutcome.SUCCESS
    error_kind: str | None = None

    provider: str | None = None
    model: str | None = None
    fallback_count: int = 0
    providers_attempted: tuple[str, ...] = ()

    cache: CacheOutcome = field(default_factory=CacheOutcome)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    total_ms: float = 0.0
    ttft_ms: float | None = None
    timings: StageTiming = field(default_factory=StageTiming)

    retrieval: RetrievalTelemetry = field(default_factory=RetrievalTelemetry)
    memories_recalled: int = 0
    cag_tokens: int = 0
    cag_docs: int = 0
    cag_identity: str | None = None
    cag_cache_hit: bool = False
    context_tokens: int = 0
    context_dropped: int = 0

    guards: tuple[Mapping[str, Any], ...] = ()
    tainted: bool = False
    tools: tuple[ToolTelemetry, ...] = ()

    plan_steps: int = 0
    plan_revisions: int = 0
    #: Planning or tool execution raised and the turn answered without tools (CB-14). Without
    #: this the turn reported plain success, and "create a task" read as done when nothing ran.
    planning_failed: bool = False
    confidence: float | None = None
    groundedness: float | None = None

    prompt_template: str | None = None
    prompt_version: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def degraded(self) -> bool:
        """True when the turn completed but not on the happy path.

        Every degradation is counted (ADR-009). A system that degrades silently cannot be
        operated -- this property is what the ``aura_turn_degraded_total`` counter reads.
        """
        return bool(
            self.fallback_count
            or self.tainted
            or self.retrieval.returned_nothing
            or any(not t.ok for t in self.tools)
            or self.planning_failed
        )

    @property
    def degraded_reasons(self) -> tuple[str, ...]:
        """Why :attr:`degraded` is true, as the same bounded slugs the counter is labelled by."""
        reasons = (
            ("provider_fallback", self.fallback_count),
            ("tainted_context", self.tainted),
            ("empty_retrieval", self.retrieval.returned_nothing),
            ("tool_failure", any(not t.ok for t in self.tools)),
            ("planning_failed", self.planning_failed),
        )
        return tuple(reason for reason, hit in reasons if hit)

    def with_timing(self, **stages: float) -> "TurnTelemetry":
        return replace(self, timings=replace(self.timings, **stages))

    def as_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id.value,
            "turn_id": self.turn_id.value,
            "session_id": self.session_id.value if self.session_id else None,
            "route": self.route,
            "agent": self.agent,
            "strategy": self.strategy,
            "outcome": str(self.outcome),
            "error_kind": self.error_kind,
            "provider": self.provider,
            "model": self.model,
            "fallback_count": self.fallback_count,
            "providers_attempted": list(self.providers_attempted),
            "cache": {
                "hit": self.cache.hit,
                "tier": self.cache.tier,
                "similarity": self.cache.similarity,
            },
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": self.total_tokens,
            },
            "cost_usd": round(self.cost_usd, 6),
            "latency": {"total_ms": round(self.total_ms, 2), "ttft_ms": self.ttft_ms,
                        **{k: round(v, 2) for k, v in self.timings.as_dict().items()}},
            "retrieval": {
                "candidates": self.retrieval.candidates,
                "returned": self.retrieval.returned,
                "top_score": round(self.retrieval.top_score, 4),
                "below_threshold": self.retrieval.below_threshold,
                "quarantined": self.retrieval.quarantined,
                "citations": self.retrieval.citations,
                "strategy": self.retrieval.strategy,
            },
            "memories_recalled": self.memories_recalled,
            "cag": {
                "tokens": self.cag_tokens,
                "docs": self.cag_docs,
                "identity": self.cag_identity,
                "cache_hit": self.cag_cache_hit,
            },
            "context": {"tokens": self.context_tokens, "dropped": self.context_dropped},
            "guards": list(self.guards),
            "tainted": self.tainted,
            "tools": [
                {"name": t.name, "ok": t.ok, "ms": round(t.duration_ms, 2),
                 "error_kind": t.error_kind, "allowed": t.allowed}
                for t in self.tools
            ],
            "plan": {"steps": self.plan_steps, "revisions": self.plan_revisions,
                     "failed": self.planning_failed},
            "confidence": self.confidence,
            "groundedness": self.groundedness,
            "prompt": {"template": self.prompt_template, "version": self.prompt_version},
            "degraded": self.degraded,
            "degraded_reasons": list(self.degraded_reasons),
            "started_at": self.started_at.isoformat(),
        }
