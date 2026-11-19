import math
from typing import Any, Dict, List, Optional


class ModelDriftAndPerformanceMonitor:
    """Monitor for detecting model performance degradation, confidence shifts,
    feature distribution drift, and PSI/KL divergence.

    Bugs fixed (v2):
    - check_accuracy_drift: NaN inputs now return drift_detected=True (fail-open)
      instead of silently computing a nonsense drop value.
    - check_mean_shift: empty input lists now return drift_detected=True (fail-open)
      instead of drift_detected=False, because we cannot prove the distributions
      are stable when no data was supplied.
    """

    @staticmethod
    def check_accuracy_drift(
        baseline_accuracy: float,
        current_accuracy: float,
        max_allowed_drop: float = 0.10,
    ) -> Dict[str, Any]:
        """Detect performance degradation when accuracy drops beyond max_allowed_drop.

        Fail-open on NaN inputs: if either accuracy value is NaN we cannot
        determine whether drift occurred, so we report drift_detected=True
        to trigger investigation rather than silently passing.
        """
        b = float(baseline_accuracy)
        c = float(current_accuracy)

        if math.isnan(b) or math.isnan(c):
            return {
                "baseline_accuracy": baseline_accuracy,
                "current_accuracy": current_accuracy,
                "accuracy_drop": None,
                "max_allowed_drop": max_allowed_drop,
                "drift_detected": True,
                "action_required": "retrain_or_recalibrate",
                "error": "NaN accuracy value supplied; treating as drift (fail-open).",
            }

        drop = round(b - c, 4)
        drift_detected = drop > max_allowed_drop
        return {
            "baseline_accuracy": baseline_accuracy,
            "current_accuracy": current_accuracy,
            "accuracy_drop": drop,
            "max_allowed_drop": max_allowed_drop,
            "drift_detected": drift_detected,
            "action_required": "retrain_or_recalibrate" if drift_detected else "none",
        }

    @staticmethod
    def check_mean_shift(
        baseline_values: List[float],
        current_values: List[float],
        max_rel_shift: float = 0.20,
    ) -> Dict[str, Any]:
        """Compare mean of two numeric distributions (e.g., confidence scores or latencies).

        Fail-open on empty inputs: when either list is empty we cannot compute
        a mean shift, so we report drift_detected=True rather than giving a
        false 'stable' verdict.
        """
        if not baseline_values or not current_values:
            return {
                "drift_detected": True,
                "error": "Both baseline_values and current_values must be non-empty; treating as drift (fail-open).",
            }

        base_mean = sum(baseline_values) / len(baseline_values)
        curr_mean = sum(current_values) / len(current_values)
        denom = abs(base_mean) if abs(base_mean) > 1e-9 else 1.0
        rel_shift = abs(curr_mean - base_mean) / denom
        drift = rel_shift > max_rel_shift

        return {
            "baseline_mean": round(base_mean, 4),
            "current_mean": round(curr_mean, 4),
            "relative_shift": round(rel_shift, 4),
            "max_rel_shift": max_rel_shift,
            "drift_detected": drift,
        }

    @staticmethod
    def calculate_kl_divergence(
        baseline_probs: List[float],
        current_probs: List[float],
        epsilon: float = 1e-7,
    ) -> Dict[str, Any]:
        """Calculate discrete Kullback-Leibler Divergence and Population Stability Index (PSI)."""
        if len(baseline_probs) != len(current_probs) or not baseline_probs:
            return {"valid": False, "error": "Distributions must be non-empty and of identical length."}

        sum_base = sum(baseline_probs) or 1.0
        sum_curr = sum(current_probs) or 1.0

        p = [max(epsilon, x / sum_base) for x in baseline_probs]
        q = [max(epsilon, x / sum_curr) for x in current_probs]

        kl_div = sum(p[i] * math.log(p[i] / q[i]) for i in range(len(p)))
        # Population Stability Index (symmetric divergence)
        psi = sum((q[i] - p[i]) * math.log(q[i] / p[i]) for i in range(len(p)))

        # Standard PSI thresholds: <0.1 no shift, 0.1-0.25 moderate shift, >0.25 significant drift
        has_drift = psi > 0.25

        return {
            "valid": True,
            "kl_divergence": round(kl_div, 4),
            "population_stability_index": round(psi, 4),
            "drift_detected": has_drift,
            "drift_severity": "HIGH" if psi > 0.25 else ("MODERATE" if psi > 0.1 else "NEGLIGIBLE"),
        }

    @staticmethod
    def detect_concept_drift(
        ground_truth_labels: List[Any],
        baseline_predictions: List[Any],
        current_predictions: List[Any],
    ) -> Dict[str, Any]:
        """Compare accuracy decay across baseline and current deployment cycles."""
        n = min(len(ground_truth_labels), len(baseline_predictions), len(current_predictions))
        if n == 0:
            return {"error": "Labels and predictions cannot be empty."}

        base_correct = sum(1 for i in range(n) if baseline_predictions[i] == ground_truth_labels[i])
        curr_correct = sum(1 for i in range(n) if current_predictions[i] == ground_truth_labels[i])

        base_acc = base_correct / n
        curr_acc = curr_correct / n
        drop = base_acc - curr_acc

        return {
            "samples_evaluated": n,
            "baseline_accuracy": round(base_acc, 4),
            "current_accuracy": round(curr_acc, 4),
            "accuracy_drop": round(drop, 4),
            "drift_detected": drop > 0.05,
        }

    @classmethod
    def generate_drift_alert_report(cls, metrics: Dict[str, Any]) -> str:
        """Render a Markdown alert report for model observability."""
        is_drift = metrics.get("drift_detected", False)
        status = "⚠️ DRIFT ALERT DETECTED" if is_drift else "✅ STABLE PERFORMANCE"

        lines = [
            f"# Model Observability Alert: {status}",
            "",
            f"- **Accuracy Drop:** `{metrics.get('accuracy_drop', 'N/A')}`",
            f"- **PSI Index:** `{metrics.get('population_stability_index', 'N/A')}`",
            f"- **Drift Severity:** `{metrics.get('drift_severity', 'N/A')}`",
            "",
            "## Recommended Action",
            "",
            "- Trigger retraining pipeline on recent production window." if is_drift else "- Continue scheduled telemetry monitoring.",
        ]

        return "\n".join(lines)
