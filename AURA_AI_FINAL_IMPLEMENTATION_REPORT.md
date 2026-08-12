# AURA AI — Final Implementation Report

Written 2026-08-12, describing the tree at commit `e7c7896`.

This is the record of a mission with one governing constraint: **AURA AI must run its own AI, on its
own servers, with no external inference API anywhere in the system, and it must keep the product it
already was.** Those two halves pull in opposite directions — the fastest way to satisfy the first is
to rewrite everything, and the brief explicitly forbade that. Section 4 is therefore as important as
section 3.

Companion documents:
[Pre-modification audit](AURA_AI_CURRENT_ARCHITECTURE.md) ·
[Production readiness score](AURA_AI_PRODUCTION_READINESS_SCORE.md) ·
[Architecture decision records](docs/architecture/00-overview.md)

---

## 1. Original architecture

The system as found, audited at `e767c4a` and recorded in full in
[`AURA_AI_CURRENT_ARCHITECTURE.md`](AURA_AI_CURRENT_ARCHITECTURE.md).

| Tree | Files | Lines | Role |
| --- | --- | --- | --- |
| `aura_backend/aura_core/` | 90 | 23,342 | v3 AI kernel — hexagonal, stdlib-only domain |
| `aura_backend/services/`, `api/` | 23 | 2,998 | legacy v1/v2 application, still serving |
| `aura_client/lib/` | 65 | 24,041 | Flutter client |

Two things defined the starting point.

**Inference was distributed across three places that should have been one.** The client could call
Google Gemini directly with a key stored on the device, run llama.cpp inference on the device
against GGUF weights it downloaded from Hugging Face, or call the backend. The backend could call
Gemini or Anthropic. Eleven live code paths reached hosted inference — four in the client, seven on
the server — and one of them, the client's Gemini fallback, existed *specifically* to fire when the
backend was unreachable. That is the "hidden external API fallback" the brief forbids by name, and
it meant no server-side policy could be enforced at all.

**The production stack could not start.** `deploy/docker-compose.prod.yml` selected `qdrant`,
`postgres` and `redis` backends. The modules implementing those three selections had never been
written; the container raised `ModuleNotFoundError` on import. Backend CI had never passed on this
repository, and Client CI had never executed `analyze` or `test` — it was pinned to Flutter 3.35.0
(Dart 3.9) against a pubspec requiring `^3.11.4`, so every run failed at dependency resolution
before reaching a check. Both facts were invisible from the badge, because a workflow that fails
early looks the same as one that never ran.

The running VPS (`srv1794216`) was a hand-assembled four-container stack, not this compose file, and
`/opt/aura` was not a git checkout — the deployment had drifted from the repository entirely.

## 2. New architecture

```
┌─ FLUTTER CLIENT ─────────────────────────────────────────────────────┐
│  aura_ai_router → HTTPS → AURA backend.  Nothing else.               │
│  No API key. No model. No weights. No inference. No model path.      │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  HTTPS, bearer token
┌───────────────────────────────┴──────────────────────────────────────┐
│  nginx ── TLS, rate limit, /api/v1,v2 → legacy · /api/v3 → kernel    │
├──────────────────────────────────────────────────────────────────────┤
│  GATEWAY (FastAPI, stateless, N replicas)                            │
│                                                                      │
│   AIKernel ─── safety in → retrieve ∥ recall ∥ route → generate      │
│      │                          → safety out → persist → emit        │
│      ├── rag/          10 strategies + adaptive router               │
│      ├── knowledge/    BM25 + dense → RRF → rerank → floor → MMR     │
│      ├── memory/       6 kinds, per-kind decay, supersede-not-delete │
│      ├── llm/          registry → router (5 policies) → circuit      │
│      ├── safety/       8 guards over 3 trust channels                │
│      ├── tools/        schema-validated registry, taint tracking     │
│      ├── planning/ reasoning/ execution/                             │
│      └── ports/        14 protocols — all the layers above know      │
├──────────────────────────────────────────────────────────────────────┤
│  ADAPTERS   qdrant · postgres · redis · in-memory · ollama · openai- │
│             compatible · echo · sentence-transformers · hash         │
├──────────────────────────────────────────────────────────────────────┤
│  Ollama (7B Q4)   Postgres   Redis   Qdrant   Prometheus/Grafana/Loki │
│  ── all on our own infrastructure; no egress required to serve ──    │
└──────────────────────────────────────────────────────────────────────┘
```

`aura_core` is 101 files / 26,609 lines. `domain/` and `ports/` import nothing outside the standard
library; every layer above depends downward only. This is not a convention — three fitness tests in
`tests/architecture/` fail the build on violation, including one that walks every import at any
nesting depth.

The legacy `services/` and `api/` trees (23 files / 3,212 lines) are untouched in shape and still
serve v1 and v2. `aura_core` never imports from them (ADR-002, strangler-fig).

## 3. What changed

