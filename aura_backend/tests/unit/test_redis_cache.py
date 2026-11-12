"""RedisCache: the two failure policies, and why they differ.

The interesting behaviour here is not the happy path -- it is what happens when Redis is down.
Reads and writes must degrade silently, because their fallback is a recomputation. ``incr`` must
raise, because its fallback is unmetered traffic and the decision of what to do about that belongs
to the rate limiter's ``fail_open`` flag, not to this adapter.

Driven by a fake client rather than a live Redis: these are tests of the adapter's policy, and
they should run in the same "no infrastructure" suite as everything else.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.redis_cache import RedisCache
from aura_core.domain.errors import ConfigurationError


class FakeRedis:
    """Enough of redis.asyncio to exercise the adapter.

    Records calls so tests can assert on the wire behaviour -- that ``incr`` really issues INCR
    rather than a get-modify-set, and that the TTL is applied with NX.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, bytes] = {}
        self.expiry: dict[str, int] = {}
        self.fail = fail
        self.calls: list[tuple] = []

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis is down")

    async def get(self, key):
        self.calls.append(("get", key))
        self._check()
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.calls.append(("set", key, ex))
        self._check()
        self.store[key] = value
        if ex:
            self.expiry[key] = ex

    async def delete(self, *keys):
        self.calls.append(("delete", *keys))
        self._check()
        for key in keys:
            self.store.pop(key, None)

    async def mget(self, keys):
        self.calls.append(("mget", tuple(keys)))
        self._check()
        return [self.store.get(k) for k in keys]

    async def ping(self):
        self.calls.append(("ping",))
        self._check()
        return True

    async def aclose(self):
        self.calls.append(("aclose",))

    def pipeline(self, transaction=True):
        return FakePipeline(self, transaction=transaction)


class FakePipeline:
    def __init__(self, client: FakeRedis, *, transaction: bool) -> None:
        self.client = client
        self.transaction = transaction
        self.queued: list[tuple] = []

    def incr(self, key):
        self.queued.append(("incr", key, 1))
        return self

    def incrby(self, key, amount):
        # The adapter issues INCRBY (one command, atomic) so it can add a token count as well as
        # a request; INCR is the amount-1 case of the same op.
        self.queued.append(("incr", key, int(amount)))
        return self

    def expire(self, key, ttl, nx=False):
        self.queued.append(("expire", key, ttl, nx))
        return self

    async def execute(self):
        self.client._check()
        results = []
        for op in self.queued:
            if op[0] == "incr":
                key, amount = op[1], op[2]
                current = int(self.client.store.get(key, b"0")) + amount
                self.client.store[key] = str(current).encode()
                results.append(current)
            elif op[0] == "expire":
                _, key, ttl, nx = op
                if not nx or key not in self.client.expiry:
                    self.client.expiry[key] = ttl
                results.append(True)
        self.client.calls.append(("pipeline", tuple(self.queued)))
        return results


def _cache(*, fail: bool = False) -> tuple[RedisCache, FakeRedis]:
    cache = RedisCache("redis://localhost:6379/0")
    fake = FakeRedis(fail=fail)
    cache._client = fake
    return cache, fake


class TestConfiguration:
    def test_an_empty_url_is_refused_at_construction(self) -> None:
        """Better than discovering it on the first cache miss in production."""
        with pytest.raises(ConfigurationError) as excinfo:
            RedisCache("")
        assert "AURA_REDIS_URL" in str(excinfo.value)

    def test_constructing_the_adapter_opens_no_connection(self) -> None:
        """The container is built during settings validation and in every test that builds one, so
        construction must not require a reachable Redis."""
        cache = RedisCache("redis://unreachable:6379/0")
        assert cache._client is None


class TestReadsAndWrites:
    async def test_a_value_round_trips(self) -> None:
        cache, _ = _cache()
        await cache.set("k", b"v")
        assert await cache.get("k") == b"v"

    async def test_a_missing_key_is_a_miss(self) -> None:
        cache, _ = _cache()
        assert await cache.get("absent") is None

    async def test_the_default_ttl_is_applied_when_none_is_given(self) -> None:
        cache, fake = _cache()
        await cache.set("k", b"v")
        assert fake.expiry["k"] == 3600

    async def test_an_explicit_ttl_wins(self) -> None:
        cache, fake = _cache()
        await cache.set("k", b"v", ttl_s=60)
        assert fake.expiry["k"] == 60

    async def test_a_zero_ttl_means_no_expiry(self) -> None:
        """Matching InMemoryCache, which stores expires_at=0.0 for this case."""
        cache, fake = _cache()
        await cache.set("k", b"v", ttl_s=0)
        assert "k" not in fake.expiry

    async def test_values_are_not_decoded(self) -> None:
        """The port's contract is bytes. Decoding would corrupt the JSON and pickled payloads
        callers store."""
        cache, _ = _cache()
        payload = b"\x80\x04\x95not-utf8\xff"
        await cache.set("k", payload)
        assert await cache.get("k") == payload

    async def test_delete_removes_a_key(self) -> None:
        cache, _ = _cache()
        await cache.set("k", b"v")
        await cache.delete("k")
        assert await cache.get("k") is None


class TestGetMany:
    async def test_it_uses_one_round_trip(self) -> None:
        cache, fake = _cache()
        await cache.set("a", b"1")
        await cache.set("b", b"2")
        await cache.get_many(["a", "b"])
        assert ("mget", ("a", "b")) in fake.calls

    async def test_absent_keys_are_omitted_not_mapped_to_none(self) -> None:
        """Callers distinguish hit from miss by absence, so a None value would make `key in
        result` true for a miss."""
        cache, _ = _cache()
        await cache.set("a", b"1")
        result = await cache.get_many(["a", "missing"])
        assert result == {"a": b"1"}
        assert "missing" not in result

    async def test_an_empty_key_list_does_not_call_redis(self) -> None:
        cache, fake = _cache()
        assert await cache.get_many([]) == {}
        assert fake.calls == []


