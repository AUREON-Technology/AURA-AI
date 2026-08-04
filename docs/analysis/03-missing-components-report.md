# AURA AI — Missing Components Report (Phase 1)

Every component required by the enterprise-platform specification that does not exist in the
repository, with the priority assigned to it in the rebuild plan.

## P0 — blocks the platform from existing

| Component | Why it is required | Delivered as |
| --- | --- | --- |
| Domain model | Nothing in the system names its own concepts. `dict` is used for messages, memories, tool results, and plans. Without entities there is no invariant to test. | `aura_core/domain/` |
| Ports / interfaces | No seam between policy and I/O; every service imports a concrete client. | `aura_core/ports/` |
| Dependency injection container | Singletons constructed at import time make the system untestable and unconfigurable. | `aura_core/container.py` |
| LLM provider abstraction | `google.genai` is called directly from 4 modules; model ids are string literals. | `aura_core/llm/` |
| Provider router (fallback, cost, latency, capability) | Failover today is a bare `except` that silently downgrades to a mock. | `aura_core/llm/router.py` |
| Prompt engine (templates, versions, variables) | Prompts are concatenated literals; a prompt change is a code deploy. | `aura_core/prompts/` |
| Context engine (budget, ranking, compression) | Nothing counts tokens. Context is whatever fits by accident. | `aura_core/context/` |
| Tool registry + schema + sandbox | Tools are closures dispatched by if/elif with no validation or timeout. | `aura_core/tools/` |
| Safety pipeline | Zero input or output guards. | `aura_core/safety/` |
| AI metrics | No token, latency, cost, retrieval-quality, or fallback metrics. | `aura_core/observability/metrics.py` |

## P1 — required for the platform to be enterprise-grade

| Component | Gap today | Delivered as |
| --- | --- | --- |
| Memory taxonomy (working, episodic, semantic, procedural, reflection, entity) | One kind: "user fact" in a shared Qdrant collection. | `aura_core/memory/` |
| Memory consolidation + decay + contradiction handling | Facts accumulate forever, duplicates included. | `aura_core/memory/consolidation.py` |
| Session / short-term memory store | No server-side conversation state at all. | `aura_core/memory/working.py` |
| Hybrid retrieval (BM25 + dense + RRF) | Dense-only top-k. | `aura_core/knowledge/hybrid.py` |
| Reranking + score thresholds + citations | Retrieval always returns k chunks regardless of relevance. | `aura_core/knowledge/rerank.py` |
| Structure-aware chunking | Fixed 1000-char slicing mid-sentence. | `aura_core/knowledge/chunking.py` |
| Document store with versioning | Chunks are upserted with deterministic ids; old versions are overwritten, never retained. | `aura_core/knowledge/documents.py` |
| Knowledge graph + entity/relation extraction | Does not exist. | `aura_core/knowledge/graph.py`, `extraction.py` |
| Planning engine (decomposition, dependencies, replanning) | Does not exist. | `aura_core/planning/` |
| Reasoning engine (CoT, ToT, reflection, verification, confidence) | Does not exist. | `aura_core/reasoning/` |
| Execution engine (step execution, retries, budgets, monitoring) | The tool loop inside the stream generator. | `aura_core/execution/` |
| Response cache (exact + semantic) | Only an unbounded in-process embedding `dict` (`rag.py:16`). | `aura_core/llm/cache.py` |
| Evaluation harness + golden datasets | 19 hand-written router examples. | `aura_core/evaluation/` |
| Model registry + health + hot swap | None. | `aura_core/mlops/model_registry.py` |
| Continuous knowledge update pipeline (02:00) | None. | `aura_core/learning/knowledge_refresh.py` |
| Index versioning + rollback | Collection is deleted and recreated on dimension mismatch. | `aura_core/knowledge/index_manager.py` |
| Dataset curation + fine-tune recommendation | `learning_logs.json` rewritten in full on each append. | `aura_core/learning/curation.py`, `finetune.py` |
| Experiment tracking | None. | `aura_core/mlops/experiments.py` |
| Audit log | None. Security-relevant actions leave only application logs. | `aura_core/security/audit.py` |
| Rate limiting per user/tenant/route | Per-IP only, one global limit (`main.py:151`). | `aura_core/security/rate_limit.py` |
| Secrets management | `.env` files mounted into containers. | `docs/guides/administrator-guide.md` + compose secrets |

## P2 — scale and operations

| Component | Delivered as |
| --- | --- |
| Reverse proxy, TLS termination, security headers | `deploy/nginx/` |
| Prometheus + Grafana + Alertmanager + Loki | `deploy/monitoring/` |
| Backup and restore automation | `deploy/scripts/backup.sh`, `restore.sh` |
| Health/readiness/liveness separation | `aura_core/api/health.py` |
| Load and stress test suite | `tests/load/` |
| Reusable neural-network module specs | `aura_core/ml/nn/` |
| Distributed-training readiness | `docs/guides/finetuning-guide.md` |
| OpenAPI-generated client contract | FastAPI schema exposed at `/api/v3/openapi.json` |

## Explicitly out of scope (and why)

| Requested | Decision | Rationale |
| --- | --- | --- |
| Training a frontier LLM from scratch | Not attempted | The prompt itself excludes it. Cost is 10^7–10^8 USD of compute; the correct move is deploying and adapting open-weight models. |
| Running every neural architecture simultaneously | Not attempted | `aura_core/ml/nn/` provides reusable, importable specs (Transformer, CNN, RNN/LSTM/GRU, autoencoder, GNN, time-series, anomaly detection) behind an optional-`torch` guard. Loading all of them into a VPS process would exhaust memory for no benefit. |
| Automatic base-model retraining | Deliberately blocked | The continuous pipeline updates retrieval indexes and *prepares* datasets; promoting a fine-tune requires human approval (`aura_core/learning/approval.py`). |
