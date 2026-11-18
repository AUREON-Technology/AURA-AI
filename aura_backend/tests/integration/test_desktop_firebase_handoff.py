"""WP12: desktop sign-in through the browser -- ``/desktop/handoff`` then ``/desktop/firebase-exchange``.

Real routes and real tokens, with the container on ``app.state.aura`` as the gateway sets it. Only
the Firebase ID-token check is stood in for, as in ``test_desktop_engineer_customers.py``.
"""

from __future__ import annotations

import secrets
from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import api.v2.auth as auth_module
import services.desktop_handoff as handoff
import services.jwt_auth as jwt_auth
from api.v2.auth import router

REDIRECT = "http://127.0.0.1:53682/"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "boss@example.com")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("DEVELOPER_EMAILS", raising=False)
    monkeypatch.setattr(handoff, "get_redis_client", lambda: None)
    auth_module._reset_desktop_rate_limits()
    handoff._reset()
    yield
    auth_module._reset_desktop_rate_limits()
    handoff._reset()


@pytest.fixture
def switch(container):
    def set_to(on: bool) -> None:
        container.settings = replace(container.settings, security=replace(container.settings.security,
                                                                          engineer_for_customers=on))
    set_to(False)
    return set_to


@pytest.fixture
def client(container):
    app = FastAPI()
    app.include_router(router)
    app.state.aura = container
    return TestClient(app)


def _person(uid: str, provider: str = "google.com") -> dict:
    return {"uid": uid, "email": f"{uid}@example.com", "name": uid, "email_verified": True,
            "sign_in_provider": provider}


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, handoff.s256(verifier)


def _handoff(client, person: dict | None, *, challenge: str, redirect: str = REDIRECT):
    async def identity(request, authorization):
        if person is None:
            raise HTTPException(status_code=401, detail="Invalid ID token")
        return dict(person)

    with patch("api.v2.auth.verify_identity_token", new=identity):
        return client.post("/api/v3/auth/desktop/handoff", headers={"Authorization": "Bearer firebase-id"},
                           json={"code_challenge": challenge, "redirect_uri": redirect})


def _exchange(client, code: str, verifier: str, redirect: str = REDIRECT):
    return client.post("/api/v3/auth/desktop/firebase-exchange", json={
        "code": code, "code_verifier": verifier, "redirect_uri": redirect, "device_name": "laptop"})


def _sign_in(client, person: dict):
    verifier, challenge = _pkce()
    started = _handoff(client, person, challenge=challenge)
    assert started.status_code == 200, started.text
    return _exchange(client, started.json()["code"], verifier)


async def _plan(container, uid: str, plan: str) -> None:
    ws = (await container.tenancy.ensure_personal(uid)).id
    await container.plans.set_plan("sa", ws, plan, 1)


def test_an_operator_signs_in_with_firebase_and_gets_a_device_bound_session(client, switch) -> None:
    response = _sign_in(client, _person("boss"))
    assert response.status_code == 200, response.text
    body = response.json()
    claims = jwt_auth._decode_access_token(body["access_token"])
    assert claims["role"] == "SuperAdmin"
    assert claims["platform"] == "desktop" and claims["device_id"] == body["device_id"]
    assert "channel" not in claims


def test_the_code_works_once(client, switch) -> None:
    verifier, challenge = _pkce()
    code = _handoff(client, _person("boss"), challenge=challenge).json()["code"]
    assert _exchange(client, code, verifier).status_code == 200
    again = _exchange(client, code, verifier)
    assert again.status_code == 400 and "already used" in again.json()["detail"]


def test_a_wrong_verifier_is_refused_and_spends_the_code(client, switch) -> None:
    verifier, challenge = _pkce()
    code = _handoff(client, _person("boss"), challenge=challenge).json()["code"]
    other, _ = _pkce()
    assert _exchange(client, code, other).status_code == 400
    assert _exchange(client, code, verifier).status_code == 400  # spent by the failed attempt


def test_the_redirect_must_match(client, switch) -> None:
    verifier, challenge = _pkce()
    code = _handoff(client, _person("boss"), challenge=challenge).json()["code"]
    assert _exchange(client, code, verifier, redirect="http://127.0.0.1:1/").status_code == 400


