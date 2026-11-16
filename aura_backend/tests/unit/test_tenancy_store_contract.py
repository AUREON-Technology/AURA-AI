"""SaaS P1-A: the TenancyStore contract, run against every adapter.

The in-memory store runs by default; the Postgres one is added under the ``load`` marker
(it needs a real Postgres built with ``alembic upgrade head``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.tenancy import InMemoryTenancyStore
from aura_core.domain.errors import ConflictError
from aura_core.domain.tenancy import (
    INVITE_TTL,
    Invite,
    Membership,
    Workspace,
    WorkspaceKind,
    WorkspaceRole,
    invite_digest,
    new_invite_token,
    new_workspace_id,
)

T0 = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
OWNER, ADMIN, MEMBER = WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER

STORE_FACTORIES = [pytest.param(InMemoryTenancyStore, id="memory")]


@pytest.fixture(params=STORE_FACTORIES)
async def store(request):
    made = request.param()
    if hasattr(made, "__aenter__"):
        async with made as entered:
            yield entered
    else:
        yield made


async def _workspace(store, owner: str, kind=WorkspaceKind.TEAM, name="Team") -> Workspace:
    ws = Workspace(id=new_workspace_id(), kind=kind, name=name, created_by=owner, created_at=T0)
    await store.create_workspace(ws, Membership(ws.id, owner, OWNER, T0))
    return ws


async def _invite(store, ws: Workspace, *, role=MEMBER, created_at=T0) -> Invite:
    invite_id, token = new_invite_token()
    invite = Invite(
        invite_id=invite_id, workspace_id=ws.id, email="new@example.com", role=role,
        digest=invite_digest(token), created_by=ws.created_by, created_at=created_at,
        expires_at=created_at + INVITE_TTL,
    )
    await store.put_invite(invite)
    return invite


async def test_one_personal_workspace_per_user(store) -> None:
    first = await _workspace(store, "u1", kind=WorkspaceKind.PERSONAL, name="Personal")
    assert (await store.personal_workspace("u1")) == first
    with pytest.raises(ConflictError):
        await _workspace(store, "u1", kind=WorkspaceKind.PERSONAL, name="Again")
    # A team does not count against the personal limit.
    await _workspace(store, "u1")
    assert await store.personal_workspace("u2") is None


async def test_memberships_of_lists_only_own(store) -> None:
    mine = await _workspace(store, "u1")
    await _workspace(store, "u2")
    listed = await store.memberships_of("u1")
    assert [(ws.id, m.role) for ws, m in listed] == [(mine.id, OWNER)]
    assert (await store.get_membership(mine.id, "u2")) is None


async def test_accept_invite_adds_member_once(store) -> None:
    ws = await _workspace(store, "u1")
    invite = await _invite(store, ws, role=ADMIN)
    member = await store.accept_invite(invite.invite_id, user_id="u2", at=T0 + timedelta(hours=1), seat_limit=5)
    assert member.role is ADMIN and member.invited_by == "u1"
    stored = await store.get_invite(invite.invite_id)
    assert stored.accepted_by == "u2" and stored.accepted_at is not None
    with pytest.raises(ConflictError):
        await store.accept_invite(invite.invite_id, user_id="u3", at=T0 + timedelta(hours=2), seat_limit=5)
    assert {m.user_id for m in await store.list_members(ws.id)} == {"u1", "u2"}


async def test_accept_refuses_an_existing_member(store) -> None:
    ws = await _workspace(store, "u1")
    invite = await _invite(store, ws)
    with pytest.raises(ConflictError):
        await store.accept_invite(invite.invite_id, user_id="u1", at=T0, seat_limit=5)


async def test_accept_respects_seat_limit(store) -> None:
    ws = await _workspace(store, "u1")
    first, second = await _invite(store, ws), await _invite(store, ws)
    await store.accept_invite(first.invite_id, user_id="u2", at=T0, seat_limit=2)
    with pytest.raises(ConflictError):
        await store.accept_invite(second.invite_id, user_id="u3", at=T0, seat_limit=2)
    assert (await store.get_invite(second.invite_id)).accepted_at is None


async def test_accept_expired_invite_refused(store) -> None:
    ws = await _workspace(store, "u1")
    invite = await _invite(store, ws)
    with pytest.raises(ConflictError):
        await store.accept_invite(invite.invite_id, user_id="u2", at=T0 + INVITE_TTL, seat_limit=5)


async def test_accept_unknown_invite_refused(store) -> None:
    with pytest.raises(ConflictError):
        await store.accept_invite("0" * 16, user_id="u2", at=T0, seat_limit=5)


async def test_revoked_invite_cannot_be_accepted(store) -> None:
    ws = await _workspace(store, "u1")
    invite = await _invite(store, ws)
    assert await store.revoke_invite(invite.invite_id) is True
    assert await store.revoke_invite(invite.invite_id) is False
    with pytest.raises(ConflictError):
        await store.accept_invite(invite.invite_id, user_id="u2", at=T0, seat_limit=5)
    assert await store.list_invites(ws.id) == []


async def test_transfer_swaps_roles(store) -> None:
    ws = await _workspace(store, "u1")
    await store.add_member(Membership(ws.id, "u2", MEMBER, T0, invited_by="u1"))
    await store.transfer_ownership(ws.id, "u1", "u2")
    roles = {m.user_id: m.role for m in await store.list_members(ws.id)}
    assert roles == {"u1": ADMIN, "u2": OWNER}


async def test_set_role_and_remove(store) -> None:
    ws = await _workspace(store, "u1")
    await store.add_member(Membership(ws.id, "u2", MEMBER, T0))
    with pytest.raises(ConflictError):
        await store.add_member(Membership(ws.id, "u2", MEMBER, T0))
    await store.set_role(ws.id, "u2", ADMIN)
    assert (await store.get_membership(ws.id, "u2")).role is ADMIN
    assert await store.remove_member(ws.id, "u2") is True
    assert await store.remove_member(ws.id, "u2") is False


async def test_delete_workspace_cascades(store) -> None:
    ws = await _workspace(store, "u1")
    await store.add_member(Membership(ws.id, "u2", MEMBER, T0))
    invite = await _invite(store, ws)
    await store.delete_workspace(ws.id)
    assert await store.get_workspace(ws.id) is None
    assert await store.memberships_of("u2") == []
    assert await store.get_invite(invite.invite_id) is None
