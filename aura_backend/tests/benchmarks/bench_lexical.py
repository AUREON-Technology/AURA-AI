"""Lexical retrieval latency vs corpus size (audit F-30 / AR-13, roadmap R3.4).

Measures what the in-process BM25 index costs per query as the corpus grows, so the decision
between keeping it, changing its algorithm, and moving lexical search into Postgres (R2.4 Option
B) is made on numbers. The report built from it is ``docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md``.

Excluded from the default run, like every benchmark here:

    pytest tests/benchmarks/bench_lexical.py -m bench -s

Environment knobs, all optional:

- ``AURA_BENCH_LEXICAL_SIZES`` -- comma-separated chunk counts (default ``1000,10000,50000``).
  ``200000`` is what R2.4 asks for; it needs a few GB of RAM, so it is opt-in.
- ``AURA_BENCH_LEXICAL_OUT`` -- path to write the measurements as JSON.

What is measured, per corpus size (p50 and p95 over every query, after a warm-up pass):

- ``forward_stage`` -- the lexical stage as it was before the inverted index: a pass over every
  chunk's owner to build the tenant allow-list, then the forward-scan BM25
  (``tests/unit/_forward_bm25.py``, the old class verbatim). Timed in the same run, on the same
  corpus, so before and after are directly comparable.
- ``inverted_search`` -- ``BM25Index.search`` restricted to the tenant's and the global groups.
- ``lexical_stage`` -- ``KnowledgeEngine._lexical_search``: what one ``retrieve()`` now pays for
  its lexical half.
- ``retrieve`` -- the whole call with the dense half empty (no vectors indexed), so the number is
  the lexical path end to end: the stage, the active-version check, content resolution (the N+1
  chunk fetches), the heuristic rerank and the threshold.
- ``*_index_mib`` -- Python heap held by a fully built index of each kind (tracemalloc, on a
  separate build so its overhead never touches a timing).
- ``feed_check`` -- what the cross-worker change feed (F-14, ``knowledge/lexical_sync.py``) adds
  to a query in its *worst* steady state: the check interval forced to 0, so every query reads
  the generation counter, and nothing has changed. Production checks at most every 250 ms per
  worker, so most queries pay only a clock read.
- ``lexical_stage_feed`` -- ``lexical_stage`` on an engine with that feed, same index, same
  worst-case interval. The difference to ``lexical_stage`` is the per-query overhead.

``AURA_BENCH_REDIS_URL`` (optional) points the feed at a real Redis instead of the in-memory
cache, so ``feed_check`` includes the network round trip production pays. The bench writes only
``knowledge:lexical:*`` keys there.

The corpus is synthetic and deterministic (seeded): a Zipf-distributed (s=1.07) 30k-word
vocabulary, 150-250 words per chunk (near the 320-token chunk target), 20 chunks per document,
ten tenants each owning 10% of the chunks. Queries are six terms from the same distribution plus
two rare ones -- the shape the audit measured (8-term query, tenant owns 10%).

Before any timing, every query's top 24 from the inverted index is checked against the forward
scan's, score for score, so a faster index that ranks differently fails here. No wall-clock bound
is asserted: shared runners make those flaky.
"""

from __future__ import annotations

import gc
import json
import os
import platform
import random
import statistics
import sys
import time
import tracemalloc
from bisect import bisect_left
from itertools import accumulate

import pytest

from aura_core.adapters.memory_stores import InMemoryDocumentStore, InMemoryVectorStore
from aura_core.domain import Chunk, Document, DocumentId, UserId
from aura_core.knowledge import BM25Index, KnowledgeEngine
from aura_core.knowledge.index_manager import IndexManager
from aura_core.knowledge.lexical_sync import LexicalChangeFeed
from aura_core.llm.cache import InMemoryCache
from aura_core.llm.embeddings import HashEmbeddingProvider
from aura_core.ports.storage import GLOBAL_OWNER

from ..unit._forward_bm25 import ForwardScanBM25

pytestmark = pytest.mark.bench

_VOCABULARY = 30_000
_WORDS_PER_CHUNK = 200
_CHUNKS_PER_DOCUMENT = 20
_TENANTS = 10
_QUERIES = 60
_QUERY_TERMS = 8
_LIMIT = 24  # the engine's default candidate_k
_SEED = 20261026


def _sizes() -> list[int]:
    raw = os.environ.get("AURA_BENCH_LEXICAL_SIZES", "1000,10000,50000")
    return [int(part) for part in raw.split(",") if part.strip()]


def _word(rank: int) -> str:
    # Letters only, so the tokenizer keeps each word whole and none collides with a stopword.
    letters = []
    n = rank + 1
    while n:
        n, r = divmod(n - 1, 26)
        letters.append(chr(ord("a") + r))
    return "q" + "".join(reversed(letters))


