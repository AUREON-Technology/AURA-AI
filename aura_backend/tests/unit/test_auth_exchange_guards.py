"""Stream B3: what every sign-in exchange must refuse, throttle, and keep on a server-side blip.

* F37 -- the erased-account guard (INT-010) ran on ``/token`` only. ``/consumer/token`` has the
  same silent re-exchange (the web app re-exchanges a cached Firebase identity), so it gets the
  same ``SIGN_IN_HEADER`` rule. ``/web/token``, ``/desktop/login`` and
  ``/desktop/firebase-exchange`` are reached only by a person completing a sign-in (a console
  button, a browser OAuth round trip, a one-time hand-off code) -- their renewal is ``/refresh``,
  which already answers ``account_deleted`` -- so they count as deliberate sign-ins and record the
  reinstatement the way an interactive ``/token`` does.
* The erased-account state is read from Redis. When that read fails, a sign-in now answers a
  retryable 503 instead of issuing a token it could not vet (fail closed at issuance).
* F39 -- ``/nonce``, ``/desktop/pair/start`` (whatever the customer switch says) and
  ``/desktop/exchange`` are throttled per client address.
* INT-104 -- a desktop refresh spends its token before it re-resolves the role and the device's
  revocation. A database or Redis failure in either used to end the session (an operator demoted
  to the env-list role, then 401) with the token gone; now the token is put back and the answer
  is a retryable 503.

Real stores throughout (the in-process ones, or the Redis-shaped ``FakeRedis``); only Google and
Firebase identity checks are stood in for.
"""

from __future__ import annotations

import secrets
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import api.v2.auth as auth_api
import services.desktop_handoff as handoff
from services import device_pairing, jwt_auth
from services.security import verify_token
from tests.unit.test_session_revocation_index import FakeRedis

OPERATOR = {
    "uid": "op-sub", "email": "boss@example.com", "name": "Boss",
    "email_verified": True, "sign_in_provider": "google.com",
}
CUSTOMER = {
    "uid": "alice-sub", "email": "alice@example.com", "name": "Alice",
    "email_verified": True, "sign_in_provider": "google.com",
}
REDIRECT = "http://127.0.0.1:53682/"


class BrokenRedis(FakeRedis):
    """A Redis whose reads of the named key prefixes fail, as a connection blip would."""

    def __init__(self, *failing_prefixes: str) -> None:
        super().__init__()
        self.failing = set(failing_prefixes)

    def _check(self, key: str) -> None:
        if any(key.startswith(prefix) for prefix in self.failing):
            raise ConnectionError("redis unavailable")

    def get(self, key):
        self._check(key)
        return super().get(key)

    def exists(self, key):
        self._check(key)
        return super().exists(key)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "boss@example.com")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("DEVELOPER_EMAILS", raising=False)
    monkeypatch.setattr(device_pairing, "get_redis_client", lambda: None)
    monkeypatch.setattr(handoff, "get_redis_client", lambda: None)
    auth_api._reset_auth_rate_limits()
    handoff._reset()
    yield
    auth_api._reset_auth_rate_limits()
    handoff._reset()


@pytest.fixture(params=["local", "redis"])
def store(request, monkeypatch):
    fake = FakeRedis() if request.param == "redis" else None
    _use_store(monkeypatch, fake)
    yield fake
    _clear_local()


def _use_store(monkeypatch, fake) -> None:
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: fake)
    _clear_local()


def _clear_local() -> None:
    for name in (
        "_local_refresh_store", "_local_device_owners", "_local_device_meta",
        "_local_refresh_used", "_local_refresh_families", "_local_erased_accounts",
        "_local_reinstated_accounts", "_local_refresh_revoked",
    ):
        getattr(jwt_auth, name).clear()
    jwt_auth._local_revoked_devices.clear()


@pytest.fixture
def persisted(monkeypatch) -> list:
    rows: list = []

    async def record(user_info, *, client_language=None):
        rows.append(user_info["uid"])

    monkeypatch.setattr(auth_api, "_persist_user", record)
    return rows


