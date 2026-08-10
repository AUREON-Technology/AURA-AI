# AURA AI — Current Architecture (Pre-Modification Audit)

Forensic record of the system **as found**, before the self-hosting mission changes anything.
Written so the diff that follows can be judged against a fixed baseline.

Audit date: 2026-08-10 · Commit: `e767c4a` · Branch: `claude/aura-enterprise-platform-al50m6`

---

## 1. Code inventory

| Tree | Files | Lines | Role |
| --- | --- | --- | --- |
| `aura_backend/aura_core/` | 90 | 23,342 | v3 AI kernel (hexagonal, stdlib-only domain) |
| `aura_backend/services/`, `api/` | 23 | 2,998 | legacy v1/v2 application |
| `aura_backend/tests/` | 33 | 10,125 | pytest suite, 1010 tests, 90% coverage |
| `aura_client/lib/` | 65 | 24,041 | Flutter client |

Two backend trees coexist by design — a strangler-fig migration (ADR-002). `aura_core` never
imports from `services/`, enforced by AST fitness tests.

---

## 2. Where computation happens today

```
┌─ FLUTTER CLIENT ────────────────────────────────────────────┐
│  aura_ai_router.dart      decides gemini vs. on-device      │  ← violation
│  online_agent_manager     holds a Gemini API key            │  ← violation
│  native_llama_provider    llama.cpp inference on device     │  ← violation
│  model_download_manager   pulls GGUF weights from HF        │  ← violation
│  aura_memory / knowledge_center / feedback_collector        │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS  (and, on failure, direct to Google)
┌──────────────────────┴──────────────────────────────────────┐
│  AURA BACKEND                                               │
│    main:app          v1/v2 routes                           │
│      services/ai.py      → google.genai  (external)         │  ← violation
│      services/ollama.py  → local Ollama  (self-hosted)      │
│      services/agent.py   → Ollama, then mock fallback       │
│    /api/v3 router    aura_core kernel                       │
│      llm/providers/  ollama, openai_compatible (local)      │
│                      gemini, anthropic       (external)     │  ← violation
│      llm/embeddings  hash | sentence-transformers (local)   │  ok
└──────────────────────┬──────────────────────────────────────┘
                       │
        Postgres · Redis · Qdrant · Firebase/Firestore
```

---

## 3. External AI dependency map

Searched the whole repository for every hosted-inference provider named in the brief. Hits that
are **live code paths**, not documentation:

### 3.1 Client — 4 findings

| # | Location | Finding | Rules violated |
| --- | --- | --- | --- |
| C-01 | `online_agents/online_agent_manager.dart:98-135` | Direct `google_generative_ai` SDK call to `gemini-1.5-flash` when the backend is unreachable. The API key lives on the device in `localApiKeyProvider`. | 4, 5, 6, 8, 17, 30 |
| C-02 | `offline_agents/native_llama_provider.dart` | Real llama.cpp inference on device via `flutter_llama`. Loads GGUF weights from app documents, ChatML/Llama-3 prompt templating, token streaming. | 9, 14, 16, 30 |
| C-03 | `offline_agents/model_download_manager.dart:51-54` | Downloads GGUF weights directly from `huggingface.co` to the device — Gemma-2-2B, Phi-3-mini, Qwen2-1.5B, Llama-3-8B. | 15, 30, 31 |
| C-04 | `ai_router/aura_ai_router.dart` | Client decides the provider and model (`gemini-2.5-flash` vs. on-device), including failover. Routing is a server responsibility. | 12, 30 |

C-01 is the most serious. It is precisely the "hidden external API fallback" Rule 17 forbids, and
it means a server-side ban on external inference is bypassable from the client.

C-03 is a supply-chain exposure independent of the AI rules: unpinned, unverified executable
artefacts fetched over the network onto user devices, with no digest check.

Client dependencies backing these: `google_generative_ai: ^0.4.7`, `flutter_llama: ^0.1.0`.

### 3.2 Server — 7 findings

