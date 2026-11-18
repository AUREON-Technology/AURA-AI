"""F38 (stream B2): a workspace's billing lock is waited for only so long on Postgres.

A held advisory lock used to make every later caller (a cancel, a notification, the cycle) wait
indefinitely on a pooled connection. Now the wait is bounded by ``lock_timeout`` and ends in a
retryable 503, and the lock itself still serialises and is released.
"""

from __future__ import annotations

import os
import time

import pytest

from aura_core.domain.errors import DependencyUnavailableError

pytestmark = pytest.mark.load

_PG_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


async def test_waiting_for_a_held_workspace_lock_times_out() -> None:
    if not _PG_URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    from aura_core.adapters.postgres_billing import PostgresBillingLocks
    from aura_core.adapters.postgres_store import PostgresPool

    pool = PostgresPool(_PG_URL, min_size=1, max_size=4)
    try:
        locks = PostgresBillingLocks(_PG_URL, pool=pool, lock_timeout_s=0.3)
        held = await locks.lock_workspace("b2-lock-timeout")
        started = time.monotonic()
        with pytest.raises(DependencyUnavailableError):
            await locks.lock_workspace("b2-lock-timeout")
        assert time.monotonic() - started < 5
        other = await locks.lock_workspace("b2-lock-timeout-other")   # other workspaces unaffected
        await other.release()
        await held.release()
        again = await locks.lock_workspace("b2-lock-timeout")         # released, and no timeout left set
        raw = await pool._get_pool()
        async with raw.acquire() as conn:
            assert await conn.fetchval("SHOW lock_timeout") == "0"
        await again.release()
    finally:
        await pool.close()
