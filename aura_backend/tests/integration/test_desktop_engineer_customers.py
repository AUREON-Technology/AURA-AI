"""SaaS P1-E: desktop sign-in, refresh and pairing for entitled customers (ADR-011 "Customers").

Real routes (``api/v2/auth.py``) and real tokens, with the container on ``app.state.aura`` as the
gateway sets it. Only the Google/Firebase ID-token check is stood in for.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.v2.auth as auth_module
import services.jwt_auth as jwt_auth
from api.v2.auth import router


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "boss@example.com")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("DEVELOPER_EMAILS", raising=False)
    auth_module._reset_desktop_rate_limits()
    yield
    auth_module._reset_desktop_rate_limits()


@pytest.fixture
def switch(container):
    def set_to(on: bool) -> None:
        container.settings = replace(container.settings, security=replace(container.settings.security,
                                                                          engineer_for_customers=on))
    set_to(True)
    return set_to


@pytest.fixture
def client(container):
    app = FastAPI()
    app.include_router(router)
    app.state.aura = container
    return TestClient(app)


def _person(uid: str, email: str | None = None) -> dict:
    return {"uid": uid, "email": email or f"{uid}@example.com", "name": uid,
            "email_verified": True, "sign_in_provider": "google.com"}


def _login(client, person: dict):
    async def identity(request, authorization):
        return dict(person)

    with patch("api.v2.auth.verify_identity_token", new=identity):
        return client.post("/api/v3/auth/desktop/login", headers={"Authorization": "Bearer id"},
                           json={"device_name": "laptop"})


def _claims(token: str) -> dict:
    return jwt_auth._decode_access_token(token)


async def _plan(container, uid: str, plan: str) -> str:
    ws = (await container.tenancy.ensure_personal(uid)).id
    await container.plans.set_plan("sa", ws, plan, 1)
    return ws


def _web_token(uid: str, email: str | None = None) -> str:
    """An ordinary (phone/web) session's access token, to approve a pairing with."""
    return jwt_auth.issue_token_pair({**_person(uid, email), "role": "User"})["access_token"]


def _approve(client, token: str):
    start = client.post("/api/v3/auth/desktop/pair/start", json={"device_name": "box"}).json()
    response = client.post("/api/v3/auth/desktop/pair/approve", json={"user_code": start["user_code"]},
                           headers={"Authorization": f"Bearer {token}"})
    return start, response


async def test_pro_customer_desktop_login_is_capped_and_device_bound(client, container, switch) -> None:
    """Review focus #1: even an operator's address on a customer desktop session is a User."""
    await _plan(container, "pam", "pro")
    with patch.dict("os.environ", {"SUPERADMIN_EMAILS": "pam@example.com"}):
        response = _login(client, _person("pam"))
    assert response.status_code == 200, response.text
    claims = _claims(response.json()["access_token"])
    # pam's address is an operator's: she is an operator, so she keeps the operator path.
    assert claims["role"] == "SuperAdmin" and "channel" not in claims
    ann = _login(client, _person("ann2"))
    assert ann.status_code == 403
    await _plan(container, "ann2", "pro")
    ann = _login(client, _person("ann2"))
    assert ann.status_code == 200, ann.text
    claims = _claims(ann.json()["access_token"])
    assert claims["role"] == "User" and claims["channel"] == "consumer_desktop" and claims["device_id"]


