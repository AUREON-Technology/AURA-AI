"""Desktop device pairing (services/device_pairing.py, services/jwt_auth.py's device-token
additions, and the /api/v3/auth/desktop/* endpoints in api/v2/auth.py). See
docs/architecture/011-desktop-engineer-agent.md (ADR-011) for the design.

Mirrors test_jwt_auth.py's approach: force the in-process fallback stores (no real Redis needed)
via patching get_redis_client to None, exercise the real service functions directly for the
service-layer tests, and use a real access token obtained from issue_token_pair (not a mocked
Google identity) as the "already-authenticated approving client" for the endpoint-level tests --
this is exactly the credential shape /token and /web/token already produce, so it exercises the
real verify_access_token dependency the endpoints actually use.
"""
import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import services.device_pairing as device_pairing
import services.jwt_auth as jwt_auth
from services.device_pairing import approve_pairing, poll_pairing, start_pairing
from services.jwt_auth import (
    is_device_revoked,
    issue_device_token,
    issue_token_pair,
    revoke_device,
    verify_access_token,
)


class FakeCredentials:
    def __init__(self, token):
        self.credentials = token


class FakeRequest:
    pass


def _verify(token):
    return asyncio.run(verify_access_token(FakeRequest(), FakeCredentials(token)))


class TestDevicePairingService(unittest.TestCase):
    """services/device_pairing.py against its in-process fallback store."""

    def setUp(self):
        device_pairing._local_pairings.clear()
        device_pairing._local_pairing_codes.clear()
        self._redis_patch = patch("services.device_pairing.get_redis_client", return_value=None)
        self._redis_patch.start()

    def tearDown(self):
        self._redis_patch.stop()
        device_pairing._local_pairings.clear()
        device_pairing._local_pairing_codes.clear()

    def test_start_pairing_returns_a_code_and_id(self):
        session = start_pairing()
        self.assertIn("pairing_id", session)
        self.assertIn("user_code", session)
        self.assertEqual(session["expires_in"], 600)  # PAIRING_TTL, 10 minutes
        self.assertEqual(session["interval"], device_pairing.POLL_INTERVAL_S)
        # XXXX-XXXX shape, from the unambiguous alphabet only.
        code = session["user_code"]
        self.assertRegex(code, r"^[A-HJ-NP-TV-Z2-9]{4}-[A-HJ-NP-TV-Z2-9]{4}$")

    def test_poll_before_approval_is_pending(self):
        session = start_pairing()
        self.assertEqual(poll_pairing(session["pairing_id"]), {"status": "pending"})

    def test_poll_unknown_pairing_id_is_expired(self):
        self.assertEqual(poll_pairing("never-issued"), {"status": "expired"})

    def test_approve_with_unknown_code_fails(self):
        self.assertIsNone(approve_pairing("ZZZZ-ZZZZ", {"uid": "u1"}))

    def test_approve_then_poll_returns_the_approving_identity(self):
        session = start_pairing()
        device_id = approve_pairing(session["user_code"], {"uid": "u1", "email": "a@b.com", "role": "User"})
        self.assertIsNotNone(device_id)

        outcome = poll_pairing(session["pairing_id"])
        self.assertEqual(outcome["status"], "approved")
        self.assertEqual(outcome["device_id"], device_id)
        self.assertEqual(outcome["user_info"]["uid"], "u1")

    def test_poll_consumes_the_approval_exactly_once(self):
        """A second poll after the approved one must not re-approve -- one token per approval."""
        session = start_pairing()
        approve_pairing(session["user_code"], {"uid": "u1"})
        first = poll_pairing(session["pairing_id"])
        self.assertEqual(first["status"], "approved")

        second = poll_pairing(session["pairing_id"])
        self.assertEqual(second, {"status": "expired"})

    def test_approving_an_already_approved_code_fails(self):
        session = start_pairing()
        approve_pairing(session["user_code"], {"uid": "u1"})
        self.assertIsNone(approve_pairing(session["user_code"], {"uid": "u2"}))

    def test_approve_is_case_insensitive_and_trims_whitespace(self):
        session = start_pairing()
        lowered = f"  {session['user_code'].lower()}  "
        device_id = approve_pairing(lowered, {"uid": "u1"})
        self.assertIsNotNone(device_id)