@pytest.fixture
def client(container):
    app = FastAPI()
    app.include_router(auth_api.router)
    app.state.aura = container
    app.dependency_overrides[verify_token] = lambda: dict(CUSTOMER)
    with TestClient(app) as test_client:
        yield test_client


def _identity(person: dict):
    async def identity(request, authorization):
        return dict(person)

    return identity


def _erase(uid: str) -> None:
    jwt_auth.revoke_all_for_uid(uid)
    jwt_auth.revoke_all_devices_for_uid(uid)
    jwt_auth.mark_account_erased(uid)


def _error(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("error") if isinstance(detail, dict) else None


def _consumer_token(client, *, interactive: bool = False):
    headers = {"Authorization": "Bearer firebase-id"}
    if interactive:
        headers[auth_api.SIGN_IN_HEADER] = "interactive"
    with patch("api.v2.auth.verify_identity_token", new=_identity(CUSTOMER)):
        return client.post("/api/v3/auth/consumer/token", headers=headers)


def _web_token(client):
    with patch("api.v2.auth.verify_identity_token", new=_identity(OPERATOR)):
        return client.post("/api/v3/auth/web/token", headers={"Authorization": "Bearer google-id"})


def _desktop_login(client):
    with patch("api.v2.auth.verify_identity_token", new=_identity(OPERATOR)):
        return client.post("/api/v3/auth/desktop/login", headers={"Authorization": "Bearer google-id"},
                           json={"device_name": "laptop"})


def _firebase_exchange(client):
    verifier = secrets.token_urlsafe(48)
    code = handoff.create(dict(OPERATOR), code_challenge=handoff.s256(verifier), redirect_uri=REDIRECT)
    return client.post("/api/v3/auth/desktop/firebase-exchange", json={
        "code": code, "code_verifier": verifier, "redirect_uri": REDIRECT, "device_name": "laptop"})


# --- F37: every exchange route runs the erased-account guard ------------------------------------


class TestConsumerTokenIsGuardedLikeToken:
    def test_a_silent_re_exchange_of_an_erased_account_is_refused(self, client, store, persisted) -> None:
        _erase(CUSTOMER["uid"])

        response = _consumer_token(client)

        assert response.status_code == 401, response.text
        assert _error(response) == "account_deleted"
        assert persisted == [], "no users row may be written back for the erased subject"

    def test_a_deliberate_sign_in_starts_a_fresh_account(self, client, store, persisted) -> None:
        _erase(CUSTOMER["uid"])

        assert _consumer_token(client, interactive=True).status_code == 200
        # Reinstated: a later silent renewal is an ordinary one.
        assert _consumer_token(client).status_code == 200

    def test_an_account_that_was_never_erased_is_untouched(self, client, store, persisted) -> None:
        assert _consumer_token(client).status_code == 200


@pytest.mark.parametrize("sign_in", [_web_token, _desktop_login, _firebase_exchange],
                         ids=["web_token", "desktop_login", "firebase_exchange"])
class TestDeliberateSignInRoutesRecordTheReinstatement:
    """These routes are a person signing in by construction, so they go ahead -- and say so, so
    the erased account's state is the same as after an interactive ``/token``."""

    def test_the_sign_in_goes_ahead_and_reinstates(self, client, store, persisted, sign_in) -> None:
        _erase(OPERATOR["uid"])
        assert jwt_auth.account_erased_and_not_reinstated(OPERATOR["uid"])

        response = sign_in(client)

        assert response.status_code == 200, response.text
        assert not jwt_auth.account_erased_and_not_reinstated(OPERATOR["uid"])

    def test_an_unreadable_erasure_state_is_a_503_not_a_token(self, client, monkeypatch, persisted, sign_in) -> None:
        _use_store(monkeypatch, BrokenRedis("erased_account:", "reinstated_account:"))

        response = sign_in(client)

        assert response.status_code == 503, response.text
        assert "access_token" not in response.text
        assert response.json()["detail"]["retryable"] is True
        assert persisted == []


class TestSignInFailsClosedWhenTheErasureStateIsUnreadable:
    @pytest.mark.parametrize("path", ["/api/v3/auth/token", "consumer"])
    def test_a_redis_failure_is_a_retryable_503(self, client, monkeypatch, persisted, path) -> None:
        _use_store(monkeypatch, BrokenRedis("erased_account:"))

        if path == "consumer":
            response = _consumer_token(client)
        else:
            response = client.post(path)

        assert response.status_code == 503, response.text
        assert _error(response) == "auth_state_unavailable"
        assert response.json()["detail"]["retryable"] is True
        assert persisted == []

    def test_an_unreadable_reinstatement_is_not_read_as_erased_forever(self, client, monkeypatch, persisted) -> None:
        """Erased, and the reinstatement record cannot be read: the old code read that as "not
        reinstated" and told a person who had signed in again that their account was deleted."""
        fake = BrokenRedis("reinstated_account:")
        _use_store(monkeypatch, fake)
        jwt_auth.mark_account_erased(CUSTOMER["uid"])

        response = client.post("/api/v3/auth/token")

        assert response.status_code == 503, response.text

    def test_ordinary_requests_still_fail_open_on_the_erasure_check(self, monkeypatch) -> None:
        """Per-request auth keeps its documented behaviour: a Redis blip on the erased-account read
        must not turn every authenticated call into an outage."""
        import asyncio

        from fastapi.security import HTTPAuthorizationCredentials

        _use_store(monkeypatch, BrokenRedis("erased_account:"))
        pair = jwt_auth.issue_token_pair(dict(CUSTOMER))

        class _Request:
            allowed_token_scopes = ()

        claims = asyncio.run(jwt_auth.verify_access_token(
            _Request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials=pair["access_token"]),
        ))
        assert claims["uid"] == CUSTOMER["uid"]

    def test_a_device_token_whose_revocation_cannot_be_read_is_a_503(self, monkeypatch) -> None:
        """Was an unhandled Redis error: a 500 on HTTP, and -- wrapped into AuthenticationError by
        the gateway -- a 4401 that unpaired the daemon. Now a retryable 503."""
        import asyncio

        from fastapi.security import HTTPAuthorizationCredentials

        _use_store(monkeypatch, BrokenRedis("revoked_device:"))
        session = jwt_auth.issue_desktop_session(dict(OPERATOR), device_id="laptop")

        class _Request:
            allowed_token_scopes = ()

        with pytest.raises(HTTPException) as refused:
            asyncio.run(jwt_auth.verify_access_token(
                _Request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials=session["access_token"]),
            ))
        assert refused.value.status_code == 503


