"""Postgres :class:`~aura_core.ports.tenancy.TenancyStore` (SaaS P1, ADR-034).

Shares the container's ``PostgresPool`` like every other ``postgres_*.py`` adapter. The tables
are owned by Alembic revision 0020; the adapter issues no DDL (one schema authority, CB-20).

Membership is read on every team-scoped request (``aura_service._build_authenticator``) with no
cache in front of it, so removing a member takes effect on their very next call. The multi-row
operations run in one transaction each; the one-owner and one-personal-workspace rules are also
enforced by partial unique indexes, so a race the code missed still cannot break them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..domain.errors import ConflictError, NotFoundError
from ..domain.tenancy import Invite, Membership, Workspace, WorkspaceKind, WorkspaceRole
from .postgres_store import PostgresPool

__all__ = ["PostgresTenancyStore"]

_WS = "id, kind, name, created_by, created_at"
_MEMBER = "workspace_id, user_id, role, invited_by, joined_at"
_INVITE = (
    "invite_id, workspace_id, email, role, digest, created_by, created_at, expires_at, "
    "accepted_at, accepted_by"
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _workspace(row: Mapping[str, Any]) -> Workspace:
    return Workspace(
        id=row["id"], kind=WorkspaceKind(row["kind"]), name=row["name"],
        created_by=row["created_by"], created_at=_utc(row["created_at"]),
    )


def _member(row: Mapping[str, Any]) -> Membership:
    return Membership(
        workspace_id=row["workspace_id"], user_id=row["user_id"], role=WorkspaceRole(row["role"]),
        joined_at=_utc(row["joined_at"]), invited_by=row["invited_by"],
    )


def _invite(row: Mapping[str, Any]) -> Invite:
    return Invite(
        invite_id=row["invite_id"], workspace_id=row["workspace_id"], email=row["email"],
        role=WorkspaceRole(row["role"]), digest=row["digest"], created_by=row["created_by"],
        created_at=_utc(row["created_at"]), expires_at=_utc(row["expires_at"]),
        accepted_at=_utc(row["accepted_at"]), accepted_by=row["accepted_by"],
    )


class PostgresTenancyStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def _conn(self) -> Any:
        return await self._pool._get_pool()

    async def create_workspace(self, ws: Workspace, owner: Membership) -> None:
        import asyncpg

        pool = await self._conn()
        async with pool.acquire() as conn:
            try:
                async with conn.transaction():
                    await conn.execute(
                        f"INSERT INTO aura_workspaces ({_WS}) VALUES ($1, $2, $3, $4, $5)",
                        ws.id, ws.kind.value, ws.name, ws.created_by, ws.created_at,
                    )
                    await conn.execute(
                        f"INSERT INTO aura_workspace_members ({_MEMBER}) VALUES ($1, $2, $3, $4, $5)",
                        owner.workspace_id, owner.user_id, owner.role.value, owner.invited_by, owner.joined_at,
                    )
            except asyncpg.UniqueViolationError as exc:
                raise ConflictError("this workspace already exists, or this person already has a personal one") from exc

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT {_WS} FROM aura_workspaces WHERE id = $1", workspace_id)
        return _workspace(row) if row else None

    async def personal_workspace(self, user_id: str) -> Workspace | None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_WS} FROM aura_workspaces WHERE created_by = $1 AND kind = 'personal'", user_id
            )
        return _workspace(row) if row else None

    async def delete_workspace(self, workspace_id: str) -> None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            # Members and invites go with it (ON DELETE CASCADE).
            await conn.execute("DELETE FROM aura_workspaces WHERE id = $1", workspace_id)

    async def memberships_of(self, user_id: str) -> list[tuple[Workspace, Membership]]:
        pool = await self._conn()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT w.id, w.kind, w.name, w.created_by, w.created_at, "
                "m.workspace_id, m.user_id, m.role, m.invited_by, m.joined_at "
                "FROM aura_workspace_members m JOIN aura_workspaces w ON w.id = m.workspace_id "
                "WHERE m.user_id = $1 ORDER BY w.created_at, w.id",
                user_id,
            )
        return [(_workspace(r), _member(r)) for r in rows]

    async def get_membership(self, workspace_id: str, user_id: str) -> Membership | None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_MEMBER} FROM aura_workspace_members WHERE workspace_id = $1 AND user_id = $2",
                workspace_id, user_id,
            )
        return _member(row) if row else None

    async def list_members(self, workspace_id: str) -> list[Membership]:
        pool = await self._conn()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_MEMBER} FROM aura_workspace_members WHERE workspace_id = $1 "
                "ORDER BY joined_at, user_id",
                workspace_id,
            )
        return [_member(r) for r in rows]

    async def add_member(self, m: Membership) -> None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            status = await conn.execute(
                f"INSERT INTO aura_workspace_members ({_MEMBER}) VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (workspace_id, user_id) DO NOTHING",
                m.workspace_id, m.user_id, m.role.value, m.invited_by, m.joined_at,
            )
        if status.rsplit(" ", 1)[-1] != "1":
            raise ConflictError("already a member of this workspace")

    async def set_role(self, workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "UPDATE aura_workspace_members SET role = $3 WHERE workspace_id = $1 AND user_id = $2",
                workspace_id, user_id, role.value,
            )
        if status.rsplit(" ", 1)[-1] != "1":
            raise NotFoundError("no such member")

    async def remove_member(self, workspace_id: str, user_id: str) -> bool:
        pool = await self._conn()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM aura_workspace_members WHERE workspace_id = $1 AND user_id = $2",
                workspace_id, user_id,
            )
        return status.rsplit(" ", 1)[-1] == "1"

    async def transfer_ownership(self, workspace_id: str, from_user: str, to_user: str) -> None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    "SELECT user_id, role FROM aura_workspace_members "
                    "WHERE workspace_id = $1 AND user_id = ANY($2::text[]) FOR UPDATE",
                    workspace_id, [from_user, to_user],
                )
                roles = {r["user_id"]: r["role"] for r in rows}
                if roles.get(from_user) != "owner" or to_user not in roles or from_user == to_user:
                    raise ConflictError("ownership can only pass from the owner to a member")
                # Demote first: the one-owner partial unique index forbids two owners at once.
                await conn.execute(
                    "UPDATE aura_workspace_members SET role = 'admin' WHERE workspace_id = $1 AND user_id = $2",
                    workspace_id, from_user,
                )
                await conn.execute(
                    "UPDATE aura_workspace_members SET role = 'owner' WHERE workspace_id = $1 AND user_id = $2",
                    workspace_id, to_user,
                )

    async def put_invite(self, i: Invite) -> None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            status = await conn.execute(
                f"INSERT INTO aura_workspace_invites ({_INVITE}) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT (invite_id) DO NOTHING",
                i.invite_id, i.workspace_id, i.email, i.role.value, i.digest, i.created_by,
                i.created_at, i.expires_at, i.accepted_at, i.accepted_by,
            )
        if status.rsplit(" ", 1)[-1] != "1":
            raise ConflictError("an invite with that id already exists")

    async def get_invite(self, invite_id: str) -> Invite | None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_INVITE} FROM aura_workspace_invites WHERE invite_id = $1", invite_id
            )
        return _invite(row) if row else None

    async def list_invites(self, workspace_id: str) -> list[Invite]:
        pool = await self._conn()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_INVITE} FROM aura_workspace_invites WHERE workspace_id = $1 "
                "ORDER BY created_at, invite_id",
                workspace_id,
            )
        return [_invite(r) for r in rows]

    async def revoke_invite(self, invite_id: str) -> bool:
        pool = await self._conn()
        async with pool.acquire() as conn:
            status = await conn.execute("DELETE FROM aura_workspace_invites WHERE invite_id = $1", invite_id)
        return status.rsplit(" ", 1)[-1] == "1"

    async def accept_invite(
        self, invite_id: str, *, user_id: str, at: datetime, seat_limit: int
    ) -> Membership:
        pool = await self._conn()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"SELECT {_INVITE} FROM aura_workspace_invites WHERE invite_id = $1 FOR UPDATE",
                    invite_id,
                )
                invite = _invite(row) if row else None
                if invite is None or not invite.is_open(at):
                    raise ConflictError("this invite is no longer valid")
                # Lock the workspace row so two accepts cannot both take the last seat.
                locked = await conn.fetchval(
                    "SELECT id FROM aura_workspaces WHERE id = $1 FOR UPDATE", invite.workspace_id
                )
                if locked is None:
                    raise ConflictError("this invite is no longer valid")
                existing = await conn.fetchval(
                    "SELECT 1 FROM aura_workspace_members WHERE workspace_id = $1 AND user_id = $2",
                    invite.workspace_id, user_id,
                )
                if existing:
                    raise ConflictError("already a member of this workspace")
                seats = await conn.fetchval(
                    "SELECT count(*) FROM aura_workspace_members WHERE workspace_id = $1", invite.workspace_id
                )
                if seats >= seat_limit:
                    raise ConflictError("this workspace has no free seats")
                member = Membership(invite.workspace_id, user_id, invite.role, at, invited_by=invite.created_by)
                await conn.execute(
                    f"INSERT INTO aura_workspace_members ({_MEMBER}) VALUES ($1, $2, $3, $4, $5)",
                    member.workspace_id, member.user_id, member.role.value, member.invited_by, member.joined_at,
                )
                await conn.execute(
                    "UPDATE aura_workspace_invites SET accepted_at = $2, accepted_by = $3 WHERE invite_id = $1",
                    invite_id, at, user_id,
                )
        return member