class TestDeviceToken(unittest.TestCase):
    """services/jwt_auth.py's device-token issuance, verification, and revocation."""

    def setUp(self):
        jwt_auth._local_revoked_devices.clear()
        jwt_auth._local_device_owners.clear()
        self._redis_patch = patch("services.jwt_auth.get_redis_client", return_value=None)
        self._redis_patch.start()

    def tearDown(self):
        self._redis_patch.stop()
        jwt_auth._local_revoked_devices.clear()
        jwt_auth._local_device_owners.clear()

    def test_issue_device_token_has_no_refresh_token(self):
        token = issue_device_token({"uid": "u1", "email": "a@b.com", "role": "User"}, device_id="d1")
        self.assertIn("access_token", token)
        self.assertNotIn("refresh_token", token)
        self.assertEqual(token["device_id"], "d1")
        self.assertEqual(token["expires_in"], int(jwt_auth.DEVICE_ACCESS_TOKEN_TTL.total_seconds()))

    def test_device_token_verifies_and_carries_device_id(self):
        token = issue_device_token({"uid": "u1", "email": "a@b.com", "role": "User"}, device_id="d1")
        claims = _verify(token["access_token"])
        self.assertEqual(claims["uid"], "u1")
        self.assertEqual(claims["device_id"], "d1")

    def test_ordinary_access_token_has_no_device_id_claim(self):
        """A mobile/web session token must not accidentally look like a device token."""
        token = issue_token_pair({"uid": "u1", "email": "a@b.com"})
        claims = _verify(token["access_token"])
        self.assertIsNone(claims["device_id"])

    def test_revoke_by_the_owning_account_succeeds_and_blocks_future_verification(self):
        token = issue_device_token({"uid": "owner", "email": "a@b.com", "role": "User"}, device_id="d1")
        self.assertFalse(is_device_revoked("d1"))

        ok = revoke_device("d1", requesting_uid="owner")
        self.assertTrue(ok)
        self.assertTrue(is_device_revoked("d1"))

        with self.assertRaises(HTTPException) as ctx:
            _verify(token["access_token"])
        self.assertEqual(ctx.exception.status_code, 401)

    def test_revoke_by_a_different_account_fails_and_does_not_revoke(self):
        issue_device_token({"uid": "owner", "email": "a@b.com", "role": "User"}, device_id="d1")
        ok = revoke_device("d1", requesting_uid="someone-else")
        self.assertFalse(ok)
        self.assertFalse(is_device_revoked("d1"))

    def test_revoke_of_unknown_device_fails(self):
        self.assertFalse(revoke_device("never-issued", requesting_uid="anyone"))


