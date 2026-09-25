# Production SLOs (proposed)

> **These are proposed targets, not claims.** None of them is met or even continuously measured
> today. Each row says how the SLO would be measured, which existing metric (if any) supports that,
> what AURA has actually measured so far and under what conditions, and the resulting status.
> "Met" may only be written with a linked report from the production topology, per the roadmap's
> standing rule.

Part of [MASTER_PRODUCTION_ROADMAP.md](MASTER_PRODUCTION_ROADMAP.md) (Phases 3, 15, 25). Metric
names come from `aura_backend/aura_core/observability/metrics.py`, whose namespace is `aura_`
(counters are exported with a `_total` suffix). Alert rules live in
`deploy/monitoring/alerts/aura.yml`. Verified against tree `11abb50` on 2026-09-24.

**Status vocabulary:** `no-metric` means the SLO can't be measured yet (a gap). `measurable` means
a metric exists but there's no production baseline. `measured-below-target` means a real
measurement exists and misses the target. `met` requires evidence, and no row has it.

## 1. Service SLOs

| # | SLO (proposed) | How measured | Existing metric / alert | Current measured value (conditions) | Status |
| --- | --- | --- | --- | --- | --- |
| S1 | **Availability ≥ 99.5%** monthly (successful `/health/ready` plus non-5xx on user routes) | External blackbox probe of `https://<domain>/health/ready` every 30 s, combined with an HTTP 5xx ratio | `up{job="aura-gateway"}` via the `AuraGatewayDown` alert, but that's an internal scrape, not a user-path probe. There's no HTTP status metric (GAP-036) and no external prober | None. A1 verified `/health` and `/health/ready` once at deploy (roadmap A1) | `no-metric` |
| S2 | **Non-LLM API p95 < 500 ms, p99 < 1 s** (auth, sync, knowledge list, admin reads) | Per-route request-duration histogram, route template label | **None.** No HTTP request histogram exists (GAP-036). `aura_stage_duration_seconds` covers kernel stages only | Pipeline overhead only (echo provider, in-memory): p95 4.56 ms, **not representative** (roadmap §1). A4 pipeline overhead with the real data tier: p50 244 ms, p95 19.0 s on a CPU-contended 4-core sandbox ([A4](../reports/phase-a4-load-test.md)) | `no-metric` |
| S3 | **TTFT p95 < 2.5 s** for chat | `histogram_quantile(0.95, sum(rate(aura_llm_ttft_seconds_bucket[5m])) by (le))` | `aura_llm_ttft_seconds` (buckets up to 12 s). The `AuraSlowTimeToFirstToken` alert fires at p95 > 8 s | Not reported by A4 as a TTFT figure. The full-turn p50 of 56.3 s on CPU implies TTFT far above target (inferred, not measured) | `measurable` (no production baseline) |
| S4 | **Ordinary answer p95 < 15 s** (a turn with no tool steps) | `histogram_quantile(0.95, sum(rate(aura_turn_duration_seconds_bucket[5m])) by (le))`, filtered to non-planned turns once a label exists | `aura_turn_duration_seconds`. Its **buckets stop at 60 s** (GAP-037), so today's p95 can't be resolved | **p50 56.3 s / p95 70.5 s**, n=20 sequential, `qwen2.5:3b-instruct-q4_K_M`, CPU, `OLLAMA_NUM_PARALLEL=1`, 4-core/15 GB sandbox ([A4](../reports/phase-a4-load-test.md)) | `measured-below-target` |
| S5 | **5xx rate < 1%** of requests (5-min windows) | HTTP status counter, `5xx / all` | **None** (GAP-036). The nearest proxy is `AuraHighErrorRate`: `aura_turns_total{outcome="error"}` ratio > 5% over 10 min, which counts turns only and uses a 5% threshold | None | `no-metric` |
| S6 | **Throughput:** sustain 50 concurrent users within S3/S4 | Load matrix 1/5/10/25/50/100 users (Phase 3) | `aura_turns_in_flight`, `aura_llm_inflight`, `aura_llm_queue_depth`, `AuraProviderSaturated` (inflight ≥ 2) | **Flat 0.04–0.06 turns/s from 1 to 8 concurrent** (A4). No run above 8 | `measured-below-target` |

## 2. Safety and integrity SLOs (zero-tolerance)

