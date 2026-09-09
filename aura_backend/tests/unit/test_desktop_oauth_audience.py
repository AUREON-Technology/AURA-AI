"""verify_identity_token must accept either the mobile/web or desktop
loopback OAuth client as `aud`, and reject anything else -- see
Settings.google_desktop_client_id and DesktopGoogleOAuthService
(aura_client/lib/features/auth/data/desktop_google_oauth_service.dart)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from config import settings
from services.security import verify_identity_token

MOBILE_CLIENT_ID = "mobile-client.apps.googleusercontent.com"
DESKTOP_CLIENT_ID = "desktop-client.apps.googleusercontent.com"


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_accepts_the_configured_mobile_web_audience(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", MOBILE_CLIENT_ID)
    monkeypatch.setattr(settings, "google_desktop_client_id", "")

    with patch(
        "services.security.id_token.verify_oauth2_token",
        return_value={"sub": "u1", "email": "user@example.com", "aud": MOBILE_CLIENT_ID},
    ):
        result = await verify_identity_token(None, _bearer("token"))

    assert result["uid"] == "u1"
    assert result["email"] == "user@example.com"


async def test_accepts_the_configured_desktop_audience(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", MOBILE_CLIENT_ID)
    monkeypatch.setattr(settings, "google_desktop_client_id", DESKTOP_CLIENT_ID)

    with patch(
        "services.security.id_token.verify_oauth2_token",
        return_value={"sub": "u2", "email": "desktop@example.com", "aud": DESKTOP_CLIENT_ID},
    ):
        result = await verify_identity_token(None, _bearer("token"))

    assert result["uid"] == "u2"


async def test_rejects_an_unrecognized_audience(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", MOBILE_CLIENT_ID)
    monkeypatch.setattr(settings, "google_desktop_client_id", DESKTOP_CLIENT_ID)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "debug_mode", False)

    with patch(
        "services.security.id_token.verify_oauth2_token",
        return_value={"sub": "attacker", "email": "attacker@example.com", "aud": "someone-elses-client-id"},
    ):
        with pytest.raises(Exception):
            await verify_identity_token(None, _bearer("token"))


async def test_desktop_audience_disabled_by_default(monkeypatch) -> None:
    """An empty google_desktop_client_id (the default) must not accidentally
    accept an empty-string `aud` -- it should just never match."""
    monkeypatch.setattr(settings, "google_client_id", MOBILE_CLIENT_ID)
    monkeypatch.setattr(settings, "google_desktop_client_id", "")
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "debug_mode", False)

    with patch(
        "services.security.id_token.verify_oauth2_token",
        return_value={"sub": "u3", "email": "u3@example.com", "aud": ""},
    ):
        with pytest.raises(Exception):
            await verify_identity_token(None, _bearer("token"))