class TestDesktopPairingEndpoints(unittest.TestCase):
    """The actual HTTP endpoints, via a real (unmocked) FastAPI app."""

    def setUp(self):
        from aura_service import app

        self.client = TestClient(app)
        device_pairing._local_pairings.clear()
        device_pairing._local_pairing_codes.clear()
        jwt_auth._local_revoked_devices.clear()
        jwt_auth._local_device_owners.clear()
        self._pairing_redis_patch = patch("services.device_pairing.get_redis_client", return_value=None)
        self._pairing_redis_patch.start()
        self._jwt_redis_patch = patch("services.jwt_auth.get_redis_client", return_value=None)
        self._jwt_redis_patch.start()
        # A real, valid access token -- the same credential shape /token or /web/token would
        # have handed the approving client, obtained directly rather than via a mocked Google
        # identity exchange this test doesn't need.
        self.approver_token = issue_token_pair(
            {"uid": "approver-1", "email": "approver@example.com", "role": "User"}
        )["access_token"]

    def tearDown(self):
        self._pairing_redis_patch.stop()
        self._jwt_redis_patch.stop()
        device_pairing._local_pairings.clear()
        device_pairing._local_pairing_codes.clear()
        jwt_auth._local_revoked_devices.clear()
        jwt_auth._local_device_owners.clear()

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_start_pair_approve_poll_full_round_trip(self):
        start = self.client.post("/api/v3/auth/desktop/pair/start")
        self.assertEqual(start.status_code, 200)
        body = start.json()
        pairing_id, user_code = body["pairing_id"], body["user_code"]

        # Not approved yet.
        pending = self.client.post("/api/v3/auth/desktop/pair/poll", json={"pairing_id": pairing_id})
        self.assertEqual(pending.json()["status"], "pending")

        approve = self.client.post(
            "/api/v3/auth/desktop/pair/approve",
            json={"user_code": user_code},
            headers=self._auth(self.approver_token),
        )
        self.assertEqual(approve.status_code, 200)
        device_id = approve.json()["device_id"]

        poll = self.client.post("/api/v3/auth/desktop/pair/poll", json={"pairing_id": pairing_id})
        self.assertEqual(poll.status_code, 200)
        poll_body = poll.json()
        self.assertEqual(poll_body["status"], "approved")
        self.assertEqual(poll_body["device_id"], device_id)
        self.assertIn("access_token", poll_body)
        self.assertEqual(poll_body["token_type"], "bearer")

        # The issued token actually authenticates a v3-shaped call: verify_access_token accepts
        # it and reports the approving user's own identity (this is *their* device, not a new
        # unrelated account).
        claims = _verify(poll_body["access_token"])
        self.assertEqual(claims["uid"], "approver-1")
        self.assertEqual(claims["device_id"], device_id)

    def test_approve_without_a_bearer_token_is_rejected(self):
        start = self.client.post("/api/v3/auth/desktop/pair/start")
        user_code = start.json()["user_code"]
        response = self.client.post("/api/v3/auth/desktop/pair/approve", json={"user_code": user_code})
        self.assertEqual(response.status_code, 401)

    def test_approve_with_wrong_code_is_rejected(self):
        response = self.client.post(
            "/api/v3/auth/desktop/pair/approve",
            json={"user_code": "ZZZZ-ZZZZ"},
            headers=self._auth(self.approver_token),
        )
        self.assertEqual(response.status_code, 400)

    def test_poll_never_leaks_a_token_before_approval(self):
        start = self.client.post("/api/v3/auth/desktop/pair/start")
        pairing_id = start.json()["pairing_id"]
        poll = self.client.post("/api/v3/auth/desktop/pair/poll", json={"pairing_id": pairing_id})
        body = poll.json()
        self.assertEqual(body["status"], "pending")
        self.assertIsNone(body.get("access_token"))

    def test_revoke_endpoint_requires_ownership(self):
        start = self.client.post("/api/v3/auth/desktop/pair/start")
        body = start.json()
        self.client.post(
            "/api/v3/auth/desktop/pair/approve",
            json={"user_code": body["user_code"]},
            headers=self._auth(self.approver_token),
        )
        poll = self.client.post("/api/v3/auth/desktop/pair/poll", json={"pairing_id": body["pairing_id"]})
        device_id = poll.json()["device_id"]

        other_token = issue_token_pair({"uid": "someone-else", "email": "x@y.com", "role": "User"})["access_token"]
        denied = self.client.post(
            "/api/v3/auth/desktop/devices/revoke",
            json={"device_id": device_id},
            headers=self._auth(other_token),
        )
        self.assertEqual(denied.status_code, 404)

        allowed = self.client.post(
            "/api/v3/auth/desktop/devices/revoke",
            json={"device_id": device_id},
            headers=self._auth(self.approver_token),
        )
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
