"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has
`aura_core/mlops/model_registry.py` (`ModelRegistry`): promotion to production there needs a
recorded human approval (ADR-008) and a duplicate version raises `ConflictError`. This class
promotes on a metric threshold alone, silently overwrites versions, can leave two versions in
`production`, and hands callers live references to its stored metrics -- registering it would be a
second path to production that skips the approval gate.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

import copy
import math
from typing import Any, Dict, List, Optional


def _finite(value: Any) -> Optional[float]:
    """``value`` as a finite float, or ``None`` (NaN, +-inf, text, None, bool, ...).

    A metric that is not a finite number never counts as passing: ``inf >= 0.85`` is true and
    ``nan`` poisons ``max()``, so both would otherwise promote or pick a broken model.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _key(value: Any) -> Optional[str]:
    """A usable model id / version: non-empty after ``str().strip()``; ``None`` is not one."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class ModelRegistryAndEvaluationManager:
    """In-memory registry and evaluation manager for tracking model versions,
    comparative benchmarks, and promotion stages.

    Bugs fixed (v2):
    - promote_if_eligible now demotes any other production version for the same
      model_id to 'staging' before promoting the new one, so only one production
      version exists per model at any time.
    - register_model_version stores a deep copy of metrics so callers cannot
      corrupt internal state via the returned reference.
    - list_versions returns deep copies of every version entry.
    - threshold <= 0 is explicitly rejected (a zero or negative threshold would
      promote a model that has no metric at all).
    - Re-registering a production version now demotes it to 'staging' first,
      forcing a fresh promotion check rather than silently overwriting metrics.
    - evaluate_eligibility documents that it is stateless (by design) and that
      callers are responsible for supplying the correct metric_score.
    """

    def __init__(self) -> None:
        self._models: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_model_version(
        self,
        model_id: str,
        version: str,
        metrics: Dict[str, float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Register or update a model version along with evaluation metrics.

        If the version already exists at 'production' stage it is demoted back
        to 'staging' before its metrics are updated, requiring a fresh call to
        promote_if_eligible.  This prevents metrics from being silently
        overwritten while the model is in production.
        """
        model_id, version = _key(model_id), _key(version)
        if model_id is None or version is None:
            return {"registered": False, "error": "model_id and version must be non-empty."}
        if not isinstance(metrics, dict):
            return {"registered": False, "error": "metrics must be an object of metric name -> number."}
        if metadata is not None and not isinstance(metadata, dict):
            return {"registered": False, "error": "metadata must be an object."}
        versions = self._models.setdefault(model_id, [])
        existing = next((v for v in versions if v["version"] == version), None)

        # Determine the new stage.  A production version being re-registered
        # is demoted so that its updated metrics are re-evaluated before it
        # can be promoted again.
        if existing is not None and existing["stage"] == "production":
            new_stage = "staging"
        elif existing is not None:
            new_stage = existing["stage"]
        else:
            new_stage = "staging"

        entry: Dict[str, Any] = {
            "model_id": model_id,
            "version": version,
            "metrics": copy.deepcopy(metrics),          # deep copy – isolate from caller
            "metadata": copy.deepcopy(metadata) if metadata else {},
            "stage": new_stage,
        }

        if existing is not None:
            existing.update(entry)
        else:
            versions.append(entry)

        return {"registered": True, "entry": copy.deepcopy(entry)}

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def list_versions(self, model_id: str) -> List[Dict[str, Any]]:
        """List all registered versions for a given model_id.

        Returns deep copies so that callers cannot mutate internal registry state.
        """
        return [copy.deepcopy(v) for v in self._models.get(_key(model_id), [])]

    # ------------------------------------------------------------------
    # Evaluation & Promotion
    # ------------------------------------------------------------------

    @staticmethod
    def evaluate_eligibility(
        model_id: str,
        metric_score: float,
        threshold: float = 0.85,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evaluate whether a model score meets the deployment promotion threshold.

        This method is intentionally stateless: it evaluates an arbitrary
        (model_id, metric_score) pair against a threshold without touching the
        registry.  Callers are responsible for supplying the correct metric_score
        retrieved from the registry.
        """
        score, limit = _finite(metric_score), _finite(threshold)
        if score is None or limit is None or limit <= 0:
            # A missing / NaN / infinite score is not evidence of quality, and a threshold of zero
            # or less accepts anything: neither may produce "ELIGIBLE".
            return {
                "model_id": model_id,
                "version": version,
                "score": metric_score,
                "threshold": threshold,
                "deployment_eligible": False,
                "status": "INVALID_INPUT",
                "error": "metric_score must be a finite number and threshold a finite positive number.",
            }
        eligible = score >= limit
        return {
            "model_id": model_id,
            "version": version,
            "score": metric_score,
            "threshold": threshold,
            "deployment_eligible": eligible,
            "status": "ELIGIBLE" if eligible else "BELOW_THRESHOLD",
        }

    def promote_if_eligible(
        self,
        model_id: str,
        version: str,
        primary_metric_key: str = "accuracy",
        threshold: float = 0.85,
    ) -> Dict[str, Any]:
        """Promote a registered model version to production if its primary metric meets threshold.

        Guarantees that at most one version per model_id is in 'production' at
        any time: any previously promoted version is demoted to 'staging'.

        A threshold <= 0 is rejected to prevent promoting a model that has no
        metric (which would score 0.0 and pass a zero threshold).
        """
        limit = _finite(threshold)
        if limit is None or limit <= 0:
            return {
                "promoted": False,
                "error": "threshold must be a positive number.",
            }

        model_id, version = _key(model_id), _key(version)
        versions = self._models.get(model_id, [])
        target = next((v for v in versions if v["version"] == version), None)
        if target is None:
            return {"promoted": False, "error": f"Version '{version}' not found for model '{model_id}'."}
        if target["stage"] == "deprecated":
            return {
                "promoted": False,
                "model_id": model_id,
                "version": version,
                "stage": "deprecated",
                "error": "A deprecated version cannot be promoted; register a new version instead.",
            }

        score = _finite(target["metrics"].get(primary_metric_key))
        if score is None:
            return {
                "promoted": False,
                "model_id": model_id,
                "version": version,
                "stage": target["stage"],
                "score": None,
                "threshold": threshold,
                "error": f"Metric '{primary_metric_key}' is missing or not a finite number.",
            }
        if score >= limit:
            # Demote any existing production version for this model first.
            for v in versions:
                if v["stage"] == "production" and v["version"] != version:
                    v["stage"] = "staging"

            target["stage"] = "production"
            return {
                "promoted": True,
                "model_id": model_id,
                "version": version,
                "stage": "production",
                "score": score,
                "threshold": threshold,
            }

        return {
            "promoted": False,
            "model_id": model_id,
            "version": version,
            "stage": target["stage"],
            "score": score,
            "threshold": threshold,
        }

    # ------------------------------------------------------------------
    # Comparison & Best-model selection
    # ------------------------------------------------------------------

    @staticmethod
    def compare_models(
        model_a: Dict[str, Any],
        model_b: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Produce head-to-head delta comparison between two model versions."""
        if not isinstance(model_a, dict) or not isinstance(model_b, dict):
            return {"model_a_id": None, "model_b_id": None, "metric_comparisons": {},
                    "error": "model_a and model_b must be objects with a 'metrics' object."}
        metrics_a = model_a.get("metrics") if isinstance(model_a.get("metrics"), dict) else {}
        metrics_b = model_b.get("metrics") if isinstance(model_b.get("metrics"), dict) else {}

        all_keys = set(metrics_a.keys()) | set(metrics_b.keys())
        deltas: Dict[str, Dict[str, Any]] = {}

        for k in sorted(all_keys):
            val_a = metrics_a.get(k)
            val_b = metrics_b.get(k)
            fa, fb = _finite(val_a), _finite(val_b)
            diff = (fb - fa) if (fa is not None and fb is not None) else None
            deltas[k] = {
                "model_a": val_a,
                "model_b": val_b,
                "delta": round(diff, 4) if diff is not None else None,
            }

        return {
            "model_a_id": model_a.get("version") or model_a.get("model_id"),
            "model_b_id": model_b.get("version") or model_b.get("model_id"),
            "metric_comparisons": deltas,
        }

    def get_best_model_for_task(
        self,
        model_id: str,
        metric_key: str = "accuracy",
        higher_is_better: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Identify the highest performing registered version for a target metric."""
        versions = self._models.get(_key(model_id), [])
        if not versions:
            return None

        # Deprecated versions are out of the candidate pool (see deprecate_model), and a metric that
        # is not a finite number cannot be ranked (NaN would make max() order-dependent).
        candidates = [
            v for v in versions
            if v["stage"] != "deprecated" and _finite(v.get("metrics", {}).get(metric_key)) is not None
        ]
        if not candidates:
            return None

        factor = 1.0 if higher_is_better else -1.0
        best = max(candidates, key=lambda v: factor * _finite(v["metrics"][metric_key]))
        return copy.deepcopy(best)

    def deprecate_model(
        self,
        model_id: str,
        version: str,
        reason: str = "Superseded by newer release",
    ) -> Dict[str, Any]:
        """Mark a model version as deprecated/archived to remove it from candidate pools."""
        model_id, version = _key(model_id), _key(version)
        versions = self._models.get(model_id, [])
        target = next((v for v in versions if v["version"] == version), None)
        if not target:
            return {"deprecated": False, "error": f"Version '{version}' not found for model '{model_id}'."}

        target["stage"] = "deprecated"
        target["deprecation_reason"] = reason

        return {
            "deprecated": True,
            "model_id": model_id,
            "version": version,
            "stage": "deprecated",
            "reason": reason,
        }
