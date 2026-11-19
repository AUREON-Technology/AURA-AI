"""WP1 against a real Postgres: migration 0023 and ``PostgresEngineerConsentStore``.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN. The database
is built by ``alembic upgrade head`` -- the adapter creates nothing.

What the in-memory store cannot show and this does: two gateway workers (two pools) see each
other's accept and withdraw through the table; re-accepting upserts rather than colliding on the
primary key and clears the withdrawal; a second withdrawal keeps the first time (COALESCE); and
``timestamptz`` round-trips as an aware datetime, which ``is_current`` and the API rely on.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from aura_core.adapters.postgres_engineer_consent import PostgresEngineerConsentStore
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION
from aura_core.domain.identifiers import UserId
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
ALICE = UserId("alice")
T0 = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
async def stores():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "engineer_consent") as url:
        pool_a = PostgresPool(url, min_size=1, max_size=2)
        pool_b = PostgresPool(url, min_size=1, max_size=2)
        try:
            yield (PostgresEngineerConsentStore(url, pool=pool_a),
                   PostgresEngineerConsentStore(url, pool=pool_b))
        finally:
            await pool_a.close()
            await pool_b.close()


async def test_accept_and_withdraw_cross_workers(stores) -> None:
    a, b = stores
    assert await b.get(ALICE) is None

    accepted = await a.accept(ALICE, ENGINEER_CONSENT_NOTICE_VERSION, at=T0)
    assert accepted.is_current() and accepted.accepted_at == T0
    seen = await b.get(ALICE)
    assert seen is not None and seen.is_current()
    assert seen.accepted_at.tzinfo is not None

    withdrawn = await b.withdraw(ALICE, at=T1)
    assert withdrawn.withdrawn_at == T1 and not withdrawn.is_current()
    assert not (await a.get(ALICE)).is_current()

    # A second withdrawal keeps when it first happened.
    assert (await a.withdraw(ALICE, at=T2)).withdrawn_at == T1


async def test_re_accepting_upserts_and_clears_the_withdrawal(stores) -> None:
    a, b = stores
    await a.accept(ALICE, "2025-01-01", at=T0)
    await a.withdraw(ALICE, at=T1)
    again = await b.accept(ALICE, ENGINEER_CONSENT_NOTICE_VERSION, at=T2)
    assert again.is_current() and again.withdrawn_at is None and again.accepted_at == T2


async def test_withdraw_without_an_answer_and_erasure(stores) -> None:
    a, _ = stores
    assert await a.withdraw(UserId("nobody"), at=T0) is None
    await a.accept(ALICE, ENGINEER_CONSENT_NOTICE_VERSION, at=T0)
    assert await a.delete_for_owner(ALICE) == 1
    assert await a.get(ALICE) is None
    assert await a.delete_for_owner(ALICE) == 0
