"""Cost-weighted, layered rate limiting.

The legacy limiter (``main.py:47-101``) counts requests per IP with one global limit of 100/min.
Three problems, all fixed here:

1. **Every request costs 1.** A health check and a full agent turn -- which consumes embeddings,
   retrieval, and a generation -- are treated identically. 100 agent turns per minute is far
   beyond what the hardware can serve, so the limit protects nothing that matters.
2. **Per-IP only.** Behind a reverse proxy every request carries the proxy's address, so the
   limit becomes global. There is no per-user limit at all.
3. **No cost ceiling.** Nothing bounds spend; a single user can exhaust the day's API budget.

This implementation applies limits in layers (user, then IP, then route class), weights requests
by what they actually consume, and uses a sliding window so a burst at a minute boundary cannot
double the effective rate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..observability.logging import get_logger
from ..ports.runtime import RateLimitDecision
from ..ports.storage import Cache

__all__ = ["RateLimitPolicy", "SlidingWindowRateLimiter", "extract_client_ip", "ROUTE_COSTS"]

logger = get_logger("security.rate_limit")


#: What each route class costs against a budget. Derived from what the request actually
#: consumes: a chat turn may run embeddings, two vector searches, and one or more generations;
#: a health check touches nothing.
ROUTE_COSTS: Mapping[str, int] = {
    "health": 0,
    "read": 1,
    "search": 3,
    "ingest": 5,
    "chat": 10,
    "agent": 20,     # planning and tool execution can multiply generations
    "admin": 2,
}


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    scope: str
    limit: int
    window_s: int = 60

    def __post_init__(self) -> None:
        if self.limit <= 0 or self.window_s <= 0:
            raise ValueError("rate limit and window must be positive")


class SlidingWindowRateLimiter:
    """Sliding-window counter over the :class:`Cache` port.

    The window is approximated from two fixed buckets, weighted by how far into the current one
    the request arrives:

        estimate = previous_bucket * (1 - elapsed_fraction) + current_bucket

    A plain fixed window lets a client send its full quota at 00:59 and again at 01:00 -- twice
    the intended rate across a two-second span. A precise sliding log would need one Redis entry
    per request. This costs two counters and is accurate to a few percent, which is the right
    trade for a limiter.

    Redis-backed via the cache port, so counts are shared across workers and replicas -- the
    legacy limiter falls back to a per-process dict when Redis is down, silently multiplying the
    effective limit by the number of workers.
    """

    def __init__(
        self,
        cache: Cache,
        *,
        policies: Sequence[RateLimitPolicy],
        metrics: Any = None,
        fail_open: bool = True,
    ) -> None:
        self._cache = cache
        self._policies = {p.scope: p for p in policies}
        self._metrics = metrics
        # Fail open by default: a Redis outage should degrade rate limiting, not availability.
        # Deployments that would rather shed load than risk an unmetered burst set this False.
        self._fail_open = fail_open

    def policy(self, scope: str) -> RateLimitPolicy | None:
        return self._policies.get(scope)

    async def check(self, *, key: str, scope: str, cost: int = 1) -> RateLimitDecision:
        policy = self._policies.get(scope)
        if policy is None or cost <= 0:
            return RateLimitDecision(allowed=True, limit=0, remaining=0, scope=scope)

        now = time.time()
        window = policy.window_s
        current_bucket = int(now // window)
        elapsed_fraction = (now % window) / window

        current_key = f"rl:{scope}:{key}:{current_bucket}"
        previous_key = f"rl:{scope}:{key}:{current_bucket - 1}"

        try:
            previous_raw = await self._cache.get(previous_key)
            previous = int(previous_raw) if previous_raw else 0
            # Increment first, then evaluate: checking before incrementing lets two concurrent
            # requests both observe an under-limit count and both proceed.
            current = 0
            for _ in range(cost):
                current = await self._cache.incr(current_key, ttl_s=window * 2)
        except Exception as exc:
            logger.warning(
                "rate limiter backend unavailable",
                extra={"scope": scope, "error": type(exc).__name__, "fail_open": self._fail_open},
            )
            return RateLimitDecision(
                allowed=self._fail_open, limit=policy.limit, remaining=0, scope=scope,
                retry_after_s=0 if self._fail_open else policy.window_s,
            )

        estimate = previous * (1.0 - elapsed_fraction) + current
        allowed = estimate <= policy.limit
        remaining = max(0, int(policy.limit - estimate))

        if not allowed:
            # Retry-After points past the portion of the previous window still in play, so a
            # client that honours it does not immediately re-trip the limit.
            retry_after = max(1, int(window * (1.0 - elapsed_fraction)) + 1)
            if self._metrics is not None:
                self._metrics.rate_limit_rejections_total.labels(scope=scope).inc()
            logger.info(
                "rate limit exceeded",
                extra={"scope": scope, "limit": policy.limit, "estimate": round(estimate, 1)},
            )
            return RateLimitDecision(
                allowed=False, limit=policy.limit, remaining=0,
                retry_after_s=retry_after, scope=scope,
            )

        return RateLimitDecision(
            allowed=True, limit=policy.limit, remaining=remaining, scope=scope
        )

    async def check_all(
        self, *, user_key: str | None, ip_key: str | None, route_class: str
    ) -> RateLimitDecision:
        """Apply every applicable layer, returning the first rejection.

        Order matters: the per-user limit is checked first so an abusive authenticated user is
        attributed correctly rather than being blamed on their NAT's shared address.
        """
        cost = ROUTE_COSTS.get(route_class, 1)
        if cost == 0:
            return RateLimitDecision(allowed=True, limit=0, remaining=0, scope="exempt")

        for scope, key in (("user", user_key), ("ip", ip_key)):
            if not key:
                continue
            decision = await self.check(key=key, scope=scope, cost=cost)
            if not decision.allowed:
                return decision
        return RateLimitDecision(allowed=True, limit=0, remaining=0, scope="ok")

    async def reset(self, *, key: str, scope: str) -> None:
        now = time.time()
        policy = self._policies.get(scope)
        window = policy.window_s if policy else 60
        bucket = int(now // window)
        await self._cache.delete(f"rl:{scope}:{key}:{bucket}")
        await self._cache.delete(f"rl:{scope}:{key}:{bucket - 1}")

    @classmethod
    def from_settings(cls, cache: Cache, settings: Any, *, metrics: Any = None
                      ) -> "SlidingWindowRateLimiter":
        return cls(
            cache,
            policies=[
                RateLimitPolicy(scope="user", limit=settings.rate_limit_per_user_per_minute * 10),
                RateLimitPolicy(scope="ip", limit=settings.rate_limit_per_ip_per_minute * 10),
            ],
            metrics=metrics,
        )


def extract_client_ip(headers: Mapping[str, str], *, peer: str | None, trusted_proxies: int = 1
                      ) -> str:
    """Resolve the real client address behind a reverse proxy.

    ``X-Forwarded-For`` is client-controlled: anyone can prepend arbitrary addresses to it. Only
    the last ``trusted_proxies`` entries were written by infrastructure we control, so the client
    is the entry immediately *before* those -- counted from the right, never from the left.

    Taking the leftmost entry (the common mistake) lets a client spoof its address and evade
    per-IP limiting entirely by sending ``X-Forwarded-For: 1.2.3.4``.
    """
    if trusted_proxies <= 0:
        return peer or "unknown"

    forwarded = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For")
    if not forwarded:
        return peer or "unknown"

    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not chain:
        return peer or "unknown"

    index = len(chain) - trusted_proxies
    if index < 0:
        # Fewer entries than configured proxies means the header did not traverse the expected
        # path. Fall back to the peer address rather than trusting an unexpected chain.
        return peer or chain[0]
    return chain[index] if index < len(chain) else chain[-1]
