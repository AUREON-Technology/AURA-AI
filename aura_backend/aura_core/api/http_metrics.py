"""Per-route HTTP request metrics (F-24, CB-15).

The turn histograms only see work that reaches the kernel. A request refused by auth, the rate
limiter or validation, a 5xx from a router outside the kernel, and every non-chat endpoint were
invisible, so there was no p95 per route to read and no error rate per status.

Pure ASGI rather than ``BaseHTTPMiddleware``: the latter buffers streaming responses through a
task group, which would hold SSE frames back and change the very latency being measured. The
duration runs to the last body byte, so for ``/api/v3/chat/stream`` it is the whole stream.

The ``route`` label is the matched route template (``/api/v3/sessions/{session_id}``), read
from ``scope["route"]`` after routing. A path that matched nothing is ``unmatched`` -- labelling
it with the raw path would let any scanner mint one series per URL it tries.
"""

from __future__ import annotations

import time
from typing import Any

__all__ = ["HttpMetricsMiddleware", "route_label"]

UNMATCHED = "unmatched"


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

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # A raised exception (or a client disconnect cancelling the stream) still counts:
            # a request that leaves no trace is how an error rate becomes invisible.
            try:
                self._metrics.observe_http(
                    scope.get("method", "GET"), route_label(scope), status,
                    time.perf_counter() - started,
                )
            except Exception:  # pragma: no cover - instrumentation must never fail a request
                pass
