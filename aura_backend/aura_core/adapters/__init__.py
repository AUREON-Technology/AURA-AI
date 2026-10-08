"""Infrastructure adapters.

In-memory implementations are the reference and the test substrate; Qdrant, Postgres, and Redis
adapters implement the same ports and are selected by configuration. Vendor SDKs are imported
lazily inside each adapter so an unused one never has to be installed.
"""

from .memory_stores import (
    InMemoryDocumentStore,
    InMemoryEngineerRunStore,
    InMemoryWorkspaceStore,
    InMemoryGraphStore,
    InMemoryMemoryStore,
    InMemoryPersonalOsStore,
    InMemoryVectorStore,
)
from .runtime import InMemoryAuditSink, InMemoryEventBus, SystemClock

__all__ = [
    "InMemoryVectorStore",
    "InMemoryDocumentStore",
    "InMemoryMemoryStore",
    "InMemoryPersonalOsStore",
    "InMemoryEngineerRunStore",
    "InMemoryWorkspaceStore",
    "InMemoryGraphStore",
    "InMemoryEventBus",
    "InMemoryAuditSink",
    "SystemClock",
]
