"""Episodic session appends under concurrency (audit F-16).

``EpisodicMemory.append`` used to be load -> (maybe) summarise with the LLM -> set, with nothing
held. Two turns of one session each loaded the same state and the later ``set`` erased the
other's exchange; a summarisation, which can take a minute on the deployed model, held that
window open the whole time and then wrote back a state without anything appended meanwhile.

``_YieldingCache`` gives the in-memory cache the one property a networked cache (Redis) has and
the in-memory one lacks: every operation is a suspension point, so other coroutines can run
between a ``get`` and the ``set`` that follows it. Two :class:`EpisodicMemory` instances over one
cache stand in for two workers.
"""

from __future__ import annotations

import asyncio

from aura_core.domain.identifiers import SessionId, UserId
from aura_core.llm.cache import InMemoryCache
from aura_core.memory.session import EpisodicMemory

SESSION, OWNER = SessionId("s1"), UserId("alice")


class _YieldingCache(InMemoryCache):
    async def get(self, key):
        await asyncio.sleep(0)
        return await super().get(key)

    async def set(self, key, value, *, ttl_s=None):
        await asyncio.sleep(0)
        await super().set(key, value, ttl_s=ttl_s)

    async def incr(self, key, *, ttl_s=None):
        # Atomic like Redis INCR: no suspension between the read and the write (the base class's
        # incr would go through the yielding get/set above, so it is spelled out here).
        await asyncio.sleep(0)
        raw = await InMemoryCache.get(self, key)
        value = int(raw) + 1 if raw else 1
        await InMemoryCache.set(self, key, str(value).encode(), ttl_s=ttl_s)
        return value


def _contents(state) -> list[str]:
    return [m.content for m in state.messages]


class TestConcurrentAppends:
    async def test_concurrent_appends_from_two_workers_are_all_kept(self) -> None:
        cache = _YieldingCache()
        workers = [EpisodicMemory(cache, window=50, summarise_after=100) for _ in range(2)]

        await asyncio.gather(*(
            workers[i % 2].append(SESSION, OWNER, user_message=f"q{i}", assistant_message=f"a{i}")
            for i in range(10)
        ))

        state = await workers[0].load(SESSION, OWNER)
        assert state.turn_count == 10
        assert sorted(_contents(state)) == sorted(
            [f"q{i}" for i in range(10)] + [f"a{i}" for i in range(10)]
        )
        # Each exchange stays adjacent: the lease serialises whole appends, not single messages.
        contents = _contents(state)
        for i in range(0, len(contents), 2):
            assert contents[i][1:] == contents[i + 1][1:]

    async def test_a_cache_that_cannot_count_still_records_the_turn(self) -> None:
        class NoIncr(_YieldingCache):
            async def incr(self, key, *, ttl_s=None):
                raise RuntimeError("redis down")

        episodic = EpisodicMemory(NoIncr(), window=2, summarise_after=4)
        for i in range(3):
            state = await episodic.append(SESSION, OWNER, user_message=f"q{i}", assistant_message="a")
        assert state.turn_count == 3
        assert _contents(state)[-2:] == ["q2", "a"]
        assert "q0" in state.summary


class TestAppendsDuringASlowSummary:
    async def test_appends_made_while_the_summariser_runs_are_not_lost(self) -> None:
        cache = _YieldingCache()
        started, release = asyncio.Event(), asyncio.Event()
        transcripts: list[str] = []

        async def slow_summariser(transcript: str, previous: str) -> str:
            transcripts.append(transcript)
            started.set()
            await release.wait()
            return "SUMMARY"

        a = EpisodicMemory(cache, window=2, summarise_after=4, summariser=slow_summariser)
        b = EpisodicMemory(cache, window=2, summarise_after=4, summariser=slow_summariser)
        for i in range(2):
            await a.append(SESSION, OWNER, user_message=f"q{i}", assistant_message=f"a{i}")

        compacting = asyncio.create_task(
            a.append(SESSION, OWNER, user_message="q2", assistant_message="a2")
        )
        await asyncio.wait_for(started.wait(), 2)

        # The session is not locked for the duration of the model call: other turns -- on this
        # worker and another -- complete while the summary is still being written.
        await asyncio.wait_for(
            asyncio.gather(
                b.append(SESSION, OWNER, user_message="q3", assistant_message="a3"),
                a.append(SESSION, OWNER, user_message="q4", assistant_message="a4"),
            ),
            2,
        )
        release.set()
        await asyncio.wait_for(compacting, 2)

        state = await a.load(SESSION, OWNER)
        assert state.summary == "SUMMARY"
        assert _contents(state) == ["q2", "a2", "q3", "a3", "q4", "a4"]
        assert state.turn_count == 5
        # One summarisation, of exactly the prefix that was dropped.
        assert len(transcripts) == 1
        assert "q0" in transcripts[0] and "a1" in transcripts[0] and "q2" not in transcripts[0]

    async def test_a_summary_of_a_prefix_that_is_gone_is_discarded(self) -> None:
        """If the session is cleared while the model runs, installing the summary would
        resurrect the forgotten conversation."""
        cache = _YieldingCache()
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_summariser(transcript: str, previous: str) -> str:
            started.set()
            await release.wait()
            return "STALE SUMMARY"

        episodic = EpisodicMemory(cache, window=2, summarise_after=4, summariser=slow_summariser)
        for i in range(2):
            await episodic.append(SESSION, OWNER, user_message=f"q{i}", assistant_message="a")
        compacting = asyncio.create_task(
            episodic.append(SESSION, OWNER, user_message="q2", assistant_message="a")
        )
        await asyncio.wait_for(started.wait(), 2)
        await episodic.clear(SESSION, OWNER)
        release.set()
        await asyncio.wait_for(compacting, 2)

        state = await episodic.load(SESSION, OWNER)
        assert state.summary == ""
        assert state.messages == ()

    async def test_the_lease_is_released_after_every_append(self) -> None:
        cache = InMemoryCache()
        episodic = EpisodicMemory(cache, window=2, summarise_after=4)
        for i in range(6):
            await episodic.append(SESSION, OWNER, user_message=f"q{i}", assistant_message="a")
        key = episodic._key(SESSION, OWNER)
        assert await cache.get(f"{EpisodicMemory._LEASE_PREFIX}{key}") is None
        assert await cache.get(f"{EpisodicMemory._COMPACT_PREFIX}{key}") is None


async def test_a_contended_lease_waits_rather_than_overwriting() -> None:
    held_for = 0.05
    cache = InMemoryCache()
    episodic = EpisodicMemory(cache, window=50, summarise_after=100)
    lease = f"{EpisodicMemory._LEASE_PREFIX}{episodic._key(SESSION, OWNER)}"
    assert await cache.incr(lease, ttl_s=15) == 1  # another worker holds it

    async def release_later() -> None:
        await asyncio.sleep(held_for)
        await cache.delete(lease)

    append = asyncio.create_task(
        episodic.append(SESSION, OWNER, user_message="q", assistant_message="a")
    )
    await asyncio.sleep(held_for / 2)
    assert not append.done()
    await release_later()
    state = await asyncio.wait_for(append, 2)
    assert _contents(state) == ["q", "a"]
