"""Embedding providers and the two-tier response cache.

Two invariants carry real weight here:

* **No provider may emit a zero vector.** TD-03 in the legacy system returns one on failure, so
  every similarity is 0.0 and retrieval silently returns whatever came first.
* **A cache must never be able to fail or cross-contaminate a turn.** A corrupt entry is a miss;
  an embedding outage is a miss; and a namespace is part of the key, not a filter applied after.
"""

from __future__ import annotations

import asyncio
import json
import math

import pytest

from aura_core.domain import CompletionRequest, Conversation, Message, SamplingParams
from aura_core.domain.errors import EmbeddingError
from aura_core.domain.llm import CompletionResponse, FinishReason, TokenUsage
from aura_core.llm import HashEmbeddingProvider, InMemoryCache
from aura_core.llm.cache import LLMCache
from aura_core.llm.embeddings import (
    OpenAICompatibleEmbeddingProvider,
    SentenceTransformerProvider,
    cosine_similarity,
)

httpx = pytest.importorskip("httpx")


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self) -> None:
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_score_minus_one(self) -> None:
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_magnitude_does_not_affect_the_score(self) -> None:
        assert cosine_similarity([1.0, 1.0], [5.0, 5.0]) == pytest.approx(1.0)

    def test_a_zero_vector_scores_zero_rather_than_producing_nan(self) -> None:
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_a_dimension_mismatch_raises(self) -> None:
        """Silently comparing across dimensions would make a mis-provisioned index look fine."""
        with pytest.raises(ValueError) as excinfo:
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])
        assert "dimension mismatch" in str(excinfo.value)


class TestHashEmbeddingProvider:
    async def test_vectors_are_unit_length(self) -> None:
        result = await HashEmbeddingProvider(dimension=64).embed(["hello world"])
        norm = math.sqrt(sum(v * v for v in result.first))
        assert norm == pytest.approx(1.0)

    async def test_it_is_deterministic_across_instances(self) -> None:
        """Reproducibility is why a retrieval test can assert an exact ranking."""
        a = await HashEmbeddingProvider(dimension=64).embed(["annual leave policy"])
        b = await HashEmbeddingProvider(dimension=64).embed(["annual leave policy"])
        assert a.first == b.first

    async def test_an_empty_string_still_produces_a_unit_vector(self) -> None:
        """The 'never emit a zero vector' invariant (TD-03) holds even for empty input."""
        result = await HashEmbeddingProvider(dimension=32).embed([""])
        assert math.sqrt(sum(v * v for v in result.first)) == pytest.approx(1.0)

    async def test_whitespace_is_normalised(self) -> None:
        provider = HashEmbeddingProvider(dimension=64)
        spaced = await provider.embed(["hello   world"])
        plain = await provider.embed(["hello world"])
        assert spaced.first == plain.first

    async def test_case_is_folded(self) -> None:
        provider = HashEmbeddingProvider(dimension=64)
        assert (await provider.embed(["Hello"])).first == (await provider.embed(["hello"])).first

    async def test_shared_substrings_score_higher_than_unrelated_text(self) -> None:
        provider = HashEmbeddingProvider(dimension=256)
        result = await provider.embed([
            "annual leave policy", "annual leave entitlement", "quarterly revenue forecast",
        ])
        related = cosine_similarity(result.vectors[0], result.vectors[1])
        unrelated = cosine_similarity(result.vectors[0], result.vectors[2])
        assert related > unrelated

    async def test_a_batch_returns_one_vector_per_text(self) -> None:
        result = await HashEmbeddingProvider(dimension=32).embed(["a", "b", "c"])
        assert len(result.vectors) == 3
        assert result.dimension == 32
        assert result.usage.prompt_tokens >= 3

    async def test_an_empty_batch_is_an_error_not_an_empty_result(self) -> None:
        """Returning nothing would let a caller store zero vectors for a document."""
        with pytest.raises(EmbeddingError):
            await HashEmbeddingProvider().embed([])

    def test_a_tiny_dimension_is_refused(self) -> None:
        with pytest.raises(ValueError):
            HashEmbeddingProvider(dimension=8)

    async def test_it_reports_healthy_and_closes_cleanly(self) -> None:
        provider = HashEmbeddingProvider()
        assert (await provider.health()).healthy is True
        assert await provider.close() is None

    def test_it_advertises_its_identity(self) -> None:
        provider = HashEmbeddingProvider(dimension=128, model_id="hash-embed-v1")
        assert provider.name == "local-embeddings"
        assert provider.model_id == "hash-embed-v1"
        assert provider.dimension == 128
        assert provider.max_batch_size == 512


