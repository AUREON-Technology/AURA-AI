import unittest
import os
import json
import base64
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from main import app, RateLimitMiddleware
from services.security import verify_device_integrity, verify_token
from services.rbac import require_role
from config import settings

class TestSecurityHardening(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Clear rate limiting history before each test to ensure a clean state
        RateLimitMiddleware.request_history.clear()

        # main.py's rate limiter holds its OWN Redis handle (`_rate_limit_redis`),
        # separate from services.security.redis_client below. Without pinning it
        # to None the limiter takes the Redis path, whose per-IP counter is keyed
        # on a shared 60s window and therefore leaks between tests: once
        # test_rate_limiting deliberately exhausts the window, every later test in
        # that minute gets a 429 instead of its expected status. CI has no Redis
        # service so this passed there and only failed in production-like envs.
        import main
        self.orig_rate_limit_redis = main._rate_limit_redis
        main._rate_limit_redis = None

        # Save original settings
        self.orig_debug_mode = settings.debug_mode
        self.orig_allow_mock_attestation = settings.allow_mock_attestation
        
        # Force redis_client to None to ensure test runs environment-independently using _local_nonce_cache
        import services.security
        self.orig_redis_client = services.security.redis_client
        services.security.redis_client = None

    def tearDown(self):
        # Restore original settings
        settings.debug_mode = self.orig_debug_mode
        settings.allow_mock_attestation = self.orig_allow_mock_attestation

        import main
        main._rate_limit_redis = self.orig_rate_limit_redis

        # Restore redis_client
        import services.security
        services.security.redis_client = self.orig_redis_client
        
        app.dependency_overrides.clear()


    # ----------------------------------------------------
    # Rate Limiting & Payload Size Tests
    # ----------------------------------------------------

    def test_payload_size_limit_under_5mb(self):
        """Verify that requests under 5MB are allowed through the middleware."""
        response = self.client.post(
            "/api/memory/save",
            headers={"Content-Length": "100"},
            json={"fact": "Test memory payload size"}
        )
        self.assertNotEqual(response.status_code, 413)

    def test_payload_size_limit_exceeded(self):
        """Verify that requests exceeding 5MB are blocked with HTTP 413 Payload Too Large."""
        response = self.client.post(
            "/api/memory/save",
            headers={"Content-Length": str(6 * 1024 * 1024)},
            json={"fact": "x" * 100}  # Small payload body, but Content-Length header claims 6MB
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("Payload Too Large", response.text)

    def test_rate_limiting(self):
        """Verify rate limiter blocks request volume exceeding 100 req/min with HTTP 429."""
        # Make 100 requests (the limit)
        for i in range(100):
            response = self.client.get("/api/memory/list")
            self.assertNotEqual(response.status_code, 429)

        # The 101st request should be rate-limited
        response = self.client.get("/api/memory/list")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Rate limit exceeded", response.text)

    # ----------------------------------------------------
    # Device Attestation Tests
    # ----------------------------------------------------

    def test_device_integrity_bypass_in_debug(self):
        """Verify that device integrity checks are bypassed in debug/testing mode."""
        settings.debug_mode = True
        self.assertTrue(verify_device_integrity("any_token", "android"))

    @patch('services.security.os.environ')
    def test_device_integrity_production_missing_token(self, mock_env):
        """Verify that missing token returns False in production mode."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = False
        
        self.assertFalse(verify_device_integrity(None, "android"))

    @patch('services.security.os.environ')
    def test_device_integrity_mock_tokens_disabled_in_production(self, mock_env):
        """Verify mock tokens are rejected in production if allow_mock_attestation is False."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = False
        
        self.assertFalse(verify_device_integrity("android_play_integrity_mock_assertion_token_string", "android"))

    @patch('services.security.os.environ')
    def test_device_integrity_mock_tokens_allowed_when_configured(self, mock_env):
        """Verify mock tokens are accepted if allow_mock_attestation is True."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = True
        
        self.assertTrue(verify_device_integrity("android_play_integrity_mock_assertion_token_string", "android"))
        self.assertTrue(verify_device_integrity("aura_dev_attestation", "android"))

    @patch('services.security.os.environ')
    def test_device_integrity_unsupported_marker_rejected(self, mock_env):
        """Release-client unsupported marker is never treated as attestation."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = True

        self.assertFalse(verify_device_integrity("aura_attestation_unsupported", "android"))

    @patch('services.security.os.environ')
    def test_device_integrity_android_invalid_jws(self, mock_env):
        """Verify invalid JWS format token is rejected."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = False
        
        self.assertFalse(verify_device_integrity("invalid_jws_token", "android"))

    @patch('services.security.os.environ')
    @patch('services.security._get_play_integrity_service')
    def test_device_integrity_android_crypto_verification_success(self, mock_get_service, mock_env):
        """Verify cryptographic verification using Google Play Integrity API succeeds with correct verdict."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = False
        
        # Mock the Google API Client
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        
        mock_response = {
            "tokenPayloadExternal": {
                "requestDetails": {
                    "packageName": settings.android_package_name,
                    "nonce": "test_nonce"
                },
                "deviceIntegrity": {
                    "deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]
                }
            }
        }
        mock_service.v1().decodeIntegrityToken().execute.return_value = mock_response
        
        # Seed the local nonce cache
        from services.security import _local_nonce_cache
        _local_nonce_cache.add("test_nonce")
        
        self.assertTrue(verify_device_integrity("valid.jws.token", "android", expected_nonce="test_nonce"))

    @patch('services.security.os.environ')
    @patch('services.security._get_play_integrity_service')
    def test_device_integrity_android_crypto_verification_failure(self, mock_get_service, mock_env):
        """Verify cryptographic verification fails if Google API reports issues (e.g. invalid package or verdict)."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = False
        
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        
        mock_response = {
            "tokenPayloadExternal": {
                "requestDetails": {
                    "packageName": "com.hacker.app",  # Wrong package
                    "nonce": "test_nonce"
                },
                "deviceIntegrity": {
                    "deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]
                }
            }
        }
        mock_service.v1().decodeIntegrityToken().execute.return_value = mock_response
        
        # Seed the local nonce cache
        from services.security import _local_nonce_cache
        _local_nonce_cache.add("test_nonce")
        
        self.assertFalse(verify_device_integrity("valid.jws.token", "android", expected_nonce="test_nonce"))

    @patch('services.security.os.environ')
    @patch('services.security._get_play_integrity_service')
    def test_device_integrity_android_unsigned_jws_rejected(self, mock_get_service, mock_env):
        """A crafted JWS payload is not attestation when Play Integrity is unavailable."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = False
        mock_get_service.return_value = None

        payload = {
            "requestDetails": {
                "packageName": settings.android_package_name,
                "nonce": "test_nonce",
            },
            "deviceIntegrity": {
                "deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]
            },
        }
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        token = f"eyJhbGciOiJub25lIn0.{payload_b64}.fakesig"
        self.assertFalse(verify_device_integrity(token, "android"))

    @patch('services.security.os.environ')
    @patch('services.security._get_play_integrity_service')
    def test_device_integrity_android_api_failure_is_rejected(self, mock_get_service, mock_env):
        """Play Integrity errors fail closed instead of decoding the token locally."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = False
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.v1().decodeIntegrityToken().execute.side_effect = RuntimeError("api down")
        self.assertFalse(verify_device_integrity("valid.jws.token", "android"))

    @patch('services.security.os.environ')
    def test_device_integrity_ios_placeholder_rejected(self, mock_env):
        """Fake iOS blobs are refused; only cryptographic App Attest verify can pass."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = False
        harmless = base64.b64encode(b"looks_valid_device_blob").decode()
        self.assertFalse(verify_device_integrity(harmless, "ios"))
        self.assertFalse(verify_device_integrity("ios_app_attest_mock_assertion_token_string", "ios"))
        self.assertFalse(verify_device_integrity("aura_attestation_unsupported", "ios"))

    @patch('services.security.os.environ')
    @patch('services.attestation.app_attest.verify_app_attest', return_value=True)
    def test_device_integrity_ios_crypto_verification_success(self, mock_verify, mock_env):
        """A valid-shaped App Attest envelope is accepted when the verifier agrees."""
        mock_env.get.return_value = "False"
        settings.debug_mode = False
        settings.allow_mock_attestation = False
        token = json.dumps({
            "format": "apple-appattest",
            "key_id": "abc",
            "attestation": base64.b64encode(b"cbor").decode(),
            "challenge": "n",
        })
        self.assertTrue(verify_device_integrity(token, "ios"))
        mock_verify.assert_called_once()
        _, kwargs = mock_verify.call_args
        self.assertEqual(kwargs["bundle_id"], settings.ios_bundle_id)

    def test_production_debug_mode_cannot_mint_mock_user(self):
        """DEBUG_MODE in a production-like env must not return mock_user_123."""
        settings.debug_mode = True
        orig_env = os.environ.get("AURA_ENVIRONMENT")
        orig_testing = os.environ.get("TESTING")
        os.environ["AURA_ENVIRONMENT"] = "production"
        os.environ.pop("TESTING", None)
        try:
            with patch("services.security.verify_device_integrity", return_value=True):
                response = self.client.get("/api/memory/list")
            self.assertNotIn("mock_user_123", response.text)
            self.assertIn(response.status_code, (401, 403))
        finally:
            if orig_env is None:
                os.environ.pop("AURA_ENVIRONMENT", None)
            else:
                os.environ["AURA_ENVIRONMENT"] = orig_env
            if orig_testing is None:
                os.environ.pop("TESTING", None)
            else:
                os.environ["TESTING"] = orig_testing

    def test_legacy_settings_refuse_debug_mode_in_production(self):
        orig_env = os.environ.get("AURA_ENVIRONMENT")
        orig_testing = os.environ.get("TESTING")
        os.environ["AURA_ENVIRONMENT"] = "production"
        os.environ.pop("TESTING", None)
        os.environ["DEBUG_MODE"] = "true"
        try:
            from aura_core.domain.errors import ConfigurationError
            from config import Settings
            with self.assertRaises(ConfigurationError):
                Settings(_env_file=None)
        finally:
            os.environ.pop("DEBUG_MODE", None)
            if orig_env is None:
                os.environ.pop("AURA_ENVIRONMENT", None)
            else:
                os.environ["AURA_ENVIRONMENT"] = orig_env
            if orig_testing is not None:
                os.environ["TESTING"] = orig_testing


    # ----------------------------------------------------
    # Role-Based Access Control (RBAC) Tests
    # ----------------------------------------------------

    def test_rbac_user_role_forbidden_on_admin_endpoints(self):
        """Verify standard users cannot access admin endpoints."""
        app.dependency_overrides[verify_token] = lambda: {
            "uid": "user_123",
            "email": "user@aura.ai",
            "role": "User"
        }
        response = self.client.get("/api/learning/export")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Access forbidden", response.text)

    def test_rbac_admin_role_allowed_on_admin_endpoints(self):
        """Verify admin users can access admin endpoints."""
        app.dependency_overrides[verify_token] = lambda: {
            "uid": "admin_123",
            "email": "admin@aura.ai",
            "role": "Admin"
        }
        # Calling export will invoke the learning pipeline which might return empty/null or raise an error,
        # but the request should pass the authorization layer (not return 403).
        response = self.client.get("/api/learning/export")
        self.assertNotEqual(response.status_code, 403)

    def test_rbac_developer_role_allowed_on_developer_endpoints(self):
        """Verify developer users can access developer endpoints."""
        app.dependency_overrides[verify_token] = lambda: {
            "uid": "dev_123",
            "email": "dev@aura.ai",
            "role": "Developer"
        }
        response = self.client.get("/api/learning/evaluate")
        self.assertNotEqual(response.status_code, 403)

if __name__ == "__main__":
    unittest.main()
