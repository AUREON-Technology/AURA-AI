"""``PostgresPool._get_pool()``: concurrent first callers must share one pool.

``CLAUDE.md`` names this method as "the canonical example" of lazy adapter initialization "behind
an ``asyncio.Lock`` when concurrent callers could race" -- but the method had no lock at all.
Without one, N concurrent first callers each see ``self._pool is None`` before any of them
finishes awaiting ``asyncpg.create_pool``, so each builds its own pool; only the last write to
``self._pool`` survives, and every other pool's connections leak, never closed by anything.

No real Postgres is available in this environment (or in the default ``pytest`` run, by
convention -- see ``tests/conftest.py``), so ``asyncpg.create_pool`` is monkeypatched with a fake
that behaves like the real thing under concurrency: it actually awaits, so a race has room to
happen, and it returns a distinct object per call, so "did every caller get the *same* pool" is a
real assertion rather than a tautology.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest

from aura_core.adapters.postgres_store import PostgresPool
from aura_core.domain.errors import DependencyUnavailableError


class _FakeConnection:
    async def execute(self, statement: str) -> None:
        return None


class _FakeAcquireContext:
    async def __aenter__(self) -> _FakeConnection:
        return _FakeConnection()

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakePool:
    def __init__(self, pool_id: int) -> None:
        self.id = pool_id
        self.closed = False

    def acquire(self) -> _FakeAcquireContext:
        return _FakeAcquireContext()

    async def close(self) -> None:
        self.closed = True


class _FakeCreatePool:
    """Stands in for ``asyncpg.create_pool``: counts calls, yields the race window, returns a
    distinct ``_FakePool`` each time so a bug that builds more than one is actually observable."""

    def __init__(self, *, delay_s: float = 0.01, fail_times: int = 0) -> None:
        self.calls = 0
        self._delay_s = delay_s
        self._fail_times = fail_times
        self.created: list[_FakePool] = []

    async def __call__(self, dsn: str, *, min_size: int, max_size: int) -> _FakePool:
        self.calls += 1
        call_index = self.calls
        # A real create_pool does real I/O; sleeping (rather than returning immediately) is what
        # gives concurrent callers a window to race in, the same way it would against a real DB.
        await asyncio.sleep(self._delay_s)
        if call_index <= self._fail_times:
            raise ConnectionError("simulated connection failure")
        pool = _FakePool(call_index)
        self.created.append(pool)
        return pool


@pytest.fixture(autouse=True)
def _patch_create_pool(monkeypatch: pytest.MonkeyPatch):
    """Yields the fake so a test can configure delay/failure, then patches it in."""
    fake = _FakeCreatePool()
    monkeypatch.setattr(asyncpg, "create_pool", fake)
    return fake


class TestPostgresPoolConcurrency:
    async def test_cold_start_creates_exactly_one_pool(self, _patch_create_pool) -> None:
        pool = PostgresPool("postgresql://user:pass@localhost/db")
        result = await pool._get_pool()
        assert _patch_create_pool.calls == 1
        assert result.id == 1

    async def test_concurrent_first_callers_share_one_pool(self, _patch_create_pool) -> None:
        pool = PostgresPool("postgresql://user:pass@localhost/db")

        results = await asyncio.gather(*(pool._get_pool() for _ in range(20)))

        assert _patch_create_pool.calls == 1, (
            f"expected exactly one asyncpg.create_pool call from 20 concurrent first callers, "
            f"got {_patch_create_pool.calls}"
        )
        assert len({id(r) for r in results}) == 1, "every caller must receive the same pool object"
        assert all(r.id == 1 for r in results)

    async def test_repeated_calls_after_warm_up_reuse_the_pool(self, _patch_create_pool) -> None:
        pool = PostgresPool("postgresql://user:pass@localhost/db")
        first = await pool._get_pool()
        for _ in range(5):
            again = await pool._get_pool()
            assert again is first
        assert _patch_create_pool.calls == 1

    async def test_failure_during_initialization_does_not_poison_the_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeCreatePool(fail_times=1)
        monkeypatch.setattr(asyncpg, "create_pool", fake)
        pool = PostgresPool("postgresql://user:pass@localhost/db")

        with pytest.raises(DependencyUnavailableError):
            await pool._get_pool()
        assert pool._pool is None, "a failed attempt must not leave a half-built pool behind"

    async def test_recovery_after_initialization_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeCreatePool(fail_times=1)
        monkeypatch.setattr(asyncpg, "create_pool", fake)
        pool = PostgresPool("postgresql://user:pass@localhost/db")

        with pytest.raises(DependencyUnavailableError):
            await pool._get_pool()

        result = await pool._get_pool()
        assert result is not None
        assert fake.calls == 2, "the retry after failure is the second real attempt"

    async def test_concurrent_callers_during_a_failing_attempt_all_see_the_same_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every caller queued behind the lock while the first attempt is failing should see
        that failure and be free to retry -- not hang, and not silently get a stale ``None``."""
        fake = _FakeCreatePool(delay_s=0.02, fail_times=20)
        monkeypatch.setattr(asyncpg, "create_pool", fake)
        pool = PostgresPool("postgresql://user:pass@localhost/db")

        results = await asyncio.gather(
            *(pool._get_pool() for _ in range(5)), return_exceptions=True
        )
        assert all(isinstance(r, DependencyUnavailableError) for r in results)
        assert pool._pool is None
