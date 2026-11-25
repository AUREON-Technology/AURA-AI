"""Agent runs as tasks, against a real Postgres (migration 0037).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. The in-memory store proves
the rules (``tests/unit/test_agent_tasks.py``); this proves the SQL on a database built by
``alembic upgrade head``: the keyset paging over ``(created_at, id)`` with timestamp ties, the
``since`` / ``until`` / ``retry_of`` filters, ``after_seq`` on steps, the ``retry_of`` column being
written once and never moved by a later upsert, and that every read stays inside its owner. The two
stores are also driven through the same sequence and must agree.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.memory_stores import InMemoryEngineerRunStore
from aura_core.adapters.postgres_engineer_runs import PostgresAgentRunStore
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.domain.agent_runs import AgentRun, AgentRunStatus, AgentStep
from aura_core.domain.identifiers import AgentRunId, AgentStepId, UserId
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
ALICE, BOB = UserId("alice"), UserId("bob")


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "agent_tasks") as url:
        yield url


@pytest.fixture
async def store(database_url):
    pool = PostgresPool(database_url, min_size=1, max_size=4)
    yield PostgresAgentRunStore(database_url, pool=pool)
    await pool.close()


def _run(owner: UserId = ALICE, *, minutes: int = 0, status: str = AgentRunStatus.FAILED, agent: str = "orchestrator",
         retry_of: str | None = None, run_id: str | None = None) -> AgentRun:
    at = NOW + timedelta(minutes=minutes)
    return AgentRun(id=AgentRunId(run_id or str(uuid.uuid4())), user_id=owner, agent_id=agent, turn_id="t",
                    correlation_id="c", status=status, created_at=at, updated_at=at, retry_of=retry_of,
                    finished_at=at if status in AgentRunStatus.TERMINAL else None)


def _step(run: AgentRun, seq: int) -> AgentStep:
    return AgentStep(id=AgentStepId(str(uuid.uuid4())), run_id=run.id, user_id=run.user_id, seq=seq, iteration=1,
                     phase="execute", kind="tool_call", created_at=NOW, tool="calculator", status="ok")


async def _page_all(store, user: UserId, **filters) -> list[AgentRun]:
    seen, before = [], None
    for _ in range(50):
        page = list(await store.list_runs(user, limit=3, before=before, **filters))
        seen += page
        if len(page) < 3:
            break
        before = (page[-1].created_at, page[-1].id.value)
    return seen


async def test_filters_order_and_keyset_paging_with_equal_timestamps(store) -> None:
    runs = [_run(minutes=i // 2, status="failed" if i % 2 else "cancelled",
                 agent="task_planner" if i % 3 == 0 else "orchestrator") for i in range(11)]
    for r in runs:
        await store.save_run(r)
    await store.save_run(_run(BOB, minutes=1))

    whole = list(await store.list_runs(ALICE, limit=100))
    assert [(r.created_at, r.id.value) for r in whole] == sorted(((r.created_at, r.id.value) for r in runs), reverse=True)
    assert all(r.user_id == ALICE for r in whole), "another owner's run listed"
    for filters in ({}, {"status": "failed"}, {"agent_id": "task_planner"},
                    {"since": NOW + timedelta(minutes=2)}, {"until": NOW + timedelta(minutes=3)},
                    {"since": NOW + timedelta(minutes=1), "until": NOW + timedelta(minutes=4), "status": "cancelled"}):
        paged = await _page_all(store, ALICE, **filters)
        full = list(await store.list_runs(ALICE, limit=100, **filters))
        assert [r.id for r in paged] == [r.id for r in full], f"paging disagrees with the unpaged list for {filters}"
        assert len({r.id for r in paged}) == len(paged), f"a run repeated for {filters}"


async def test_retry_of_is_written_once_listed_and_never_moved(store) -> None:
    first = _run(minutes=0)
    retry = _run(minutes=5, retry_of=first.id.value)
    await store.save_run(first)
    await store.save_run(retry)

    assert [r.id for r in await store.list_runs(ALICE, retry_of=first.id.value)] == [retry.id]
    assert (await store.get_run(retry.id, user_id=ALICE)).retry_of == first.id.value
    assert (await store.get_run(first.id, user_id=ALICE)).retry_of is None

    # The loop saves the run again as it progresses; a later save must not rewrite the link.
    await store.save_run(retry.with_(status=AgentRunStatus.COMPLETED, retry_of="something-else"))
    stored = await store.get_run(retry.id, user_id=ALICE)
    assert stored.status == AgentRunStatus.COMPLETED and stored.retry_of == first.id.value


async def test_a_cancelled_run_round_trips_and_another_owner_cannot_take_its_id(store) -> None:
    run = _run(status=AgentRunStatus.CANCELLED).with_(stop_reason="run_cancelled")
    await store.save_run(run)
    stored = await store.get_run(run.id, user_id=ALICE)
    assert stored.status == "cancelled" and stored.stop_reason == "run_cancelled" and stored.is_terminal
    assert await store.get_run(run.id, user_id=BOB) is None
    with pytest.raises(ValueError):
        await store.save_run(run.with_(user_id=BOB))
    assert (await store.get_run(run.id, user_id=ALICE)).user_id == ALICE, "an upsert from another owner moved the run"


async def test_steps_after_a_seq_and_owner_scoping(store) -> None:
    run = _run(status=AgentRunStatus.RUNNING)
    await store.save_run(run)
    for seq in range(6):
        await store.append_step(_step(run, seq))
    assert [s.seq for s in await store.list_steps(run.id, user_id=ALICE)] == [0, 1, 2, 3, 4, 5]
    assert [s.seq for s in await store.list_steps(run.id, user_id=ALICE, after_seq=3)] == [4, 5]
    assert [s.seq for s in await store.list_steps(run.id, user_id=ALICE, after_seq=1, limit=2)] == [2, 3]
    assert await store.list_steps(run.id, user_id=ALICE, after_seq=5) == []
    assert await store.list_steps(run.id, user_id=BOB) == [], "another owner's steps listed"


async def test_the_two_stores_agree_on_the_same_data(database_url) -> None:
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    real, memory = PostgresAgentRunStore(database_url, pool=pool), InMemoryEngineerRunStore()
    try:
        first = _run(minutes=0, run_id="00000000-0000-4000-8000-00000000000a")
        data = [first] + [_run(minutes=i // 2, run_id=f"00000000-0000-4000-8000-0000000001{i:02d}",
                               retry_of=first.id.value if i % 4 == 0 else None,
                               status="failed" if i % 2 else "cancelled") for i in range(1, 9)]
        for r in data:
            await real.save_run(r)
            await memory.save_run(r)

        async def view(store):
            out = []
            for filters in ({}, {"status": "failed"}, {"retry_of": first.id.value},
                            {"since": NOW + timedelta(minutes=1), "until": NOW + timedelta(minutes=3)}):
                out.append([r.id.value for r in await _page_all(store, ALICE, **filters)])
            return out

        assert await view(real) == await view(memory)
    finally:
        await pool.close()