Sixteen commits between the audit baseline and `e7c7896`. The substantive ones:

**Every external inference path is gone, and cannot come back.** Deleted
`llm/providers/gemini.py` and `anthropic.py`; removed `google-genai` from requirements; narrowed
`ProviderConfig._KINDS` to `{openai_compatible, ollama, echo}`; rewrote `services/ai.py` to use
Ollama as its only provider. Enforcement is four independent layers reading **one shared registry**
(`aura_core/config/external_inference.py`) so they cannot drift apart:

| Layer | What it catches | Where |
| --- | --- | --- |
| AST import scan | any import of a banned SDK, at any nesting depth — including lazy imports inside functions | `tests/architecture/test_no_external_inference.py` |
| Source endpoint scan | a banned hostname appearing as a literal anywhere in the tree | same |
| Dependency scan | a banned distribution declared in `requirements*.txt` or `pubspec.yaml` | same |
| Runtime validation | a `base_url` pointing at a banned host — rejected at construction, before a request can be made | `config/settings.py::_reject_external_base_url` |

Getting inference off this system now requires defeating all four deliberately. That is the
strongest claim in this report.

**The client became a client.** Deleted `lib/core/ai/offline_agents/` (5 files), the
`google_generative_ai` and `flutter_llama` dependencies, and a 986 MB GGUF asset tracked in LFS.
Rewrote `aura_ai_router.dart` as server-only, deleting the `AIProvider` enum outright — the client
no longer has a vocabulary for expressing which provider to use, which is the correct way to make a
rule unbreakable rather than merely unbroken. `model_manager_page.dart` became a read-only status
view.

**The production stack boots.** Wrote the three adapter modules the compose file had always
referenced but which had never existed: `redis_cache.py`, `qdrant_store.py`, `postgres_store.py`
(2,214 lines with the two that existed). Added `tests/architecture/test_backends_are_resolvable.py`,
which **parses the real compose file** and builds a container from exactly the backends it selects —
so this class of failure cannot recur silently.

**Ten retrieval strategies and an adaptive router** (`aura_core/rag/`, 8 modules / 1,863 lines) —
section 8.

**A self-hosted cross-encoder reranker** (`knowledge/reranking.py`) — section 6.

**Page-aware document extraction** (`knowledge/documents.py`, `ports/documents.py`) — section 11.

**Four correctness defects fixed in code that predated this mission:**

- *A zero-vector embedding fallback.* When embedding failed, `services/rag.py` returned a vector of
  zeros and indexed it. That poisons the index permanently: a zero vector has undefined cosine
  similarity to everything, so retrieval returns confident nonsense forever after, with no error
  anywhere. It now raises `EmbeddingUnavailableError`.
- *A search could delete the corpus.* `query_knowledge` called `_ensure_collection_exists`, whose
  dimension-mismatch branch deletes and recreates the collection. Reads now pass
  `allow_recreate=False` and refuse.
- *PDF ingestion destroyed page numbers.* Section 11. Every page concatenated into one string, and
  scanned pages dropped without a trace.
- *The sync API silently dropped offline edits.* Conflict detection compared the client's
  `client_updated_at` against the server's `updated_at`, which the server sets on write — so any
  edit made offline before the last server write was discarded as stale. Fixed with a new
  `client_updated_at` column (migration `0002`), a `conflict_comparand()` free function, and 11 unit
  tests including one that asserts the *old* comparand would have rejected a valid edit. This
  correction contradicts something I had written myself in the migration guide, which called
  `api/v2/sync.py` "correct and efficient as written". It was not.

**CI works for the first time.** Unpinned Flutter to `channel: stable`; declared
`prometheus_client`; fixed two event-loop bugs in the sync integration tests (a bare `TestClient`
opens a fresh anyio portal per request — measured, three distinct loops across four requests — and
`infrastructure/db/base.py` caches its engine in a module global). Run `31390460001`: all four jobs
green on `9b2c27b`.

## 4. What was intentionally NOT changed

The brief's first three rules forbid redesigning the existing application because a different
architecture would be preferable. Held to, deliberately:

| Preserved | Why it was tempting to change it, and why I didn't |
| --- | --- |
| Every screen, route, widget and theme | The client diff is exclusively *removal* of inference code plus the four call-site repairs that removal required. `home_page`, `chat_page`, `settings_page` kept their layout and navigation. |
| Branding, naming, `app_en.arb` copy | One string changed: `onDeviceInference` → `serverInference`, because leaving it would have described the system falsely to the user. |
| `services/` and `api/` v1/v2 structure | A single unified API would be cleaner. It would also break every deployed client. The strangler boundary (ADR-002) is the whole point: v3 is added beside, not on top. |
| Firebase / Firestore auth and sync | Not AI infrastructure, and Rule 19 permits ordinary external services. Replacing it would have been architectural destruction with no bearing on the mission. |
| Drift, Riverpod, go_router | Working, idiomatic, and not the problem. |
| The legacy ORM | `postgres_store.py` uses asyncpg directly rather than the ORM, per ADR-002 — the new adapter does not reach into the old tree's models, and the old tree keeps its ORM. |
| Greedy context packing | Optimal packing is a knapsack problem. Measured cost of greedy: 1.45 ms. A solver would cost more than the tokens it saves. |
| Postgres connection pool defaults | Documented, not retuned. Retuning without load data is guessing dressed as work. |

