"""Tenancy audit fixes (stream B2): F9 an invite never outlives its inviter's authority, F10
deleting a team erases its shared knowledge, F41 an Owner transfer tells billing first."""

from __future__ import annotations

import pytest

from aura_core.adapters.runtime import InMemoryAuditSink
from aura_core.adapters.tenancy import InMemoryTenancyStore
from aura_core.config.settings import TenancySettings
from aura_core.domain.errors import ConflictError
from aura_core.domain.identifiers import UserId
from aura_core.domain.tenancy import WorkspaceRole, workspace_scope
from aura_core.tenancy import TenancyService

OWNER, ADMIN, MEMBER = WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER


@pytest.fixture
def service(clock) -> TenancyService:
    return TenancyService(
        store=InMemoryTenancyStore(), clock=clock, audit=InMemoryAuditSink(clock=clock),
        settings=TenancySettings(default_team_seats=10),
    )


async def _team_with(service, *members: tuple[str, WorkspaceRole]):
    ws = await service.create_team("owner", "Acme")
    for user, role in members:
        _invite, token = await service.invite("owner", ws.id, f"{user}@example.com", role)
        await service.accept(user, token)
    return ws


async def _open_invites(service, ws_id) -> list:
    return [i for i in await service.store.list_invites(ws_id) if i.is_open(service.clock.now())]


# -- F9 -------------------------------------------------------------------------------------------


async def test_removed_admins_invite_cannot_be_accepted(service) -> None:
    ws = await _team_with(service, ("adm", ADMIN))
    _invite, token = await service.invite("adm", ws.id, "x@example.com", ADMIN)
    await service.remove("owner", ws.id, "adm")
    assert await _open_invites(service, ws.id) == []
    with pytest.raises(ConflictError):
        await service.accept("x", token)
    assert await service.store.get_membership(ws.id, "x") is None


async def test_leaving_revokes_your_open_invites_only(service) -> None:
    ws = await _team_with(service, ("adm", ADMIN))
    await service.invite("adm", ws.id, "x@example.com", MEMBER)
    keep, _ = await service.invite("owner", ws.id, "y@example.com", MEMBER)
    await service.leave("adm", ws.id)
    assert [i.invite_id for i in await _open_invites(service, ws.id)] == [keep.invite_id]


async def test_demotion_revokes_the_invites_the_new_role_could_not_send(service) -> None:
    ws = await _team_with(service, ("adm", ADMIN))
    _invite, token = await service.invite("adm", ws.id, "x@example.com", MEMBER)
    await service.change_role("owner", ws.id, "adm", MEMBER)
    assert await _open_invites(service, ws.id) == []
    with pytest.raises(ConflictError):
        await service.accept("x", token)


async def test_accept_rechecks_the_inviter_even_if_revocation_was_missed(service) -> None:
    """The invite raced the removal (or predates this fix): accept itself refuses it."""
    ws = await _team_with(service, ("adm", ADMIN), ("adm2", ADMIN))
    _invite, gone = await service.invite("adm", ws.id, "x@example.com", ADMIN)
    _invite, demoted = await service.invite("adm2", ws.id, "y@example.com", ADMIN)
    assert await service.store.remove_member(ws.id, "adm", expected=ADMIN)   # behind the service's back
    await service.store.set_role(ws.id, "adm2", MEMBER, expected=ADMIN)
    with pytest.raises(ConflictError):
        await service.accept("x", gone)
    with pytest.raises(ConflictError):
        await service.accept("y", demoted)
    assert await service.store.get_membership(ws.id, "x") is None
    assert await service.store.get_membership(ws.id, "y") is None


async def test_former_owner_keeps_invites_an_admin_may_send(service) -> None:
    ws = await _team_with(service, ("next", ADMIN))
    _invite, token = await service.invite("owner", ws.id, "x@example.com", ADMIN)
    await service.transfer("owner", ws.id, "next")       # the former Owner is now an Admin
    member = await service.accept("x", token)
    assert member.role is ADMIN


# -- F10 ------------------------------------------------------------------------------------------


async def test_deleting_a_team_erases_its_knowledge(container) -> None:
    tenancy = container.tenancy
    tenancy.erase_knowledge = container.knowledge.erase_owner
    team = await tenancy.create_team("owner", "Acme")
    other = await tenancy.create_team("owner", "Other")
    scope, other_scope = UserId(workspace_scope(team.id)), UserId(workspace_scope(other.id))
    await container.knowledge.ingest(content="Shared runbook for the team.", title="Runbook", owner_id=scope)
    await container.knowledge.ingest(content="Another team's notes.", title="Notes", owner_id=other_scope)
    assert await container.documents.list_active(owner_id=scope, limit=10)
    await tenancy.delete_team("owner", team.id)
    left = await container.documents.list_active(owner_id=scope, limit=10)
    assert [d for d in left if d.owner_id is not None and d.owner_id.value == scope.value] == []
    assert await container.documents.list_active(owner_id=other_scope, limit=10)


async def test_failed_erasure_keeps_the_team(service) -> None:
    async def broken(owner_id):
        raise RuntimeError("vector store down")

    service.erase_knowledge = broken
    ws = await service.create_team("owner", "Acme")
    with pytest.raises(RuntimeError):
        await service.delete_team("owner", ws.id)
    assert await service.store.get_workspace(ws.id) is not None   # retry-able, not orphaned


# -- F41 ------------------------------------------------------------------------------------------


async def test_transfer_tells_billing_first(service) -> None:
    told: list[str] = []

    async def owner_changing(workspace_id: str) -> None:
        told.append(workspace_id)
        assert (await service.store.get_membership(workspace_id, "owner")).role is OWNER  # before

    service.owner_changing = owner_changing
    ws = await _team_with(service, ("next", ADMIN))
    await service.transfer("owner", ws.id, "next")
    assert told == [ws.id]
