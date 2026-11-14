"""``POST /api/v3/account/delete``: the signed-in user erases their own account.

Both app stores require in-app account deletion (Apple 5.1.1(v), Google Play). The route runs
the operator erasure (``erase_account``) on the caller, so these check what is specific to it:
who may call it, that it needs an explicit confirmation, and that it reaches the caller's data
and sessions and nobody else's. The cascade over the ``users`` row on a real Postgres is
``test_erasure_integration.py``'s job (same function).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.jwt_auth as jwt_auth
from api.account import router
from aura_core.domain.identifiers import UserId


@pytest.fixture
def local_sessions(monkeypatch):
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: None)
    for name in ("_local_refresh_store", "_local_device_owners", "_local_device_meta"):
        getattr(jwt_auth, name).clear()
    jwt_auth._local_revoked_devices.clear()
    jwt_auth._local_erased_accounts.clear()
    yield
    jwt_auth._local_refresh_store.clear()
    jwt_auth._local_erased_accounts.clear()


@pytest.fixture
def app(container) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.aura = container
    return app


def _pair(uid: str) -> dict:
    return jwt_auth.issue_token_pair({"uid": uid, "email": f"{uid}@example.com"})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _memories(container, uid: str) -> int:
    return len(await container.memory._store.list_for_owner(UserId(uid), include_superseded=True))


def test_deletes_the_caller_s_data_and_sessions_and_nobody_else_s(app, container, local_sessions) -> None:
    alice, bob = _pair("alice-sub"), _pair("bob-sub")
    with TestClient(app) as client:
        client.portal.call(lambda: container.memory.remember(
            owner_id=UserId("alice-sub"), content="alice likes tea", subject="drink"))
        client.portal.call(lambda: container.memory.remember(
            owner_id=UserId("bob-sub"), content="bob likes coffee", subject="drink"))

        response = client.post(
            "/api/v3/account/delete", json={"confirm": "DELETE"}, headers=_bearer(alice["access_token"]),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["forgotten"] is True and body["failed"] == []
        assert body["counts"]["memories"] == 1
        assert body["counts"]["sessions"] == 1
        assert "alice" not in response.text, "the response carries counts, never content or identity"
        assert client.portal.call(lambda: _memories(container, "alice-sub")) == 0
        assert client.portal.call(lambda: _memories(container, "bob-sub")) == 1

    assert jwt_auth.consume_refresh_token(alice["refresh_token"]) is None, "the session must be revoked"
    assert jwt_auth.consume_refresh_token(bob["refresh_token"])["uid"] == "bob-sub"


def test_live_access_tokens_of_the_account_stop_working(app, local_sessions, monkeypatch) -> None:
    """Another phone still signed in must not be able to sync the account back into existence
    with an access token that has minutes left."""
    other_phone, bob = _pair("alice-sub"), _pair("bob-sub")
    this_phone = _pair("alice-sub")
    with TestClient(app) as client:
        assert client.post(
            "/api/v3/account/delete", json={"confirm": "DELETE"},
            headers=_bearer(this_phone["access_token"]),
        ).status_code == 200

        for token in (other_phone["access_token"], this_phone["access_token"]):
            response = client.post(
                "/api/v3/account/delete", json={"confirm": "DELETE"}, headers=_bearer(token),
            )
            assert response.status_code == 401
            assert "deleted" in response.json()["detail"]
        # Nobody else is affected.
        assert client.post(
            "/api/v3/account/delete", json={"confirm": "nope"}, headers=_bearer(bob["access_token"]),
        ).status_code == 422

        # Signing in again later starts a fresh account: a token issued after the erasure is
        # accepted (the erasure is moved into the past instead of waiting for the clock).
        jwt_auth._local_erased_accounts["alice-sub"] -= 10
        fresh = _pair("alice-sub")
        assert client.post(
            "/api/v3/account/delete", json={"confirm": "nope"}, headers=_bearer(fresh["access_token"]),
        ).status_code == 422, "authenticated (then refused for the body), not 401"


def test_requires_the_explicit_confirmation(app, local_sessions) -> None:
    token = _pair("alice-sub")["access_token"]
    with TestClient(app) as client:
        for body in (None, {}, {"confirm": "yes"}, {"confirm": "delete"}):
            response = client.post("/api/v3/account/delete", json=body, headers=_bearer(token))
            assert response.status_code == 422, (body, response.text)


def test_refuses_unauthenticated_and_scoped_tokens(app, local_sessions) -> None:
    daemon = jwt_auth.issue_device_token(
        {"uid": "alice-sub", "email": "alice-sub@example.com"},
        device_id="daemon-1", scope=jwt_auth.HOST_AGENT_SCOPE,
    )
    with TestClient(app) as client:
        assert client.post("/api/v3/account/delete", json={"confirm": "DELETE"}).status_code == 401
        assert client.post(
            "/api/v3/account/delete", json={"confirm": "DELETE"}, headers=_bearer("not-a-jwt"),
        ).status_code == 401
        # A paired daemon's host-agent token must not be able to delete the account it serves.
        assert client.post(
            "/api/v3/account/delete", json={"confirm": "DELETE"},
            headers=_bearer(daemon["access_token"]),
        ).status_code == 401


def test_is_mounted_on_the_production_gateway() -> None:
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "api_reachability.py"
    spec = importlib.util.spec_from_file_location("api_reachability", script)
    reach = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reach)
    assert ("POST", "/api/v3/account/delete") in reach.routes()
