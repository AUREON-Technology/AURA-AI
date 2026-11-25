"""Platform operations tools (ADR-047): health of the platform's own hosts, and live telemetry.

Two read-only tools, each registered only when the operator configured what it reads:

``check_platform_health``
    GETs ``/health/live``, ``/health/version`` and ``/health/ready`` on the operator's own platform
    origins (``AURA_OPS_PLATFORM_ORIGINS``). ``verify_release_health`` cannot: it goes through
    ``open_guarded``, which refuses the platform's hosts and ``/health`` on purpose (it takes
    caller-chosen URLs, so a poisoned prompt must not aim it at the gateway). This tool takes **no
    URL** -- the origin must be one of the configured ones and the three paths are fixed in code --
    so there is nothing for a prompt to redirect. https only, redirects are not followed, 64 KiB cap.

``query_telemetry``
    Reads the operator's Loki (logs), Prometheus (metrics) and Tempo (traces) over their HTTP APIs
    (``AURA_OPS_LOKI_URL`` ...), with bounded windows, row limits and timeouts, then runs the
    analyzers ``analyze_logs`` already has over the result. The URLs are operator configuration; the
    caller supplies only the query. Everything returned is another system's text, so both tools carry
    ``untrusted_content``.

Neither can change anything. Deploy / rollback / restart are ``tools/ops_deploy.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Sequence

from ..domain.errors import ToolExecutionError
from ..domain.tools import NETWORK_TAG, ToolCategory, ToolSensitivity
from ..ports.runtime import Tool
from .base import param, tool
from .open.telemetry_ext import analyze_incident
from .open.trace_ext import analyze_trace

__all__ = ["build_ops_tools", "OPS_TOOL_NAMES", "HEALTH_PATHS"]

OPS_TOOL_NAMES: frozenset[str] = frozenset({"check_platform_health", "query_telemetry"})
#: The only paths the health tool requests.
HEALTH_PATHS: tuple[str, ...] = ("/health/live", "/health/version", "/health/ready")
_MAX_BODY = 64 * 1024
_MAX_TELEMETRY_BODY = 4 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # a 3xx is reported, never followed
        return None


def _get(url: str, *, timeout: float, limit: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """One GET; never raises for an HTTP status. ``{status, body(bytes), ms}`` or ``{error}``."""
    request = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(_NoRedirect())
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(limit + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read(limit + 1)
    except Exception as exc:  # DNS, TLS, refused, timeout: the tool's answer is "could not reach"
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}", "ms": round((time.monotonic() - started) * 1000)}
    return {"status": status, "body": body[:limit], "truncated": len(body) > limit,
            "ms": round((time.monotonic() - started) * 1000)}


def _json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None


def _origin(url: str, *, insecure_ok: bool) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in (("https", "http") if insecure_ok else ("https",)) or not parts.hostname or parts.username \
            or parts.path.strip("/") or parts.query or parts.fragment:
        raise ValueError(f"{url!r} must be a bare https origin such as https://example.com")
    return f"{parts.scheme}://{parts.netloc}"


def _components(ready: Any) -> dict[str, Any]:
    """Every top-level entry of the readiness body that carries a ``healthy`` flag."""
    if not isinstance(ready, dict):
        return {}
    return {k: bool(v.get("healthy")) for k, v in ready.items() if isinstance(v, dict) and "healthy" in v}


def judge_health(results: dict[str, dict[str, Any]], *, expected_git_sha: str = "",
                 expected_schema_revision: str = "") -> tuple[bool, list[str], dict[str, Any]]:
    """``(healthy, problems, facts)`` from the three probe results."""
    problems: list[str] = []
    facts: dict[str, Any] = {}
    for path, result in results.items():
        if "error" in result:
            problems.append(f"{path}: could not be reached ({result['error']})")
        elif result["status"] == 404 and b"Application not found" in result.get("body", b""):
            problems.append(f"{path}: the host answers 'Application not found' -- the platform's router has "
                            "no application behind this hostname (not the app's own 404)")
        elif path != "/health/ready" and result["status"] != 200:
            problems.append(f"{path}: HTTP {result['status']}")
    version = _json(results.get("/health/version", {}).get("body", b"")) if "body" in results.get("/health/version", {}) else None
    if isinstance(version, dict):
        facts["git_sha"] = version.get("git_sha")
        facts["deployment_id"] = version.get("deployment_id")
        facts["app_version"] = version.get("app_version")
        if expected_git_sha and not str(version.get("git_sha") or "").startswith(expected_git_sha.lower()):
            problems.append(f"serving commit {str(version.get('git_sha') or 'unknown')[:12]}, expected {expected_git_sha[:12]}")
    ready_result = results.get("/health/ready", {})
    ready = _json(ready_result.get("body", b"")) if "body" in ready_result else None
    if "status" in ready_result:
        facts["ready_http_status"] = ready_result["status"]
        if ready_result["status"] != 200 and not (isinstance(ready, dict) and "healthy" in ready):
            if not any(p.startswith("/health/ready") for p in problems):
                problems.append(f"/health/ready: HTTP {ready_result['status']}")
    if isinstance(ready, dict):
        facts["components"] = _components(ready)
        if ready.get("healthy") is not True:
            down = [k for k, ok in facts["components"].items() if not ok]
            problems.append("not ready" + (f": unhealthy components: {', '.join(down)}" if down else ""))
        schema = ready.get("schema") if isinstance(ready.get("schema"), dict) else {}
        if schema:
            facts["schema"] = {k: schema.get(k) for k in ("revision", "expected", "healthy") if k in schema}
            if schema.get("healthy") is False:
                problems.append(f"database schema is behind the code (revision {schema.get('revision')}, "
                                f"expected {schema.get('expected')}): the migration has not run")
            if expected_schema_revision and str(schema.get("revision")) != expected_schema_revision:
                problems.append(f"schema revision {schema.get('revision')}, expected {expected_schema_revision}")
    return (not problems), problems, facts


def build_ops_tools(
    *,
    platform_origins: Sequence[str] = (),
    loki_url: str = "",
    prometheus_url: str = "",
    tempo_url: str = "",
    token_env: str = "",
    allow_insecure_http: bool = False,
) -> list[Tool]:
    """The ops tools the operator's configuration supports (possibly none)."""
    origins = [_origin(o, insecure_ok=allow_insecure_http) for o in platform_origins if o]
    sources = {"logs": loki_url.rstrip("/"), "metrics": prometheus_url.rstrip("/"), "traces": tempo_url.rstrip("/")}
    tools: list[Tool] = []

    def auth() -> dict[str, str]:
        token = os.environ.get(token_env, "") if token_env else ""
        return {"Authorization": f"Bearer {token}"} if token else {}

    if origins:
        @tool(
            "check_platform_health",
            "Probe the platform's own hosts: /health/live, /health/version and /health/ready. Reports "
            "whether it is ready, which commit is serving, component health and whether the database "
            "schema is at the code's revision. Takes no URL: origin must be one of the configured ones.",
            param("origin", "string", "One configured origin (default: all of them).", required=False, max_length=200),
            param("expected_git_sha", "string", "Commit (prefix) that should be serving.", required=False, max_length=40),
            param("expected_schema_revision", "string", "Alembic revision the database should be at.",
                  required=False, max_length=20),
            param("attempts", "integer", "Tries until healthy (default 2).", required=False, default=2,
                  minimum=1, maximum=6),
            category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.PUBLIC, timeout_s=120.0,
            max_result_chars=12_000, tags=("untrusted_content", NETWORK_TAG),
        )
        async def check_platform_health(origin: str | None = None, expected_git_sha: str | None = None,
                                        expected_schema_revision: str | None = None, attempts: int = 2) -> str:
            name = "check_platform_health"
            if origin is not None:
                try:
                    wanted = _origin(origin.strip().rstrip("/"), insecure_ok=allow_insecure_http)
                except ValueError as exc:
                    raise ToolExecutionError(str(exc), tool=name) from exc
                if wanted not in origins:
                    raise ToolExecutionError("that origin is not one of the platform origins the operator "
                                             "configured", tool=name, details={"configured": origins})
                targets = [wanted]
            else:
                targets = origins
            sha = (expected_git_sha or "").strip()
            if sha and not all(c in "0123456789abcdefABCDEF" for c in sha):
                raise ToolExecutionError("expected_git_sha must be hexadecimal", tool=name)

            async def probe(host: str) -> dict[str, Any]:
                healthy, problems, facts = False, ["not probed"], {}
                for attempt in range(1, attempts + 1):
                    raw = await asyncio.gather(*(asyncio.to_thread(
                        _get, host + p, timeout=10.0, limit=_MAX_BODY) for p in HEALTH_PATHS))
                    results = dict(zip(HEALTH_PATHS, raw))
                    healthy, problems, facts = judge_health(
                        results, expected_git_sha=sha, expected_schema_revision=(expected_schema_revision or "").strip())
                    if healthy or attempt == attempts:
                        break
                    await asyncio.sleep(3)
                return {"origin": host, "healthy": healthy, "problems": problems, "attempts": attempt, **facts,
                        "latency_ms": {p: r.get("ms") for p, r in results.items()}}

            reports = await asyncio.gather(*(probe(t) for t in targets))
            ok = all(r["healthy"] for r in reports)
            return json.dumps({
                "all_healthy": ok, "origins": reports, "read_only": True,
                "verdict": "platform is serving and ready" if ok else "platform NOT healthy: " + "; ".join(
                    f"{r['origin']}: {', '.join(r['problems'])}" for r in reports if not r["healthy"]),
            }, ensure_ascii=False, default=str)

        tools.append(check_platform_health)

    configured = [k for k, v in sources.items() if v]
    if configured:
        @tool(
            "query_telemetry",
            "Read live telemetry from the operator's stack, bounded and read-only. source: logs (LogQL, "
            "Loki), metrics (PromQL range query, Prometheus), traces (TraceQL search, Tempo) or trace "
            "(one trace id, analysed: critical path, slowest spans, errors). Configured: "
            + ", ".join(configured) + ".",
            param("source", "string", "What to read.", enum=("logs", "metrics", "traces", "trace")),
            param("query", "string", "LogQL / PromQL / TraceQL, or the trace id for source=trace.", max_length=500),
            param("minutes", "integer", "Look back this far (default 60, max 1440).", required=False, default=60,
                  minimum=1, maximum=1440),
            param("limit", "integer", "Max log lines / traces (default 200, max 500).", required=False,
                  default=200, minimum=1, maximum=500),
            category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.CONFIDENTIAL, timeout_s=60.0,
            max_result_chars=16_000, tags=("untrusted_content", NETWORK_TAG),
        )
        async def query_telemetry(source: str, query: str, minutes: int = 60, limit: int = 200) -> str:
            name = "query_telemetry"
            kind = "traces" if source == "trace" else source
            base = sources.get(kind, "")
            if not base:
                raise ToolExecutionError(f"{source} is not configured on this deployment (configured: "
                                         f"{', '.join(configured)})", tool=name)
            if not query.strip() or any(ord(c) < 32 for c in query):
                raise ToolExecutionError("the query must be non-empty text without control characters", tool=name)
            now = time.time()
            start = now - minutes * 60
            if source == "logs":
                qs = urllib.parse.urlencode({"query": query, "start": int(start * 1e9), "end": int(now * 1e9),
                                             "limit": limit, "direction": "backward"})
                got = await asyncio.to_thread(_get, f"{base}/loki/api/v1/query_range?{qs}", timeout=30.0,
                                              limit=_MAX_TELEMETRY_BODY, headers=auth())
                data = _need(got, name)
                streams = (data.get("data") or {}).get("result") or []
                rows = sorted(((int(v[0]), v[1], s.get("stream", {})) for s in streams for v in s.get("values", [])),
                              key=lambda r: r[0])[-limit:]
                lines = [r[1] for r in rows]
                analysis = analyze_incident(lines, None, None) if lines else None
                return json.dumps({
                    "source": "Loki", "query": query, "window_minutes": minutes, "lines": len(lines),
                    "possibly_truncated": len(rows) >= limit,
                    "sample": [{"ts_ns": r[0], "line": r[1][:400], "labels": r[2]} for r in rows[-20:]],
                    "analysis": analysis, "read_only": True}, ensure_ascii=False, default=str)
            if source == "metrics":
                step = max(15, minutes * 60 // 120)
                qs = urllib.parse.urlencode({"query": query, "start": int(start), "end": int(now), "step": step})
                got = await asyncio.to_thread(_get, f"{base}/api/v1/query_range?{qs}", timeout=30.0,
                                              limit=_MAX_TELEMETRY_BODY, headers=auth())
                data = _need(got, name)
                series = (data.get("data") or {}).get("result") or []
                summary = []
                for s in series[:25]:
                    values = [float(v[1]) for v in s.get("values", []) if v[1] not in ("NaN", "+Inf", "-Inf")]
                    if values:
                        summary.append({"metric": s.get("metric", {}), "points": len(values), "min": min(values),
                                        "max": max(values), "last": values[-1], "first": values[0]})
                return json.dumps({"source": "Prometheus", "query": query, "window_minutes": minutes,
                                   "series": len(series), "summary": summary, "read_only": True},
                                  ensure_ascii=False, default=str)
            if source == "traces":
                qs = urllib.parse.urlencode({"q": query, "start": int(start), "end": int(now), "limit": min(limit, 50)})
                got = await asyncio.to_thread(_get, f"{base}/api/search?{qs}", timeout=30.0,
                                              limit=_MAX_TELEMETRY_BODY, headers=auth())
                data = _need(got, name)
                found = [{k: t.get(k) for k in ("traceID", "rootServiceName", "rootTraceName", "durationMs")}
                         for t in data.get("traces", [])[:50]]
                return json.dumps({"source": "Tempo", "query": query, "window_minutes": minutes, "traces": found,
                                   "next": "call again with source=trace and a traceID to analyse one",
                                   "read_only": True}, ensure_ascii=False, default=str)
            trace_id = query.strip()
            if not all(c in "0123456789abcdefABCDEF" for c in trace_id) or not 16 <= len(trace_id) <= 32:
                raise ToolExecutionError("a trace id is 16-32 hexadecimal characters", tool=name)
            got = await asyncio.to_thread(_get, f"{base}/api/traces/{trace_id}", timeout=30.0,
                                          limit=_MAX_TELEMETRY_BODY, headers=auth())
            data = _need(got, name)
            return json.dumps({"source": "Tempo", "trace_id": trace_id, "analysis": analyze_trace(data),
                               "read_only": True}, ensure_ascii=False, default=str)

        tools.append(query_telemetry)
    return tools


def _need(got: dict[str, Any], name: str) -> Any:
    """The parsed JSON of a successful response, else a ``ToolExecutionError`` that says why."""
    if "error" in got:
        raise ToolExecutionError(f"telemetry backend unreachable: {got['error']}", tool=name)
    if got["status"] != 200:
        raise ToolExecutionError(f"telemetry backend answered HTTP {got['status']}: "
                                 f"{got['body'][:200].decode('utf-8', 'replace')}", tool=name)
    if got.get("truncated"):
        raise ToolExecutionError("the telemetry response was larger than the 4 MiB limit: narrow the query "
                                 "or the time window", tool=name)
    data = _json(got["body"])
    if data is None:
        raise ToolExecutionError("the telemetry backend did not return JSON", tool=name)
    return data
