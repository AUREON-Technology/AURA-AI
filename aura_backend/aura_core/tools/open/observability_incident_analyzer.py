import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional


class ObservabilityIncidentAnalyzer:
    """Log and trace incident analyzer for extracting errors, stack exceptions, clustering traces, and generating postmortems."""

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

    @classmethod
    def cluster_error_traces(cls, error_messages: List[str]) -> List[Dict[str, Any]]:
        """Group similar error messages into pattern clusters by masking dynamic tokens."""
        clusters: Dict[str, List[str]] = defaultdict(list)

        for msg in error_messages:
            # Mask UUIDs, hex addresses, numbers, and quoted strings
            template = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<UUID>", msg)
            template = re.sub(r"0x[0-9a-fA-F]+", "<HEX>", template)
            template = re.sub(r"\b\d+\b", "<NUM>", template)
            template = re.sub(r"'[^']*'", "<STR>", template)
            template = re.sub(r'"[^"]*"', "<STR>", template)
            template = re.sub(r"\s+", " ", template).strip()
            clusters[template].append(msg)

        sorted_clusters = sorted(clusters.items(), key=lambda x: len(x[1]), reverse=True)
        return [
            {
                "pattern": pattern,
                "occurrence_count": len(samples),
                "example": samples[0],
            }
            for pattern, samples in sorted_clusters
        ]

    @classmethod
    def detect_error_spikes(
        cls,
        timestamps: List[float],
        window_seconds: float = 60.0,
        spike_threshold: int = 10,
    ) -> List[Dict[str, Any]]:
        """Identify temporal clusters where error occurrences exceed normal thresholds."""
        if not timestamps:
            return []

        sorted_ts = sorted(timestamps)
        spikes: List[Dict[str, Any]] = []

        window_start = sorted_ts[0]
        current_count = 0

        for ts in sorted_ts:
            if ts - window_start <= window_seconds:
                current_count += 1
            else:
                if current_count >= spike_threshold:
                    spikes.append({
                        "window_start": window_start,
                        "window_end": window_start + window_seconds,
                        "count": current_count,
                    })
                window_start = ts
                current_count = 1

        if current_count >= spike_threshold:
            spikes.append({
                "window_start": window_start,
                "window_end": window_start + window_seconds,
                "count": current_count,
            })

        return spikes

    @classmethod
    def generate_incident_postmortem(
        cls,
        incident_title: str,
        root_cause_analysis: Dict[str, Any],
        mitigation_steps: Optional[List[str]] = None,
    ) -> str:
        """Render a formal Markdown Incident Postmortem document."""
        first_inc = root_cause_analysis.get("first_incident", {})
        top_exc = root_cause_analysis.get("most_common_exception", "Unknown")

        lines = [
            f"# Postmortem: {incident_title}",
            "",
            f"**Incident Status:** {root_cause_analysis.get('status', 'resolved').upper()}",
            f"**Total Error Events:** {root_cause_analysis.get('incidents_count', 0)}",
            f"**Primary Exception:** `{top_exc}`",
            "",
            "## Timeline & Trigger",
            "",
            f"- **Earliest Error Line:** {first_inc.get('line_number', 'N/A')}",
            f"- **Log Message:** `{first_inc.get('log', 'N/A')}`",
            "",
            "## Root Cause Summary",
            "",
            f"The service experienced degraded performance driven predominantly by `{top_exc}`.",
            "",
            "## Mitigation & Action Items",
            "",
        ]

        actions = mitigation_steps or [
            "Implement defensive retry with exponential backoff.",
            "Add circuit breaker for upstream dependencies.",
            "Update integration test suite to cover fault scenario.",
        ]
        for a in actions:
            lines.append(f"- [ ] {a}")

        return "\n".join(lines)
