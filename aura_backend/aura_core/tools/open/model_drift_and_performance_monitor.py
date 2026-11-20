import math
from typing import Any, Dict, List


def _finite(values: List[Any]) -> bool:
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values
    )


class ModelDriftAndPerformanceMonitor:
    """Monitor for detecting metric degradation, confidence shifts, and drift between baseline and current runs.

    Fails closed. A monitor that cannot compute a verdict -- a NaN or infinite metric, an empty
    sample -- reports ``drift_detected: True`` with ``status: "invalid_input"`` and an ``error``,
    because "no drift" is a claim the data does not support. Callers that test only
    ``drift_detected`` therefore still stop and look.
    """

    @staticmethod
    def _invalid(error: str, **extra: Any) -> Dict[str, Any]:
        return {
            "status": "invalid_input",
            "drift_detected": True,
            "error": error,
            "action_required": "investigate_invalid_metrics",
            **extra,
        }

    @classmethod
    def check_accuracy_drift(
        cls,
        baseline_accuracy: float,
        current_accuracy: float,
        max_allowed_drop: float = 0.10,
    ) -> Dict[str, Any]:
        """Detect performance degradation when accuracy drops beyond max_allowed_drop."""
        if not _finite([baseline_accuracy, current_accuracy, max_allowed_drop]):
            return cls._invalid(
                "baseline_accuracy, current_accuracy and max_allowed_drop must be finite numbers.",
                baseline_accuracy=baseline_accuracy,
                current_accuracy=current_accuracy,
            )
        drop = round(float(baseline_accuracy) - float(current_accuracy), 4)
        drift_detected = drop > max_allowed_drop
        return {
            "status": "ok",
            "baseline_accuracy": baseline_accuracy,
            "current_accuracy": current_accuracy,
            "accuracy_drop": drop,
            "max_allowed_drop": max_allowed_drop,
            "drift_detected": drift_detected,
            "action_required": "retrain_or_recalibrate" if drift_detected else "none",
        }

    @classmethod
    def check_mean_shift(
        cls,
        baseline_values: List[float],
        current_values: List[float],
        max_rel_shift: float = 0.20,
    ) -> Dict[str, Any]:
        """Compare mean of two numeric distributions (e.g., confidence scores or latencies)."""
        if not baseline_values or not current_values:
            return cls._invalid("Both baseline_values and current_values must be non-empty.")
        if not _finite(list(baseline_values) + list(current_values) + [max_rel_shift]):
            return cls._invalid("All values and max_rel_shift must be finite numbers.")

        base_mean = sum(baseline_values) / len(baseline_values)
        curr_mean = sum(current_values) / len(current_values)
        denom = abs(base_mean) if abs(base_mean) > 1e-9 else 1.0
        rel_shift = abs(curr_mean - base_mean) / denom
        drift = rel_shift > max_rel_shift

        return {
            "status": "ok",
            "baseline_mean": round(base_mean, 4),
            "current_mean": round(curr_mean, 4),
            "relative_shift": round(rel_shift, 4),
            "max_rel_shift": max_rel_shift,
            "drift_detected": drift,
        }
