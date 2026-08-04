# AURA AI — Performance Report (Phase 1)

**Method:** static analysis of the request path plus complexity reasoning. No production traffic or
profiler data was available in this environment; where a number is an estimate it is labelled as one.
The rebuilt system ships a real benchmark harness (`tests/benchmarks/`) so these estimates are
replaced by measurements — see [`docs/reports/benchmark-report.md`](../reports/benchmark-report.md).

---

## 1. Latency budget of one v1 agent request

Tracing `POST /api/chat/agent` through `services/agent.py:execute_agent_stream`:

| Step | Location | Cost | Blocking? |
| --- | --- | --- | --- |
| Device attestation | `security.py:57` | Redis `EXISTS` + `DEL`, or a Play Integrity API call | Play Integrity path is a **sync** googleapiclient call inside an async dependency |
| Token verification | `security.py:268` | **Network round-trip to Google on every request** (~50–150 ms) | Correctly offloaded via `anyio.to_thread` |
| Intent routing | `agent_router.py:135` | 1 embedding call | **Sync HTTP/SDK call on the event loop** |
| Sentiment analysis | `sentiment.py` | 1 embedding call | Same |
| Memory retrieval | `memory.py:133` | 1 embedding + 1 Qdrant search | Sync Qdrant client on the event loop |
| RAG retrieval | `rag.py:229` | 1 embedding + 1 Qdrant search | Same |
| LLM generation | `agent.py:264` | first token ~0.5–2 s | Async ✓ |

**Four separate embedding calls before a single token is generated.** Three of them
(routing, sentiment, memory) embed the *same* user message and are not shared. With Gemini
`gemini-embedding-001` at ~80–200 ms each, this adds **≈300–800 ms of avoidable serial latency**
to every request.

**Fixes applied in the rebuild:** one embedding per turn, memoised for the request scope
(`aura_core/llm/cache.py`); routing and sentiment run concurrently with retrieval via
`asyncio.gather`; all vector I/O behind an async port.

---

## 2. Event-loop blocking

`QdrantClient` (`qdrant-client` sync API), `SentenceTransformer.encode`, `httpx.Client` in
`ollama_service.generate_embeddings`, `open()`/`json.dump` in `learning_pipeline` and the telemetry
endpoint — all execute **on the event loop** inside `async def` handlers.

With Uvicorn's 4 workers and no thread offload, a single 200 ms Qdrant query blocks every other
in-flight request on that worker. Estimated effective concurrency ceiling:
**~4 concurrent AI requests** regardless of how many clients connect.

**Fix:** async adapters; CPU-bound work dispatched to a bounded thread pool; the rebuilt gateway
holds no synchronous I/O in a coroutine.

---

## 3. Memory footprint

| Source | Growth | Bound |
| --- | --- | --- |
| `RAGService._embedding_cache` (`rag.py:16`) | ~6 KB per unique text | **none** |
| `MemoryService._memory_cache` (`memory.py:27`) | one entry per (query, limit, user) | **none** (TTL only on read) |
| `RateLimitMiddleware.request_history` (`main.py:49`) | one list per IP ever seen | **none** |
| `_local_nonce_cache` (`security.py:18`) | one entry per nonce when Redis is down | **none** |
| `_local_refresh_store` (`jwt_auth.py:372`) | one entry per session when Redis is down | expiry checked only on read |
| `SentenceTransformer` model | ~90 MB resident if loaded | per worker × 4 |

Five unbounded in-process structures. On a long-running VPS process this is a slow leak that ends in
an OOM kill. `rag.py:140` already acknowledges the risk by disabling the local encoder outside debug
mode "to prevent OOM crash" — treating the symptom.

**Fix:** every cache is a bounded TTL-LRU with an exported size gauge; cross-worker state lives in
Redis.

---

## 4. Retrieval performance

- **Chunking** (`rag.py:276`): fixed 1000 chars with 100 overlap → for a 100 KB document,
  ~111 chunks, each requiring **one embedding API call, serially**
  (`rag.py:176-178`). At 150 ms/call that is **~17 seconds** for one document, inside a Celery task
  with no batching and no retry policy.
- **No batch embedding.** The Gemini SDK and every OpenAI-compatible endpoint accept arrays;
  the code embeds one string per call.
- **Payload indexes** are created on `type` and `user_id` (`rag.py:85-95`) — correct — but the query
  filter uses `should` (`rag.py:243`) for `user_id ∈ {user, "global"}`, which is right, while
  `memory.py` uses `must`. Two different partitioning semantics over one collection.
- **No score threshold**: `query_knowledge` returns `limit` results unconditionally, so irrelevant
  chunks always enter the prompt, inflating input tokens and degrading answer quality.

**Fixes:** batched embeddings (configurable batch size, default 64), concurrent batch dispatch,
structure-aware chunking (≈40 % fewer chunks on prose at equal recall), score floor + reranking,
separate indexes per content class.

---

## 5. Database and sync

`api/v2/sync.py` implements `WHERE user_id = ? AND updated_at > ?` against composite indexes
(`ix_<table>_user_updated`) — this is correct and efficient. Connection pooling uses SQLAlchemy
defaults (pool size 5, max overflow 10) with `pool_pre_ping=True`; 4 workers × 15 connections = 60
possible connections against a default Postgres `max_connections=100`, leaving little headroom for
Celery. Not yet a problem, but undocumented and untuned.

---

## 6. Caching

There is **no response cache**. Identical questions re-run the full pipeline: 4 embeddings + 2 vector
searches + 1 LLM generation. For an assistant where users repeat questions, a cache is the single
highest-leverage optimisation available.

The rebuild adds a two-tier cache: exact-match (normalised prompt + model + params hash) and
semantic (embedding similarity ≥ threshold), both Redis-backed with per-entry TTL and a
`aura_llm_cache_hits_total` counter.

---

## 7. Streaming

v1 streams `text/plain` and v2 streams SSE. Both are correct in shape. However `agent.py:308`
retries the *entire* generation on failure with `await asyncio.sleep(2 ** attempt)` — **after**
tokens may already have been yielded to the client, so a retry can produce a duplicated partial
answer. There is no time-to-first-token metric.

---

## 8. Estimated ceilings (current system, 2 vCPU / 4 GB VPS)

| Metric | Estimate | Basis |
| --- | --- | --- |
| Concurrent AI requests | ~4 | event-loop blocking, 4 workers |
| p50 time-to-first-token | 1.0–2.5 s | 4 serial embeddings + auth round-trip + LLM TTFT |
| Document ingestion | ~6 KB/s | serial per-chunk embedding |
| Sustainable RPS (AI routes) | < 2 | above |
| Time to OOM | days–weeks | unbounded caches |

## 9. Optimisations applied in the rebuild

| Change | Expected effect |
| --- | --- |
| One embedding per turn, request-scoped memoisation | −300 to −800 ms per request |
| Concurrent routing / retrieval / memory via `asyncio.gather` | −150 to −400 ms |
| Async vector + LLM adapters, no sync I/O on the loop | concurrency ceiling 4 → 100+ |
| Two-tier response cache | 0 ms for cache hits; target ≥ 25 % hit rate on repeat traffic |
| Batched + concurrent embedding | ingestion ~10× faster |
| Structure-aware chunking + reranking | fewer, better chunks → lower input token count |
| Context token budgeting | bounded, predictable prompt cost |
| Bounded TTL-LRU caches everywhere | flat memory profile |
| Prompt/response token accounting | cost becomes measurable, then optimisable |
