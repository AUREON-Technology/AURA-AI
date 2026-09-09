"""Desktop app login (Unit 7, ADR-011) -- distinct from Unit 6's daemon pairing flow.

services.security.verify_identity_token's dual-audience acceptance (google_client_id for
mobile/web, google_desktop_client_id for the desktop loopback-OAuth exchange, since Google
requires a separate "Desktop app"-type OAuth client for a loopback redirect URI) and the
POST /api/v3/auth/desktop/login endpoint that mints a device-scoped token (not a full session
pair) for a real-but-unattested identity. Mirrors test_web_token.py's approach for the HTTP
layer and test_device_pairing.py's for exercising real service functions directly.
"""
import asyncio
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.v2.auth import router
from config import settings


class FakeCredentials:
    def __init__(self, token):
        self.credentials = token


class FakeRequest:
    pass


def _verify_identity(token):
    from services.security import verify_identity_token

    return asyncio.run(verify_identity_token(FakeRequest(), FakeCredentials(token)))


class TestDualAudienceIdentityVerification(unittest.TestCase):
    """services/security.py::verify_identity_token accepting either OAuth client's tokens."""

    def setUp(self):
        self._orig_desktop_client_id = settings.google_desktop_client_id

    def tearDown(self):
        settings.google_desktop_client_id = self._orig_desktop_client_id

    def test_accepts_the_mobile_web_audience(self):
        with patch(
            "services.security.id_token.verify_oauth2_token",
            return_value={
                "sub": "u1",
                "email": "a@b.com",
                "name": "A",
                "aud": settings.google_client_id,
            },
        ):
            identity = _verify_identity("tok")
        self.assertEqual(identity["uid"], "u1")
        self.assertEqual(identity["email"], "a@b.com")

    def test_accepts_the_desktop_audience_when_configured(self):
        settings.google_desktop_client_id = "desktop-client-id.apps.googleusercontent.com"
        with patch(
            "services.security.id_token.verify_oauth2_token",
            return_value={
                "sub": "u2",
                "email": "b@c.com",
                "name": "B",
                "aud": "desktop-client-id.apps.googleusercontent.com",
            },
        ):
            identity = _verify_identity("tok")
        self.assertEqual(identity["uid"], "u2")

    def test_rejects_the_desktop_audience_when_not_configured(self):
        """An unconfigured google_desktop_client_id must not silently widen who's accepted --
        the token is real and Google-signed, but for an audience this deployment never opted
        into trusting."""
        settings.google_desktop_client_id = ""
        with patch(
            "services.security.id_token.verify_oauth2_token",
            return_value={
                "sub": "u3",
                "email": "c@d.com",
                "aud": "some-other-client-id.apps.googleusercontent.com",
            },
        ), self.assertRaises(HTTPException) as ctx:
            _verify_identity("tok")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_rejects_an_unrecognized_audience_even_with_desktop_configured(self):
        settings.google_desktop_client_id = "desktop-client-id.apps.googleusercontent.com"
        with patch(
            "services.security.id_token.verify_oauth2_token",
            return_value={"sub": "u4", "email": "d@e.com", "aud": "attacker-client-id"},
        ), self.assertRaises(HTTPException) as ctx:
            _verify_identity("tok")
        self.assertEqual(ctx.exception.status_code, 401)


class TestDesktopLoginEndpoint(unittest.TestCase):
    """POST /api/v3/auth/desktop/login."""

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_issues_a_device_token_for_any_role_not_just_operators(self):
        """Unlike /web/token, an ordinary consumer must not be refused -- the desktop app is
        for every AURA user, not console operators."""

        async def identity(request, authorization):
            return {"uid": "u1", "email": "user@example.com", "name": "User"}

        with patch("api.v2.auth.verify_identity_token", new=identity):
            response = self._client().post(
                "/api/v3/auth/desktop/login", headers={"Authorization": "Bearer google-id"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertIn("access_token", body)
        self.assertIn("device_id", body)
        self.assertNotIn("refresh_token", body)

    def test_issued_token_verifies_as_a_device_token(self):
        async def identity(request, authorization):
            return {"uid": "u5", "email": "e@f.com", "name": "E"}

        with patch("api.v2.auth.verify_identity_token", new=identity):
            response = self._client().post(
                "/api/v3/auth/desktop/login", headers={"Authorization": "Bearer google-id"}
            )
        body = response.json()

        from services.jwt_auth import verify_access_token

        claims = asyncio.run(
            verify_access_token(FakeRequest(), FakeCredentials(body["access_token"]))
        )
        self.assertEqual(claims["uid"], "u5")
        self.assertEqual(claims["device_id"], body["device_id"])

    def test_persists_the_user(self):
        async def identity(request, authorization):
            return {"uid": "u6", "email": "g@h.com", "name": "G"}

        seen: dict = {}

        async def fake_get_or_create(db, user_info):
            seen["user"] = dict(user_info)
            return object()

        class _Session:
            async def commit(self) -> None:
                seen["committed"] = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        with (
            patch("api.v2.auth.verify_identity_token", new=identity),
            patch("infrastructure.db.base.is_configured", return_value=True),
            patch("infrastructure.db.base.get_session_factory", return_value=lambda: _Session()),
            patch("api.v2.auth.get_or_create_user", new=fake_get_or_create),
        ):
            response = self._client().post(
                "/api/v3/auth/desktop/login", headers={"Authorization": "Bearer google-id"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["user"]["uid"], "u6")
        self.assertTrue(seen.get("committed"))

    def test_without_a_bearer_token_is_rejected(self):
        # verify_identity_token has a testing-only mock-user bypass for a missing header
        # (debug_bypass_allowed) -- disabled here so this actually exercises the "no
        # Authorization header" rejection path rather than the test-mode shortcut.
        with patch("services.security.debug_bypass_allowed", return_value=False):
            response = self._client().post("/api/v3/auth/desktop/login")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
