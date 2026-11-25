"""Model-metric history under concurrent writers (audit F: "concurrent updates can lose observations").

The first design kept a series as one JSON blob under get-modify-set. Two writers read the same blob, each
added its observation, the later ``set`` erased the earlier one, and both took the same ``seq``. These tests
run many writers at once through separate tool instances over one cache (the stand-in for several gateway
workers and a scheduled job) and require every observation to survive with a unique sequence number. The
same suite runs against a real Redis when ``AURA_TEST_REDIS_URL`` names one:

    redis-server --port 56379 --save "" &
    AURA_TEST_REDIS_URL=redis://localhost:56379/0 pytest tests/unit/test_drift_concurrency.py
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from aura_core.adapters.redis_cache import RedisCache
from aura_core.domain.identifiers import UserId
from aura_core.llm.cache import InMemoryCache
from aura_core.ports.runtime import ToolContext
from aura_core.tools.drift import build_drift_tools

REDIS_URL = os.environ.get("AURA_TEST_REDIS_URL", "")
CTX = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)


@pytest.fixture(params=["memory", "redis"])
async def make_cache(request):
    if request.param == "memory":
        shared = InMemoryCache(max_entries=100_000)
        yield lambda: shared
        return
    if not REDIS_URL:
        pytest.skip("AURA_TEST_REDIS_URL not set (opt-in: needs a throwaway Redis)")
    import redis.asyncio as aioredis

    flush = aioredis.from_url(REDIS_URL)
    await flush.flushdb()
    made: list[RedisCache] = []

    def new() -> RedisCache:  # a separate connection per "worker"
        cache = RedisCache(REDIS_URL)
        made.append(cache)
        return cache

    yield new
    await flush.flushdb()
    await flush.aclose()


def tools_for(cache):
    by = {t.spec.name: t for t in build_drift_tools(cache)}
    return by["record_model_metric"], by["compare_model_metric"]


async def record(tool, **args) -> dict:
    return json.loads(await tool.execute({"operation": "record", "model": "m", "metric": "acc", **args}, CTX))


async def history(compare_tool, metric="acc") -> list[dict]:
    out = json.loads(await compare_tool.execute({"model": "m", "metric": metric, "include_history": True}, CTX))
    return out.get("history", [])


async def test_concurrent_writers_never_lose_an_observation(make_cache) -> None:
    workers = [tools_for(make_cache()) for _ in range(4)]  # four "workers", each with its own tool instance
    results = await asyncio.gather(*(
        record(workers[i % 4][0], value=i / 100) for i in range(40)))
    assert len({r["seq"] for r in results}) == 40, "every observation took its own sequence number"
    stored = await history(workers[0][1])
    assert sorted(e["value"] for e in stored) == sorted(i / 100 for i in range(40)), "nothing was erased"
    assert len({e["seq"] for e in stored}) == 40


async def test_the_history_stays_capped_and_keeps_the_newest(make_cache) -> None:
    record_tool, compare_tool = tools_for(make_cache())
    for i in range(70):
        await record(record_tool, value=float(i))
    stored = await history(compare_tool)
    assert len(stored) == 50 and stored[0]["value"] == 20.0 and stored[-1]["value"] == 69.0
    assert [e["seq"] for e in stored] == sorted(e["seq"] for e in stored)


async def test_concurrent_first_writers_do_not_lose_a_series(make_cache) -> None:
    workers = [tools_for(make_cache()) for _ in range(4)]
    await asyncio.gather(*(record(workers[i % 4][0], value=1.0) | {} if False else workers[i % 4][0].execute(
        {"operation": "record", "model": "m", "metric": f"metric{i}", "value": 1.0}, CTX) for i in range(12)))
    for i in range(12):
        assert len(await history(workers[0][1], f"metric{i}")) == 1, f"series metric{i} was lost"


async def test_set_baseline_clear_and_kind_checks_still_work(make_cache) -> None:
    record_tool, compare_tool = tools_for(make_cache())
    await record(record_tool, value=0.9)
    pinned = json.loads(await record_tool.execute({"operation": "set_baseline", "model": "m", "metric": "acc"}, CTX))
    assert pinned["baseline_set"] and pinned["baseline"]["value"] == 0.9
    await record(record_tool, value=0.5)
    verdict = json.loads(await compare_tool.execute({"model": "m", "metric": "acc"}, CTX))
    assert verdict["baseline_source"] == "pinned" and verdict["drift_detected"] is True
    with pytest.raises(Exception, match="single scores"):
        await record(record_tool, values=[1.0, 2.0])
    cleared = json.loads(await record_tool.execute({"operation": "clear", "model": "m", "metric": "acc"}, CTX))
    assert cleared["cleared"] and cleared["entries_removed"] == 2
    assert await history(compare_tool) == []


async def test_a_history_written_by_the_old_version_is_still_read_and_its_sequence_continues() -> None:
    import hashlib

    cache = InMemoryCache()
    owner = hashlib.sha256(b"u").hexdigest()[:24]
    old = {"baseline": {"t": 1.0, "seq": 2, "value": 0.9}, "seq": 2,
           "entries": [{"t": 1.0, "seq": 1, "value": 0.8}, {"t": 2.0, "seq": 2, "value": 0.9}]}
    await cache.set(f"aura:drift:v1:{owner}:m:acc", json.dumps(old).encode())
    record_tool, compare_tool = tools_for(cache)
    fresh = await record(record_tool, value=0.7)
    assert fresh["seq"] == 3, "no sequence number is reused across the two storage layouts"
    stored = await history(compare_tool)
    assert [e["value"] for e in stored] == [0.8, 0.9, 0.7]
    verdict = json.loads(await compare_tool.execute({"model": "m", "metric": "acc"}, CTX))
    assert verdict["baseline_source"] == "pinned" and verdict["baseline"]["value"] == 0.9


async def test_the_old_get_modify_set_design_really_did_lose_observations() -> None:
    """The defect, reproduced on the old algorithm over the same cache: this is what the fix removes."""
    cache = InMemoryCache()

    async def old_record(value: float) -> None:
        raw = await cache.get("series")
        data = json.loads(raw) if raw else {"entries": [], "seq": 0}
        await asyncio.sleep(0)  # the interleaving point: another writer runs here
        data["seq"] += 1
        data["entries"].append({"seq": data["seq"], "value": value})
        await cache.set("series", json.dumps(data).encode())

    await asyncio.gather(*(old_record(i) for i in range(10)))
    final = json.loads(await cache.get("series"))
    assert len(final["entries"]) < 10, "the old algorithm lost writes"


async def test_a_backend_failure_on_append_is_not_silent() -> None:
    class Down:
        async def get(self, key): return None
        async def get_list(self, key): return []
        async def set(self, *a, **k): ...
        async def delete(self, key): ...
        async def incr(self, key, **k): return 1

        async def append_capped(self, *a, **k):
            raise ConnectionError("redis is down")

    record_tool, _ = tools_for(Down())
    with pytest.raises(Exception, match="down|failed"):
        await record(record_tool, value=1.0)
