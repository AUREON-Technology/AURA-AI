"""``PostgresControlStore`` against a real Postgres built by ``alembic upgrade head`` (ADR-037).

The in-memory store carries the default suite; this checks the SQL the production store runs:
upserts, the journal's JSONB detail and timestamps, one-time challenges under a race, and the
reconciler lease that must admit exactly one gateway process.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN, e.g.
``postgresql://aura:pw@localhost:5432/postgres``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.control_store import PostgresControlStore
from aura_core.domain.control import ChangeRecord, ChangeStatus, Platform, PlatformCredential, StepUpGrant
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "control") as url:
        yield PostgresControlStore(url)


def _change(n: int, *, actor: str = "root@aura.ai", status: ChangeStatus = ChangeStatus.WATCHING) -> ChangeRecord:
    at = NOW + timedelta(seconds=n)
    return ChangeRecord(
        change_id=f"chg_{n}", actor=actor, platform=Platform.RAILWAY, target="gateway",
        action="set_variables", status=status, created_at=at, updated_at=at,
        detail={"variables": [{"name": "AURA_KNOWLEDGE_TOP_K", "secret": False}]},
        deployment_id=f"dep-{n}", previous_deployment_id="dep-0", restore_ref="cred_" + "a" * 32,
    )


async def test_platform_credentials_upsert_and_delete(store) -> None:
    first = PlatformCredential(
        platform=Platform.HF_SPACE, secret_ref="cred_" + "1" * 32, configured_by="root@aura.ai",
        configured_at=NOW, verified_at=NOW, verified_detail="Space org/space", target="org/space",
    )
    await store.put_credential(first)
    await store.put_credential(PlatformCredential(
        platform=Platform.HF_SPACE, secret_ref="cred_" + "2" * 32, configured_by="root@aura.ai",
        configured_at=NOW + timedelta(minutes=1),
    ))
    got = await store.get_credential(Platform.HF_SPACE)
    assert got.secret_ref == "cred_" + "2" * 32 and got.verified_at is None and got.target == ""
    assert [c.platform for c in await store.list_credentials()] == [Platform.HF_SPACE]
    assert await store.delete_credential(Platform.HF_SPACE) is True
    assert await store.delete_credential(Platform.HF_SPACE) is False


async def test_the_journal_round_trips_and_tracks_open_changes(store) -> None:
    for n in (1, 2):
        await store.record_change(_change(n))
    await store.record_change(_change(3, actor="other@aura.ai", status=ChangeStatus.RECORDED))
    assert [c.change_id for c in await store.list_changes(limit=2)] == ["chg_3", "chg_2"]
    assert [c.change_id for c in await store.open_changes()] == ["chg_1", "chg_2"]
    assert await store.count_changes_since("root@aura.ai", NOW) == 2

    closed = await store.update_change(
        "chg_1", status=ChangeStatus.ROLLED_BACK, updated_at=NOW + timedelta(minutes=6),
        reason="unhealthy", clear_restore_ref=True,
    )
    assert closed.status is ChangeStatus.ROLLED_BACK and closed.restore_ref is None
    assert closed.detail["variables"][0]["name"] == "AURA_KNOWLEDGE_TOP_K"
    assert closed.updated_at == NOW + timedelta(minutes=6)
    assert [c.change_id for c in await store.open_changes()] == ["chg_2"]
    assert await store.update_change("chg_missing", status=ChangeStatus.FAILED, updated_at=NOW) is None


async def test_a_challenge_is_taken_once_even_by_racing_requests(store) -> None:
    await store.put_grant(StepUpGrant(
        digest="d" * 64, subject="root", email="root@aura.ai", created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    ))
    taken = await asyncio.gather(*(store.take_grant("d" * 64, now=NOW) for _ in range(5)))
    assert sum(1 for t in taken if t is not None) == 1


async def test_grants_expire_and_are_revoked_by_subject(store) -> None:
    for n, subject in enumerate(("root", "root", "other")):
        await store.put_grant(StepUpGrant(
            digest=str(n) * 64, subject=subject, email="", created_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        ))
    assert await store.find_grant("0" * 64, now=NOW) is not None
    assert await store.find_grant("0" * 64, now=NOW + timedelta(minutes=6)) is None
    assert await store.delete_grants("root") == 2
    assert await store.find_grant("2" * 64, now=NOW) is not None


async def test_the_lease_admits_one_holder_until_it_expires(store) -> None:
    results = await asyncio.gather(*(
        store.try_lease("reconciler", f"gw-{n}", now=NOW, ttl_s=60) for n in range(5)
    ))
    assert results.count(True) == 1
    winner = f"gw-{results.index(True)}"
    assert await store.try_lease("reconciler", winner, now=NOW + timedelta(seconds=30), ttl_s=60)
    assert not await store.try_lease("reconciler", "gw-late", now=NOW + timedelta(seconds=30), ttl_s=60)
    assert await store.try_lease("reconciler", "gw-late", now=NOW + timedelta(seconds=91), ttl_s=60)
