import math
from typing import Any, Dict, List


def _finite(values: List[Any]) -> bool:
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values
    )


class ModelDriftAndPerformanceMonitor:
    """Monitor for detecting metric degradation, confidence shifts, and drift between baseline and current runs.

    Fails closed (ADR-036). A monitor that cannot compute a verdict -- a NaN or infinite metric, an
    empty sample -- reports ``drift_detected: True`` with ``status: "invalid_input"`` and an
    ``error``, because "no drift" is a claim the data does not support. Callers that test only
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
        if not _finite([baseline_accuracy, current_accuracy, max_allowed_drop]) or max_allowed_drop < 0:
            return cls._invalid(
                "baseline_accuracy, current_accuracy and max_allowed_drop must be finite numbers "
                "(max_allowed_drop >= 0).",
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
        if not isinstance(baseline_values, (list, tuple)) or not isinstance(current_values, (list, tuple)):
            return cls._invalid("baseline_values and current_values must be lists of numbers.")
        if not _finite(list(baseline_values) + list(current_values) + [max_rel_shift]) or max_rel_shift < 0:
            return cls._invalid("All values and max_rel_shift must be finite numbers (max_rel_shift >= 0).")

        base_mean = sum(baseline_values) / len(baseline_values)
        curr_mean = sum(current_values) / len(current_values)
        denom = abs(base_mean) if abs(base_mean) > 1e-9 else 1.0
        rel_shift = abs(curr_mean - base_mean) / denom
        if not (math.isfinite(base_mean) and math.isfinite(curr_mean) and math.isfinite(rel_shift)):
            # e.g. a sum that overflowed to infinity: NaN > threshold is False, which would read as "no drift".
            return cls._invalid("The means are not finite (values too large to average).")
        drift = rel_shift > max_rel_shift

        return {
            "status": "ok",
            "baseline_mean": round(base_mean, 4),
            "current_mean": round(curr_mean, 4),
            "relative_shift": round(rel_shift, 4),
            "max_rel_shift": max_rel_shift,
            "drift_detected": drift,
        }

    @classmethod
    def calculate_kl_divergence(
        cls,
        baseline_probs: List[float],
        current_probs: List[float],
        epsilon: float = 1e-7,
    ) -> Dict[str, Any]:
        """Calculate discrete Kullback-Leibler Divergence and Population Stability Index (PSI).

        Inputs are probabilities or counts (each side is normalised to sum to 1). Unequal or empty
        distributions, NaN/infinite/negative entries, an all-zero side or a non-positive
        ``epsilon`` cannot give a verdict: the result is ``valid: False`` and, like the other
        checks, ``drift_detected: True`` with ``status: "invalid_input"``.
        """
        if (
            not isinstance(baseline_probs, (list, tuple))
            or not isinstance(current_probs, (list, tuple))
            or len(baseline_probs) != len(current_probs)
            or not baseline_probs
        ):
            return {"valid": False, **cls._invalid("Distributions must be non-empty and of identical length.")}
        if not _finite(list(baseline_probs) + list(current_probs)) or min(list(baseline_probs) + list(current_probs)) < 0:
            return {"valid": False, **cls._invalid("Distributions must contain finite, non-negative numbers.")}
        if not _finite([epsilon]) or epsilon <= 0:
            return {"valid": False, **cls._invalid("epsilon must be a finite number > 0.")}
        sum_base, sum_curr = sum(baseline_probs), sum(current_probs)
        if sum_base <= 0 or sum_curr <= 0 or not math.isfinite(sum_base) or not math.isfinite(sum_curr):
            return {"valid": False, **cls._invalid("Each distribution must have a positive, finite total.")}

        p = [max(epsilon, x / sum_base) for x in baseline_probs]
        q = [max(epsilon, x / sum_curr) for x in current_probs]

        kl_div = sum(p[i] * math.log(p[i] / q[i]) for i in range(len(p)))
        # Population Stability Index (symmetric divergence)
        psi = sum((q[i] - p[i]) * math.log(q[i] / p[i]) for i in range(len(p)))

        # Standard PSI thresholds: <0.1 no shift, 0.1-0.25 moderate shift, >0.25 significant drift
        has_drift = psi > 0.25

        return {
            "valid": True,
            "status": "ok",
            "kl_divergence": round(kl_div, 4),
            "population_stability_index": round(psi, 4),
            "drift_detected": has_drift,
            "drift_severity": "HIGH" if psi > 0.25 else ("MODERATE" if psi > 0.1 else "NEGLIGIBLE"),
        }

    @classmethod
    def detect_concept_drift(
        cls,
        ground_truth_labels: List[Any],
        baseline_predictions: List[Any],
        current_predictions: List[Any],
        max_allowed_drop: float = 0.05,
    ) -> Dict[str, Any]:
        """Compare accuracy decay across baseline and current deployment cycles.

        The three sequences must be non-empty and the same length; a shorter one would silently
        score only part of the data, so a mismatch is invalid input (``drift_detected: True``).
        """
        seqs = (ground_truth_labels, baseline_predictions, current_predictions)
        if not all(isinstance(x, (list, tuple)) for x in seqs):
            return cls._invalid("Labels and predictions must be lists.")
        n = len(ground_truth_labels)
        if n == 0 or len(baseline_predictions) == 0 or len(current_predictions) == 0:
            return cls._invalid("Labels and predictions cannot be empty.")
        if not (len(baseline_predictions) == n == len(current_predictions)):
            return cls._invalid(
                "Labels and both prediction lists must be the same length "
                f"(got {n}, {len(baseline_predictions)}, {len(current_predictions)}).",
            )
        if not _finite([max_allowed_drop]) or max_allowed_drop < 0:
            return cls._invalid("max_allowed_drop must be a finite number >= 0.")

        base_correct = sum(1 for i in range(n) if baseline_predictions[i] == ground_truth_labels[i])
        curr_correct = sum(1 for i in range(n) if current_predictions[i] == ground_truth_labels[i])

        base_acc = base_correct / n
        curr_acc = curr_correct / n
        drop = round(base_acc - curr_acc, 4)

        return {
            "status": "ok",
            "samples_evaluated": n,
            "baseline_accuracy": round(base_acc, 4),
            "current_accuracy": round(curr_acc, 4),
            "accuracy_drop": drop,
            "drift_detected": drop > max_allowed_drop,
        }

    @classmethod
    def generate_drift_alert_report(cls, metrics: Dict[str, Any]) -> str:
        """Render a Markdown alert report for model observability.

        Stable only when the metrics carry an explicit ``drift_detected: False`` from a valid check;
        a missing verdict or an invalid-input result is never reported as stable.
        """
        if not isinstance(metrics, dict):
            metrics = {}
        verdict = metrics.get("drift_detected")
        invalid = metrics.get("status") == "invalid_input" or metrics.get("valid") is False
        if invalid:
            status = "\u26a0\ufe0f DRIFT CHECK COULD NOT RUN (INVALID INPUT)"
        elif verdict is True:
            status = "\u26a0\ufe0f DRIFT ALERT DETECTED"
        elif verdict is False:
            status = "\u2705 STABLE PERFORMANCE"
        else:
            status = "\u2753 NO DRIFT VERDICT SUPPLIED"

        lines = [
            f"# Model Observability Alert: {status}",
            "",
            f"- **Accuracy Drop:** `{metrics.get('accuracy_drop', 'N/A')}`",
            f"- **PSI Index:** `{metrics.get('population_stability_index', 'N/A')}`",
            f"- **Drift Severity:** `{metrics.get('drift_severity', 'N/A')}`",
        ]
        if metrics.get("error"):
            lines.append(f"- **Error:** {' '.join(str(metrics['error']).split())[:300]}")
        lines.extend([
            "",
            "## Recommended Action",
            "",
        ])
        if invalid:
            lines.append("- Fix the metric inputs and re-run the check before trusting any drift conclusion.")
        elif verdict is True:
            lines.append("- Trigger retraining pipeline on recent production window.")
        elif verdict is False:
            lines.append("- Continue scheduled telemetry monitoring.")
        else:
            lines.append("- Run a drift check; this report has no verdict to act on.")

        return "\n".join(lines)
