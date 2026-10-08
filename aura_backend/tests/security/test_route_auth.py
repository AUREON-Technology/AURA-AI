"""Authentication bypass: every non-public route of the production gateway refuses a request that
carries no valid session.

Runs against ``aura_service.app`` -- the real production assembly, with the real authenticator
(``_build_authenticator`` -> ``services.jwt_auth.verify_access_token``) and the real platform
routers -- so a route that forgets its dependency, or a router mounted without one, is caught
here rather than by inspection.

Routes are enumerated from the app, not listed by hand. A route is either in :data:`PUBLIC`
(with the reason it needs no session), in :data:`IDENTITY_EXCHANGE` (it *creates* a session from
an external identity proof, tested separately below), or it must reject every credential in
:data:`BAD_CREDENTIALS` with 401/403. A new route therefore fails this file until it is either
protected or deliberately allowlisted.

WebSocket routes authenticate with a first frame rather than a header; they are enumerated the
same way and driven with the same bad credentials.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from starlette.websockets import WebSocketDisconnect

import services.jwt_auth as jwt_auth

pytest.importorskip("fastapi")

#: Reachable without a session, and why.
PUBLIC = {
    ("GET", "/health/live"): "liveness probe -- must work during an auth outage",
    ("GET", "/health/ready"): "readiness probe",
    # BLOCK-03. Public on purpose: a deploy's smoke test asserts "the SHA I just shipped is the
    # SHA now serving" before any credential exists, and it has to keep answering while a
    # dependency is down. ReleaseSettings.to_public_dict() is an allowlist of build provenance
    # with no configuration in it -- see test_release_identity.py.
    ("GET", "/health/version"): "release identity -- deploy verification, no credential yet",
    ("GET", "/metrics"): "Prometheus scrape; refused publicly at nginx (SEC-12)",
    ("GET", "/openapi.json"): "schema, no data",
    ("GET", "/docs"): "schema UI, no data",
    ("GET", "/docs/oauth2-redirect"): "schema UI, no data",
    ("GET", "/redoc"): "schema UI, no data",
    ("POST", "/api/v3/auth/nonce"): "attestation nonce, issued before a session can exist",
    ("POST", "/api/v3/auth/refresh"): "authenticated by the refresh token in the body",
    ("POST", "/api/v3/auth/revoke"): "authenticated by the refresh token in the body",
    ("POST", "/api/v3/auth/desktop/pair/start"): "RFC 8628 device flow: daemon has no identity yet",
    ("POST", "/api/v3/auth/desktop/pair/poll"): "RFC 8628 device flow: bound to an unguessable id",
    ("GET", "/api/v3/integrations/github/callback"): "browser redirect; authenticated by the "
        "single-use OAuth `state` (tests/unit/test_github_oauth_state.py)",
}

#: Take an *external* identity proof (Google/Firebase ID token) and mint a session.
IDENTITY_EXCHANGE = {
    ("POST", "/api/v3/auth/token"),
    ("POST", "/api/v3/auth/web/token"),
    ("POST", "/api/v3/auth/desktop/login"),
}


def _jwt(payload: dict, *, key: str | None = None, algorithm: str = "HS256") -> str:
    return jwt.encode(payload, key if key is not None else jwt_auth._secret(), algorithm=algorithm)


def _claims(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    claims = {"sub": "intruder", "email": "i@example.com", "role": "SuperAdmin",
              "iat": now, "exp": now + timedelta(minutes=10)}
    claims.update(overrides)
    return claims


def _revoked_device_token() -> str:
    issued = jwt_auth.issue_device_token({"uid": "device-owner"}, device_id="revoked-device")
    assert jwt_auth.revoke_device("revoked-device", requesting_uid="device-owner")
    return issued["access_token"]


def _bad_credentials() -> dict[str, dict[str, str]]:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    refresh = jwt_auth.issue_token_pair({"uid": "someone"})["refresh_token"]
    return {
        "missing": {},
        "empty_bearer": {"Authorization": "Bearer "},
        "not_bearer": {"Authorization": "Basic YWRtaW46YWRtaW4="},
        "garbage": {"Authorization": "Bearer not-a-jwt"},
        "expired": {"Authorization": "Bearer " + _jwt(_claims(iat=past - timedelta(minutes=15),
                                                               exp=past))},
        "wrong_signature": {"Authorization": "Bearer " + _jwt(_claims(), key="not-the-secret")},
        "alg_none": {"Authorization": "Bearer " + jwt.encode(_claims(), None, algorithm="none")},
        "refresh_token_as_access": {"Authorization": f"Bearer {refresh}"},
        "revoked_device": {"Authorization": f"Bearer {_revoked_device_token()}"},
    }


BAD_CREDENTIALS = list(_bad_credentials())


@pytest.fixture
def app(monkeypatch):
    import infrastructure.http_limits as http_limits
    from infrastructure.http_limits import RateLimitMiddleware
    from aura_service import app as production_app

    # The IP limiter would 429 a suite that sends hundreds of requests from one client; it is not
    # what is under test. Pinned to its in-process path and cleared per request below.
    monkeypatch.setattr(http_limits, "_rate_limit_redis", None)
    RateLimitMiddleware.request_history.clear()
    return production_app


def _routes() -> tuple[set[tuple[str, str]], set[str]]:
    from fastapi import routing
    from fastapi.routing import APIWebSocketRoute

    from aura_service import app

    http, sockets = set(), set()
    for ctx in routing.iter_route_contexts(app.routes):
        original = ctx.original_route
        if isinstance(original, APIWebSocketRoute):
            sockets.add(ctx.path or original.path)
            continue
        for method in getattr(original, "methods", None) or ():
            if method != "HEAD":
                http.add((method, ctx.path or original.path))
    return http, sockets


def _protected() -> list[tuple[str, str]]:
    http, _ = _routes()
    return sorted(r for r in http if r not in PUBLIC and r not in IDENTITY_EXCHANGE)


def _concrete(path: str) -> str:
    import re

    return re.sub(r"\{[^}]+\}", "probe-id", path)


class TestInventory:
    def test_enumeration_sees_the_real_surface(self) -> None:
        http, sockets = _routes()
        assert len(http) > 60, "route walk found too little -- it is broken, not the surface small"
        assert {"/api/v3/voice/live", "/api/v3/host/agent"} <= sockets

    def test_allowlists_name_only_routes_that_exist(self) -> None:
        http, _ = _routes()
        stale = sorted(r for r in (set(PUBLIC) | IDENTITY_EXCHANGE) if r not in http)
        assert not stale, f"allowlist names routes that no longer exist: {stale}"

    def test_no_public_route_touches_user_data(self) -> None:
        """The public allowlist is a list of probes, docs and session bootstrap -- nothing under
        a data prefix may join it."""
        data_prefixes = ("/api/v3/chat", "/api/v3/knowledge", "/api/v3/memory", "/api/v3/sync",
                         "/api/v3/engineering", "/api/v3/admin", "/api/v3/insights",
                         "/api/v3/agents")
        assert not [p for _, p in PUBLIC if p.startswith(data_prefixes)]


@pytest.mark.parametrize("method,path", _protected(), ids=lambda v: str(v))
def test_protected_route_rejects_every_bad_credential(app, method: str, path: str) -> None:
    from fastapi.testclient import TestClient

    from infrastructure.http_limits import RateLimitMiddleware

    client = TestClient(app)
    accepted = {}
    for name, headers in _bad_credentials().items():
        RateLimitMiddleware.request_history.clear()
        response = client.request(method, _concrete(path), headers=headers)
        if response.status_code not in (401, 403):
            accepted[name] = response.status_code
    assert not accepted, f"{method} {path} did not refuse: {accepted}"


class TestAValidSessionIsWhatGetsThrough:
    """Guards the test above against passing vacuously: the same token shape, correctly signed
    and unexpired, is accepted -- so the refusals are about validity, not about the route."""

    def test_a_valid_access_token_reaches_a_user_route(self, app) -> None:
        from fastapi.testclient import TestClient

        token = jwt_auth.create_access_token({"uid": "valid-user", "email": "v@example.com"})
        response = TestClient(app).get(
            "/api/v3/tools", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

    def test_a_forged_admin_role_claim_does_not_grant_admin(self, app, monkeypatch) -> None:
        """Role comes from the verified token *and* the server's own role sources; a validly
        signed token for a plain user stays a plain user on admin routes."""
        from fastapi.testclient import TestClient

        for var in ("ADMIN_EMAILS", "SUPERADMIN_EMAILS", "DEVELOPER_EMAILS"):
            monkeypatch.delenv(var, raising=False)
        token = jwt_auth.create_access_token({"uid": "plain", "email": "p@example.com"})
        response = TestClient(app).get(
            "/api/v3/admin/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403


class TestIdentityExchangeRoutes:
    """These mint a session, so they cannot require one. Outside debug they must still refuse a
    request carrying no identity proof -- and do so before any identity-provider call."""

    @pytest.fixture
    def production(self, monkeypatch):
        import config
        import services.security as security

        monkeypatch.delenv("TESTING", raising=False)
        monkeypatch.setenv("AURA_ENVIRONMENT", "production")
        monkeypatch.setattr(security, "settings", config.Settings(debug_mode=False))

    @pytest.mark.parametrize("method,path", sorted(IDENTITY_EXCHANGE))
    def test_refused_without_an_identity_proof(self, app, production, method, path) -> None:
        from fastapi.testclient import TestClient

        response = TestClient(app).request(method, path)
        assert response.status_code in (401, 403)
        assert "access_token" not in response.text


# --------------------------------------------------------------------------------------
# WebSockets
# --------------------------------------------------------------------------------------


def _ws_app(container):
    """The v3 app with the *production* authenticator, and live voice switched on so the voice
    route reaches its auth step instead of closing 4503 first."""
    from aura_core.adapters.in_memory_speech import InMemoryStt, InMemoryTts
    from aura_core.api import build_app
    from aura_core.config import load_settings
    from aura_core.voice import LiveVoiceConfig
    from aura_service import _build_authenticator

    container.stt_engine = InMemoryStt(default="hello")
    container.tts_engine = InMemoryTts()
    container.live_voice = {
        "enabled": True, "config": LiveVoiceConfig(), "stt": container.stt_engine,
        "tts": container.tts_engine, "nlp": container.nlp, "max_sessions": 4,
        "kernel": container.kernel, "cache": container.cache_backend,
        "safety_guards_enabled": False,
    }
    settings = load_settings(refresh=True, validate=False)
    return build_app(container, authenticator=_build_authenticator(settings))


def _ws_frame(headers: dict[str, str]) -> dict:
    token = headers.get("Authorization", "")
    return {"type": "auth", "token": token[7:] if token.startswith("Bearer ") else token}


@pytest.mark.parametrize("path", ["/api/v3/voice/live", "/api/v3/host/agent"])
async def test_websocket_rejects_every_bad_credential(container, path: str) -> None:
    from fastapi.testclient import TestClient

    await container.startup()
    accepted = {}
    with TestClient(_ws_app(container)) as client:
        for name, headers in _bad_credentials().items():
            try:
                with client.websocket_connect(path) as ws:
                    ws.send_json(_ws_frame(headers))
                    ws.receive_json()
                accepted[name] = "session stayed open"
            except WebSocketDisconnect as exc:
                if exc.code != 4401:
                    accepted[name] = f"closed {exc.code}"
    assert not accepted, f"{path} did not refuse: {accepted}"


async def test_every_websocket_route_is_covered() -> None:
    _, sockets = _routes()
    assert sockets == {"/api/v3/voice/live", "/api/v3/host/agent"}, (
        f"new websocket route(s) {sockets} -- add them to test_websocket_rejects_every_bad_credential"
    )
