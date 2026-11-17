"""Port for workspaces, memberships and invites (SaaS P1, ADR-034). Stdlib only (ADR-001).

Every method that changes more than one row is atomic in every adapter: a team always has
exactly one Owner, and an invite is accepted at most once and never past the seat limit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain.tenancy import Invite, Membership, Workspace, WorkspaceRole

__all__ = ["TenancyStore"]


class TenancyStore(Protocol):
    async def create_workspace(self, ws: Workspace, owner: Membership) -> None:
        """Create ``ws`` with ``owner`` as its only member. ``ConflictError`` if ``ws`` is
        personal and its creator already has a personal workspace."""

    async def get_workspace(self, workspace_id: str) -> Workspace | None: ...

    async def personal_workspace(self, user_id: str) -> Workspace | None: ...

    async def delete_workspace(self, workspace_id: str) -> None:
        """Remove the workspace, its memberships and its invites."""

    async def memberships_of(self, user_id: str) -> list[tuple[Workspace, Membership]]: ...

    async def get_membership(self, workspace_id: str, user_id: str) -> Membership | None: ...

    async def list_members(self, workspace_id: str) -> list[Membership]: ...

    async def add_member(self, m: Membership) -> None:
        """``ConflictError`` if the person is already a member."""

    async def set_role(
        self, workspace_id: str, user_id: str, role: WorkspaceRole, *, expected: WorkspaceRole | None = None
    ) -> None:
        """With ``expected``, change the role only if it is still ``expected`` (``ConflictError``
        otherwise), so a decision made on a stale read never demotes a new Owner."""

    async def remove_member(
        self, workspace_id: str, user_id: str, *, expected: WorkspaceRole | None = None
    ) -> bool:
        """False if there was no such member, or (with ``expected``) their role had changed."""

    async def transfer_ownership(self, workspace_id: str, from_user: str, to_user: str) -> None:
        """Atomically: ``from_user`` becomes Admin, ``to_user`` becomes Owner."""

    async def put_invite(self, i: Invite) -> None: ...

    async def get_invite(self, invite_id: str) -> Invite | None: ...

    async def list_invites(self, workspace_id: str) -> list[Invite]:
        """Every invite not revoked, open or already accepted, oldest first."""

    async def revoke_invite(self, invite_id: str) -> bool:
        """Delete the invite; False if there was none."""

    async def accept_invite(
        self, invite_id: str, *, user_id: str, at: datetime, seat_limit: int
    ) -> Membership:
        """Atomically mark the invite accepted and add ``user_id`` with the invite's role.
        ``ConflictError`` if the invite is missing or not open at ``at``, the person is already
        a member, or the workspace already has ``seat_limit`` members."""
