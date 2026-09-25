# AURA AI — Enterprise Architecture (Phase 2)

This document defines the target architecture. Decisions with meaningful trade-offs are recorded as
ADRs in this directory; each ADR states the rationale, the alternatives rejected, and the expected
impact on scalability, maintainability, security, and operating cost.

---

## 1. Principle

> The AI is not a feature of the web application. The web application is one adapter of the AI.

Everything AURA can *think* lives in `aura_backend/aura_core/`, a framework-free Python package that
imports no web framework, no database driver, and no vendor SDK at its core. FastAPI, Celery,
Postgres, Qdrant, Redis, Ollama, and vLLM are all **adapters plugged into ports**.

Concretely: `aura_core/domain/` and `aura_core/ports/` import only the Python standard library. The
engines (`prompts`, `context`, `memory`, `knowledge`, `tools`, `planning`, `reasoning`, `execution`,
`safety`) depend only on domain + ports. Adapters depend on engines and on vendor SDKs. The
dependency arrow never reverses. This is enforced by a test
(`tests/architecture/test_dependency_rules.py`), not by convention.

---

## 2. Request pipeline

The pipeline requested in the brief, as implemented:

```
                            ┌──────────────────────────────────────┐
   client ──── HTTPS ──────►│  Nginx: TLS, HSTS, headers, limits   │
                            └───────────────┬──────────────────────┘
                                            ▼
                            ┌──────────────────────────────────────┐
                            │  AI Gateway   (aura_core/api/v3)     │  authn, quotas,
                            │  correlation id, request budget      │  cost-weighted rate limit
                            └───────────────┬──────────────────────┘
                                            ▼
   ┌────────────────────────────────────────────────────────────────────────────┐
   │  AI Kernel  (aura_core/kernel)  — orchestrates the stages below            │
   │                                                                            │
   │   1. Safety (input)      injection · jailbreak · PII · size · encoding      │
   │   2. Prompt Manager      versioned template → rendered, budgeted prompt     │
   │   3. Memory Manager      working · episodic · semantic · entity · reflection│
   │   4. Knowledge Manager   hybrid retrieval → rerank → cite → fence as data   │
   │   5. Context Engine      rank · compress · fit to token budget              │
   │   6. Planning Engine     decompose goal → DAG of steps (when agentic)       │
   │   7. Reasoning Engine    strategy: direct | CoT | ToT | reflect + verify    │
   │   8. Tool Router         select · validate args · authorise                 │
   │   9. Execution Engine    run steps/tools with timeout, retry, budget        │
   │  10. LLM Engine          provider routing · cache · stream · fallback       │
   │  11. Safety (output)     PII · secrets · prompt leakage · schema validation │
   │  12. Evaluation          confidence, groundedness, hallucination proxy      │
   └────────────────────────────────────────────────────────────────────────────┘
                                            │
             ┌──────────────────────────────┼──────────────────────────────┐
             ▼                              ▼                              ▼
      Monitoring (Prometheus)       Logging (structured JSON)        Audit (Postgres)
```

Every stage is a class behind an interface, constructed by `aura_core/container.py`. Stages are
independently testable and independently replaceable. A deployment that wants no planning stage
simply doesn't configure one.

---

## 3. Package layout

```
aura_backend/aura_core/
├── domain/          entities, value objects, domain errors, domain events   [stdlib only]
├── ports/           Protocols: LLMProvider, EmbeddingProvider, VectorStore,
│                    MemoryStore, DocumentStore, GraphStore, Cache, EventBus,
│                    Tool, Clock, AuditSink                                   [stdlib only]
├── config/          typed settings, environment profiles, startup validation
├── observability/   structured logging, Prometheus metrics, tracing, correlation
├── llm/             provider registry, router (fallback/cost/latency/LB),
│                    two-tier cache, streaming, token accounting
│   └── providers/   openai_compatible · ollama · echo
├── prompts/         template registry, renderer, versioning, evaluation
├── context/         token budgeting, ranking, compression, assembly
├── memory/          working · episodic · semantic · entity · reflection,
│                    consolidation, decay, retrieval
├── knowledge/       ingestion, chunking, embedding pipeline, hybrid search,
│                    reranking, index manager, document versioning,
│                    knowledge graph, entity/relation extraction
├── tools/           registry, JSON-schema generation, argument validation,
│                    policy/authorisation, sandbox (timeout, concurrency), builtins
├── safety/          input guards, output guards, guard pipeline
├── planning/        goal decomposition, dependency DAG, replanning, priorities
├── reasoning/       direct · chain-of-thought · tree-of-thoughts · reflection ·
│                    verification · confidence scoring
├── execution/       step executor, retries, budgets, progress events
├── kernel/          AIKernel — assembles and runs the pipeline
├── evaluation/      harness, datasets, RAG/answer/regression metrics
├── learning/        02:00 knowledge refresh, source registry, validation,
│                    curation, dataset export, fine-tune recommendation, approval
├── mlops/           model registry, experiment tracking, A/B assignment, rollback
├── ml/              reusable NN module specs (optional torch)
├── security/        rate limiting, audit log, secrets, PII, RBAC
├── api/             FastAPI adapters: v3 gateway, admin, health, metrics
└── adapters/        concrete infrastructure: qdrant, postgres, redis, memory-only
```

---

## 4. Key decisions (full ADRs alongside)

