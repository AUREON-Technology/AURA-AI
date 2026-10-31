"""``Container.shutdown()`` releases every wired network resource, once, in bounded time (F-25).

The in-memory container wires no Postgres pool, Redis client or Qdrant client, so those slots are
filled with stand-ins that record ``close()`` -- the external resource is the only thing faked;
the shutdown code under test is the production method. The real adapters' own ``close()``
methods are covered against their clients elsewhere; what these tests pin is that the container
actually calls them, keeps going when one fails or hangs, and does it only once.
"""

from __future__ import annotations

import asyncio
import time

from aura_core.adapters.postgres_store import PostgresPool
from aura_core.adapters.qdrant_store import QdrantVectorStore
from aura_core.adapters.redis_cache import RedisCache


class _Closable:
    def __init__(self, *, fail: bool = False, hang: bool = False) -> None:
        self.calls = 0
        self._fail = fail
        self._hang = hang

    async def close(self) -> None:
        self.calls += 1
        if self._hang:
            await asyncio.sleep(3600)
        if self._fail:
            raise RuntimeError("close failed")


class _AcloseOnly:
    """An httpx-style client: only ``aclose``. Exercises the fallback lookup."""

    def __init__(self) -> None:
        self.calls = 0

    async def aclose(self) -> None:
        self.calls += 1


def _wire(container, **overrides):
    for name, value in overrides.items():
        setattr(container, name, value)
    return overrides


async def test_shutdown_closes_postgres_redis_qdrant_and_clients(container) -> None:
    wired = _wire(
        container,
        _pg_pool=_Closable(),
        cache_backend=_Closable(),
        vectors=_Closable(),
        embedder=_Closable(),
        github_client=_Closable(),
        mcp_client=_AcloseOnly(),
    )
    container._github_clients_by_user["u1"] = per_user = _Closable()

    await container.shutdown()

    assert {name: r.calls for name, r in wired.items()} == {name: 1 for name in wired}
    assert per_user.calls == 1


async def test_shutdown_is_idempotent(container) -> None:
    pool, cache = _Closable(), _Closable()
    _wire(container, _pg_pool=pool, cache_backend=cache)

    await container.shutdown()
    await container.shutdown()

    assert (pool.calls, cache.calls) == (1, 1)


async def test_one_failure_or_hang_does_not_skip_the_rest(container) -> None:
    failing, hanging, pool = _Closable(fail=True), _Closable(hang=True), _Closable()
    # Order in shutdown(): embedder, ..., cache, vectors, postgres_pool -- the pool comes after
    # both bad ones.
    _wire(container, embedder=failing, cache_backend=hanging, _pg_pool=pool)

    started = time.monotonic()
    await container.shutdown(timeout_s=0.5, per_resource_timeout_s=0.2)
    elapsed = time.monotonic() - started

    assert failing.calls == 1 and hanging.calls == 1
    assert pool.calls == 1, "a failing and a hanging close must not skip the Postgres pool"
    assert elapsed < 2.0, f"shutdown was not bounded: {elapsed:.2f}s"


async def test_real_adapters_never_connected_close_cleanly(container) -> None:
    """The production adapters, constructed lazily and never used: close is a safe no-op."""
    _wire(
        container,
        _pg_pool=PostgresPool("postgresql://u:p@127.0.0.1:1/none"),
        cache_backend=RedisCache("redis://127.0.0.1:1/0"),
        vectors=QdrantVectorStore(url="http://127.0.0.1:1"),
    )
    await container.shutdown(timeout_s=2.0)