class TestOpenAICompatibleEmbeddings:
    def _provider(self, **overrides) -> OpenAICompatibleEmbeddingProvider:
        kwargs = dict(
            name="embeddings", base_url="https://embed.internal/v1",
            model="bge-small-en-v1.5", dimension=4,
        )
        kwargs.update(overrides)
        return OpenAICompatibleEmbeddingProvider(**kwargs)

    def _install(self, provider, handler) -> None:
        provider._client = httpx.AsyncClient(
            base_url="https://embed.internal/v1", transport=httpx.MockTransport(handler)
        )

    async def test_a_batch_is_embedded_in_one_request(self) -> None:
        """The legacy pipeline embeds one chunk per call: ~111 serial round trips per 100 KB."""
        calls: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            return httpx.Response(200, json={"data": [
                {"index": i, "embedding": [float(i), 0.0, 0.0, 1.0]}
                for i in range(len(body["input"]))
            ]})

        provider = self._provider()
        self._install(provider, handler)
        result = await provider.embed(["one", "two", "three"])

        assert len(calls) == 1
        assert len(result.vectors) == 3
        assert result.provider == "embeddings"

    async def test_out_of_order_responses_are_reordered_by_index(self) -> None:
        """The API is not required to respond in order; getting this wrong silently
        misattributes every vector to the wrong chunk."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [
                {"index": 2, "embedding": [2.0, 0, 0, 0]},
                {"index": 0, "embedding": [0.0, 0, 0, 1]},
                {"index": 1, "embedding": [1.0, 0, 0, 0]},
            ]})

        provider = self._provider()
        self._install(provider, handler)
        result = await provider.embed(["a", "b", "c"])

        assert [v[0] for v in result.vectors] == [0.0, 1.0, 2.0]

    async def test_batches_are_split_and_dispatched_concurrently(self) -> None:
        batch_sizes: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            texts = json.loads(request.content)["input"]
            batch_sizes.append(len(texts))
            return httpx.Response(200, json={"data": [
                {"index": i, "embedding": [1.0, 0, 0, 0]} for i in range(len(texts))
            ]})

        provider = self._provider(max_batch_size=2)
        self._install(provider, handler)
        result = await provider.embed(["a", "b", "c", "d", "e"])

        assert sorted(batch_sizes) == [1, 2, 2]
        assert len(result.vectors) == 5

    async def test_asymmetric_prefixes_are_applied_per_direction(self) -> None:
        """Omitting these on an E5/BGE/GTE model costs several points of recall."""
        seen: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            texts = json.loads(request.content)["input"]
            seen.append(texts)
            return httpx.Response(200, json={"data": [
                {"index": i, "embedding": [1.0, 0, 0, 0]} for i in range(len(texts))
            ]})

        provider = self._provider(query_prefix="query: ", document_prefix="passage: ")
        self._install(provider, handler)
        await provider.embed(["leave policy"], for_query=True)
        await provider.embed(["leave policy"], for_query=False)

        assert seen[0] == ["query: leave policy"]
        assert seen[1] == ["passage: leave policy"]

    async def test_a_dimension_mismatch_is_a_non_retryable_error_naming_the_fix(self) -> None:
        """Retrying will not change a model's output dimension; the operator must reconfigure."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 768}]})

        provider = self._provider(dimension=384)
        self._install(provider, handler)
        with pytest.raises(EmbeddingError) as excinfo:
            await provider.embed(["text"])
        assert excinfo.value.retryable is False
        assert "AURA_EMBEDDING_DIMENSION" in str(excinfo.value)

    async def test_a_short_response_is_an_error_not_a_partial_result(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

        provider = self._provider()
        self._install(provider, handler)
        with pytest.raises(EmbeddingError) as excinfo:
            await provider.embed(["a", "b"])
        assert "expected 2 embeddings" in str(excinfo.value)

    @pytest.mark.parametrize("status,retryable", [(500, True), (429, True), (400, False)])
    async def test_http_errors_carry_the_right_retryability(
        self, status: int, retryable: bool
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, text="upstream said no")

        provider = self._provider()
        self._install(provider, handler)
        with pytest.raises(EmbeddingError) as excinfo:
            await provider.embed(["text"])
        assert excinfo.value.retryable is retryable

    async def test_a_transport_failure_is_translated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        provider = self._provider()
        self._install(provider, handler)
        with pytest.raises(EmbeddingError):
            await provider.embed(["text"])

    async def test_an_empty_input_is_refused(self) -> None:
        with pytest.raises(EmbeddingError):
            await self._provider().embed([])

    async def test_health_reports_rather_than_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="down")

        provider = self._provider()
        self._install(provider, handler)
        assert (await provider.health()).healthy is False

    async def test_health_succeeds_against_a_working_endpoint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

        provider = self._provider()
        self._install(provider, handler)
        assert (await provider.health()).healthy is True

    async def test_close_releases_the_client(self) -> None:
        provider = self._provider()
        self._install(provider, lambda r: httpx.Response(200, json={"data": []}))
        await provider.close()
        assert provider._client is None
        await provider.close()  # idempotent

    def test_identity_is_advertised(self) -> None:
        provider = self._provider(max_batch_size=16)
        assert provider.name == "embeddings"
        assert provider.model_id == "bge-small-en-v1.5"
        assert provider.dimension == 4
        assert provider.max_batch_size == 16

    def test_batch_and_parallelism_floors_are_enforced(self) -> None:
        """A zero batch size would produce an infinite loop, not merely a slow one."""
        provider = self._provider(max_batch_size=0, max_parallel_batches=0)
        assert provider.max_batch_size == 1
        assert provider._parallel == 1