| # | Location | Finding | Rules violated |
| --- | --- | --- | --- |
| S-01 | `aura_core/llm/providers/gemini.py` (142 stmts) | Full Gemini provider — streaming, tool calls, cost accounting. Registered in `registry.py:78`. | 4, 5, 6 |
| S-02 | `aura_core/llm/providers/anthropic.py` (158 stmts) | Full Anthropic provider. Registered in `registry.py:95`. | 4, 5, 6 |
| S-03 | `services/ai.py` | Chat streaming via `gemini-2.5-flash`. Ollama existed only as a fallback, so the external call was the *preferred* path. | 4, 5, 6, 18 |
| S-04 | `services/agent.py` | Agent loop using Gemini **native function calling** across three Drive/RAG tools. The hardest to replace, and the only one needing new capability rather than rewiring. | 4, 5, 6, 18 |
| S-05 | `services/rag.py` | `gemini-embedding-001` at 768d as the primary embedder, with local encoders as fallback. | 4, 6, 18 |
| S-06 | `services/memory.py` | Fact extraction via `generate_content` with `response_schema=ExtractedFacts`. | 4, 6, 18 |
| S-07 | `services/automation.py` | Email and GitHub-issue analysis via `generate_content` in JSON mode, two call sites. | 4, 6, 18 |

`requirements.txt` carried `google-genai>=0.1.1` for S-03 through S-07.

> **Correction.** The first pass of this audit recorded only `services/ai.py` for the legacy tree.
> That was wrong: an AST scan of every import at any nesting depth found five files. The
> undercount came from grepping for the provider *names* rather than for the SDK import, and it is
> the reason the fitness test in §3.5 scans imports mechanically instead of relying on a read-through.

### 3.3 Already compliant

| Component | Implementation | Verdict |
| --- | --- | --- |
| Inference (v3 preferred) | Ollama + `openai_compatible` against a local base URL | self-hosted |
| Embeddings | `hash` (deterministic, stdlib) or `sentence-transformers` (local weights) | self-hosted |
| Tokenisation | `tiktoken` when present, heuristic fallback — no network either way | self-hosted |
| Reranking | heuristic (lexical overlap + position + MMR), no model call | self-hosted, but not a model |
| Safety guards | rule-based, in-process | self-hosted |
| Vector search | Qdrant (self-hosted container) | self-hosted |

No OpenAI, Azure, Bedrock, Groq, OpenRouter, Together, Replicate, or HF Inference API usage
exists anywhere. The external surface is Google and Anthropic only.

### 3.5 How this is now enforced

All seven server findings are closed, and the enforcement is mechanical rather than a one-off
deletion — `tests/architecture/test_no_external_inference.py` fails the build on any of:

1. **an SDK import**, found by walking the full AST so a lazy `import openai` inside a function is
   caught as readily as a module-level one;
2. **a hosted endpoint in source**, which catches an SDK-free raw HTTP call;
3. **a hosted package in either requirements file**, which catches the dependency returning before
   any code uses it;
4. **a hosted `base_url`** on the surviving `openai_compatible` kind — necessary because that kind
   speaks the same wire protocol as several hosted APIs, so removing the vendor adapters alone left
   a one-setting bypass.

Every list lives in `aura_core/config/external_inference.py` and is read by both the runtime check
and the scans. When the two kept separate copies, the weaker became the real policy.

**What this does not do:** it cannot stop an operator pointing `base_url` at a proxy they run that
forwards to a vendor. No in-process check can. That is what the compose file's private network and
the deployment's egress policy are for. These layers close the accidental path — a hurried
fallback, a copied snippet, a reinstalled SDK — not the determined one.

### 3.4 Non-AI external services (permitted under Rule 19)

Firebase Auth / Firestore, Google Drive (a user-authorised data source), Sentry, Let's Encrypt,
OTLP trace export. None performs inference. Firebase Auth is load-bearing: the shipped client
holds tokens it issued.

---

## 4. Subsystem status against the target

