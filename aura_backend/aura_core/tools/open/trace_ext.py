"""Trace / span analysis (ADR-047): critical path, slowest spans, errors, services.

Reads an OTLP-JSON trace as Tempo serves it (``batches`` / ``resourceSpans`` with ``scopeSpans``),
a Jaeger-style ``{"data":[{"spans":[...], "processes":{...}}]}`` document, or a flat list of span
objects, and answers the questions an incident needs: which span dominated the latency, what was the
chain that made the trace as long as it was (the critical path), which spans errored, and which
services the time went to. Pure: it analyses the text it is given.

A "critical path" here is the longest chain of spans from a root where each next span is the child that
ends last -- the sequence that, if made faster, would shorten the trace. It is not a proof of
causality; spans that overlap in time without a parent link are reported as separate roots.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["parse_spans", "analyze_trace"]

_MAX_SPANS = 20_000


def _attr_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("stringValue", "intValue", "boolValue", "doubleValue"):
            if key in value:
                return value[key]
    return value


def _attrs(items: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        if isinstance(item, dict) and "key" in item:
            out[str(item["key"])] = _attr_value(item.get("value"))
    return out


def _span(raw: dict[str, Any], service: str) -> dict[str, Any] | None:
    try:
        start = int(raw.get("startTimeUnixNano") or 0)
        end = int(raw.get("endTimeUnixNano") or 0)
    except (TypeError, ValueError):
        return None
    if not raw.get("spanId") or start <= 0 or end < 0:
        return None
    status = raw.get("status") or {}
    code = status.get("code") if isinstance(status, dict) else None
    error = code in (2, "2", "STATUS_CODE_ERROR", "ERROR")
    attrs = _attrs(raw.get("attributes"))
    if str(attrs.get("error", "")).lower() == "true" or attrs.get("otel.status_code") == "ERROR":
        error = True
    return {
        "id": str(raw.get("spanId") or ""), "parent": str(raw.get("parentSpanId") or ""),
        "name": str(raw.get("name") or "?")[:200], "service": service,
        "start": start, "end": max(end, start), "error": error,
        "message": str(status.get("message") or "")[:200] if isinstance(status, dict) else "",
    }


def parse_spans(document: Any) -> list[dict[str, Any]]:
    """Normalised spans from any supported trace document (a JSON string or already-parsed)."""
    if isinstance(document, (str, bytes)):
        document = json.loads(document)
    spans: list[dict[str, Any]] = []

    def otlp(batches: Any) -> None:
        for batch in batches or []:
            if not isinstance(batch, dict):
                continue
            service = str(_attrs((batch.get("resource") or {}).get("attributes")).get("service.name") or "unknown")
            for scope in batch.get("scopeSpans") or batch.get("instrumentationLibrarySpans") or []:
                for raw in scope.get("spans") or []:
                    if isinstance(raw, dict) and (s := _span(raw, service)):
                        spans.append(s)

    if isinstance(document, dict):
        trace = document.get("trace") if isinstance(document.get("trace"), dict) else document
        if "batches" in trace or "resourceSpans" in trace:
            otlp(trace.get("batches") or trace.get("resourceSpans"))
        elif isinstance(document.get("data"), list):  # Jaeger
            for item in document["data"]:
                procs = item.get("processes") or {}
                for raw in item.get("spans") or []:
                    refs = raw.get("references") or []
                    parent = next((r.get("spanID") for r in refs if r.get("refType") == "CHILD_OF"), "")
                    start = int(raw.get("startTime") or 0) * 1000
                    tags = {t.get("key"): t.get("value") for t in raw.get("tags") or [] if isinstance(t, dict)}
                    spans.append({
                        "id": str(raw.get("spanID") or ""), "parent": str(parent or ""),
                        "name": str(raw.get("operationName") or "?")[:200],
                        "service": str((procs.get(raw.get("processID")) or {}).get("serviceName") or "unknown"),
                        "start": start, "end": start + int(raw.get("duration") or 0) * 1000,
                        "error": bool(tags.get("error")), "message": "",
                    })
    elif isinstance(document, list):
        for raw in document:
            if isinstance(raw, dict) and (s := _span(raw, str(raw.get("service") or "unknown"))):
                spans.append(s)
    return spans[:_MAX_SPANS]


def analyze_trace(document: Any, *, top: int = 5) -> dict[str, Any]:
    spans = parse_spans(document)
    # Untrusted input: keep the first span of a duplicated id, and never let a span be its own parent.
    unique: dict[str, dict[str, Any]] = {}
    for sp in spans:
        if sp["id"] and sp["id"] not in unique:
            unique[sp["id"]] = sp
    spans = list(unique.values())
    if not spans:
        return {"spans": 0, "problem": "no spans were found in the trace document"}
    for sp in spans:
        if sp["parent"] == sp["id"]:
            sp["parent"] = ""
    by_id = unique
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for sp in spans:
        if sp["parent"] and sp["parent"] in by_id:
            children.setdefault(sp["parent"], []).append(sp)
        else:
            roots.append(sp)
    cyclic = not roots  # every span has a parent in the set: a loop, not a tree
    if cyclic:
        roots = [min(spans, key=lambda x: x["start"])]
    for sp in spans:
        kids = children.get(sp["id"], [])
        covered, last = 0, sp["start"]
        for kid in sorted(kids, key=lambda k: k["start"]):  # union of the children's time, clipped to the span
            lo, hi = max(kid["start"], last, sp["start"]), min(kid["end"], sp["end"])
            if hi > lo:
                covered += hi - lo
                last = hi
        sp["self_ms"] = round(max(0, (sp["end"] - sp["start"]) - covered) / 1e6, 3)
        sp["duration_ms"] = round((sp["end"] - sp["start"]) / 1e6, 3)

    root = max(roots, key=lambda r: r["end"] - r["start"])
    path, seen = [root], {root["id"]}
    node = root
    while True:  # iterative and cycle-safe: a trace may be thousands of spans deep, or malformed
        options = [k for k in children.get(node["id"], []) if k["id"] not in seen]
        if not options:
            break
        node = max(options, key=lambda k: k["end"])
        seen.add(node["id"])
        path.append(node)
    start, end = min(s["start"] for s in spans), max(s["end"] for s in spans)
    per_service: dict[str, float] = {}
    for sp in spans:
        per_service[sp["service"]] = per_service.get(sp["service"], 0.0) + sp["self_ms"]
    brief = lambda s: {k: s[k] for k in ("name", "service", "duration_ms", "self_ms", "error")}  # noqa: E731
    errors = [s for s in spans if s["error"]]
    out = {
        "spans": len(spans), "services": sorted(per_service), "roots": 0 if cyclic else len(roots),
        "duration_ms": round((end - start) / 1e6, 3),
        "root": brief(root),
        "critical_path": [brief(s) for s in path[:50]],
        "slowest_by_self_time": [brief(s) for s in sorted(spans, key=lambda s: -s["self_ms"])[:top]],
        "self_time_by_service_ms": {k: round(v, 3) for k, v in sorted(per_service.items(), key=lambda kv: -kv[1])[:20]},
        "errors": [{**brief(s), "message": s["message"]} for s in errors[:20]],
        "error_count": len(errors),
        "orphans": sum(1 for s in spans if s["parent"] and s["parent"] not in by_id),
        "verdict": (f"{len(errors)} span(s) errored; first on the path: "
                    f"{next((s['name'] for s in path if s['error']), errors[0]['name'] if errors else '-')}"
                    if errors else "no span reported an error"),
        "source": "supplied or fetched trace document",
    }
    if cyclic:
        out["warning"] = "the parent links form a loop (no root span); the earliest span was used as the root"
    if len(path) > 50:
        out["critical_path_truncated"] = len(path)
    return out
