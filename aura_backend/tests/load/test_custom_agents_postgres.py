"""``PostgresCustomAgentStore`` against a real Postgres (migration 0035).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. The in-memory store proves
the rules (``tests/unit/test_custom_agents.py``); this proves the SQL on a database built by
``alembic upgrade head``: the owner scoping of every statement, the unique index on
``(owner_id, lower(name))`` reported as the ``name`` conflict, the limit held *under concurrency*
(the advisory lock), the JSONB tool list round trip, and the table's own CHECKs, which are the last
guard if the application's were ever bypassed.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from aura_core.adapters.custom_agents import InMemoryCustomAgentStore
from aura_core.adapters.postgres_custom_agents import PostgresCustomAgentStore
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.domain.custom_agents import CustomAgent, CustomAgentConflict
from aura_core.domain.identifiers import UserId
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "custom_agents") as url:
        yield url


@pytest.fixture
async def store(database_url):
    # min_size == max_size: every connection is open before the test starts. A cold pool serialises
    # concurrent requests on opening connections, which hides exactly the race the limit test is for.
    pool = PostgresPool(database_url, min_size=8, max_size=8)
    yield PostgresCustomAgentStore(database_url, pool=pool), pool
    await pool.close()


def _agent(owner: str = "alice", **overrides) -> CustomAgent:
    base = dict(id=str(uuid.uuid4()), owner_id=UserId(owner), name="Planner", base_agent_id="task_planner",
                created_at=NOW, updated_at=NOW)
    base.update(overrides)
    return CustomAgent(**base)


async def test_round_trip_keeps_every_field_including_the_tool_list(store) -> None:
    pg, _ = store
    from datetime import timedelta

    narrowed = await pg.create(_agent(instruction="Plan by week.\nBe brief.", description="d",
                                      allowed_tools=("a_tool", "b_tool"), risk_ceiling="PUBLIC"), limit=5)
    plain = await pg.create(_agent(name="Plain", created_at=NOW + timedelta(minutes=1)), limit=5)

    fetched = await pg.get(UserId("alice"), narrowed.id)
    assert fetched == narrowed and fetched.allowed_tools == ("a_tool", "b_tool")
    assert (await pg.get(UserId("alice"), plain.id)).allowed_tools is None, "NULL means no narrowing, not an empty list"
    empty = await pg.create(_agent(name="Nothing", allowed_tools=(), created_at=NOW + timedelta(minutes=2)), limit=5)
    assert (await pg.get(UserId("alice"), empty.id)).allowed_tools == (), "an empty list is not NULL"
    assert [a.name for a in await pg.list_for_owner(UserId("alice"))] == ["Planner", "Plain", "Nothing"]


async def test_every_statement_is_scoped_to_the_owner(store) -> None:
    pg, _ = store
    mine = await pg.create(_agent("alice"), limit=5)

    assert await pg.get(UserId("bob"), mine.id) is None
    assert await pg.list_for_owner(UserId("bob")) == []
    assert await pg.delete(UserId("bob"), mine.id) is False
    assert await pg.update(_agent("bob", id=mine.id, name="stolen")) is None
    assert await pg.get(UserId("alice"), "not-a-uuid") is None and await pg.delete(UserId("alice"), "not-a-uuid") is False
    survivor = await pg.get(UserId("alice"), mine.id)
    assert survivor == mine, "Bob's attempts changed Alice's agent"

    other = await pg.create(_agent("bob", name="Planner"), limit=5)
    assert other.id != mine.id, "another person may use the same name"
    assert await pg.delete_for_owner(UserId("alice")) == 1
    assert await pg.get(UserId("bob"), other.id) == other


async def test_a_name_is_unique_per_owner_ignoring_case_on_create_and_on_update(store) -> None:
    pg, _ = store
    first = await pg.create(_agent(name="Planner"), limit=5)
    second = await pg.create(_agent(name="Writer"), limit=5)
    with pytest.raises(CustomAgentConflict) as taken:
        await pg.create(_agent(name="PLANNER"), limit=5)
    assert taken.value.reason == "name"
    with pytest.raises(CustomAgentConflict) as renamed:
        await pg.update(replace(second, name="planner"))
    assert renamed.value.reason == "name"
    kept = await pg.update(replace(first, name="PLANNER", instruction="x"))
    assert kept.name == "PLANNER" and kept.instruction == "x", "an agent may change the case of its own name"
    assert (await pg.get(UserId("alice"), second.id)).name == "Writer", "the failed rename changed something"


async def test_the_limit_holds_when_requests_race_for_the_last_slot(store) -> None:
    """Without the per-owner advisory lock every racer reads "one slot left" and inserts: with the
    lock removed this exceeds the limit in every round (checked by hand), so several rounds on a
    warm pool are what make a pass mean something."""
    pg, _ = store
    limit = 5
    for round_ in range(5):
        owner = f"racer-owner-{round_}"
        for i in range(limit - 1):
            await pg.create(_agent(owner, name=f"Agent {i}"), limit=limit)

        results = await asyncio.gather(
            *[pg.create(_agent(owner, name=f"Racer {i}"), limit=limit) for i in range(8)], return_exceptions=True,
        )

        won = [r for r in results if isinstance(r, CustomAgent)]
        lost = [r for r in results if isinstance(r, CustomAgentConflict)]
        assert len(won) == 1 and len(lost) == 7 and all(r.reason == "limit" for r in lost), (round_, results)
        assert len(await pg.list_for_owner(UserId(owner))) == limit, f"round {round_}: the race exceeded the limit"
    # The lock is per owner: a full owner does not hold another person back.
    assert (await pg.create(_agent("bob", name="Agent 0"), limit=limit)).owner_id == UserId("bob")


async def test_the_tables_own_checks_are_the_last_guard(store) -> None:
    """The API refuses these first; if a write ever bypassed it, the database still does."""
    import asyncpg

    pg, pool = store
    raw = await pool._get_pool()

    async def insert(**columns):
        values = dict(id=uuid.uuid4(), owner_id="alice", name="n", description="", base_agent_id="task_planner",
                      instruction="", allowed_tools=None, risk_ceiling=None, created_at=NOW, updated_at=NOW)
        values.update(columns)
        async with raw.acquire() as conn:
            await conn.execute(
                "INSERT INTO aura_custom_agents (id, owner_id, name, description, base_agent_id, instruction, "
                "allowed_tools, risk_ceiling, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10)",
                values["id"], values["owner_id"], values["name"], values["description"], values["base_agent_id"],
                values["instruction"], values["allowed_tools"], values["risk_ceiling"], values["created_at"], values["updated_at"],
            )

    await insert(name="fine")
    for bad in ({"base_agent_id": "engineer"}, {"name": ""}, {"name": "x" * 61}, {"description": "x" * 281},
                {"instruction": "x" * 4001}, {"risk_ceiling": "ROOT"}, {"owner_id": ""}):
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(**bad)


async def test_the_two_stores_agree_on_the_rules(database_url) -> None:
    """The same sequence of operations gives the same outcomes on the real store and the in-memory
    one the rest of the suite runs on, so a unit test against the latter says something true."""
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    real, memory = PostgresCustomAgentStore(database_url, pool=pool), InMemoryCustomAgentStore()
    try:
        async def drive(store) -> list:
            out = []
            a = await store.create(_agent("alice", id="00000000-0000-4000-8000-000000000001", name="One"), limit=2)
            b = await store.create(_agent("alice", id="00000000-0000-4000-8000-000000000002", name="Two",
                                          created_at=datetime(2026, 11, 22, 13, tzinfo=timezone.utc)), limit=2)
            out.append([x.name for x in await store.list_for_owner(UserId("alice"))])
            for attempt in (_agent("alice", name="three"), _agent("alice", name="ONE")):
                try:
                    await store.create(attempt, limit=2)
                except CustomAgentConflict as exc:
                    out.append(exc.reason)
            try:
                await store.update(replace(b, name="one"))
            except CustomAgentConflict as exc:
                out.append(("update", exc.reason))
            out += [await store.delete(UserId("alice"), a.id), await store.delete(UserId("alice"), a.id),
                    await store.delete_for_owner(UserId("alice"))]
            return out

        assert await drive(real) == await drive(memory)
    finally:
        await pool.close()
