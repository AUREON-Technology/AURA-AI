import re
from collections import Counter
from typing import Any, Dict, List


class ObservabilityIncidentAnalyzer:
    """Log and trace incident analyzer for extracting errors, stack exceptions, and root-cause hints."""

    SEVERITY_RE = re.compile(r"\b(ERROR|CRITICAL|FATAL|EXCEPTION|TRACEBACK)\b", re.IGNORECASE)
    EXCEPTION_TYPE_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Timeout|Failure))\b")

    @classmethod
    def parse_error_logs(cls, logs: List[str]) -> Dict[str, Any]:
        """Filter and categorize log lines containing error or fatal indicators."""
        error_lines: List[Dict[str, Any]] = []
        exception_counter: Counter[str] = Counter()

        for idx, line in enumerate(logs, start=1):
            if cls.SEVERITY_RE.search(line):
                exc_matches = cls.EXCEPTION_TYPE_RE.findall(line)
                for ex in exc_matches:
                    exception_counter[ex] += 1
                error_lines.append({
                    "line_number": idx,
                    "log": line.strip(),
                    "exceptions": exc_matches,
                })

        return {
            "total_log_lines": len(logs),
            "incidents_count": len(error_lines),
            "top_exceptions": exception_counter.most_common(5),
            "incidents": error_lines,
        }

    @classmethod
    def identify_root_cause(cls, logs: List[str]) -> Dict[str, Any]:
        """Identify the earliest and most frequent error signals from log entries."""
        parsed = cls.parse_error_logs(logs)
        if parsed["incidents_count"] == 0:
            return {
                "status": "healthy",
                "incidents_count": 0,
                "first_incident": None,
                "most_common_exception": None,
            }

        first_inc = parsed["incidents"][0]
        top_exc = parsed["top_exceptions"][0][0] if parsed["top_exceptions"] else None

        return {
            "status": "degraded",
            "incidents_count": parsed["incidents_count"],
            "first_incident": first_inc,
            "most_common_exception": top_exc,
        }

    @staticmethod
    def extract_latency_outliers(latencies_ms: List[float], threshold_ms: float = 1000.0) -> Dict[str, Any]:
        """Identify request or span latencies exceeding a millisecond threshold."""
        outliers = [
            {"index": i, "latency_ms": val}
            for i, val in enumerate(latencies_ms)
            if val > threshold_ms
        ]
        avg_ms = round(sum(latencies_ms) / len(latencies_ms), 2) if latencies_ms else 0.0
        return {
            "count": len(latencies_ms),
            "avg_ms": avg_ms,
            "threshold_ms": threshold_ms,
            "outliers_count": len(outliers),
            "outliers": outliers,
        }