"""``POST /api/v3/auth/desktop/exchange`` -- the server half of the desktop app's Google sign-in.

Found live (2026-11-17): the desktop app's PKCE-only code exchange was refused by Google with
``invalid_request: client_secret is missing`` for a correctly registered "Desktop app" client, so
sign-in failed after the browser step. The exchange moved here, where the secret lives in the
environment. These tests pin what that move must keep true:

* the secret is added server-side, only when configured, and never echoed or logged;
* only the ID token goes back -- not Google's access or refresh token;
* the route is not a relay: the redirect must be the loopback form, and Google's own refusals
  map to messages that say whose problem it is.

Only the one function that makes the HTTP call (``services.google_oauth._post_form``) is
replaced; request building, validation and error mapping all run for real.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

import services.google_oauth as google_oauth
from api.v2.auth import router
from config import settings

DESKTOP_CLIENT_ID = "desktop-client.apps.googleusercontent.com"
SERVER_SECRET = "unit-test-server-secret-value"
CODE = "4/0AUn6Hd-unit-test-code"
VERIFIER = "v" * 64
REDIRECT = "http://127.0.0.1:51868/"
ID_TOKEN = "header.payload.signature"


class FakeGoogle:
    """Records what would have been POSTed to Google's token endpoint."""

    def __init__(self, status: int = 200, payload: dict | None = None, raises: Exception | None = None):
        self.status = status
        self.payload = payload if payload is not None else {"id_token": ID_TOKEN}
        self.raises = raises
        self.forms: list[dict[str, str]] = []

    async def __call__(self, data):
        self.forms.append(dict(data))
        if self.raises is not None:
            raise self.raises
        return self.status, self.payload


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "google_desktop_client_id", DESKTOP_CLIENT_ID)
    monkeypatch.setattr(settings, "google_desktop_client_secret", SecretStr(SERVER_SECRET))


def _install(monkeypatch, google: FakeGoogle) -> FakeGoogle:
    monkeypatch.setattr(google_oauth, "_post_form", google)
    return google


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _body(**overrides):
    body = {"code": CODE, "code_verifier": VERIFIER, "redirect_uri": REDIRECT}
    body.update(overrides)
    return body


# --- what is sent to Google ----------------------------------------------------------------


async def test_the_server_adds_its_secret_and_the_apps_proof(configured, monkeypatch) -> None:
    google = _install(monkeypatch, FakeGoogle())

    id_token = await google_oauth.exchange_desktop_code(
        code=CODE, code_verifier=VERIFIER, redirect_uri=REDIRECT
    )

    assert id_token == ID_TOKEN
    assert google.forms == [
        {
            "client_id": DESKTOP_CLIENT_ID,
            "code": CODE,
            "code_verifier": VERIFIER,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT,
            "client_secret": SERVER_SECRET,
        }
    ]


async def test_with_no_secret_configured_the_field_is_omitted_not_blank(monkeypatch) -> None:
    """Google reads ``client_secret=`` (present, empty) as a wrong secret, so absent and blank
    are different requests -- a deployment whose client really is public must send neither."""
    monkeypatch.setattr(settings, "google_desktop_client_id", DESKTOP_CLIENT_ID)
    monkeypatch.setattr(settings, "google_desktop_client_secret", SecretStr(""))
    google = _install(monkeypatch, FakeGoogle())

    await google_oauth.exchange_desktop_code(
        code=CODE, code_verifier=VERIFIER, redirect_uri=REDIRECT
    )

    assert "client_secret" not in google.forms[0]


# --- the route -----------------------------------------------------------------------------


def test_the_route_returns_only_the_id_token(configured, monkeypatch) -> None:
    """Google's response also carries an access token and sometimes a refresh token. Nothing in
    AURA acts on the user's Google account, so neither may leave the server."""
    _install(
        monkeypatch,
        FakeGoogle(
            payload={
                "id_token": ID_TOKEN,
                "access_token": "google-access-token",
                "refresh_token": "google-refresh-token",
            }
        ),
    )

    response = _client().post("/api/v3/auth/desktop/exchange", json=_body())

    assert response.status_code == 200
    assert response.json() == {"id_token": ID_TOKEN}
    assert "google-access-token" not in response.text
    assert "google-refresh-token" not in response.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": ""},
        {"code": "x" * 2049},
        {"code_verifier": "short"},
        {"code_verifier": "v" * 129},
        {"code_verifier": "has spaces " + "v" * 60},
        {"redirect_uri": ""},
    ],
    ids=["empty-code", "huge-code", "short-verifier", "long-verifier", "bad-verifier-chars", "no-redirect"],
)
def test_malformed_requests_never_reach_google(configured, monkeypatch, overrides) -> None:
    google = _install(monkeypatch, FakeGoogle())

    response = _client().post("/api/v3/auth/desktop/exchange", json=_body(**overrides))

    assert response.status_code == 422
    assert google.forms == []


# --- this route is not a relay -------------------------------------------------------------


