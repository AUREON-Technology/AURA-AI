# Performance Report

What was slow in the legacy system, what changed, and what the change is actually worth. Phase 1's
findings are in [`docs/analysis/05-performance-report.md`](../analysis/05-performance-report.md);
this report closes them out.

Measurements come from [`benchmark-report.md`](benchmark-report.md). Where a number could not be
measured in this environment it is labelled an estimate and its basis is given — the Phase 1
report was disciplined about that distinction and this one keeps it.

---

## 1. Closing out the Phase 1 findings

| # | Finding | Status | Evidence |
| --- | --- | --- | --- |
| P-01 | Four serial embeddings of the same message per turn | **Fixed** | One embedding per turn, memoised for the request scope. Routing, retrieval, and memory run under one `asyncio.gather` (`kernel/kernel.py:571`). |
| P-02 | Sync clients on the event loop (Qdrant, SentenceTransformer, httpx) | **Fixed** | Every port method is a coroutine; `tests/architecture/test_dependency_rules.py::test_ports_are_async` fails the build otherwise. CPU-bound encoding runs in a worker thread. |
| P-03 | Five unbounded in-process caches | **Fixed** | Every cache is a bounded TTL-LRU (`llm/cache.py:InMemoryCache`); cross-worker state is in Redis. Eviction is tested. |
| P-04 | Serial per-chunk embedding (~17 s per 100 KB) | **Fixed** | Batched to `max_batch_size`, batches dispatched concurrently under a semaphore (`knowledge/engine.py:_embed_and_index`). |
| P-05 | No score threshold — irrelevant chunks always entered the prompt | **Fixed** | Score floor that can return nothing; `RetrievalResult.is_empty_but_searched` distinguishes "we looked and found nothing" from "we did not look". |
| P-06 | No response cache | **Fixed** | Two-tier exact + semantic cache, restricted to deterministic tool-free turns, namespaced per user. |
| P-07 | Retry after partial output could duplicate a streamed answer | **Fixed** | The router refuses to fail over once a chunk has been emitted and raises instead (`llm/router.py:479`). Asserted for every streaming adapter. |
| P-08 | Two different partitioning semantics over one collection (`should` vs `must`) | **Fixed** | One owner-scoping rule in the vector port; separate indexes per content class. |
| P-09 | No time-to-first-token metric | **Fixed** | `aura_llm_ttft_seconds`, plus EWMA TTFT per provider driving the `fastest` routing policy. |
| P-10 | Connection pooling undocumented and untuned | **Documented** | Sizing table in the Deployment Guide; pool settings in `deploy/postgres/postgresql.conf`. Not retuned — the defaults are adequate at this scale and changing them without load data would be guessing. |

## 2. What the fixes are worth

### Concurrency: the one that matters

The legacy ceiling of ~4 simultaneous AI requests is not a tuning problem. `QdrantClient`,
`SentenceTransformer.encode`, and `httpx.Client` execute **inside `async def` handlers**, so each
one parks its entire event loop; concurrency is bounded by workers, not by work. Adding workers
multiplies memory (each holds a ~90 MB model) without lifting the ceiling much.

Measured on the rebuilt pipeline: throughput is flat from 8 to 32 concurrent turns and falls off
at 64 on 4 vCPU — the shape of CPU-bound work with no I/O to overlap. With a real provider the
work becomes I/O-bound and the binding constraint moves to generation concurrency, which is a
capacity question rather than an architectural one.

**Trade-off accepted:** async-everywhere means every adapter must be written against an async
client, and a synchronous library cannot be dropped in casually. `FunctionTool` rejects a
synchronous handler at construction rather than letting one silently block the loop in
production. That is a real constraint on contributors, and it is the price of the property.

### Latency: honestly, a rounding error

Pipeline overhead is **4.56 ms at p95** against a 60 ms target. Generation is 0.5–5 s. AURA's own
work is under 1% of a turn.

This is worth stating plainly because it sets where optimisation effort should *not* go. Shaving
the context engine from 1.45 ms to 1.0 ms is unmeasurable to a user. The two changes that move
perceived latency are:

1. **Removing the serial embeddings** — 300–800 ms per request, based on 3–4 avoidable calls at
   80–200 ms each. *Estimate*, since it depends on the embedding provider.
2. **Cache hits** — a hit removes the entire generation. On repeat traffic this is the only
   change that alters the order of magnitude.

### Ingestion

Batching turns ~111 serial round trips per 100 KB document into ~2 batched calls. At 150 ms per
call that is ~17 s → ~1 s. *Estimate*: the measured 64 ms figure uses the hash embedder, which
has no network cost. The structural claim — one call per batch instead of one per chunk — is what
the benchmark verifies.

Structure-aware chunking produces roughly 40% fewer chunks on prose at equal recall by splitting
on heading and paragraph boundaries rather than every 1000 characters. Fewer chunks means fewer
embeddings, less storage, and less index traversal.

## 3. Where the cost actually is

Per turn, in a production deployment:

```
generation             0.5–5 s      ~99%        provider and hardware
query embedding        80–200 ms     ~1%        one per turn (was four)
vector search          5–20 ms      < 0.5%      network + HNSW
AURA overhead          4.6 ms       < 0.1%      measured, p95
```

Three consequences shape the design:

