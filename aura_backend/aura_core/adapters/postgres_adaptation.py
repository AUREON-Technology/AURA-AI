"""Postgres stores for Track AP: connector records and sealed connector credentials (AP2).

Unlike most older ``aura_*`` adapters these do **not** create their tables at runtime: Alembic is
the single schema authority (master roadmap principle 9, CB-20), and migration ``0009`` owns both
tables. A missing table surfaces as ``DependencyUnavailableError`` naming the fix, rather than
being papered over with DDL from the request path.

``aura_connector_credentials`` only ever holds ciphertext (``EncryptedCredentialVault`` seals
before it calls ``put``). ``aura_connectors`` holds no secret at all -- the descriptor carries a
vault reference -- so it can be read, backed up and audited like any configuration table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence

from ..domain.connectors import (
    ConnectorDescriptor,
    ConnectorKind,
    ConnectorRecord,
    ConnectorState,
    IdentityMode,
)
from ..domain.errors import DependencyUnavailableError
from .postgres_store import PostgresPool

__all__ = ["PostgresConnectorStore", "PostgresCiphertextStore", "ADAPTATION_SCHEMA"]

#: Kept textually in step with ``alembic/versions/0009_adaptation_connectors.py``
#: (``tests/unit/test_adaptation_connectors.py`` asserts it).
ADAPTATION_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_connectors (
        id              TEXT        PRIMARY KEY,
        project_id      TEXT        NOT NULL DEFAULT 'p0',
        kind            TEXT        NOT NULL,
        identity_mode   TEXT        NOT NULL,
        display_name    TEXT        NOT NULL,
        endpoint        TEXT        NOT NULL,
        options         JSONB       NOT NULL DEFAULT '{}'::jsonb,
        secret_ref      TEXT        NULL,
        max_concurrency INTEGER     NOT NULL,
        timeout_s       DOUBLE PRECISION NOT NULL,
        row_cap         INTEGER     NOT NULL,
        state           TEXT        NOT NULL,
        approval_id     TEXT        NULL,
        registered_by   TEXT        NOT NULL,
        registered_at   TIMESTAMPTZ NOT NULL,
        activated_by    TEXT        NULL,
        activated_at    TIMESTAMPTZ NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_connector_credentials (
        secret_ref  TEXT        PRIMARY KEY,
        project_id  TEXT        NOT NULL DEFAULT 'p0',
        ciphertext  TEXT        NOT NULL CHECK (ciphertext LIKE 'aura-enc:v1:%'),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)

_MISSING_TABLE = "42P01"  # undefined_table


def _unavailable(action: str, exc: Exception) -> DependencyUnavailableError:
    if getattr(exc, "sqlstate", None) == _MISSING_TABLE:
        message = f"could not {action}: Track AP tables are missing -- run `alembic upgrade head`"
    else:
        # Never the vendor message: it can echo the statement and its parameters.
        message = f"could not {action}"
    return DependencyUnavailableError(
        message, dependency="postgres", details={"error": type(exc).__name__}
    )


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


_COLUMNS = (
    "id, kind, identity_mode, display_name, endpoint, options, secret_ref, max_concurrency, "
    "timeout_s, row_cap, state, approval_id, registered_by, registered_at, activated_by, activated_at"
)


class PostgresConnectorStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def put(self, record: ConnectorRecord) -> None:
        d = record.descriptor
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    f"""
                    INSERT INTO aura_connectors ({_COLUMNS})
                    VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                    ON CONFLICT (id) DO UPDATE SET
                        kind = EXCLUDED.kind, identity_mode = EXCLUDED.identity_mode,
                        display_name = EXCLUDED.display_name, endpoint = EXCLUDED.endpoint,
                        options = EXCLUDED.options, secret_ref = EXCLUDED.secret_ref,
                        max_concurrency = EXCLUDED.max_concurrency, timeout_s = EXCLUDED.timeout_s,
                        row_cap = EXCLUDED.row_cap, state = EXCLUDED.state,
                        approval_id = EXCLUDED.approval_id, registered_by = EXCLUDED.registered_by,
                        registered_at = EXCLUDED.registered_at, activated_by = EXCLUDED.activated_by,
                        activated_at = EXCLUDED.activated_at
                    """,
                    d.id, str(d.kind), str(d.identity_mode), d.display_name, d.endpoint,
                    json.dumps(dict(d.options)), d.secret_ref, d.max_concurrency, d.timeout_s,
                    d.row_cap, str(record.state), record.approval_id, record.registered_by,
                    record.registered_at, record.activated_by, record.activated_at,
                )
        except Exception as exc:
            raise _unavailable("write the connector", exc) from exc

    async def get(self, connector_id: str) -> ConnectorRecord | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM aura_connectors WHERE id = $1", connector_id)
        except Exception as exc:
            raise _unavailable("read the connector", exc) from exc
        return _record(row) if row else None

    async def list(self, *, state: ConnectorState | None = None) -> Sequence[ConnectorRecord]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if state is None:
                    rows = await conn.fetch(f"SELECT {_COLUMNS} FROM aura_connectors ORDER BY id")
                else:
                    rows = await conn.fetch(
                        f"SELECT {_COLUMNS} FROM aura_connectors WHERE state = $1 ORDER BY id", str(state)
                    )
        except Exception as exc:
            raise _unavailable("list connectors", exc) from exc
        return [_record(row) for row in rows]

    async def delete(self, connector_id: str) -> bool:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute("DELETE FROM aura_connectors WHERE id = $1", connector_id)
        except Exception as exc:
            raise _unavailable("delete the connector", exc) from exc
        return status.endswith(" 1")


def _record(row: Any) -> ConnectorRecord:
    options = row["options"]
    if isinstance(options, str):
        options = json.loads(options)
    return ConnectorRecord(
        descriptor=ConnectorDescriptor(
            id=row["id"],
            kind=ConnectorKind(row["kind"]),
            identity_mode=IdentityMode(row["identity_mode"]),
            display_name=row["display_name"],
            endpoint=row["endpoint"],
            options=options or {},
            secret_ref=row["secret_ref"],
            max_concurrency=int(row["max_concurrency"]),
            timeout_s=float(row["timeout_s"]),
            row_cap=int(row["row_cap"]),
        ),
        state=ConnectorState(row["state"]),
        registered_by=row["registered_by"],
        registered_at=_utc(row["registered_at"]),
        approval_id=row["approval_id"],
        activated_by=row["activated_by"],
        activated_at=_utc(row["activated_at"]),
    )


class PostgresCiphertextStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def put(self, secret_ref: str, ciphertext: str) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_connector_credentials (secret_ref, ciphertext) VALUES ($1, $2)
                    ON CONFLICT (secret_ref) DO UPDATE SET ciphertext = EXCLUDED.ciphertext
                    """,
                    secret_ref, ciphertext,
                )
        except Exception as exc:
            raise _unavailable("store the credential", exc) from exc

    async def get(self, secret_ref: str) -> str | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                return await conn.fetchval(
                    "SELECT ciphertext FROM aura_connector_credentials WHERE secret_ref = $1", secret_ref
                )
        except Exception as exc:
            raise _unavailable("read the credential", exc) from exc

    async def delete(self, secret_ref: str) -> bool:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    "DELETE FROM aura_connector_credentials WHERE secret_ref = $1", secret_ref
                )
        except Exception as exc:
            raise _unavailable("delete the credential", exc) from exc
        return status.endswith(" 1")
