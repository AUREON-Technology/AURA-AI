"""Embedding providers.

Three adapters behind one port:

``HashEmbeddingProvider``            deterministic, stdlib-only. **Test and local-dev only.**
``OpenAICompatibleEmbeddingProvider`` any ``/v1/embeddings`` endpoint: vLLM, Ollama,
                                      text-embeddings-inference, OpenAI, Jina, Voyage.
``SentenceTransformerProvider``       in-process models, for a VPS with no separate embedding
                                      service. Optional dependency, lazily imported.

Two rules apply to all of them, and both are fixes for real defects:

1. **A failure is an error, never a vector.** ``services/rag.py:154`` substitutes
   ``[0.0] * dim`` when embedding fails; that vector is then written to the index and used for
   search, producing a corpus that looks ingested and is unsearchable (TD-03).
2. **The model id is part of the result.** Vectors are meaningless without knowing which model
   produced them, and :class:`~aura_core.domain.knowledge.IndexIdentity` uses that id to keep
   embedding spaces from mixing (ADR-007).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import struct
import time
from typing import Any, Mapping, Sequence

from ..domain.errors import EmbeddingError
from ..domain.llm import EmbeddingResult, TokenUsage
from ..ports.llm import HealthStatus
from .providers.base import http_client

__all__ = [
    "HashEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "SentenceTransformerProvider",
    "cosine_similarity",
]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity without numpy.

    Returns 0.0 for a zero-norm vector rather than raising or producing NaN -- but note that no
    provider in this module can *produce* a zero vector, so this only guards against callers
    passing degenerate input.
    """
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = norm_a = norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


