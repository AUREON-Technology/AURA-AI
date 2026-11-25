"""``PostgresApprovalLedger`` against a real Postgres (migration 0038).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. ``tests/unit/test_approvals.py``
proves the rules on the in-memory ledger; this proves the SQL on a database built by
``alembic upgrade head``: owner scoping, a decision that can only be made once even when two
requests race for it, ``expired`` derived from the clock at the boundary, keyset paging over rows
created at the same instant, and the table's own CHECKs -- the last guard if the application's
were ever bypassed.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.approvals import InMemoryApprovalLedger
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.adapters.postgres_tool_approvals import PostgresApprovalLedger
from aura_core.domain.approvals import ApprovalStatus, ToolApproval
from aura_core.domain.identifiers import UserId
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
T0 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
ALICE, BOB = UserId("alice"), UserId("bob")


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "tool_approvals") as url:
        yield url


@pytest.fixture
async def ledger(database_url):
    # min_size == max_size: every connection is open before the test starts, so the race test is a
    # real race and not a queue on a cold pool.
    pool = PostgresPool(database_url, min_size=8, max_size=8)
    yield PostgresApprovalLedger(database_url, pool=pool), pool
    await pool.close()


def _row(id_: str, owner: UserId = ALICE, *, tool: str = "delete_record", created: datetime = T0, **kw) -> ToolApproval:
    return ToolApproval(
        id=id_, owner_id=owner, tool=tool, call_id=f"step_{id_}", digest="d" * 8,
        created_at=created, expires_at=created + HOUR, session_id="conv-1", turn_id="t-1", **kw,
    )


async def test_round_trip_keeps_every_field(ledger) -> None:
    pg, _ = ledger
    row = _row("apr.a.b")
    await pg.record_held([row])
    assert await pg.get(ALICE, "apr.a.b") == row

    await pg.decide(ALICE, ["apr.a.b"], ApprovalStatus.APPROVED, at=T0 + timedelta(minutes=2), via="chat")
    decided = await pg.get(ALICE, "apr.a.b")
    assert (decided.status, decided.decided_via, decided.decided_at) == (
        ApprovalStatus.APPROVED, "chat", T0 + timedelta(minutes=2),
    )
    assert decided.digest == row.digest and decided.session_id == "conv-1" and decided.turn_id == "t-1"


async def test_recording_a_held_call_twice_keeps_the_first(ledger) -> None:
    pg, _ = ledger
    await pg.record_held([_row("a")])
    await pg.record_held([_row("a", tool="other")])
    assert (await pg.get(ALICE, "a")).tool == "delete_record"
    await pg.record_held([])


async def test_every_statement_is_scoped_to_the_owner(ledger) -> None:
    pg, _ = ledger
    await pg.record_held([_row("a"), _row("b", BOB)])

    assert await pg.get(BOB, "a") is None
    assert [r.id for r in await pg.list(BOB, now=T0)] == ["b"]
    assert await pg.decide(BOB, ["a"], ApprovalStatus.REJECTED, at=T0, via="reject") == 0
    assert (await pg.get(ALICE, "a")).status is ApprovalStatus.PENDING, "Bob decided Alice's"
    assert await pg.delete_for_owner(BOB) == 1 and await pg.get(ALICE, "a") is not None
    assert await pg.delete_for_owner(ALICE) == 1 and await pg.get(ALICE, "a") is None


async def test_a_decision_is_made_once_even_when_requests_race_for_it(ledger) -> None:
    """Approve and reject arriving together: the UPDATE only touches a still-pending row, so exactly
    one of the sixteen wins and the row ends in that one's state, never overwritten."""
    pg, _ = ledger
    for round_ in range(5):
        approval_id = f"apr.race.{round_}"
        await pg.record_held([_row(approval_id)])
        statuses = [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.SUPERSEDED, ApprovalStatus.APPROVED]
        moved = await asyncio.gather(*[
            pg.decide(ALICE, [approval_id], statuses[i % 4], at=T0 + timedelta(seconds=i), via=f"racer-{i}")
            for i in range(16)
        ])
        assert sum(moved) == 1, (round_, moved)
        row = await pg.get(ALICE, approval_id)
        winner = moved.index(1)
        assert (row.status, row.decided_via) == (statuses[winner % 4], f"racer-{winner}")