class TestSentenceTransformerProvider:
    def test_the_model_is_not_loaded_at_construction(self) -> None:
        """Loading at import is TD-01: it makes the process un-startable without the weights."""
        provider = SentenceTransformerProvider(model="BAAI/bge-small-en-v1.5", dimension=384)
        assert provider._model is None
        assert provider.dimension == 384
        assert provider.model_id == "BAAI/bge-small-en-v1.5"

    async def test_a_missing_dependency_surfaces_as_an_embedding_error(self) -> None:
        """The failure must name the missing package, not raise a bare ImportError upward."""
        provider = SentenceTransformerProvider(model="does-not-exist", dimension=384)
        with pytest.raises(EmbeddingError):
            await provider.embed(["text"])

    async def test_health_reports_unhealthy_when_the_model_cannot_load(self) -> None:
        provider = SentenceTransformerProvider(model="does-not-exist", dimension=384)
        assert (await provider.health()).healthy is False


# --------------------------------------------------------------------------------------
# Response cache
# --------------------------------------------------------------------------------------


def _request(text: str = "what is my leave balance?", **overrides) -> CompletionRequest:
    base = dict(
        conversation=Conversation.of([Message.user(text)]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=128),
    )
    base.update(overrides)
    return CompletionRequest(**base)


def _response(text: str = "21 days.") -> CompletionResponse:
    return CompletionResponse(
        text=text, provider="local", model="qwen", finish_reason=FinishReason.STOP,
        usage=TokenUsage(prompt_tokens=40, completion_tokens=6), cost_usd=0.002,
    )


