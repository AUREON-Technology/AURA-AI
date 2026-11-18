"""Prompt engine: versioned, testable, editable prompts (TD-11).

Prompts are data, not string literals in a service module. A prompt change is a registry
update with a version bump, traceable in telemetry and reversible by pinning.
"""

from __future__ import annotations

from ..ports.prompts import PromptPinStore
from .engine import (
    PIN_SYNC_INTERVAL_S,
    PromptRegistry,
    PromptTemplate,
    PromptVariable,
    RenderedPrompt,
    variables,
)
from .library import AGENT_PROMPT_IDS, BUILTIN_PROMPTS


def build_default_registry(*, pins: PromptPinStore | None = None) -> PromptRegistry:
    """A registry pre-loaded with the built-in library, its pins backed by ``pins`` if given."""
    registry = PromptRegistry(pins=pins)
    registry.register_all(BUILTIN_PROMPTS)
    return registry


__all__ = [
    "PIN_SYNC_INTERVAL_S",
    "PromptRegistry",
    "PromptTemplate",
    "PromptVariable",
    "RenderedPrompt",
    "variables",
    "BUILTIN_PROMPTS",
    "AGENT_PROMPT_IDS",
    "build_default_registry",
]