@pytest.mark.parametrize(
    "redirect",
    [
        "https://evil.example/callback",
        "http://127.0.0.1.evil.example:5000/",
        "http://localhost:5000/",
        "http://127.0.0.1/",
        "http://127.0.0.1:99999/",
        "http://127.0.0.1:5000/steal",
        "http://127.0.0.1:5000/?x=1",
        "http://user@127.0.0.1:5000/",
    ],
)
def test_only_the_loopback_redirect_is_accepted(configured, monkeypatch, redirect) -> None:
    google = _install(monkeypatch, FakeGoogle())

    response = _client().post("/api/v3/auth/desktop/exchange", json=_body(redirect_uri=redirect))

    assert response.status_code == 400
    assert google.forms == [], "an unsafe redirect must be refused before Google is called"


# --- Google's refusals, and whose problem each one is ---------------------------------------


def test_a_missing_server_secret_says_so_instead_of_blaming_the_user(monkeypatch) -> None:
    """The exact failure this fixes, reproduced from the server side: with no secret configured,
    Google answers ``client_secret is missing``. That is a deployment gap -- an operator
    reading the message must learn which variable to set."""
    monkeypatch.setattr(settings, "google_desktop_client_id", DESKTOP_CLIENT_ID)
    monkeypatch.setattr(settings, "google_desktop_client_secret", SecretStr(""))
    _install(
        monkeypatch,
        FakeGoogle(
            status=400,
            payload={"error": "invalid_request", "error_description": "client_secret is missing."},
        ),
    )

    response = _client().post("/api/v3/auth/desktop/exchange", json=_body())

    assert response.status_code == 503
    assert "GOOGLE_DESKTOP_CLIENT_SECRET" in response.json()["detail"]


def test_a_rejected_server_secret_is_a_server_fault_and_is_not_echoed(configured, monkeypatch) -> None:
    _install(
        monkeypatch,
        FakeGoogle(
            status=401,
            payload={
                "error": "invalid_client",
                "error_description": f"The provided client secret is invalid: {SERVER_SECRET}",
            },
        ),
    )

    response = _client().post("/api/v3/auth/desktop/exchange", json=_body())

    assert response.status_code == 503
    assert SERVER_SECRET not in response.text


def test_a_spent_or_wrong_code_is_the_users_to_retry(configured, monkeypatch) -> None:
    _install(
        monkeypatch,
        FakeGoogle(status=400, payload={"error": "invalid_grant", "error_description": "Bad Request"}),
    )

    response = _client().post("/api/v3/auth/desktop/exchange", json=_body())

    assert response.status_code == 400
    assert "sign in again" in response.json()["detail"].lower()


def test_google_being_unreachable_is_a_502_without_the_exception_text(configured, monkeypatch) -> None:
    _install(monkeypatch, FakeGoogle(raises=ConnectionError(f"boom {CODE} {SERVER_SECRET}")))

    response = _client().post("/api/v3/auth/desktop/exchange", json=_body())

    assert response.status_code == 502
    assert CODE not in response.text
    assert SERVER_SECRET not in response.text


def test_a_200_without_an_id_token_is_not_treated_as_success(configured, monkeypatch) -> None:
    _install(monkeypatch, FakeGoogle(payload={"access_token": "only-an-access-token"}))

    response = _client().post("/api/v3/auth/desktop/exchange", json=_body())

    assert response.status_code == 502
    assert "only-an-access-token" not in response.text


def test_an_unconfigured_client_id_is_refused_without_calling_google(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_desktop_client_id", "")
    google = _install(monkeypatch, FakeGoogle())

    response = _client().post("/api/v3/auth/desktop/exchange", json=_body())

    assert response.status_code == 503
    assert google.forms == []


# --- nothing secret reaches a log ------------------------------------------------------------


@pytest.mark.parametrize(
    "google",
    [
        FakeGoogle(status=401, payload={"error": "invalid_client", "error_description": SERVER_SECRET}),
        FakeGoogle(status=400, payload={"error": "invalid_grant", "error_description": CODE}),
        FakeGoogle(raises=RuntimeError(f"{CODE} {VERIFIER} {SERVER_SECRET}")),
    ],
    ids=["invalid-client", "invalid-grant", "transport-error"],
)
def test_failures_log_no_credential(configured, monkeypatch, caplog, google) -> None:
    _install(monkeypatch, google)

    with caplog.at_level(logging.DEBUG, logger="aura.auth.google_oauth"):
        _client().post("/api/v3/auth/desktop/exchange", json=_body())

    logged = caplog.text
    for credential in (SERVER_SECRET, CODE, VERIFIER):
        assert credential not in logged


def test_the_secret_is_masked_in_the_settings_repr() -> None:
    """A SecretStr, so an accidental ``print(settings)`` or log of the object stays clean."""
    masked = repr(SecretStr(SERVER_SECRET))
    assert SERVER_SECRET not in masked
    assert isinstance(settings.google_desktop_client_secret, SecretStr)
