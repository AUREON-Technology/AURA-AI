"""Hybrid LLM layer: providers, routing, caching, embeddings (ADR-003).

Import cost is deliberate: only the router, cache, circuit breaker, and embedding adapters are
re-exported here. Concrete chat providers are reached through
:func:`aura_core.llm.registry.build_provider`, which imports each adapter lazily, so a
local-only deployment never loads the cloud vendor modules.
"""

from .cache import CacheHit, InMemoryCache, LLMCache
from .circuit import CircuitBreaker, CircuitState, LatencyTracker, ProviderStats
from .embeddings import (
    HashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    SentenceTransformerProvider,
    cosine_similarity,
)
from .registry import build_provider, build_providers, capabilities_from_config
from .router import ProviderRouter, RoutingDecision, TurnBudget

__all__ = [
    "ProviderRouter",
    "RoutingDecision",
    "TurnBudget",
    "LLMCache",
    "CacheHit",
    "InMemoryCache",
    "CircuitBreaker",
    "CircuitState",
    "LatencyTracker",
    "ProviderStats",
    "HashEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "SentenceTransformerProvider",
    "cosine_similarity",
    "build_provider",
    "build_providers",
    "capabilities_from_config",
]
