"""Prompt engine: versioned, testable, editable prompts (TD-11).

Prompts are data, not string literals in a service module. A prompt change is a registry
update with a version bump, traceable in telemetry and reversible by pinning.
"""

from .engine import PromptRegistry, PromptTemplate, PromptVariable, RenderedPrompt, variables
from .library import AGENT_PROMPT_IDS, BUILTIN_PROMPTS


def build_default_registry() -> PromptRegistry:
    """A registry pre-loaded with the built-in library."""
    registry = PromptRegistry()
    registry.register_all(BUILTIN_PROMPTS)
    return registry


__all__ = [
    "PromptRegistry",
    "PromptTemplate",
    "PromptVariable",
    "RenderedPrompt",
    "variables",
    "BUILTIN_PROMPTS",
    "AGENT_PROMPT_IDS",
    "build_default_registry",
]
