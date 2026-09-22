"""Proactive event: something that happened in the system that we want to monitor.

Every event carries enough context to derive quality signals and trigger detections.
Events flow from the kernel through the observability layer into the proactive loop.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProactiveEvent:
    """One observed event in the system.

    Attributes:
        id: Unique event identifier (auto-generated timestamp-based).
        kind: Category of event (e.g. "turn_complete", "retrieval", "tool_output", "answer_quality").
        timestamp: Epoch seconds when the event occurred.
        payload: Event-specific data - structured, not raw strings when possible.
        quality_signal: Derived quality metric from this event (optional, computed by detections).
        tags: Free-form tags for grouping events (e.g. "route:eval", "model:gpt-4").
    """

    id: str
    kind: str
    timestamp: float
    payload: dict[str, Any] = field(default_factory=dict)
    quality_signal: float | None = None  # -1 (bad) to 1 (good), None = unscored yet
    tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"event-{int(time.time()*1000)}-{hash(frozenset(self.payload.items())) % 10000:04d}"
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "quality_signal": self.quality_signal,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProactiveEvent":
        return cls(
            id=data["id"],
            kind=data["kind"],
            timestamp=data["timestamp"],
            payload=data.get("payload", {}),
            quality_signal=data.get("quality_signal"),
            tags=tuple(data.get("tags", [])),
        )