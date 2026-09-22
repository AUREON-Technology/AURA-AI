"""Proactive Intelligence - event → observe → detect → reason → prioritize → approve → execute → verify → learn.

The continuous intelligence loop that monitors system quality, detects degradation,
and triggers automatic remediation cycles without requiring manual intervention.

Three core abstractions:
- Event: Something that happened in the system (user turn, retrieval result, tool output, etc.)
- Detection: Identifies quality problems from events
- Action: Automatic remediation or human-in-the-loop approval flow
"""

from __future__ import annotations

from .event import ProactiveEvent
from .signal import QualitySignal
from .detector import DetectionRule
from .loop import ProactiveLoop, build_proactive_loop

__all__ = [
    "ProactiveEvent",
    "QualitySignal",
    "DetectionRule",
    "ProactiveLoop",
    "build_proactive_loop",
]