# ADR-052: Monitoring production inference, and metric history that cannot lose writes

Status: Proposed (built and tested; the job has not run against a live deployment).

## Problem

`check_model_drift` / `record_model_metric` / `compare_model_metric` only knew what a caller told them, nothing
watched the models actually serving users, and the history was one cache blob under get-modify-set, so two writers
erased each other's observations.

## Decisions

1. **History without lost writes.** `Cache` gained `append_capped` (Redis `RPUSH`+`LTRIM`+`EXPIRE` in one
   transaction) and `get_list`; the sequence number comes from the atomic counter; the baseline pin is its own key;
   the old blob is still read (`DriftStore`, `tools/drift.py`). The mutating methods raise on a backend failure.
   Verified with parallel writers on the in-memory cache and on a real Redis, and the old algorithm's loss is
   reproduced in a test.
2. **Scheduled monitoring** (`learning/model_monitor.py`, `tasks.model_monitor_task`, hourly, opt-in
   `AURA_MODEL_MONITOR_ENABLED`). It reads per-model aggregates of the turn telemetry the gateway already records
   (`recent_summary_by_model`, Postgres `GROUP BY model` and the in-memory twin), appends one observation per
   (model, metric) to the platform scope, compares it with the series' pinned baseline and stores the report.
   Series: success rate, non-degraded rate, tool success rate, groundedness, p95 latency, average cost.
3. **Policy, stated:** the first observation pins itself (`baseline_pinned: auto`); drift is measured against the
   pin until an operator re-baselines on purpose (`POST /admin/model-monitor/rebaseline`, audited); rates falling by
   more than 0.05 or latency/cost rising more than 50% are drift; a model with fewer than `min_turns` (20) turns in
   the window is skipped, not judged on noise. `GET /admin/model-monitor` returns the last report.
4. **It measures; it does not act.** No evaluation or retraining is started. A drift finding is logged and kept in the
   report (`action_required: evaluate_or_retrain`). Retraining stays human-gated (ADR-008; `finetune_auto_launch`
   can never be true).

## Not built

* Alert delivery (webhook, email, Prometheus gauge): drift is in the log and the admin report. Who is alerted, and how,
  is an operator decision.
* Launching an evaluation run (the harness exists: `evaluation/runner.py`, `eval-ci.yml`); a cost-capped,
  admin-confirmed trigger needs a budget decision.
* A refusal-rate series: telemetry has no refusal field.
* Platform-wide series only (no per-workspace scope).

## Evidence

`tests/unit/test_model_monitor.py` (first pass, stability, drift on each metric kind, relative latency/cost
thresholds, per-model isolation, re-baseline, the 50-series cap, concurrent monitors, the admin routes over a real
container), `tests/unit/test_drift_concurrency.py`.