class _Zipf:
    def __init__(self, size: int, s: float, rng: random.Random) -> None:
        self._words = [_word(i) for i in range(size)]
        self._cumulative = list(accumulate(1.0 / (i + 1) ** s for i in range(size)))
        self._rng = rng

    def draw(self, k: int) -> list[str]:
        total = self._cumulative[-1]
        return [
            self._words[bisect_left(self._cumulative, self._rng.random() * total)]
            for _ in range(k)
        ]

    def rare(self) -> str:
        # Rank 2000+ of 30k: in a few hundred chunks per 50k, like a product name or ticket id.
        return self._words[self._rng.randrange(2_000, len(self._words))]


def _tenant(index: int) -> UserId:
    return UserId(f"tenant-{index % _TENANTS}")


def _group(chunk: Chunk) -> str:
    return chunk.owner_id.value if chunk.owner_id else GLOBAL_OWNER


async def _build(size: int) -> tuple[KnowledgeEngine, list[tuple[str, UserId]], float, int]:
    rng = random.Random(_SEED)
    zipf = _Zipf(_VOCABULARY, 1.07, rng)
    documents = InMemoryDocumentStore()
    vectors = InMemoryVectorStore()
    embedder = HashEmbeddingProvider(dimension=64)
    indexes = IndexManager(vectors=vectors, documents=documents, embedder=embedder,
                           alias="bench_lexical")
    await indexes.ensure_ready()

    for doc_number in range(max(1, size // _CHUNKS_PER_DOCUMENT)):
        owner = _tenant(doc_number)
        doc_id = DocumentId(f"doc-{doc_number}")
        chunks = []
        for index in range(_CHUNKS_PER_DOCUMENT):
            length = rng.randint(_WORDS_PER_CHUNK * 3 // 4, _WORDS_PER_CHUNK * 5 // 4)
            chunks.append(
                Chunk(
                    id=Chunk.build_id(doc_id, 1, index),
                    document_id=doc_id, document_version=1, index=index,
                    content=" ".join(zipf.draw(length)), owner_id=owner,
                )
            )
        document = Document(
            id=doc_id, version=1, title=f"Document {doc_number}",
            content="(benchmark corpus; content lives in the chunks)", owner_id=owner,
        )
        await documents.put(document, chunks)

    engine = KnowledgeEngine(
        documents=documents, vectors=vectors, embedder=embedder, index_manager=indexes,
        score_threshold=0.35,
    )
    gc.collect()
    started = time.perf_counter()
    count = await engine.rebuild_lexical_index()
    build_s = time.perf_counter() - started

    queries = []
    for q in range(_QUERIES):
        terms = zipf.draw(_QUERY_TERMS - 2) + [zipf.rare(), zipf.rare()]
        queries.append((" ".join(terms), _tenant(q)))
    return engine, queries, build_s, count


async def _chunks(engine: KnowledgeEngine):
    async for batch in engine._documents.iter_all_chunks(batch_size=500):  # noqa: SLF001
        for chunk in batch:
            yield chunk


async def _forward_reference(engine: KnowledgeEngine) -> tuple[ForwardScanBM25, dict[str, str]]:
    """The pre-change forward-scan index over the same chunks, plus the chunk -> owner map the
    old ``_lexical_search`` scanned to build its allow-list on every query."""
    reference = ForwardScanBM25()
    owners: dict[str, str] = {}
    async for chunk in _chunks(engine):
        reference.add(chunk.id.value, chunk.content)
        owners[chunk.id.value] = _group(chunk)
    return reference, owners


def _old_stage(reference: ForwardScanBM25, owners: dict[str, str], text: str, owner: str):
    """``KnowledgeEngine._lexical_search`` as it was before the change, line for line."""
    allowed = {c for c, o in owners.items() if o == owner or o == GLOBAL_OWNER}
    if not allowed:
        return []
    return reference.search(text, limit=_LIMIT, allowed_ids=allowed)


async def _footprint_mib(engine: KnowledgeEngine, grouped: bool) -> float:
    """Python heap a freshly built index holds once built (tracemalloc)."""
    gc.collect()
    tracemalloc.start()
    try:
        index = BM25Index() if grouped else ForwardScanBM25()
        async for chunk in _chunks(engine):
            if grouped:
                index.add(chunk.id.value, chunk.content, group=_group(chunk))
            else:
                index.add(chunk.id.value, chunk.content)
        # Subtract nothing: the chunks are already resident in the document store, so what is
        # traced here is the index itself (plus a few hundred bytes of iteration state).
        current, _peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del index
    gc.collect()
    return current / (1024 * 1024)


def _summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "mean_ms": round(statistics.fmean(ordered), 3),
        "max_ms": round(ordered[-1], 3),
    }


def _feed_cache():
    url = os.environ.get("AURA_BENCH_REDIS_URL")
    if not url:
        return InMemoryCache(max_entries=1024), "memory"
    from aura_core.adapters.redis_cache import RedisCache

    return RedisCache(url), "redis"


async def _with_feed(engine: KnowledgeEngine, cache) -> KnowledgeEngine:
    """A second engine over the same stores *and the same built index*, with a change feed
    whose interval is 0: every lexical search reads the generation counter (the worst case)."""
    feed = LexicalChangeFeed(cache, check_interval_s=0.0)
    synced = KnowledgeEngine(
        documents=engine._documents, vectors=engine._vectors,  # noqa: SLF001
        embedder=engine._embedder, index_manager=engine._indexes,  # noqa: SLF001
        score_threshold=0.35, lexical_feed=feed,
    )
    synced._lexical = engine._lexical  # noqa: SLF001 -- share the index, do not rebuild it
    # Caught up with a counter that exists, so each check is one GET that finds nothing new.
    synced._lexical_cursor = await feed.publish(["bench-no-such-document"])  # noqa: SLF001
    return synced


async def _measure(size: int, *, repeats: int) -> dict:
    engine, queries, build_s, count = await _build(size)
    index = engine._lexical  # noqa: SLF001 -- the benchmark measures the private index directly
    feed_cache, feed_backend = _feed_cache()
    synced = await _with_feed(engine, feed_cache)
    reference, owners = await _forward_reference(engine)

    # Same answers before timing anything. Equal scores are ordered by doc_id in the inverted
    # index, so the forward scan's full result is put in that order before comparing.
    for text, owner in queries:
        groups = (owner.value, GLOBAL_OWNER)
        full = reference.search(
            text, limit=10**9, allowed_ids={c for c, o in owners.items() if o in groups}
        )
        expected = sorted(full, key=lambda item: (-item[1], item[0]))[:_LIMIT]
        assert index.search(text, limit=_LIMIT, groups=groups) == expected, text

    # Warm-up: first touches of dicts and the allocator are not what a steady-state worker pays.
    for text, owner in queries[:5]:
        _old_stage(reference, owners, text, owner.value)
        await engine.retrieve(text, owner_id=owner)

    timings: dict[str, list[float]] = {
        "forward_stage": [], "inverted_search": [], "lexical_stage": [], "retrieve": [],
        "feed_check": [], "lexical_stage_feed": [],
    }
    hits = 0
    for _ in range(repeats):
        for text, owner in queries:
            started = time.perf_counter()
            _old_stage(reference, owners, text, owner.value)
            timings["forward_stage"].append((time.perf_counter() - started) * 1000)

            started = time.perf_counter()
            found = index.search(text, limit=_LIMIT, groups=(owner.value, GLOBAL_OWNER))
            timings["inverted_search"].append((time.perf_counter() - started) * 1000)
            hits += bool(found)

            started = time.perf_counter()
            await engine._lexical_search(text, owner, True)  # noqa: SLF001
            timings["lexical_stage"].append((time.perf_counter() - started) * 1000)

            started = time.perf_counter()
            await engine.retrieve(text, owner_id=owner)
            timings["retrieve"].append((time.perf_counter() - started) * 1000)

            started = time.perf_counter()
            await synced._sync_lexical()  # noqa: SLF001
            timings["feed_check"].append((time.perf_counter() - started) * 1000)

            started = time.perf_counter()
            await synced._lexical_search(text, owner, True)  # noqa: SLF001
            timings["lexical_stage_feed"].append((time.perf_counter() - started) * 1000)

    del reference, owners
    if feed_backend == "redis":
        await feed_cache.close()
    result = {
        "chunks": count,
        "feed_backend": feed_backend,
        "build_s": round(build_s, 2),
        "inverted_index_mib": round(await _footprint_mib(engine, grouped=True), 1),
        "forward_index_mib": round(await _footprint_mib(engine, grouped=False), 1),
        "queries_with_hits": hits / (repeats * len(queries)),
        **{name: _summary(samples) for name, samples in timings.items()},
    }
    del engine, index, synced
    gc.collect()
    return result


def _machine() -> dict[str, str]:
    cpu = platform.processor() or "unknown"
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu": cpu,
        "cpus": str(os.cpu_count()),
    }


class TestLexicalScaling:
    async def test_lexical_latency_vs_corpus_size(self) -> None:
        results = []
        print("\nLexical retrieval vs corpus size (tenant owns 10%, 8-term queries; ms)")
        for size in _sizes():
            measured = await _measure(size, repeats=5 if size <= 10_000 else 3)
            results.append(measured)
            print(
                f"  {measured['chunks']:>7} chunks  build={measured['build_s']:5.2f}s  "
                f"MiB inverted/forward={measured['inverted_index_mib']:6.1f}/"
                f"{measured['forward_index_mib']:6.1f}  "
                + "  ".join(
                    f"{name} p50={measured[name]['p50_ms']:.3f} p95={measured[name]['p95_ms']:.3f}"
                    for name in ("forward_stage", "inverted_search", "lexical_stage", "retrieve",
                                 "feed_check", "lexical_stage_feed")
                )
            )
            assert measured["queries_with_hits"] > 0.9

        out = os.environ.get("AURA_BENCH_LEXICAL_OUT")
        if out:
            with open(out, "w", encoding="utf-8") as handle:
                json.dump({"machine": _machine(), "results": results}, handle, indent=2)
                handle.write("\n")
