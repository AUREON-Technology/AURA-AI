"""SaaS P1-D2: in-app subscriptions over HTTP -- the binding token, Apple's and Google's notifications."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import timedelta

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_core.adapters.apple_store import AppleNotificationVerifier
from aura_core.adapters.google_play import GooglePushVerifier, PlaySubscription
from aura_core.domain.errors import DependencyUnavailableError
from aura_service import _build_authenticator
from tests.unit.test_apple_store_verifier import BUNDLE, NOW, Chain

SA = "push@aura.iam.gserviceaccount.com"
AUD = "https://api.example.test/api/v3/billing/google/notify"
PKG = "tech.aureon.app"


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        person = credentials.credentials
        return {"uid": person, "email": f"{person}@example.com", "email_verified": not person.startswith("unverified")}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: "User")


class _Play:
    package_name = PKG

    def __init__(self) -> None:
        self.states: dict = {}
        self.down = False

    async def subscription(self, token):
        if self.down:
            raise DependencyUnavailableError("down", dependency="google_play")
        return self.states[token]

    async def acknowledge(self, product_id, token):
        return None


@pytest.fixture
def chain():
    return Chain()


@pytest.fixture
def stores(container, clock, chain):
    clock.advance((NOW - clock.now()).total_seconds())
    container.plans.clock = container.tenancy.clock = container.clock = clock
    container.billing.clock = container.store_billing.clock = clock
    container.billing.settings = replace(container.billing.settings, enabled=True)
    stores = container.store_billing
    stores.products = {"apple": {"aura.pro.monthly": "pro"}, "google": {"aura_pro": "pro"}}
    stores.apple = AppleNotificationVerifier(BUNDLE, root_der=chain.root_der, clock=clock)

    def google_says(token, audience):
        if token != "google-signed":
            raise ValueError("bad token")
        return {"email": SA, "email_verified": True, "aud": audience}

    stores.google_push = GooglePushVerifier(AUD, SA, verify_token=google_says)
    stores.google_play = _Play()
    return stores


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


def _token(client, person="ann") -> tuple[str, str]:
    ws = _personal(client, person)
    response = client.post(f"/api/v3/workspaces/{ws}/billing/store-token", headers=_as(person))
    assert response.status_code == 201, response.text
    return ws, response.json()["token"]


def _signed(chain, token, kind="SUBSCRIBED", uuid="n-1", days=30) -> bytes:
    tx = chain.sign({"bundleId": BUNDLE, "environment": "Sandbox", "productId": "aura.pro.monthly",
                     "originalTransactionId": "1", "transactionId": "1", "appAccountToken": token,
                     "expiresDate": int((NOW + timedelta(days=days)).timestamp() * 1000)})
    payload = chain.sign({"notificationType": kind, "notificationUUID": uuid, "signedDate": int(NOW.timestamp() * 1000),
                          "data": {"bundleId": BUNDLE, "environment": "Sandbox", "signedTransactionInfo": tx}})
    return json.dumps({"signedPayload": payload}).encode()


def test_apple_subscribe_over_http(client, stores, chain) -> None:
    ws, token = _token(client)
    first = client.post("/api/v3/billing/apple/notify", content=_signed(chain, token))
    assert first.status_code == 200 and first.json() == {"ok": True, "result": "applied"}
    assert client.get(f"/api/v3/workspaces/{ws}/plan", headers=_as("ann")).json()["plan"] == "pro"
    assert client.post("/api/v3/billing/apple/notify", content=_signed(chain, token)).json()["result"] == "duplicate"


def test_apple_forged_and_malformed(client, stores, chain) -> None:
    ws, token = _token(client)
    assert client.post("/api/v3/billing/apple/notify", content=_signed(Chain(), token)).status_code == 403
    for body in (b"", b"[]", b"{}", b'{"signedPayload": 5}', b'{"signedPayload": "a.b.c"}'):
        assert client.post("/api/v3/billing/apple/notify", content=body).status_code in (400, 403), body
    assert client.post("/api/v3/billing/apple/notify", content=b"x" * 70_000).status_code == 413
    assert client.get(f"/api/v3/workspaces/{ws}/plan", headers=_as("ann")).json()["plan"] == "free"


def _push(token: str, message_id: str = "m-1") -> bytes:
    data = {"packageName": PKG, "subscriptionNotification": {"notificationType": 4, "purchaseToken": token}}
    return json.dumps({"message": {"messageId": message_id, "data": base64.b64encode(json.dumps(data).encode()).decode()}}).encode()


def test_google_push_over_http(client, stores, clock) -> None:
    ws, token = _token(client, "gus")
    stores.google_play.states["p1"] = PlaySubscription("active", "aura_pro", clock.now() + timedelta(days=30), token, None, True)
    signed = {"authorization": "Bearer google-signed"}
    assert client.post("/api/v3/billing/google/notify", content=_push("p1")).status_code == 403
    assert client.post("/api/v3/billing/google/notify", content=_push("p1"), headers={"authorization": "Bearer x"}).status_code == 403
    # Permanently bad: acknowledged (200 "ignored"), or Pub/Sub would redeliver it for a week.
    bad = client.post("/api/v3/billing/google/notify", content=b"{}", headers=signed)
    assert bad.status_code == 200 and bad.json()["result"] == "ignored"
    stores.google_play.down = True
    assert client.post("/api/v3/billing/google/notify", content=_push("p1"), headers=signed).status_code == 503
    stores.google_play.down = False
    ok = client.post("/api/v3/billing/google/notify", content=_push("p1"), headers=signed)
    assert ok.status_code == 200 and ok.json()["result"] == "applied"
    assert client.get(f"/api/v3/workspaces/{ws}/plan", headers=_as("gus")).json()["plan"] == "pro"


def test_store_token_rules(client, stores) -> None:
    team = client.post("/api/v3/workspaces", headers=_as("own"), json={"name": "T"}).json()["id"]
    assert client.post(f"/api/v3/workspaces/{team}/billing/store-token", headers=_as("own")).status_code == 422
    assert client.post(f"/api/v3/workspaces/{team}/billing/store-token", headers=_as("mallory")).status_code == 403
    ws = _personal(client, "unverified-uma")
    assert client.post(f"/api/v3/workspaces/{ws}/billing/store-token", headers=_as("unverified-uma")).status_code == 403


def test_unconfigured_store_and_disabled_billing_are_503(client, container) -> None:
    assert client.post("/api/v3/billing/apple/notify", content=b"{}").status_code == 503  # billing off
    container.billing.settings = replace(container.billing.settings, enabled=True)
    container.store_billing.apple = None
    container.store_billing.google_push = None
    assert client.post("/api/v3/billing/apple/notify", content=b"{}").status_code == 503
    assert client.post("/api/v3/billing/google/notify", content=b"{}").status_code == 503
