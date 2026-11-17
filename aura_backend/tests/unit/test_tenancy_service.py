"""SaaS P1-A: TenancyService -- personal workspaces, team roles, invites, seats and audit."""

from __future__ import annotations

import pytest

from aura_core.adapters.runtime import InMemoryAuditSink
from aura_core.adapters.tenancy import InMemoryTenancyStore
from aura_core.config.settings import TenancySettings
from aura_core.domain.errors import AuthorizationError, ConflictError, ValidationError
from aura_core.domain.plans import PlanLimitError
from aura_core.domain.tenancy import WorkspaceKind, WorkspaceRole
from aura_core.tenancy import TenancyService

OWNER, ADMIN, MEMBER = WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER


@pytest.fixture
def audit(clock) -> InMemoryAuditSink:
    return InMemoryAuditSink(clock=clock)


@pytest.fixture
def service(clock, audit) -> TenancyService:
    return TenancyService(
        store=InMemoryTenancyStore(), clock=clock, audit=audit, settings=TenancySettings(default_team_seats=3)
    )


async def _team_with(service, *members: tuple[str, WorkspaceRole]):
    ws = await service.create_team("owner", "Acme")
    for user, role in members:
        _invite, token = await service.invite("owner", ws.id, f"{user}@example.com", role)
        await service.accept(user, token)
    return ws


async def test_personal_created_once(service) -> None:
    first = await service.ensure_personal("u1")
    again = await service.ensure_personal("u1")
    assert first == again and first.kind is WorkspaceKind.PERSONAL
    listed = await service.list_for("u1")
    assert [(ws.id, m.role) for ws, m in listed] == [(first.id, OWNER)]


async def test_non_member_and_missing_workspace_same_error(service) -> None:
    ws = await service.create_team("owner", "Acme")
    with pytest.raises(AuthorizationError) as foreign:
        await service.resolve("stranger", ws.id)
    with pytest.raises(AuthorizationError) as missing:
        await service.resolve("stranger", "0" * 32)
    with pytest.raises(AuthorizationError) as malformed:
        await service.resolve("stranger", "not-an-id")
    assert str(foreign.value) == str(missing.value) == str(malformed.value)
    workspace, membership = await service.resolve("owner", ws.id)
    assert workspace.id == ws.id and membership.role is OWNER


async def test_admin_can_invite_member_not_owner(service) -> None:
    ws = await _team_with(service, ("admin1", ADMIN))
    invite, token = await service.invite("admin1", ws.id, "Someone@Example.com", MEMBER)
    assert invite.email == "someone@example.com" and token.startswith("auraiv_")
    with pytest.raises(ValidationError):
        await service.invite("admin1", ws.id, "boss@example.com", OWNER)


async def test_member_cannot_invite(service) -> None:
    ws = await _team_with(service, ("m1", MEMBER))
    with pytest.raises(AuthorizationError):
        await service.invite("m1", ws.id, "x@example.com", MEMBER)


async def test_seat_limit_counts_open_invites(service) -> None:
    ws = await _team_with(service, ("m1", MEMBER))  # 2 of 3 seats taken
    await service.invite("owner", ws.id, "third@example.com", MEMBER)  # 3rd seat held by an open invite
    with pytest.raises(PlanLimitError):
        await service.invite("owner", ws.id, "fourth@example.com", MEMBER)


async def test_accept_wrong_token_secret_refused(service) -> None:
    ws = await service.create_team("owner", "Acme")
    invite, token = await service.invite("owner", ws.id, "x@example.com", MEMBER)
    forged = f"auraiv_{invite.invite_id}_{'A' * 43}"
    with pytest.raises(ConflictError):
        await service.accept("x", forged)
    with pytest.raises(ValidationError):
        await service.accept("x", "not-a-token")
    assert (await service.accept("x", token)).role is MEMBER


async def test_owner_cannot_leave_until_transfer(service) -> None:
    ws = await _team_with(service, ("m1", MEMBER))
    with pytest.raises(ConflictError):
        await service.leave("owner", ws.id)
    with pytest.raises(AuthorizationError):
        await service.transfer("m1", ws.id, "m1")
    await service.transfer("owner", ws.id, "m1")
    await service.leave("owner", ws.id)
    with pytest.raises(AuthorizationError):
        await service.resolve("owner", ws.id)
    assert (await service.resolve("m1", ws.id))[1].role is OWNER


async def test_admin_cannot_remove_admin(service) -> None:
    ws = await _team_with(service, ("a1", ADMIN), ("a2", ADMIN))
    with pytest.raises(AuthorizationError):
        await service.remove("a1", ws.id, "a2")
    with pytest.raises(AuthorizationError):
        await service.remove("a1", ws.id, "owner")
    with pytest.raises(AuthorizationError):
        await service.change_role("a1", ws.id, "a2", MEMBER)
    await service.remove("owner", ws.id, "a2")
    with pytest.raises(AuthorizationError):
        await service.resolve("a2", ws.id)


async def test_roles_never_change_to_or_from_owner(service) -> None:
    ws = await _team_with(service, ("m1", MEMBER))
    with pytest.raises(ValidationError):
        await service.change_role("owner", ws.id, "m1", OWNER)
    with pytest.raises(ValidationError):
        await service.change_role("owner", ws.id, "owner", MEMBER)
    await service.change_role("owner", ws.id, "m1", ADMIN)
    assert (await service.resolve("m1", ws.id))[1].role is ADMIN


async def test_personal_workspace_cannot_be_invited_into_or_deleted(service) -> None:
    personal = await service.ensure_personal("u1")
    with pytest.raises(ValidationError):
        await service.invite("u1", personal.id, "x@example.com", MEMBER)
    with pytest.raises(ValidationError):
        await service.delete_team("u1", personal.id)
    with pytest.raises(ValidationError):
        await service.leave("u1", personal.id)


async def test_delete_team_owner_only(service) -> None:
    ws = await _team_with(service, ("a1", ADMIN))
    with pytest.raises(AuthorizationError):
        await service.delete_team("a1", ws.id)
    await service.delete_team("owner", ws.id)
    with pytest.raises(AuthorizationError):
        await service.resolve("owner", ws.id)


async def test_sole_ownership_lists_team_workspaces(service) -> None:
    ws = await service.create_team("owner", "Acme")
    await service.ensure_personal("owner")
    assert [w.id for w in await service.sole_ownerships("owner")] == [ws.id]


async def test_mutations_are_audited_without_tokens(service, audit) -> None:
    ws = await service.create_team("owner", "Acme")
    _invite, token = await service.invite("owner", ws.id, "x@example.com", MEMBER)
    await service.accept("x", token)
    await service.remove("owner", ws.id, "x")
    events = await audit.query(limit=50)
    actions = {e.action for e in events}
    assert {"workspace.create", "workspace.invite", "workspace.join", "workspace.remove_member"} <= actions
    for event in events:
        assert event.resource == f"workspace:{ws.id}"
        assert token not in repr(event.detail) and token.split("_")[-1] not in repr(event.detail)
        assert event.actor_role.startswith("workspace:") or event.actor_role == ""


async def test_container_wires_an_in_memory_tenancy_service(container) -> None:
    assert isinstance(container.tenancy, TenancyService)
    assert container.tenancy.settings.default_team_seats == container.settings.tenancy.default_team_seats
    ws = await container.tenancy.create_team("owner", "Acme")
    assert (await container.tenancy.resolve("owner", ws.id))[1].role is OWNER
