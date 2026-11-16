"""In-memory :class:`~aura_core.ports.tenancy.TenancyStore` (SaaS P1, ADR-034).

The real substrate for tests and a single-process local run; production uses
``postgres_tenancy.py``. One lock serialises every write, which is what makes the multi-row
operations atomic here.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime

from ..domain.errors import ConflictError, NotFoundError
from ..domain.tenancy import Invite, Membership, Workspace, WorkspaceKind, WorkspaceRole

__all__ = ["InMemoryTenancyStore"]


class InMemoryTenancyStore:
    def __init__(self) -> None:
        self._workspaces: dict[str, Workspace] = {}
        self._members: dict[tuple[str, str], Membership] = {}
        self._invites: dict[str, Invite] = {}
        self._lock = asyncio.Lock()

    async def create_workspace(self, ws: Workspace, owner: Membership) -> None:
        async with self._lock:
            if ws.id in self._workspaces:
                raise ConflictError("a workspace with that id already exists")
            if ws.kind is WorkspaceKind.PERSONAL and self._personal(ws.created_by) is not None:
                raise ConflictError("this person already has a personal workspace")
            self._workspaces[ws.id] = ws
            self._members[(ws.id, owner.user_id)] = owner

    def _personal(self, user_id: str) -> Workspace | None:
        for ws in self._workspaces.values():
            if ws.kind is WorkspaceKind.PERSONAL and ws.created_by == user_id:
                return ws
        return None

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        return self._workspaces.get(workspace_id)

    async def personal_workspace(self, user_id: str) -> Workspace | None:
        return self._personal(user_id)

    async def delete_workspace(self, workspace_id: str) -> None:
        async with self._lock:
            self._workspaces.pop(workspace_id, None)
            for key in [k for k in self._members if k[0] == workspace_id]:
                del self._members[key]
            for key in [k for k, i in self._invites.items() if i.workspace_id == workspace_id]:
                del self._invites[key]

    async def memberships_of(self, user_id: str) -> list[tuple[Workspace, Membership]]:
        found = [
            (self._workspaces[ws_id], m)
            for (ws_id, uid), m in self._members.items()
            if uid == user_id and ws_id in self._workspaces
        ]
        return sorted(found, key=lambda pair: (pair[0].created_at, pair[0].id))

    async def get_membership(self, workspace_id: str, user_id: str) -> Membership | None:
        return self._members.get((workspace_id, user_id))

    async def list_members(self, workspace_id: str) -> list[Membership]:
        members = [m for (ws_id, _), m in self._members.items() if ws_id == workspace_id]
        return sorted(members, key=lambda m: (m.joined_at, m.user_id))

    async def add_member(self, m: Membership) -> None:
        async with self._lock:
            if (m.workspace_id, m.user_id) in self._members:
                raise ConflictError("already a member of this workspace")
            self._members[(m.workspace_id, m.user_id)] = m

    async def set_role(self, workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
        async with self._lock:
            current = self._members.get((workspace_id, user_id))
            if current is None:
                raise NotFoundError("no such member")
            self._members[(workspace_id, user_id)] = replace(current, role=role)

    async def remove_member(self, workspace_id: str, user_id: str) -> bool:
        async with self._lock:
            return self._members.pop((workspace_id, user_id), None) is not None

    async def transfer_ownership(self, workspace_id: str, from_user: str, to_user: str) -> None:
        async with self._lock:
            old = self._members.get((workspace_id, from_user))
            new = self._members.get((workspace_id, to_user))
            if old is None or new is None or old.role is not WorkspaceRole.OWNER:
                raise ConflictError("ownership can only pass from the owner to a member")
            self._members[(workspace_id, from_user)] = replace(old, role=WorkspaceRole.ADMIN)
            self._members[(workspace_id, to_user)] = replace(new, role=WorkspaceRole.OWNER)

    async def put_invite(self, i: Invite) -> None:
        async with self._lock:
            if i.invite_id in self._invites:
                raise ConflictError("an invite with that id already exists")
            self._invites[i.invite_id] = i

    async def get_invite(self, invite_id: str) -> Invite | None:
        return self._invites.get(invite_id)

    async def list_invites(self, workspace_id: str) -> list[Invite]:
        invites = [i for i in self._invites.values() if i.workspace_id == workspace_id]
        return sorted(invites, key=lambda i: (i.created_at, i.invite_id))

    async def revoke_invite(self, invite_id: str) -> bool:
        async with self._lock:
            return self._invites.pop(invite_id, None) is not None

    async def accept_invite(
        self, invite_id: str, *, user_id: str, at: datetime, seat_limit: int
    ) -> Membership:
        async with self._lock:
            invite = self._invites.get(invite_id)
            if invite is None or not invite.is_open(at) or invite.workspace_id not in self._workspaces:
                raise ConflictError("this invite is no longer valid")
            if (invite.workspace_id, user_id) in self._members:
                raise ConflictError("already a member of this workspace")
            seats = sum(1 for (ws_id, _) in self._members if ws_id == invite.workspace_id)
            if seats >= seat_limit:
                raise ConflictError("this workspace has no free seats")
            member = Membership(invite.workspace_id, user_id, invite.role, at, invited_by=invite.created_by)
            self._members[(invite.workspace_id, user_id)] = member
            self._invites[invite_id] = replace(invite, accepted_at=at, accepted_by=user_id)
            return member
