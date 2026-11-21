"""KAN-111: a turn embeds its message once, however many stages need the vector.

Memory recall, dense retrieval, and the semantic cache's lookup and store each embedded the same
message: up to four calls to the embedding endpoint for one vector. The embedder here is the real
hash embedder behind a counter at the port boundary, declared semantic so the cache's semantic
tier is on (the hash embedder alone turns it off, PP-08).
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.domain.errors import EmbeddingError
from aura_core.domain.identifiers import UserId
from aura_core.kernel import TurnRequest
from aura_core.llm.embeddings import HashEmbeddingProvider
from aura_core.llm.turn_embeddings import embed_query, embedding_memo_scope

USER = UserId("embed-user")


class CountingEmbedder:
    """The hash embedder, counting query-side calls; ``is_semantic`` so the semantic tier runs."""

    is_semantic = True

    def __init__(self, inner: HashEmbeddingProvider) -> None:
        self._inner = inner
        self.query_calls: list[tuple[str, ...]] = []
        self.fail_next = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def embed(self, texts, *, for_query: bool = False):
        if for_query:
            self.query_calls.append(tuple(texts))
            if self.fail_next:
                self.fail_next -= 1
                raise EmbeddingError("embedding endpoint unavailable")
        await asyncio.sleep(0)  # a real call yields; concurrent callers can overlap
        return await self._inner.embed(texts, for_query=for_query)


@pytest.fixture
async def counting(container) -> CountingEmbedder:
    await container.startup()
    # A document, so retrieval has an index to search (with none it returns before embedding).
    await container.knowledge.ingest(
        title="car notes", owner_id=USER,
        content="The car is parked on level 3 of the station garage, near the lifts.",
    )
    embedder = CountingEmbedder(container.embedder)
    container.memory._embedder = embedder
    container.knowledge._embedder = embedder
    container.llm_cache._embedder = embedder
    container.llm_cache._semantic_enabled = True
    return embedder


def _cacheable(message: str) -> TurnRequest:
    # Deterministic sampling, so the LLM cache takes part (it skips sampled answers).
    return TurnRequest(message=message, user_id=USER, temperature=0.0)


class TestOneEmbeddingPerTurn:
    async def test_run_embeds_the_message_once(self, container, counting) -> None:
        """Recall, retrieval and the semantic cache's store: three calls before, one now."""
        result = await container.kernel.run(_cacheable("Where did I park the car?"))
        assert result.telemetry.retrieval.candidates  # retrieval really searched
        assert counting.query_calls == [("Where did I park the car?",)]

    async def test_a_later_turn_embeds_only_its_own_message(self, container, counting) -> None:
        await container.kernel.run(_cacheable("Where did I park the car?"))
        counting.query_calls.clear()
        await container.kernel.run(_cacheable("What should I cook tonight?"))
        assert counting.query_calls == [("What should I cook tonight?",)]

    async def test_stream_embeds_the_message_once(self, container, counting) -> None:
        frames = [
            f async for f in container.kernel.stream(
                TurnRequest(message="Where did I park the car?", user_id=USER)
            )
        ]
        assert frames[-1]["type"] == "done"
        assert counting.query_calls == [("Where did I park the car?",)]

    async def test_each_turn_embeds_its_own_message(self, container, counting) -> None:
        await container.kernel.run(TurnRequest(message="first message", user_id=USER))
        await container.kernel.run(TurnRequest(message="second message", user_id=USER))
        assert counting.query_calls == [("first message",), ("second message",)]


class TestTheMemo:
    async def test_outside_a_turn_nothing_is_shared(self) -> None:
        embedder = CountingEmbedder(HashEmbeddingProvider(dimension=32))
        await embed_query(embedder, "hello")
        await embed_query(embedder, "hello")
        assert len(embedder.query_calls) == 2

    async def test_concurrent_callers_share_one_call(self) -> None:
        embedder = CountingEmbedder(HashEmbeddingProvider(dimension=32))
        with embedding_memo_scope():
            first, second = await asyncio.gather(
                embed_query(embedder, "hello"), embed_query(embedder, "hello")
            )
        assert len(embedder.query_calls) == 1
        assert first.first == second.first

    async def test_different_text_or_model_is_not_shared(self) -> None:
        a = CountingEmbedder(HashEmbeddingProvider(dimension=32, model_id="model-a"))
        b = CountingEmbedder(HashEmbeddingProvider(dimension=32, model_id="model-b"))
        with embedding_memo_scope():
            await embed_query(a, "hello")
            await embed_query(a, "goodbye")
            await embed_query(b, "hello")
        assert len(a.query_calls) == 2 and len(b.query_calls) == 1

    async def test_a_failure_is_not_remembered(self) -> None:
        embedder = CountingEmbedder(HashEmbeddingProvider(dimension=32))
        embedder.fail_next = 1
        with embedding_memo_scope():
            with pytest.raises(EmbeddingError):
                await embed_query(embedder, "hello")
            result = await embed_query(embedder, "hello")
        assert result.first
        assert len(embedder.query_calls) == 2
