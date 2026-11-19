"""SaaS P1-I review #1: a raw Google ID token must not get past a second factor.

The app asks for the authenticator code through Firebase, and Firebase issues no ID token without
it. But ``verify_identity_token`` also accepts a raw Google ID token (the desktop loopback login,
older clients), which Firebase never sees. For an account with a second factor enrolled, that path
is refused, so anyone holding only the Google credential cannot exchange it for a session.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import services.security as security
from config import settings
from services.security import verify_identity_token

CLIENT = "mobile-client.apps.googleusercontent.com"


@pytest.fixture(autouse=True)
def _google(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", CLIENT)
    monkeypatch.setattr(settings, "google_desktop_client_id", "")
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "debug_mode", False)


def _bearer() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="google-token")


def _google_token():
    return patch("services.security.id_token.verify_oauth2_token",
                 return_value={"sub": "g-1", "email": "ops@example.com", "aud": CLIENT, "email_verified": True})


async def test_a_google_token_for_an_account_with_a_second_factor_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(security, "firebase_app", object())
    monkeypatch.setattr(security, "_firebase_second_factor_enrolled", lambda sub, email: True)
    with _google_token(), pytest.raises(HTTPException) as refused:
        await verify_identity_token(None, _bearer())
    assert refused.value.status_code == 401
    assert "two-step" in str(refused.value.detail)


async def test_a_google_token_without_a_second_factor_still_signs_in(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(security, "firebase_app", object())
    monkeypatch.setattr(security, "_firebase_second_factor_enrolled", lambda sub, email: seen.append((sub, email)) or False)
    with _google_token():
        result = await verify_identity_token(None, _bearer())
    assert result["uid"] == "g-1"
    assert seen == [("g-1", "ops@example.com")], "looked up by the Google subject and the email"


async def test_an_unanswerable_lookup_fails_closed(monkeypatch) -> None:
    def broken(sub, email):
        raise RuntimeError("firebase unreachable")

    monkeypatch.setattr(security, "firebase_app", object())
    monkeypatch.setattr(security, "_firebase_second_factor_enrolled", broken)
    with _google_token(), pytest.raises(HTTPException) as refused:
        await verify_identity_token(None, _bearer())
    assert refused.value.status_code == 401


async def test_without_firebase_there_is_nothing_to_check(monkeypatch) -> None:
    monkeypatch.setattr(security, "firebase_app", None)

    def must_not_run(sub, email):
        raise AssertionError("no Firebase project: no second factors exist")

    monkeypatch.setattr(security, "_firebase_second_factor_enrolled", must_not_run, raising=False)
    with _google_token():
        assert (await verify_identity_token(None, _bearer()))["uid"] == "g-1"


def test_the_lookup_reads_mfa_info_by_subject_then_email(monkeypatch) -> None:
    from firebase_admin import auth

    class Record:
        def __init__(self, mfa):
            self._data = {"mfaInfo": mfa} if mfa else {}

    def by_uid(uid):
        raise auth.UserNotFoundError("no such uid")

    monkeypatch.setattr(auth, "get_user", by_uid)
    monkeypatch.setattr(auth, "get_user_by_email", lambda email: Record([{"mfaEnrollmentId": "f1", "totpInfo": {}}]))
    assert security._firebase_second_factor_enrolled("g-1", "ops@example.com") is True
    monkeypatch.setattr(auth, "get_user_by_email", lambda email: Record(None))
    assert security._firebase_second_factor_enrolled("g-1", "ops@example.com") is False
