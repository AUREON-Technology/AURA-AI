"""Hybrid LLM layer: providers, routing, caching, embeddings (ADR-003).

Import cost is deliberate: only the router, cache, circuit breaker, and embedding adapters are
re-exported here. Concrete chat providers are reached through
:func:`aura_core.llm.registry.build_provider`, which imports each adapter lazily, so a
local-only deployment never loads the cloud vendor modules.
"""

from .admission import AdmissionController, AdmissionTicket, PoolLimits
from .cache import CacheHit, InMemoryCache, LLMCache
from .circuit import CircuitBreaker, CircuitState, LatencyTracker, ProviderStats
from .embeddings import (
    HashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    SentenceTransformerProvider,
    cosine_similarity,
)
from .registry import (
    admission_limits_from_settings,
    build_provider,
    build_providers,
    capabilities_from_config,
)
from .router import ProviderRouter, RoutingDecision, TurnBudget
from .workload import current_workload, effective_workload, workload_for_turn, workload_scope

__all__ = [
    "ProviderRouter",
    "RoutingDecision",
    "TurnBudget",
    "AdmissionController",
    "AdmissionTicket",
    "PoolLimits",
    "admission_limits_from_settings",
    "current_workload",
    "effective_workload",
    "workload_for_turn",
    "workload_scope",
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
