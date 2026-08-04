# AURA AI — Technical Debt Report (Phase 1)

Each item is rated by **impact** (what breaks) and **effort** (to repay). Line references are to the
tree at commit `57dee3f`.

---

## TD-01 — Import-time singletons with network side effects
**Impact: critical · Effort: high**

`services/rag.py:287`, `ai.py:108`, `memory.py:246`, `agent.py:365`, `ollama.py:118`,
`security.py:16` all instantiate a client at module import. `RAGService.__init__` opens a Qdrant
connection, may create a local SQLite store on disk, constructs a Gemini client, and creates a
collection — all before `main.py` has finished importing.

Consequences: the process cannot start without its dependencies reachable; tests must monkey-patch
globals; two configurations cannot coexist; a slow Qdrant makes the container fail its startup probe.

**Repayment:** constructor injection through `aura_core/container.py`; all adapters lazily connect on
first use and expose `health()`.

---

## TD-02 — Private-member access across module boundaries
**Impact: high · Effort: medium**

`memory.py:106,147`, `sentiment.py`, `agent_router.py:105` call `rag_service._get_embedding(...)`
and `rag_service._ensure_collection_exists(...)`. `memory.py:31-35` proxies
`rag_service.collection_name` and `rag_service.client` as properties. The embedding backend cannot be
changed without touching four modules.

**Repayment:** `EmbeddingProvider` port; every consumer depends on the port, not on `RAGService`.

---

## TD-03 — Silent zero-vector fallback
**Impact: critical · Effort: low**

```python
# services/rag.py:153-156
if not res_embedding:
    default_dim = 384 if self._is_local else 768
    res_embedding = [0.0] * default_dim
```

A failed embedding is written into the vector store and used for search. A zero vector has undefined
cosine similarity with everything (the code guards division by zero in `agent_router` but Qdrant does
not), so ingestion appears to succeed while producing an unsearchable corpus, and routing silently
falls back to `chat_agent`. There is no metric or alert.

**Repayment:** embeddings raise `EmbeddingError`; the caller decides (retry, queue, fail the request).
Never persist a synthetic vector.

---

## TD-04 — Destructive collection recreation
**Impact: critical · Effort: medium**

```python
# services/rag.py:69-72
if current_size and current_size != vector_size:
    logger.warning("Dimension mismatch … Recreating collection...")
    self.client.delete_collection(self.collection_name)
```

If Gemini is unavailable at boot, embeddings fall back to 384-dim MiniLM, the dimension check fires,
and **the entire knowledge base is deleted**. No backup, no confirmation, no audit entry.

**Repayment:** dimension is part of the index identity (`aura_knowledge__v3__bge-small__384`).
Mismatches create a *new* index and trigger a documented reindex, never a delete.

---

## TD-05 — Unauthenticated Ollama proxy
**Impact: critical · Effort: low**

`main.py:470,510,524,538` — `/api/tags`, `/api/chat`, `/api/pull`, `/api/delete` have no
`Depends(verify_token)`. Any internet caller can run inference on the host, pull arbitrary model
weights (disk exhaustion, untrusted binaries), or delete the deployed model.

**Repayment:** removed. Local inference is reached only through the authenticated gateway; model
lifecycle is admin-only and audited.

---

## TD-06 — Files used as databases
**Impact: high · Effort: medium**

`learning_logs.json` is read fully, mutated, and rewritten on **every** feedback record
(`learning_pipeline.py:20-36`) — O(n) per write, no locking, corrupt on concurrent writers or a crash
mid-write. `telemetry_logs.jsonl` (`main.py:415`) is appended from inside a request handler with
blocking I/O on the event loop. Both are container-local and lost on restart. `roles.json` grants
**authorisation** from an unversioned file on the container filesystem (`rbac.py:38`).

**Repayment:** Postgres tables with Alembic migrations; roles resolved from database + token claims.

---

## TD-07 — Blocking I/O on the async event loop
**Impact: high · Effort: medium**

Every Qdrant call (`QdrantClient` is synchronous), every `sentence_transformers` encode, every
`open()` in a handler, and `ollama_service.generate_embeddings` (`httpx.Client`, sync) runs inside
`async def` handlers. Under concurrency these serialise the entire worker.

