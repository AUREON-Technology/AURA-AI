"""Per-route HTTP request metrics (F-24, CB-15).

The turn histograms only see work that reaches the kernel. A request refused by auth, the rate
limiter or validation, a 5xx from a router outside the kernel, and every non-chat endpoint were
invisible, so there was no p95 per route to read and no error rate per status.

Pure ASGI rather than ``BaseHTTPMiddleware``: the latter buffers streaming responses through a
task group, which would hold SSE frames back and change the very latency being measured. The
duration runs to the last body byte, so for ``/api/v3/chat/stream`` it is the whole stream.

The ``route`` label is the matched route template (``/api/v3/sessions/{session_id}``), read
from ``scope["route"]`` after routing. A path that matched nothing is ``unmatched`` -- labelling
it with the raw path would let any scanner mint one series per URL it tries. The ``method`` label
is allow-listed for the same reason (F43): any RFC 9110 token parses as a method, so anything
outside the standard verbs is ``OTHER``.

The same middleware opens the request's trace span (Phase R2), so every span the kernel and
router record nests under it. The span carries the method, the route template and the status
code only: no URL, query string, client address or user agent, because spans leave for a
third-party collector. Health checks and the metrics scrape are not traced; at one probe a
minute they would be most of the trace volume and say nothing.
"""

from __future__ import annotations

import time
from typing import Any

from ..observability.tracing import span

__all__ = ["HttpMetricsMiddleware", "method_label", "route_label"]

#: Paths never traced: liveness/readiness probes and the metrics scrape.
UNTRACED_PREFIXES = ("/health", "/metrics")

UNMATCHED = "unmatched"

#: The only method labels a metric or span carries; every other token is :data:`OTHER_METHOD`.
KNOWN_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
OTHER_METHOD = "OTHER"


def method_label(scope: dict[str, Any]) -> str:
    method = scope.get("method")
    return method if isinstance(method, str) and method in KNOWN_METHODS else OTHER_METHOD


def route_label(scope: dict[str, Any]) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None) or getattr(route, "path_format", None)
    return path if isinstance(path, str) and path else UNMATCHED


class HttpMetricsMiddleware:
    def __init__(self, app: Any, *, metrics: Any) -> None:
        self.app = app
        self._metrics = metrics

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 500))
            await send(message)

        method = method_label(scope)
        try:
            if str(scope.get("path", "")).startswith(UNTRACED_PREFIXES):
                await self.app(scope, receive, send_wrapper)
            else:
                with span(f"HTTP {method}", **{"http.request.method": method}) as timer:
                    try:
                        await self.app(scope, receive, send_wrapper)
                    finally:
                        route = route_label(scope)
                        timer.rename(f"{method} {route}")
                        timer.set("http.route", route)
                        timer.set("http.response.status_code", status)
                        if status >= 500:
                            timer.mark_error()
        finally:
            # A raised exception (or a client disconnect cancelling the stream) still counts:
            # a request that leaves no trace is how an error rate becomes invisible.
            try:
                self._metrics.observe_http(
                    method, route_label(scope), status,
                    time.perf_counter() - started,
                )
            except Exception:  # pragma: no cover - instrumentation must never fail a request
                pass
