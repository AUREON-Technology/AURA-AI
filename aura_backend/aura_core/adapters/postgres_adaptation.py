"""Postgres stores for Track AP: connector records and sealed credentials (AP2), twin versions (AP4),
blueprint versions (AP6).

Unlike most older ``aura_*`` adapters these do **not** create their tables at runtime: Alembic is
the single schema authority (master roadmap principle 9, CB-20), and migrations ``0009``-``0012`` own
the tables. A missing table surfaces as ``DependencyUnavailableError`` naming the fix, rather than
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
from ..domain.ausm import AusmElement, ProjectTwin
from ..domain.blueprint import BlueprintState
from ..domain.compilation import ActivePointer, BlueprintRecord, plan_activation
from ..domain.errors import ConflictError, DataIntegrityError, DependencyUnavailableError, ValidationError
from ..domain.serialization import canonical_json, from_data, to_data
from ..domain.twin import TwinVersion
from .postgres_store import PostgresPool

__all__ = [
    "PostgresConnectorStore",
    "PostgresCiphertextStore",
    "PostgresTwinStore",
    "PostgresBlueprintStore",
    "ADAPTATION_SCHEMA",
    "TWIN_SCHEMA",
    "BLUEPRINT_SCHEMA",
    "ACTIVE_SCHEMA",
]

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


#: Kept textually in step with ``alembic/versions/0010_adaptation_twin.py``
#: (``tests/unit/test_adaptation_twin.py`` asserts it).
TWIN_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_adaptation_twin_versions (
        project_id      TEXT        NOT NULL DEFAULT 'p0',
        version         INTEGER     NOT NULL CHECK (version >= 1),
        digest          TEXT        NOT NULL CHECK (digest LIKE 'sha256:%'),
        parent_version  INTEGER     NULL,
        created_at      TIMESTAMPTZ NOT NULL,
        created_by      TEXT        NOT NULL,
        element_count   INTEGER     NOT NULL,
        record          JSONB       NOT NULL,
        PRIMARY KEY (project_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_adaptation_twin_elements (
        project_id  TEXT    NOT NULL DEFAULT 'p0',
        version     INTEGER NOT NULL,
        element_id  TEXT    NOT NULL,
        kind        TEXT    NOT NULL,
        body        JSONB   NOT NULL,
        PRIMARY KEY (project_id, version, element_id),
        FOREIGN KEY (project_id, version)
            REFERENCES aura_adaptation_twin_versions (project_id, version) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS aura_adaptation_twin_elements_kind
        ON aura_adaptation_twin_elements (project_id, version, kind)
    """,
)

_UNIQUE_VIOLATION = "23505"


class PostgresTwinStore:
    """Append-only Project Twin versions (AP4). One transaction per snapshot; no UPDATE path.

    Reading a version back re-validates every element through the strict codec and re-checks the
    twin's digest against the stored one, so a row edited behind AURA's back is refused
    (``DataIntegrityError``) rather than planned from.
    """

    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def append(self, version: TwinVersion, twin: ProjectTwin) -> None:
        if twin.version != version.version or twin.digest != version.digest:
            raise ValidationError("twin does not match its version record")
        pool = await self._pool._get_pool()
        rows = [
            (version.project_id, version.version, e.id, str(e.kind), canonical_json(to_data(e)))
            for e in twin.elements
        ]
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO aura_adaptation_twin_versions
                            (project_id, version, digest, parent_version, created_at, created_by,
                             element_count, record)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                        """,
                        version.project_id, version.version, version.digest, version.parent_version,
                        datetime.fromisoformat(version.created_at), version.created_by,
                        version.element_count, canonical_json(to_data(version)),
                    )
                    await conn.executemany(
                        """
                        INSERT INTO aura_adaptation_twin_elements (project_id, version, element_id, kind, body)
                        VALUES ($1, $2, $3, $4, $5::jsonb)
                        """,
                        rows,
                    )
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == _UNIQUE_VIOLATION:
                raise ConflictError("twin version already exists", details={"version": version.version}) from None
            raise _unavailable("store the twin version", exc) from exc

    async def latest(self, project_id: str) -> TwinVersion | None:
        versions = await self.versions(project_id, limit=1)
        return versions[0] if versions else None

    async def versions(self, project_id: str, *, limit: int = 50) -> Sequence[TwinVersion]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT record FROM aura_adaptation_twin_versions
                     WHERE project_id = $1 ORDER BY version DESC LIMIT $2
                    """,
                    project_id, limit,
                )
        except Exception as exc:
            raise _unavailable("list twin versions", exc) from exc
        return [from_data(TwinVersion, _json(row["record"])) for row in rows]

    async def get(self, project_id: str, version: int) -> tuple[TwinVersion, ProjectTwin] | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                record = await conn.fetchval(
                    "SELECT record FROM aura_adaptation_twin_versions WHERE project_id = $1 AND version = $2",
                    project_id, version,
                )
                if record is None:
                    return None
                rows = await conn.fetch(
                    """
                    SELECT body FROM aura_adaptation_twin_elements
                     WHERE project_id = $1 AND version = $2 ORDER BY element_id COLLATE "C"
                    """,
                    project_id, version,
                )
        except Exception as exc:
            raise _unavailable("read the twin version", exc) from exc
        meta = from_data(TwinVersion, _json(record))
        elements = [from_data(AusmElement, _json(row["body"]), path=f"$.elements[{i}]") for i, row in enumerate(rows)]
        twin = ProjectTwin.build(project_id, version, elements)
        if twin.digest != meta.digest:
            raise DataIntegrityError(
                "stored twin does not match its recorded digest", details={"version": version}
            )
        return meta, twin


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


