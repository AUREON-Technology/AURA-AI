import math
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional


class ObservabilityIncidentAnalyzer:
    """Log and trace incident analyzer for extracting errors, stack exceptions, clustering traces, and generating postmortems."""

    # English severity words plus the Arabic ones (error, fatal, critical, exception) that Arabic-language
    # services log. ``\b`` is Unicode-aware, so they match as whole words.
    SEVERITY_RE = re.compile(
        r"\b(ERROR|CRITICAL|FATAL|EXCEPTION|TRACEBACK|خطأ|خطا|فادح|حرج|استثناء)\b", re.IGNORECASE
    )
    EXCEPTION_TYPE_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Timeout|Failure))\b")
    _TRACEBACK_RE = re.compile(r"\bTraceback \(most recent call last\)", re.IGNORECASE)
    #: Incident lines listed in a result; ``incidents_count`` is always the true total.
    MAX_LISTED_INCIDENTS = 1000
    MAX_LOG_CHARS = 500

    @staticmethod
    def _lines(logs: Any) -> List[str]:
        """The log lines as text; non-string items are converted, ``None`` items dropped."""
        if isinstance(logs, str):
            return logs.splitlines()
        if not isinstance(logs, (list, tuple)):
            return []
        return [item if isinstance(item, str) else str(item) for item in logs if item is not None]

    @classmethod
    def parse_error_logs(cls, logs: List[str]) -> Dict[str, Any]:
        """Filter and categorize log lines containing error or fatal indicators.

        The exception named on the last line of a Python traceback (``ValueError: ...``) is credited
        to the traceback's incident rather than being lost or counted as a second incident.
        """
        lines = cls._lines(logs)
        error_lines: List[Dict[str, Any]] = []
        exception_counter: Counter[str] = Counter()
        total_incidents = 0
        open_traceback: Optional[Dict[str, Any]] = None

        for idx, line in enumerate(lines, start=1):
            if open_traceback is not None:
                if not line.strip() or line[:1] in " \t" or line.startswith(("During handling", "The above")):
                    continue  # frame lines of the same traceback
                trace_exc = cls.EXCEPTION_TYPE_RE.findall(line)
                for ex in trace_exc:
                    exception_counter[ex] += 1
                if open_traceback is not None and trace_exc:
                    open_traceback["exceptions"].extend(trace_exc)
                open_traceback = None
                if trace_exc and not cls.SEVERITY_RE.search(line):
                    continue

            if cls.SEVERITY_RE.search(line):
                total_incidents += 1
                exc_matches = cls.EXCEPTION_TYPE_RE.findall(line)
                for ex in exc_matches:
                    exception_counter[ex] += 1
                entry = {
                    "line_number": idx,
                    "log": line.strip()[:cls.MAX_LOG_CHARS],
                    "exceptions": exc_matches,
                }
                if len(error_lines) < cls.MAX_LISTED_INCIDENTS:
                    error_lines.append(entry)
                    if cls._TRACEBACK_RE.search(line):
                        open_traceback = entry

        result: Dict[str, Any] = {
            "total_log_lines": len(lines),
            "incidents_count": total_incidents,
            "top_exceptions": exception_counter.most_common(5),
            "incidents": error_lines,
        }
        if total_incidents > len(error_lines):
            result["incidents_truncated"] = True
        return result

    @classmethod
    def identify_root_cause(cls, logs: List[str]) -> Dict[str, Any]:
        """Identify the earliest and most frequent error signals from log entries.

        ``status`` is ``"healthy"`` only when non-blank log lines were read and none matched;
        with no log lines at all it is ``"no_data"`` (an empty log says nothing about health).
        """
        lines = cls._lines(logs)
        parsed = cls.parse_error_logs(lines)
        if not any(line.strip() for line in lines):
            return {
                "status": "no_data",
                "incidents_count": 0,
                "first_incident": None,
                "most_common_exception": None,
                "error": "No log lines were supplied, so nothing was analysed.",
            }
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
        """Identify request or span latencies exceeding a millisecond threshold.

        NaN, infinite, negative and non-numeric samples are not latencies: they are excluded from
        the average and counted in ``invalid_count`` (``status`` becomes ``"invalid_input"`` for a
        bad threshold and ``"no_data"`` when no valid sample was given), so a broken series cannot
        look like "no outliers".
        """
        if (
            isinstance(threshold_ms, bool)
            or not isinstance(threshold_ms, (int, float))
            or not math.isfinite(threshold_ms)
        ):
            return {
                "status": "invalid_input",
                "valid": False,
                "error": "threshold_ms must be a finite number.",
                "count": 0,
                "invalid_count": 0,
                "threshold_ms": threshold_ms if isinstance(threshold_ms, (int, float)) else None,
                "outliers_count": 0,
                "outliers": [],
            }

        samples: List[Any] = list(latencies_ms) if isinstance(latencies_ms, (list, tuple)) else []
        valid: List[tuple] = []
        invalid = 0
        for i, val in enumerate(samples):
            ok = isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val) and val >= 0
            if ok:
                valid.append((i, val))
            else:
                invalid += 1

        outliers = [{"index": i, "latency_ms": val} for i, val in valid if val > threshold_ms]
        avg_ms = round(sum(v for _, v in valid) / len(valid), 2) if valid else 0.0
        if not valid:
            status = "no_data"
        elif invalid:
            status = "partial"
        else:
            status = "ok"
        return {
            "status": status,
            "valid": invalid == 0 and bool(valid),
            "count": len(valid),
            "invalid_count": invalid,
            "avg_ms": avg_ms,
            "threshold_ms": threshold_ms,
            "outliers_count": len(outliers),
            "outliers": outliers,
        }

    @classmethod
    def cluster_error_traces(cls, error_messages: List[str]) -> List[Dict[str, Any]]:
        """Group similar error messages into pattern clusters by masking dynamic tokens."""
        clusters: Dict[str, List[str]] = defaultdict(list)

        for msg in cls._lines(error_messages):
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
                "example": samples[0][:cls.MAX_LOG_CHARS],
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
        """Identify temporal clusters where error occurrences exceed normal thresholds.

        A spike is any ``window_seconds``-long span holding at least ``spike_threshold`` events
        (a sliding window, so a burst that straddles a fixed boundary is still found). Overlapping
        spans are merged into one spike whose ``count`` is every event in it. Non-numeric and
        non-finite timestamps are ignored.
        """
        stamps = sorted(
            float(t) for t in (timestamps if isinstance(timestamps, (list, tuple)) else [])
            if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t)
        )
        if not stamps or not isinstance(window_seconds, (int, float)) or not math.isfinite(window_seconds):
            return []

        spikes: List[Dict[str, Any]] = []
        n = len(stamps)
        j = 0
        current: Optional[List[int]] = None  # [first index, last index] of the spike being built
        for i in range(n):
            if j < i:
                j = i
            while j + 1 < n and stamps[j + 1] - stamps[i] <= window_seconds:
                j += 1
            if j - i + 1 >= spike_threshold:
                if current is not None and i <= current[1]:
                    current[1] = max(current[1], j)
                else:
                    if current is not None:
                        spikes.append(cls._spike(stamps, current, window_seconds))
                    current = [i, j]
        if current is not None:
            spikes.append(cls._spike(stamps, current, window_seconds))
        return spikes

    @staticmethod
    def _spike(stamps: List[float], span: List[int], window_seconds: float) -> Dict[str, Any]:
        start = stamps[span[0]]
        return {
            "window_start": start,
            "window_end": max(start + window_seconds, stamps[span[1]]),
            "count": span[1] - span[0] + 1,
        }

    @staticmethod
    def _inline(value: Any, limit: int = 300) -> str:
        """One line of plain text safe to put between backticks in Markdown."""
        return " ".join(str(value).split()).replace("`", "'")[:limit]

    @classmethod
    def generate_incident_postmortem(
        cls,
        incident_title: str,
        root_cause_analysis: Dict[str, Any],
        mitigation_steps: Optional[List[str]] = None,
    ) -> str:
        """Render a formal Markdown Incident Postmortem document.

        The root-cause paragraph only names an exception when the analysis found one; a healthy or
        empty analysis is reported as such instead of being dressed up as an incident.
        """
        analysis = root_cause_analysis if isinstance(root_cause_analysis, dict) else {}
        first_inc = analysis.get("first_incident") or {}
        top_exc = analysis.get("most_common_exception")
        status = str(analysis.get("status", "unknown")).upper()

        if top_exc:
            summary = f"The service experienced degraded performance driven predominantly by `{cls._inline(top_exc)}`."
        elif analysis.get("status") == "degraded":
            summary = "Errors were logged but no exception type could be identified from them."
        else:
            summary = f"No error events were identified in the analysed logs (status: {cls._inline(status)})."

        lines = [
            f"# Postmortem: {cls._inline(incident_title, 200)}",
            "",
            f"**Incident Status:** {cls._inline(status)}",
            f"**Total Error Events:** {analysis.get('incidents_count', 0)}",
            f"**Primary Exception:** `{cls._inline(top_exc) if top_exc else 'Unknown'}`",
            "",
            "## Timeline & Trigger",
            "",
            f"- **Earliest Error Line:** {first_inc.get('line_number', 'N/A')}",
            f"- **Log Message:** `{cls._inline(first_inc.get('log', 'N/A'))}`",
            "",
            "## Root Cause Summary",
            "",
            summary,
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
            lines.append(f"- [ ] {cls._inline(a, 500)}")

        return "\n".join(lines)