class TestBestEffortDegradation:
    """A Redis outage must cost latency, never correctness."""

    async def test_get_reports_a_miss(self) -> None:
        cache, _ = _cache(fail=True)
        assert await cache.get("k") is None

    async def test_set_is_swallowed(self) -> None:
        cache, _ = _cache(fail=True)
        await cache.set("k", b"v")  # must not raise

    async def test_delete_is_swallowed(self) -> None:
        cache, _ = _cache(fail=True)
        await cache.delete("k")  # must not raise

    async def test_get_many_returns_nothing(self) -> None:
        cache, _ = _cache(fail=True)
        assert await cache.get_many(["a", "b"]) == {}

    async def test_ping_answers_false_rather_than_raising(self) -> None:
        """Unreachable is an answer the readiness probe can act on."""
        cache, _ = _cache(fail=True)
        assert await cache.ping() is False


class TestIncrMustNotFailSilently:
    """The one operation that raises, and the reason it has to.

    SlidingWindowRateLimiter wraps its cache calls and consults `fail_open` to choose between
    shedding load and admitting it. If this adapter swallowed the error and returned a plausible
    count, that `except` would never fire and `fail_open=False` would silently behave as True --
    a deployment that had explicitly chosen to shed load under a Redis outage would serve
    unlimited requests believing it was protected.
    """

    async def test_it_returns_the_incremented_value(self) -> None:
        cache, _ = _cache()
        assert await cache.incr("c") == 1
        assert await cache.incr("c") == 2

    async def test_it_raises_when_the_backend_is_down(self) -> None:
        cache, _ = _cache(fail=True)
        with pytest.raises(Exception):
            await cache.incr("c")

    async def test_it_uses_a_real_incr_not_a_get_modify_set(self) -> None:
        """Get-modify-set would let two concurrent requests read the same count and both be
        admitted, which defeats the only reason the limiter shares state at all."""
        cache, fake = _cache()
        await cache.incr("c", ttl_s=60)
        pipelines = [c for c in fake.calls if c[0] == "pipeline"]
        assert pipelines, "expected a pipelined INCR"
        ops = [op[0] for op in pipelines[0][1]]
        assert "incr" in ops
        assert not any(c[0] == "get" for c in fake.calls)

    async def test_the_ttl_is_set_with_nx_so_the_window_still_slides(self) -> None:
        """Re-applying the TTL on every increment would slide the expiry forward with each
        request, so continuous traffic would keep one bucket alive forever and the sliding window
        would stop sliding."""
        cache, fake = _cache()
        await cache.incr("c", ttl_s=60)
        pipeline_ops = [c for c in fake.calls if c[0] == "pipeline"][0][1]
        expire_ops = [op for op in pipeline_ops if op[0] == "expire"]
        assert expire_ops == [("expire", "c", 60, True)]

    async def test_a_second_increment_does_not_extend_the_expiry(self) -> None:
        cache, fake = _cache()
        await cache.incr("c", ttl_s=60)
        fake.expiry["c"] = 5  # simulate time passing
        await cache.incr("c", ttl_s=60)
        assert fake.expiry["c"] == 5

    async def test_no_ttl_means_no_expire_call(self) -> None:
        cache, fake = _cache()
        await cache.incr("c")
        pipeline_ops = [c for c in fake.calls if c[0] == "pipeline"][0][1]
        assert not [op for op in pipeline_ops if op[0] == "expire"]


class TestRateLimiterIntegration:
    """The policy above, verified through the component that depends on it."""

    async def _limiter(self, cache, *, fail_open: bool):
        from aura_core.security.rate_limit import RateLimitPolicy, SlidingWindowRateLimiter

        return SlidingWindowRateLimiter(
            cache,
            policies=[RateLimitPolicy(scope="chat", limit=5, window_s=60)],
            fail_open=fail_open,
        )

    async def test_a_redis_outage_can_shed_load_when_configured_to(self) -> None:
        """This is the assertion that a silently-degrading incr would break: the adapter's raise
        is what lets fail_open=False mean anything."""
        cache, _ = _cache(fail=True)
        limiter = await self._limiter(cache, fail_open=False)
        decision = await limiter.check(key="u1", scope="chat")
        assert decision.allowed is False

    async def test_a_redis_outage_admits_traffic_when_configured_to(self) -> None:
        cache, _ = _cache(fail=True)
        limiter = await self._limiter(cache, fail_open=True)
        decision = await limiter.check(key="u1", scope="chat")
        assert decision.allowed is True

    async def test_counts_accumulate_across_calls_when_redis_works(self) -> None:
        cache, _ = _cache()
        limiter = await self._limiter(cache, fail_open=False)
        for _ in range(5):
            assert (await limiter.check(key="u1", scope="chat")).allowed is True
        assert (await limiter.check(key="u1", scope="chat")).allowed is False


class TestLifecycle:
    async def test_close_releases_the_client(self) -> None:
        cache, fake = _cache()
        await cache.close()
        assert ("aclose",) in fake.calls
        assert cache._client is None

    async def test_close_is_safe_when_never_connected(self) -> None:
        cache = RedisCache("redis://localhost:6379/0")
        await cache.close()  # must not raise