# --- F39: the unauthenticated entry points are throttled ----------------------------------------


class TestUnauthenticatedEntryPointsAreThrottled:
    def test_nonces(self, client) -> None:
        codes = [client.post("/api/v3/auth/nonce").status_code
                 for _ in range(auth_api.NONCES_PER_MINUTE + 1)]
        assert codes[:-1] == [200] * auth_api.NONCES_PER_MINUTE
        assert codes[-1] == 429

    def test_pairing_starts_with_the_customer_switch_off(self, client) -> None:
        assert client.app.state.aura.settings.security.engineer_for_customers is False
        codes = [client.post("/api/v3/auth/desktop/pair/start").status_code
                 for _ in range(auth_api.DESKTOP_SIGN_INS_PER_MINUTE + 1)]
        assert codes[:-1] == [200] * auth_api.DESKTOP_SIGN_INS_PER_MINUTE
        assert codes[-1] == 429

    def test_desktop_code_exchanges(self, client, monkeypatch) -> None:
        calls: list = []

        async def google(**kwargs):
            calls.append(kwargs)
            return "header.payload.signature"

        monkeypatch.setattr(auth_api, "exchange_desktop_code", google)
        body = {"code": "4/code", "code_verifier": "v" * 64, "redirect_uri": REDIRECT}
        codes = [client.post("/api/v3/auth/desktop/exchange", json=body).status_code
                 for _ in range(auth_api.DESKTOP_SIGN_INS_PER_MINUTE + 1)]
        assert codes[:-1] == [200] * auth_api.DESKTOP_SIGN_INS_PER_MINUTE
        assert codes[-1] == 429
        assert len(calls) == auth_api.DESKTOP_SIGN_INS_PER_MINUTE, "a throttled call must not reach Google"


