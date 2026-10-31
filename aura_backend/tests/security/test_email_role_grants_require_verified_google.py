"""Email-list roles need a verified email from Google sign-in (audit F-38 / SEC-08).

SUPERADMIN_EMAILS / ADMIN_EMAILS / DEVELOPER_EMAILS (and roles.json's email keys) grant a role to
whoever's token carries a listed ``email``. Nothing checked ``email_verified`` or the sign-in
provider. If the Firebase project ever had the email/password provider enabled, anyone holding
the public Firebase API key could create an unverified account with an operator's address,
exchange its ID token at ``/api/v3/auth/web/token`` and receive a SuperAdmin session.

These go through the real ``verify_identity_token`` with only the token-signature checks
stubbed: the Google verifier refuses (as it does for any Firebase token) and the Firebase
verifier returns the decoded claims a real token of each kind carries.
"""

from __future__ import annotations

from unittest.mock import patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.jwt_auth as jwt_auth
import services.security as security
from api.v2.auth import router
from services.rbac import get_user_role

OPERATOR = "ops@aura.example"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no roles.json in sight
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: None)
    monkeypatch.setattr(jwt_auth, "_local_refresh_store", {})
    monkeypatch.setenv("SUPERADMIN_EMAILS", OPERATOR)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("DEVELOPER_EMAILS", raising=False)
    monkeypatch.delenv("AURA_SUPERADMIN_EMAILS", raising=False)
    monkeypatch.delenv("AURA_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("AURA_DEVELOPER_EMAILS", raising=False)


def _firebase_claims(*, verified: bool, provider: str) -> dict:
    return {
        "uid": f"uid-{provider}-{verified}",
        "email": OPERATOR,
        "email_verified": verified,
        "name": "Someone",
        "firebase": {"sign_in_provider": provider, "identities": {}},
    }


def _not_a_google_token(*_args, **_kwargs):
    raise ValueError("Wrong issuer")


def _web_login(claims: dict):
    app = FastAPI()
    app.include_router(router)
    with (
        patch.object(security.id_token, "verify_oauth2_token", _not_a_google_token),
        patch.object(security, "firebase_app", object()),
        patch("firebase_admin.auth.verify_id_token", lambda token: claims),
    ):
        return TestClient(app).post(
            "/api/v3/auth/web/token", headers={"Authorization": "Bearer firebase-id-token"}
        )


def _role(access_token: str) -> str:
    return jwt.decode(access_token, options={"verify_signature": False})["role"]


class TestConsoleLogin:
    def test_an_unverified_email_gets_no_elevated_role(self) -> None:
        response = _web_login(_firebase_claims(verified=False, provider="google.com"))
        assert response.status_code == 403
        assert "resolved User" in response.json()["detail"]

    def test_a_password_account_gets_no_elevated_role_even_when_verified(self) -> None:
        response = _web_login(_firebase_claims(verified=True, provider="password"))
        assert response.status_code == 403
        assert "resolved User" in response.json()["detail"]

    def test_an_unverified_password_account_gets_no_elevated_role(self) -> None:
        """The attack itself: register the operator's address with email/password."""
        response = _web_login(_firebase_claims(verified=False, provider="password"))
        assert response.status_code == 403

    def test_a_verified_google_sign_in_is_granted_the_role(self) -> None:
        response = _web_login(_firebase_claims(verified=True, provider="google.com"))
        assert response.status_code == 200, response.text
        assert _role(response.json()["access_token"]) == "SuperAdmin"

    def test_the_granted_role_survives_refresh(self) -> None:
        """The evidence rides in the server-side refresh record, so rotation keeps the role."""
        response = _web_login(_firebase_claims(verified=True, provider="google.com"))
        rotated = jwt_auth.refresh_token_pair(response.json()["refresh_token"])
        assert rotated is not None
        assert _role(rotated["access_token"]) == "SuperAdmin"


class TestGoogleIdTokens:
    """The direct Google ID-token path (verify_oauth2_token) checks email_verified too."""

    @pytest.mark.parametrize("verified,expected", [(False, "User"), (True, "SuperAdmin")])
    def test_email_verified_decides(self, verified: bool, expected: str) -> None:
        import asyncio

        id_info = {
            "sub": "google-sub",
            "email": OPERATOR,
            "email_verified": verified,
            "aud": security.settings.google_client_id,
            "iss": "https://accounts.google.com",
        }
        with patch.object(security.id_token, "verify_oauth2_token", lambda *a, **k: id_info):
            user_info = asyncio.run(
                security.verify_identity_token(
                    None, type("Creds", (), {"credentials": "google-id-token"})()
                )
            )
        assert get_user_role(user_info) == expected


class TestRoleResolution:
    def test_a_bare_email_claim_selects_nothing(self) -> None:
        assert get_user_role({"uid": "u", "email": OPERATOR}) == "User"

    def test_client_shaped_strings_do_not_count_as_verified(self) -> None:
        assert (
            get_user_role(
                {"uid": "u", "email": OPERATOR, "email_verified": "true", "sign_in_provider": "google.com"}
            )
            == "User"
        )

    def test_roles_json_by_email_needs_the_same_proof_but_by_uid_does_not(self, tmp_path) -> None:
        (tmp_path / "roles.json").write_text(
            '{"' + OPERATOR + '": "Admin", "uid-trusted": "Developer"}', encoding="utf-8"
        )
        unverified = {"uid": "uid-x", "email": OPERATOR, "email_verified": False,
                      "sign_in_provider": "password"}
        assert get_user_role(unverified) == "User"
        assert get_user_role({"uid": "uid-trusted", "email": "x@y.z"}) == "Developer"

    def test_the_server_stamped_claim_still_counts(self) -> None:
        """An access token's own role claim is this server's statement, not the user's."""
        assert get_user_role({"uid": "u", "email": OPERATOR, "role": "SuperAdmin"}) == "SuperAdmin"
