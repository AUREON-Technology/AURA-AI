"""F-14 against a real Postgres and a real Redis: two workers, one store, one cache.

Opt-in (``-m load``). Skips unless both are named:

- ``AURA_TEST_DATABASE_URL`` -- a superuser DSN; each test gets its own throwaway database;
- ``AURA_TEST_REDIS_URL`` -- a Redis the test may write ``knowledge:lexical:*`` keys to; each
  test uses its own logical database number (``/1`` .. ``/15``) and flushes it.

Each "worker" gets its own ``PostgresDocumentStore`` pool and its own ``RedisCache`` client, as
two uvicorn processes would. The vector store is the in-memory one shared between them, which
stands in for Qdrant: the property under test is the lexical index and ``chunks_for``, and the
dense half is already shared in production.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adapters.memory_stores import InMemoryVectorStore
from aura_core.adapters.postgres_store import PostgresDocumentStore
from aura_core.adapters.redis_cache import RedisCache
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.knowledge.index_manager import IndexManager
from aura_core.knowledge.lexical_sync import LexicalChangeFeed
from aura_core.llm.embeddings import HashEmbeddingProvider

pytestmark = pytest.mark.load

_PG = os.environ.get("AURA_TEST_DATABASE_URL", "")
_REDIS = os.environ.get("AURA_TEST_REDIS_URL", "")
USER = UserId("f14-user")
RUNBOOK = "The AURA-4419 escalation runbook says to page the on-call SRE."


@pytest.fixture
async def database_url():
    if not _PG:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"f14_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_PG)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield urlunsplit(urlsplit(_PG)._replace(path=f"/{name}"))
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def redis_url():
    if not _REDIS:
        pytest.skip("AURA_TEST_REDIS_URL is not set")
    url = urlunsplit(urlsplit(_REDIS)._replace(path="/9"))
    cache = RedisCache(url)
    await cache._get_client().flushdb()
    yield url
    await cache._get_client().flushdb()
    await cache.close()


class _Workers:
    def __init__(self, database_url: str, redis_url: str, *, check_interval_s: float) -> None:
        self.vectors = InMemoryVectorStore()
        self.embedder = HashEmbeddingProvider(dimension=128)
        self.stores: list[PostgresDocumentStore] = []
        self.caches: list[RedisCache] = []
        self.engines = [
            self._engine(database_url, redis_url, check_interval_s) for _ in range(2)
        ]

    def _engine(self, database_url: str, redis_url: str, interval: float) -> KnowledgeEngine:
        documents = PostgresDocumentStore(database_url)
        cache = RedisCache(redis_url)
        self.stores.append(documents)
        self.caches.append(cache)
        return KnowledgeEngine(
            documents=documents, vectors=self.vectors, embedder=self.embedder,
            index_manager=IndexManager(
                vectors=self.vectors, documents=documents, embedder=self.embedder, alias="f14",
            ),
            score_threshold=0.15,
            lexical_feed=LexicalChangeFeed(cache, check_interval_s=interval),
        )

    async def close(self) -> None:
        for cache in self.caches:
            await cache.close()
        for store in self.stores:
            pool = store._pool._pool
            if pool is not None:
                await pool.close()


@pytest.fixture
async def workers(database_url, redis_url):
    pair = _Workers(database_url, redis_url, check_interval_s=0.25)
    yield pair
    await pair.close()


async def _lexical(engine: KnowledgeEngine, query: str) -> list[str]:
    return [chunk_id for chunk_id, _ in await engine._lexical_search(query, USER, True)]


async def test_the_audit_scenario_on_postgres_and_redis(workers) -> None:
    a, b = workers.engines
    await b.rebuild_lexical_index()
    assert b.lexical_size == 0

    result = await a.ingest(content=RUNBOOK, title="runbook", owner_id=USER)

    # R2.4's acceptance: lexically retrievable from every worker within 1 s.
    started = time.perf_counter()
    while not await _lexical(b, "AURA-4419"):
        assert time.perf_counter() - started < 1.0, "upload not visible on worker B within 1 s"
        await asyncio.sleep(0.02)
    assert (await b.retrieve("AURA-4419 runbook", owner_id=USER)).chunks

    await a.delete_document(result.document.id, owner_id=USER)
    # Immediately, before B's next feed check: enforced on read by the shared store.
    assert not (await b.retrieve("AURA-4419 runbook", owner_id=USER)).chunks
    await asyncio.sleep(0.3)
    await _lexical(b, "AURA-4419")
    assert b.lexical_size == 0


async def test_a_new_version_replaces_the_old_on_the_other_worker(workers) -> None:
    a, b = workers.engines
    await a.ingest(content="The expense cap is 250 dollars per claim.", title="Policy",
                   document_id=DocumentId("policy"), owner_id=USER)
    await b.rebuild_lexical_index()
    await a.ingest(content="The expense cap is 500 dollars per claim.", title="Policy",
                   document_id=DocumentId("policy"), owner_id=USER)
    await asyncio.sleep(0.3)
    assert await _lexical(b, "250") == []
    assert await _lexical(b, "500") == ["policy:v2:0"]


async def test_chunks_for_resolves_only_the_active_version(workers) -> None:
    a, _b = workers.engines
    documents = workers.stores[0]
    await a.ingest(content="Version one text.", title="Doc",
                   document_id=DocumentId("doc"), owner_id=USER)
    await a.ingest(content="Version two text.", title="Doc",
                   document_id=DocumentId("doc"), owner_id=USER)
    assert list(await documents.chunks_for(DocumentId("doc"), 1)) == []
    assert [c.id.value for c in await documents.chunks_for(DocumentId("doc"), 2)] == ["doc:v2:0"]
    # The superseded version's vectors were retired, which needs its chunks read before the
    # supersede now that `chunks_for` hides it afterwards.
    identity = await a._indexes.resolve_for_read()
    assert await workers.vectors.count(identity) == 1

    await a.delete_document(DocumentId("doc"), owner_id=USER)
    assert list(await documents.chunks_for(DocumentId("doc"), 2)) == []


async def test_a_flushed_redis_makes_the_other_worker_rebuild(workers, redis_url) -> None:
    a, b = workers.engines
    for n in range(3):
        await a.ingest(content=f"Note {n} about AURA-{n}00.", title=f"n{n}", owner_id=USER)
    await b.rebuild_lexical_index()
    assert b._lexical_cursor == 3

    await workers.caches[0]._get_client().flushdb()
    await a.ingest(content=RUNBOOK, title="runbook", owner_id=USER)
    await asyncio.sleep(0.3)
    assert await _lexical(b, "AURA-4419")
    assert b._lexical_cursor == 1
