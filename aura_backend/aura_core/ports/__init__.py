"""AURA port layer -- the interfaces between policy and infrastructure.

Like :mod:`aura_core.domain`, this package imports only the standard library and the domain
layer. Ports are ``typing.Protocol`` classes: adapters satisfy them structurally, with no
inheritance and therefore no import from adapter to port at definition time.

Every port method that performs I/O is ``async``. That is not stylistic -- the current system's
concurrency ceiling of roughly four simultaneous AI requests comes entirely from synchronous
clients running inside coroutines (see ``docs/analysis/05-performance-report.md`` §2), and an
async-only port surface makes that mistake impossible to repeat above the adapter boundary.
"""

from .llm import EmbeddingProvider, HealthStatus, LLMProvider, Reranker
from .runtime import (
    AuditEvent,
    AuditSink,
    Clock,
    Closeable,
    EventBus,
    RateLimitDecision,
    RateLimiter,
    TelemetrySink,
    Tool,
    ToolContext,
)
from .storage import (
    Cache,
    DocumentStore,
    EntityExtractor,
    GraphStore,
    MemoryStore,
    VectorPoint,
    VectorStore,
)

__all__ = [
    "LLMProvider",
    "EmbeddingProvider",
    "Reranker",
    "HealthStatus",
    "VectorStore",
    "VectorPoint",
    "DocumentStore",
    "MemoryStore",
    "GraphStore",
    "EntityExtractor",
    "Cache",
    "Tool",
    "ToolContext",
    "EventBus",
    "Clock",
    "Closeable",
    "AuditSink",
    "AuditEvent",
    "TelemetrySink",
    "RateLimiter",
    "RateLimitDecision",
]
