"""Workspace rules: who may create, invite, remove, promote, transfer and delete (ADR-034).

Every decision about a team is made here, over the :class:`~aura_core.ports.tenancy.TenancyStore`
port; the HTTP routes only translate. Invariants this module keeps:

* each person has exactly one personal workspace, which nobody can be invited into, leave or
  delete;
* each team has exactly one Owner -- the Owner cannot be removed or demoted and leaves only after
  transferring ownership;
* a team's members plus its open invites never exceed its seats;
* "no such workspace" and "not a member" are the same error, so the API never confirms that a
  workspace someone does not belong to exists;
* every change is audited, and no audit record carries an invite token.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from ..domain.errors import AuthorizationError, ConflictError, ValidationError
from ..domain.tenancy import (
    INVITE_TTL,
    Invite,
    Membership,
    Workspace,
    WorkspaceKind,
    WorkspaceRole,
    can,
    clean_workspace_name,
    invite_digest,
    is_workspace_id,
    new_invite_token,
    new_workspace_id,
    parse_invite_token,
)
from ..ports.runtime import AuditEvent

__all__ = ["TenancyService"]

_NOT_A_MEMBER = "not a member of this workspace"
_MAX_EMAIL = 320


@dataclass
class TenancyService:
    store: Any
    clock: Any
    audit: Any
    settings: Any
    #: ``async (workspace_id) -> int``: the team's seats from its plan (``PlanService.seat_limit``,
    #: SaaS P1-C). Unset (tests, no plans wired): ``settings.default_team_seats``.
    seat_limit: Any = None

    async def _seats(self, workspace_id: str) -> int:
        if self.seat_limit is not None:
            return await self.seat_limit(workspace_id)
        return self.settings.default_team_seats

    # -- reading --------------------------------------------------------------------------

    async def ensure_personal(self, user_id: str, display_name: str = "") -> Workspace:
        existing = await self.store.personal_workspace(user_id)
        if existing is not None:
            return existing
        now = self.clock.now()
        ws = Workspace(
            id=new_workspace_id(), kind=WorkspaceKind.PERSONAL,
            name=_personal_name(display_name), created_by=user_id, created_at=now,
        )
        try:
            await self.store.create_workspace(ws, Membership(ws.id, user_id, WorkspaceRole.OWNER, now))
        except ConflictError:
            # A concurrent first request made it; theirs is the one.
            raced = await self.store.personal_workspace(user_id)
            if raced is None:
                raise
            return raced
        return ws

    async def list_for(self, user_id: str) -> list[tuple[Workspace, Membership]]:
        await self.ensure_personal(user_id)
        return await self.store.memberships_of(user_id)

    async def resolve(self, user_id: str, workspace_id: str) -> tuple[Workspace, Membership]:
        if not is_workspace_id(workspace_id):
            raise AuthorizationError(_NOT_A_MEMBER)
        membership = await self.store.get_membership(workspace_id, user_id)
        workspace = await self.store.get_workspace(workspace_id) if membership else None
        if membership is None or workspace is None:
            raise AuthorizationError(_NOT_A_MEMBER)
        return workspace, membership

    async def list_members(self, actor: str, workspace_id: str) -> list[Membership]:
        await self.resolve(actor, workspace_id)
        return await self.store.list_members(workspace_id)

    async def list_invites(self, actor: str, workspace_id: str) -> list[Invite]:
        _ws, mine = await self._team(actor, workspace_id)
        self._require(mine.role, "invite")
        now = self.clock.now()
        return [i for i in await self.store.list_invites(workspace_id) if i.is_open(now)]

    async def sole_ownerships(self, user_id: str) -> list[Workspace]:
        """Team workspaces this person owns -- what blocks deleting their account."""
        return [
            ws for ws, m in await self.store.memberships_of(user_id)
            if ws.kind is WorkspaceKind.TEAM and m.role is WorkspaceRole.OWNER
        ]

    # -- changing -------------------------------------------------------------------------

    async def create_team(self, user_id: str, name: str) -> Workspace:
        now = self.clock.now()
        ws = Workspace(
            id=new_workspace_id(), kind=WorkspaceKind.TEAM, name=clean_workspace_name(name),
            created_by=user_id, created_at=now,
        )
        await self.store.create_workspace(ws, Membership(ws.id, user_id, WorkspaceRole.OWNER, now))
        await self._audit("create", user_id, WorkspaceRole.OWNER, ws.id)
        return ws

    async def invite(
        self, actor: str, workspace_id: str, email: str, role: WorkspaceRole
    ) -> tuple[Invite, str]:
        _ws, mine = await self._team(actor, workspace_id)
        self._require(mine.role, "invite")
        role = WorkspaceRole(role)
        if role is WorkspaceRole.OWNER:
            raise ValidationError("invite as admin or member; ownership is transferred, not invited")
        address = _clean_email(email)
        now = self.clock.now()
        members = await self.store.list_members(workspace_id)
        open_invites = [i for i in await self.store.list_invites(workspace_id) if i.is_open(now)]
        if len(members) + len(open_invites) >= await self._seats(workspace_id):
            raise ConflictError("this workspace has no free seats")
        invite_id, token = new_invite_token()
        invite = Invite(
            invite_id=invite_id, workspace_id=workspace_id, email=address, role=role,
            digest=invite_digest(token), created_by=actor, created_at=now, expires_at=now + INVITE_TTL,
        )
        await self.store.put_invite(invite)
        await self._audit("invite", actor, mine.role, workspace_id, {"invite_id": invite_id, "role": role.value})
        return invite, token

    async def accept(self, user_id: str, token: str) -> Membership:
        invite_id = parse_invite_token(token)
        invite = await self.store.get_invite(invite_id)
        if invite is None or not hmac.compare_digest(invite.digest, invite_digest(token)):
            raise ConflictError("this invite is no longer valid")
        member = await self.store.accept_invite(
            invite_id, user_id=user_id, at=self.clock.now(), seat_limit=await self._seats(invite.workspace_id)
        )
        await self._audit("join", user_id, member.role, member.workspace_id, {"invite_id": invite_id})
        return member

    async def revoke_invite(self, actor: str, workspace_id: str, invite_id: str) -> None:
        _ws, mine = await self._team(actor, workspace_id)
        self._require(mine.role, "invite")
        invite = await self.store.get_invite(invite_id)
        if invite is None or invite.workspace_id != workspace_id:
            raise ConflictError("no such invite")
        await self.store.revoke_invite(invite_id)
        await self._audit("revoke_invite", actor, mine.role, workspace_id, {"invite_id": invite_id})

    async def remove(self, actor: str, workspace_id: str, user_id: str) -> None:
        if actor == user_id:
            await self.leave(actor, workspace_id)
            return
        _ws, mine = await self._team(actor, workspace_id)
        target = await self.store.get_membership(workspace_id, user_id)
        if target is None:
            raise ConflictError("no such member")
        if not can(mine.role, "remove_member", target_role=target.role):
            raise AuthorizationError("your role cannot remove this member")
        if not await self.store.remove_member(workspace_id, user_id, expected=target.role):
            raise ConflictError("this member's role changed; reload and try again")
        await self._audit("remove_member", actor, mine.role, workspace_id, {"member": user_id})

    async def leave(self, user_id: str, workspace_id: str) -> None:
        _ws, mine = await self._team(user_id, workspace_id)
        if mine.role is WorkspaceRole.OWNER:
            raise ConflictError("transfer ownership first")
        if not await self.store.remove_member(workspace_id, user_id, expected=mine.role):
            raise ConflictError("your role changed; reload and try again")
        await self._audit("leave", user_id, mine.role, workspace_id)

    async def change_role(self, actor: str, workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
        _ws, mine = await self._team(actor, workspace_id)
        role = WorkspaceRole(role)
        target = await self.store.get_membership(workspace_id, user_id)
        if target is None:
            raise ConflictError("no such member")
        if role is WorkspaceRole.OWNER or target.role is WorkspaceRole.OWNER:
            raise ValidationError("ownership changes only by transfer")
        if not can(mine.role, "change_role", target_role=target.role):
            raise AuthorizationError("your role cannot change this member's role")
        await self.store.set_role(workspace_id, user_id, role, expected=target.role)
        await self._audit("change_role", actor, mine.role, workspace_id, {"member": user_id, "role": role.value})

    async def transfer(self, actor: str, workspace_id: str, to_user: str) -> None:
        _ws, mine = await self._team(actor, workspace_id)
        self._require(mine.role, "transfer")
        if to_user == actor or await self.store.get_membership(workspace_id, to_user) is None:
            raise ConflictError("ownership can only pass to another member")
        await self.store.transfer_ownership(workspace_id, actor, to_user)
        await self._audit("transfer", actor, mine.role, workspace_id, {"to": to_user})

    async def delete_team(self, actor: str, workspace_id: str) -> None:
        _ws, mine = await self._team(actor, workspace_id)
        self._require(mine.role, "delete")
        await self.store.delete_workspace(workspace_id)
        await self._audit("delete", actor, mine.role, workspace_id)

    # -- helpers --------------------------------------------------------------------------

    async def _team(self, user_id: str, workspace_id: str) -> tuple[Workspace, Membership]:
        ws, mine = await self.resolve(user_id, workspace_id)
        if ws.kind is not WorkspaceKind.TEAM:
            raise ValidationError("a personal workspace has no members to manage")
        return ws, mine

    @staticmethod
    def _require(role: WorkspaceRole, action: str) -> None:
        if not can(role, action):
            raise AuthorizationError(f"your role cannot {action.replace('_', ' ')} in this workspace")

    async def _audit(
        self, verb: str, actor: str, role: WorkspaceRole, workspace_id: str, detail: dict | None = None
    ) -> None:
        await self.audit.record(AuditEvent(
            action=f"workspace.{verb}", actor=actor, actor_role=f"workspace:{role.value}",
            resource=f"workspace:{workspace_id}", outcome="ok", at=self.clock.now(),
            detail=dict(detail or {}),
        ))


def _personal_name(display_name: str) -> str:
    try:
        return clean_workspace_name(display_name) if display_name else "Personal"
    except ValidationError:
        return "Personal"


def _clean_email(email: str) -> str:
    address = (email or "").strip().lower()
    local, _, domain = address.partition("@")
    if not local or "." not in domain or len(address) > _MAX_EMAIL or any(c.isspace() for c in address):
        raise ValidationError("a valid email address is required")
    return address
