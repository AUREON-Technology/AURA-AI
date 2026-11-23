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

Rendered diagrams of this pipeline, the layering, the deployment topologies, and the Engineer
agent are in [`diagrams/`](diagrams/README.md).

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
| [016](016-agent-platform.md) | Every persona runs one bounded plan/execute/replan loop with five data-driven budgets, recorded as a durable run, with memory isolated per (user, agent scope) | One code path and an auditable run ledger, in exchange for budgets that must be tuned per persona and a second table set to migrate |
| [018](018-adaptive-platform.md) | *(Proposed)* Adaptive Intelligence Layer emits only declarative data the unchanged core executes; extensions only via reviewed PR | Every new capability kind needs a human-written primitive, in exchange for an inspectable, revertible adaptation surface |
| [019](019-one-project-per-install.md) | *(Proposed)* One project per install; `project_id` key without tenancy semantics | An install per connected system, in exchange for process-level isolation with no tenant boundary to retrofit |
| [020](020-universal-semantic-model.md) | *(Proposed)* AUSM with mandatory provenance; versioned, diffable Project Twin | Merges are proposals an admin reviews, in exchange for source-independent downstream logic |
| [021](021-connector-ports-and-credentials.md) | *(Proposed)* Read-only connector ports, per-connector identity mode, credential vault (`secret_ref`) | Live reads load the target system, in exchange for no copied customer data and no write path in v1 |
| [022](022-untrusted-discovery-and-query-templates.md) | *(Proposed)* Discovered text is untrusted DATA; reads only via allowlisted, parameterized `QueryTemplate`s | Ad-hoc analytics need a new primitive, in exchange for no free-form SQL and no schema-borne injection |
| [023](023-capability-registry.md) | *(Proposed)* Domain-agnostic capability primitives as data; evidence-linked planning | No industry shortcuts, in exchange for reproducible, explainable capability selection |
| [024](024-blueprint-lifecycle.md) | *(Proposed)* Blueprint propose → evaluate → digest-bound approve → alias deploy → rollback; nothing auto-applies in v1 | Change throughput bounded by Super Admin review, in exchange for an approver and revert path on every change |
| [025](025-tool-output-schema.md) | *(Proposed)* Optional `ToolSpec.output_schema` with post-execution validation | One more field to maintain, in exchange for typed UI binding and golden evals |
| [026](026-deterministic-first-compiler.md) | *(Proposed)* Deterministic structure; model only labels/proposes; mandatory CPU degraded mode | Label quality depends on the GPU plane, in exchange for reproducible twins and an unchanged self-hosting invariant |
| [027](027-ui-capability-manifest.md) | *(Proposed)* Closed-vocabulary UI Capability Manifest; staged surfaces | Rich screens need new view types, in exchange for zero client releases per project |
| [028](028-project-zero.md) | *(Proposed)* Personal AURA becomes Project 0; zero-knowledge tables never discovered | A staged parity/shadow migration, in exchange for one code path |
| [029](029-service-principals-and-caller-context.md) | Service keys scoped to named routes; caller evidence on the DATA channel tainting the turn; structured output repaired once then failing closed | A second principal class every caller-identity check must consider, in exchange for system-to-system integration without a person's token |
| [030](030-hosted-open-weight-inference.md) | *(Proposed; Phase 1 built)* Hosted open-weight APIs become a third host category, gated by `AURA_ALLOW_HOSTED_INFERENCE`, open-weight model IDs only, never `is_local` | Prompts and retrieved content leave our infrastructure when enabled, in exchange for large models at no cost within free quotas |
| [031](031-image-understanding.md) | *(Proposed; Phases 1–6 built)* Attached images are described by a self-hosted model in the `vision` pool and enter the turn as DATA caller evidence; never sent to a third party, never persisted | One extra generation per image turn and some loss of pixel-level detail, in exchange for keeping ADR-006 trust semantics and `Message` unchanged |
| [032](032-personal-os-single-store.md) | *(Accepted 2026-10-31; built)* One personal-OS store: each user gets a data key, held server-side and wrapped by `AURA_DATA_ENCRYPTION_KEYS`, which both the devices and the agent use; the agent reads and writes the synced tables | The synced personal-OS fields are no longer zero-knowledge, in exchange for an agent that can act on the user's real items and devices that share one key |
| [033](033-claude-for-complex-requests.md) | *(Proposed; built, not proven)* Anthropic's Claude as one gated provider kind (`claude`, `AURA_ALLOW_CLAUDE_INFERENCE`); interactive turns whose message scores as complex prefer the `reasoning` pool it declares, with the local model as fallback | Complex turns' prompts leave our infrastructure and cost money when enabled, in exchange for frontier-quality answers where the CPU model is weakest; the self-hosting fitness tests now encode one named exception |
| [034](034-workspaces-and-tenancy.md) | *(Proposed; built, not proven)* Personal and team workspaces with Owner/Admin/Member roles; shareable data (knowledge, graph, CAG) scoped to `workspace:<id>` via `X-Aura-Workspace`, checked against the store per request; personal-life data never shared | One membership lookup per team-scoped request and a second role system kept strictly apart from platform roles, in exchange for immediate revocation and a scope that needs no data rewrite |
| [035](035-adaptive-knowledge-updates.md) | *(Proposed; designed, not built)* "Loop C": knowledge gaps, explicit requests, corrections, new versions and staleness are researched from approved sources and self-hosted SearxNG into cited proposals; every change to the global corpus waits for a platform admin's digest-bound approval and is applied through versioned ingest with revert | Throughput bounded by admin review and a residual cross-tenant leakage risk from the always-global target, in exchange for demand-driven knowledge with an approver, evidence and revert on every change |
| [036](036-open-analysis-tools.md) | *(Proposed; built, not proven)* The 29 `tools/open` classes are exposed in three risk tiers: pure analysis tools on by default, code-running tools off by default behind `engineer_agent` and a per-call confirmation, and docker/profiling tools that run only on a paired desktop; six duplicates of existing engines stay unregistered | A larger planner prompt and, when the code-running group is enabled, project code executing in a child of the gateway with no container (GAP-066), in exchange for the agent being able to analyse, lint and test code it did not write |
| [040](040-lint-search-media-and-document-tools.md) | *(Proposed; built, not proven)* The Engineer gets structured `lint_code`/`check_format` (a missing linter is never "clean"), `web_search` through an operator-run SearXNG only, and structured file readers (`parse_document`, `inspect_spreadsheet`, `ocr_image`, `analyze_image_requirements`, `transcribe_audio`) under the `read_file` root, each registered only when its engine or setting exists; `/voice/transcribe` gains `detail=segments`; speakers are never labelled | Five more tool descriptions in the planner prompt when a files root is set, CPU seconds on the gateway for OCR and parsing (bounded), and an honest PARTIAL on diarization, in exchange for agents that can read structure, schemas, screenshots and recordings instead of flattened text |
| [039](039-declarative-tool-policy-autonomy-snapshots-and-kill-switch.md) | *(Proposed; built, not proven)* Operator-written tool-call rules over a tool's arguments (can only deny or require confirmation), a per-user `strict`/`standard`/`autonomous` review profile, workspace snapshots and rollback in plain git, a `stop-all` kill switch, opt-in redaction of credentials in context/input/history, and an egress allowlist rendered from `AURA_SANDBOX_ALLOWED_EGRESS_HOSTS` | Every setting is off by default; the profile is held in the cache port (a Redis eviction forgets a `strict` choice), the stop switch is per-process, and the compose and desktop halves are not yet run on a real host |
| [041](041-agent-task-gateway-queue-and-usage-api.md) | *(Proposed; built, not proven)* User agent tasks (`POST /agents/tasks`) run a `/chat` turn server-side through an in-process priority/fair/capped queue, idempotent by `client_request_id`; voice-to-task that asks instead of guessing; owner-scoped workspace start/stop/stats; run search and timeline (goal text only behind an opt-in redacted preview); per-turn usage and cost API. All state in the cache port, no table | Tasks do not survive a server restart, limits are per process, and records share the cache, in exchange for a server-side task with a pollable result and a replay-safe submission contract without a migration |
| [042](042-open-pure-tools.md) | *(Proposed; built, not proven)* Fourteen more `tools/open` classes become pure agent tools (`tools/open_pure.py`): the Engineer and, where it fits, the coding, planning and research personas can check architecture, detect ambiguity, verify acceptance criteria, guard a diff's scope against its task, explain and quiz on source, validate workflows and more, with the gaps the review found closed in `open_pure_logic.py` | Fourteen more tools in the planner prompt, and heuristic checks (task relevance, ambiguity, non-Python structure) whose results a person still has to judge |
| [043](043-open-workspace-tools.md) | *(Proposed; built, not proven)* Nine Engineer tools that read the user's workspace under the `read_file` root (`index_repository`/`search_repository`, `check_file_staleness`, `load_project_conventions`, `load_project_skills`, `read_notebook`, `inspect_binary_file`, `verify_backup`, plus `project_status` over the recorded runs) and optional `paths`/`repo_dir`/`advisories_path` arguments on five analysis tools (lockfile and virtualenv consistency, licences from installed metadata, importers and real tests, import and signature resolution, file and advisory scans); `write_file`/`edit_file` warn, never block, when a file changed since the agent last saw it | Nine more tool descriptions when a files root is set and bounded CPU on the gateway for walks and hashing, in exchange for agents that can check their own context and dependencies against real files; offline by design, so no outdated-package or CVE check without an advisory file, no embeddings, and a `.sql` backup is checked, never loaded |
| [044](044-open-partial-tools-completed.md) | *(Proposed; built, not proven)* Thirteen PARTIAL open tools gain the missing capability additively: deep line-numbered review, behaviour tests from docstrings and type hints, ER/flow/sequence/architecture diagrams derived from SQL and code, changed-line coverage and operator mutants, memory figures and baseline regression for `profile_python`, evidence-carrying quality gates, SQL schema and OpenAPI validation/breaking-change analysis, release checklists plus `verify_release_health`, Prometheus/structured-log incident hypotheses, data-quality checks, a per-owner model-metric history in the cache port, and `research_topic` composed through the registry | The analysis tools' planner-prompt budget rises 7,000 to 8,500 characters, three new tool names, and honest limits remain (no EXPLAIN, no triggered evaluation, no live telemetry, lexical contradictions only), in exchange for tools that find what the sheet said they could not |

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
