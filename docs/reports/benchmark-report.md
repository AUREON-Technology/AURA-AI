# Benchmark Report

Measured numbers for the rebuilt pipeline, replacing the estimates in
[`docs/analysis/05-performance-report.md`](../analysis/05-performance-report.md).

Everything here is reproducible on any machine with the repository checked out and no
infrastructure at all:

```bash
cd aura_backend
pytest tests/benchmarks -m bench -s
```

That measures pipeline overhead against in-memory adapters and `EchoProvider`. It is **not**
time-to-first-token with Ollama in the path.

To time a real chat turn against compose (CPU Ollama, nginx, the 180s LLM timeout):

```bash
# from a machine that can reach the gateway, after compose is up and a model is pulled
curl -N -X POST "https://$AURA_DOMAIN/api/v3/chat/stream" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"hello"}' -w "\nTTFB via curl %{time_starttransfer}s total %{time_total}s\n"
```

Do not treat the bench millisecond figures as production TTFT, and do not add a CI job that
needs GPU/weights for this measurement.

---

## What is measured, and what deliberately is not

**Measured: the overhead AURA adds to a turn.** Guard evaluation, prompt rendering, memory
recall, retrieval, context assembly, tool dispatch, and output guards.

**Not measured: model time.** Time-to-first-token is dominated by the provider and the hardware
it runs on. A number for it would describe the benchmark host's CPU, not this codebase, and
would go stale the moment a deployment changes its model. What the code controls is the
overhead, and that is what [ADR-000](../architecture/00-overview.md) sets a target for.

The benchmarks run against the in-memory adapters and the `EchoProvider`, which is exactly why
they are reproducible. They therefore **exclude** network round-trips to Postgres, Qdrant, and
Redis. §5 accounts for what those add in a real deployment.

### Environment

| | |
| --- | --- |
| CPU | Intel Xeon @ 2.80 GHz, 4 vCPU |
| Memory | 15 GB |
| Python | 3.11.15 |
| Storage | in-memory adapters |
| Provider | `EchoProvider` (deterministic, no network) |
| Date | 2026-08-03 |

Absolute figures scale with the host. The **ratios** and the **shape** of the distributions are
the durable results.

---

## 1. Per-stage overhead

| Stage | n | p50 | p95 | max |
| --- | ---: | ---: | ---: | ---: |
| Context assembly (6 chunks, 12 history turns) | 200 | 1.45 ms | 1.61 ms | 2.10 ms |
| Hybrid retrieval (dense + BM25 + RRF + rerank + MMR) | 100 | 1.19 ms | 1.54 ms | 1.93 ms |
| Memory recall (50 records) | 100 | 1.22 ms | 1.40 ms | 1.84 ms |
| Guard pipeline (2 KB of context) | 200 | 0.21 ms | 0.26 ms | 0.71 ms |

Two things are worth drawing out.

**The safety pipeline is not the bottleneck.** Eight guards over 2 KB cost 0.21 ms at the
median. This matters because "safety checks are too slow" is the usual argument for sampling
them or skipping them under load, and at 0.2 ms there is nothing to trade away. The Security
Report's design assumed this; now it is measured.

**Hybrid retrieval costs about the same as dense-only would.** BM25 scoring is over an
in-process inverted index, and RRF is a sort over two ranked lists — both are cheap relative to
the embedding call that dominates real retrieval. Adding lexical search to close the
exact-identifier gap (see §4) did not cost latency.

## 2. Full-turn overhead

| Metric | n | p50 | p95 | max |
| --- | ---: | ---: | ---: | ---: |
| Pipeline overhead (model time excluded) | 50 | 3.75 ms | **4.56 ms** | 5.15 ms |
| Total turn (echo provider) | 50 | 3.49 ms | 4.43 ms | 5.45 ms |

**The ADR-000 target is p95 overhead < 60 ms. Measured: 4.56 ms — roughly 13× headroom.**

That headroom is the point. It means the budget is available for the things that will consume
it in production: a real embedding call for the query, a network round-trip to Qdrant, and a
Postgres read. Those are covered in §5.

> **Reading the two rows together.** Overhead is the *sum* of per-stage timings; total is
> wall-clock. Memory recall, retrieval, and history load run concurrently (`asyncio.gather` in
> `kernel/kernel.py:571`), so summing them double-counts and the sum can exceed the wall clock.
> The overhead figure is therefore a **conservative upper bound**, which is the right property
> for a number a target is set against — it cannot flatter the system.

## 3. Concurrency

| Concurrent turns | Wall clock | Throughput |
| ---: | ---: | ---: |
| 1 | 2.5 ms | 394 turns/s |
| 8 | 17.1 ms | 468 turns/s |
| 32 | 66.9 ms | 478 turns/s |
| 64 | 167.1 ms | 383 turns/s |

