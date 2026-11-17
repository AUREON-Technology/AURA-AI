"""SaaS P1-F1: the operator's read-only Customers list and its one write, "extend grace"."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_core.domain.billing import Subscription, SubscriptionStatus
from aura_service import _build_authenticator

ROLES = {"sa": "SuperAdmin", "adm": "Admin"}


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        person = credentials.credentials
        return {"uid": person, "email": f"{person}@example.com", "email_verified": True}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: ROLES.get(claims.get("uid"), "User"))


@pytest.fixture
def client(container, clock):
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    container.plans.clock = container.tenancy.clock = container.clock = container.billing.clock = clock
    container.billing.settings = replace(container.billing.settings, enabled=True)
    authenticator = _build_authenticator(container.settings, service_keys=container.service_keys, tenancy=container.tenancy)
    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _as(person: str) -> dict:
    return {"authorization": f"Bearer {person}"}


async def _paid(container, user: str) -> str:
    billing = container.billing
    ws = (await container.tenancy.ensure_personal(user)).id
    checkout = await billing.checkout(user, ws, "pro", 1, email=None)
    await billing.handle_notification(billing.provider.notification(checkout["payment_id"], "paid", 15000))
    return ws


async def _set(container, ws: str, **changes) -> None:
    sub = await container.billing.subscriptions.get(ws)
    await container.billing.subscriptions.put(replace(sub, **changes))


async def test_customers_lists_paying_workspaces_with_last_payment(client, container) -> None:
    ws = await _paid(container, "ann")
    response = client.get("/api/v3/admin/customers", headers=_as("sa"))
    assert response.status_code == 200, response.text
    [row] = response.json()["customers"]
    assert row["workspace_id"] == ws and row["plan"] == "pro" and row["status"] == "active"
    assert row["kind"] == "personal" and row["provider"] == "fawry"
    assert row["last_payment"]["status"] == "paid" and row["last_payment"]["amount_piastres"] == 15000


async def test_pagination(client, container) -> None:
    ids = sorted([await _paid(container, u) for u in ("a1", "a2", "a3")])
    first = client.get("/api/v3/admin/customers?limit=2", headers=_as("sa")).json()
    assert [r["workspace_id"] for r in first["customers"]] == ids[:2] and first["next"] == ids[1]
    rest = client.get(f"/api/v3/admin/customers?limit=2&after={first['next']}", headers=_as("sa")).json()
    assert [r["workspace_id"] for r in rest["customers"]] == ids[2:] and rest["next"] is None


async def test_non_superadmin_403(client, container) -> None:
    ws = await _paid(container, "ann")
    for who in ("adm", "ann"):
        assert client.get("/api/v3/admin/customers", headers=_as(who)).status_code == 403
        assert client.post(f"/api/v3/admin/customers/{ws}/extend-grace", headers=_as(who), json={"days": 3}).status_code == 403


async def test_extend_grace_from_past_due_and_expired_restores_plan(client, container, clock) -> None:
    ws = await _paid(container, "ann")
    await _set(container, ws, status=SubscriptionStatus.PAST_DUE)
    ok = client.post(f"/api/v3/admin/customers/{ws}/extend-grace", headers=_as("sa"), json={"days": 5})
    assert ok.status_code == 200, ok.text
    sub = await container.billing.subscriptions.get(ws)
    assert sub.status is SubscriptionStatus.GRACE and sub.grace_until == clock.now() + timedelta(days=5)
    await _set(container, ws, status=SubscriptionStatus.EXPIRED)
    await container.plans.set_entitlement("billing", ws, "free", 1, source="subscription")
    assert client.post(f"/api/v3/admin/customers/{ws}/extend-grace", headers=_as("sa"), json={"days": 2}).status_code == 200
    assert (await container.plans.entitlement(ws))[0].id == "pro"
    assert (await container.billing.subscriptions.get(ws)).status is SubscriptionStatus.GRACE


async def test_extend_grace_refused_for_store_or_active(client, container) -> None:
    ws = await _paid(container, "ann")
    assert client.post(f"/api/v3/admin/customers/{ws}/extend-grace", headers=_as("sa"), json={"days": 3}).status_code == 409
    await _set(container, ws, status=SubscriptionStatus.PAST_DUE, provider="apple")
    assert client.post(f"/api/v3/admin/customers/{ws}/extend-grace", headers=_as("sa"), json={"days": 3}).status_code == 409
    assert client.post(f"/api/v3/admin/customers/{'0' * 32}/extend-grace", headers=_as("sa"), json={"days": 3}).status_code == 409


async def test_days_out_of_range_422(client, container) -> None:
    ws = await _paid(container, "ann")
    for days in (0, 31, "x"):
        assert client.post(f"/api/v3/admin/customers/{ws}/extend-grace", headers=_as("sa"), json={"days": days}).status_code == 422


async def test_extend_grace_is_audited(client, container) -> None:
    ws = await _paid(container, "ann")
    await _set(container, ws, status=SubscriptionStatus.GRACE)
    client.post(f"/api/v3/admin/customers/{ws}/extend-grace", headers=_as("sa"), json={"days": 4})
    events = [e for e in await container.audit.query(limit=100) if e.action == "billing.grace_extended"]
    assert events and events[0].actor == "sa" and events[0].detail["days"] == 4
