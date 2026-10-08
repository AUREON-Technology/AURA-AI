"""LLM and embedding ports.

The seam that makes ADR-003 real. Everything above this line speaks
:mod:`aura_core.domain.llm` value objects; everything below translates to a vendor wire format.

Design notes that adapters must honour:

* **Async only.** Every method is a coroutine. Synchronous SDKs are wrapped with
  ``anyio.to_thread.run_sync`` inside the adapter, never called from a coroutine directly --
  the defect described in the Performance Report §2 that caps concurrency at ~4 requests.
* **Errors are domain errors.** Adapters raise :class:`~aura_core.domain.errors.ProviderError`
  or :class:`~aura_core.domain.errors.EmbeddingError`, never vendor exceptions and never a
  silent empty result.
* **Capabilities are declared honestly.** The router filters on them; a wrong
  ``context_window`` produces truncation surprises rather than a clean rejection.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence, runtime_checkable

from ..domain.llm import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingResult,
    ProviderCapabilities,
    StreamChunk,
)

__all__ = ["LLMProvider", "EmbeddingProvider", "Reranker", "HealthStatus"]


class HealthStatus:
    """Result of a provider health probe.

    A plain class rather than an enum because it carries diagnostics: the router logs *why* a
    provider was removed from candidacy, which is the difference between a useful alert and
    "provider down".
    """

    __slots__ = ("healthy", "detail", "latency_ms")

    def __init__(self, healthy: bool, detail: str = "", latency_ms: float = 0.0) -> None:
        self.healthy = healthy
        self.detail = detail
        self.latency_ms = latency_ms

    def __bool__(self) -> bool:
        return self.healthy

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"HealthStatus(healthy={self.healthy}, detail={self.detail!r}, latency_ms={self.latency_ms:.1f})"


@runtime_checkable
class LLMProvider(Protocol):
    """One model endpoint.

    A *provider instance* is one (endpoint, model) pair, not a vendor. ``qwen2.5:7b-instruct``
    on the local Ollama and ``qwen2.5:32b-instruct`` on a GPU host are two providers sharing an
    adapter class. That granularity is what lets the router reason about latency and quality per
    model rather than per host.
    """

    @property
    def name(self) -> str:
        """Stable identifier used as a metric label and in configuration. Low cardinality."""
        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        ...

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Generate a full response.

        Raises :class:`ProviderError` on any failure; the router decides whether to try the next
        candidate.
        """
        ...

    def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        """Stream a response incrementally.

        Not an ``async def`` returning an iterator: adapters implement this as an async
        generator, so the signature is a plain method returning ``AsyncIterator``.

        Contract: the adapter must not yield any chunk until the upstream connection has been
        established successfully. Once the first chunk is yielded, failover is no longer
        possible without duplicating output, so connection errors have to surface *before* the
        stream starts.
        """
        ...

    async def health(self) -> HealthStatus:
        """Cheap liveness probe. Must not consume meaningful quota or cost."""
        ...

    async def close(self) -> None:
        """Release connections. Idempotent."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Text embeddings.

    Separate from :class:`LLMProvider` because the lifecycles differ: embeddings are pinned to
    an index (ADR-007) and must not change when the chat model does. A provider that does both
    simply implements both protocols.
    """

    @property
    def name(self) -> str:
        ...

    @property
    def model_id(self) -> str:
        """Identity recorded in the index name. Changing it forces a rebuild, by design."""
        ...

    @property
    def dimension(self) -> int:
        ...

    @property
    def max_batch_size(self) -> int:
        """Largest batch the endpoint accepts. The pipeline chunks to this size and dispatches
        batches concurrently -- the fix for the serial per-chunk embedding that makes ingestion
        ~17 s per 100 KB document today."""
        ...

    async def embed(self, texts: Sequence[str], *, for_query: bool = False) -> EmbeddingResult:
        """Embed a batch.

        ``for_query`` selects the asymmetric task type where the model supports one (query vs.
        document embeddings); adapters for symmetric models ignore it.

        Raises :class:`EmbeddingError` on failure. **Never** returns a zero vector -- see TD-03:
        a synthetic vector written to the index produces a silently unsearchable corpus.
        """
        ...

    async def health(self) -> HealthStatus:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class Reranker(Protocol):
    """Second-stage relevance scoring over retrieval candidates (ADR-005 stage 3).

    Kept as its own port so the zero-dependency heuristic reranker, a cross-encoder, and an
    LLM-as-reranker are interchangeable by configuration.
    """

    @property
    def name(self) -> str:
        ...

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_k: int | None = None
    ) -> Sequence[tuple[int, float]]:
        """Return ``(original_index, score)`` pairs ordered best-first.

        Returning indices rather than reordered documents keeps the caller's richer objects
        (chunks with provenance) intact without the reranker needing to know about them.
        """
        ...
