"""SaaS P1-C: PlanService -- which plan a workspace has and what it may still use."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from aura_core.adapters.plans import InMemoryEntitlementStore, InMemoryUsageMeter
from aura_core.adapters.runtime import InMemoryAuditSink
from aura_core.adapters.tenancy import InMemoryTenancyStore
from aura_core.config.settings import TenancySettings
from aura_core.domain.errors import ConflictError, ValidationError
from aura_core.domain.plans import DEFAULT_CATALOGUE, PlanLimitError
from aura_core.domain.tenancy import WorkspaceContext, WorkspaceKind, WorkspaceRole
from aura_core.plans import PlanService
from aura_core.tenancy import TenancyService

MEMBER = WorkspaceRole.MEMBER


@pytest.fixture
def audit(clock):
    return InMemoryAuditSink(clock=clock)


@pytest.fixture
def services(clock, audit):
    tenancy = TenancyService(store=InMemoryTenancyStore(), clock=clock, audit=audit,
                             settings=TenancySettings(default_team_seats=5))
    plans = PlanService(entitlements=InMemoryEntitlementStore(), meter=InMemoryUsageMeter(),
                        tenancy=tenancy, clock=clock, audit=audit, catalogue=DEFAULT_CATALOGUE)
    tenancy.seat_limit = plans.seat_limit
    return tenancy, plans


async def _personal(tenancy, user="u1"):
    return (await tenancy.ensure_personal(user)).id


async def test_free_by_default(services) -> None:
    tenancy, plans = services
    plan, seats = await plans.entitlement(await _personal(tenancy))
    assert plan.id == "free" and seats == 1


async def test_messages_refused_at_limit_with_reset_time(services, clock) -> None:
    tenancy, plans = services
    ws = await _personal(tenancy)
    for _ in range(50):
        await plans.admit_message(ws)
    with pytest.raises(PlanLimitError) as refused:
        await plans.admit_message(ws)
    details = refused.value.details
    assert details["limit"] == "messages" and details["used"] == 50 and details["allowed"] == 50
    assert details["resets_at"].startswith(f"{clock.now().year + (clock.now().month == 12):04d}")


async def test_release_returns_the_unit(services) -> None:
    tenancy, plans = services
    ws = await _personal(tenancy)
    reservation = await plans.admit_message(ws)
    await plans.release(reservation)
    await plans.release(reservation)  # idempotent
    assert (await plans.usage(ws))["messages_used"] == 0


async def test_engineer_refused_on_free_allowed_on_pro(services) -> None:
    tenancy, plans = services
    ws = await _personal(tenancy)
    with pytest.raises(PlanLimitError) as refused:
        await plans.require_engineer(ws)
    assert refused.value.details["limit"] == "engineer"
    await plans.set_plan("sa@example.com", ws, "pro", 1)
    await plans.require_engineer(ws)


async def test_storage_admit_and_free(services) -> None:
    tenancy, plans = services
    ws = await _personal(tenancy)
    limit = DEFAULT_CATALOGUE["free"].storage_bytes
    await plans.admit_storage(ws, limit - 10)
    with pytest.raises(PlanLimitError):
        await plans.admit_storage(ws, 11)
    await plans.free_storage(ws, limit)  # more than used: floors at 0
    assert (await plans.usage(ws))["storage_used"] == 0


async def test_set_plan_validates_kind_and_seats_and_audits(services, audit) -> None:
    tenancy, plans = services
    personal = await _personal(tenancy)
    team = (await tenancy.create_team("u1", "Acme")).id
    with pytest.raises(ValidationError):
        await plans.set_plan("sa", personal, "team", 2)   # team plan on a personal workspace
    with pytest.raises(ValidationError):
        await plans.set_plan("sa", team, "pro", 1)        # pro is personal-only
    with pytest.raises(ValidationError):
        await plans.set_plan("sa", team, "team", 1)       # below min seats
    with pytest.raises(ValidationError):
        await plans.set_plan("sa", team, "gold", 2)       # unknown plan
    entitlement = await plans.set_plan("sa", team, "team", 4)
    assert (entitlement.plan_id, entitlement.seats, entitlement.source) == ("team", 4, "manual")
    events = await audit.query(action="plan.set")
    assert events and events[0].resource == f"workspace:{team}"


async def test_downgrade_applies_next_request_and_never_negative(services) -> None:
    tenancy, plans = services
    ws = await _personal(tenancy)
    await plans.set_plan("sa", ws, "pro", 1)
    for _ in range(60):
        await plans.admit_message(ws)
    await plans.set_plan("sa", ws, "free", 1)  # now 60 used against 50
    with pytest.raises(PlanLimitError) as refused:
        await plans.admit_message(ws)
    assert refused.value.details["used"] == 60 and refused.value.details["allowed"] == 50


async def test_team_seats_follow_plan(services) -> None:
    tenancy, plans = services
    team = await tenancy.create_team("owner", "Acme")
    assert await plans.seat_limit(team.id) == DEFAULT_CATALOGUE["free"].max_seats   # 3
    for person in ("a", "b"):
        _invite, token = await tenancy.invite("owner", team.id, f"{person}@example.com", MEMBER)
        await tenancy.accept(person, token)
    with pytest.raises(PlanLimitError):
        await tenancy.invite("owner", team.id, "c@example.com", MEMBER)
    await plans.set_plan("sa", team.id, "team", 4)
    _invite, token = await tenancy.invite("owner", team.id, "c@example.com", MEMBER)
    await tenancy.accept("c", token)
    await plans.set_plan("sa", team.id, "team", 2)   # below current members: keep them, refuse new
    assert len(await tenancy.list_members("owner", team.id)) == 4
    with pytest.raises(PlanLimitError):
        await tenancy.invite("owner", team.id, "d@example.com", MEMBER)


async def test_operator_and_service_unmetered(services) -> None:
    tenancy, plans = services
    operator = SimpleNamespace(role="Developer", is_service=False, user_id="op", workspace=None)
    service = SimpleNamespace(role="service", is_service=True, user_id="svc", workspace=None)
    customer = SimpleNamespace(role="User", is_service=False, user_id="u9", workspace=None)
    assert await plans.metered_workspace(operator) is None
    assert await plans.metered_workspace(service) is None
    personal = await plans.metered_workspace(customer)
    assert personal == (await tenancy.ensure_personal("u9")).id
    team = await tenancy.create_team("u9", "Nine")
    in_team = SimpleNamespace(role="User", is_service=False, user_id="u9",
                              workspace=WorkspaceContext(team.id, WorkspaceKind.TEAM, WorkspaceRole.OWNER))
    assert await plans.metered_workspace(in_team) == team.id
