"""Billing routes, audit fixes (stream B2): F38 cancel is throttled per person, F57 store-token
answers a stranger 403 whatever the workspace's payments are doing."""

from __future__ import annotations

from dataclasses import replace

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_service import _build_authenticator


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        person = credentials.credentials
        return {"uid": person, "email": f"{person}@example.com", "email_verified": True}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: "User")


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
    return next(w["id"] for w in client.get("/api/v3/workspaces", headers=_as(person)).json()["workspaces"]
                if w["kind"] == "personal")


def test_cancel_is_rate_limited_per_person(client, enabled) -> None:
    ws = _personal(client, "ann")
    answers = [client.post(f"/api/v3/workspaces/{ws}/billing/cancel", headers=_as("ann")).status_code
               for _ in range(8)]
    assert answers[0] == 409 and 429 in answers   # nothing to cancel, then throttled
    other = _personal(client, "bob")
    assert client.post(f"/api/v3/workspaces/{other}/billing/cancel", headers=_as("bob")).status_code == 409


def test_store_token_answers_a_stranger_403_even_with_a_payment_waiting(client, enabled) -> None:
    ws = _personal(client, "ann")
    checkout = client.post(f"/api/v3/workspaces/{ws}/billing/checkout", headers=_as("ann"), json={"plan": "pro", "seats": 1})
    assert checkout.status_code == 201, checkout.text
    stranger = client.post(f"/api/v3/workspaces/{ws}/billing/store-token", headers=_as("mallory"))
    assert stranger.status_code == 403, stranger.text
    owner = client.post(f"/api/v3/workspaces/{ws}/billing/store-token", headers=_as("ann"))
    assert owner.status_code == 409   # the Owner may learn their own workspace's payment state