One reversal worth recording. I added a production boot gate rejecting any provider whose
`is_local` flag was false, reasoning that a self-hosted deployment should have only local providers.
That was wrong: `is_local` is a *routing hint* for the `LOCAL_FIRST` policy and defaults to false,
so the gate would have rejected every correctly-configured deployment on startup. Reverted, with a
comment at the site explaining why the obvious check is the wrong one. Self-hosting is enforced by
`base_url` validation, which tests what actually matters.

## 5. AI infrastructure

Everything runs on our own machines. `deploy/docker-compose.prod.yml` composes sixteen services
across three networks (`edge`, `data`, `observability`), with secrets as files rather than
environment variables:

| Service | Role | Network |
| --- | --- | --- |
| `nginx` | TLS termination, rate limiting, version routing | edge |
| `gateway` | FastAPI, stateless, health split (live/ready) | edge + data |
| `worker-realtime`, `worker-ingestion` | Celery, separate queues so a large ingestion cannot starve chat | data |
| `beat` | scheduler — decay sweeps, nightly index refresh | data |
| `ollama` | local generation, `OLLAMA_MAX_LOADED_MODELS: 1` | data |
| `postgres` | documents, memories, sync | data |
| `qdrant` | vectors, one collection per index identity | data |
| `redis` | cache, rate-limit state, Celery broker | data |
| `prometheus`, `alertmanager` | metrics, alert routing | observability |
| `grafana` | three provisioned dashboards | observability |
| `loki`, `promtail` | log aggregation and shipping | observability |
| `node-exporter` | host metrics | observability |
| `backup` | scheduled dump with restore verification | data |

Two queues rather than one is a deliberate isolation choice: ingesting a 200-page PDF and answering
a chat turn have incompatible latency profiles, and sharing a queue means the second waits on the
first.

**Storage is behind ports, and the backend is a configuration value.** `AURA_VECTOR_BACKEND`,
`AURA_DOCUMENT_BACKEND`, `AURA_MEMORY_BACKEND`, `AURA_CACHE_BACKEND` each select an adapter. The
in-memory set is what the 1247 tests run against, which is why the suite needs no infrastructure and
finishes in about seven seconds.

Three adapter decisions that are not obvious:

- **`redis_cache.incr` raises; every other method swallows.** A cache miss on a failed Redis is
  harmless. But `incr` backs the rate limiter, and the rate limiter has a `fail_open` flag — if the
  adapter silently returned a made-up count, that flag would be a lie and the operator's choice
  about whether to fail open would have been made for them by an adapter.
- **`qdrant_store.ensure_index` never recreates.** On a dimension or distance mismatch it raises
  `ConflictError`. Recreating is data loss disguised as recovery.
- **`postgres_store.put` replaces a document's chunks in one transaction.** A partial write leaves
  a document whose chunks are half old and half new, which no reader can detect.

## 6. LLM runtime

`aura_core/llm/` — 11 files / 2,773 lines.

**Providers** (`llm/providers/`) are declared as configuration, not code (ADR-003). Three kinds
remain: `ollama`, `openai_compatible` (which covers vLLM, llama.cpp's server, TGI, LocalAI — any
self-hosted OpenAI-shaped endpoint), and `echo` for tests. `base_url` is validated against the
banned-host registry at construction.

**Routing** (`llm/router.py`) has five policies — `LOCAL_FIRST` (default), `QUALITY_FIRST`,
`FASTEST`, `CHEAPEST`, `BALANCED` — over a circuit breaker with EWMA per-provider latency and
TTFT. The rule that matters most: **failover only happens before the first token.** Once a chunk has
been emitted, a retry would duplicate visible output, so the router raises instead
(`llm/router.py:479`), asserted for every streaming adapter.

**Caching** is two-tier — exact-match then semantic — restricted to `temperature == 0` and tool-free
turns and namespaced per user. Both restrictions are correctness, not caution: serving a stored
sample for a sampled request makes non-determinism *inconsistent* rather than absent, and a cached
answer grounded in one user's documents must never reach another.

**Reranking** (`knowledge/reranking.py`) has two adapters behind one port. The default is a
zero-dependency heuristic (lexical overlap + position). `CrossEncoderReranker` runs
ms-marco-MiniLM-L-6-v2 locally through sentence-transformers, loaded lazily under a lock inside
`asyncio.to_thread`, selected with `AURA_KNOWLEDGE_RERANK_PROVIDER=cross_encoder`. It ranks
materially better where the corpus and the question use different vocabulary — the case a lexical
reranker cannot help with at all. Every failure path (load failure, timeout, shape mismatch)
degrades to the heuristic, never to fusion order, so enabling it cannot take search down. A
`_load_failed` latch stops a broken model being retried on every query.

