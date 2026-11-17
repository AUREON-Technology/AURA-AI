"""WP2 against a real Redis: one user's daily allowance is shared by every gateway worker.

Opt-in (``-m load``) and needs ``AURA_REDIS_URL``, e.g. a throwaway server::

    redis-server --port 6390 --save '' --daemonize yes
    AURA_REDIS_URL=redis://127.0.0.1:6390/0 pytest tests/load/test_user_quota_redis.py -m load

Two :class:`QuotaTracker` instances stand for two uvicorn workers. Without the shared counter
each would allow the full allowance, so a user would get ``workers x USER_TPD`` per day.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from aura_core.adapters.redis_cache import RedisCache
from aura_core.adapters.runtime import FrozenClock
from aura_core.llm import QuotaLimits, QuotaTracker

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_REDIS_URL", "")
CAP = 1_000


@pytest.fixture
def store():
    if not _URL:
        pytest.skip("AURA_REDIS_URL is not set")
    return RedisCache(_URL)


async def test_two_workers_share_one_user_allowance(store) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    wall = lambda: clock.now().timestamp()  # noqa: E731
    limits = {"claude1": QuotaLimits(user_tpd=CAP)}
    a = QuotaTracker(limits, wall=wall, store=store)
    b = QuotaTracker(limits, wall=wall, store=store)
    user = f"alice-{uuid.uuid4().hex}"  # a fresh key: the counter lives in the real server

    assert a.try_reserve("claude1", 600, user_key=user)[1] is None
    await a.flush()
    assert b.try_reserve("claude1", 300, user_key=user)[1] is None
    await b.flush()

    # Worker b now knows the shared total (900); a second 300 would exceed the allowance.
    assert b.refusal("claude1", 300, user_key=user) == "user_quota_tpd"
    # And the other user is untouched.
    assert b.refusal("claude1", 900, user_key=f"bob-{uuid.uuid4().hex}") is None
