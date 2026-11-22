"""``/api/v3/me``: read and update the caller's profile, change the Firebase password.

The identity provider is a seam (``_verify_fresh_identity``, ``_set_firebase_password``,
``_firebase_available``): these tests replace it with a scripted one, so what they exercise is
this server's own rules -- who may change a password, how fresh the proof must be, what is
revoked, what is never echoed -- not Firebase. The database is a stub: the route only sets
columns on the ``User`` row ``get_or_create_user`` returns, and the real row creation is
``test_erasure_integration.py`` / the Postgres suite's job.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.me as me
import services.jwt_auth as jwt_auth
from infrastructure.db.base import get_db

NEW_PASSWORD = "Str0ng!Passw0rd-xyz"


class _Db:
    commits = 0

    async def commit(self) -> None:
        type(self).commits += 1


@pytest.fixture
def local_sessions(monkeypatch):
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: None)
    for name in ("_local_refresh_store", "_local_device_owners", "_local_device_meta"):
        getattr(jwt_auth, name).clear()
    jwt_auth._local_revoked_devices.clear()
    me._reset_me_rate_limits()
    yield
    jwt_auth._local_refresh_store.clear()
    me._reset_me_rate_limits()


@pytest.fixture
def users(monkeypatch) -> dict:
    """uid -> the stub ``User`` row ``get_or_create_user`` hands the route."""
    rows: dict[str, SimpleNamespace] = {}

    async def fake_get_or_create(_db, user_info):
        uid = user_info["uid"]
        return rows.setdefault(uid, SimpleNamespace(
            id=f"id-{uid}", auth_subject=uid, email=user_info.get("email") or f"{uid}@unknown.local",
            display_name="", photo_url=None, role="User", timezone=None, language=None,
            created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        ))

    monkeypatch.setattr(me, "get_or_create_user", fake_get_or_create)
    monkeypatch.setattr(me, "is_configured", lambda: True)
    return rows


class _Firebase:
    """A scripted identity provider: what ``verify`` returns, and what was written."""

    def __init__(self) -> None:
        self.claims: dict | None = None
        self.verify_error: Exception | None = None
        self.set_error: Exception | None = None
        self.available = True
        self.set_calls: list[tuple[str, str]] = []

    def fresh_password_signin(self, uid: str, *, age_s: float = 5, provider: str = "password") -> None:
        self.claims = {"uid": uid, "auth_time": time.time() - age_s,
                       "firebase": {"sign_in_provider": provider}, "_canonical_subject": uid}


@pytest.fixture
def firebase(monkeypatch) -> _Firebase:
    fake = _Firebase()

    async def verify(_token: str) -> dict:
        if fake.verify_error:
            raise fake.verify_error
        assert fake.claims is not None, "the test did not script a token"
        return dict(fake.claims)

    async def set_password(uid: str, password: str) -> None:
        if fake.set_error:
            raise fake.set_error
        fake.set_calls.append((uid, password))

    monkeypatch.setattr(me, "_verify_fresh_identity", verify)
    monkeypatch.setattr(me, "_set_firebase_password", set_password)
    monkeypatch.setattr(me, "_firebase_available", lambda: fake.available)
    return fake


@pytest.fixture
def client(users, local_sessions) -> TestClient:
    app = FastAPI()
    app.include_router(me.router)

    async def _db():
        yield _Db()

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as test_client:
        yield test_client


def _pair(uid: str = "alice-sub", **extra) -> dict:
    return jwt_auth.issue_token_pair({"uid": uid, "email": f"{uid}@example.com", **extra})


def _auth(pair: dict) -> dict:
    return {"Authorization": f"Bearer {pair['access_token']}"}


# --- GET /me ---

class TestGetProfile:
    def test_returns_the_callers_own_profile(self, client, users) -> None:
        pair = _pair("alice-sub", name="Alice")
        response = client.get("/api/v3/me", headers=_auth(pair))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["email"] == "alice-sub@example.com"
        assert body["language"] == "en", "an account that never reported one reads as English"
        assert body["timezone"] is None and body["photo_url"] is None and body["display_name"] == ""
        assert body["role"] == "User"
        assert set(body) == {"id", "email", "email_verified", "display_name", "photo_url", "language",
                             "timezone", "role", "created_at"}, "no hash, no token, no auth_subject"

    def test_a_placeholder_email_is_not_reported_as_an_address(self, client, users) -> None:
        pair = jwt_auth.issue_token_pair({"uid": "no-email-sub"})
        users["no-email-sub"] = SimpleNamespace(
            id="x", auth_subject="no-email-sub", email="no-email-sub@unknown.local", display_name="",
            photo_url=None, role="User", timezone=None, language=None,
            created_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
        body = client.get("/api/v3/me", headers=_auth(pair)).json()
        assert body["email"] is None and body["email_verified"] is False

    def test_requires_a_signed_in_caller(self, client) -> None:
        assert client.get("/api/v3/me").status_code == 401
        assert client.get("/api/v3/me", headers={"Authorization": "Bearer junk"}).status_code == 401

    def test_each_caller_sees_only_themselves(self, client) -> None:
        alice, bob = _pair("alice-sub"), _pair("bob-sub")
        client.patch("/api/v3/me", json={"display_name": "Alice"}, headers=_auth(alice))
        assert client.get("/api/v3/me", headers=_auth(bob)).json()["display_name"] == ""


# --- PATCH /me ---

class TestUpdateProfile:
    def test_updates_every_owned_field_and_returns_the_result(self, client, users) -> None:
        pair = _pair()
        response = client.patch("/api/v3/me", headers=_auth(pair), json={
            "display_name": "  Alice A.  ", "photo_url": "https://cdn.example.com/a.png",
            "language": "ar-EG", "timezone": "Africa/Cairo"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["display_name"] == "Alice A." and body["language"] == "ar"
        assert body["timezone"] == "Africa/Cairo" and body["photo_url"] == "https://cdn.example.com/a.png"
        assert users["alice-sub"].language == "ar", "stored, so server-written text follows it"
        assert client.get("/api/v3/me", headers=_auth(pair)).json()["display_name"] == "Alice A."

    def test_only_the_fields_sent_change(self, client, users) -> None:
        pair = _pair()
        client.patch("/api/v3/me", json={"display_name": "Alice", "timezone": "Africa/Cairo"}, headers=_auth(pair))
        client.patch("/api/v3/me", json={"language": "ar"}, headers=_auth(pair))
        body = client.get("/api/v3/me", headers=_auth(pair)).json()
        assert (body["display_name"], body["timezone"], body["language"]) == ("Alice", "Africa/Cairo", "ar")

    def test_photo_can_be_removed_and_name_cleared(self, client) -> None:
        pair = _pair()
        client.patch("/api/v3/me", json={"photo_url": "https://x.example/a.png", "display_name": "A"}, headers=_auth(pair))
        body = client.patch("/api/v3/me", json={"photo_url": None, "display_name": ""}, headers=_auth(pair)).json()
        assert body["photo_url"] is None and body["display_name"] == ""

    @pytest.mark.parametrize("payload, field", [
        ({"email": "mallory@example.com"}, None),
        ({"role": "SuperAdmin"}, None),
        ({"id": "other"}, None),
        ({"display_name": None}, "display_name"),
        ({"display_name": "x" * 201}, "display_name"),
        ({"display_name": "bad\x00name"}, "display_name"),
        ({"photo_url": "http://insecure.example/a.png"}, "photo_url"),
        ({"photo_url": "javascript:alert(1)"}, "photo_url"),
        ({"photo_url": "https://" + "a" * 1000 + ".example"}, "photo_url"),
        ({"language": "fr"}, "language"),
        ({"language": None}, "language"),
        ({"timezone": "Mars/Olympus"}, "timezone"),
        ({"timezone": "localtime"}, "timezone"),
    ])
    def test_refuses_what_it_cannot_store(self, client, users, payload, field) -> None:
        response = client.patch("/api/v3/me", json=payload, headers=_auth(_pair()))
        assert response.status_code == 422, response.text
        if field:
            assert response.json()["detail"]["field"] == field
        row = users.get("alice-sub")
        assert row is None or (row.display_name, row.photo_url, row.language, row.timezone, row.role) == ("", None, None, None, "User")

    def test_the_email_cannot_be_edited(self, client, users) -> None:
        """Role grants key on the email, and the verified one comes from the identity provider."""
        pair = _pair()
        assert client.patch("/api/v3/me", json={"email": "boss@example.com"}, headers=_auth(pair)).status_code == 422
        assert users.get("alice-sub") is None or users["alice-sub"].email == "alice-sub@example.com"

    def test_an_empty_update_is_refused(self, client) -> None:
        response = client.patch("/api/v3/me", json={}, headers=_auth(_pair()))
        assert response.status_code == 422 and response.json()["detail"]["error"] == "empty_update"

    def test_requires_a_signed_in_caller(self, client) -> None:
        assert client.patch("/api/v3/me", json={"display_name": "x"}).status_code == 401


# --- POST /me/password ---

def _change(client, pair, *, password: str = NEW_PASSWORD, token: str = "fresh-id-token"):
    return client.post("/api/v3/me/password", json={"new_password": password, "id_token": token}, headers=_auth(pair))


class TestChangePassword:
    def test_changes_it_and_revokes_every_session_and_device(self, client, firebase) -> None:
        pair, other_phone = _pair(), _pair()
        firebase.fresh_password_signin("alice-sub")
        bystander = _pair("bob-sub")

        response = _change(client, pair)

        assert response.status_code == 200, response.text
        assert firebase.set_calls == [("alice-sub", NEW_PASSWORD)]
        body = response.json()
        assert body["changed"] is True and body["sign_in_again"] is True and body["sessions_revoked"] == 2
        assert jwt_auth.consume_refresh_token(pair["refresh_token"]) is None
        assert jwt_auth.consume_refresh_token(other_phone["refresh_token"]) is None
        assert jwt_auth.consume_refresh_token(bystander["refresh_token"])["uid"] == "bob-sub"

    def test_never_echoes_or_logs_the_password_or_the_proof(self, client, firebase, caplog) -> None:
        firebase.fresh_password_signin("alice-sub")
        with caplog.at_level(logging.DEBUG):
            response = _change(client, _pair(), token="proof-token-123")
        assert NEW_PASSWORD not in response.text and "proof-token-123" not in response.text
        assert NEW_PASSWORD not in caplog.text and "proof-token-123" not in caplog.text
        assert "alice" not in caplog.text, "no subject or email in the log"

    def test_the_new_password_must_meet_the_platform_policy(self, client, firebase) -> None:
        firebase.fresh_password_signin("alice-sub")
        for weak in ("short1!A", "alllowercase1!x", "ALLUPPERCASE1!X", "NoDigitsHere!!xx", "NoSymbolsHere123x"):
            response = _change(client, _pair(), password=weak)
            assert response.status_code == 422, weak
            assert response.json()["detail"]["error"] == "weak_password"
            assert weak not in response.text
        assert firebase.set_calls == [], "a weak password never reaches the identity provider"

    def test_a_stale_sign_in_is_not_proof(self, client, firebase) -> None:
        firebase.fresh_password_signin("alice-sub", age_s=me.PASSWORD_REAUTH_MAX_AGE_S + 30)
        response = _change(client, _pair())
        assert response.status_code == 401 and response.json()["detail"]["error"] == "reauth_required"
        assert firebase.set_calls == []

    def test_a_token_from_the_future_is_not_proof(self, client, firebase) -> None:
        firebase.fresh_password_signin("alice-sub", age_s=-3600)
        assert _change(client, _pair()).status_code == 401
        assert firebase.set_calls == []

    def test_a_token_without_auth_time_is_not_proof(self, client, firebase) -> None:
        firebase.fresh_password_signin("alice-sub")
        del firebase.claims["auth_time"]
        assert _change(client, _pair()).status_code == 401
        assert firebase.set_calls == []

    def test_a_token_for_another_account_is_refused(self, client, firebase) -> None:
        """A stolen access token plus the attacker's OWN fresh sign-in must not reset Alice's password."""
        firebase.fresh_password_signin("mallory-sub")
        response = _change(client, _pair("alice-sub"))
        assert response.status_code == 401 and response.json()["detail"]["error"] == "reauth_required"
        assert firebase.set_calls == []

    def test_a_google_only_account_has_no_password_to_change(self, client, firebase) -> None:
        firebase.fresh_password_signin("alice-sub", provider="google.com")
        response = _change(client, _pair())
        assert response.status_code == 409 and response.json()["detail"]["error"] == "password_not_enabled"
        assert firebase.set_calls == []

    def test_an_unverifiable_token_is_refused(self, client, firebase) -> None:
        firebase.verify_error = ValueError("expired")
        response = _change(client, _pair())
        assert response.status_code == 401 and response.json()["detail"]["error"] == "reauth_required"
        assert firebase.set_calls == []

    def test_a_stolen_access_token_alone_cannot_do_it(self, client, firebase) -> None:
        assert client.post("/api/v3/me/password", json={"new_password": NEW_PASSWORD},
                           headers=_auth(_pair())).status_code == 422
        assert firebase.set_calls == []

    def test_requires_a_signed_in_caller(self, client, firebase) -> None:
        firebase.fresh_password_signin("alice-sub")
        response = client.post("/api/v3/me/password", json={"new_password": NEW_PASSWORD, "id_token": "t"})
        assert response.status_code == 401 and firebase.set_calls == []

    def test_a_server_without_firebase_says_so(self, client, firebase) -> None:
        firebase.available = False
        response = _change(client, _pair())
        assert response.status_code == 503 and response.json()["detail"]["error"] == "password_change_unavailable"

    def test_a_provider_failure_leaves_sessions_alone_and_says_the_password_did_not_change(self, client, firebase) -> None:
        pair = _pair()
        firebase.fresh_password_signin("alice-sub")
        firebase.set_error = RuntimeError("identity toolkit down")
        response = _change(client, pair)
        assert response.status_code == 502
        assert "identity toolkit" not in response.text, "provider internals stay out of the response"
        assert jwt_auth.consume_refresh_token(pair["refresh_token"]) is not None

    def test_the_provider_rejecting_the_password_is_a_422(self, client, firebase) -> None:
        firebase.fresh_password_signin("alice-sub")
        firebase.set_error = ValueError("bad password")
        assert _change(client, _pair()).status_code == 422

    def test_incomplete_revocation_does_not_fail_a_change_that_happened(self, client, firebase, monkeypatch) -> None:
        firebase.fresh_password_signin("alice-sub")

        def boom(_uid):
            raise jwt_auth.AuthStateUnavailable("redis down")

        monkeypatch.setattr(me, "revoke_all_for_uid", boom)
        response = _change(client, _pair())
        assert response.status_code == 200
        assert response.json()["revocation_incomplete"] is True and firebase.set_calls

    def test_attempts_are_rate_limited_per_account(self, client, firebase) -> None:
        firebase.fresh_password_signin("alice-sub")
        firebase.verify_error = ValueError("nope")
        statuses = [_change(client, _pair()).status_code for _ in range(me.PASSWORD_CHANGES_PER_WINDOW + 1)]
        assert statuses[:-1] == [401] * me.PASSWORD_CHANGES_PER_WINDOW and statuses[-1] == 429
        assert _change(client, _pair("bob-sub")).status_code == 401, "another account is not throttled"

    def test_extra_fields_are_refused(self, client, firebase) -> None:
        firebase.fresh_password_signin("alice-sub")
        response = client.post("/api/v3/me/password", headers=_auth(_pair()), json={
            "new_password": NEW_PASSWORD, "id_token": "t", "old_password": "hunter2"})
        assert response.status_code == 422 and firebase.set_calls == []


def test_password_problems_names_requirements_not_the_password() -> None:
    assert me.password_problems(NEW_PASSWORD) == []
    problems = me.password_problems("abc")
    assert problems and all("abc" not in p for p in problems)
