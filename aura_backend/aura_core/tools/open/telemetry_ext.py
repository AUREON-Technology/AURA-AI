"""Metrics, structured logs and ranked root-cause hypotheses for ``analyze_logs`` (ADR-044, row 95).

``ObservabilityIncidentAnalyzer`` greps lines for error words. This works on what an operator actually
has to hand and reads it as data:

* ``parse_prometheus`` -- the Prometheus text exposition format (what ``/metrics`` returns, and what
  ``observability/`` here emits): families, types, labels, histograms (quantiles estimated from the
  buckets), counters.
* ``metric_anomalies`` -- ranks what looks wrong in one snapshot or, given an earlier snapshot, in the
  change between them: a ``*_healthy``/``up`` gauge at 0, an open circuit, an error share of a counter
  family, a slow histogram tail, a saturated pool or queue, a counter that jumped.
* ``parse_structured_logs`` -- JSON lines and logfmt (and plain ``<ts> LEVEL message`` lines): levels,
  components, a per-minute error timeline with burst detection, normalised error signatures, trace ids
  that carry errors across components, duration outliers.
* ``hypotheses`` -- a ranked list of root-cause *hypotheses*, each with the evidence lines (number and
  text) and the metric anomalies that support it, plus the next check to run. They are rules over
  patterns (dependency unreachable, resource exhaustion, bad deploy/config, auth, timeouts, rate
  limiting, database, provider failure, crash loop), so a confidence is how much of the pattern was
  seen, not a probability; the first-error ordering is used to prefer the earliest plausible cause.

It reads text the caller passes; it does not scrape ``/metrics`` or read a log store. Pulling those from
the live telemetry stack needs a connector the platform does not give agent tools, and the result says
so (``source: supplied text``).
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

__all__ = ["parse_prometheus", "metric_anomalies", "parse_structured_logs", "hypotheses", "analyze_incident"]

_MAX_SAMPLES = 20_000
_SAMPLE = re.compile(r'^([A-Za-z_:][\w:]*)(?:\{(.*)\})?\s+([^\s]+)(?:\s+(-?\d+))?\s*$')
_LABEL = re.compile(r'([A-Za-z_]\w*)\s*=\s*"((?:[^"\\]|\\.)*)"')
_ERRORISH_LABEL_VALUES = re.compile(r"^(error|errors|err|failed|failure|fail|5\d\d|timeout|exception|denied)$", re.IGNORECASE)


def _number(text: str) -> float | None:
    low = text.lower()
    if low in ("nan", "+nan", "-nan"):
        return None
    if low in ("+inf", "inf"):
        return math.inf
    if low == "-inf":
        return -math.inf
    try:
        return float(text)
    except ValueError:
        return None


def parse_prometheus(text: str) -> dict[str, Any]:
    """``{"families": {name: {type, help, samples:[{labels,value}]}}, "samples", "skipped"}``."""
    families: dict[str, dict[str, Any]] = {}
    types: dict[str, str] = {}
    helps: dict[str, str] = {}
    total = skipped = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line.split(None, 3)
            if len(parts) >= 4 and parts[1] == "TYPE":
                types[parts[2]] = parts[3].strip()
            elif len(parts) >= 4 and parts[1] == "HELP":
                helps[parts[2]] = parts[3][:200]
            continue
        match = _SAMPLE.match(line)
        value = _number(match.group(3)) if match else None
        if not match or value is None:
            skipped += 1
            continue
        total += 1
        if total > _MAX_SAMPLES:
            skipped += 1
            continue
        name = match.group(1)
        base = re.sub(r"_(bucket|sum|count|total|created)$", "", name) if re.search(r"_(bucket|sum|count)$", name) else name
        family = base if base in types or name not in types else name
        labels = {k: v for k, v in _LABEL.findall(match.group(2) or "")}
        entry = families.setdefault(family, {"type": types.get(family, "untyped"), "help": helps.get(family, ""), "samples": []})
        entry["samples"].append({"name": name, "labels": labels, "value": value})
    return {"families": families, "samples": min(total, _MAX_SAMPLES), "skipped": skipped}


def _quantile(buckets: list[tuple[float, float]], q: float) -> float | None:
    """Prometheus-style quantile estimate from cumulative ``(le, count)`` buckets."""
    ordered = sorted(buckets)
    if not ordered or ordered[-1][1] <= 0:
        return None
    rank = q * ordered[-1][1]
    previous_le, previous_count = 0.0, 0.0
    for le, count in ordered:
        if count >= rank:
            if math.isinf(le):
                return previous_le
            span = count - previous_count
            return le if span <= 0 else previous_le + (le - previous_le) * (rank - previous_count) / span
        previous_le, previous_count = (0.0 if math.isinf(le) else le), count
    return None


def _group(samples: list[dict[str, Any]], drop: tuple[str, ...] = ("le",)) -> dict[tuple, list[dict[str, Any]]]:
    out: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for s in samples:
        out[tuple(sorted((k, v) for k, v in s["labels"].items() if k not in drop))].append(s)
    return out


def _flat(parsed: dict[str, Any]) -> dict[tuple[str, tuple], float]:
    return {(s["name"], tuple(sorted(s["labels"].items()))): s["value"]
            for f in parsed["families"].values() for s in f["samples"]}


def metric_anomalies(
    parsed: dict[str, Any], previous: dict[str, Any] | None = None, slow_s: float = 1.0, error_share: float = 0.05,
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def add(severity: float, kind: str, metric: str, labels: dict[str, str], message: str, value: Any = None) -> None:
        found.append({"severity": round(severity, 2), "kind": kind, "metric": metric, "labels": labels,
                      "message": message, **({"value": value} if value is not None else {})})

    before = _flat(previous) if previous else {}
    for family, info in parsed["families"].items():
        samples = info["samples"]
        low = family.lower()
        if info["type"] in ("gauge", "untyped") and (low.endswith(("_healthy", "_up")) or low in ("up",)):
            for s in samples:
                if s["name"] == family and s["value"] == 0:
                    add(1.0, "down", family, s["labels"], f"{family}{_fmt(s['labels'])} is 0 (unhealthy/down)", 0)
        if "circuit" in low and info["type"] in ("gauge", "untyped"):
            for s in samples:
                if s["value"] >= 1 and "open" in (s["labels"].get("state", "open") + low):
                    add(0.9, "circuit_open", family, s["labels"], f"circuit open: {family}{_fmt(s['labels'])}", s["value"])
        if info["type"] == "histogram" or any(s["name"].endswith("_bucket") for s in samples):
            for key, group in _group(samples).items():
                buckets = [(math.inf if s["labels"].get("le") in ("+Inf", "Inf") else float(s["labels"]["le"]), s["value"])
                           for s in group if s["name"].endswith("_bucket") and "le" in s["labels"]
                           and _number(s["labels"]["le"].replace("Inf", "inf")) is not None]
                p95, p50 = _quantile(buckets, 0.95), _quantile(buckets, 0.5)
                unit_s = not low.endswith(("_ms", "_milliseconds"))
                limit = slow_s if unit_s else slow_s * 1000
                if p95 is not None and p95 > limit:
                    add(min(1.0, 0.5 + 0.1 * p95 / limit), "slow_tail", family, dict(key),
                        f"p95 of {family}{_fmt(dict(key))} is about {p95:.3g}{'s' if unit_s else 'ms'} "
                        f"(median {p50:.3g}), over the {limit:g} limit", round(p95, 4))
        if info["type"] == "counter" or low.endswith(("_total", "_errors", "_failures")):
            totals = [s for s in samples if s["name"] == family or s["name"] == family + "_total" or s["name"].endswith("_total")]
            all_sum = sum(s["value"] for s in totals) or 0.0
            err_sum = sum(s["value"] for s in totals if any(_ERRORISH_LABEL_VALUES.match(v) for v in s["labels"].values()))
            if all_sum > 0 and err_sum / all_sum > error_share and err_sum >= 1:
                add(min(1.0, 0.4 + err_sum / all_sum), "error_share", family, {},
                    f"{err_sum:g} of {all_sum:g} ({100 * err_sum / all_sum:.1f}%) in {family} carry an error-like label", round(err_sum / all_sum, 4))
            elif low.endswith(("_errors_total", "_failures_total", "_exceptions_total")) and err_sum == 0 and all_sum >= 1:
                add(min(0.8, 0.3 + math.log10(all_sum + 1) / 10), "error_counter", family, {}, f"{family} total is {all_sum:g}", all_sum)
            if before:
                for s in totals:
                    key = (s["name"], tuple(sorted(s["labels"].items())))
                    delta = s["value"] - before.get(key, 0.0)
                    errorish = any(_ERRORISH_LABEL_VALUES.match(v) for v in s["labels"].values()) or "error" in low or "fail" in low
                    if errorish and delta > 0 and (key not in before or delta >= max(5.0, 0.5 * before[key])):
                        add(min(1.0, 0.5 + math.log10(delta + 1) / 5), "error_jump", family, s["labels"],
                            f"{family}{_fmt(s['labels'])} rose by {delta:g} since the earlier snapshot", delta)
    # saturation: <x>_in_use|_active|_size against <x>_limit|_max|_capacity with the same labels
    flat = _flat(parsed)
    for (name, labels), value in flat.items():
        for used_suffix in ("_in_use", "_active", "_used", "_open"):
            if name.endswith(used_suffix):
                stem = name[: -len(used_suffix)]
                for cap_suffix in ("_limit", "_max", "_capacity", "_size", "_total_max"):
                    cap = flat.get((stem + cap_suffix, labels))
                    if cap and cap > 0 and value / cap >= 0.9:
                        add(min(1.0, 0.5 + value / cap / 2), "saturation", name, dict(labels),
                            f"{name}{_fmt(dict(labels))} is {value:g} of {cap:g} ({100 * value / cap:.0f}%)", round(value / cap, 3))
        if re.search(r"(queue|backlog|pending)_?(depth|length|size)?$", name) and value >= 100:
            add(min(1.0, 0.4 + math.log10(value) / 10), "backlog", name, dict(labels), f"{name}{_fmt(dict(labels))} is {value:g}", value)
    return sorted(found, key=lambda a: -a["severity"])[:50]


def _fmt(labels: dict[str, str]) -> str:
    return "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}" if labels else ""


# --- logs -------------------------------------------------------------------------------------

_LEVELS = {"debug": "debug", "trace": "debug", "info": "info", "notice": "info", "warn": "warning", "warning": "warning",
           "err": "error", "error": "error", "critical": "critical", "crit": "critical", "fatal": "critical",
           "panic": "critical", "alert": "critical", "emerg": "critical"}
_KV = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')
_PLAIN = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\d[T ][\d:.,]+(?:Z|[+-]\d\d:?\d\d)?)\s+(?:\[?(?P<lvl>[A-Za-z]+)\]?)\s+(?P<msg>.*)$")
_NORMALISE = [(re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
              (re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{16,}\b", re.I), "<hex>"),
              (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"), (re.compile(r"'[^']{0,80}'|\"[^\"]{0,80}\""), "<str>")]


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        text = re.sub(r"Z$", "+00:00", text)
        try:
            parsed = datetime.fromisoformat(text.replace(" ", "T", 1))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _record(index: int, line: str) -> dict[str, Any]:
    rec: dict[str, Any] = {"line": index, "raw": line.strip()[:500], "level": "info", "message": line.strip()[:500]}
    text = line.strip()
    fields: dict[str, Any] = {}
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                fields = data
        except ValueError:
            pass
    if not fields:
        plain = _PLAIN.match(text)
        if plain:
            fields = {"ts": plain.group("ts"), "level": plain.group("lvl"), "message": plain.group("msg")}
        else:
            fields = {k: v.strip('"') for k, v in _KV.findall(text)} if "=" in text else {}
    low = {str(k).lower(): v for k, v in fields.items()}
    level = str(low.get("level") or low.get("severity") or low.get("lvl") or "").lower()
    rec["level"] = _LEVELS.get(level, "error" if re.search(r"\b(error|exception|traceback|fatal|critical)\b", text, re.I) and not level
                               else "warning" if re.search(r"\bwarn(ing)?\b", text, re.I) and not level else "info")
    rec["message"] = str(low.get("message") or low.get("msg") or low.get("event") or text)[:500]
    rec["component"] = str(low.get("logger") or low.get("component") or low.get("service") or low.get("module") or "") or None
    rec["trace_id"] = str(low.get("trace_id") or low.get("traceid") or low.get("request_id") or low.get("correlation_id") or "") or None
    rec["time"] = _parse_time(low.get("ts") or low.get("timestamp") or low.get("time") or low.get("@timestamp"))
    for key in ("duration_ms", "latency_ms", "elapsed_ms", "took_ms"):
        if isinstance(low.get(key), (int, float)) and not isinstance(low.get(key), bool):
            rec["duration_ms"] = float(low[key])
            break
    exc = low.get("exception") or low.get("exc_info") or low.get("error") or low.get("error_type")
    rec["exception"] = str(exc)[:200] if exc else None
    for key in ("status", "status_code", "http_status"):
        if str(low.get(key, "")).isdigit():
            rec["status"] = int(low[key])
    return rec


def _signature(message: str) -> str:
    for pattern, repl in _NORMALISE:
        message = pattern.sub(repl, message)
    return re.sub(r"\s+", " ", message)[:160]


def parse_structured_logs(lines: list[str]) -> dict[str, Any]:
    records = [_record(i, ln) for i, ln in enumerate(lines, start=1) if ln.strip()]
    levels = Counter(r["level"] for r in records)
    errors = [r for r in records if r["level"] in ("error", "critical")]
    by_component = Counter(r["component"] for r in errors if r["component"])
    signatures = Counter(_signature(r["message"]) for r in errors)
    sig_first: dict[str, int] = {}
    for r in errors:
        sig_first.setdefault(_signature(r["message"]), r["line"])
    timeline: dict[str, int] = defaultdict(int)
    for r in errors:
        if r["time"]:
            timeline[r["time"].strftime("%Y-%m-%dT%H:%M")] += 1
    bursts: list[dict[str, Any]] = []
    if len(timeline) >= 3:
        counts = list(timeline.values())
        median = statistics.median(counts)
        spread = statistics.pstdev(counts)
        for minute, count in sorted(timeline.items()):
            if count >= max(3, median * 3, median + 3 * spread):
                bursts.append({"minute": minute, "errors": count})
    elif len(timeline) >= 1 and errors:
        peak = max(timeline.items(), key=lambda kv: kv[1])
        if peak[1] >= 5:
            bursts.append({"minute": peak[0], "errors": peak[1]})
    traces: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in errors:
        if r["trace_id"]:
            traces[r["trace_id"]].append(r)
    cross = [{"trace_id": t, "components": sorted({x["component"] or "?" for x in rs}), "errors": len(rs), "first_line": rs[0]["line"]}
             for t, rs in traces.items() if len({x["component"] for x in rs}) > 1]
    durations = [r["duration_ms"] for r in records if "duration_ms" in r]
    outliers: list[dict[str, Any]] = []
    if len(durations) >= 5:
        median = statistics.median(durations)
        cutoff = max(median * 5, 1000.0)
        outliers = [{"line": r["line"], "duration_ms": r["duration_ms"], "message": r["message"][:120]}
                    for r in records if r.get("duration_ms", 0) > cutoff][:10]
    times = [r["time"] for r in records if r["time"]]
    return {
        "records": len(records), "levels": dict(levels), "error_count": len(errors),
        "first_error": {"line": errors[0]["line"], "message": errors[0]["message"][:200],
                        "time": errors[0]["time"].isoformat() if errors[0]["time"] else None} if errors else None,
        "top_signatures": [{"signature": s, "count": c, "first_line": sig_first[s]} for s, c in signatures.most_common(8)],
        "errors_by_component": dict(by_component.most_common(8)),
        "bursts": bursts[:5], "cross_component_traces": cross[:5], "duration_outliers": outliers,
        "span": {"start": min(times).isoformat(), "end": max(times).isoformat()} if times else None,
        "_records": records,
    }


# --- hypotheses ---------------------------------------------------------------------------------

_RULES: list[tuple[str, re.Pattern[str], str, str, tuple[str, ...]]] = [
    ("dependency_unreachable", re.compile(r"connection ?(refused|reset|error)|econnrefused|could not connect|name or service not known|"
                                          r"no route to host|connect(?:ion)? timed out|temporary failure in name resolution|errno 111|"
                                          r"ConnectionError|ConnectError", re.I),
     "A dependency (database, cache, upstream API or model server) is unreachable.",
     "Check that the target is up and reachable from this service (DNS, network policy, port) and when it last restarted.", ("down", "circuit_open")),
    ("resource_exhaustion", re.compile(r"out of memory|\boom\b|killed process|memoryerror|too many (open files|connections|clients)|"
                                       r"pool (is )?exhausted|no space left|disk full|cannot allocate|resource temporarily unavailable|queuepool", re.I),
     "A resource limit was hit (memory, file descriptors, connection pool or disk).",
     "Compare usage with limits (container memory, ulimit, pool size, disk); look for a leak or a traffic spike before the first error.", ("saturation", "backlog")),
    ("bad_deploy_or_config", re.compile(r"modulenotfounderror|importerror|keyerror|missing (required )?(env|environment|config|setting)|"
                                        r"invalid (config|configuration)|alembic|migration|schema.*(behind|mismatch)|relation .* does not exist|"
                                        r"undefinedtable|no such (table|column)|unrecognized (argument|option)", re.I),
     "The service started with a bad build or configuration, or the database schema does not match the code.",
     "Compare the running release with the last good one; check required environment variables and that migrations ran before the app started.", ()),
    ("authentication", re.compile(r"\b401\b|\b403\b|unauthori[sz]ed|forbidden|invalid (token|credentials|api key)|token (has )?expired|"
                                  r"authentication failed|permission denied|access denied", re.I),
     "Credentials were rejected: an expired or wrong token/key, or a changed permission.",
     "Check credential expiry and the secret the failing caller actually sends; compare the time of the first rejection with a rotation or deploy.", ()),
    ("timeouts", re.compile(r"timed? ?out|deadline exceeded|\btimeout(error)?\b|read timeout|gateway time-?out|\b504\b|\b408\b", re.I),
     "Calls are exceeding their deadline: a slow or overloaded dependency, or a deadline set too tight.",
     "Look at the slow dependency's latency and load around the first timeout; check retries amplifying the load.", ("slow_tail", "backlog", "saturation")),
    ("rate_limited", re.compile(r"\b429\b|rate.?limit|too many requests|quota (exceeded|exhausted)|throttl", re.I),
     "An upstream or this service is rate limiting requests.", "Check request volume and the limit; look for a retry storm or a new heavy caller.", ()),
    ("database_problem", re.compile(r"deadlock|could not serialize|lock timeout|database is locked|psycopg|asyncpg|sqlalchemy|"
                                    r"too many clients already|canceling statement|statement timeout|integrityerror|uniqueviolation", re.I),
     "The database is refusing, blocking or cancelling queries.", "Check active/blocked queries, pool size against max_connections and recent migrations.", ("saturation",)),
    ("model_provider_failure", re.compile(r"allprovidersfailed|provider .*(unhealthy|unavailable|failed)|circuit (is )?open|no healthy provider|"
                                          r"model .* not found|inference .* (failed|error)", re.I),
     "Model providers are failing, so generation cannot be served.", "Check each provider's health and circuit state and the inference host's logs.", ("down", "circuit_open")),
    ("crash_loop", re.compile(r"\b(starting|booting|listening on|server started|application startup complete)\b", re.I),
     "The process keeps (re)starting.", "Look at the exit reason of the previous run (OOM kill, failed health check, uncaught exception on boot).", ()),
]


def hypotheses(logs: dict[str, Any] | None, anomalies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    records = logs["_records"] if logs else []
    errors = [r for r in records if r["level"] in ("error", "critical", "warning")]
    first_error_line = next((r["line"] for r in records if r["level"] in ("error", "critical")), None)
    for code, pattern, cause, next_check, anomaly_kinds in _RULES:
        pool = records if code == "crash_loop" else errors
        hits = [r for r in pool if pattern.search(r["raw"]) or pattern.search(r["message"])]
        if code == "crash_loop" and len(hits) < 3:
            hits = []
        support = [a for a in anomalies if a["kind"] in anomaly_kinds]
        if not hits and not support:
            continue
        if not hits and support and not logs:
            pass  # metrics-only incident: hypotheses from anomalies alone are allowed but weaker
        score = 0.0
        if hits:
            score += min(0.5, 0.15 + 0.07 * len(hits))
            if first_error_line is not None and hits[0]["line"] <= first_error_line + 2:
                score += 0.2  # the pattern is at (or right at) the start of the incident
            if any(r["level"] == "critical" for r in hits):
                score += 0.05
        if support:
            score += min(0.3, 0.12 * len(support))
        if hits and support:
            score += 0.1
        out.append({
            "cause": code, "summary": cause, "confidence": round(min(score, 0.95), 2),
            "evidence_lines": [{"line": r["line"], "text": r["raw"][:240]} for r in hits[:5]],
            "evidence_count": len(hits), "first_seen_line": hits[0]["line"] if hits else None,
            "supporting_metrics": [{"kind": a["kind"], "metric": a["metric"], "message": a["message"]} for a in support[:4]],
            "next_check": next_check,
        })
    # metric-only anomalies that no log rule explains still deserve a line
    explained = {k for _, _, _, _, kinds in _RULES for k in kinds}
    for a in anomalies:
        if a["kind"] not in explained and a["severity"] >= 0.6:
            out.append({"cause": f"metric_{a['kind']}", "summary": a["message"], "confidence": round(min(a["severity"] * 0.6, 0.6), 2),
                        "evidence_lines": [], "evidence_count": 0, "first_seen_line": None,
                        "supporting_metrics": [{"kind": a["kind"], "metric": a["metric"], "message": a["message"]}],
                        "next_check": f"Investigate {a['metric']}."})
    return sorted(out, key=lambda h: (-h["confidence"], h["first_seen_line"] or 10**9))[:8]


def analyze_incident(
    lines: list[str] | None, metrics_text: str | None, baseline_metrics_text: str | None, slow_s: float = 1.0,
) -> dict[str, Any]:
    logs = parse_structured_logs(lines) if lines else None
    parsed = parse_prometheus(metrics_text) if metrics_text else None
    previous = parse_prometheus(baseline_metrics_text) if baseline_metrics_text else None
    anomalies = metric_anomalies(parsed, previous, slow_s=slow_s) if parsed else []
    ranked = hypotheses(logs, anomalies)
    summary_logs = {k: v for k, v in logs.items() if k != "_records"} if logs else None
    return {
        "source": "supplied text (no live telemetry connection)",
        "logs": summary_logs,
        "metrics": {"samples": parsed["samples"], "families": len(parsed["families"]), "skipped": parsed["skipped"],
                    "anomalies": anomalies[:15]} if parsed else None,
        "hypotheses": ranked,
        "top_hypothesis": ranked[0]["cause"] if ranked else None,
        "note": "Hypotheses come from pattern rules over the supplied text; confidence is how much of the pattern was "
                "seen, not a probability. Verify with the next_check before acting.",
    }
