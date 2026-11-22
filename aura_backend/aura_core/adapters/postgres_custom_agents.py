"""Postgres :class:`~aura_core.ports.custom_agents.CustomAgentStore`.

Reads and writes ``aura_custom_agents``, which Alembic revision 0035 creates; the adapter issues no
DDL (one schema authority, CB-20). Shares the container's ``PostgresPool``.

Every statement carries the owner. The per-owner limit is checked inside the insert's transaction,
under an advisory lock keyed on the owner, so two requests racing for the last slot serialise and
only one wins. The name rule is the unique index ``uq_aura_custom_agents_owner_name``
(``lower(name)`` per owner); its violation is reported as the ``name`` conflict.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..domain.custom_agents import CustomAgent, CustomAgentConflict
from ..domain.identifiers import UserId
from .postgres_store import PostgresPool

__all__ = ["PostgresCustomAgentStore"]

_COLUMNS = (
    "id, owner_id, name, description, base_agent_id, instruction, allowed_tools, risk_ceiling, "
    "created_at, updated_at"
)
_LIST = f"SELECT {_COLUMNS} FROM aura_custom_agents WHERE owner_id = $1 ORDER BY created_at, id"
_GET = f"SELECT {_COLUMNS} FROM aura_custom_agents WHERE owner_id = $1 AND id = $2"
_COUNT = "SELECT count(*) FROM aura_custom_agents WHERE owner_id = $1"
_LOCK = "SELECT pg_advisory_xact_lock(hashtext($1))"
_INSERT = (
    "INSERT INTO aura_custom_agents (id, owner_id, name, description, base_agent_id, instruction, "
    "allowed_tools, risk_ceiling, created_at, updated_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10)"
)
_UPDATE = (
    "UPDATE aura_custom_agents SET name = $3, description = $4, base_agent_id = $5, instruction = $6, "
    "allowed_tools = $7::jsonb, risk_ceiling = $8, updated_at = $9 "
    f"WHERE owner_id = $1 AND id = $2 RETURNING {_COLUMNS}"
)
_DELETE = "DELETE FROM aura_custom_agents WHERE owner_id = $1 AND id = $2"
_DELETE_OWNER = "DELETE FROM aura_custom_agents WHERE owner_id = $1"


def _row(row: Any) -> CustomAgent:
    tools = row["allowed_tools"]
    if isinstance(tools, str):
        tools = json.loads(tools)
    return CustomAgent(
        id=str(row["id"]),
        owner_id=UserId(row["owner_id"]),
        name=row["name"],
        description=row["description"],
        base_agent_id=row["base_agent_id"],
        instruction=row["instruction"],
        allowed_tools=tuple(tools) if tools is not None else None,
        risk_ceiling=row["risk_ceiling"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _tools_json(agent: CustomAgent) -> str | None:
    return json.dumps(list(agent.allowed_tools)) if agent.allowed_tools is not None else None


def _count(status: str) -> int:
    # asyncpg reports the command tag ("DELETE 3"); the count is its last word.
    return int(str(status).rsplit(" ", 1)[-1] or 0)


class PostgresCustomAgentStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def _conn(self) -> Any:
        return await self._pool._get_pool()

    async def list_for_owner(self, owner_id: UserId) -> list[CustomAgent]:
        pool = await self._conn()
        async with pool.acquire() as conn:
            return [_row(r) for r in await conn.fetch(_LIST, owner_id.value)]

    async def get(self, owner_id: UserId, agent_id: str) -> CustomAgent | None:
        try:
            ident = uuid.UUID(agent_id)
        except ValueError:
            return None
        pool = await self._conn()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_GET, owner_id.value, ident)
        return _row(row) if row is not None else None

    async def create(self, agent: CustomAgent, *, limit: int) -> CustomAgent:
        import asyncpg  # lazily: a vendor SDK is imported inside the adapter that needs it

        pool = await self._conn()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(_LOCK, agent.owner_id.value)
                if await conn.fetchval(_COUNT, agent.owner_id.value) >= limit:
                    raise CustomAgentConflict("limit", f"at most {limit} custom agents")
                try:
                    await conn.execute(
                        _INSERT, uuid.UUID(agent.id), agent.owner_id.value, agent.name, agent.description,
                        agent.base_agent_id, agent.instruction, _tools_json(agent), agent.risk_ceiling,
                        agent.created_at, agent.updated_at,
                    )
                except asyncpg.UniqueViolationError:
                    raise CustomAgentConflict("name", "you already have an agent with this name") from None
        return agent

    async def update(self, agent: CustomAgent) -> CustomAgent | None:
        import asyncpg

        pool = await self._conn()
        async with pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    _UPDATE, agent.owner_id.value, uuid.UUID(agent.id), agent.name, agent.description,
                    agent.base_agent_id, agent.instruction, _tools_json(agent), agent.risk_ceiling,
                    agent.updated_at,
                )
            except asyncpg.UniqueViolationError:
                raise CustomAgentConflict("name", "you already have an agent with this name") from None
        return _row(row) if row is not None else None

    async def delete(self, owner_id: UserId, agent_id: str) -> bool:
        try:
            ident = uuid.UUID(agent_id)
        except ValueError:
            return False
        pool = await self._conn()
        async with pool.acquire() as conn:
            return _count(await conn.execute(_DELETE, owner_id.value, ident)) > 0

    async def delete_for_owner(self, owner_id: UserId) -> int:
        pool = await self._conn()
        async with pool.acquire() as conn:
            return _count(await conn.execute(_DELETE_OWNER, owner_id.value))