class TestInMemoryCache:
    async def test_round_trip(self) -> None:
        cache = InMemoryCache()
        await cache.set("k", b"v")
        assert await cache.get("k") == b"v"

    async def test_a_missing_key_is_none(self) -> None:
        assert await InMemoryCache().get("absent") is None

    async def test_expired_entries_are_dropped_on_read(self) -> None:
        cache = InMemoryCache()
        await cache.set("k", b"v", ttl_s=-1)
        assert await cache.get("k") is None
        assert len(cache) == 0

    async def test_a_zero_ttl_means_no_expiry(self) -> None:
        cache = InMemoryCache()
        await cache.set("k", b"v", ttl_s=0)
        assert await cache.get("k") == b"v"

    async def test_it_is_bounded_and_evicts_least_recently_used(self) -> None:
        """The legacy code has five unbounded in-process dicts that grow until an OOM kill."""
        cache = InMemoryCache(max_entries=3)
        for i in range(3):
            await cache.set(f"k{i}", str(i).encode())
        await cache.get("k0")  # refresh recency
        await cache.set("k3", b"3")

        assert len(cache) == 3
        assert await cache.get("k0") is not None
        assert await cache.get("k1") is None

    async def test_delete_and_clear(self) -> None:
        cache = InMemoryCache()
        await cache.set("k", b"v")
        await cache.delete("k")
        assert await cache.get("k") is None
        await cache.set("k2", b"v")
        cache.clear()
        assert len(cache) == 0

    async def test_incr_starts_at_one_and_accumulates(self) -> None:
        cache = InMemoryCache()
        assert await cache.incr("counter") == 1
        assert await cache.incr("counter") == 2

    async def test_get_many_returns_only_what_is_present(self) -> None:
        cache = InMemoryCache()
        await cache.set("a", b"1")
        assert await cache.get_many(["a", "b"]) == {"a": b"1"}


class TestCacheEligibility:
    def _cache(self, **kwargs) -> LLMCache:
        return LLMCache(InMemoryCache(), **kwargs)

    def test_a_deterministic_tool_free_request_is_cacheable(self) -> None:
        assert self._cache().is_cacheable(_request()) is True

    def test_a_sampled_request_is_not_cached(self) -> None:
        """Serving a stored sample makes non-determinism inconsistent rather than absent."""
        sampled = _request(sampling=SamplingParams(temperature=0.7, max_output_tokens=128))
        assert self._cache().is_cacheable(sampled) is False

    def test_a_tool_calling_request_is_not_cached(self) -> None:
        """A replayed tool call would be evaluated against possibly-changed external state."""
        with_tools = _request(tools=({"name": "get_weather", "description": "..."},))
        assert self._cache().is_cacheable(with_tools) is False

    def test_a_disabled_cache_caches_nothing(self) -> None:
        assert self._cache(enabled=False).is_cacheable(_request()) is False


