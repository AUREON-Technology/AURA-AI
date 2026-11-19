# SaaS P1-H: Billing Alerting and the Launch Checklist, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the code part of readiness item 5 for billing. A forged or broken payment notification, a store API outage, a billing cycle that stopped running, or email that stopped sending must raise an alert. Then write the one checklist that maps every §6.1 readiness item to its evidence and to who acts next.

**Architecture:**
- **Two new counters** in the metrics catalogue:
  - notification outcomes, labelled by source and result, in the gateway's notify routes;
  - email send outcomes, in `Notifier._send`.
- **A heartbeat for the cycle.** The cycle runs as a separate Railway cron process that nothing scrapes. When it finishes, it writes a small JSON heartbeat to the shared cache (Redis on Railway): when it ran, its error count, and its email failures. The gateway reads that heartbeat into gauges when Prometheus scrapes, as `refresh_scrape_gauges` already does for the outbox.
- **Alert rules** go in the one rule file, `deploy/monitoring/alerts/aura.yml`. Grafana Cloud loads that file (`docs/deployment/observability.md` §2).

**Tech Stack:** `prometheus_client` (existing catalogue), FastAPI, the Cache port, Prometheus rule YAML.

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` §6.1, item 5 ("payment webhook failures") and items 1–10 for the checklist.

## Global Constraints

- **No label carries a workspace, user, amount or reference.** Labels are only `source` ∈ {`fawry`, `apple`, `google`}, `result` (a fixed set), and the email `tag`.
- **A metrics failure never fails a payment.** Recording a metric or the heartbeat is best-effort: a cache outage is logged, and it never changes the response to Fawry or a store.
- **The heartbeat is written only by a cycle that ran.** A cycle that was skipped (another holds the lock) writes nothing. The cycle that holds the lock writes its own heartbeat.
- **The default `pytest` stays offline**, using the in-memory cache.
- **Alert names are unique.** Every metric an alert references exists in the catalogue (the existing `_referenced_metrics` / `_catalogue_names` test helpers).

## Review Focus

1. **Billing on, cycle never ran.** No heartbeat at all must alert, not read as healthy. Test: with billing enabled and no heartbeat, `aura_billing_enabled` is 1 and `aura_billing_cycle_last_run_timestamp_seconds` is 0. The rule's expression fires on 0 (`time() - 0` is large), and the rule test asserts the expression has no `> 0 and` guard.
2. **Billing off.** No billing alert may fire. Test: the gauge `aura_billing_enabled` is 0, and every billing rule's expression is conjoined with `aura_billing_enabled == 1`.
3. **The cache is down during a notification.** Fawry still gets its 200 and the payment applies. Test: a cache whose `set` raises causes no change to the notify response.
4. **A notify that raises an unexpected exception** is counted, not just the four known outcomes. Test: the handler raises `RuntimeError`, `result="error"` is counted, and the exception still propagates (500), as before.
5. **Email failures inside the cron cycle** reach the alert. Test: the cycle's notifier fails twice, the heartbeat records `email_failures: 2`, and the scrape gauge shows 2.

---

### Task 1: Metrics, the heartbeat and the scrape gauges

**Files:**
- `aura_backend/aura_core/observability/metrics.py`:
  - add `billing_notifications_total` (labels `source`, `result`);
  - add `email_sends_total` (labels `tag`, `result`);
  - add the gauges `billing_enabled`, `billing_cycle_last_run_timestamp_seconds`, `billing_cycle_last_errors` and `billing_cycle_last_email_failures`, all with `multiprocess_mode="livemax"`;
  - add `set_billing(*, enabled, last_run, errors, email_failures)`.
- `aura_backend/aura_core/api/billing.py`: wrap each notify handler's outcome in `_count(source, result)`.
  - The result is the handler's string result (`applied`, `duplicate`, `ignored`, `stale`, …).
  - Or one of `invalid_signature`, `malformed`, `store_unavailable`, `too_large`, `error`.
  - The route keeps its exact current responses.
- `aura_backend/aura_core/notifications/service.py`: `Notifier` gets `metrics: Any = None` and a `failures: int` counter. `_send` counts `ok` or `failed`.
- `aura_backend/aura_core/billing/service.py`:
  - `BillingService` gets `heartbeat: Any = None`, a Cache.
  - `run_cycle`, after a run that held the lock, writes JSON to `billing:cycle:heartbeat`: `{"at": epoch, "errors": n, "email_failures": notifier failures during this run}`. The write is best-effort.
- `aura_backend/aura_core/container.py`: wire `heartbeat=self.cache_backend` and `metrics` into the Notifier.
- `aura_backend/aura_core/api/v3.py`: `refresh_scrape_gauges` also reads the heartbeat and calls `metrics.set_billing(...)`, with `enabled=settings.billing.enabled`.
- **Test:** `aura_backend/tests/unit/test_billing_observability.py`.

**Interfaces:**
- **The heartbeat key:** `BILLING_HEARTBEAT_KEY = "billing:cycle:heartbeat"` in `aura_core/billing/service.py`, imported by `v3.py`.
- **`Metrics.set_billing`:**
  `set_billing(*, enabled: bool, last_run: float, errors: int, email_failures: int) -> None`.

**Steps:**
- [ ] **Write the tests.** They need, Review Focus 1, 3, 4 and 5 among them:
  - `test_notify_outcomes_are_counted_by_source_and_result`: a Fawry `applied`, then a `duplicate`, then a bad signature. In `metrics.render()`, `aura_billing_notifications_total{result="applied",source="fawry"} 1.0`, and the same for `duplicate` and `invalid_signature`.
  - `test_a_cache_outage_never_changes_the_notify_answer`.
  - `test_an_unexpected_error_is_counted_and_still_raises`.
  - `test_the_cycle_writes_a_heartbeat_and_the_scrape_reads_it`.
  - `test_cycle_email_failures_reach_the_gauge`.
  - `test_no_heartbeat_reads_as_zero_not_healthy`.
  - `test_email_sends_are_counted_without_the_address`: the render contains no `@`.
- [ ] **Run them:** they fail.
- [ ] **Implement.**
- [ ] **Run** them, then the full `pytest` and `tests/architecture`.
- [ ] **Commit.**

### Task 2: Alert rules

**Files:**
- `deploy/monitoring/alerts/aura.yml`: a new group, `aura-billing`. Every expression is conjoined with `aura_billing_enabled == 1`. The rules:
  - **`AuraBillingCycleStale`** (critical, `for: 15m`):
    `time() - max(aura_billing_cycle_last_run_timestamp_seconds) > 10800`, i.e. 3 h against an hourly cron.
  - **`AuraBillingCycleErrors`** (warning, `for: 2h`): `max(aura_billing_cycle_last_errors) > 0`.
  - **`AuraBillingNotificationsRejected`** (warning, `for: 0m`):
    `sum(increase(aura_billing_notifications_total{result=~"invalid_signature|malformed|too_large"}[1h])) > 3`.
    This means a forgery attempt or a wrong key or secret.
  - **`AuraBillingNotifyErrors`** (critical, `for: 5m`):
    `sum(increase(aura_billing_notifications_total{result="error"}[15m])) > 0`.
  - **`AuraStoreApiUnavailable`** (warning, `for: 30m`):
    `sum(increase(aura_billing_notifications_total{result="store_unavailable"}[30m])) > 0`.
  - **`AuraEmailFailing`** (warning, `for: 0m`):
    `sum(increase(aura_email_sends_total{result="failed"}[1h])) + max(aura_billing_cycle_last_email_failures) > 3`.

  Each rule has a summary and a description that names the first thing to check.
- **Test:** `aura_backend/tests/unit/test_gpu_pools_and_monitoring.py` gets `test_billing_alerts_exist_reference_catalogue_metrics_and_respect_the_switch`. For each of the six alerts it checks the severity label, that the metrics are in the catalogue, that `aura_billing_enabled == 1` is in the expression, and that alert names are unique. It also asserts that `AuraBillingCycleStale`'s expression has no `> 0 and` guard (Review Focus 1).

- [ ] Write the test, and run it: it fails.
- [ ] Add the rules.
- [ ] Run it: it passes.
- [ ] Commit.

### Task 3: The launch checklist

- [ ] **Create `docs/deployment/launch-readiness.md`.** It has one row per §6.1 item (1–10), with four columns:
  - **Status:** built, tested, proven, or open.
  - **Evidence named by the spec.**
  - **What exists today**, with links and the live facts seen on 2026-11-18.
  - **The next action and who takes it:** the owner, or a code change.
  - Item 5's row names the Grafana Cloud steps (`observability.md`) and the evidence still needed: **one alert fired in a test**. For example, `AuraBillingNotificationsRejected` fired by posting four unsigned notifications to `/billing/fawry/notify` on a staging deployment, with a screenshot or notification log committed under `docs/reports/`.
- [ ] **Link it** from `docs/status/README.md`, the index.
- [ ] **Commit.**
