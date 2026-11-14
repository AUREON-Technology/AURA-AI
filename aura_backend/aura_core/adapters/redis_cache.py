"""Redis implementation of the :class:`~aura_core.ports.storage.Cache` port.

Shared cache state across workers and replicas. That sharing is the point: the legacy rate
limiter falls back to a per-process dict when Redis is unreachable, which silently multiplies the
effective limit by the worker count.

**Two different failure policies in one class, deliberately.**

Reads and writes are best-effort. A cache miss costs a recomputation, so swallowing a connection
error and reporting a miss keeps a Redis outage to a latency problem -- the port documents this as
the one place in the codebase where silent degradation is correct.

``incr`` is the exception and **raises**. It backs
:class:`~aura_core.security.rate_limit.SlidingWindowRateLimiter`, whose fallback is not
recomputation but *unmetered traffic*. That limiter already catches backend errors and consults
its own ``fail_open`` flag to decide between shedding load and admitting it. If this method
swallowed the error and returned a plausible count instead, the limiter's ``except`` would never
fire, ``fail_open=False`` would silently become ``fail_open=True``, and a deployment that had
explicitly chosen to shed load under a Redis outage would instead serve unlimited requests
believing it was protected. Raising is what keeps that decision where an operator set it.

The client is created lazily on first use, so importing this module -- or constructing the
container -- never opens a socket. Health checks and settings validation stay independent of
whether Redis happens to be up.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from ..domain.errors import ConfigurationError

logger = logging.getLogger(__name__)

__all__ = ["RedisCache"]


class RedisCache:
    """Implements the ``Cache`` port against Redis.

    Values are opaque ``bytes``; this adapter never interprets them. Namespacing and key
    construction belong to callers -- ``LLMCache`` namespaces per user, and the rate limiter
    builds ``rl:{scope}:{key}:{bucket}`` -- so a prefix here would only obscure keys during
    debugging.
    """

    def __init__(
        self,
        url: str,
        *,
        metrics: Any = None,
        socket_timeout_s: float = 2.0,
        default_ttl_s: int = 3600,
    ) -> None:
        if not url:
            raise ConfigurationError(
                "cache_backend='redis' requires AURA_REDIS_URL",
                details={"setting": "AURA_REDIS_URL"},
            )
        self._url = url
        self._metrics = metrics
        self._default_ttl = default_ttl_s
        # A short timeout on purpose. This sits in the request path, and a cache that takes
        # seconds to answer is worse than one that reports a miss immediately: the fallback is a
        # recomputation that would already have finished.
        self._socket_timeout = socket_timeout_s
        self._client: Any = None

    # -- connection -------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Build the client on first use.

        Lazy so that constructing the container -- which happens during settings validation and in
        every test that builds one -- does not require a reachable Redis. The import is local for
        the same reason the provider registry imports adapters inside branches: a deployment that
        uses the in-memory cache should not need the dependency installed.
        """
        if self._client is None:
            try:
                import redis.asyncio as redis
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise ConfigurationError(
                    "cache_backend='redis' requires the redis package",
                    details={"install": "pip install redis"},
                ) from exc
            self._client = redis.from_url(
                self._url,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_timeout,
                # Bytes in, bytes out. The port's contract is bytes, and decoding here would make
                # `get` lossy for the pickled and JSON payloads callers store.
                decode_responses=False,
            )
        return self._client

    def _record_error(self, operation: str, exc: Exception) -> None:
        logger.warning(
            "redis cache operation failed",
            extra={"operation": operation, "error": type(exc).__name__},
        )
        if self._metrics is not None:
            counter = getattr(self._metrics, "cache_errors_total", None)
            if counter is not None:
                counter.labels(operation=operation).inc()

    # -- best-effort reads and writes -------------------------------------------------

    async def get(self, key: str) -> bytes | None:
        try:
            return await self._get_client().get(key)
        except Exception as exc:
            self._record_error("get", exc)
            return None

    async def set(self, key: str, value: bytes, *, ttl_s: int | None = None) -> None:
        ttl = self._default_ttl if ttl_s is None else ttl_s
        try:
            if ttl and ttl > 0:
                await self._get_client().set(key, value, ex=ttl)
            else:
                # ttl of 0 or None-after-default means "no expiry", matching InMemoryCache, which
                # stores expires_at=0.0 for that case.
                await self._get_client().set(key, value)
        except Exception as exc:
            self._record_error("set", exc)

    async def delete(self, key: str) -> None:
        try:
            await self._get_client().delete(key)
        except Exception as exc:
            self._record_error("delete", exc)

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, bytes]:
        """One round trip rather than N.

        Returns only the keys that were present, like ``InMemoryCache.get_many`` -- callers
        distinguish hit from miss by absence, so mapping a miss to ``None`` here would make
        ``key in result`` true for a miss.
        """
        if not keys:
            return {}
        try:
            values = await self._get_client().mget(list(keys))
        except Exception as exc:
            self._record_error("get_many", exc)
            return {}
        return {key: value for key, value in zip(keys, values) if value is not None}

    # -- the operation that must not fail silently ------------------------------------

    async def incr(self, key: str, *, ttl_s: int | None = None, amount: int = 1) -> int:
        """Atomic increment, returning the new value. **Raises** on backend failure.

        Atomicity across replicas is the entire reason the rate limiter uses the cache rather than
        process memory, so this is a real ``INCR`` -- never a get-modify-set, which would let two
        concurrent requests both read the same count and both be admitted.

        The TTL is applied only when the counter is created. Re-applying it on every increment
        would slide the expiry forward with each request, so a client sending continuous traffic
        would keep one bucket alive indefinitely and the sliding window would stop sliding.

        See the module docstring for why this raises while the other methods do not.
        """
        client = self._get_client()
        pipe = client.pipeline(transaction=True)
        pipe.incrby(key, int(amount))
        if ttl_s and ttl_s > 0:
            # NX: set the expiry only if the key has none, i.e. on the increment that created it.
            pipe.expire(key, ttl_s, nx=True)
        results = await pipe.execute()
        return int(results[0])

    # -- lifecycle --------------------------------------------------------------------

    async def close(self) -> None:
        """Release the connection pool. Safe to call when never connected."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:  # pragma: no cover - shutdown path
                self._record_error("close", exc)
            finally:
                self._client = None

    async def ping(self) -> bool:
        """For the readiness probe. False rather than raising -- unreachable is an answer."""
        try:
            return bool(await self._get_client().ping())
        except Exception as exc:
            self._record_error("ping", exc)
            return False
