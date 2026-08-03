"""Safety domain types (ADR-006).

A guard never mutates and never decides on its own whether the turn proceeds -- it returns a
:class:`GuardVerdict`. The pipeline aggregates verdicts and applies the strictest action. That
separation is what makes each guard ~40 lines, independently testable, and safely composable.

The action ladder is ordered by severity so aggregation is a ``max``:

    ALLOW < FLAG < REDACT < QUARANTINE < BLOCK
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "GuardAction",
    "GuardStage",
    "Span",
    "GuardVerdict",
    "SafetyReport",
]


class GuardAction(int, enum.Enum):
    """What a guard wants done. Ordered by severity."""

    ALLOW = 0
    FLAG = 1        # record and continue; used for advisory signals like groundedness
    REDACT = 2      # remove matched spans, continue with the remainder
    QUARANTINE = 3  # drop this item from context and taint the turn
    BLOCK = 4       # refuse the request or response outright

    def __str__(self) -> str:
        return self.name.lower()


class GuardStage(str, enum.Enum):
    INPUT = "input"        # the user's own message
    CONTEXT = "context"    # retrieved chunks, memories -- where injection lives
    TOOL_RESULT = "tool_result"
    OUTPUT = "output"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Span:
    """A matched region. ``start``/``end`` index the *original* text."""

    start: int
    end: int
    label: str
    excerpt: str = ""

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("invalid span bounds")

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """One guard's opinion.

    ``sanitised`` is populated only for ``REDACT``; the pipeline uses it as the replacement text.
    ``reason`` is a low-cardinality slug (metric label), while ``detail`` is human-readable and
    stays server-side -- echoing detection detail to the caller would turn error responses into
    an oracle for probing guard thresholds.
    """

    guard: str
    action: GuardAction
    reason: str = "ok"
    confidence: float = 1.0
    spans: tuple[Span, ...] = ()
    sanitised: str | None = None
    detail: str = ""
    duration_ms: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        if self.action is GuardAction.REDACT and self.sanitised is None:
            raise ValueError("REDACT verdicts must supply sanitised text")

    @classmethod
    def allow(cls, guard: str, **kw: Any) -> "GuardVerdict":
        return cls(guard=guard, action=GuardAction.ALLOW, **kw)

    @property
    def is_blocking(self) -> bool:
        return self.action is GuardAction.BLOCK

    @property
    def passed(self) -> bool:
        return self.action is GuardAction.ALLOW


@dataclass(frozen=True, slots=True)
class SafetyReport:
    """Aggregate of every guard that ran at one stage."""

    stage: GuardStage
    verdicts: tuple[GuardVerdict, ...] = ()
    text: str = ""
    original_text: str = ""
    duration_ms: float = 0.0

    @classmethod
    def clean(cls, stage: GuardStage, text: str) -> "SafetyReport":
        return cls(stage=stage, text=text, original_text=text)

    @property
    def action(self) -> GuardAction:
        """Strictest verdict wins. Fail-closed by construction."""
        if not self.verdicts:
            return GuardAction.ALLOW
        return max(v.action for v in self.verdicts)

    @property
    def blocked(self) -> bool:
        return self.action is GuardAction.BLOCK

    @property
    def quarantined(self) -> bool:
        return self.action is GuardAction.QUARANTINE

    @property
    def modified(self) -> bool:
        return self.text != self.original_text

    @property
    def triggered(self) -> tuple[GuardVerdict, ...]:
        return tuple(v for v in self.verdicts if not v.passed)

    @property
    def blocking_guard(self) -> str | None:
        for verdict in self.verdicts:
            if verdict.is_blocking:
                return verdict.guard
        return None

    def reasons(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(v.reason for v in self.triggered))

    def summary(self) -> list[dict[str, Any]]:
        """Content-free summary for telemetry and audit."""
        return [
            {
                "guard": v.guard,
                "action": str(v.action),
                "reason": v.reason,
                "confidence": round(v.confidence, 3),
                "spans": len(v.spans),
            }
            for v in self.triggered
        ]

    def merged_with(self, verdicts: Iterable[GuardVerdict], text: str) -> "SafetyReport":
        return SafetyReport(
            stage=self.stage,
            verdicts=self.verdicts + tuple(verdicts),
            text=text,
            original_text=self.original_text,
            duration_ms=self.duration_ms,
        )


def redact_spans(text: str, spans: Sequence[Span], *, placeholder: str = "[REDACTED:{label}]") -> str:
    """Replace spans right-to-left so earlier offsets stay valid.

    Overlapping spans are merged first; without that, two guards matching the same region would
    corrupt each other's offsets and produce garbled output.
    """
    if not spans:
        return text
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    merged: list[Span] = []
    for span in ordered:
        if merged and span.start <= merged[-1].end:
            previous = merged[-1]
            # When two guards match overlapping regions (a card number also matches the phone
            # pattern), keep the label of the longer match -- it is the more specific detection,
            # and a placeholder reading "[REDACTED:card]" is more useful to a reader than "mixed".
            label = previous.label if previous.length >= span.length else span.label
            merged[-1] = Span(previous.start, max(previous.end, span.end), label)
        else:
            merged.append(span)

    result = text
    for span in reversed(merged):
        result = result[: span.start] + placeholder.format(label=span.label) + result[span.end :]
    return result
