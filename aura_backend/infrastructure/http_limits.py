"""Shared request limits for the production gateway.

Extracted from ``main.py`` so ``aura_service.create_app()`` can apply them
without importing the unused legacy application.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List

from fastapi import Request, Response
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
                limited = self._is_rate_limited_redis(client_ip)
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


class PayloadSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int = 5 * 1024 * 1024):  # 5MB limit
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    size = int(content_length)
                    if size > self.max_bytes:
                        return Response(
                            content="Payload Too Large. Maximum size is 5MB.",
                            status_code=413
                        )
                except ValueError:
                    return Response(
                        content="Invalid Content-Length header.",
                        status_code=400
                    )
        return await call_next(request)