One implementation detail that took a second attempt: the tail sentinel for unscored candidates is
derived from the observed minimum, not from zero. Cross-encoder logits are frequently negative, and
a zero sentinel silently promoted unscored candidates above scored ones.

## 7. Models

| Role | Model | Where it runs | Notes |
| --- | --- | --- | --- |
| Generation | `qwen2.5:7b-instruct-q4_K_M` | Ollama, our host | Compose default; `AURA_LOCAL_MODEL` overrides |
| Embedding | `BAAI/bge-small-en-v1.5`, 384-dim | sentence-transformers, in-process | Compose default |
| Embedding (tests) | `hash-embed-v1`, 384-dim | deterministic, no model | Settings default — makes the suite reproducible and offline |
| Reranking | `cross-encoder/ms-marco-MiniLM-L-6-v2` | sentence-transformers, worker thread | Opt-in |
| Reranking (default) | heuristic | pure Python | Zero dependencies |

`AURA_MODEL_ALLOWLIST` restricts which model names a request may name at all, so a compromised
client cannot select an unvetted model.

Nothing is downloaded at request time. Weights are pulled once into the `ollama_models` volume and
the sentence-transformers cache; after that the system serves with no egress, which is Rule 20 —
functional with the internet completely unavailable.

**Index identity is `(logical_name, schema_version, embedding_model_id, dimension)`** (ADR-007).
Changing the embedding model therefore *cannot* silently mix incompatible vectors: it names a
different index. Promotion is an alias switch, so rollback is O(1) and needs no reindex.

## 8. RAG strategies

`aura_core/rag/` — ten strategies as composable objects plus a router, 90–100% covered.

| Strategy | What it does | Composes over |
| --- | --- | --- |
| `simple` | hybrid BM25 + dense → RRF (k=60) → rerank → score floor → MMR | the corpus |
| `memory` | recalls what the user said, kept separate from corpus chunks | `simple` |
| `multi_query` | generates N query variants, fuses the results | `simple` |
| `hyde` | drafts a hypothetical answer, retrieves against that | `simple` |
| `corrective` | grades the first pass; retries a different way if thin | `simple` → `multi_query` |
| `self_rag` | decides whether to retrieve at all, then grades evidence | `corrective` |
| `agentic` | iterative retrieve-reflect-refine with a step budget | `simple` |
| `graph` | multi-hop traversal for relational questions | `simple` |
| `multimodal` | filters on the modality and page that extraction now writes — section 11 | `simple` |
| `adaptive` | classifies the query and dispatches to the above | all |

The composition is the interesting part. `corrective` falls back to `multi_query` specifically
because vocabulary mismatch is the commonest reason a first pass is thin, and that is what
Multi-Query attacks. `self_rag` wraps `corrective` rather than `simple`, so that evidence it grades
has already had one corrective attempt — grading a result that was never allowed to retry would
report weak grounding it could have fixed.

`AdaptiveRouter` classifies into seven non-exclusive classes — `SELF_CONTAINED`, `VERIFICATION`,
`MEDIA`, `COMPARATIVE`, `RELATIONAL`, `PERSONAL`, `FACTUAL` — and composes. `SELF_CONTAINED` is
checked first because "rewrite this paragraph" needs no corpus at all, and retrieving for it is pure
latency. Unmatched queries default to `FACTUAL`, which is both the correct reading of a plain
question and the cheapest path.

`memory` and `graph` are optional: passing `None` maps their routes to plain retrieval rather than
raising, so a deployment without a graph store still routes relational questions somewhere sensible.

Every strategy returns a `StrategyTrace` with candidate counts, notes and children, so a
disappointing answer can be explained rather than guessed at.

**What I will not claim:** that these improve answers on this corpus. They are correctly implemented
and tested. Whether `hyde` beats `simple` here is unmeasured, and on some corpora it is worse — HyDE
is known to hurt on entity-specific questions, which is exactly why the router selects it rather
than defaulting to it.

## 9. Memory

`aura_core/memory/` plus `domain/memory.py` — six kinds (`WORKING`, `EPISODIC`, `SEMANTIC`,
`ENTITY`, `PROCEDURAL`, `REFLECTION`), each with its own half-life and its own retrieval weights
(ADR-004). A working note and a stated preference should not decay at the same rate; one is
scaffolding for a conversation, the other is a fact about the person.

Three properties are load-bearing:

- **Supersede, don't delete.** A changed preference writes a new memory pointing at the old one.
  History is auditable and a mistaken update is recoverable.
- **Provenance-scoped forgetting.** "Forget what I told you about X" removes memories derived from
  that source, not everything matching a string.
- **Per-owner quotas**, so one user's memory growth cannot degrade another's retrieval.