class TestExactTier:
    async def test_a_repeated_request_hits(self) -> None:
        cache = LLMCache(InMemoryCache())
        await cache.store(_request(), _response())
        hit = await cache.lookup(_request())

        assert hit is not None
        assert hit.tier == "exact"
        assert hit.response.text == "21 days."
        assert hit.response.cached is True

    async def test_a_hit_reports_zero_cost_and_preserves_the_original(self) -> None:
        """Reporting the original cost on a hit would double-count spend."""
        cache = LLMCache(InMemoryCache())
        await cache.store(_request(), _response())
        hit = await cache.lookup(_request())

        assert hit.response.cost_usd == 0.0
        assert hit.response.raw_metadata["original_cost_usd"] == pytest.approx(0.002)

    async def test_whitespace_differences_still_hit(self) -> None:
        cache = LLMCache(InMemoryCache())
        await cache.store(_request("what   is my leave balance?"), _response())
        assert await cache.lookup(_request("what is my leave balance?")) is not None

    async def test_a_different_namespace_misses(self) -> None:
        """A response grounded in one user's documents must never reach another."""
        cache = LLMCache(InMemoryCache())
        await cache.store(_request(), _response(), namespace="user-a")
        assert await cache.lookup(_request(), namespace="user-b") is None
        assert await cache.lookup(_request(), namespace="user-a") is not None

    async def test_a_different_model_misses(self) -> None:
        cache = LLMCache(InMemoryCache())
        await cache.store(_request(model="qwen"), _response())
        assert await cache.lookup(_request(model="llama")) is None

    async def test_different_sampling_parameters_miss(self) -> None:
        cache = LLMCache(InMemoryCache())
        await cache.store(_request(), _response())
        other = _request(sampling=SamplingParams(temperature=0.0, max_output_tokens=512))
        assert await cache.lookup(other) is None

    async def test_declaring_tools_changes_the_key(self) -> None:
        """Tool presence changes model behaviour, so it must not share a cache entry."""
        cache = LLMCache(InMemoryCache())
        assert cache._fingerprint(_request(), "ns") != cache._fingerprint(
            _request(tools=({"name": "x"},)), "ns"
        )

    async def test_an_empty_response_is_not_stored(self) -> None:
        cache = LLMCache(InMemoryCache())
        await cache.store(_request(), _response(text=""))
        assert await cache.lookup(_request()) is None

    async def test_a_corrupt_entry_is_a_miss_not_an_exception(self) -> None:
        """The cache must not be able to fail a request."""
        backing = InMemoryCache()
        cache = LLMCache(backing)
        await cache.store(_request(), _response())
        key = LLMCache._EXACT_PREFIX + cache._fingerprint(_request(), "global")
        await backing.set(key, b"{{{ not json")

        assert await cache.lookup(_request()) is None

    async def test_a_fingerprint_does_not_contain_the_prompt(self) -> None:
        """A cache key is stored and logged; it must not reconstruct a user's question."""
        fingerprint = LLMCache(InMemoryCache())._fingerprint(
            _request("my salary is 95000 EGP"), "ns"
        )
        assert "95000" not in fingerprint
        assert len(fingerprint) == 64


