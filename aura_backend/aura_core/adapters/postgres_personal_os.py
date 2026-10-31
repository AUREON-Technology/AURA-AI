"""Postgres implementation of the ``PersonalOsStore`` port (Phase C2).

**Superseded by ADR-032.** The agent's personal-OS store is now the app's synced tables
(``postgres_synced_personal_os.py``). This table, ``aura_personal_os_records``, is what the agent
wrote before; it is still read by ``scripts/move_personal_os_records.py`` (which copies it into
the synced tables) and cleared by erasure, and nothing else writes it. It is not dropped yet.

**``fields`` is encrypted at rest** (server-side, not end-to-end) when the container passes an
enabled :class:`~aura_core.adapters.field_crypto.FieldCipher`: the whole mapping is sealed as one
value and stored as ``{"_aura_enc": "aura-enc:v1:..."}``, so the column stays ``JSONB`` and no
migration is needed. ``id``/``owner_id``/``kind``/``source``/timestamps stay in the clear -- they
are the only things any statement here filters or sorts on. Nothing queries *inside* ``fields``
(no ``fields->>'x'`` predicate exists); a future "records due this week" query must filter in
Python after decrypting, not in SQL. Rows written before a key was configured are read as
plaintext and sealed on their next write. See ``docs/architecture/004-memory-taxonomy.md``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from ..domain.errors import DependencyUnavailableError
from ..domain.identifiers import PersonalOsRecordId, UserId
from ..domain.personal_os import PersonalOsRecord, RecordKind
from .field_crypto import FieldCipher
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresPersonalOsStore"]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_personal_os_records (
        id          TEXT        PRIMARY KEY,
        owner_id    TEXT        NOT NULL,
        kind        TEXT        NOT NULL,
        fields      JSONB       NOT NULL DEFAULT '{}'::jsonb,
        source      TEXT        NOT NULL DEFAULT 'kernel',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # The shape `list_for_owner` asks for: this owner, optionally this kind, newest first.
    """
    CREATE INDEX IF NOT EXISTS ix_aura_personal_os_owner_kind
        ON aura_personal_os_records (owner_id, kind, created_at DESC)
    """,
)


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class PostgresPersonalOsStore:
    def __init__(
        self, url: str, *, pool: PostgresPool | None = None, cipher: FieldCipher | None = None,
    ) -> None:
        self._pool = pool or PostgresPool(url)
        self._schema_ready = False
        self._cipher = cipher or FieldCipher.disabled()

    @staticmethod
    def encryption_context(record_id: str, owner_id: str) -> str:
        """What sealed ``fields`` are bound to: copying them into another row fails to decrypt."""
        return f"aura_personal_os_records:{record_id}:{owner_id}"

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                for statement in _SCHEMA:
                    await conn.execute(statement)
        except Exception as exc:
            raise _unavailable(f"could not create the personal-OS schema: {exc}") from exc
        self._schema_ready = True

    @staticmethod
    def _record(row: Mapping[str, Any], cipher: FieldCipher | None = None) -> PersonalOsRecord:
        fields = row["fields"]
        if isinstance(fields, str):
            fields = json.loads(fields)
        fields = (cipher or FieldCipher.disabled()).decrypt_mapping(
            fields,
            context=PostgresPersonalOsStore.encryption_context(row["id"], row["owner_id"]),
        )
        return PersonalOsRecord(
            id=PersonalOsRecordId(row["id"]),
            owner_id=UserId(row["owner_id"]),
            kind=RecordKind(row["kind"]),
            fields=fields,
            source=row["source"],
            created_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]),
        )

    async def put(self, record: PersonalOsRecord) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_personal_os_records
                        (id, owner_id, kind, fields, source, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (id) DO UPDATE SET
                        fields = EXCLUDED.fields, updated_at = EXCLUDED.updated_at
                    """,
                    record.id.value, record.owner_id.value, record.kind.value,
                    json.dumps(self._cipher.encrypt_mapping(
                        record.fields,
                        context=self.encryption_context(record.id.value, record.owner_id.value),
                    )),
                    record.source,
                    record.created_at, record.updated_at,
                )
        except Exception as exc:
            raise _unavailable(f"could not write personal-OS record: {exc}") from exc

    async def get(
        self, record_id: PersonalOsRecordId, *, owner_id: UserId
    ) -> PersonalOsRecord | None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM aura_personal_os_records WHERE id = $1 AND owner_id = $2",
                    record_id.value, owner_id.value,
                )
        except Exception as exc:
            raise _unavailable(f"could not read personal-OS record: {exc}") from exc
        return self._record(row, self._cipher) if row else None

    async def list_for_owner(
        self, owner_id: UserId, *, kind: RecordKind | None = None, limit: int = 100,
    ) -> list[PersonalOsRecord]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if kind is not None:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_personal_os_records
                        WHERE owner_id = $1 AND kind = $2
                        ORDER BY created_at DESC LIMIT $3
                        """,
                        owner_id.value, kind.value, limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_personal_os_records
                        WHERE owner_id = $1
                        ORDER BY created_at DESC LIMIT $2
                        """,
                        owner_id.value, limit,
                    )
        except Exception as exc:
            raise _unavailable(f"could not list personal-OS records: {exc}") from exc
        return [self._record(row, self._cipher) for row in rows]

    async def update(self, record: PersonalOsRecord) -> None:
        await self.put(record)

    async def delete(self, record_id: PersonalOsRecordId, *, owner_id: UserId) -> bool:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM aura_personal_os_records WHERE id = $1 AND owner_id = $2",
                    record_id.value, owner_id.value,
                )
        except Exception as exc:
            raise _unavailable(f"could not delete personal-OS record: {exc}") from exc
        return result != "DELETE 0"
