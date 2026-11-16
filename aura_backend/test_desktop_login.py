"""Desktop app login (Unit 7, ADR-011) -- distinct from Unit 6's daemon pairing flow.

services.security.verify_identity_token's dual-audience acceptance (google_client_id for
mobile/web, google_desktop_client_id for the desktop loopback-OAuth exchange, since Google
requires a separate "Desktop app"-type OAuth client for a loopback redirect URI) and the
POST /api/v3/auth/desktop/login endpoint that mints a device-bound session for a real-but-
unattested identity -- an operator's only (F-09, owner decision 2026-11-16). Mirrors test_web_token.py's approach for the HTTP
layer and test_device_pairing.py's for exercising real service functions directly.
"""
import asyncio
import os
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


#: A verified Google sign-in by an operator: desktop sign-in is for Developer and above (F-09).
#: Email-list roles need both verification keys (SEC-08).
_OPERATOR_EMAIL = "ops@aura.ai"
_GOOGLE = {"email_verified": True, "sign_in_provider": "google.com"}
_OPERATOR_ENV = {"DEVELOPER_EMAILS": _OPERATOR_EMAIL}
_NO_OPERATOR_ENV = ("SUPERADMIN_EMAILS", "ADMIN_EMAILS", "DEVELOPER_EMAILS",
                    "AURA_SUPERADMIN_EMAILS", "AURA_ADMIN_EMAILS", "AURA_DEVELOPER_EMAILS")