**The response cache is the highest-leverage optimisation available.** Nothing else removes 99%
of a turn. It is also the one with real correctness risk, which is why it is restricted to
`temperature == 0` and tool-free turns, and namespaced per user: a cached answer grounded in one
user's documents must never reach another. Serving a stored sample for a sampled request would
make non-determinism *inconsistent* rather than absent — a worse contract than either extreme.

**Local inference changes the shape, not just the number.** A local 7B Q4 model on CPU has higher
TTFT than a hosted frontier model but zero marginal cost and no data egress. `LOCAL_FIRST` is the
default policy for that reason; `QUALITY_FIRST` exists for turns where the trade is worth
reversing. The router makes this a per-request decision rather than a deployment-wide one.

**Retrieval quality is a latency lever.** A precise retrieval that returns 4 relevant chunks
produces a shorter prompt than a loose one returning 12 marginal ones — fewer input tokens, lower
cost, and faster generation. This is why the score floor and reranking are performance work as
much as quality work.

## 4. Memory

| Component | Steady state | Bound |
| --- | --- | --- |
| Gateway worker | ~250 MB | Process; 4 workers on the reference host |
| Response cache | ≤ `max_entries × ~2 KB` | LRU eviction, tested |
| Lexical (BM25) index | ~100 bytes/chunk | Process-local; rebuilt at startup |
| Semantic cache index | 64 entries × dimension × 8 B | Explicit cap |
| Rate-limit state | Redis | Not in-process |

Every in-process structure has a maximum size. The legacy system's five unbounded dicts are why
`rag.py:140` disables the local encoder outside debug mode "to prevent OOM crash" — treating the
symptom rather than bounding the caches.

**Trade-off accepted:** a bounded cache evicts, so a working set larger than the cap gets a lower
hit rate. That is strictly better than an unbounded cache that eventually kills the process, and
the size is a configured number rather than an emergent property of traffic.

## 5. What is not optimised, and why

**Context assembly is greedy, not optimal.** Packing items into a token budget by priority then
density is a knapsack problem; the greedy solution is not optimal. Measured cost of the greedy
version: 1.45 ms. An optimal solver would cost more than the tokens it saves.

**BM25 is in-process and rebuilt at startup.** It does not survive a restart and each replica
holds its own copy. For a corpus that fits in memory this is right — an external search service
is another network hop, another failure mode, and another thing to operate. `rebuild_lexical_index`
runs at startup and is tested; the point at which this stops being right is documented in the
Scalability Report.

**The reranker is heuristic by default, and a cross-encoder is now available.**
`CrossEncoderReranker` (ms-marco-MiniLM-L-6-v2, loaded locally by sentence-transformers) is
selected with `AURA_KNOWLEDGE_RERANK_PROVIDER=cross_encoder`. It costs a forward pass per
candidate — roughly 100-300 ms for a 24-candidate list on the 4-vCPU box these figures come from,
which is why it is not the default on hardware also running a 7B generator.

That estimate is **not measured**: it is inferred from published MiniLM-L6 throughput, and this
environment has no torch installed to benchmark against. Treat it as an order of magnitude, and
measure on your own hardware before enabling it in production. What *is* verified is the failure
behaviour — a load failure, timeout or malformed output degrades to the heuristic reranker rather
than to fusion order, so the reranking stage survives the model being unavailable.

**No query result caching at the vector layer.** The response cache already covers repeated
questions, and a vector-layer cache would need invalidating on every ingestion — complexity
without a distinct benefit.

## 6. Operating targets

These are what the alerts in `deploy/monitoring/alerts/aura.yml` are set against.

| Metric | Target | Alert |
| --- | --- | --- |
| p95 pipeline overhead | < 60 ms | `AuraHighOverhead` |
| p95 TTFT (local 7B Q4, CPU) | < 3 s | `AuraSlowTimeToFirstToken` |
| p95 TTFT (cloud) | < 1.5 s | same |
| Cache hit rate (repeat traffic) | ≥ 25% | dashboard only — a low hit rate is a signal, not an incident |
| Retrieval empty rate | < 15% | `AuraRetrievalReturningNothing` |
| Provider error rate | < 1% | `AuraProviderErrorRate` |
| Ingestion throughput | > 50 KB/s | dashboard only |

The cache hit rate and ingestion throughput are deliberately not alerts. Both vary legitimately
with traffic mix, and an alert that fires on normal variation is an alert that gets muted.

## 7. Scaling order

When one host stops being enough, the constraints bind in this order
([Scalability Report](../analysis/07-scalability-report.md)):

1. **Move inference to its own host.** Generation concurrency binds first at every stage. This is
   a configuration change (`AURA_PROVIDER_LOCAL_BASE_URL`), not a code change — which is the
   payoff for declaring providers as data (ADR-003).
2. **Scale gateway replicas.** They are stateless; all shared state is already in Redis and
   Postgres. The one caveat is the process-local BM25 index, which each replica rebuilds at
   startup.
3. **Split the data tier.** Managed Postgres with read replicas, then a Qdrant cluster sharded by
   `user_id` — the natural shard key, since retrieval is already owner-scoped.

---

Related: [Benchmark Report](benchmark-report.md) · [Security Audit](security-audit.md) ·
[Scalability Report](../analysis/07-scalability-report.md)
