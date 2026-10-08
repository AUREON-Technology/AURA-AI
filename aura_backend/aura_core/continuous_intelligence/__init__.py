"""Continuous Intelligence Loop - the full closed loop connecting all phases.

Orchestrates: USER/SYSTEM EVENTS → OBSERVATION → FEEDBACK → DATASET → EVALUATION → 
TRAINING → REGRESSION GATE → HUMAN/POLICY APPROVAL → MODEL REGISTRY → 
CANARY → PRODUCTION → MONITORING → NEW OBSERVATIONS

This is the master loop that wraps all 13 previous phases into an operational
cycle, enabling continuous improvement without manual intervention.
"""

from __future__ import annotations

__all__ = [
    "ContinuousIntelligenceLoop",
    "IntelligenceCycle",
    "LoopPhase",
    "run_continuous_loop",
]