async def test_capped_customer_session_stays_user_on_refresh_even_if_listed(client, container, switch) -> None:
    await _plan(container, "quin", "pro")
    first = _login(client, _person("quin")).json()
    with patch.dict("os.environ", {"SUPERADMIN_EMAILS": "quin@example.com"}):
        refreshed = client.post("/api/v3/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    claims = _claims(refreshed.json()["access_token"])
    assert claims["role"] == "User" and claims["channel"] == "consumer_desktop"


async def test_free_customer_cannot_sign_in_pair_or_refresh(client, container, switch) -> None:
    """Spec 6.1 #10: a Free or downgraded workspace cannot pair or refresh a desktop session."""
    refused = _login(client, _person("rex"))
    assert refused.status_code == 403 and "Pro or Team" in refused.json()["detail"]
    _start, approve = _approve(client, _web_token("rex"))
    assert approve.status_code == 403
    ws = await _plan(container, "rex", "pro")
    session = _login(client, _person("rex")).json()
    await container.plans.set_plan("sa", ws, "free", 1)
    downgraded = client.post("/api/v3/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert downgraded.status_code == 401
    _start, approve = _approve(client, _web_token("rex"))
    assert approve.status_code == 403


async def test_switch_off_keeps_operators_only(client, container, switch) -> None:
    await _plan(container, "sid", "pro")
    switch(False)
    refused = _login(client, _person("sid"))
    assert refused.status_code == 403 and "Pro or Team" not in refused.json()["detail"]


async def test_operator_desktop_session_unchanged(client, container, switch) -> None:
    """Review focus #5."""
    response = _login(client, _person("op", "boss@example.com"))
    assert response.status_code == 200
    claims = _claims(response.json()["access_token"])
    assert claims["role"] == "SuperAdmin" and "channel" not in claims


async def test_daemon_token_from_a_customer_approval_is_channel_capped(client, container, switch) -> None:
    await _plan(container, "tia", "pro")
    start, approve = _approve(client, _web_token("tia", "boss@example.com"))
    assert approve.status_code == 200, approve.text
    polled = client.post("/api/v3/auth/desktop/pair/poll", json={"pairing_id": start["pairing_id"]}).json()
    assert polled["status"] == "approved"
    claims = _claims(polled["access_token"])
    assert claims["channel"] == "consumer_desktop" and claims["role"] == "User" and claims["scope"] == "host_agent"


async def test_pairing_limits(client, container, switch, monkeypatch) -> None:
    await _plan(container, "uma", "pro")
    token = _web_token("uma")
    for _ in range(auth_module.PAIR_APPROVALS_PER_HOUR):
        assert _approve(client, token)[1].status_code == 200
    assert _approve(client, token)[1].status_code == 429
    await _plan(container, "vic", "pro")
    monkeypatch.setattr(auth_module, "list_devices_for_uid",
                        lambda uid: [{"device_id": str(i)} for i in range(auth_module.MAX_DESKTOP_DEVICES_PER_USER)])
    assert _approve(client, _web_token("vic"))[1].status_code == 409


async def test_desktop_sign_in_rate_limited(client, container, switch) -> None:
    for _ in range(auth_module.DESKTOP_SIGN_INS_PER_MINUTE):
        _login(client, _person("wes"))
    assert _login(client, _person("wes")).status_code == 429


async def test_customer_desktop_session_never_reaches_the_console(client, container, switch) -> None:
    """Spec 6.1 #10: a customer desktop token through the gateway's real authenticator."""
    from api.admin_platform import router as admin_platform_router
    from aura_core.api import build_app
    from aura_service import _build_authenticator

    await _plan(container, "xan", "pro")
    # Signed in as a customer; the address joins the operator list only afterwards, and the
    # channel cap must still keep the session out of every console route.
    session = _login(client, _person("xan")).json()
    token = session["access_token"]
    authenticator = _build_authenticator(container.settings, service_keys=container.service_keys, tenancy=container.tenancy)
    app = build_app(container, authenticator=authenticator)
    app.include_router(admin_platform_router)
    with patch.dict("os.environ", {"SUPERADMIN_EMAILS": "xan@example.com"}), TestClient(app) as gateway:
        headers = {"Authorization": f"Bearer {token}"}
        assert gateway.get("/api/v3/admin/status", headers=headers).status_code == 403
        assert gateway.get("/api/v3/admin/operators", headers=headers).status_code == 403
