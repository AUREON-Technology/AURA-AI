"""Typed configuration for the AURA core.

See :mod:`aura_core.config.settings`. The public surface is deliberately small: construct once
at startup with :func:`load_settings`, pass the result down, never read the environment again.
"""

from .settings import (
    AuraSettings,
    Environment,
    InfrastructureSettings,
    KnowledgeSettings,
    LLMSettings,
    LearningSettings,
    MemorySettings,
    ObservabilitySettings,
    ProviderConfig,
    ReasoningSettings,
    SafetySettings,
    SecuritySettings,
    ToolSettings,
    load_settings,
)

__all__ = [
    "AuraSettings",
    "Environment",
    "LLMSettings",
    "ProviderConfig",
    "KnowledgeSettings",
    "MemorySettings",
    "SafetySettings",
    "ToolSettings",
    "ReasoningSettings",
    "ObservabilitySettings",
    "SecuritySettings",
    "InfrastructureSettings",
    "LearningSettings",
    "load_settings",
]
