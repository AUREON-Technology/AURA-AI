# AURA AI — AI Capability Report (Phase 1)

Assessment of every AI capability requested for the enterprise platform against what exists in the
repository today. Ratings: **None** / **Partial** / **Adequate**.

| # | Capability | Today | Evidence | Gap |
| --- | --- | --- | --- | --- |
| 1 | Large Language Model | Partial | `services/ai.py:91`, `services/agent.py:250` — Gemini 2.5 Flash hard-coded; Ollama used only as a crash fallback | No provider abstraction, no model selection, no cost/latency policy, no streaming contract |
| 2 | Deep Learning | None | — | No training or inference code beyond calling hosted APIs |
| 3 | Machine Learning | Partial | `services/agent_router.py`, `services/sentiment.py` — nearest-centroid classifiers over embeddings | No training loop, no evaluation gates, no versioned artefacts (centroids are JSON blobs in the image) |
| 4 | Neural Networks | None | — | No model definitions of any kind |
| 5 | RAG | Partial | `services/rag.py` — fixed 1000-char chunking, dense-only top-k, no reranking | No hybrid search, no reranker, no citation contract, no retrieval evaluation, no ingestion versioning |
| 6 | Knowledge Graph | None | — | No entity/relation extraction, no graph store |
| 7 | Long-term memory | Partial | `services/memory.py` — LLM fact extraction at confidence ≥ 0.75, stored in the shared Qdrant collection | Only one memory kind. No decay, consolidation, contradiction handling, or provenance |
| 8 | Short-term memory | None | Conversation history is whatever the client re-sends on every request | No server-side session state, no working memory, no summarisation |
| 9 | Semantic search | Partial | `rag_service.query_knowledge` | Dense-only, no filters beyond `user_id`, no score threshold, no MMR/diversity |
| 10 | Autonomous learning pipeline | Partial | `services/learning_pipeline.py` — appends feedback to a JSON file, formats an Alpaca/ChatML dataset | Not scheduled, not validated, not deduplicated, not versioned, not reversible; no knowledge refresh |
| 11 | Agentic AI | Partial | `services/agent.py` — 7 personas selected by an if/elif keyword ladder (`agent.py:71-95`) | No goals, no plans, no multi-step execution, no reflection, no termination criteria |
| 12 | Tool calling | Partial | 3 closures defined inline inside `execute_agent_stream` (`agent.py:108-145`), dispatched by an if/elif chain (`agent.py:281`) | Not a registry, not extensible without editing the orchestrator, no schema validation, no timeouts, no authorisation, no result contract |
| 13 | Local AI inference | Partial | `services/ollama.py` + unauthenticated proxy routes | No GPU detection, no model registry, no health-gated routing, no hot swap, no quantisation policy |
| 14 | Cloud AI integration | Partial | Gemini only | No OpenAI-compatible, Anthropic, or vLLM path |
| 15 | Continuous knowledge updating | None | — | No scheduled ingestion, no source registry, no validation, no index reload |
| 16 | Enterprise monitoring | Partial | `infrastructure/observability.py` — structured logs, optional Sentry + OTLP | Zero AI-specific metrics: no token accounting, no latency histograms per provider, no retrieval quality, no cache hit rate, no drift |
| 17 | MLOps | None | — | No experiment tracking, no model registry, no A/B testing, no rollback |
| 18 | Prompt management | None | Prompts are string literals (`services/agent.py:15-40`, `:25-40`) concatenated at runtime | No templates, versioning, evaluation, or regression tests |
| 19 | Safety / guardrails | None | — | No prompt-injection defence, no jailbreak detection, no PII handling, no output validation |
| 20 | Evaluation | Partial | `agent_router.evaluate_router()` — 19 hand-written examples, accuracy only | No golden dataset, no RAG metrics, no regression gate in CI |

## Detailed observations

### Prompt handling is string concatenation

`services/agent.py:158-221` builds the system prompt by appending, in order: a persona literal,
`ACTION_INSTRUCTIONS`, a sentiment directive, remembered facts, and retrieved chunks. There is no
token budget, no truncation strategy, and no separation between trusted instructions and untrusted
retrieved text — retrieved document content is injected directly into the instruction channel. This
is the classic indirect prompt-injection setup: a poisoned document in the knowledge base can issue
instructions to the model. See the Security Report.

### Tool calling cannot be extended

Tools are Python closures created **per request** inside the streaming generator so they can capture
`google_access_token` via closure (`agent.py:108`). They are registered only when a Google token is
present (`agent.py:246`), dispatched by name through an if/elif chain (`agent.py:281-288`), and their
results are passed back with no schema validation, no timeout, no size limit, and no error taxonomy.
Adding a tool means editing the orchestrator's control flow.

### The "budget" is a per-request call counter

`services/budget.py` caps Gemini calls at **2 per request** via a `ContextVar`. Any agent task
requiring three tool round-trips is structurally impossible. There is no token budget, no cost
accounting, and no per-user or per-tenant quota.

### Retrieval has no quality controls

`_chunk_text` (`rag.py:276`) splits on raw character offsets with no respect for sentence or
paragraph boundaries. Retrieval returns the top-k by cosine similarity with **no score floor**, so
when the knowledge base contains nothing relevant the model still receives three arbitrary chunks
presented as "Relevant Context". Combined with the zero-vector fallback at `rag.py:154-156` — where
a failed embedding silently becomes `[0.0] * 768` — a degraded embedding backend produces
*confidently wrong* retrieval rather than an error.

### Fallback chains hide failure

Every AI path degrades silently: Gemini → Ollama → hard-coded mock strings (`agent.py:349-363`).
In `debug_mode` the user receives a fabricated answer ("I am your Finance Advisor… proposed logging
an expense") that is indistinguishable from a real one. There is no signal in the response
indicating which engine produced it, and no metric counting fallbacks.

## Capability score

| Domain | Score |
| --- | --- |
| Model access | 3 / 10 |
| Retrieval | 3 / 10 |
| Memory | 2 / 10 |
| Agency (planning, tools, execution) | 2 / 10 |
| Safety | 0 / 10 |
| Observability of AI behaviour | 1 / 10 |
| MLOps | 0 / 10 |
| **Overall** | **1.6 / 10** |
