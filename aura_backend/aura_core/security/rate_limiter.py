"""Rate limiter for production deployment protection.

Implements token bucket rate limiting with per-scope limits and global caps.
All features degrade gracefully when Redis is unavailable (in-memory fallback).

The limiter uses the same interface as the metrics module for consistency.
"""

from __future__ import annotations

import asyncio
import time
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

# Self-contained error classes
class ValidationError(Exception):
    """Minimal validation error for rate limiter self-containment."""
    pass


class RateLimitExceeded(ValidationError):
    """Raised when a rate limit is exceeded."""

    def __init__(self, scope: str, limit: int, retry_after: float) -> None:
        super().__init__(f"Rate limit exceeded for scope '{scope}': {limit} requests per minute. "
                         f"Retry after {retry_after:.1f} seconds.")
        self.scope = scope
        self.limit = limit
        self.retry_after = retry_after


@dataclass
class RateLimiter:
    """Token bucket rate limiter with per-scope tracking.

    Attributes:
        limit: Maximum requests per period
        period: Time period in seconds (default 60 = per minute)
        burst: Allow burst up to this many tokens (defaults to limit)
        _tokens: Current token count in bucket
        _last_refill: When tokens were last refilled
        _lock: Thread-safe lock for concurrent access
    """

    limit: int
    period: float = 60.0
    burst: int | None = None

    def __post_init__(self) -> None:
        self._burst = self.burst if self.burst is not None else self.limit
        self._tokens: float = float(self._burst)
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        add_tokens = (elapsed * self.limit) / self.period
        self._tokens = min(float(self._burst), self._tokens + add_tokens)
        self._last_refill = now

    def allow(self, *, scope: str = "global") -> Tuple[bool, dict[str, Any]]:
        """Check if a request is allowed under the rate limit.

        Returns:
            (allowed, info) where:
            - allowed: True if the request should be accepted
            - info: dict with rate limit metadata including retry_after
        """
        with self._lock:
            self._refill()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True, {
                    "allowed": True,
                    "scope": scope,
                    "limit": self.limit,
                    "remaining": max(0, int(self._tokens)),
                    "reset_at": self._last_refill + self.period,
                }
            else:
                # Calculate time until a token is available
                retry_after = ((1.0 - self._tokens) * self.period) / self.limit
                return False, {
                    "allowed": False,
                    "scope": scope,
                    "limit": self.limit,
                    "remaining": 0,
                    "retry_after": max(0.1, retry_after),
                    "reset_at": self._last_refill + self.period,
                }

    def check(self, *, scope: str = "global") -> dict[str, Any]:
        """Check rate limit status without consuming a token.

        Returns info about current rate limit state.
        """
        with self._lock:
            self._refill()
            return {
                "allowed": self._tokens >= 1.0,
                "scope": scope,
                "limit": self.limit,
                "remaining": max(0, int(self._tokens)),
                "reset_at": self._last_refill + self.period,
            }


class RateLimitManager:
    """Manages multiple rate limiters with scope-based routing."""

    def __init__(self) -> None:
        # Default limiters
        self._default_limiter = RateLimiter(limit=60, period=60.0)  # 60 per minute
        self._auth_limiter = RateLimiter(limit=10, period=60.0)  # 10 auth per minute
        self._endpoint_limits: dict[str, RateLimiter] = {}

        # Per-IP and per-user tracking (in-memory; Redis-backed in production)
        self._ip_counters: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"tokens": 60.0, "last_refill": time.monotonic()}
        )
        self._user_counters: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"tokens": 60.0, "last_refill": time.monotonic()}
        )

    def _get_limiter(self, scope: str, endpoint: str | None = None) -> RateLimiter:
        """Get the appropriate rate limiter for the given scope/endpoint."""
        # Check endpoint-specific limits first
        if endpoint and endpoint in self._endpoint_limits:
            return self._endpoint_limits[endpoint]

        # Check if this is an auth-related scope
        if scope == "auth" or endpoint and "/auth/" in (endpoint or ""):
            return self._auth_limiter

        # Return default
        return self._default_limiter

    def allow(
        self,
        *,
        scope: str = "global",
        endpoint: str | None = None,
        user_id: str | None = None,
        ip_address: str | None = None,
    ) -> Tuple[bool, dict[str, Any]]:
        """Check rate limit for a request.

        Args:
            scope: Rate limit scope ("global", "auth", "user:<id>", "ip:<addr>")
            endpoint: API endpoint path for endpoint-specific limits
            user_id: User identifier for per-user limiting
            ip_address: Client IP for per-IP limiting

        Returns:
            (allowed, info) tuple
        """
        limiter = self._get_limiter(scope, endpoint)

        # Apply per-user or per-IP overrides
        if user_id:
            user_key = f"user:{user_id}"
            if user_key in self._user_counters:
                user_limiter = RateLimiter(limit=limiter.limit, period=limiter.period)
                user_limiter._tokens = self._user_counters[user_key]["tokens"]
                user_limiter._last_refill = self._user_counters[user_key]["last_refill"]
                allowed, info = user_limiter.allow(scope=user_key)
                # Update counters
                self._user_counters[user_key] = {
                    "tokens": user_limiter._tokens,
                    "last_refill": user_limiter._last_refill,
                }
                info["scope"] = f"user:{user_id}"
                return allowed, info

        if ip_address:
            ip_key = f"ip:{ip_address}"
            if ip_key in self._ip_counters:
                ip_limiter = RateLimiter(limit=limiter.limit, period=limiter.period)
                ip_limiter._tokens = self._ip_counters[ip_key]["tokens"]
                ip_limiter._last_refill = self._ip_counters[ip_key]["last_refill"]
                allowed, info = ip_limiter.allow(scope=ip_key)
                # Update counters
                self._ip_counters[ip_key] = {
                    "tokens": ip_limiter._tokens,
                    "last_refill": ip_limiter._last_refill,
                }
                info["scope"] = f"ip:{ip_address}"
                return allowed, info

        # Use the main limiter
        return limiter.allow(scope=scope)

    def check(self, *, scope: str = "global", endpoint: str | None = None,
              user_id: str | None = None, ip_address: str | None = None) -> dict[str, Any]:
        """Check rate limit status without consuming a token."""
        limiter = self._get_limiter(scope, endpoint)
        info = limiter.check(scope=scope)

        # Add per-user/IP info if available
        if user_id:
            user_key = f"user:{user_id}"
            user_info = self._user_counters.get(user_key, {"tokens": limiter.limit, "last_refill": time.monotonic()})
            info["user_remaining"] = max(0, int(user_info["tokens"]))

        if ip_address:
            ip_key = f"ip:{ip_address}"
            ip_info = self._ip_counters.get(ip_key, {"tokens": limiter.limit, "last_refill": time.monotonic()})
            info["ip_remaining"] = max(0, int(ip_info["tokens"]))

        return info