Memories reach the prompt in `outcome.memories`, never merged into `result.chunks`. Rendering a
memory as a retrieved source would cite the corpus for the user's own statement — which reads to the
user as the system inventing a document.

## 10. Agents

Two agent paths, deliberately.

**The kernel path** (`planning/`, `reasoning/`, `execution/`, `tools/`) — `PlanningEngine`
decomposes a goal into steps, `ExecutionEngine` runs them under a budget, `ReasoningEngine`
implements chain-of-thought and tree-of-thought with explicit thought nodes and paths. Tools go
through a schema-validated registry with a policy layer; `FunctionTool` rejects a synchronous
handler at construction rather than letting one silently park the event loop in production.

**The legacy path** (`services/agent.py` + `services/ollama.py`) — native Ollama tool calling,
`max_tool_rounds=4`. This exists because v1/v2 clients still use it and Rule 2 says not to redesign
what works.

**Tool taint tracking.** A tool result is `TrustChannel.DATA`, and data that entered through a tool
is marked as such for the rest of the turn, so a tool cannot launder instructions into the system
channel by returning them as output.

I fixed a regression I had introduced here: `generate_agent_stream` originally caught every
exception and yielded it into the stream, while `agent.py` had an unconditional `return` that made
the debug mock fallback unreachable. Combined, a transport failure produced a stream containing an
error message rather than an error — indistinguishable to the caller from a real answer. It now
raises if nothing has been emitted yet, and degrades in-stream only after the first token, where
raising is no longer possible.

## 11. Multimodal

Half of this is now real, and the half that is not is precisely identified.

**What exists.** `aura_core/knowledge/documents.py` extracts a PDF page by page and tags each block
with a modality and a page number. Those two keys travel into chunk metadata, from there into the
vector payload (`IndexManager._payload_for` already forwarded scalar metadata), and from there back
onto retrieved chunks — so `MultimodalRAG`'s filter now selects real content instead of an empty set,
and `Chunk.citation()` can say "page 4" rather than "chunk 7".

The defect this fixed was not in retrieval at all. The legacy PDF path concatenated every page into
one string, so page numbers were destroyed *at ingestion*. No retrieval strategy, however good,
could ever have answered "what does page 4 say" — the information had already been thrown away. And
a page that was a scan extracted to nothing and was silently dropped, so a twenty-page scanned
contract could be indexed as five pages of text with no record that fifteen pages went unread.

Four modalities are produced: `pdf_page`, `table` (a conservative layout heuristic — a majority of
lines showing two or more column gaps), `image`, and `text` for plain uploads. `pdf_page` is
deliberately inside the retrieval filter's accepted set: without it, "what does page 4 say" could
not be answered, and that is a more common question than "what does the diagram show".

**An unreadable page becomes a block that says it is unreadable.** A page with images and no text
layer is indexed with text naming the page, the number of images on it, and the fact that OCR is not
configured. Asked about page 7 of a scanned contract, the system retrieves an honest account of the
gap instead of answering confidently from page 6. That is the design decision worth defending here:
indexing a truthful statement about missing content beats indexing nothing, because silence is
indistinguishable from absence.

**What does not exist.** No OCR and no vision model. `ports/documents.py` defines the `OcrEngine`
port and `apply_ocr()` is the place an adapter plugs in — both tested, including every degradation
path — but **no production adapter ships**. There is a second, smaller gap behind it:
`pypdf` counts a page's images with no help, which is all the scan/blank distinction needs, but
*decoding* those images to bytes calls into Pillow, which this platform does not declare. So
`image_extraction_available()` returns false on a stock deployment and `apply_ocr` correctly reports
that it never had the pixels — a different finding from "OCR could not read them", and an operator
debugging an empty corpus needs to tell those apart.

Completing it is Pillow plus a Tesseract adapter: a class with one method. What it needs that I do
not have is hardware to measure the cost on, next to a 7B generator.

Also absent: audio and video (the modality constants exist, nothing produces them), and layout-aware
table structure — a table is currently detected and tagged, not parsed into cells.

## 12. Graph

`GraphStore` (`ports/storage.py`) with an in-memory implementation and entity/relation extraction in
`knowledge/extraction.py`. `GraphRAG` (section 8) traverses it for relational questions, where the
answer is a path rather than a passage — vector search is poor at these, because the sentence
stating a dependency often resembles neither the question nor the other fact needed to compose the
answer.

Traversal is delegated to `GraphStore.neighbours`, which does bounded breadth-first expansion and
clamps depth internally. An unbounded walk driven by user input is a denial-of-service primitive,
and re-implementing the loop in the strategy would duplicate that cap while being free to disagree
with it. `max_hops` defaults to 2: three hops on a well-connected graph reaches most of it,
producing a context full of loosely-related edges — worse than not using the graph at all.

Paths come back as `(subject, predicate, object)` triples of entity *names*, not keys, because the
point of returning them is that a person can read the derivation instead of taking a conclusion on
trust.

