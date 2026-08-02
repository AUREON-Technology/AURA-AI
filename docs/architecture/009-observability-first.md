# ADR-009 — AI metrics are a first-class output of the kernel

**Status:** Accepted · **Date:** 2026-08-02

## Context

The existing system has structured logs, optional Sentry, and optional OTLP tracing — all generic
web-service observability. It has **zero** AI-specific telemetry: no token counts, no cost, no
per-provider latency, no cache hit rate, no retrieval quality, no fallback counter, no drift signal.
The one place telemetry is collected (`POST /api/telemetry/log`, `main.py:410`) appends JSON lines to
a container-local file from inside a request handler.

Consequently nobody can answer: *is it getting worse?* *what is it costing?* *how often does
retrieval return nothing useful?* *which provider is slow right now?*

## Decision

**1. The kernel emits a `TurnTelemetry` record for every turn**, as a domain object, not a log line.
It is the return value's sibling, so it cannot be forgotten:

```
TurnTelemetry(
  correlation_id, user_ref, route, agent, strategy,
  provider, model, fallback_count, cache: {hit, tier},
  tokens: {prompt, completion, total}, cost_usd,
  latency: {total, ttft, guards, retrieval, memory, planning, generation},
  retrieval: {candidates, returned, top_score, below_threshold, citations},
  guards: [(name, action, confidence)],
  tools: [(name, ok, ms, error_kind)],
  confidence, groundedness, error_kind
)
```

It is exported three ways: Prometheus metrics (aggregate), structured log (sampled, no content), and
Postgres (full record, retention-bounded) for offline analysis and drift computation.

**2. Metric catalogue** (`observability/metrics.py`) covers every item Phase 10 requires:

| Requirement | Metric |
| --- | --- |
| Latency | `aura_turn_duration_seconds`, `aura_llm_ttft_seconds`, `aura_stage_duration_seconds{stage}` |
| Token usage | `aura_llm_tokens_total{direction,provider,model}` |
| Cost | `aura_llm_cost_usd_total{provider,model}` |
| CPU / memory / GPU | `aura_process_*` + node-exporter; GPU via `aura_gpu_*` when a probe is available |
| Hallucination rate | `aura_ungrounded_claims_total`, `aura_groundedness_score` |
| Accuracy | `aura_eval_score{suite,metric}` from scheduled eval runs |
| Tool failures | `aura_tool_calls_total{tool,outcome}`, `aura_tool_duration_seconds` |
| RAG retrieval quality | `aura_retrieval_top_score`, `aura_retrieval_empty_total`, `aura_eval_recall_at_k` |
| Embedding quality | `aura_embedding_failures_total`, `aura_embedding_duration_seconds` |
| Cache hit rate | `aura_cache_lookups_total{tier,result}` |
| User satisfaction | `aura_feedback_total{rating}` |
| Model drift | `aura_drift_score{signal}` — PSI over embedding centroid, refusal rate, length, and eval score |
| Knowledge freshness | `aura_knowledge_age_seconds`, `aura_knowledge_generation`, `aura_refresh_last_success_timestamp` |

**3. Every degradation is counted.** Provider fallback, cache miss, guard quarantine, retrieval below
threshold, tool timeout, memory-store unavailability — each increments a counter. A system that
degrades silently cannot be operated. This is the direct fix for TD-09's 47 log-and-continue blocks.

**4. No content in telemetry.** Prompts, documents, memories, and outputs never enter metrics or
logs. The Postgres record stores hashes and lengths, not text. Sampling for content-bearing traces is
opt-in per deployment and off by default.

**5. Correlation.** One `correlation_id` per turn flows through logs, metrics exemplars, traces,
audit events, and the telemetry record.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Logs only, aggregate later | Cardinality and cost of log-derived metrics at query time; no alerting without a log pipeline; no histograms. |
| Vendor LLM-observability SaaS | Sends prompts and outputs to a third party — unacceptable for a system holding health, finance, and personal data. The OTLP export seam remains for teams that choose it. |
| Instrument only the HTTP layer | Measures the wrong thing: the interesting variance is inside the pipeline (retrieval, guards, provider selection), not in FastAPI. |
| Emit metrics from each engine independently | Engines would need a metrics dependency, breaking the stdlib-only rule for the core. Instead engines return `StageResult`s with timings and the kernel emits. |

## Consequences

**Positive** — quality, cost, and drift become measurable and alertable; dashboards are derived from
one catalogue rather than invented per panel; regressions are caught by CI eval gates using the same
metrics.

**Negative** — ~1–3 ms per turn of instrumentation, one more table with a retention policy, and
metric cardinality must be watched (provider × model × route is bounded; user id is deliberately
**not** a label).

**Impact:** operability is the point; cost control becomes possible (token/cost metrics are the input
to every optimisation in ADR-003); security benefits from audit-adjacent counters; no scalability
cost since Prometheus scrapes per-replica and aggregates centrally.
