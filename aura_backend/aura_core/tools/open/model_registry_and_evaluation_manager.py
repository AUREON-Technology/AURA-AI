"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has
`aura_core/mlops/model_registry.py` (`ModelRegistry`): promotion to production there needs a
recorded human approval (ADR-008) and a duplicate version raises `ConflictError`. This class
promotes on a metric threshold alone, silently overwrites versions, can leave two versions in
`production`, and hands callers live references to its stored metrics -- registering it would be a
second path to production that skips the approval gate.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

from typing import Any, Dict, List, Optional


class ModelRegistryAndEvaluationManager:
    """In-memory registry and evaluation evaluator for tracking model versions and promotion criteria."""

    def __init__(self) -> None:
        self._models: Dict[str, List[Dict[str, Any]]] = {}

    def register_model_version(
        self,
        model_id: str,
        version: str,
        metrics: Dict[str, float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Register or update a model version along with evaluation metrics."""
        versions = self._models.setdefault(model_id, [])
        existing = next((v for v in versions if v["version"] == version), None)
        entry = {
            "model_id": model_id,
            "version": version,
            "metrics": dict(metrics),
            "metadata": dict(metadata) if metadata else {},
            "stage": existing["stage"] if existing else "staging",
        }
        if existing is not None:
            existing.update(entry)
        else:
            versions.append(entry)
        return {"registered": True, "entry": dict(entry)}

    def list_versions(self, model_id: str) -> List[Dict[str, Any]]:
        """List all registered versions for a given model_id."""
        return [dict(v) for v in self._models.get(model_id, [])]

    @staticmethod
    def evaluate_eligibility(
        model_id: str,
        metric_score: float,
        threshold: float = 0.85,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evaluate whether a model score meets the deployment promotion threshold."""
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
        """Promote a registered model version to production if its primary metric meets threshold."""
        versions = self._models.get(model_id, [])
        target = next((v for v in versions if v["version"] == version), None)
        if target is None:
            return {"promoted": False, "error": f"Version '{version}' not found for model '{model_id}'."}

        score = float(target["metrics"].get(primary_metric_key, 0.0))
        if score >= float(threshold):
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