#: Kept textually in step with ``alembic/versions/0011_adaptation_blueprints.py``
#: (``tests/unit/test_adaptation_compiler.py`` asserts it).
BLUEPRINT_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_adaptation_blueprints (
        project_id    TEXT        NOT NULL DEFAULT 'p0',
        version       INTEGER     NOT NULL CHECK (version >= 1),
        state         TEXT        NOT NULL,
        digest        TEXT        NOT NULL CHECK (digest LIKE 'sha256:%'),
        twin_version  INTEGER     NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL,
        created_by    TEXT        NOT NULL,
        record        JSONB       NOT NULL,
        PRIMARY KEY (project_id, version)
    )
    """,
)


#: Kept textually in step with ``alembic/versions/0012_adaptation_blueprint_active.py``.
ACTIVE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_adaptation_blueprint_active (
        project_id        TEXT        PRIMARY KEY DEFAULT 'p0',
        version           INTEGER     NOT NULL,
        digest            TEXT        NOT NULL CHECK (digest LIKE 'sha256:%'),
        activated_at      TIMESTAMPTZ NOT NULL,
        activated_by      TEXT        NOT NULL,
        previous_version  INTEGER     NULL,
        FOREIGN KEY (project_id, version) REFERENCES aura_adaptation_blueprints (project_id, version)
    )
    """,
)


async def _record_for_update(conn: Any, project_id: str, version: int) -> BlueprintRecord | None:
    value = await conn.fetchval(
        "SELECT record FROM aura_adaptation_blueprints WHERE project_id = $1 AND version = $2 FOR UPDATE",
        project_id, version,
    )
    return _blueprint_record(value) if value is not None else None


async def _write_record(conn: Any, record: BlueprintRecord) -> None:
    await conn.execute(
        "UPDATE aura_adaptation_blueprints SET state = $3, record = $4::jsonb WHERE project_id = $1 AND version = $2",
        record.project_id, record.version, record.state.value, canonical_json(to_data(record)),
    )