# --- INT-104: a desktop refresh keeps its token through a server-side failure -------------------


DB_OPERATOR = {"uid": "db-op", "email": "dbop@example.com", "email_verified": True,
               "sign_in_provider": "google.com"}


@pytest.fixture
def access_page(monkeypatch):
    """The Access page's assignment, read through ``user_repo.assigned_role``'s real code path
    with the database replaced by a switchable stand-in."""
    import infrastructure.db.base as db_base

    state = {"up": True, "role": "Developer"}

    class _Session:
        async def __aenter__(self):
            if not state["up"]:
                raise ConnectionError("database unavailable")
            return self

        async def __aexit__(self, *exc):
            return False

        async def scalar(self, _statement):
            return state["role"]

    monkeypatch.setattr(db_base, "is_configured", lambda: True)
    monkeypatch.setattr(db_base, "get_session_factory", lambda: (lambda: _Session()))
    return state


class TestDesktopRefreshSurvivesAServerSideFailure:
    def _refresh(self, client, token: str):
        return client.post("/api/v3/auth/refresh", json={"refresh_token": token})

    def test_an_unreadable_role_assignment_is_a_503_and_the_token_still_works(
        self, client, store, access_page,
    ) -> None:
        session = jwt_auth.issue_desktop_session(dict(DB_OPERATOR), device_id="laptop")
        access_page["up"] = False

        response = self._refresh(client, session["refresh_token"])

        assert response.status_code == 503, response.text
        access_page["up"] = True
        retried = self._refresh(client, session["refresh_token"])
        assert retried.status_code == 200, retried.text

    def test_a_real_demotion_still_ends_the_desktop_session(self, client, store, access_page) -> None:
        session = jwt_auth.issue_desktop_session(dict(DB_OPERATOR), device_id="laptop")
        access_page["role"] = "User"

        assert self._refresh(client, session["refresh_token"]).status_code == 401

    def test_an_unreadable_device_revocation_is_a_503_and_the_token_still_works(
        self, client, monkeypatch,
    ) -> None:
        fake = BrokenRedis("revoked_device:")
        _use_store(monkeypatch, fake)
        session = jwt_auth.issue_desktop_session(dict(OPERATOR), device_id="laptop")

        response = self._refresh(client, session["refresh_token"])

        assert response.status_code == 503, response.text
        fake.failing.clear()
        retried = self._refresh(client, session["refresh_token"])
        assert retried.status_code == 200, retried.text

    def test_a_phone_refresh_is_unaffected_by_an_unreadable_assignment(
        self, client, store, access_page,
    ) -> None:
        """No device: the assignment can only raise a role there, and losing it for one
        access-token lifetime ends nothing -- so a database blip does not block the renewal."""
        pair = jwt_auth.issue_token_pair(dict(CUSTOMER))
        access_page["up"] = False

        assert self._refresh(client, pair["refresh_token"]).status_code == 200


class TestOtherCallersOfTheAssignmentKeepTheirBehaviour:
    async def test_assigned_role_still_reads_as_none_on_a_database_error(self, access_page) -> None:
        from infrastructure.db.user_repo import assigned_role

        access_page["up"] = False
        assert await assigned_role("db-op") is None

    async def test_the_strict_read_says_unavailable(self, access_page) -> None:
        from infrastructure.db.user_repo import RoleLookupUnavailable, assigned_role, role_lookup_must_succeed

        access_page["up"] = False
        with role_lookup_must_succeed(), pytest.raises(RoleLookupUnavailable):
            await assigned_role("db-op")
