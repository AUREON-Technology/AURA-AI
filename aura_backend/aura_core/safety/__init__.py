"""Safety layer (ADR-006).

Two-sided guard pipeline: input, context (retrieved chunks / memories), tool results, and
output. The architectural point is that retrieved content is **data, never instruction** --
the defect behind SEC-02, where documents and memories are concatenated into the system prompt.
"""

from .guards import (
    EncodingGuard,
    GroundednessGuard,
    Guard,
    JailbreakGuard,
    PIIGuard,
    PromptInjectionGuard,
    SecretGuard,
    SizeGuard,
    SystemPromptLeakGuard,
    default_context_guards,
    default_input_guards,
    default_output_guards,
)
from .pipeline import SafetyDecision, SafetyPipeline

__all__ = [
    "SafetyPipeline",
    "SafetyDecision",
    "Guard",
    "SizeGuard",
    "EncodingGuard",
    "PromptInjectionGuard",
    "JailbreakGuard",
    "PIIGuard",
    "SecretGuard",
    "SystemPromptLeakGuard",
    "GroundednessGuard",
    "default_input_guards",
    "default_context_guards",
    "default_output_guards",
]