**This section is where I was most wrong, and the record matters more than the outcome.** My first
`GraphRAG` had four defects: it called `find_entities` positionally when the parameter is
keyword-only; it treated `neighbours()` as returning a flat list when it returns an
`(entities, relations)` tuple; it read `.subject`/`.object` on `Relation`, which has
`subject_key`/`object_key`; and it re-implemented a BFS that the store already does. **All five of
its tests passed**, because I had written a `FakeGraph` that returned flat objects with the attribute
names I had assumed. The strategy could not have traversed a real graph at all.

Rewriting it against the real interface — and replacing the fake with the real
`InMemoryGraphStore` — immediately exposed a **fifth** defect the fake had also hidden:
`find_entities` matches its needle as a substring *of* an entity name, so passing the whole query
never matches anything. Fixed with `_seed_entities()`: try the query as-is (correct and one round
trip for a user typing just "Auth Service"), then capitalised phrases (entity mentions in English
prose are overwhelmingly proper nouns, and this keeps multi-word names intact), then individual
significant words.

The generalisable lesson, now applied elsewhere: **a fake that does not match the type it stands in
for hides exactly the defects a test exists to catch.** Where a real in-memory implementation
exists, use it.

## 13. Training pipeline

Complete as a pipeline, and honest about having never run.

`ml/nn.py` declares ten architectures as validated specifications — transformer, CNN, recurrent,
autoencoder, graph network, time series, tabular, recommender, anomaly detector — with `build(spec)`
constructing real modules when torch is present and `torch_available()` reporting when it is not.
Torch is not installed here, so `build` has never produced a model in this environment. The specs
and their validation are tested; the construction path is not.

`learning/curation.py` is the part that would actually feed a training run: it collects
`FeedbackRecord`s, deduplicates by shingle Jaccard, computes dataset statistics, and emits a
`FineTuneRecommendation` (LoRA by default, with epoch and rank suggestions derived from dataset
size). `dataset_digest()` hashes the curated set, which is what makes the approval binding in
section 15 possible.

`mlops/model_registry.py` carries versions through `DEVELOPMENT → STAGING → CANARY → PRODUCTION →
ARCHIVED` with A/B assignment by stable hash.

**Nothing has been trained. No model has been fine-tuned. No version has been promoted.** The
plumbing is real and tested; the ML is aspirational, and the readiness score marks AI/ML at 5 for
exactly this reason.

## 14. Evaluation

`evaluation/harness.py` — `RetrievalEvaluator` computes recall@k, precision@k, MRR and nDCG;
`AnswerEvaluator` computes token-F1, groundedness and refusal detection; `LLMJudge` scores with a
local model; `RegressionGate` fails a run when a metric drops below a recorded baseline.

The gate is wired into the nightly index refresh: a new index is built, evaluated, and promoted by
alias switch **only if** it does not regress. Rollback is another alias switch, and needs no
approval (section 15).

What is missing is not code. **There is no labelled evaluation suite for this corpus.** The metrics
are implemented and tested against synthetic cases; they have never scored real retrieval. Until
that exists, "ten strategies" is a count, not a demonstrated capability — which is the single
cheapest thing anyone could do to make this system's quality claims real.

## 15. Security

`docs/reports/security-audit.md` has the full treatment. The substance:

**Prompt injection, four layers** (SEC-02). Content arrives on one of three trust channels —
`SYSTEM`, `USER`, `DATA`. Retrieved documents and tool results are always `DATA` and are rendered
inside a nonce-delimited fence carrying provenance, so instructions in a document cannot be
mistaken for instructions from the operator. `Message.__post_init__` refuses to construct a
`SYSTEM`-role message on a non-`SYSTEM` channel, which makes the invariant a type property rather
than a review item. Ingestion quarantines suspicious content; tool output carries taint forward.

**Eight guards** across three stages (`safety/guards.py`): size, encoding, prompt injection,
jailbreak, PII, secrets, system-prompt leak, groundedness. `SafetyPipeline` composes them and
returns a `SafetyDecision` — never a silent mutation.

**Approval binding** (ADR-008). An approval binds to *person + time + artefact digest* and expires
after 14 days. Approving "the fine-tune" does not approve a different dataset that appeared
afterwards, because the digest is part of what was approved. **Rollback is never gated** — requiring
approval to undo makes the safe action slower than the unsafe one, which is how outages get
extended.

**Rate limiting** is cost-weighted and Redis-backed, with an explicit `fail_open` choice (see
section 5 for why the adapter refuses to fake it).

**Logging is content-free by construction.** No prompt, document text, or memory content reaches a
log line.

**What has not happened:** no penetration test, no CVE scanning in CI, no formal threat model, and
the Firebase auth path is inherited rather than reviewed. Design plus tests is not a penetration
test, and the readiness score marks Security at 6 for that reason. Adding `pip-audit` to CI is hours
of work and would move it.

## 16. Performance

