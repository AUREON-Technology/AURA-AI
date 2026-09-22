"""Quality signal: a detected problem or opportunity from proactive monitoring.

A quality signal is what the detection engine produces from events. It has a severity
and suggested action, and it flows into the prioritize → approve → execute pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class SignalSeverity:
    """Severity levels for quality signals."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class QualitySignal:
    """A detected quality problem or opportunity from the monitoring loop.

    Emitted by :class:`~aura_core.proactive.detector.DetectionRule` after observing
    one or more :class:`~aura_core.proactive.event.ProactiveEvent` s.

    Attributes:
        id: Unique signal identifier.
        severity: INFO / WARNING / CRITICAL.
        title: Human-readable summary.
        description: Detailed explanation.
        metric_name: The evaluation metric that degraded (e.g. "recall_at_k", "token_f1").
        metric_value: Current measured value.
        baseline_value: The baseline/reference value for comparison.
        turn_ids: Event turn IDs that contributed to this signal.
        suggested_action: One of "monitor", "investigate", "retrain", "index_refresh", "rollback".
        auto_approvable: Whether the loop can auto-approve remediation.
        metadata: Arbitrary structured data for downstream consumers.
    """

    id: str
    severity: str
    title: str
    description: str
    metric_name: str
    metric_value: float
    baseline_value: float
    turn_ids: tuple[str, ...] = field(default_factory=tuple)
    suggested_action: str = "monitor"
    auto_approvable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.id is None or self.id == "":
            self.id = f"signal-{int(time.time()*1000)}"
        if self.severity not in (SignalSeverity.INFO, SignalSeverity.WARNING, SignalSeverity.CRITICAL):
            self.severity = SignalSeverity.WARNING

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "baseline_value": self.baseline_value,
            "turn_ids": list(self.turn_ids),
            "suggested_action": self.suggested_action,
            "auto_approvable": self.auto_approvable,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QualitySignal":
        return cls(
            id=data["id"],
            severity=data["severity"],
            title=data["title"],
            description=data["description"],
            metric_name=data["metric_name"],
            metric_value=float(data["metric_value"]),
            baseline_value=float(data["baseline_value"]),
            turn_ids=tuple(data.get("turn_ids", [])),
            suggested_action=data.get("suggested_action", "monitor"),
            auto_approvable=bool(data.get("auto_approvable", False)),
            metadata=data.get("metadata", {}),
        )

    @property
    def is_degraded(self) -> bool:
        """Return True if the metric has fallen below baseline."""
        return self.metric_value < self.baseline_value

    @property
    def severity_weight(self) -> float:
        """Numeric weight for sorting/signaling: INFO=0.1, WARNING=1.0, CRITICAL=5.0."""
        weights = {SignalSeverity.INFO: 0.1, SignalSeverity.WARNING: 1.0, SignalSeverity.CRITICAL: 5.0}
        return weights.get(self.severity, 1.0)