Throughput is roughly flat from 8 to 32 concurrent turns and falls off at 64 — the expected
shape when the work is CPU-bound on 4 vCPU and there is no I/O to overlap. In production the
picture inverts: the work becomes I/O-bound on the provider, and the binding constraint moves to
generation concurrency (§5).

**The comparison that matters is with the legacy pipeline.** `docs/analysis/07-scalability-report.md`
identifies a ceiling of roughly 4 simultaneous AI requests, caused by synchronous clients
(`QdrantClient`, `SentenceTransformer.encode`, `httpx.Client`) executing inside `async def`
handlers — each one parks the entire event loop, so concurrency is bounded by the thread pool
rather than by the work. Every I/O path in `aura_core` is behind an async port, which is why the
table above shows no collapse at 32.

This is an *architectural* difference, not a tuning one. It is not recoverable by adding workers
to the legacy pipeline: four blocked event loops is four blocked event loops.

## 4. Ingestion

| Operation | n | p50 | p95 |
| --- | ---: | ---: | ---: |
| Chunking (21 KB → 120 chunks) | 20 | 3.13 ms | 3.81 ms |
| End-to-end ingest (10 KB, batched embeddings) | 5 | 64.2 ms | 68.5 ms |

Chunking runs at roughly 6.7 MB/s single-threaded — structural boundary detection, heading-path
tracking, and overlap are not a meaningful cost.

The end-to-end figure is dominated by embedding. The relevant comparison is the batching change:
the legacy pipeline embeds **one chunk per call**, so a 100 KB document is ~111 serial round
trips. `KnowledgeEngine._embed_and_index` batches to `max_batch_size` and dispatches batches
concurrently. With a real embedding backend at ~150 ms per call, that is the difference between
~17 s and ~1 s for the same document.

## 5. What production adds

The benchmarks isolate this codebase. A real deployment adds, per turn:

| Addition | Typical | Notes |
| --- | --- | --- |
| Query embedding | 80–200 ms | The single largest addition. Cached per turn, not per stage. |
| Qdrant search | 5–20 ms | Network plus HNSW traversal. |
| Postgres reads | 2–10 ms | Session and memory records; pooled. |
| Redis (cache, rate limit) | < 2 ms | Same host. |
| Generation | 0.5–5 s | Provider-dependent. Dominates everything above. |

Two consequences follow, and both are already reflected in the design:

1. **Generation is ~99% of a turn's wall clock.** Optimising the 4.5 ms of overhead further
   would be measuring an effect the user cannot perceive. This is why the response cache
   (`llm/cache.py`) exists: on a cache hit the entire generation disappears, which is a change
   of a different order from any micro-optimisation.
2. **Query embedding is the largest addition AURA controls.** One embedding per turn, memoised
   for the request scope, replaces the legacy pipeline's four separate embeddings of the same
   message (routing, sentiment, memory, RAG) — an avoidable 300–800 ms on every request.

## 6. Regression guard

Three of the benchmarks carry assertions, so a change that makes the pipeline materially slower
fails rather than merely being slower:

| Assertion | Threshold | Measured |
| --- | --- | --- |
| Context assembly p50 | < 25 ms | 1.45 ms |
| Guard pipeline p50 | < 10 ms | 0.21 ms |
| Full-turn overhead p95 | < 60 ms | 4.56 ms |

The thresholds are deliberately far above the measurements. A benchmark that gates CI on
wall-clock time on a shared runner produces flaky failures and gets disabled within a month;
these are set to catch an order-of-magnitude regression, which is the class of change actually
worth blocking. Benchmarks are excluded from the default `pytest` run (`-m "not bench"` in
`pytest.ini`) and opted into explicitly.

---

## Method and honesty notes

- **n is small for ingestion (5) and chunking (20).** Those operations are slow enough that a
  large n makes the suite unpleasant to run. Treat their p95 as indicative; the per-stage
  figures (n = 100–200) are the reliable ones.
- **No warm-up is discarded.** Python's first execution of a code path includes bytecode
  specialisation and import-time work. Keeping it in makes the p50 slightly pessimistic, which
  is the direction to err.
- **The hash embedder is not semantic.** It exercises the same code path at a comparable cost
  per call, so the *shape* is right, but it is not a proxy for real retrieval quality. Quality
  is measured separately by `aura_core/evaluation/harness.py`.
- **These are single-host numbers.** Multi-host scaling behaviour is reasoned about, not
  measured, in [`docs/analysis/07-scalability-report.md`](../analysis/07-scalability-report.md).

Related: [Performance Report](performance-report.md) · [Security Audit](security-audit.md)