Full detail in [`docs/reports/performance-report.md`](docs/reports/performance-report.md). All ten
Phase-1 findings are closed; the two that mattered:

- **Four serial embeddings of the same message per turn** → one, memoised for the request scope,
  with routing, retrieval and memory under a single `asyncio.gather`.
- **Synchronous clients inside `async def` handlers** — `QdrantClient`, `SentenceTransformer.encode`
  and `httpx.Client` each parked the whole event loop, so concurrency was bounded by worker count,
  not by work. Every port method is now a coroutine, enforced by
  `test_dependency_rules.py::test_ports_are_async`, and CPU-bound encoding runs in a worker thread.

Measured pipeline overhead: **p95 4.56 ms** against a 60 ms budget. Generation is 0.5–5 s, so AURA's
own work is under 1% of a turn — which is the useful conclusion, because it says where optimisation
effort should *not* go.

**The condition on that number matters more than the number.** It was measured with in-memory
adapters and an echo provider. It measured the parts that are not the bottleneck. There is no
measurement with Postgres, Qdrant, Redis or a real model in the path, and the 478 turns/s
concurrency figure in the benchmark report describes an echo provider and says nothing about real
capacity. The cross-encoder's 100–300 ms is *inferred* from published MiniLM-L6 throughput, not
measured, because this environment has no torch — it is labelled as an estimate everywhere it
appears, and it should be measured on real hardware before anyone enables it next to a 7B generator.

Every in-process cache is a bounded TTL-LRU with tested eviction. The legacy system's five unbounded
dicts are why `rag.py` disabled the local encoder outside debug mode "to prevent OOM crash" —
treating the symptom instead of bounding the cache.

## 17. Deployment

`deploy/` — production compose (11 services), nginx config, `postgresql.conf`, secret bootstrap,
backup and restore scripts, Prometheus/Alertmanager/Loki/Promtail config, three provisioned Grafana
dashboards, and alert rules including one that fires on an *active security bypass* rather than only
on resource symptoms.

Three CI workflows: `backend-ci.yml`, `client-ci.yml`, `deploy-backend.yml`. Backend CI runs lint,
type check, the 1247-test suite with coverage, and the architecture fitness tests — including the
self-hosting scan and the compose-file backend resolution test, so a regression in either is a build
failure rather than a discovery in production.

Health is split: liveness checks only that the process is up; readiness checks dependencies. A
single combined endpoint gets a container killed during a transient database blip.

**What is honest about this section:** the stack has been deployed exactly zero times from this
compose file. It is verified to *construct* — the fitness test builds the real container from the
real compose file's backend selection — and that is a different claim from verified to *run*. The
live VPS is still the hand-assembled four-container stack from section 1, and `/opt/aura` is not a
git checkout, so the first real deployment is also a migration.

The restore script exists and its verification step is written. It has never been executed against a
staging copy. One drill would be a couple of hours and would move Reliability off 5.

## 18. Tests

| Suite | Count | Notes |
| --- | --- | --- |
| `aura_backend/tests/` | **1277 passed**, 8 deselected | ~7 s, no infrastructure required |
| Legacy root suite | **61 passed, 2 skipped** | 2 skipped pending Docker |
| Coverage on `aura_core` | **90%** | 11,059 statements, 1,159 uncovered |
| Test code | 41 files / ~13,700 lines | roughly half the size of the code it covers |

Three fitness-test groups make architecture a build failure rather than a review comment:
`test_dependency_rules.py` (layer direction, stdlib-only domain, async ports),
`test_no_external_inference.py` (the four layers in section 3), and
`test_backends_are_resolvable.py` (every backend name constructs; the production compose file's
selection is built exactly as written).

**The deduction I would defend hardest in the readiness score is on Testing, and it belongs here
too: fakes concealed real defects twice in this work.** Section 12 is one case, and it sat at 90%
coverage while `GraphRAG` was incapable of traversing a graph. Coverage measures execution, not
correctness. 90% is real and useful and it is not evidence that the system works.

The extraction work in section 11 is the method corrected. Its tests build real PDFs byte by byte
and hand them to real `pypdf`, and that decision paid immediately: it caught a regex that counted
column gaps by consuming the character between them (so no table was ever detected while every
prose test still passed), and then an end-to-end assertion caught the payload round trip dropping
`modality` and `page` — which would have left the feature filterable but uncitable, working in the
store and invisible to the reader. Neither defect was reachable from a test double.

## 19. Remaining limitations

Ordered by how much they matter, not by how easy they are.

1. **It has never served a user.** Every ceiling in the readiness score is this one ceiling. No
   production traffic, no real corpus, no load test with the real data tier.
2. **Retrieval quality is unmeasured.** The harness exists; there is no labelled suite. Ten
   strategies with no measurement is a count.
3. **No OCR and no vision.** Section 11. PDF page structure and modality now survive ingestion, so
   `multimodal` retrieval works on real content — but a scanned page is still indexed as a
   placeholder saying it could not be read. Needs Pillow and one `OcrEngine` adapter. Audio and
   video have no extraction at all.