class HashEmbeddingProvider:
    """Deterministic character-n-gram hashing embedding.

    **Not semantic.** Two paraphrases with no shared character n-grams score near zero. It
    exists so that every retrieval, memory, and cache code path can be tested end-to-end with no
    model, no network, and no GPU -- and it is reproducible, so a retrieval test asserting an
    exact ranking stays stable across machines.

    ``AuraSettings.validate()`` refuses to let this run in staging or production. Shipping it as
    a "fallback" would recreate exactly the failure mode this architecture removes: a system that
    appears to work while its retrieval is meaningless.

    Method: hash 3-, 4-, and 5-character shingles into ``dimension`` buckets with signed
    contributions (the signed-hashing trick keeps collisions from systematically inflating
    magnitude), then L2-normalise so cosine similarity is well behaved.
    """

    def __init__(self, *, dimension: int = 384, model_id: str = "hash-embed-v1") -> None:
        if dimension < 16:
            raise ValueError("dimension must be at least 16")
        self._dimension = dimension
        self._model_id = model_id

    @property
    def name(self) -> str:
        return "local-embeddings"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_batch_size(self) -> int:
        return 512

    def _vector(self, text: str) -> tuple[float, ...]:
        buckets = [0.0] * self._dimension
        normalised = " ".join(text.lower().split())
        if not normalised:
            # An empty string has no features. Returning a fixed unit vector keeps the "never
            # emit a zero vector" invariant without pretending the text had content.
            buckets[0] = 1.0
            return tuple(buckets)

        padded = f" {normalised} "
        for size in (3, 4, 5):
            for i in range(len(padded) - size + 1):
                shingle = padded[i : i + size]
                digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
                (value,) = struct.unpack("<Q", digest)
                index = value % self._dimension
                sign = 1.0 if (value >> 63) & 1 else -1.0
                # Longer shingles are more specific and are weighted accordingly.
                buckets[index] += sign * (size / 5.0)

        norm = math.sqrt(sum(v * v for v in buckets))
        if norm == 0.0:  # pragma: no cover - only reachable on exact signed cancellation
            buckets[0] = 1.0
            return tuple(buckets)
        return tuple(v / norm for v in buckets)

    async def embed(self, texts: Sequence[str], *, for_query: bool = False) -> EmbeddingResult:
        if not texts:
            raise EmbeddingError("embed() requires at least one text")
        return EmbeddingResult(
            vectors=tuple(self._vector(t) for t in texts),
            model_id=self._model_id,
            dimension=self._dimension,
            usage=TokenUsage(prompt_tokens=sum(max(1, len(t) // 4) for t in texts)),
            provider=self.name,
        )

    async def health(self) -> HealthStatus:
        return HealthStatus(True, "in-process", 0.0)

    async def close(self) -> None:
        return None


class OpenAICompatibleEmbeddingProvider:
    """Any ``POST /v1/embeddings`` endpoint.

    One adapter covers every practical serving option, matching ADR-003's reasoning for chat.
    Batching is the point: the legacy pipeline embeds one chunk per call, so a 100 KB document
    costs ~111 serial round trips (~17 s). Here, texts are split into ``max_batch_size`` groups
    and the groups are dispatched concurrently.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        dimension: int,
        api_key: str = "",
        max_batch_size: int = 64,
        max_parallel_batches: int = 4,
        timeout_s: float = 30.0,
        query_prefix: str = "",
        document_prefix: str = "",
    ) -> None:
        self._name = name
        self._url = base_url.rstrip("/")
        self._model = model
        self._dimension = dimension
        self._api_key = api_key
        self._max_batch = max(1, max_batch_size)
        self._parallel = max(1, max_parallel_batches)
        self._timeout_s = timeout_s
        # Asymmetric models (E5, BGE, GTE) need distinct prefixes for queries and documents;
        # omitting them costs several points of recall.
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._client: Any = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_batch_size(self) -> int:
        return self._max_batch

    async def _get_client(self) -> Any:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = http_client(base_url=self._url, timeout_s=self._timeout_s, headers=headers)
        return self._client

    async def _embed_batch(self, batch: Sequence[str]) -> list[tuple[float, ...]]:
        client = await self._get_client()
        try:
            response = await client.post(
                "/embeddings", json={"model": self._model, "input": list(batch)}
            )
        except Exception as exc:
            raise EmbeddingError(
                f"{self._name}: embedding request failed ({type(exc).__name__})",
                cause=exc,
                details={"provider": self._name, "batch_size": len(batch)},
            ) from exc

        if response.status_code >= 400:
            raise EmbeddingError(
                f"{self._name}: embedding endpoint returned HTTP {response.status_code}",
                retryable=response.status_code >= 500 or response.status_code == 429,
                details={"provider": self._name, "status": response.status_code,
                         "body": response.text[:300]},
            )

        body = response.json()
        data = body.get("data") or []
        if len(data) != len(batch):
            raise EmbeddingError(
                f"{self._name}: expected {len(batch)} embeddings, received {len(data)}",
                details={"provider": self._name},
            )

        # Preserve request order: the API returns an `index` field and is not required to
        # respond in order. Getting this wrong silently misattributes every vector.
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
        vectors: list[tuple[float, ...]] = []
        for item in ordered:
            vector = tuple(float(v) for v in item.get("embedding") or ())
            if len(vector) != self._dimension:
                raise EmbeddingError(
                    f"{self._name}: model returned dimension {len(vector)}, configured "
                    f"{self._dimension} -- correct AURA_EMBEDDING_DIMENSION and rebuild the index",
                    retryable=False,
                    details={"provider": self._name, "expected": self._dimension, "actual": len(vector)},
                )
            vectors.append(vector)
        return vectors

    async def embed(self, texts: Sequence[str], *, for_query: bool = False) -> EmbeddingResult:
        if not texts:
            raise EmbeddingError("embed() requires at least one text")

        prefix = self._query_prefix if for_query else self._document_prefix
        prepared = [f"{prefix}{t}" for t in texts] if prefix else list(texts)
        batches = [prepared[i : i + self._max_batch] for i in range(0, len(prepared), self._max_batch)]

        semaphore = asyncio.Semaphore(self._parallel)

        async def run(batch: Sequence[str]) -> list[tuple[float, ...]]:
            async with semaphore:
                return await self._embed_batch(batch)

        results = await asyncio.gather(*(run(b) for b in batches))
        vectors = [vector for group in results for vector in group]

        return EmbeddingResult(
            vectors=tuple(vectors),
            model_id=self._model,
            dimension=self._dimension,
            usage=TokenUsage(prompt_tokens=sum(max(1, len(t) // 4) for t in prepared)),
            provider=self._name,
        )

    async def health(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            await self._embed_batch(["health"])
            return HealthStatus(True, "ok", (time.perf_counter() - started) * 1000.0)
        except Exception as exc:
            return HealthStatus(False, type(exc).__name__, (time.perf_counter() - started) * 1000.0)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class SentenceTransformerProvider:
    """In-process ``sentence-transformers`` models.

    For a single VPS with no separate embedding service this avoids a network hop entirely. The
    cost is resident memory per worker (~90-400 MB depending on model), which is why the legacy
    code disables it outside debug mode "to prevent OOM crash" (``rag.py:141``) -- treating the
    symptom rather than sizing the deployment.

    Two properties make it usable here where it was not before:
      * the model is loaded **once, lazily**, on first use, not at import;
      * encoding runs in a worker thread, so it never blocks the event loop. ``encode`` is
        CPU-bound C code that releases the GIL, so a thread is the right primitive.
    """

    def __init__(
        self,
        *,
        name: str = "sentence-transformers",
        model: str = "BAAI/bge-small-en-v1.5",
        dimension: int = 384,
        max_batch_size: int = 32,
        normalize: bool = True,
        query_prefix: str = "",
        document_prefix: str = "",
    ) -> None:
        self._name = name
        self._model_name = model
        self._dimension = dimension
        self._max_batch = max_batch_size
        self._normalize = normalize
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._model: Any = None
        self._load_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_id(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_batch_size(self) -> int:
        return self._max_batch

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        # The lock prevents N concurrent first-requests from each loading a copy of the weights.
        async with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "sentence-transformers is not installed; install it or configure an "
                    "OpenAI-compatible embedding endpoint",
                    retryable=False,
                    details={"provider": self._name},
                ) from exc
            try:
                self._model = await asyncio.to_thread(SentenceTransformer, self._model_name)
            except Exception as exc:
                raise EmbeddingError(
                    f"{self._name}: failed to load {self._model_name}",
                    cause=exc,
                    retryable=False,
                ) from exc
            actual = int(self._model.get_sentence_embedding_dimension())
            if actual != self._dimension:
                raise EmbeddingError(
                    f"{self._name}: {self._model_name} produces dimension {actual}, configured "
                    f"{self._dimension}",
                    retryable=False,
                    details={"expected": self._dimension, "actual": actual},
                )
            return self._model

    async def embed(self, texts: Sequence[str], *, for_query: bool = False) -> EmbeddingResult:
        if not texts:
            raise EmbeddingError("embed() requires at least one text")
        model = await self._ensure_model()
        prefix = self._query_prefix if for_query else self._document_prefix
        prepared = [f"{prefix}{t}" for t in texts] if prefix else list(texts)

        def encode() -> list[list[float]]:
            output = model.encode(
                prepared,
                batch_size=self._max_batch,
                normalize_embeddings=self._normalize,
                show_progress_bar=False,
            )
            return [list(map(float, row)) for row in output]

        try:
            vectors = await asyncio.to_thread(encode)
        except Exception as exc:
            raise EmbeddingError(
                f"{self._name}: encoding failed", cause=exc, details={"count": len(prepared)}
            ) from exc

        return EmbeddingResult(
            vectors=tuple(tuple(v) for v in vectors),
            model_id=self._model_name,
            dimension=self._dimension,
            usage=TokenUsage(prompt_tokens=sum(max(1, len(t) // 4) for t in prepared)),
            provider=self._name,
        )

    async def health(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            await self._ensure_model()
            return HealthStatus(True, "loaded", (time.perf_counter() - started) * 1000.0)
        except Exception as exc:
            return HealthStatus(False, str(exc)[:120], (time.perf_counter() - started) * 1000.0)

    async def close(self) -> None:
        self._model = None


def build_embedding_provider(config: Mapping[str, Any]):  # pragma: no cover - thin factory
    """Construct an embedding provider from a settings mapping. Used by the container."""
    kind = config.get("kind", "hash")
    if kind == "hash":
        return HashEmbeddingProvider(
            dimension=int(config.get("dimension", 384)),
            model_id=str(config.get("model", "hash-embed-v1")),
        )
    if kind == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(
            name=str(config.get("name", "embeddings")),
            base_url=str(config["base_url"]),
            model=str(config["model"]),
            dimension=int(config["dimension"]),
            api_key=str(config.get("api_key", "")),
            max_batch_size=int(config.get("max_batch_size", 64)),
            query_prefix=str(config.get("query_prefix", "")),
            document_prefix=str(config.get("document_prefix", "")),
        )
    if kind == "sentence_transformers":
        return SentenceTransformerProvider(
            model=str(config["model"]),
            dimension=int(config["dimension"]),
            max_batch_size=int(config.get("max_batch_size", 32)),
            query_prefix=str(config.get("query_prefix", "")),
            document_prefix=str(config.get("document_prefix", "")),
        )
    raise EmbeddingError(f"unknown embedding provider kind {kind!r}", retryable=False)