| # | SLO (proposed) | How measured | Existing metric / test | Current evidence | Status |
| --- | --- | --- | --- | --- | --- |
| Z1 | **0 unauthorized tool executions.** No side-effecting tool runs without its grant, capability and confirmation | Audit log review, plus a counter of executions whose policy decision was not `allow`, which must stay 0 by construction | `aura_tool_denied_total` counts refusals. `aura_tool_calls_total` counts executions. There's no metric of "executed despite deny", because the registry returns before dispatch. Pinned by tool-policy unit tests. Residual: concurrent-batch taint window (`aura_core/tools/registry.py:378-389`, GAP-014) | No production incident recorded. Tests only | `measurable` (tests, no production audit review cadence) |
| Z2 | **0 cross-user data leakage** | Owner-scoping tests on every store, plus a periodic cross-tenant probe with two test users in production | No runtime metric. Owner-scope tests exist (e.g. `tests/unit/test_postgres_graph.py`, `test_personal_os_records.py` `*_is_owner_scoped`), but most use fakes or in-memory stores | No real-Postgres cross-tenant suite. No production probe | `no-metric` |
| Z3 | **0 lost durable events** (outbox) | Outbox rows older than lease × N that are still unprocessed must equal 0. Celery task failures | `aura_event_handler_failures_total` exists. There's no outbox-age gauge. The outbox design is at-least-once (`FOR UPDATE SKIP LOCKED` + 300 s lease, per the audit §24) | Never measured under a crash/restart test on real Postgres | `no-metric` |
| Z4 | **0 prompt-injection-driven side effects** | Tainted turns that executed a side-effecting tool without confirmation must equal 0 | `aura_turns_tainted_total`, `aura_guard_verdicts_total`, plus alerts `AuraInjectionQuarantineSpike` and `AuraGuardBlockSpike` | Tests only (`tests/security/test_prompt_injection.py`) | `measurable` |

## 3. AI quality SLOs

| # | SLO (proposed) | How measured | Existing metric | Current measured value (conditions) | Status |
| --- | --- | --- | --- | --- | --- |
| Q1 | **RAG Recall@10 ≥ 0.80** | `RetrievalEvaluator` on a production-representative labelled corpus with the production embedder | `aura_eval_score{metric="recall_at_k"}`. `AuraEvalRegression` fires below 0.6 | **recall@5 = 0.65**, nDCG 0.51, MRR 0.37: 10-doc `docs_corpus`, 23 queries, **hash embedder (non-semantic)**, default hybrid strategy ([A3](../reports/phase-a3-retrieval-corpus.md)). Recall@10 NOT MEASURED. The production-embedder figure is NOT MEASURED | `measured-below-target` (on a non-production embedder) |
| Q2 | **Groundedness ≥ 0.90** | `AnswerEvaluator` with the **production LLM** on the labelled corpus. Live `aura_groundedness_score` histogram | `aura_groundedness_score`. `AuraGroundednessDegraded` fires if the p50 is below 0.35 | **0.94** on 23 cases with `ContextGroundedProvider`, a **scripted provider, not a real model** (roadmap §5). With a real model: NOT MEASURED. A4 saw 9 of 15 real-model turns flagged `low_groundedness` (unlabelled, GAP-022) | `no-metric` for real-model quality |
| Q3 | **Citation precision ≥ 0.90** | `AnswerEvaluator` citation flags, production LLM | Offline eval only. No live metric | Citation *coverage* 0.83 with the scripted provider (roadmap §5). Precision NOT MEASURED | `no-metric` |
| Q4 | **Guard false-positive rate** (target to be set in Phase 14) | Labelled leak/no-leak and injection suites | `aura_guard_verdicts_total` | `SystemPromptLeakGuard` blocked 3 of 15 real-model turns in A4, unclassified | `no-metric` |

## 4. Error budget and alerting (proposed)

- **Availability 99.5%** allows about 3 h 36 min of downtime per 30 days. When more than 50% of
  the budget is burned inside 7 days, feature deploys freeze until the cause is fixed.
- **Burn-rate alerts** get added once S1/S5 metrics exist (GAP-036): fast burn at 14.4× over 1 h,
  slow burn at 6× over 6 h (the standard multi-window pattern).
- Zero-tolerance SLOs (Z1–Z4) page on the first occurrence.
- Quality SLOs (Q1–Q3) gate releases through `RegressionGate` (see
  [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)). They don't page.

## 5. Metrics that must be added before these SLOs mean anything

| Needed for | Metric (proposed name) | Gap |
| --- | --- | --- |
| S1, S2, S5 | `aura_http_requests_total{route,method,status}`, `aura_http_request_duration_seconds{route,method}` | GAP-036 |
| S1 | External blackbox probe (e.g. `prom/blackbox-exporter`, digest-pinned) | GAP-036 |
| S4, S6 | Extend `_TURN_BUCKETS` beyond 60 s until latency is under target | GAP-037 |
| Z3 | `aura_outbox_oldest_pending_seconds` gauge | new, Phase 15 |
| Q1–Q3 | A scheduled real-model eval publishing `aura_eval_score` | GAP-006, GAP-021 |
