"""Migrations that must not destroy data the schema change cannot bring back (audit F34).

* ``0019``'s downgrade drops ``aura_user_data_keys``. With any key in it that makes every value
  sealed under a per-user data key unreadable for good, so the downgrade refuses while keys exist.
  On an empty table it still runs -- ``test_migration_roundtrip_postgres.py`` walks it.
* Erasing a user deleted their personal workspace, and ``aura_payments`` cascaded with it: the
  record of money taken went with the account. ``0030`` keeps the ledger row and drops only the
  reference (``ON DELETE SET NULL``), and its own downgrade refuses while such rows exist, since
  restoring ``NOT NULL`` would need them deleted.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set.
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.postgres_migrations import alembic, migrated_database

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


@pytest.fixture
async def head_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "guards") as url:
        yield url


async def _user(conn) -> str:
    subject = f"user_{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO users (id, auth_subject, email) VALUES ($1, $2, $3)",
        uuid.uuid4(), subject, f"{subject}@aura.test",
    )
    return subject


async def _personal_workspace(conn, subject: str) -> str:
    workspace_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO aura_workspaces (id, kind, name, created_by, personal_owner) "
        "VALUES ($1, 'personal', 'Personal', $2, $2)",
        workspace_id, subject,
    )
    return workspace_id


async def _payment(conn, workspace_id: str) -> str:
    payment_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO aura_payments (id, workspace_id, plan_id, seats, amount_piastres, method, purpose, status, "
        "provider_ref) VALUES ($1, $2, 'pro', 1, 49900, 'card', 'initial', 'paid', 'fawry-123')",
        payment_id, workspace_id,
    )
    return payment_id


async def _revision(conn) -> str:
    return await conn.fetchval("SELECT version_num FROM alembic_version")


# -- 0019: data keys ------------------------------------------------------------------------------


async def test_0019_downgrade_refuses_while_data_keys_exist(head_url) -> None:
    import asyncpg

    conn = await asyncpg.connect(head_url)
    try:
        subject = await _user(conn)
        await conn.execute(
            "INSERT INTO aura_user_data_keys (owner_id, kid, wrapped_key) VALUES ($1, 'deadbeef', 'wrapped')",
            subject,
        )
        head = await _revision(conn)

        result = alembic(head_url, "downgrade", "0018")
        assert result.returncode != 0
        assert "aura_user_data_keys" in result.stderr and "refusing" in result.stderr.lower()

        # Nothing was undone: one transaction for the whole run, rolled back.
        assert await _revision(conn) == head
        assert await conn.fetchval("SELECT count(*) FROM aura_user_data_keys") == 1
    finally:
        await conn.close()


async def test_0019_downgrade_runs_once_the_keys_are_gone(head_url) -> None:
    import asyncpg

    result = alembic(head_url, "downgrade", "0018")
    assert result.returncode == 0, result.stderr[-3000:]
    conn = await asyncpg.connect(head_url)
    try:
        assert await _revision(conn) == "0018"
        assert await conn.fetchval("SELECT to_regclass('aura_user_data_keys')") is None
    finally:
        await conn.close()


# -- 0030: the payment ledger survives erasure -----------------------------------------------------


async def test_erasing_a_user_keeps_their_payments_without_the_reference(head_url) -> None:
    import asyncpg

    conn = await asyncpg.connect(head_url)
    try:
        subject = await _user(conn)
        workspace_id = await _personal_workspace(conn, subject)
        payment_id = await _payment(conn, workspace_id)

        # What `erase_account` does last: delete the users row and let the schema cascade.
        await conn.execute("DELETE FROM users WHERE auth_subject = $1", subject)

        assert await conn.fetchval("SELECT count(*) FROM aura_workspaces WHERE id = $1", workspace_id) == 0
        row = await conn.fetchrow("SELECT workspace_id, amount_piastres, status, provider_ref FROM aura_payments "
                                  "WHERE id = $1", payment_id)
        assert row is not None, "the payment ledger row was deleted with the account"
        assert row["workspace_id"] is None
        assert (row["amount_piastres"], row["status"], row["provider_ref"]) == (49900, "paid", "fawry-123")
    finally:
        await conn.close()


async def test_a_payment_still_needs_an_existing_workspace_when_written(head_url) -> None:
    import asyncpg

    conn = await asyncpg.connect(head_url)
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _payment(conn, uuid.uuid4().hex)
    finally:
        await conn.close()


async def test_0030_downgrade_refuses_while_anonymised_payments_exist(head_url) -> None:
    import asyncpg

    conn = await asyncpg.connect(head_url)
    try:
        subject = await _user(conn)
        await _payment(conn, await _personal_workspace(conn, subject))
        await conn.execute("DELETE FROM users WHERE auth_subject = $1", subject)

        result = alembic(head_url, "downgrade", "0029")
        assert result.returncode != 0
        assert "aura_payments" in result.stderr and "refusing" in result.stderr.lower()
        assert await conn.fetchval("SELECT count(*) FROM aura_payments") == 1
    finally:
        await conn.close()


async def test_0030_downgrade_restores_the_cascade(head_url) -> None:
    import asyncpg

    result = alembic(head_url, "downgrade", "0029")
    assert result.returncode == 0, result.stderr[-3000:]
    conn = await asyncpg.connect(head_url)
    try:
        subject = await _user(conn)
        payment_id = await _payment(conn, await _personal_workspace(conn, subject))
        await conn.execute("DELETE FROM users WHERE auth_subject = $1", subject)
        assert await conn.fetchval("SELECT count(*) FROM aura_payments WHERE id = $1", payment_id) == 0
    finally:
        await conn.close()


async def test_the_billing_cycle_queries_skip_payments_whose_workspace_was_erased(head_url) -> None:
    """After 0030 a pending or received payment can outlive its workspace. The cycle's queries must
    not hand such a row to the service, which has no workspace to apply it to."""
    import asyncpg

    from aura_core.adapters.postgres_billing import PostgresPaymentStore

    conn = await asyncpg.connect(head_url)
    try:
        subject = await _user(conn)
        workspace_id = await _personal_workspace(conn, subject)
        ids = {}
        for status in ("pending", "received"):
            ids[status] = await _payment(conn, workspace_id)
            await conn.execute("UPDATE aura_payments SET status = $2 WHERE id = $1", ids[status], status)
        await conn.execute("DELETE FROM users WHERE auth_subject = $1", subject)
    finally:
        await conn.close()

    store = PostgresPaymentStore(head_url)
    assert ids["pending"] not in {p.id for p in await store.pending(before=None)}
    assert ids["received"] not in {p.id for p in await store.received()}
    orphan = await store.get(ids["pending"])
    assert orphan is not None and orphan.workspace_id is None