def test_the_code_expires(client, switch, monkeypatch) -> None:
    verifier, challenge = _pkce()
    code = _handoff(client, _person("boss"), challenge=challenge).json()["code"]
    later = handoff.time.monotonic() + handoff.HANDOFF_TTL_S + 1
    monkeypatch.setattr(handoff.time, "monotonic", lambda: later)
    assert _exchange(client, code, verifier).status_code == 400


@pytest.mark.parametrize("redirect", [
    "https://evil.example/", "http://localhost:5000/", "http://127.0.0.1:0/", "http://127.0.0.2:80/",
])
def test_the_page_may_only_send_a_code_to_the_loopback(client, switch, redirect) -> None:
    _verifier, challenge = _pkce()
    response = _handoff(client, _person("boss"), challenge=challenge, redirect=redirect)
    assert response.status_code == 400


def test_a_plain_challenge_is_refused(client, switch) -> None:
    response = _handoff(client, _person("boss"), challenge="plain-verifier-is-not-a-challenge")
    assert response.status_code == 422


def test_no_valid_firebase_token_no_code(client, switch) -> None:
    _verifier, challenge = _pkce()
    assert _handoff(client, None, challenge=challenge).status_code == 401


@pytest.mark.parametrize("provider", ["apple.com", "password"])
def test_only_google_proves_an_operators_email(client, switch, provider) -> None:
    """SEC-08 holds on the new path: WP12 brings Apple and email sign-in to the desktop, and an
    operator's address from either of them grants nothing -- anyone can register an address with
    Firebase email/password. The identity survives the hand-off unchanged, provider included."""
    response = _sign_in(client, _person("boss", provider))
    assert response.status_code == 403


def test_a_non_operator_is_refused_while_customers_are_off(client, switch) -> None:
    response = _sign_in(client, _person("ann"))
    assert response.status_code == 403


async def test_customers_by_plan_when_switched_on(client, container, switch) -> None:
    """T-6 at the exchange: Free refused with the upgrade reason, Pro capped at consumer_desktop."""
    switch(True)
    refused = _sign_in(client, _person("fay"))
    assert refused.status_code == 403 and "Pro or Team" in refused.json()["detail"]
    await _plan(container, "fay", "pro")
    allowed = _sign_in(client, _person("fay"))
    assert allowed.status_code == 200, allowed.text
    claims = jwt_auth._decode_access_token(allowed.json()["access_token"])
    assert claims["role"] == "User" and claims["channel"] == "consumer_desktop"
    assert claims["platform"] == "desktop"


def test_handoffs_are_rate_limited_per_address(client, switch) -> None:
    _verifier, challenge = _pkce()
    for _ in range(auth_module.DESKTOP_HANDOFFS_PER_MINUTE):
        assert _handoff(client, _person("boss"), challenge=challenge).status_code == 200
    assert _handoff(client, _person("boss"), challenge=challenge).status_code == 429


class _FakeRedis:
    """Enough of redis-py for the store: SETEX, and a GET+DEL pipeline."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def setex(self, key, _ttl, value):
        self.data[key] = value

    def pipeline(self):
        store = self

        class _Pipe:
            def __init__(self):
                self.ops = []

            def get(self, key):
                self.ops.append(("get", key))

            def delete(self, key):
                self.ops.append(("del", key))

            def execute(self):
                out = []
                for op, key in self.ops:
                    out.append(store.data.get(key) if op == "get" else int(store.data.pop(key, None) is not None))
                return out

        return _Pipe()


def test_redis_store_redeems_once_across_workers(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(handoff, "get_redis_client", lambda: fake)
    verifier, challenge = _pkce()
    code = handoff.create(_person("boss"), code_challenge=challenge, redirect_uri=REDIRECT)
    assert handoff._local == {}  # nothing in-process: another worker can redeem it
    assert handoff.redeem(code, code_verifier=verifier, redirect_uri=REDIRECT)["uid"] == "boss"
    with pytest.raises(handoff.HandoffError):
        handoff.redeem(code, code_verifier=verifier, redirect_uri=REDIRECT)
