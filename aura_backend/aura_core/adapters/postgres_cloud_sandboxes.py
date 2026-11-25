"""Postgres implementation of the ``CloudSandboxStore`` port (``aura_cloud_sandboxes``, ``0040``).

Production runs several web workers, so which sandbox serves a workspace cannot live in one
process's memory. Every change here is one ``UPDATE`` or ``DELETE`` whose ``WHERE`` clause is the
condition, so two workers racing for the same key are settled by the row lock: the second one's
condition is re-checked against the first one's result and it is told ``False``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..domain.errors import DependencyUnavailableError
from ..ports.coding import CloudSandbox
from .postgres_store import PostgresPool

__all__ = ["PostgresCloudSandboxStore"]


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=timezone.utc)


class PostgresCloudSandboxStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def close(self) -> None:
        await self._pool.close()

    async def _query(self, method: str, sql: str, *args: Any) -> Any:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                return await getattr(conn, method)(sql, *args)
        except Exception as exc:
            raise DependencyUnavailableError(
                f"could not reach the cloud sandbox records: {exc}", dependency="postgres"
            ) from exc

    async def _applied(self, sql: str, *args: Any) -> bool:
        """Whether a single-row ``UPDATE``/``DELETE`` found its row in the expected state."""
        status = await self._query("execute", sql, *args)
        return str(status).endswith(" 1")

    @staticmethod
    def _row(row: Mapping[str, Any]) -> CloudSandbox:
        return CloudSandbox(
            key=row["key"],
            kind=row["kind"],
            sandbox_id=row["sandbox_id"],
            booted_at=_utc(row["booted_at"]),
            checkpoint_name=row["checkpoint_name"],
            saving_name=row["saving_name"],
            saving_until=_utc(row["saving_until"]),
            in_use=row["in_use"],
            in_use_until=_utc(row["in_use_until"]),
            used_bytes=row["used_bytes"],
            lost=row["lost"],
        )

    async def ensure(self, key: str, kind: str, *, at: datetime) -> CloudSandbox:
        await self._query(
            "execute",
            """
            INSERT INTO aura_cloud_sandboxes (key, kind, created_at, updated_at)
            VALUES ($1, $2, $3, $3)
            ON CONFLICT (key) DO NOTHING
            """,
            key, kind, at,
        )
        row = await self.get(key)
        if row is None:  # deleted between the two statements
            return await self.ensure(key, kind, at=at)
        return row

    async def get(self, key: str) -> CloudSandbox | None:
        row = await self._query(
            "fetchrow", "SELECT * FROM aura_cloud_sandboxes WHERE key = $1", key
        )
        return self._row(row) if row else None

    async def list_keys(self, *, kind: str) -> list[str]:
        rows = await self._query(
            "fetch", "SELECT key FROM aura_cloud_sandboxes WHERE kind = $1 ORDER BY key", kind
        )
        return [row["key"] for row in rows]

    async def attach(
        self, key: str, sandbox_id: str, *, expected_checkpoint: str | None, at: datetime
    ) -> bool:
        return await self._applied(
            """
            UPDATE aura_cloud_sandboxes
            SET sandbox_id = $2, booted_at = $4, in_use = 0, in_use_until = NULL,
                saving_name = NULL, saving_until = NULL, updated_at = $4
            WHERE key = $1 AND sandbox_id IS NULL AND checkpoint_name IS NOT DISTINCT FROM $3
            """,
            key, sandbox_id, expected_checkpoint, at,
        )

    async def detach(self, key: str, sandbox_id: str, *, lost: bool = False) -> bool:
        return await self._applied(
            """
            UPDATE aura_cloud_sandboxes
            SET sandbox_id = NULL, booted_at = NULL, in_use = 0, in_use_until = NULL,
                saving_name = NULL, saving_until = NULL, lost = lost OR $3::boolean,
                updated_at = now()
            WHERE key = $1 AND sandbox_id = $2
            """,
            key, sandbox_id, lost,
        )

    async def take_loss(self, key: str) -> bool:
        return await self._applied(
            "UPDATE aura_cloud_sandboxes SET lost = FALSE, updated_at = now() "
            "WHERE key = $1 AND lost",
            key,
        )

    async def begin_use(self, key: str, sandbox_id: str, *, at: datetime, until: datetime) -> bool:
        return await self._applied(
            """
            UPDATE aura_cloud_sandboxes
            SET in_use = CASE WHEN in_use > 0 AND in_use_until > $3::timestamptz
                              THEN in_use + 1 ELSE 1 END,
                in_use_until = CASE WHEN in_use > 0 AND in_use_until > $3::timestamptz
                                    THEN GREATEST(in_use_until, $4::timestamptz)
                                    ELSE $4::timestamptz END,
                updated_at = $3::timestamptz
            WHERE key = $1 AND sandbox_id = $2
              AND (saving_until IS NULL OR saving_until <= $3::timestamptz)
            """,
            key, sandbox_id, at, until,
        )

    async def end_use(self, key: str, sandbox_id: str) -> None:
        await self._applied(
            """
            UPDATE aura_cloud_sandboxes
            SET in_use = GREATEST(in_use - 1, 0), updated_at = now()
            WHERE key = $1 AND sandbox_id = $2
            """,
            key, sandbox_id,
        )

    async def begin_save(
        self, key: str, sandbox_id: str, name: str, *, at: datetime, until: datetime
    ) -> bool:
        return await self._applied(
            """
            UPDATE aura_cloud_sandboxes
            SET saving_name = $3, saving_until = $5, updated_at = $4::timestamptz
            WHERE key = $1 AND sandbox_id = $2
              AND (saving_until IS NULL OR saving_until <= $4::timestamptz)
              AND (in_use = 0 OR in_use_until IS NULL OR in_use_until <= $4::timestamptz)
            """,
            key, sandbox_id, name, at, until,
        )

    async def commit_save(self, key: str, name: str, *, used_bytes: int | None) -> bool:
        return await self._applied(
            """
            UPDATE aura_cloud_sandboxes
            SET checkpoint_name = $2, used_bytes = COALESCE($3::bigint, used_bytes),
                updated_at = now()
            WHERE key = $1 AND saving_name = $2
            """,
            key, name, used_bytes,
        )

    async def release_save(self, key: str, name: str) -> None:
        await self._applied(
            """
            UPDATE aura_cloud_sandboxes
            SET saving_name = NULL, saving_until = NULL, updated_at = now()
            WHERE key = $1 AND saving_name = $2
            """,
            key, name,
        )

    async def record_usage(self, key: str, used_bytes: int) -> None:
        await self._applied(
            "UPDATE aura_cloud_sandboxes SET used_bytes = $2, updated_at = now() WHERE key = $1",
            key, used_bytes,
        )

    async def delete(
        self, key: str, *, sandbox_id: str | None, checkpoint_name: str | None
    ) -> bool:
        return await self._applied(
            """
            DELETE FROM aura_cloud_sandboxes
            WHERE key = $1 AND sandbox_id IS NOT DISTINCT FROM $2
              AND checkpoint_name IS NOT DISTINCT FROM $3
            """,
            key, sandbox_id, checkpoint_name,
        )
