"""Scheduled monitoring of production inference (ADR-052): turn real telemetry into model-metric series.

``record_model_metric`` / ``compare_model_metric`` only knew what a caller told them, so nothing watched the
models actually serving users. This job reads the per-turn telemetry the gateway already records
(``aura_turn_telemetry``: outcome, degradation, latency, tool failures, groundedness, cost), summarises it per
model over a trailing window, appends one observation per (model, metric) to the same history the tools use
(platform-wide scope: no user is named), compares it with a baseline, and keeps the last report for the
admin API.

Policy, stated so nobody has to infer it:

* **Baseline.** The first observation of a series pins itself as its baseline (``baseline_pinned: auto``). An
  operator re-baselines on purpose (``rebaseline``) after a deliberate model change; until then drift is
  measured against the pin, so a slow degradation is not normalised away by a moving reference.
* **Thresholds.** Rates fall by more than 0.05 (success, non-degraded, tool success, groundedness) or latency / cost
  rise by more than 50% relative to the baseline.
* **Sample size.** A model with fewer than ``min_turns`` turns in the window is skipped, never judged on noise.
* **It measures; it does not act.** No evaluation or retraining is started (ADR-008 keeps retraining human-gated).
  A drift finding is logged and kept in the report, which says an evaluation is warranted.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..domain.errors import ToolExecutionError
from ..observability.logging import get_logger
from ..tools.drift import DriftStore

__all__ = ["run_model_monitor", "rebaseline", "last_report", "SERIES", "PLATFORM_OWNER"]

logger = get_logger("learning.model_monitor")

#: The scope the monitor writes under: the same hash the drift tools make of an owner id, of a fixed word.
PLATFORM_OWNER = hashlib.sha256(b"platform").hexdigest()[:24]
_REPORT_KEY = "aura:modelmonitor:last:v1"
_REPORT_TTL_S = 14 * 24 * 3600

#: ``(metric, telemetry summary key, transform, bad direction, threshold, relative)``.
SERIES: tuple[tuple[str, str, Callable[[float], float], str, float, bool], ...] = (
    ("success_rate", "error_rate", lambda v: 1.0 - v, "drop", 0.05, False),
    ("non_degraded_rate", "degraded_rate", lambda v: 1.0 - v, "drop", 0.05, False),
    ("tool_success_rate", "tool_success_rate", lambda v: v, "drop", 0.05, False),
    ("groundedness", "avg_groundedness", lambda v: v, "drop", 0.05, False),
    ("p95_latency_ms", "p95_latency_ms", lambda v: v, "rise", 0.5, True),
    ("avg_cost_usd", "avg_cost_usd", lambda v: v, "rise", 0.5, True),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _judge(reference: float, current: float, direction: str, threshold: float, relative: bool) -> dict[str, Any]:
    change = current - reference
    if relative:
        change = (change / reference) if reference else (0.0 if current == reference else float("inf"))
    bad = (-change if direction == "drop" else change)
    return {"reference": round(reference, 6), "current": round(current, 6),
            "change": round(change, 4) if change != float("inf") else "inf", "drift": bad > threshold}


async def run_model_monitor(
    telemetry: Any, cache: Any, *, window_hours: int = 24, min_turns: int = 20,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """One monitoring pass. Returns (and stores) the report; never starts an evaluation or a retrain."""
    now = clock()
    since = now - timedelta(hours=window_hours)
    summaries = await telemetry.recent_summary_by_model(since=since, min_turns=min_turns)
    store = DriftStore(cache)
    findings: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for model in sorted(summaries):
        summary = summaries[model]
        for metric, key, transform, direction, threshold, relative in SERIES:
            raw = summary.get(key)
            if raw is None:
                continue
            value = float(transform(float(raw)))
            try:
                data = await store.load(PLATFORM_OWNER, model, metric)
                await store.register_series(PLATFORM_OWNER, f"{model}:{metric}", "model_monitor")
                entry = await store.append(PLATFORM_OWNER, model, metric, {
                    "t": round(now.timestamp(), 3), "value": value,
                    "note": f"window={window_hours}h turns={summary['turns']}"}, data["legacy_seq"])
            except ToolExecutionError as exc:
                skipped.append({"model": model, "metric": metric, "reason": str(exc)[:160]})
                continue
            baseline = data["baseline"]
            auto = False
            if baseline is None:
                await store.pin_baseline(PLATFORM_OWNER, model, metric, entry)
                baseline, auto = entry, True
            verdict = _judge(float(baseline["value"]), value, direction, threshold, relative)
            status = "baseline" if auto else ("drift" if verdict["drift"] else "stable")
            finding = {"model": model, "metric": metric, "turns": summary["turns"], "status": status,
                       "baseline_pinned": "auto" if auto else "existing", **verdict}
            findings.append(finding)
            if status == "drift":
                logger.warning("model drift: %s %s moved from %s to %s (%s turns, last %sh)", model, metric,
                               verdict["reference"], verdict["current"], summary["turns"], window_hours)
    drift = [f for f in findings if f["status"] == "drift"]
    report = {
        "generated_at": now.isoformat(), "window_hours": window_hours, "min_turns": min_turns,
        "models_observed": sorted(summaries), "observations": len(findings), "drift": drift,
        "findings": findings, "skipped": skipped,
        "evaluation_triggered": False,
        "action_required": "evaluate_or_retrain" if drift else None,
        "note": ("Drift is measured against each series' pinned baseline; re-baseline deliberately after a model "
                 "change. This job measures only: retraining stays human-gated (ADR-008)."),
    }
    await cache.set(_REPORT_KEY, json.dumps(report, default=str).encode("utf-8"), ttl_s=_REPORT_TTL_S)
    return report


async def last_report(cache: Any) -> dict[str, Any] | None:
    raw = await cache.get(_REPORT_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


async def rebaseline(cache: Any, *, model: str | None = None) -> dict[str, Any]:
    """Pin each series' latest observation as its new baseline (all models, or just ``model``)."""
    store = DriftStore(cache)
    pinned: list[str] = []
    for name in await store.known_series(PLATFORM_OWNER):
        m, _, metric = name.partition(":")
        if model is not None and m != model:
            continue
        data = await store.load(PLATFORM_OWNER, m, metric)
        if data["entries"]:
            await store.pin_baseline(PLATFORM_OWNER, m, metric, data["entries"][-1])
            pinned.append(name)
    return {"rebaselined": pinned}
