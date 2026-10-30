"""Shared request limits for the production gateway.

Extracted from ``main.py`` so ``aura_service.create_app()`` can apply them
without importing the unused legacy application.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List

from fastapi import Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from infrastructure.redis_client import get_redis_client

logger = logging.getLogger("aura_backend.http_limits")

# Shared connection — infrastructure/redis_client.py — same one used by the
# attestation nonce store and the refresh-token store (services/jwt_auth.py).
# Tests pin this to None so the in-process fallback is deterministic.
_rate_limit_redis = get_redis_client()


class RateLimitMiddleware(BaseHTTPMiddleware):
    # In-process fallback history; also what test suites clear/reset between tests.
    request_history: Dict[str, List[float]] = {}

    def __init__(self, app, requests_per_minute: int = 100):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute

    def _is_rate_limited_redis(self, client_ip: str) -> bool:
        window = int(time.time() // 60)
        key = f"ratelimit:{client_ip}:{window}"
        count = _rate_limit_redis.incr(key)
        if count == 1:
            _rate_limit_redis.expire(key, 60)
        return count > self.requests_per_minute

    def _is_rate_limited_local(self, client_ip: str) -> bool:
        now = time.time()
        if client_ip not in RateLimitMiddleware.request_history:
            RateLimitMiddleware.request_history[client_ip] = []

        # Keep only timestamps within the last 60 seconds
        RateLimitMiddleware.request_history[client_ip] = [
            ts for ts in RateLimitMiddleware.request_history[client_ip] if now - ts < 60
        ]

        if len(RateLimitMiddleware.request_history[client_ip]) >= self.requests_per_minute:
            return True

        RateLimitMiddleware.request_history[client_ip].append(now)
        return False

    async def dispatch(self, request: Request, call_next):
        # Exempt liveness/readiness probes so an exhausted IP window cannot
        # flap the orchestrator. Exact ``/health`` is the legacy probe path.
        path = request.url.path
        if path == "/health" or path.startswith("/health/"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        if _rate_limit_redis is not None:
            try:
                # The client is synchronous; called inline, every request's INCR ran on the event
                # loop, so a slow Redis stalled every request on the worker -- live SSE streams
                # included (F-23). In the threadpool it costs one thread for at most the client's
                # socket_timeout.
                limited = await run_in_threadpool(self._is_rate_limited_redis, client_ip)
            except Exception as e:
                logger.warning(f"Redis rate limit check failed, falling back to in-process: {e}")
                limited = self._is_rate_limited_local(client_ip)
        else:
            limited = self._is_rate_limited_local(client_ip)

        if limited:
            return Response(
                content="Rate limit exceeded. Maximum 100 requests per minute.",
                status_code=429
            )

        return await call_next(request)


class _PayloadTooLarge(StarletteHTTPException):
    """Raised from inside the body stream once it passes the limit.

    An ``HTTPException`` on purpose: FastAPI turns any other exception raised while reading a body
    into a 400 "error parsing the body", which would hide the real reason.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(
            status_code=413,
            detail=f"Payload Too Large. Maximum size is {limit // (1024 * 1024)}MB.",
        )


class PayloadSizeLimitMiddleware:
    """Caps request bodies by counting the bytes that actually arrive.

    This used to be a ``BaseHTTPMiddleware`` that only read ``Content-Length``: a body sent with
    ``Transfer-Encoding: chunked`` (no length header) was never measured at all. ``Content-Length``
    is still checked first, so an honest oversized request is refused before any of it is read;
    the receive stream is then counted, so a missing or understated header cannot get past.

    ``route_limits`` maps a path prefix to its own ceiling, for the routes whose real payloads are
    larger than the default (document upload is bounded by ``knowledge.max_document_bytes``, which
    a single global 5MB cap used to undercut).
    """

    def __init__(
        self,
        app,
        max_bytes: int = 5 * 1024 * 1024,
        route_limits: Dict[str, int] | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.route_limits = dict(route_limits or {})

    def _limit_for(self, path: str) -> int:
        for prefix, limit in self.route_limits.items():
            if path.startswith(prefix):
                return limit
        return self.max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope.get("path", ""))
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                await Response(content="Invalid Content-Length header.", status_code=400)(
                    scope, receive, send
                )
                return
            if size > limit:
                await self._too_large(limit)(scope, receive, send)
                return

        received = 0
        response_started = False

        async def counted_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _PayloadTooLarge(limit)
            return message

        async def tracked_send(message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except _PayloadTooLarge:
            # Reached only when nothing inside the app turned it into a response first.
            if not response_started:
                await self._too_large(limit)(scope, receive, send)

    @staticmethod
    def _too_large(limit: int) -> Response:
        return Response(
            content=f"Payload Too Large. Maximum size is {limit // (1024 * 1024)}MB.",
            status_code=413,
        )
