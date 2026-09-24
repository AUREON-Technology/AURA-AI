"""The ``IdempotencyStore`` contract (roadmap P1.4), run against every adapter.

The in-memory adapter always runs. The Postgres adapter runs only when
``AURA_TEST_POSTGRES_URL`` names a real, **throwaway** database (the table is truncated before
each test) -- the default suite needs no infrastructure, but the adapter's claim is three SQL
statements whose atomicity only a real server can demonstrate:

    initdb -D /tmp/pg && pg_ctl -D /tmp/pg -o "-p 55434 -k /tmp/pg" start
    createdb -h /tmp/pg -p 55434 aura_test
    AURA_TEST_POSTGRES_URL=postgresql://$USER@localhost:55434/aura_test \\
        pytest tests/unit/test_idempotency_store.py
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.idempotency import InMemoryIdempotencyStore
from aura_core.domain.identifiers import UserId
from aura_core.domain.idempotency import ClaimOutcome, IdempotencyStatus, StoredResponse
from aura_core.ports.idempotency import IdempotencyStore

PG_URL = os.environ.get("AURA_TEST_POSTGRES_URL", "")

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(hours=24)
LEASE = timedelta(minutes=15)
ALICE = UserId("alice")
BOB = UserId("bob")
OP = "POST /api/v3/memory"
OK = StoredResponse(status_code=200, body=b'{"id":"m1"}', headers={"content-type": "application/json"})


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield InMemoryIdempotencyStore()
        return
    if not PG_URL:
        pytest.skip("AURA_TEST_POSTGRES_URL not set (opt-in: needs a throwaway Postgres)")
    from aura_core.adapters.postgres_idempotency import PostgresIdempotencyStore
    from aura_core.adapters.postgres_store import PostgresPool

    pool = PostgresPool(PG_URL)
    adapter = PostgresIdempotencyStore(PG_URL, pool=pool)
    await adapter._ensure_schema()
    raw = await pool._get_pool()
    async with raw.acquire() as conn:
        await conn.execute("TRUNCATE aura_idempotency_keys")
    try:
        yield adapter
    finally:
        await pool.close()


async def _claim(store, *, user=ALICE, op=OP, key="k1", request_hash="h1", now=T0):
    return await store.claim(
        user_id=user, operation=op, key=key, request_hash=request_hash,
        now=now, ttl=TTL, lease=LEASE,
    )


async def _complete(store, claim, *, response=OK, now=T0, user=ALICE, op=OP, key="k1"):
    return await store.complete(
        user_id=user, operation=op, key=key, claim_token=claim.claim_token,
        response=response, now=now,
    )


def test_both_adapters_satisfy_the_port() -> None:
    assert isinstance(InMemoryIdempotencyStore(), IdempotencyStore)
    from aura_core.adapters.postgres_idempotency import PostgresIdempotencyStore

    assert isinstance(PostgresIdempotencyStore("postgresql://u@h/db"), IdempotencyStore)


class TestClaimRules:
    async def test_a_new_key_is_claimed_in_progress(self, store) -> None:
        result = await _claim(store)
        assert result.outcome is ClaimOutcome.CLAIMED
        assert result.record.status is IdempotencyStatus.IN_PROGRESS
        assert result.record.expires_at == T0 + TTL
        assert result.claim_token

    async def test_a_completed_key_replays_the_stored_response(self, store) -> None:
        first = await _claim(store)
        assert await _complete(store, first) is True

        again = await _claim(store, now=T0 + timedelta(minutes=5))
        assert again.outcome is ClaimOutcome.REPLAY
        assert again.record.status is IdempotencyStatus.COMPLETED
        assert again.record.response == OK

    async def test_a_binary_body_round_trips_byte_for_byte(self, store) -> None:
        body = bytes(range(256)) * 4
        first = await _claim(store)
        await _complete(store, first, response=StoredResponse(201, body, {"content-type": "audio/wav"}))
        replay = await _claim(store)
        assert replay.record.response.body == body
        assert replay.record.response.status_code == 201

    async def test_a_running_key_reports_in_progress(self, store) -> None:
        await _claim(store)
        again = await _claim(store, now=T0 + timedelta(seconds=3))
        assert again.outcome is ClaimOutcome.IN_PROGRESS

    async def test_the_same_key_with_a_different_request_is_a_mismatch(self, store) -> None:
        first = await _claim(store)
        assert (await _claim(store, request_hash="other")).outcome is ClaimOutcome.MISMATCH
        await _complete(store, first)
        assert (await _claim(store, request_hash="other")).outcome is ClaimOutcome.MISMATCH

    async def test_a_failed_claim_can_be_retried_by_the_same_request(self, store) -> None:
        first = await _claim(store)
        assert await store.fail(
            user_id=ALICE, operation=OP, key="k1", claim_token=first.claim_token, now=T0,
        ) is True
        retry = await _claim(store, now=T0 + timedelta(seconds=1))
        assert retry.outcome is ClaimOutcome.CLAIMED
        assert retry.claim_token != first.claim_token
        # ...but not by a different request: the key still belongs to the original payload.
        assert (await _claim(store, request_hash="other")).outcome is ClaimOutcome.MISMATCH

    async def test_a_stale_claim_is_taken_over_after_its_lease(self, store) -> None:
        first = await _claim(store)
        assert (await _claim(store, now=T0 + LEASE - timedelta(seconds=1))).outcome is (
            ClaimOutcome.IN_PROGRESS
        )
        takeover = await _claim(store, now=T0 + LEASE)
        assert takeover.outcome is ClaimOutcome.CLAIMED
        # The original holder's late completion must not clobber the new claim.
        assert await _complete(store, first) is False
        assert await store.fail(
            user_id=ALICE, operation=OP, key="k1", claim_token=first.claim_token, now=T0,
        ) is False
        assert await _complete(store, takeover) is True

    async def test_an_expired_key_is_a_brand_new_key(self, store) -> None:
        first = await _claim(store)
        await _complete(store, first)
        later = T0 + TTL + timedelta(seconds=1)
        fresh = await _claim(store, request_hash="different-payload", now=later)
        assert fresh.outcome is ClaimOutcome.CLAIMED
        assert fresh.record.expires_at == later + TTL
        assert fresh.record.response is None

    async def test_complete_is_only_accepted_once(self, store) -> None:
        first = await _claim(store)
        assert await _complete(store, first) is True
        assert await _complete(store, first) is False


class TestScoping:
    async def test_two_users_with_the_same_key_do_not_collide(self, store) -> None:
        alice = await _claim(store, user=ALICE)
        await _complete(store, alice)
        bob = await _claim(store, user=BOB, request_hash="bobs-request")
        assert bob.outcome is ClaimOutcome.CLAIMED  # not a mismatch, not alice's replay
        assert bob.record.response is None

    async def test_the_same_key_on_another_operation_is_independent(self, store) -> None:
        first = await _claim(store)
        await _complete(store, first)
        other = await _claim(store, op="DELETE /api/v3/memory/{memory_id}", request_hash="h2")
        assert other.outcome is ClaimOutcome.CLAIMED

    async def test_one_user_cannot_complete_anothers_claim(self, store) -> None:
        alice = await _claim(store, user=ALICE)
        assert await store.complete(
            user_id=BOB, operation=OP, key="k1", claim_token=alice.claim_token,
            response=OK, now=T0,
        ) is False
        assert (await _claim(store, user=ALICE)).outcome is ClaimOutcome.IN_PROGRESS


class TestConcurrency:
    async def test_concurrent_claims_of_one_key_produce_exactly_one_winner(self, store) -> None:
        results = await asyncio.gather(*(_claim(store) for _ in range(20)))
        outcomes = [r.outcome for r in results]
        assert outcomes.count(ClaimOutcome.CLAIMED) == 1
        assert outcomes.count(ClaimOutcome.IN_PROGRESS) == 19

    async def test_concurrent_takeovers_of_a_failed_claim_produce_one_winner(self, store) -> None:
        first = await _claim(store)
        await store.fail(
            user_id=ALICE, operation=OP, key="k1", claim_token=first.claim_token, now=T0,
        )
        results = await asyncio.gather(*(
            _claim(store, now=T0 + timedelta(seconds=1)) for _ in range(10)
        ))
        assert [r.outcome for r in results].count(ClaimOutcome.CLAIMED) == 1


class TestExpiryCleanup:
    async def test_purge_removes_only_expired_records(self, store) -> None:
        old = await _claim(store, key="old")
        await _complete(store, old, key="old")
        await _claim(store, key="fresh", now=T0 + timedelta(hours=12))

        removed = await store.purge_expired(now=T0 + TTL + timedelta(minutes=1))

        assert removed == 1
        survivor = await _claim(store, key="fresh", now=T0 + timedelta(hours=12, minutes=1))
        assert survivor.outcome is ClaimOutcome.IN_PROGRESS
        # The purged key starts over rather than replaying.
        assert (await _claim(store, key="old", now=T0 + TTL + timedelta(minutes=2))).outcome is (
            ClaimOutcome.CLAIMED
        )
