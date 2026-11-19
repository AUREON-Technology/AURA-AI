import copy
from typing import Any, Dict, List, Optional


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
        return [copy.deepcopy(v) for v in self._models.get(model_id, [])]

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
        eligible = float(metric_score) >= float(threshold)
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
        if float(threshold) <= 0:
            return {
                "promoted": False,
                "error": "threshold must be a positive number.",
            }

        versions = self._models.get(model_id, [])
        target = next((v for v in versions if v["version"] == version), None)
        if target is None:
            return {"promoted": False, "error": f"Version '{version}' not found for model '{model_id}'."}

        score = float(target["metrics"].get(primary_metric_key, 0.0))
        if score >= float(threshold):
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
        metrics_a = model_a.get("metrics", {})
        metrics_b = model_b.get("metrics", {})

        all_keys = set(metrics_a.keys()) | set(metrics_b.keys())
        deltas: Dict[str, Dict[str, Any]] = {}

        for k in sorted(all_keys):
            val_a = metrics_a.get(k)
            val_b = metrics_b.get(k)
            diff = (val_b - val_a) if (val_a is not None and val_b is not None) else None
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
        versions = self._models.get(model_id, [])
        if not versions:
            return None

        candidates = [v for v in versions if metric_key in v.get("metrics", {})]
        if not candidates:
            return None

        factor = 1.0 if higher_is_better else -1.0
        best = max(candidates, key=lambda v: factor * v["metrics"][metric_key])
        return copy.deepcopy(best)

    def deprecate_model(
        self,
        model_id: str,
        version: str,
        reason: str = "Superseded by newer release",
    ) -> Dict[str, Any]:
        """Mark a model version as deprecated/archived to remove it from candidate pools."""
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