class _BlueprintLifecycle:
    """AP7 lifecycle methods of :class:`PostgresBlueprintStore`: compare-and-set transitions and
    the one atomic activation (target live, predecessor superseded, pointer row moved)."""

    _pool: PostgresPool

    async def transition(self, record: BlueprintRecord, *, expected: BlueprintState) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    """
                    UPDATE aura_adaptation_blueprints SET state = $3, record = $4::jsonb
                     WHERE project_id = $1 AND version = $2 AND state = $5 AND digest = $6
                    """,
                    record.project_id, record.version, record.state.value, canonical_json(to_data(record)),
                    expected.value, record.digest,
                )
        except Exception as exc:
            raise _unavailable("update the blueprint", exc) from exc
        if not status.endswith(" 1"):
            raise ConflictError("blueprint changed state concurrently", details={"version": record.version})

    async def activate(self, project_id: str, version: int, *, by: str, at: str) -> ActivePointer:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Serialises deploys and rollbacks for the project, including the very first
                    # one (no pointer row to lock yet).
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('aura_blueprint_active:' || $1))", project_id)
                    row = await conn.fetchrow(
                        "SELECT version FROM aura_adaptation_blueprint_active WHERE project_id = $1", project_id,
                    )
                    target = await _record_for_update(conn, project_id, version)
                    if target is None:
                        raise ConflictError("unknown blueprint version", details={"version": version})
                    current = await _record_for_update(conn, project_id, row["version"]) if row else None
                    live, retired, pointer = plan_activation(target, current, by=by, at=at)
                    await _write_record(conn, live)
                    if retired is not None:
                        await _write_record(conn, retired)
                    await conn.execute(
                        """
                        INSERT INTO aura_adaptation_blueprint_active
                            (project_id, version, digest, activated_at, activated_by, previous_version)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (project_id) DO UPDATE SET
                            version = EXCLUDED.version, digest = EXCLUDED.digest,
                            activated_at = EXCLUDED.activated_at, activated_by = EXCLUDED.activated_by,
                            previous_version = EXCLUDED.previous_version
                        """,
                        pointer.project_id, pointer.version, pointer.digest,
                        datetime.fromisoformat(pointer.activated_at), pointer.activated_by, pointer.previous_version,
                    )
        except (ConflictError, DataIntegrityError):
            raise
        except Exception as exc:
            raise _unavailable("activate the blueprint", exc) from exc
        return pointer

    async def active(self, project_id: str) -> ActivePointer | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT project_id, version, digest, activated_at, activated_by, previous_version
                      FROM aura_adaptation_blueprint_active WHERE project_id = $1
                    """,
                    project_id,
                )
        except Exception as exc:
            raise _unavailable("read the active blueprint", exc) from exc
        if row is None:
            return None
        return ActivePointer(
            project_id=row["project_id"], version=row["version"], digest=row["digest"],
            activated_at=_utc(row["activated_at"]).isoformat(), activated_by=row["activated_by"],
            previous_version=row["previous_version"],
        )


class PostgresBlueprintStore(_BlueprintLifecycle):
    """Compiled blueprint versions (AP6). Reading a row back re-decodes the whole record through
    the strict codec, which re-checks the blueprint's digest -- an edited row is refused."""

    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def append(self, record: BlueprintRecord) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_adaptation_blueprints
                        (project_id, version, state, digest, twin_version, created_at, created_by, record)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    """,
                    record.project_id, record.version, record.state.value, record.digest,
                    record.blueprint.project.twin_version, datetime.fromisoformat(record.created_at),
                    record.created_by, canonical_json(to_data(record)),
                )
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == _UNIQUE_VIOLATION:
                raise ConflictError("blueprint version already exists", details={"version": record.version}) from None
            raise _unavailable("store the blueprint", exc) from exc

    async def latest(self, project_id: str) -> BlueprintRecord | None:
        found = await self.versions(project_id, limit=1)
        return found[0] if found else None

    async def get(self, project_id: str, version: int) -> BlueprintRecord | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                record = await conn.fetchval(
                    "SELECT record FROM aura_adaptation_blueprints WHERE project_id = $1 AND version = $2",
                    project_id, version,
                )
        except Exception as exc:
            raise _unavailable("read the blueprint", exc) from exc
        return _blueprint_record(record) if record is not None else None

    async def versions(self, project_id: str, *, limit: int = 50) -> Sequence[BlueprintRecord]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT record FROM aura_adaptation_blueprints
                     WHERE project_id = $1 ORDER BY version DESC LIMIT $2
                    """,
                    project_id, limit,
                )
        except Exception as exc:
            raise _unavailable("list blueprints", exc) from exc
        return [_blueprint_record(row["record"]) for row in rows]


def _blueprint_record(value: Any) -> BlueprintRecord:
    try:
        return from_data(BlueprintRecord, _json(value))
    except ValidationError as exc:
        raise DataIntegrityError("stored blueprint failed validation", details={"error": exc.message}) from None