class TestSemanticTier:
    def _cache(self, **kwargs) -> LLMCache:
        return LLMCache(
            InMemoryCache(), embedder=HashEmbeddingProvider(dimension=128), **kwargs
        )

    async def test_a_near_identical_question_hits(self) -> None:
        cache = self._cache(similarity_threshold=0.90)
        await cache.store(_request("what is my schedule?"), _response("Three meetings."))
        hit = await cache.lookup(_request("what is my schedule"))

        assert hit is not None
        assert hit.tier == "semantic"
        assert hit.similarity >= 0.90

    async def test_an_unrelated_question_misses(self) -> None:
        cache = self._cache(similarity_threshold=0.90)
        await cache.store(_request("what is my schedule?"), _response())
        assert await cache.lookup(_request("how do I file an expense claim?")) is None

    async def test_a_multi_turn_conversation_is_never_matched_semantically(self) -> None:
        """The same final message means different things depending on what preceded it."""
        cache = self._cache(similarity_threshold=0.90)
        await cache.store(_request("what is my schedule?"), _response())

        multi_turn = CompletionRequest(
            conversation=Conversation.of([
                Message.user("tell me about project atlas"),
                Message.assistant("Atlas ships in Q3."),
                Message.user("what is my schedule"),
            ]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=128),
        )
        assert await cache.lookup(multi_turn) is None

    async def test_a_system_message_does_not_block_semantic_matching(self) -> None:
        cache = self._cache(similarity_threshold=0.90)
        with_system = CompletionRequest(
            conversation=Conversation.of([
                Message.system("You are AURA."), Message.user("what is my schedule?"),
            ]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=128),
        )
        await cache.store(with_system, _response())

        probe = CompletionRequest(
            conversation=Conversation.of([
                Message.system("You are AURA."), Message.user("what is my schedule"),
            ]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=128),
        )
        assert await cache.lookup(probe) is not None

    async def test_the_semantic_index_is_bounded(self) -> None:
        """The index is scanned linearly on every lookup, so its size is a direct latency cost."""
        cache = self._cache(semantic_index_size=4)
        for i in range(10):
            await cache.store(_request(f"question number {i}?"), _response(f"answer {i}"))

        index_key = LLMCache._SEM_INDEX_PREFIX + __import__("hashlib").sha256(
            b"global|"
        ).hexdigest()
        raw = await cache._cache.get(index_key)
        assert len(json.loads(raw)) == 4

    async def test_an_embedding_failure_degrades_to_a_miss(self) -> None:
        """An embedding outage must not fail the turn."""

        class BrokenEmbedder:
            async def embed(self, texts, *, for_query: bool = False):  # noqa: ANN001
                raise RuntimeError("embedding service is down")

        backing = InMemoryCache()
        working = LLMCache(backing, embedder=HashEmbeddingProvider(dimension=128))
        await working.store(_request("what is my schedule?"), _response())

        broken = LLMCache(backing, embedder=BrokenEmbedder())
        assert await broken.lookup(_request("what is my schedule")) is None

    async def test_semantic_matching_can_be_disabled_independently(self) -> None:
        cache = self._cache(semantic_enabled=False, similarity_threshold=0.5)
        await cache.store(_request("what is my schedule?"), _response())
        assert await cache.lookup(_request("what is my schedule")) is None

    async def test_no_embedder_disables_the_semantic_tier(self) -> None:
        cache = LLMCache(InMemoryCache(), embedder=None)
        await cache.store(_request("what is my schedule?"), _response())
        assert await cache.lookup(_request("what is my schedule")) is None

    async def test_invalidating_a_namespace_drops_semantic_matches_only(self) -> None:
        """A cached answer grounded in a superseded document is stale faster than the TTL."""
        cache = self._cache(similarity_threshold=0.90)
        await cache.store(_request("what is my schedule?"), _response(), namespace="user-a")
        await cache.invalidate_namespace("user-a")

        assert await cache.lookup(_request("what is my schedule"), namespace="user-a") is None
        # The exact entry survives; it is keyed by content, not by the semantic index.
        assert await cache.lookup(_request("what is my schedule?"), namespace="user-a") is not None

    async def test_a_store_failure_is_swallowed(self) -> None:
        """A cache write failure is not a turn failure."""

        class BrokenBacking(InMemoryCache):
            async def set(self, key, value, *, ttl_s=None):  # noqa: ANN001
                raise RuntimeError("redis is down")

        cache = LLMCache(BrokenBacking(), embedder=HashEmbeddingProvider(dimension=64))
        await cache.store(_request(), _response())  # must not raise

    async def test_metrics_are_recorded_for_hits_and_misses(self, metrics) -> None:
        cache = LLMCache(InMemoryCache(), metrics=metrics)
        await cache.lookup(_request())
        await cache.store(_request(), _response())
        assert (await cache.lookup(_request())).tier == "exact"


class TestSemanticTierNeedsASemanticEmbedder:
    """PP-08 (provider performance audit, 2026-11-08)."""

    def test_the_hash_embedder_declares_itself_not_semantic(self) -> None:
        from aura_core.llm.cache import semantic_tier_useful

        assert semantic_tier_useful(HashEmbeddingProvider(dimension=128)) is False

    def test_an_embedder_that_declares_nothing_counts_as_semantic(self) -> None:
        from aura_core.llm.cache import semantic_tier_useful

        class _ModelEmbedder:
            pass

        assert semantic_tier_useful(_ModelEmbedder()) is True
        assert semantic_tier_useful(None) is False

    def test_the_container_leaves_the_tier_off_with_the_hash_embedder(self, container) -> None:
        assert container.settings.llm.semantic_cache_enabled is True  # the setting is on...
        assert container.llm_cache._semantic_enabled is False  # ...and the wiring overrides it

    async def test_the_exact_tier_still_works_without_it(self, container) -> None:
        cache = container.llm_cache
        await cache.store(_request("what is my schedule?"), _response("Three meetings."))
        hit = await cache.lookup(_request("what is my schedule?"))
        assert hit is not None and hit.tier == "exact"
