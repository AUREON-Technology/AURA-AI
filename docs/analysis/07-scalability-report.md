# AURA AI — Scalability Report (Phase 1)

Target: run well on a single VPS **today**, scale to multiple servers **without a rewrite**.

---

## 1. Statefulness audit

Horizontal scaling is only possible if a request can be served by any replica. Today it cannot:

| State | Location | Where it lives | Blocks scaling? |
| --- | --- | --- | --- |
| Embedding cache | `rag.py:16` | process memory | No (correctness-safe), but wastes memory ×N replicas |
| Memory query cache | `memory.py:27` | process memory | No, but inconsistent TTL across replicas |
| Rate-limit counters | `main.py:49` | Redis, falls back to process memory | **Yes** when Redis is down — limits become per-replica |
| Attestation nonces | `security.py:18` | Redis, falls back to a process `set()` | **Yes** — a nonce issued by replica A is unknown to replica B |
| Refresh tokens | `jwt_auth.py:372` | Redis, falls back to a process dict | **Yes** — sessions break across replicas |
| Local Qdrant | `rag.py:36` | **container-local SQLite file** | **Yes** — each replica gets a different knowledge base |
| `learning_logs.json` | `learning_pipeline.py` | container filesystem | **Yes** — writes are lost and conflict |
| `telemetry_logs.jsonl` | `main.py:415` | container filesystem | **Yes** |
| `roles.json` | `rbac.py:38` | container filesystem | **Yes** — inconsistent authorisation |
| `routing_centroids.json` | `agent_router.py:88` | image, written at runtime | Partially — recomputed per replica on first boot |
| Ollama model files | host volume | single host | **Yes** — local inference is pinned to one machine |

**Verdict:** the backend is *not* horizontally scalable as written. Four categories of state are
process- or container-local with no shared alternative.

---

## 2. Concurrency ceiling

Covered in the Performance Report §2: synchronous Qdrant, `SentenceTransformer`, and file I/O run on
the asyncio event loop. Adding replicas multiplies a ceiling of ~4 concurrent AI requests per worker
rather than removing it. **Vertical scaling does not help** — more CPU cores do not unblock an event
loop.

---

## 3. Bottleneck analysis by tier

### 3.1 API tier
Stateless *in principle*, blocked by §1. Once the state is externalised and I/O is async, this tier
scales linearly behind a load balancer. This is the tier that should scale first.

### 3.2 Inference tier
The hardest constraint. Local inference (Ollama/vLLM) is bound to the machine holding the weights and
the GPU. Scaling means either:
- **(a)** a dedicated inference host (or pool) that API replicas call over the network, or
- **(b)** replicating the whole node including weights (expensive, cache-cold).

Option (a) is correct and is what the rebuild's architecture assumes: inference is a **network
service behind a port**, never an in-process library. That is why the design uses the
OpenAI-compatible protocol as the local-inference contract — vLLM, Ollama, TGI, and llama.cpp's
server all speak it, so the pool can be swapped without touching application code.

### 3.3 Vector tier
Qdrant scales to multiple nodes with sharding and replication, but the current code:
- uses a **single collection** for all users and all content types → no shard key strategy,
- has **no index versioning** → reindexing means downtime,
- falls back to **container-local storage** when the cluster is unreachable → silent divergence.

### 3.4 Relational tier
Postgres with correct composite indexes on `(user_id, updated_at)`. Read scaling via replicas is
straightforward. Connection pooling is untuned (see Performance §5) and will be the first thing to
break at ~8 replicas.

### 3.5 Worker tier
Celery over Redis. Correct pattern. Currently one queue for everything: PDF ingestion (minutes,
CPU-heavy) shares a queue with insight generation (seconds). A slow ingestion backlog delays every
other task.

### 3.6 Cache/broker tier
Single Redis instance, no persistence configuration, no Sentinel/Cluster, used as broker + result
backend + rate limiter + nonce store + session store. It is a **single point of failure** for
authentication.

---

## 4. Scaling plan

### Stage 0 — single VPS (2–4 vCPU, 8–16 GB), target: 50 concurrent users
- All services in one compose stack, Nginx ingress, TLS.
- Local inference via a small quantised model (7–8B, Q4) on CPU or a single consumer GPU.
- Cloud LLM for quality-sensitive requests; local for cheap/private ones.
- **Requirements:** all state externalised to Redis/Postgres/Qdrant; async I/O everywhere;
  response cache enabled.

### Stage 1 — split the inference tier, target: 200 concurrent users
- Move Ollama/vLLM to its own host with the GPU; API replicas reach it over the private network.
- API tier scales to 2–4 replicas behind Nginx (`least_conn`).
- Celery split into `queue:ingestion` and `queue:realtime` with separate worker pools.
- Postgres connection pooling via PgBouncer.

### Stage 2 — multi-server, target: 1,000+ concurrent users
- Managed Postgres with read replicas; reads for sync/insights routed to replicas.
- Qdrant cluster (3 nodes, replication factor 2), sharded by `user_id`.
- Redis Sentinel or managed Redis; separate instances for broker vs. session/rate-limit.
- Inference pool of N nodes behind the provider router's load balancer, health-gated.
- Prometheus federation; central Grafana and Loki.

### Stage 3 — beyond
- Kubernetes with HPA on `aura_llm_queue_depth` and CPU.
- Regional deployment; Qdrant per region, Postgres primary + regional read replicas.

---

## 5. What the rebuild changes to make this possible

| Requirement | Mechanism |
| --- | --- |
| Any replica can serve any request | No process-local state on the request path; all caches optional and correctness-neutral |
| Inference is location-transparent | `LLMProvider` port + OpenAI-compatible adapter; providers configured by URL |
| Provider pool scales out | `ProviderRouter` with health checks, weighted least-latency selection, circuit breakers |
| Reindexing without downtime | Named, versioned indexes + alias switch (`aura_core/knowledge/index_manager.py`) |
| Work isolation | Named Celery queues per workload class with independent concurrency |
| Backpressure | Bounded queues + `429` with `Retry-After` rather than unbounded latency growth |
| Capacity is measurable | `aura_llm_inflight`, `aura_llm_queue_depth`, per-provider latency histograms, saturation alerts |
| Cost scales sub-linearly | Two-tier response cache, context budgeting, local-first routing policy |

---

## 6. Capacity model (rebuilt system, single VPS)

Assumes 4 vCPU / 16 GB, cloud LLM for generation, local embeddings, cache hit rate 25 %.

| Resource | Per AI request | Ceiling |
| --- | --- | --- |
| Event-loop time | ~5 ms (all I/O awaited) | ~200 req/s theoretical |
| Embedding calls | 1 (cached across the turn) | provider rate limit |
| Vector searches | 2 (knowledge + memory, concurrent) | Qdrant single node ≈ 1,000 qps |
| LLM generation | 1 (or 0 on cache hit) | **provider concurrency — the real limit** |
| Memory | ~250 MB/worker steady state | 16 GB / 4 workers, ample |

**Binding constraint at every stage is LLM generation concurrency**, which is exactly why the
provider router (multi-provider, load-balanced, cached, with local overflow) is the highest-leverage
component in the design.