**Repayment:** async clients where available; `anyio.to_thread.run_sync` for genuinely sync SDKs;
CPU-bound embedding work moved to the worker pool.

---

## TD-08 — Unbounded in-process caches
**Impact: medium · Effort: low**

`rag.py:16` `self._embedding_cache = {}` — never evicted, keyed by SHA-256 of full text, holding
768 floats per entry (~6 KB). `memory.py:27` `_memory_cache` — 15-minute TTL but no size bound and
`.clear()`-on-write invalidation. `main.py:49` `request_history` — per-IP timestamp lists, never
pruned for IPs that stop calling.

**Repayment:** bounded LRU with TTL and a `cache_size` gauge; Redis for anything cross-worker.

---

## TD-09 — Exception handling that swallows failures
**Impact: high · Effort: medium**

47 `except Exception` blocks across `services/`. The dominant pattern is log-and-return-empty:
`rag.py:273` returns `[]` on any query failure, `memory.py:183` returns `[]`, `agent_router.py:150`
returns `"chat_agent"`. Callers cannot distinguish "no results" from "the vector store is down".

**Repayment:** typed exception hierarchy (`aura_core/domain/errors.py`); adapters translate vendor
errors into domain errors; the gateway maps domain errors to HTTP status codes; every degradation
increments a metric.

---

## TD-10 — 565-line `main.py` mixing concerns
**Impact: medium · Effort: medium**

Two middleware classes, an exception handler, 9 Pydantic schemas, 22 route handlers, and an Ollama
proxy in one module. Routes call service singletons directly with no use-case layer.

**Repayment:** `main.py` becomes application assembly only; routers live in `aura_core/api/`.

---

## TD-11 — Prompt text embedded in code
**Impact: high · Effort: medium**

`agent.py:15-40` — 7 persona prompts and the `[ACTIONS]` protocol as module constants. Changing a
prompt requires a code review, a build, and a redeploy. There is no version history, no A/B
capability, and no regression test that a prompt change did not break tool-calling.

**Repayment:** versioned YAML prompt templates + a registry + prompt regression tests.

---

## TD-12 — `[ACTIONS]` marker protocol
**Impact: medium · Effort: low (already half-repaid)**

v1 asks the model to emit `[ACTIONS]` followed by raw JSON at the end of a free-text stream
(`agent.py:25-40`) and the client string-splits it out. `api/v2/chat.py` + `sse_marker_splitter.py`
correctly buffer the marker across chunk boundaries, but the underlying contract is still "hope the
model emits parseable JSON". No schema validation of the emitted actions exists on either side.

**Repayment:** native structured tool-calling with JSON-schema validation of arguments; the marker
protocol is retained only as a compatibility shim for older clients.

---

## TD-13 — Hard-coded identifiers and mixed indentation
**Impact: low · Effort: low**

- `config.py:15` — a real Google OAuth client id checked into source as a default.
- `security.py:139` — `packageName="com.aura.ai"` hard-coded inside the verification function.
- `agent.py:318` — `os.getenv("DEPLOYMENT_MODE")` read inline, not in `Settings`.
- `services/ai.py` and `services/rag.py` use 2-space indentation; every other module uses 4.
- No formatter, linter, or type checker is configured. No `pyproject.toml`.

**Repayment:** `pyproject.toml` with ruff + black + mypy; all configuration through typed settings.

---

## Debt summary

| Severity | Count | Items |
| --- | --- | --- |
| Critical | 4 | TD-01, TD-03, TD-04, TD-05 |
| High | 5 | TD-02, TD-06, TD-07, TD-09, TD-11 |
| Medium | 3 | TD-08, TD-10, TD-12 |
| Low | 1 | TD-13 |

**Strategy:** strangler-fig. The new `aura_core` package is built alongside the existing code with no
import from `services/` into `aura_core/`. The v3 gateway is the new entry point; v1/v2 keep working
unchanged until the client migrates. TD-05 is the one item fixed in place immediately, because it is
an open door.