| ADR | Decision | Primary trade-off |
| --- | --- | --- |
| [001](001-hexagonal-core.md) | Hexagonal core, stdlib-only domain and ports | More files and indirection, in exchange for testability without infrastructure and for provider portability |
| [002](002-strangler-migration.md) | Build `aura_core` alongside v1/v2; new `/api/v3` gateway; no imports from `services/` | Temporary duplication, in exchange for zero-downtime migration and no big-bang rewrite risk |
| [003](003-provider-routing.md) | OpenAI-compatible protocol as the universal local-inference contract; policy-driven router | A thin translation layer per non-compatible vendor, in exchange for swapping vLLM/Ollama/TGI/cloud with config only |
| [004](004-memory-taxonomy.md) | Six memory kinds with distinct lifecycles, one retrieval facade; memory content and personal-OS fields encrypted at rest server-side (not E2E) | More concepts to learn, in exchange for correct decay/consolidation semantics per kind; no SQL search over sealed columns |
| [005](005-hybrid-retrieval.md) | BM25 + dense fused by Reciprocal Rank Fusion, then rerank, then score floor | Extra index to maintain, in exchange for large recall gains on keyword/rare-term queries |
| [006](006-safety-pipeline.md) | Retrieved content is data, never instruction; two-sided guard pipeline | Some false positives, in exchange for closing the indirect-injection hole (SEC-02) |
| [007](007-versioned-indexes.md) | Index identity = (name, schema version, embedding model, dimension); alias switch | Extra storage during reindex, in exchange for reversible, zero-downtime index changes |
| [008](008-human-in-the-loop-learning.md) | Automatic knowledge refresh; fine-tuning requires human approval | Slower model iteration, in exchange for never silently changing model behaviour |
| [009](009-observability-first.md) | AI metrics are first-class domain output, not logging afterthoughts | Small runtime cost, in exchange for measurable quality, cost, and drift |
| [010](010-cache-augmented-prefix.md) | CAG-lite: reserved byte-stable knowledge prefix (pins + budget), KV seams later | 80 tokens of the 400-token prefill reserved when pins exist, in exchange for always-on handbook grounding without retrieval luck |
| [011](011-desktop-engineer-agent.md) | Desktop Engineer agent executes on the user's own machine via a host-agent bridge; `SandboxRunner` is the seam, containment is enforced by the daemon, never trusted from the server | A real relaxation of attestation for one platform (a pairing token, not mobile-grade attestation), in exchange for the Engineer persona acting on real local projects instead of only the server Docker workspace (ADR-013) |
| [013](013-agent-workspaces.md) | Persistent per-(user, repo) engineer workspaces; stopped between turns; git runs host-side so the token never enters the container; opt-in (`AURA_SANDBOX_ENABLED` + `COMPOSE_PROFILES=sandbox`) | Peak RAM is `max_running x memory_limit` rather than one container per workspace, in exchange for pause/resume machinery and a reaper that must not fail |
| [014](014-workspace-local-side-effects.md) | Confirmation gates the workspace boundary, not writes inside it (amends ADR-006 rules 3-4) | One prompt per task at `git_push` instead of dozens, in exchange for a pinned allowlist that must be reviewed whenever it grows |

---

## 5. Non-functional targets

| Property | Target | Verified by |
| --- | --- | --- |
| Core importable with zero third-party packages | `import aura_core.kernel` works on bare CPython | `tests/architecture/test_dependency_rules.py` |
| No synchronous I/O on the event loop | all ports are `async` | code review + `tests/architecture` |
| p95 gateway overhead (excl. model time) | < 60 ms | `tests/benchmarks/bench_pipeline.py` |
| Memory profile | flat over 24 h | bounded TTL-LRU everywhere; `aura_cache_entries` gauge |
| Failure visibility | every degradation increments a metric and emits an event | `tests/unit/test_metrics.py` |
| Configuration errors fail at startup | production profile refuses insecure settings | `tests/unit/test_settings_validation.py` |

---

## 6. Deployment topology (Stage 0, single VPS)

```
                    Internet
                       │ 443
              ┌────────▼────────┐
              │      nginx      │  TLS · HSTS · rate limit · gzip · buffering off for SSE
              └────┬───────┬────┘
        /api/v3    │       │  /console · /grafana (operator session)
              ┌────▼────┐  └──────────────┐
              │  aura   │                 │
              │ gateway │◄─── metrics ────┤
              │ (uvicorn│                 │
              │  ×N)    │            ┌────▼─────┐   ┌─────────┐   ┌──────┐
              └──┬──┬───┘            │prometheus│──►│ grafana │   │ loki │
                 │  │                └────┬─────┘   └─────────┘   └──────┘
                 │  │                     │ alerts
                 │  │                ┌────▼───────┐
                 │  │                │alertmanager│
                 │  │                └────────────┘
     ┌───────────┘  └──────────┬──────────────┬─────────────┐
     ▼                         ▼              ▼             ▼
┌─────────┐            ┌────────────┐  ┌───────────┐  ┌──────────┐
│ postgres│            │   qdrant   │  │   redis   │  │  ollama  │
└─────────┘            └────────────┘  └───────────┘  │  / vLLM  │
                                                      └──────────┘
     ▲                                                      ▲
     └──────────── celery workers (realtime / ingestion / learning) ┘
                   celery beat  ── 02:00 knowledge refresh
```

Only Nginx publishes a port. Every backing service is on an internal Docker network.
