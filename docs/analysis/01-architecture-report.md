# AURA AI — Architecture Report (Phase 1)

**Scope:** full repository as of branch `claude/aura-enterprise-platform-al50m6`, commit `57dee3f`.
**Method:** static reading of every backend module, the Flutter client's `lib/core` and `lib/features`
trees, infrastructure manifests, CI workflows, and existing documentation. No code was changed while
producing this report.

---

## 1. System inventory

| Layer | Location | Size | Runtime |
| --- | --- | --- | --- |
| Backend API | `aura_backend/` | ~4,100 LOC Python | FastAPI + Uvicorn (4 workers) |
| Mobile/desktop client | `aura_client/` | 71 Dart files | Flutter 3.11, Riverpod, Drift, go_router |
| Vector store | `qdrant` container | — | Qdrant (cloud or local disk fallback) |
| Relational store | `postgres` container | 6 tables | Postgres 16 + Alembic |
| Cache / broker | `redis` container | — | Redis 7 |
| Async workers | `celery_worker`, `celery_beat` | 4 tasks | Celery 5 |
| IaC | `infra/terraform/` | 9 `.tf` files | Google Cloud Run target |
| CI/CD | `.github/workflows/` | 3 workflows | GitHub Actions → Cloud Run |

## 2. Backend module map

```
aura_backend/
├── main.py                     565 LOC — 22 route handlers + 2 middlewares, single file
├── config.py                    49 LOC — flat pydantic Settings, 18 fields
├── celery_app.py / tasks.py     126 LOC — 4 Celery tasks, 1 beat schedule
├── api/v2/                     381 LOC — sync, auth, chat (SSE), insights
├── services/                  2,006 LOC — 15 modules, all module-level singletons
│   ├── ai.py                  Gemini chat + Ollama fallback
│   ├── agent.py               "orchestrator": routing + sentiment + RAG + tools + fallback
│   ├── agent_router.py        embedding-centroid intent classifier
│   ├── rag.py                 Qdrant client + embeddings + chunking + query
│   ├── memory.py              fact extraction + Qdrant-backed recall
│   ├── ollama.py              local HTTP client
│   ├── security.py            device attestation + Google/Firebase token verify
│   ├── jwt_auth.py            v2 access/refresh JWTs
│   ├── learning_pipeline.py   JSON-file feedback log → LoRA dataset
│   └── …
└── infrastructure/            195 LOC — db/base, db/models, redis_client, observability
```

### 2.1 Control flow of a chat request today

```
Client ──POST /api/chat/agent──► main.py:agent_chat
                                    │  Depends(verify_token)  ── network round-trip to Google
                                    ▼
                        services/agent.py:execute_agent_stream
                          ├─ classify_intent()      → agent_router (embedding centroids)
                          ├─ sentiment_analyzer     → embedding centroids
                          ├─ memory_service.retrieve_relevant_memories()   → Qdrant
                          ├─ rag_service.query_knowledge()                 → Qdrant
                          ├─ string-concatenate everything into system_instruction
                          ├─ genai.Client(...).aio.chats.create(model="gemini-2.5-flash")
                          │    └─ inline tool loop (3 hard-coded Google Drive/RAG closures)
                          ├─ except → Ollama fallback
                          └─ except → hard-coded mock strings
```

### 2.2 Layering assessment

The codebase has **two-and-a-half layers**, not the four a clean architecture requires:

| Clean Architecture layer | Present? | Evidence |
| --- | --- | --- |
| Domain (entities, value objects, invariants) | ✗ | No domain package. Concepts (message, memory, plan, tool) exist only as ad-hoc `dict`s or Pydantic request schemas in `main.py`. |
| Application (use cases, ports) | ✗ | No ports/interfaces anywhere. `services/` mixes use-case logic with I/O. |
| Adapters (repositories, gateways) | ~ | `infrastructure/db/` is a genuine adapter. `services/rag.py`, `services/ollama.py`, `services/drive.py` are adapters *and* use cases in one class. |
| Frameworks/drivers | ✓ | FastAPI, Celery, SQLAlchemy correctly at the edge. |

## 3. Dependency graph (backend)

```
main.py ──► services.ai ──► services.rag ──► qdrant_client, google.genai
   │           └────────────► services.ollama
   ├──► services.agent ──┬─► services.memory ──► services.rag   (circular-ish: memory
   │                     │                                        proxies rag's client)
   │                     ├─► services.rag
   │                     ├─► services.sentiment ──► services.rag
   │                     ├─► services.agent_router ──► services.rag
   │                     ├─► services.budget
   │                     └─► services.drive, tasks
   ├──► services.security ──► firebase_admin, google.oauth2, redis
   └──► api.v2.* ──► services.jwt_auth, infrastructure.db
```

**Findings:**

