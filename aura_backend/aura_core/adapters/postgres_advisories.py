"""Postgres :class:`~aura_core.ports.advisories.AdvisoryStore`.

Reads and writes ``aura_osv_advisories`` and ``aura_osv_sync``, which Alembic revision 0034 creates;
the adapter issues no DDL (one schema authority, CB-20). Shares the container's ``PostgresPool``.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..domain.advisories import EcosystemStatus, StoredAdvisory
from .postgres_store import PostgresPool

__all__ = ["PostgresAdvisoryStore"]

_UPSERT = (
    "INSERT INTO aura_osv_advisories (ecosystem, package, advisory_id, modified, payload) "
    "VALUES ($1, $2, $3, $4, $5::jsonb) "
    "ON CONFLICT (ecosystem, package, advisory_id) DO UPDATE SET "
    "modified = EXCLUDED.modified, payload = EXCLUDED.payload"
)
_REMOVE = "DELETE FROM aura_osv_advisories WHERE ecosystem = $1 AND advisory_id = ANY($2::text[])"
_LOOKUP = (
    "SELECT advisory_id, payload FROM aura_osv_advisories "
    "WHERE ecosystem = $1 AND package = ANY($2::text[]) ORDER BY advisory_id, package"
)
_COUNTS = "SELECT ecosystem, count(DISTINCT advisory_id) AS n FROM aura_osv_advisories GROUP BY ecosystem"
_SYNC_ROWS = (
    "SELECT ecosystem, newest_modified, synced_at, last_attempt_at, last_error FROM aura_osv_sync"
)
_RECORD = (
    "INSERT INTO aura_osv_sync (ecosystem, newest_modified, synced_at, last_attempt_at, last_error) "
    "VALUES ($1, $2, $3, $4, $5) "
    "ON CONFLICT (ecosystem) DO UPDATE SET newest_modified = EXCLUDED.newest_modified, "
    "synced_at = EXCLUDED.synced_at, last_attempt_at = EXCLUDED.last_attempt_at, "
    "last_error = EXCLUDED.last_error"
)
#: Rows per statement: a bootstrap writes tens of thousands, and one huge ``executemany`` holds the
#: connection and a transaction for the whole of it.
_BATCH = 500


class PostgresAdvisoryStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def _conn(self) -> Any:
        return await self._pool._get_pool()

    async def upsert(self, items: Sequence[StoredAdvisory]) -> int:
        pool = await self._conn()
        written = 0
        for start in range(0, len(items), _BATCH):
            chunk = items[start:start + _BATCH]
            rows = [(i.ecosystem, i.package, i.advisory_id, i.modified, json.dumps(i.payload)) for i in chunk]
            async with pool.acquire() as conn:
                await conn.executemany(_UPSERT, rows)
            written += len(chunk)
        return written

    async def remove(self, ecosystem: str, advisory_ids: Sequence[str]) -> int:
        if not advisory_ids:
            return 0
        pool = await self._conn()
        async with pool.acquire() as conn:
            status = await conn.execute(_REMOVE, ecosystem, list(advisory_ids))
        return int(status.rsplit(" ", 1)[-1])

    async def lookup(self, ecosystem: str, packages: Sequence[str]) -> list[Mapping[str, Any]]:
        if not packages:
            return []
        pool = await self._conn()
        out: dict[str, dict[str, Any]] = {}
        # A lockfile can pin thousands of packages; ``ANY`` takes them all but keep each call bounded.
        names = list(dict.fromkeys(packages))
        for start in range(0, len(names), 2000):
            async with pool.acquire() as conn:
                rows = await conn.fetch(_LOOKUP, ecosystem, names[start:start + 2000])
            for row in rows:
                payload = row["payload"]
                payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
                known = out.get(row["advisory_id"])
                if known is None:
                    out[row["advisory_id"]] = {**payload, "affected": list(payload.get("affected") or [])}
                else:
                    known["affected"] = known["affected"] + list(payload.get("affected") or [])
        return list(out.values())

    async def status(self) -> list[EcosystemStatus]:
        pool = await self._conn()
        async with pool.acquire() as conn:
            counts = {r["ecosystem"]: int(r["n"]) for r in await conn.fetch(_COUNTS)}
            synced = {r["ecosystem"]: r for r in await conn.fetch(_SYNC_ROWS)}
        out = []
        for eco in sorted(set(counts) | set(synced)):
            row = synced.get(eco)
            out.append(EcosystemStatus(
                eco, counts.get(eco, 0),
                row["newest_modified"] if row else None, row["synced_at"] if row else None,
                row["last_error"] if row else None, row["last_attempt_at"] if row else None))
        return out

    async def record_sync(self, status: EcosystemStatus) -> None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            await conn.execute(_RECORD, status.ecosystem, status.newest_modified, status.synced_at,
                               status.last_attempt_at, status.last_error)
