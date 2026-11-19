from typing import Any, Dict, List


class ModelDriftAndPerformanceMonitor:
    """Monitor for detecting metric degradation, confidence shifts, and drift between baseline and current runs."""

    @staticmethod
    def check_accuracy_drift(
        baseline_accuracy: float,
        current_accuracy: float,
        max_allowed_drop: float = 0.10,
    ) -> Dict[str, Any]:
        """Detect performance degradation when accuracy drops beyond max_allowed_drop."""
        drop = round(float(baseline_accuracy) - float(current_accuracy), 4)
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
        """Compare mean of two numeric distributions (e.g., confidence scores or latencies)."""
        if not baseline_values or not current_values:
            return {
                "drift_detected": False,
                "error": "Both baseline_values and current_values must be non-empty.",
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