| Target capability | Present? | Notes |
| --- | --- | --- |
| AI gateway, authenticated | yes | `/api/v3/*`, auth → authz → rate limit → orchestrator |
| Server-side streaming | yes | typed SSE frames (`token`/`citations`/`revision`/`done`/`error`) |
| Context engine | yes | budgeted assembly, 90% covered |
| Memory engine | yes | six kinds, decay, provenance, consolidation |
| Model manager / registry | yes | stages, canary by stable hash, approval-gated promotion |
| Model routing | yes | five policies, circuit breaker, EWMA latency |
| Nightly improvement (02:00) | yes | gated index promotion, O(1) alias rollback |
| Evaluation harness | yes | recall/precision/MRR/nDCG, groundedness, regression gate |
| Observability | partial | catalogue complete; `prometheus-client` was undeclared (fixed in PR #8) |
| Safety / prompt injection | yes | trust channels, fencing, quarantine, tool taint |
| **Simple RAG** | yes | hybrid BM25 + dense, RRF k=60, score floor |
| **Memory RAG** | partial | memory recall exists but is not a selectable retrieval strategy |
| **Multi-Query RAG** | **no** | — |
| **HyDE RAG** | **no** | — |
| **Adaptive RAG router** | **no** | no query classifier selecting a strategy |
| **Corrective RAG** | **no** | no relevance evaluation → reformulate → re-retrieve loop |
| **Self-RAG** | **no** | confidence is computed, but no reflective verification pass |
| **Agentic RAG** | partial | planner/executor/tools exist; not wired as a retrieval strategy |
| **Multimodal RAG** | **no** | no OCR, no vision, no image or table extraction |
| **Graph RAG** | **no** | `InMemoryGraphStore` is a stub; no entities, edges, or multi-hop retrieval |
| Self-hosted reranker model | **no** | heuristic only |
| Storage adapters for prod | **no** | see §5 |

---

## 5. The deployment blocker

`deploy/docker-compose.prod.yml` sets `AURA_VECTOR_BACKEND=qdrant`,
`AURA_DOCUMENT_BACKEND=postgres`, `AURA_MEMORY_BACKEND=postgres`, `AURA_CACHE_BACKEND=redis`.
`container.py` resolves each to an adapter module. **None of the three modules exists**:

```
aura_core/adapters/  →  __init__.py  memory_stores.py  runtime.py
missing:                redis_cache.py  qdrant_store.py  postgres_store.py
```

Reproduced with the exact production environment:

```
settings OK
ModuleNotFoundError  No module named 'aura_core.adapters.redis_cache'
```

Settings validation passes; `Container.__init__` raises. And in-memory backends are refused in
production by `settings.py:626`, so there is no configuration that boots. Every other service in
the stack comes up healthy — the gateway alone crash-loops.

This makes the acceptance criterion "production deployment works" currently **false**, and it is
the single highest-priority item.

---

## 6. Live VPS state (`srv1794216`)

The running deployment is **not** either stack in this repository:

```
aura_backend  aura_redis  aura_celery_beat  aura_celery_worker      up 33 h
compose file: /opt/aura/docker-compose.vps.yml   (untracked, hand-assembled)
git repo:     /opt/aura/aura_backend/.git        (backend only)
```

Four containers, no Postgres, no Qdrant, no Ollama, no observability. It runs the **legacy**
`main:app` — so the box is serving `services/ai.py`, which is finding S-03. Hardware inspection
for `AI_HARDWARE_CAPACITY_REPORT.md` is pending shell output from the operator.

---

## 7. Findings summary

```
External AI APIs in code:        3 server + 1 client   (target: NONE)
Mobile AI inference:             yes, llama.cpp        (target: NONE)
Mobile model weights:            yes, GGUF from HF     (target: NONE)
Client-side provider routing:    yes                   (target: server-only)
Server-side self-hosted infer:   yes (Ollama)          ✓
Server-side embeddings:          yes                   ✓
Server-side memory:              yes                   ✓
Server-side reranking:           heuristic, no model   partial
Server-side agents:              yes                   ✓
RAG strategies implemented:      2 of 10               partial
Production deployment:           BROKEN                blocker
```

Seven violations, one deployment blocker, eight missing retrieval strategies, one missing
reranker model, no multimodal or graph layer.

---

## 8. What must not change

Recorded so the work can be checked against it:

- The Flutter UI, navigation, screens, and branding.
- Firebase Auth — the shipped client holds its tokens; `_build_authenticator` is the one place
  v3 touches `services/jwt_auth`.
- `api/v2/sync.py` — correct and efficient; the main reason the legacy tree still exists.
- The v1/v2 route surface, minus the four unauthenticated model-proxy routes already deleted
  under SEC-01.
- Google Drive as a user-authorised **data source** — it is not inference.

---

Next: `AI_HARDWARE_CAPACITY_REPORT.md`, then Phase 23/24 (remove external inference and on-device
inference), then the storage adapters, then the retrieval strategies.
