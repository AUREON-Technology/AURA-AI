"""Postgres :class:`~aura_core.ports.sharing.SharedConversationStore` (SaaS P1-F1).

Table owned by Alembic revision 0025; no DDL here.
"""

from __future__ import annotations

import json
from typing import Any

from ..domain.sharing import SharedConversation, SharedMessage
from .postgres_store import PostgresPool

__all__ = ["PostgresSharedConversationStore"]

_COLUMNS = "id, workspace_id, shared_by, source_id, title, messages, created_at"


def _row(row: Any, *, with_messages: bool = True) -> SharedConversation:
    messages: tuple[SharedMessage, ...] = ()
    if with_messages:
        raw = row["messages"]
        data = json.loads(raw) if isinstance(raw, str) else raw
        messages = tuple(SharedMessage(m["role"], m["text"]) for m in data)
    return SharedConversation(
        id=row["id"], workspace_id=row["workspace_id"], shared_by=row["shared_by"],
        source_id=row["source_id"], title=row["title"], messages=messages, created_at=row["created_at"],
    )


class PostgresSharedConversationStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def put(self, c: SharedConversation) -> SharedConversation:
        pool = await self._pool._get_pool()
        payload = json.dumps([{"role": m.role, "text": m.text} for m in c.messages])
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"INSERT INTO aura_shared_conversations ({_COLUMNS}) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7) "
                "ON CONFLICT (workspace_id, shared_by, source_id) DO UPDATE SET title = EXCLUDED.title, "
                "messages = EXCLUDED.messages, created_at = EXCLUDED.created_at "
                f"RETURNING {_COLUMNS}",
                c.id, c.workspace_id, c.shared_by, c.source_id, c.title, payload, c.created_at,
            )
        return _row(row)

    async def get(self, workspace_id: str, conversation_id: str) -> SharedConversation | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM aura_shared_conversations WHERE workspace_id = $1 AND id = $2",
                workspace_id, conversation_id,
            )
        return _row(row) if row is not None else None

    async def list(self, workspace_id: str, limit: int) -> list[SharedConversation]:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, workspace_id, shared_by, source_id, title, NULL AS messages, created_at "
                "FROM aura_shared_conversations WHERE workspace_id = $1 ORDER BY created_at DESC, id LIMIT $2",
                workspace_id, limit,
            )
        return [_row(r, with_messages=False) for r in rows]

    async def delete(self, workspace_id: str, conversation_id: str) -> bool:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM aura_shared_conversations WHERE workspace_id = $1 AND id = $2",
                workspace_id, conversation_id,
            )
        return status.rsplit(" ", 1)[-1] != "0"
