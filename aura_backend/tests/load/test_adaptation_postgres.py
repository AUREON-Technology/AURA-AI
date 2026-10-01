"""AP2 against a real Postgres: migration 0009, the stores, the vault, and the live connector.

Opt-in like everything under ``tests/load``: the ``load`` marker keeps it out of the default run,
and it skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN **with password auth** (the
``auth_failed`` case needs a server that checks passwords)::

    initdb -D /tmp/pg/data -U aura --auth-local=trust --auth-host=scram-sha-256 --pwfile=<(echo pw)
    pg_ctl -D /tmp/pg/data -o "-p 55436 -k /tmp/pg -c listen_addresses=localhost" -l /tmp/pg/log start
    AURA_TEST_DATABASE_URL='postgresql://aura:pw@localhost:55436/postgres' \\
        pytest tests/load/test_adaptation_postgres.py -m load -q

What the unit tests (in-memory stores, a stub connected system) cannot show and this does: the
migration creates what the adapter queries; the CHECK constraint refuses plaintext at rest even
from a writer that bypasses the vault; JSONB and TIMESTAMPTZ round-trip into the domain types;
and -- the point of AP2's activation gate -- the privilege inspection tells a read-only role from
a role that can write, on a real server, including grants inherited through a group role.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adaptation.connectors import ConnectorRegistry
from aura_core.adapters.connectors.postgres import PostgresConnectorFactory
from aura_core.adapters.credential_vault import EncryptedCredentialVault
from aura_core.adapters.field_crypto import FieldCipher
from aura_core.adapters.postgres_adaptation import PostgresCiphertextStore, PostgresConnectorStore
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.domain.connectors import (
    ConnectorDescriptor,
    ConnectorKind,
    ConnectorState,
    Credential,
    HealthStatus,
    IdentityMode,
)
from aura_core.domain.errors import ConflictError, DependencyUnavailableError
from aura_core.learning.approval import ApprovalService, InMemoryApprovalStore

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
_BACKEND = Path(__file__).resolve().parents[2]


def _with_database(url: str, database: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{database}"))


def _without_userinfo(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit(parts._replace(netloc=netloc))


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    sqlalchemy_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    env = {**os.environ, "DATABASE_URL": sqlalchemy_url, "AURA_DATABASE_URL": sqlalchemy_url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=_BACKEND, env=env,
                          capture_output=True, text=True, timeout=300)


@pytest.fixture
async def database():
    """A fresh database migrated to head; dropped afterwards."""
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"ap2_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = _with_database(_URL, name)
    try:
        result = _alembic(url, "upgrade", "head")
        assert result.returncode == 0, result.stderr[-3000:]
        yield url
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def roles(database):
    """Create login roles in the target database; dropped (with their grants) afterwards."""
    import asyncpg

    created: list[str] = []
    conn = await asyncpg.connect(database)

    async def make(*, grants: tuple[str, ...]) -> tuple[str, str]:
        name = f"ap2_role_{uuid.uuid4().hex[:8]}"
        password = uuid.uuid4().hex
        await conn.execute(f"CREATE ROLE {name} LOGIN PASSWORD '{password}'")
        created.append(name)
        for grant in grants:
            await conn.execute(grant.format(role=name))
        return name, password

    await conn.execute("CREATE TABLE IF NOT EXISTS employee (id int primary key, full_name text)")
    await conn.execute("CREATE TABLE IF NOT EXISTS leave_request (id int primary key, status text)")
    try:
        yield make
    finally:
        for name in created:
            await conn.execute(f"DROP OWNED BY {name}")
            await conn.execute(f"DROP ROLE IF EXISTS {name}")
        await conn.close()


_READ_ONLY = (
    "GRANT CONNECT ON DATABASE {db} TO {{role}}",
    "GRANT USAGE ON SCHEMA public TO {{role}}",
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {{role}}",
)


def _read_only(database: str) -> tuple[str, ...]:
    db = urlsplit(database).path.lstrip("/")
    return tuple(g.format(db=db) for g in _READ_ONLY)


def _descriptor(database: str, user: str, **overrides) -> ConnectorDescriptor:
    fields = dict(
        id="hr-db",
        kind=ConnectorKind.POSTGRES,
        identity_mode=IdentityMode.SERVICE_ACCOUNT,
        display_name="Reference HR",
        endpoint=_without_userinfo(database),
        options={"user": user, "sslmode": "disable"},
        timeout_s=10.0,
    )
    fields.update(overrides)
    return ConnectorDescriptor(**fields)


async def _health(database: str, user: str, password: str, **overrides):
    connector = await PostgresConnectorFactory().open(_descriptor(database, user, **overrides), Credential(password))
    return await connector.health()


class TestMigrationAndStores:
    async def test_tables_exist_and_downgrade_removes_them(self, database) -> None:
        import asyncpg

        conn = await asyncpg.connect(database)
        try:
            tables = {r["table_name"] for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'aura_connector%'")}
            assert tables == {"aura_connectors", "aura_connector_credentials"}
        finally:
            await conn.close()
        down = _alembic(database, "downgrade", "0008")
        assert down.returncode == 0, down.stderr[-3000:]
        conn = await asyncpg.connect(database)
        try:
            assert await conn.fetchval("SELECT to_regclass('aura_connectors')") is None
        finally:
            await conn.close()

    async def test_plaintext_cannot_rest_in_the_credentials_table(self, database) -> None:
        import asyncpg

        conn = await asyncpg.connect(database)
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO aura_connector_credentials (secret_ref, ciphertext) VALUES ($1, $2)",
                    "cred_" + "a" * 32, "hunter2",
                )
        finally:
            await conn.close()

    async def test_stores_and_vault_round_trip(self, database) -> None:
        from cryptography.fernet import Fernet

        pool = PostgresPool(database, min_size=1, max_size=2)
        try:
            vault = EncryptedCredentialVault(
                cipher=FieldCipher([Fernet.generate_key().decode()]), store=PostgresCiphertextStore(database, pool=pool)
            )
            ref = await vault.put(Credential("pw-123"))
            assert (await vault.get(ref)).reveal() == "pw-123"

            store = PostgresConnectorStore(database, pool=pool)
            from datetime import datetime, timezone

            from aura_core.domain.connectors import ConnectorRecord

            record = ConnectorRecord(
                descriptor=_descriptor(database, "reader", secret_ref=ref, options={"user": "reader", "sslmode": "require", "statement_timeout_ms": 5000}),
                state=ConnectorState.PENDING_APPROVAL,
                registered_by="sa@example.com",
                registered_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
                approval_id="apr-1",
            )
            await store.put(record)
            assert await store.get("hr-db") == record
            assert [r.id for r in await store.list(state=ConnectorState.PENDING_APPROVAL)] == ["hr-db"]
            assert await store.list(state=ConnectorState.ACTIVE) == []
            assert await store.delete("hr-db") and await store.get("hr-db") is None
            assert await vault.delete(ref) and not await vault.delete(ref)
        finally:
            await pool._pool.close()

    async def test_a_missing_table_names_the_fix(self, database) -> None:
        _alembic(database, "downgrade", "0008")
        pool = PostgresPool(database, min_size=1, max_size=1)
        try:
            with pytest.raises(DependencyUnavailableError, match="alembic upgrade head"):
                await PostgresConnectorStore(database, pool=pool).get("hr-db")
        finally:
            await pool._pool.close()


class TestLiveConnector:
    async def test_a_read_only_role_is_healthy_and_cannot_write(self, database, roles) -> None:
        user, password = await roles(grants=_read_only(database))
        health = await _health(database, user, password)
        assert health.status is HealthStatus.HEALTHY, health
        assert health.write_capable is False and health.findings == ()

    async def test_a_table_write_grant_is_found(self, database, roles) -> None:
        user, password = await roles(grants=_read_only(database) + ("GRANT INSERT ON leave_request TO {role}",))
        health = await _health(database, user, password)
        assert health.write_capable is True and "table_write" in health.findings

    async def test_a_write_grant_inherited_through_a_group_role_is_found(self, database, roles) -> None:
        group, _ = await roles(grants=("GRANT UPDATE ON employee TO {role}",))
        user, password = await roles(grants=_read_only(database) + (f"GRANT {group} TO {{role}}",))
        health = await _health(database, user, password)
        assert "table_write" in health.findings

    async def test_schema_create_is_found(self, database, roles) -> None:
        user, password = await roles(grants=_read_only(database) + ("GRANT CREATE ON SCHEMA public TO {role}",))
        assert "schema_create" in (await _health(database, user, password)).findings

    async def test_the_superuser_is_found(self, database) -> None:
        parts = urlsplit(database)
        health = await _health(database, parts.username, parts.password)
        assert health.write_capable is True and "superuser" in health.findings

    async def test_wrong_password_and_unknown_database(self, database, roles) -> None:
        user, _ = await roles(grants=_read_only(database))
        assert (await _health(database, user, "not-the-password")).reason == "auth_failed"
        missing = _with_database(database, "no_such_db")
        assert (await _health(missing, user, "x")).reason in {"unknown_database", "auth_failed"}

    async def test_unreachable(self, database) -> None:
        health = await _health(database, "reader", "x", endpoint="postgresql://localhost:1/hr", timeout_s=2.0)
        assert health.reason == "unreachable"


class TestRegistryEndToEnd:
    async def test_register_approve_activate_with_a_read_only_role(self, database, roles, clock, audit) -> None:
        from cryptography.fernet import Fernet

        user, password = await roles(grants=_read_only(database))
        writer, writer_password = await roles(grants=_read_only(database) + ("GRANT DELETE ON employee TO {role}",))
        pool = PostgresPool(database, min_size=1, max_size=2)
        approvals = ApprovalService(store=InMemoryApprovalStore(), audit=audit)
        registry = ConnectorRegistry(
            store=PostgresConnectorStore(database, pool=pool),
            vault=EncryptedCredentialVault(
                cipher=FieldCipher([Fernet.generate_key().decode()]),
                store=PostgresCiphertextStore(database, pool=pool),
            ),
            approvals=approvals,
            factories={ConnectorKind.POSTGRES: PostgresConnectorFactory()},
            clock=clock,
            audit=audit,
        )
        try:
            _, approval = await registry.register(_descriptor(database, user), secret=Credential(password), requested_by="sa@example.com")
            await approvals.decide(approval.id, approved=True, by="reviewer@example.com")
            active = await registry.activate("hr-db", approval_id=approval.id, by="sa@example.com")
            assert active.state is ConnectorState.ACTIVE

            _, writer_approval = await registry.register(
                _descriptor(database, writer, id="hr-db-writer"), secret=Credential(writer_password), requested_by="sa@example.com"
            )
            await approvals.decide(writer_approval.id, approved=True, by="reviewer@example.com")
            with pytest.raises(ConflictError, match="read-only"):
                await registry.activate("hr-db-writer", approval_id=writer_approval.id, by="sa@example.com")
            assert (await registry.get("hr-db-writer")).state is ConnectorState.PENDING_APPROVAL
        finally:
            await pool._pool.close()
