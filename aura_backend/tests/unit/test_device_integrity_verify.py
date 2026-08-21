"""Device integrity glue: Android fail-closed + iOS App Attest mocks.

Does not import ``main`` (that process boots the full app). These tests call
``verify_device_integrity`` directly with Play Integrity / Apple clients mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from config import settings
from services.security import verify_device_integrity


@pytest.fixture(autouse=True)
def _restore_security_flags():
    orig_debug = settings.debug_mode
    orig_mock = settings.allow_mock_attestation
    yield
    settings.debug_mode = orig_debug
    settings.allow_mock_attestation = orig_mock


def _production_mode():
    settings.debug_mode = False
    settings.allow_mock_attestation = False


@patch("services.security.debug_bypass_allowed", return_value=False)
@patch("services.security._get_play_integrity_service", return_value=None)
def test_android_unsigned_or_missing_api_is_rejected(_service, _bypass):
    _production_mode()
    assert verify_device_integrity("valid.jws.token", "android") is False
    assert verify_device_integrity("aura_attestation_unsupported", "android") is False


@patch("services.security.debug_bypass_allowed", return_value=False)
@patch("services.security._get_play_integrity_service")
def test_android_play_integrity_mock_accepted(mock_get_service, _bypass):
    _production_mode()
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    mock_service.v1().decodeIntegrityToken().execute.return_value = {
        "tokenPayloadExternal": {
            "requestDetails": {"packageName": settings.android_package_name, "nonce": "n"},
            "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
        }
    }
    assert verify_device_integrity("header.payload.sig", "android") is True


@patch("services.security.debug_bypass_allowed", return_value=False)
@patch("services.attestation.app_attest.verify_app_attest", return_value=True)
def test_ios_valid_envelope_accepted_when_verifier_agrees(mock_verify, _bypass):
    _production_mode()
    token = json.dumps(
        {
            "format": "apple-appattest",
            "key_id": "abc",
            "attestation": "dGVzdA==",
            "challenge": "n",
        }
    )
    assert verify_device_integrity(token, "ios") is True
    mock_verify.assert_called_once()
    _, kwargs = mock_verify.call_args
    assert kwargs["bundle_id"] == settings.ios_bundle_id


@patch("services.security.debug_bypass_allowed", return_value=False)
def test_ios_fake_tokens_rejected(_bypass):
    _production_mode()
    assert verify_device_integrity("ios_app_attest_mock_assertion_token_string", "ios") is False
    assert verify_device_integrity("aura_attestation_unsupported", "ios") is False
    assert verify_device_integrity("not-an-attestation", "ios") is False
