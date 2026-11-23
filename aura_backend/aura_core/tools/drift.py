"""``record_model_metric`` / ``compare_model_metric``: model drift against a *stored* history (ADR-044).

``check_model_drift`` compares two numbers the caller already has. This keeps the history itself, in
the platform's :class:`~aura_core.ports.storage.Cache` port (Redis in production, in-memory in tests),
so "is the model worse than it was last week" no longer needs the caller to remember last week:

* ``record_model_metric`` appends an observation -- one score (``value``: accuracy, F1, pass rate) or a
  sample (``values``: latencies, confidences) -- to the series for ``(owner, model, metric)``, or pins
  the current state as the baseline, or clears the series.
* ``compare_model_metric`` judges the latest observation against the pinned baseline (or, with none, the
  earliest observations) using the same checks as ``check_model_drift`` -- accuracy drop, mean shift,
  and for samples PSI/KL over shared bins -- and adds the trend across the series.

Design points, each pinned by tests:

* **Per owner, no process-global state.** Keys are ``aura:drift:v1:<owner>:<model>:<metric>``, with the
  owner the caller's knowledge owner (a customer workspace / team scope) else the user. Nothing is held
  in module globals except a lock table that only serialises read-modify-write inside one process.
* **Bounded.** 50 observations per series, 200 sample values per observation, 50 series per owner,
  90-day expiry refreshed on every write, names restricted to ``[A-Za-z0-9_.:-]``.
* **Best-effort store.** The Cache contract swallows its own failures and reports a miss, so a store
  that is down reads as an empty history: the result then says ``history_entries: 0`` and asks for more
  data rather than claiming stability. Two *processes* recording the same series at the same instant
  can lose one observation (the port has no compare-and-set); the lock only covers one process.

It does **not** trigger an evaluation or a retraining run. When drift is found the result says
``evaluation_triggered: false`` and names what to run; starting one needs a job runner that agent tools
do not have (ADR-044).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import statistics
import time
from typing import Any

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from ..ports.storage import Cache
from .base import param, tool
from .open.model_drift_and_performance_monitor import ModelDriftAndPerformanceMonitor as Drift

__all__ = ["build_drift_tools", "DRIFT_TOOL_NAMES"]

DRIFT_TOOL_NAMES: frozenset[str] = frozenset({"record_model_metric", "compare_model_metric"})
_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_MAX_ENTRIES = 50
_MAX_VALUES = 200
_MAX_SERIES = 50
_TTL_S = 90 * 24 * 3600
_BINS = 10


def _owner(context: ToolContext) -> str:
    raw = str(context.knowledge_owner or context.user_id)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _key(owner: str, model: str, metric: str) -> str:
    return f"aura:drift:v1:{owner}:{model}:{metric}"


def _names(tool_name: str, model: str, metric: str) -> None:
    for label, value in (("model", model), ("metric", metric)):
        if not _NAME.match(value or ""):
            raise ToolExecutionError(f"{label} must be 1-80 characters of letters, digits and _ . : -", tool=tool_name)


def _clean_numbers(tool_name: str, values: list) -> list[float]:
    out: list[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise ToolExecutionError("values must all be finite numbers", tool=tool_name)
        out.append(float(v))
    return out


def _psi_bins(baseline: list[float], current: list[float]) -> tuple[list[float], list[float]] | None:
    lo, hi = min(baseline), max(baseline)
    if hi <= lo:
        return None
    width = (hi - lo) / _BINS

    def histogram(sample: list[float]) -> list[float]:
        counts = [0.0] * _BINS
        for v in sample:
            counts[min(_BINS - 1, max(0, int((v - lo) / width)))] += 1
        return counts

    return histogram(baseline), histogram(current)


def build_drift_tools(cache: Cache) -> list[Tool]:
    """The two tools over ``cache``; nothing else is shared between calls."""
    locks: dict[str, asyncio.Lock] = {}

    def lock_for(key: str) -> asyncio.Lock:
        if len(locks) > 2000:  # a long-lived gateway must not grow this without bound
            for stale in [k for k, v in locks.items() if not v.locked()][:1000]:
                locks.pop(stale, None)
        return locks.setdefault(key, asyncio.Lock())

    async def load(key: str) -> dict[str, Any]:
        raw = await cache.get(key)
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and isinstance(data.get("entries"), list):
                    return data
            except ValueError:
                pass
        return {"baseline": None, "entries": []}

    async def save(key: str, data: dict[str, Any]) -> None:
        await cache.set(key, json.dumps(data).encode("utf-8"), ttl_s=_TTL_S)

    async def register_series(owner: str, series: str, tool_name: str) -> None:
        index_key = f"aura:drift:v1:{owner}:_index"
        raw = await cache.get(index_key)
        try:
            known = json.loads(raw) if raw else []
        except ValueError:
            known = []
        if series not in known:
            if len(known) >= _MAX_SERIES:
                raise ToolExecutionError(
                    f"at most {_MAX_SERIES} model/metric series per owner; clear one first", tool=tool_name)
            known.append(series)
        await cache.set(index_key, json.dumps(known).encode("utf-8"), ttl_s=_TTL_S)

    @tool(
        "record_model_metric",
        "Store a model quality observation for later drift checks, per owner. operation: record (one "
        "`value` score such as accuracy, or a `values` sample such as latencies), set_baseline (pin "
        "the latest observation as the reference), clear. Writes to this owner's metric history only.",
        param("operation", "string", "What to do.", enum=("record", "set_baseline", "clear")),
        param("model", "string", "Model name, e.g. qwen2.5-3b.", max_length=80),
        param("metric", "string", "Metric name, e.g. accuracy, latency_ms.", max_length=80),
        param("value", "number", "One score (record).", required=False),
        param("values", "array", "A sample of numbers (record).", required=False, item_type="number"),
        param("note", "string", "Free text, e.g. the eval set or release.", required=False, max_length=200),
        category=ToolCategory.DATA, sensitivity=ToolSensitivity.PERSONAL, side_effecting=True, timeout_s=10.0,
    )
    async def record_model_metric(
        operation: str, model: str, metric: str, context: ToolContext, value: float | None = None,
        values: list | None = None, note: str | None = None,
    ) -> str:
        name = "record_model_metric"
        _names(name, model, metric)
        owner = _owner(context)
        key = _key(owner, model, metric)
        async with lock_for(key):
            data = await load(key)
            if operation == "clear":
                await cache.delete(key)
                return json.dumps({"cleared": True, "model": model, "metric": metric,
                                   "entries_removed": len(data["entries"])})
            if operation == "set_baseline":
                if not data["entries"]:
                    raise ToolExecutionError("nothing recorded for this model/metric yet", tool=name)
                data["baseline"] = data["entries"][-1]
                await save(key, data)
                return json.dumps({"baseline_set": True, "baseline": data["baseline"], "entries": len(data["entries"])})
            if (value is None) == (values is None):
                raise ToolExecutionError("record needs exactly one of value or values", tool=name)
            data["seq"] = int(data.get("seq", 0)) + 1  # identity of an observation (two can share a millisecond)
            entry: dict[str, Any] = {"t": round(time.time(), 3), "seq": data["seq"]}
            if value is not None:
                entry["value"] = _clean_numbers(name, [value])[0]
            else:
                sample = _clean_numbers(name, values or [])
                if not sample:
                    raise ToolExecutionError("values is empty", tool=name)
                entry["values"] = sample[:_MAX_VALUES]
                entry["truncated_from"] = len(sample) if len(sample) > _MAX_VALUES else None
            kind = "value" if "value" in entry else "values"
            if data["entries"] and ("value" in data["entries"][-1]) != ("value" in entry):
                raise ToolExecutionError(
                    "this series holds " + ("single scores" if "value" in data["entries"][-1] else "samples")
                    + "; use a different metric name for the other kind", tool=name)
            if note:
                entry["note"] = note
            await register_series(owner, f"{model}:{metric}", name)
            data["entries"] = [*data["entries"], entry][-_MAX_ENTRIES:]
            await save(key, data)
        return json.dumps({"recorded": True, "model": model, "metric": metric, "kind": kind,
                           "entries": len(data["entries"]), "baseline_pinned": data["baseline"] is not None})

    @tool(
        "compare_model_metric",
        "Judge a model's latest recorded observation against its pinned baseline (or its earliest "
        "observations) for drift: accuracy drop, mean shift, PSI for samples, and the trend. Reads this "
        "owner's history only. It does not start an evaluation; it says whether one is warranted.",
        param("model", "string", "Model name.", max_length=80),
        param("metric", "string", "Metric name.", max_length=80),
        param("max_allowed_drop", "number", "Largest acceptable drop of a score (default 0.05).", required=False,
              default=0.05, minimum=0),
        param("max_rel_shift", "number", "Largest acceptable relative mean shift of a sample (default 0.2).",
              required=False, default=0.2, minimum=0),
        param("include_history", "boolean", "Return the stored observations too.", required=False, default=False),
        category=ToolCategory.DATA, sensitivity=ToolSensitivity.PERSONAL, timeout_s=10.0,
    )
    async def compare_model_metric(
        model: str, metric: str, context: ToolContext, max_allowed_drop: float = 0.05,
        max_rel_shift: float = 0.2, include_history: bool = False,
    ) -> str:
        name = "compare_model_metric"
        _names(name, model, metric)
        data = await load(_key(_owner(context), model, metric))
        entries = data["entries"]
        result: dict[str, Any] = {"model": model, "metric": metric, "history_entries": len(entries),
                                  "evaluation_triggered": False}
        if include_history:
            result["history"] = entries[-_MAX_ENTRIES:]
        baseline = data.get("baseline")
        if not entries or (baseline is None and len(entries) < 2):
            result.update({
                "status": "insufficient_history", "drift_detected": None,
                "message": "Need a pinned baseline plus one observation, or at least two observations. "
                           "(A store that is unavailable also reads as empty.)"})
            return json.dumps(result)
        current = entries[-1]
        reference = baseline if baseline is not None else entries[0]
        if reference.get("seq") == current.get("seq"):
            result.update({"status": "insufficient_history", "drift_detected": None,
                           "message": "The baseline is the latest observation; record a newer one."})
            return json.dumps(result)
        result["baseline"] = {k: v for k, v in reference.items() if k != "values"} | (
            {"sample_size": len(reference["values"]), "mean": round(statistics.fmean(reference["values"]), 6)}
            if "values" in reference else {})
        result["baseline_source"] = "pinned" if baseline is not None else "earliest observation"
        if "value" in current and "value" in reference:
            check = Drift.check_accuracy_drift(reference["value"], current["value"], max_allowed_drop=max_allowed_drop)
            scores = [e["value"] for e in entries if "value" in e]
            trend: dict[str, Any] = {"observations": len(scores)}
            if len(scores) >= 3:
                trend["last_three_declining"] = scores[-3] > scores[-2] > scores[-1]
                n = len(scores)
                x_mean, y_mean = (n - 1) / 2, statistics.fmean(scores)
                denom = sum((i - x_mean) ** 2 for i in range(n)) or 1.0
                trend["slope_per_observation"] = round(sum((i - x_mean) * (s - y_mean) for i, s in enumerate(scores)) / denom, 6)
            result.update({"mode": "score", "current": current["value"], "check": check, "trend": trend,
                           "drift_detected": bool(check.get("drift_detected"))})
        elif "values" in current and "values" in reference:
            shift = Drift.check_mean_shift(reference["values"], current["values"], max_rel_shift=max_rel_shift)
            checks: dict[str, Any] = {"mean_shift": shift}
            bins = _psi_bins(reference["values"], current["values"])
            drift = bool(shift.get("drift_detected"))
            if bins is not None:
                psi = Drift.calculate_kl_divergence(bins[0], bins[1])
                checks["distribution"] = psi
                drift = drift or bool(psi.get("drift_detected"))
            result.update({"mode": "sample", "current": {"sample_size": len(current["values"]),
                                                          "mean": round(statistics.fmean(current["values"]), 6)},
                           "checks": checks, "drift_detected": drift})
        else:
            raise ToolExecutionError("the baseline and the latest observation are of different kinds", tool=name)
        if result["drift_detected"]:
            result["action_required"] = "evaluate_or_retrain"
            result["suggested_next"] = (
                "Re-run the model's evaluation set and record the new score; if it confirms the drop, "
                "retrain or roll back. This tool did not start anything.")
        result["status"] = "drift" if result["drift_detected"] else "stable"
        return json.dumps(result, default=str)

    return [record_model_metric, compare_model_metric]
