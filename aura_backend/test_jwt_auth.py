import time
import unittest
from unittest.mock import patch

import jwt as pyjwt

import services.jwt_auth as jwt_auth
from services.jwt_auth import (
    create_access_token,
    issue_token_pair,
    refresh_token_pair,
    revoke_refresh_token,
    verify_access_token,
)


class FakeCredentials:
    def __init__(self, token):
        self.credentials = token


class FakeRequest:
    pass


class TestJwtAuth(unittest.TestCase):
    def setUp(self):
        # Force the in-process refresh-token fallback store so these tests
        # don't depend on a real Redis instance (mirrors test_security.py's
        # approach of forcing redis_client = None for isolation).
        jwt_auth._local_refresh_store.clear()
        self._redis_patch = patch("services.jwt_auth.get_redis_client", return_value=None)
        self._redis_patch.start()

    def tearDown(self):
        self._redis_patch.stop()
        jwt_auth._local_refresh_store.clear()

    def test_access_token_round_trip(self):
        user_info = {"uid": "abc123", "email": "user@aura.ai", "name": "Test User", "picture": None, "role": "User"}
        token = create_access_token(user_info)
        decoded = pyjwt.decode(token, jwt_auth._secret(), algorithms=[jwt_auth.ALGORITHM])
        self.assertEqual(decoded["sub"], "abc123")
        self.assertEqual(decoded["email"], "user@aura.ai")

    def test_issue_token_pair_returns_both_tokens(self):
        pair = issue_token_pair({"uid": "u1", "email": "a@b.com"})
        self.assertIn("access_token", pair)
        self.assertIn("refresh_token", pair)
        self.assertEqual(pair["token_type"], "bearer")
        self.assertEqual(pair["expires_in"], 900)  # 15 minutes

    async def _verify(self, token):
        return await verify_access_token(FakeRequest(), FakeCredentials(token))

    def test_verify_access_token_accepts_valid_token(self):
        import asyncio

        pair = issue_token_pair({"uid": "u2", "email": "b@c.com", "role": "Admin"})
        result = asyncio.run(self._verify(pair["access_token"]))
        self.assertEqual(result["uid"], "u2")
        self.assertEqual(result["role"], "Admin")

    def test_verify_access_token_rejects_expired_token(self):
        import asyncio
        from fastapi import HTTPException

        # Craft an already-expired token directly rather than sleeping 15 minutes.
        payload = {"sub": "u3", "email": "d@e.com", "role": "User", "iat": time.time() - 1000, "exp": time.time() - 1}
        expired = pyjwt.encode(payload, jwt_auth._secret(), algorithm=jwt_auth.ALGORITHM)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self._verify(expired))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_verify_access_token_rejects_garbage_token(self):
        import asyncio
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self._verify("not.a.jwt"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_verify_access_token_rejects_wrong_signature(self):
        import asyncio
        from fastapi import HTTPException

        payload = {"sub": "u4", "email": "f@g.com", "role": "User", "exp": time.time() + 900}
        forged = pyjwt.encode(payload, "wrong-secret", algorithm=jwt_auth.ALGORITHM)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self._verify(forged))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_refresh_token_pair_rotates_and_invalidates_old_token(self):
        pair = issue_token_pair({"uid": "u5", "email": "h@i.com"})
        old_refresh = pair["refresh_token"]

        refreshed = refresh_token_pair(old_refresh)
        self.assertIsNotNone(refreshed)
        self.assertNotEqual(refreshed["refresh_token"], old_refresh)

        # The old refresh token must no longer work (single-use rotation).
        self.assertIsNone(refresh_token_pair(old_refresh))

    def test_refresh_with_unknown_token_returns_none(self):
        self.assertIsNone(refresh_token_pair("this-token-was-never-issued"))

    def test_revoke_refresh_token_invalidates_it(self):
        pair = issue_token_pair({"uid": "u6", "email": "j@k.com"})
        revoke_refresh_token(pair["refresh_token"])
        self.assertIsNone(refresh_token_pair(pair["refresh_token"]))


if __name__ == "__main__":
    unittest.main()