4. **Nothing has been trained or promoted.** Section 13. The ML pipeline is plumbing.
5. **Performance numbers describe in-memory adapters and an echo provider.** Section 16. The
   interesting numbers do not exist yet.
6. **Security is designed, not verified.** No penetration test, no CVE scan in CI, no threat model.
7. **The restore path has never been executed.** Neither has any chaos or recovery drill.
8. **BM25 is process-local and rebuilt at startup.** Correct at this corpus size and documented; the
   point at which it stops being correct is in the Scalability Report.
9. **Only Ollama and echo have actually run.** vLLM and llama.cpp are supported by protocol.
10. **The live VPS does not match this repository.** The first deployment is also a reconciliation.

## 20. Future hardware upgrade recommendations

I do not have this VPS's specifications — no CPU, RAM, GPU or disk figures for `srv1794216` — so
these are stated as thresholds against the constraint that actually binds, not as a purchase order.
This is also why `AI_HARDWARE_CAPACITY_REPORT.md` is not in this deliverable set: writing capacity
numbers without the hardware they describe would be fabrication, and it is the one gap in the brief
I am leaving open rather than filling with plausible-looking figures.

**Generation concurrency binds first, at every scale.** Everything else is a rounding error against
0.5–5 s per turn.

| Stage | Trigger | Change | Why this one |
| --- | --- | --- | --- |
| **0 — now** | ≤ ~10 concurrent turns | Single host, 7B Q4 on CPU, 16 GB, one resident model | `OLLAMA_MAX_LOADED_MODELS: 1` is deliberate — a second model evicts the first on 16 GB and turns every other request into a reload |
| **1** | TTFT p95 > 3 s | **Move inference to its own host.** A single consumer GPU (24 GB) serves a 7B–14B model with an order-of-magnitude better TTFT | This is `AURA_PROVIDER_LOCAL_BASE_URL`, a *configuration* change — the payoff for declaring providers as data (ADR-003) |
| **1b** | Enabling the cross-encoder | Measure first. On CPU shared with a 7B generator, do not | 100–300 ms per retrieval is *inferred*, not measured (section 16) |
| **2** | Gateway CPU-bound before inference saturates | Scale gateway replicas — stateless, all shared state already in Redis/Postgres | Only caveat is the process-local BM25 index, rebuilt per replica at startup |
| **3** | Postgres or Qdrant becomes the bottleneck | Managed Postgres with read replicas; Qdrant cluster sharded by `user_id` | `user_id` is the natural shard key because retrieval is already owner-scoped |
| **4** | Fine-tuning becomes real | Dedicated training host, ≥ 48 GB VRAM for LoRA on a 7B | Never the serving host — a training job starves inference |

Two things I would spend money on before any hardware:

- **A labelled evaluation suite.** It costs a person a week and it converts every quality claim in
  this document from a design argument into a measurement. No hardware purchase does that.
- **One real deployment with real traffic.** Ten of the twenty limitations above dissolve the moment
  this system serves someone. Nothing else on this list competes.

---

## Verification of the absolute rules

| Rule | Requirement | Status | How it is enforced |
| --- | --- | --- | --- |
| 1–3 | Do not redesign the app, UI, navigation or branding | ✅ | Client diff is removal + call-site repair only; one string changed (section 4) |
| 4–6, 17–19 | No external AI/LLM API, no hidden fallback | ✅ | **External AI APIs: NONE** — four independent checks over one registry |
| 7–13 | All AI processing on our servers; client is a client | ✅ | Ollama + local embeddings; client has no provider vocabulary |
| 14–16 | No model in the APK, on the device, or inferring on-device | ✅ | **Mobile AI models: NONE** — 5 files, 2 packages and a 986 MB asset deleted; green release build |
| 20 | Functional with no internet | ✅ | Weights resident in volumes; no egress on the serving path |
| 30 | Client must not know model path, weights, runtime, vector DB or model server | ✅ | `AIProvider` enum deleted; `model_manager_page` is read-only status |
| 39–40 | Improvements must be explained; no architectural destruction | ✅ | 9 ADRs; section 4 lists what was preserved and why |
| 42 | External API audit | ✅ | **External AI APIs: NONE · Mobile AI models: NONE** |
| 44 | Final report + honest score | ✅ | This document + [readiness score](AURA_AI_PRODUCTION_READINESS_SCORE.md) at **6.2/10** |
| 46 | Inspect, audit, plan, then implement | ✅ | [Audit](AURA_AI_CURRENT_ARCHITECTURE.md) at `e767c4a`, then 16 commits |

**Overall production readiness: 6.2/10.** A well-architected system with unusually good failure
handling and unusually honest empty-result semantics, which has never met a user, a real corpus, or
production load. The engineering is largely sound. The evidence that it works is largely absent, and
the fastest way to change that is to deploy it.
