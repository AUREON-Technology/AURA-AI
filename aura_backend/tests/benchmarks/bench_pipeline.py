"""Pipeline benchmarks.

Measures what the Performance Report estimated, so the estimates can be replaced by numbers.
Excluded from the default test run (`-m bench`), because a benchmark that gates CI on wall-clock
time on shared runners produces flaky failures and gets disabled.

    pytest tests/benchmarks -m bench -s

Model time is deliberately excluded: it is dominated by the provider and tells you nothing about
this code. What is measured is the *overhead* AURA adds -- the number ADR-000 sets a p95 target
of 60 ms for.
"""

from __future__ import annotations

import asyncio
import statistics
import time

import pytest

from aura_core.context import ContextEngine, ContextRequest
from aura_core.domain import Conversation, DocumentId, Message, MemoryQuery, UserId
from aura_core.knowledge import chunk_text

pytestmark = pytest.mark.bench


def _report(name: str, samples: list[float], *, unit: str = "ms") -> None:
    samples = sorted(samples)
    p50 = statistics.median(samples)
    p95 = samples[int(len(samples) * 0.95)] if len(samples) > 1 else samples[0]
    print(
        f"  {name:<38} n={len(samples):<5} "
        f"p50={p50:8.3f}{unit}  p95={p95:8.3f}{unit}  max={samples[-1]:8.3f}{unit}"
    )


async def _time(coro_factory, iterations: int) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        await coro_factory()
        samples.append((time.perf_counter() - started) * 1000)
    return samples


class TestPipelineOverhead:
    async def test_context_assembly(self, handbook) -> None:
        """The most latency-sensitive synchronous stage."""
        from aura_core.domain import Chunk, ChunkId, RetrievalResult, RetrievedChunk

        engine = ContextEngine()
        chunks = tuple(
            RetrievedChunk(
                chunk=Chunk(
                    id=ChunkId(f"d:v1:{i}"), document_id=DocumentId("d"),
                    document_version=1, index=i, content=handbook[:600],
                ),
                score=0.8 - i * 0.05,
            )
            for i in range(6)
        )
        request = ContextRequest(
            system_prompt="You are AURA." * 20,
            user_message="How many days of annual leave do I get?",
            history=Conversation.of([Message.user(f"turn {i}") for i in range(12)]),
            retrieval=RetrievalResult(chunks=chunks),
            context_window=8192,
            max_prompt_tokens=0,
        )

        samples = []
        for _ in range(200):
            started = time.perf_counter()
            engine.assemble(request)
            samples.append((time.perf_counter() - started) * 1000)
        print("\nContext engine")
        _report("assemble (6 chunks, 12 history turns)", samples)
        assert statistics.median(samples) < 25

    async def test_retrieval(self, knowledge, user, handbook) -> None:
        await knowledge.ingest(
            content=handbook * 5, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        samples = await _time(
            lambda: knowledge.retrieve("annual leave policy", owner_id=user), 100
        )
        print("\nRetrieval (hybrid: dense + BM25 + rerank + MMR)")
        _report("retrieve", samples)

    async def test_memory_recall(self, memory, user) -> None:
        for i in range(50):
            await memory.remember(
                owner_id=user, content=f"Distinct fact number {i} about the user's work.",
                source=f"turn:{i}",
            )
        samples = await _time(
            lambda: memory.recall(MemoryQuery(owner_id=user, text="what does the user do")), 100
        )
        print("\nMemory recall (50 records)")
        _report("recall", samples)

    async def test_guard_pipeline(self, safety, handbook) -> None:
        from aura_core.domain.safety import GuardStage

        samples = await _time(
            lambda: safety.evaluate(handbook[:2000], stage=GuardStage.CONTEXT), 200
        )
        print("\nSafety pipeline")
        _report("context guards (2KB)", samples)
        assert statistics.median(samples) < 10

    async def test_full_turn_overhead(self, container, user, handbook) -> None:
        """End-to-end minus model time -- the ADR-000 p95 < 60ms target."""
        from aura_core.kernel import TurnRequest

        await container.startup()
        await container.knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )

        overheads, totals = [], []
        for _ in range(50):
            result = await container.kernel.run(
                TurnRequest(message="How many days of annual leave do I get?", user_id=user)
            )
            overheads.append(result.telemetry.timings.overhead)
            totals.append(result.telemetry.total_ms)

        print("\nFull turn (echo provider; model time excluded from 'overhead')")
        _report("pipeline overhead", overheads)
        _report("total turn", totals)
        assert sorted(overheads)[int(len(overheads) * 0.95)] < 60, "p95 overhead target missed"


class TestThroughput:
    async def test_concurrent_turns(self, container, user, handbook) -> None:
        """Concurrency is the headline fix: the legacy pipeline caps at ~4 simultaneous AI
        requests because synchronous clients run inside coroutines."""
        from aura_core.kernel import TurnRequest

        await container.startup()
        await container.knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )

        for concurrency in (1, 8, 32, 64):
            started = time.perf_counter()
            await asyncio.gather(*(
                container.kernel.run(
                    TurnRequest(message=f"Question {i} about leave?", user_id=user)
                )
                for i in range(concurrency)
            ))
            elapsed = time.perf_counter() - started
            print(f"  concurrency={concurrency:<4} {elapsed * 1000:8.1f}ms  "
                  f"({concurrency / elapsed:7.1f} turns/s)")


class TestIngestion:
    async def test_chunking_throughput(self, handbook) -> None:
        document = handbook * 40  # ~40 KB
        samples = []
        for _ in range(20):
            started = time.perf_counter()
            pieces = chunk_text(document)
            samples.append((time.perf_counter() - started) * 1000)
        print(f"\nChunking ({len(document) // 1024}KB -> {len(pieces)} chunks)")
        _report("chunk_text", samples)

    async def test_ingestion_end_to_end(self, knowledge, user, handbook) -> None:
        document = handbook * 20
        samples = []
        for i in range(5):
            started = time.perf_counter()
            await knowledge.ingest(
                content=document + f"\n\nRevision {i}.", title=f"Doc {i}",
                document_id=DocumentId(f"doc-{i}"), owner_id=user,
            )
            samples.append((time.perf_counter() - started) * 1000)
        print(f"\nIngestion ({len(document) // 1024}KB document, batched embeddings)")
        _report("ingest", samples)