def _operator(uid):
    return {"uid": uid, "email": _OPERATOR_EMAIL, "name": "Ops", **_GOOGLE}


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

    def setUp(self):
        self._env = patch.dict(os.environ, _OPERATOR_ENV)
        self._env.start()
        self.addCleanup(self._env.stop)

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def _post_as(self, user_info):
        async def identity(request, authorization):
            return dict(user_info)

        with patch("api.v2.auth.verify_identity_token", new=identity):
            return self._client().post(
                "/api/v3/auth/desktop/login", headers={"Authorization": "Bearer google-id"}
            )

    def test_refuses_a_consumer(self):
        """F-09: this route skips device attestation, so -- like /web/token -- it is closed
        below Developer. Before, any Google account got here the session a phone only gets after
        Play Integrity or App Attest."""
        response = self._post_as(
            {"uid": "u1", "email": "user@example.com", "name": "User", **_GOOGLE}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("desktop sign-in requires at least Developer", response.json()["detail"])

    def test_says_so_when_no_operator_is_configured(self):
        with patch.dict(os.environ, {}, clear=False):
            for name in _NO_OPERATOR_ENV:
                os.environ.pop(name, None)
            response = self._post_as(_operator("u1"))
        self.assertEqual(response.status_code, 403)
        self.assertIn("nobody can sign in to the desktop app", response.json()["detail"])

    def test_issues_a_device_session_to_an_operator(self):
        async def identity(request, authorization):
            return _operator("u1")

        with patch("api.v2.auth.verify_identity_token", new=identity):
            response = self._client().post(
                "/api/v3/auth/desktop/login", headers={"Authorization": "Bearer google-id"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertIn("access_token", body)
        self.assertIn("device_id", body)
        # FSA-004: a short access token plus a device-bound refresh token, not a 30-day token.
        self.assertIn("refresh_token", body)
        self.assertLessEqual(body["expires_in"], 15 * 60)

    def test_issued_token_verifies_as_a_device_token(self):
        async def identity(request, authorization):
            return _operator("u5")

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
            return _operator("u6")

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


class TestDesktopSessionIsShortAndDeviceBound(unittest.TestCase):
    """Audit FSA-004: the desktop app's sign-in is a device-bound session, not a month-long token."""

    def setUp(self):
        import services.jwt_auth as jwt_auth

        self._env = patch.dict(os.environ, _OPERATOR_ENV)
        self._env.start()
        self.addCleanup(self._env.stop)
        self._jwt = jwt_auth
        self._redis = patch("services.jwt_auth.get_redis_client", return_value=None)
        self._redis.start()
        self._reset()

    def tearDown(self):
        self._redis.stop()
        self._reset()

    def _reset(self):
        self._jwt._local_refresh_store.clear()
        self._jwt._local_revoked_devices.clear()
        self._jwt._local_device_owners.clear()
        self._jwt._local_device_meta.clear()

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def _refresh(self, client, refresh_token, role="Developer"):
        """/auth/refresh re-resolves the role through the users table; this suite has no
        database, so the role stamp is replaced with ``role``."""
        async def stamp(user_info):
            user_info["role"] = role
            return user_info

        with patch("api.v2.auth.stamp_role", new=stamp):
            return client.post("/api/v3/auth/refresh", json={"refresh_token": refresh_token})

    def _login(self, client, device_name="anas-laptop"):
        async def identity(request, authorization):
            return _operator("u9")

        with patch("api.v2.auth.verify_identity_token", new=identity):
            return client.post(
                "/api/v3/auth/desktop/login",
                headers={"Authorization": "Bearer google-id"},
                json={"device_name": device_name},
            )

    def _claims(self, token):
        from services.jwt_auth import verify_access_token

        return asyncio.run(verify_access_token(FakeRequest(), FakeCredentials(token)))

    def test_access_token_is_short_lived_and_names_the_device(self):
        body = self._login(self._client()).json()
        self.assertEqual(body["expires_in"], int(self._jwt.ACCESS_TOKEN_TTL.total_seconds()))
        self.assertEqual(self._claims(body["access_token"])["device_id"], body["device_id"])

    def test_refresh_keeps_the_device_binding(self):
        client = self._client()
        first = self._login(client).json()
        refreshed = self._refresh(client, first["refresh_token"])
        self.assertEqual(refreshed.status_code, 200)
        body = refreshed.json()
        self.assertNotEqual(body["refresh_token"], first["refresh_token"], "refresh must rotate")
        self.assertEqual(self._claims(body["access_token"])["device_id"], first["device_id"])
        again = self._refresh(client, first["refresh_token"])
        self.assertEqual(again.status_code, 401)

    def test_the_device_is_listed_and_revoke_ends_access_and_renewal(self):
        client = self._client()
        issued = self._login(client, device_name="anas-laptop").json()
        auth = {"Authorization": f"Bearer {issued['access_token']}"}

        listed = client.get("/api/v3/auth/desktop/devices", headers=auth)
        self.assertEqual(listed.status_code, 200)
        (device,) = listed.json()["devices"]
        self.assertEqual(device["device_id"], issued["device_id"])
        self.assertEqual(device["name"], "anas-laptop")
        self.assertEqual(device["kind"], "app")
        self.assertTrue(device["current"])
        self.assertTrue(device["created_at"])

        revoked = client.post(
            "/api/v3/auth/desktop/devices/revoke",
            json={"device_id": issued["device_id"]}, headers=auth,
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(client.get("/api/v3/auth/desktop/devices", headers=auth).status_code, 401)
        renew = self._refresh(client, issued["refresh_token"])
        self.assertEqual(renew.status_code, 401)

    def test_a_revoked_device_cannot_renew_even_with_a_surviving_refresh_token(self):
        client = self._client()
        issued = self._login(client).json()
        self._jwt._local_revoked_devices.add(issued["device_id"])
        renew = self._refresh(client, issued["refresh_token"])
        self.assertEqual(renew.status_code, 401)

    def test_a_desktop_session_ends_once_its_owner_is_no_longer_an_operator(self):
        """F-09: a desktop session issued before the operator rule, or to someone since
        demoted, ends at its next refresh -- and the spent refresh token cannot be retried."""
        client = self._client()
        issued = self._login(client).json()
        demoted = self._refresh(client, issued["refresh_token"], role="User")
        self.assertEqual(demoted.status_code, 401)
        self.assertIn("Desktop sign-in now requires", demoted.json()["detail"])
        again = self._refresh(client, issued["refresh_token"])
        self.assertEqual(again.status_code, 401)

    def test_an_ordinary_session_refresh_is_unchanged(self):
        """A phone or web session carries no device_id, so the desktop rule never applies."""
        from services.jwt_auth import issue_token_pair

        pair = issue_token_pair({"uid": "m1", "email": "m@example.com", "role": "User"})
        refreshed = self._refresh(self._client(), pair["refresh_token"], role="User")
        self.assertEqual(refreshed.status_code, 200)
        self.assertIsNone(self._claims(refreshed.json()["access_token"]).get("device_id"))

    def test_device_name_is_sanitised_and_bounded(self):
        body = self._login(self._client(), device_name="  evil\x00name" + "x" * 200).json()
        auth = {"Authorization": f"Bearer {body['access_token']}"}
        (device,) = self._client().get("/api/v3/auth/desktop/devices", headers=auth).json()["devices"]
        self.assertNotIn("\x00", device["name"])
        self.assertLessEqual(len(device["name"]), self._jwt.DEVICE_NAME_MAX_CHARS)
