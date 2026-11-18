"""SaaS P1-D1: billing over HTTP -- checkout, Fawry's notification, cancel, the cycle."""

from __future__ import annotations

from dataclasses import replace

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_service import _build_authenticator

ROLES = {"sa": "SuperAdmin"}


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        person = credentials.credentials
        return {"uid": person, "email": f"{person}@example.com", "email_verified": not person.startswith("unverified")}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: ROLES.get(claims.get("uid"), "User"))


@pytest.fixture
def enabled(container):
    container.billing.settings = replace(container.billing.settings, enabled=True)
    return container.billing


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    authenticator = _build_authenticator(container.settings, service_keys=container.service_keys, tenancy=container.tenancy)
    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _as(person: str) -> dict:
    return {"authorization": f"Bearer {person}"}


def _personal(client, person: str) -> str:
    return next(w["id"] for w in client.get("/api/v3/workspaces", headers=_as(person)).json()["workspaces"] if w["kind"] == "personal")


def test_checkout_notify_then_plan_is_pro(client, container, enabled) -> None:
    ws = _personal(client, "ann")
    checkout = client.post(f"/api/v3/workspaces/{ws}/billing/checkout", headers=_as("ann"), json={"plan": "pro", "seats": 1})
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert body["reference_number"] and body["amount_piastres"] == 15000
    note = enabled.provider.notification(body["payment_id"], "paid", 15000)
    notified = client.post("/api/v3/billing/fawry/notify", content=note)
    assert notified.status_code == 200 and notified.json()["result"] == "applied"
    plan = client.get(f"/api/v3/workspaces/{ws}/plan", headers=_as("ann")).json()
    assert plan["plan"] == "pro"
    status = client.get(f"/api/v3/workspaces/{ws}/billing", headers=_as("ann")).json()
    assert status["subscription"]["status"] == "active"
    assert client.post("/api/v3/billing/fawry/notify", content=note).json()["result"] == "duplicate"


def test_non_owner_403_and_unverified_403(client, container, enabled) -> None:
    team = client.post("/api/v3/workspaces", headers=_as("own"), json={"name": "T"}).json()["id"]
    assert client.post(f"/api/v3/workspaces/{team}/billing/checkout", headers=_as("mallory"),
                       json={"plan": "team", "seats": 2}).status_code == 403
    assert client.get(f"/api/v3/workspaces/{team}/billing", headers=_as("mallory")).status_code == 403
    ws = _personal(client, "unverified-uma")
    assert client.post(f"/api/v3/workspaces/{ws}/billing/checkout", headers=_as("unverified-uma"),
                       json={"plan": "pro", "seats": 1}).status_code == 403


def test_billing_disabled_is_503(client, container) -> None:
    ws = _personal(client, "bob")
    assert client.post(f"/api/v3/workspaces/{ws}/billing/checkout", headers=_as("bob"),
                       json={"plan": "pro", "seats": 1}).status_code == 503
    assert client.post("/api/v3/billing/fawry/notify", content=b"{}").status_code == 503


def test_bad_signature_403_and_malformed_400(client, container, enabled) -> None:
    ws = _personal(client, "cat")
    payment_id = client.post(f"/api/v3/workspaces/{ws}/billing/checkout", headers=_as("cat"),
                             json={"plan": "pro", "seats": 1}).json()["payment_id"]
    forged = enabled.provider.notification(payment_id, "paid", 15000, sign=False)
    assert client.post("/api/v3/billing/fawry/notify", content=forged).status_code == 403
    assert client.post("/api/v3/billing/fawry/notify", content=b"not json").status_code == 400
    assert client.post("/api/v3/billing/fawry/notify", content=b"x" * 20000).status_code == 413
    assert client.get(f"/api/v3/workspaces/{ws}/plan", headers=_as("cat")).json()["plan"] == "free"


def test_cancel_and_run_cycle(client, container, enabled) -> None:
    ws = _personal(client, "dee")
    payment_id = client.post(f"/api/v3/workspaces/{ws}/billing/checkout", headers=_as("dee"),
                             json={"plan": "pro", "seats": 1}).json()["payment_id"]
    client.post("/api/v3/billing/fawry/notify", content=enabled.provider.notification(payment_id, "paid", 15000))
    assert client.post(f"/api/v3/workspaces/{ws}/billing/cancel", headers=_as("dee")).status_code == 204
    assert client.get(f"/api/v3/workspaces/{ws}/billing", headers=_as("dee")).json()["subscription"]["status"] == "canceled"
    assert client.post("/api/v3/admin/billing/run-cycle", headers=_as("dee")).status_code == 403
    ran = client.post("/api/v3/admin/billing/run-cycle", headers=_as("sa"))
    assert ran.status_code == 200 and set(ran.json()) >= {"renewals", "advanced", "expired", "reconciled", "errors"}


def test_the_fawry_secure_key_never_appears_in_settings_output(monkeypatch) -> None:
    """/admin/status serves settings.as_dict(): the secret must not be a field it can walk into."""
    import json as _json

    from aura_core.config.settings import load_settings

    monkeypatch.setenv("AURA_FAWRY_SECURE_KEY", "very-secret-fawry-key")
    monkeypatch.setenv("AURA_FAWRY_MERCHANT_CODE", "M1")
    settings = load_settings(refresh=True, validate=False)
    assert settings.billing.fawry_configured
    for redact in (True, False):
        assert "very-secret-fawry-key" not in _json.dumps(settings.as_dict(redact=redact))


def test_billing_status_lists_the_pro_store_products(client, container, enabled) -> None:
    """SaaS P1-F2b: the app reads which store product sells Pro from the server, not a build."""
    container.store_billing.products = {"apple": {"aura.pro.monthly": "pro"}, "google": {}}
    ws = _personal(client, "ann")
    body = client.get(f"/api/v3/workspaces/{ws}/billing", headers=_as("ann")).json()
    assert body["store_products"] == {"apple": ["aura.pro.monthly"]}