1. **`services/rag.py` is a god-object hub.** Five other modules import it, and three of them
   (`memory`, `sentiment`, `agent_router`) reach into its **private** members
   (`rag_service._get_embedding`, `rag_service._ensure_collection_exists`,
   `rag_service.client`). Embedding generation, vector storage, collection management, chunking,
   and PDF parsing are one class. Changing the embedding backend requires touching four modules.
2. **Import-time side effects everywhere.** `rag_service = RAGService()` at
   `services/rag.py:287` opens a network connection to Qdrant *at import*. Same pattern in
   `ai.py:108`, `memory.py:246`, `agent.py:365`, `ollama.py`, `security.py:16`. Consequence:
   `import main` cannot succeed without network/disk, unit tests must monkey-patch singletons,
   and configuration cannot be varied per-request or per-tenant.
3. **No dependency injection.** Nothing takes its collaborators as constructor arguments. Every
   dependency is resolved by module-level import of a global. This is the single largest obstacle
   to testability and to running two configurations (e.g. two model tiers) in one process.
4. **Provider lock-in at the call site.** `google.genai` is imported directly in four modules
   (`ai.py`, `agent.py`, `memory.py`, `rag.py`) and the model id `"gemini-2.5-flash"` is a string
   literal in three of them. There is no provider abstraction — adding Anthropic or vLLM means
   editing every call site.

## 4. API surface

| Version | Prefix | Auth | Count | Notes |
| --- | --- | --- | --- | --- |
| v1 | `/api/*` | Google/Firebase ID token verified **per request** | 15 | `main.py` |
| v1 proxy | `/api/tags`, `/api/chat`, `/api/pull`, `/api/delete` | **none** | 4 | Unauthenticated Ollama passthrough |
| v2 | `/api/v2/*` | Locally-signed access JWT | 7 | `api/v2/` |
| health | `/health` | none | 1 | No dependency checks |

`POST /api/chat` (Ollama proxy, `main.py:510`) and `POST /api/pull` (`main.py:524`) take **no
authentication dependency at all** and forward an arbitrary JSON body to the configured Ollama
host. `/api/pull` can be used by any unauthenticated caller to make the server download arbitrary
model weights — a disk-exhaustion and supply-chain vector. See the Security Report.

## 5. Client architecture

`aura_client/lib` is organised feature-first (`features/<domain>/{presentation,data}`) with shared
concerns in `core/`. This part is sound. The AI layer (`core/ai/`, 12 files) mirrors the backend's
concepts client-side: `aura_ai_router.dart`, `orchestrator/agent_orchestrator.dart`,
`offline_agents/native_llama_provider.dart`, `memory/aura_memory.dart`, `knowledge/knowledge_center.dart`.

**Finding:** routing, memory, and knowledge logic is **duplicated** between Dart and Python with no
shared contract. The two implementations will drift. There is no OpenAPI schema or generated client;
the Flutter code hand-parses the `[ACTIONS]` marker (v1) and SSE frames (v2).

## 6. Data architecture

- **Postgres** (`infrastructure/db/models.py`): 6 tables, a clean `SyncableMixin`
  (`user_id`, `created_at`, `updated_at`, `deleted_at`) driving cursor sync. This is the best-designed
  part of the repository.
- **Qdrant**: **one collection** (`aura_knowledge`) holding *both* RAG document chunks and user
  memories, separated only by a `type` payload field (`services/memory.py:151`). Vector dimension is
  decided at runtime from whichever embedding backend answered first
  (`services/rag.py:50` → 768 or 384) and the collection is **silently deleted and recreated** on a
  dimension mismatch (`services/rag.py:71`). A cold start with a different embedding provider destroys
  the knowledge base.
- **Files as databases**: `learning_logs.json` (rewritten wholesale on every append,
  `services/learning_pipeline.py:24`), `telemetry_logs.jsonl` (`main.py:414`), `roles.json`,
  `routing_centroids.json`, `sentiment_centroids.json`. All are container-local and lost on restart.

## 7. Deployment architecture

`docker-compose.yml` defines 6 services with no reverse proxy, no TLS, no resource limits, no
healthchecks except on Postgres, and every backing store's port published to the host
(`6333`, `6379`, `5432`). Terraform targets Cloud Run, which conflicts with the stated VPS goal and
with the Ollama-based local-inference story (Cloud Run has no persistent GPU/model cache).

## 8. Verdict

AURA today is a **competent single-process AI-assisted CRUD application** with a strong sync
protocol and a solid Flutter shell. It is **not** an AI platform: there is no abstraction boundary
between "the AI" and "the web framework", no provider portability, no memory taxonomy, no planning
or reasoning stage, no evaluation, and no way to change a model, a prompt, or a retrieval strategy
without editing and redeploying Python.

The architecture chosen for the rebuild is documented in
[`docs/architecture/00-overview.md`](../architecture/00-overview.md) and the ADRs beside it.