async def test_expired_is_pending_past_its_hour_with_the_boundary_inclusive(ledger) -> None:
    pg, _ = ledger
    await pg.record_held([_row("a")])
    just_before, at_expiry = T0 + HOUR - timedelta(seconds=1), T0 + HOUR

    assert [r.id for r in await pg.list(ALICE, now=just_before, status=ApprovalStatus.PENDING)] == ["a"]
    assert await pg.list(ALICE, now=just_before, status=ApprovalStatus.EXPIRED) == []
    assert await pg.list(ALICE, now=at_expiry, status=ApprovalStatus.PENDING) == []
    assert [r.id for r in await pg.list(ALICE, now=at_expiry, status=ApprovalStatus.EXPIRED)] == ["a"]
    await pg.decide(ALICE, ["a"], ApprovalStatus.APPROVED, at=T0, via="chat")
    assert await pg.list(ALICE, now=T0 + 5 * HOUR, status=ApprovalStatus.EXPIRED) == [], "a decided row expired"


async def test_listing_orders_filters_and_pages_without_gaps_or_repeats(ledger) -> None:
    pg, _ = ledger
    rows = [_row(f"r{i}", tool="send_message" if i % 2 else "delete_record", created=T0 + timedelta(minutes=i)) for i in range(7)]
    rows += [_row("tie-a", created=T0), _row("tie-b", created=T0)]
    await pg.record_held(rows)
    now = T0 + timedelta(minutes=10)

    assert [r.id for r in await pg.list(ALICE, now=now, limit=3)] == ["r6", "r5", "r4"]
    assert [r.id for r in await pg.list(ALICE, now=now, tool="send_message")] == ["r5", "r3", "r1"]
    window = await pg.list(ALICE, now=now, since=T0 + timedelta(minutes=2), until=T0 + timedelta(minutes=5))
    assert [r.id for r in window] == ["r4", "r3", "r2"]
    seen, before = [], None
    while page := await pg.list(ALICE, now=now, limit=2, before=before):
        seen += [r.id for r in page]
        before = (page[-1].created_at, page[-1].id)
    # Rows created at the same instant page by id, newest instant first.
    assert seen == ["r6", "r5", "r4", "r3", "r2", "r1", "tie-b", "tie-a", "r0"]


async def test_the_tables_own_checks_are_the_last_guard(ledger) -> None:
    import asyncpg

    pg, pool = ledger
    raw = await pool._get_pool()

    async def insert(**columns):
        values = dict(id="x", owner_id="alice", tool="t", call_id="c", digest="d", session_id=None, turn_id=None,
                      status="pending", created_at=T0, expires_at=T0 + HOUR, decided_at=None, decided_via=None)
        values.update(columns)
        async with raw.acquire() as conn:
            await conn.execute(
                "INSERT INTO aura_tool_approvals (id, owner_id, tool, call_id, digest, session_id, turn_id, status, "
                "created_at, expires_at, decided_at, decided_via) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
                *[values[k] for k in ("id", "owner_id", "tool", "call_id", "digest", "session_id", "turn_id", "status",
                                      "created_at", "expires_at", "decided_at", "decided_via")],
            )

    await insert(id="fine")
    for index, bad in enumerate((
        {"status": "expired"},                                   # derived, never stored
        {"status": "maybe"},
        {"status": "approved"},                                  # decided with no decided_at/via
        {"status": "pending", "decided_at": T0, "decided_via": "chat"},   # pending but decided
        {"expires_at": T0},                                      # not after created_at
        {"owner_id": ""}, {"tool": ""}, {"digest": ""}, {"id": ""},
    )):
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(**{"id": f"bad-{index}", **bad} if "id" not in bad else bad)


async def test_the_two_ledgers_agree_on_the_rules(database_url) -> None:
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    real, memory = PostgresApprovalLedger(database_url, pool=pool), InMemoryApprovalLedger()
    try:
        async def drive(ledger) -> list:
            out = []
            await ledger.record_held([_row("a"), _row("b", created=T0 + timedelta(minutes=1)), _row("c", BOB), _row("old", created=T0 - 3 * HOUR)])
            out.append(await ledger.decide(ALICE, ["a", "c", "old"], ApprovalStatus.APPROVED, at=T0, via="chat"))
            out.append(await ledger.decide(ALICE, ["a", "b"], ApprovalStatus.REJECTED, at=T0, via="reject"))
            now = T0 + timedelta(minutes=5)
            for status in (None, *ApprovalStatus):
                out.append([(r.id, r.status.value) for r in await ledger.list(ALICE, now=now, status=status)])
            out.append(await ledger.get(BOB, "a"))
            out += [await ledger.delete_for_owner(ALICE), await ledger.delete_for_owner(ALICE)]
            return out

        assert await drive(real) == await drive(memory)
    finally:
        await pool.close()
