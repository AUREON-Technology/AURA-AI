"""Device attestation at the session-exchange boundary (SEC-11).

``services/security.py::verify_device_integrity`` is the only place attestation is enforced (the
mobile ``/api/v3/auth/token`` exchange); every later request rides the session it issued. Mock
attestation tokens exist for local development and are gated on ``allow_mock_attestation``.

The v3 production gate (``aura_core/config/settings.py``) refuses ``allow_mock_attestation`` in
staging/production -- but it reads ``AURA_ALLOW_MOCK_ATTESTATION`` first and the unprefixed
name only as a fallback, while the legacy ``config.Settings`` that ``verify_device_integrity``
actually consults reads only the unprefixed name (plus a ``.env`` file). With
``AURA_ALLOW_MOCK_ATTESTATION=false`` and ``ALLOW_MOCK_ATTESTATION=true`` the gate passed and
the mock tokens were accepted in production. The fix makes the check itself refuse mock tokens
in a production-like environment, the same defence in depth ``debug_bypass_allowed`` already
applies to ``DEBUG_MODE``.

No network: Play Integrity is the vendor boundary and is replaced by "unavailable", which is
what a deployment without credentials sees.
"""

from __future__ import annotations

import pytest

import services.security as security

MOCK_TOKENS = [
    "aura_dev_attestation",
    "android_play_integrity_mock_assertion_token_string",
    "ios_app_attest_mock_assertion_token_string",
    "local_developer_trust_token",
]


@pytest.fixture
def production(monkeypatch):
    """A production-like process whose two config loaders disagree about mock attestation."""
    import config

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("AURA_ENVIRONMENT", "production")
    monkeypatch.setenv("AURA_ALLOW_MOCK_ATTESTATION", "false")
    monkeypatch.setenv("ALLOW_MOCK_ATTESTATION", "true")
    legacy = config.Settings(debug_mode=False)
    monkeypatch.setattr(security, "settings", legacy)
    monkeypatch.setattr(security, "_get_play_integrity_service", lambda: None)
    monkeypatch.setattr(security, "redis_client", None)
    return legacy


class TestMockAttestationOutsideDev:
    def test_the_two_loaders_really_disagree(self, production) -> None:
        """Precondition, so the test below is about the real configuration shape: the v3 gate
        is satisfied while the legacy settings enable mock attestation."""
        from aura_core.config import load_settings

        v3 = load_settings(refresh=True, validate=False)
        assert v3.security.allow_mock_attestation is False
        assert production.allow_mock_attestation is True

    @pytest.mark.parametrize("token", MOCK_TOKENS)
    def test_mock_tokens_are_refused_in_production_even_if_enabled(
        self, production, token: str
    ) -> None:
        assert security.verify_device_integrity(token, "android") is False

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_refused_in_every_production_like_environment(
        self, production, monkeypatch, environment: str
    ) -> None:
        monkeypatch.setenv("AURA_ENVIRONMENT", environment)
        assert security.verify_device_integrity("aura_dev_attestation", "android") is False

    def test_still_accepted_in_local_development_when_enabled(self, production, monkeypatch) -> None:
        """The flag keeps working where it is meant to."""
        monkeypatch.setenv("AURA_ENVIRONMENT", "development")
        assert security.verify_device_integrity("aura_dev_attestation", "android") is True

    def test_debug_mode_does_not_reopen_it_in_production(self, production) -> None:
        production.debug_mode = True
        assert security.verify_device_integrity("aura_dev_attestation", "android") is False


class TestMissingAttestation:
    @pytest.mark.parametrize("token", [None, ""])
    def test_a_missing_token_is_refused_with_a_reason(self, production, token) -> None:
        assert security.verify_device_integrity(token, "android") is False
        assert security.attestation_failure_reason.get() == "missing_token"

    @pytest.mark.parametrize("platform", [None, "", "windows", "android'; DROP"])
    def test_an_unknown_platform_is_refused(self, production, platform) -> None:
        assert security.verify_device_integrity("some-real-looking-token", platform) is False

    def test_the_exchange_route_refuses_a_request_with_no_attestation_headers(
        self, production
    ) -> None:
        """Through HTTP: the mobile session exchange answers 403 before it ever looks at the
        identity token, so no identity-provider call is made at all."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.v2.auth import router

        app = FastAPI()
        app.include_router(router)
        response = TestClient(app).post(
            "/api/v3/auth/token", headers={"Authorization": "Bearer some-google-id-token"}
        )
        assert response.status_code == 403
        assert "missing_token" in response.json()["detail"]

    def test_the_unsupported_marker_is_never_attestation(self, production) -> None:
        assert security.verify_device_integrity("aura_attestation_unsupported", "android") is False
