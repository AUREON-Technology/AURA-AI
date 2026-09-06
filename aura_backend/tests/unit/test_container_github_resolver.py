"""``Container._build_github_client_resolver`` -- the seam that lets the engineer agent's GitHub
tools (``tools/coding.py::build_github_tools``) serve either the single deployment-wide admin PAT
or each user's own OAuth-connected identity, without the tools themselves knowing which.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aura_core.adapters.github_oauth as github_oauth
from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.github_identity import GitHubIdentity
from aura_core.domain.identifiers import UserId

_ENV_KEYS = (
    "AURA_GITHUB_ENABLED",
    "AURA_GITHUB_TOKEN",
    "AURA_GITHUB_OAUTH_ENABLED",
    "AURA_GITHUB_OAUTH_CLIENT_ID",
    "AURA_GITHUB_OAUTH_CLIENT_SECRET",
    "AURA_GITHUB_OAUTH_CALLBACK_URL",
)

_OAUTH_ENV = dict(
    AURA_GITHUB_OAUTH_ENABLED="true",
    AURA_GITHUB_OAUTH_CLIENT_ID="client-id",
    AURA_GITHUB_OAUTH_CLIENT_SECRET="client-secret",
    AURA_GITHUB_OAUTH_CALLBACK_URL="https://example.com/api/v3/integrations/github/callback",
)


def _build_container(monkeypatch, **env) -> Container:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Container(load_settings(refresh=True, validate=False))


def _identity(**overrides) -> GitHubIdentity:
    now = datetime.now(timezone.utc)
    defaults = dict(
        github_user_id=1,
        github_login="alice-gh",
        access_token="ghu_valid",
        refresh_token=None,
        token_expires_at=None,
        refresh_token_expires_at=None,
        installation_id=None,
        scopes="",
        connected_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return GitHubIdentity(**defaults)


async def test_resolver_is_none_when_nothing_is_configured(monkeypatch) -> None:
    container = _build_container(monkeypatch)
    assert container.github_client_resolver is None


async def test_admin_pat_path_serves_every_user_the_same_client(monkeypatch) -> None:
    container = _build_container(
        monkeypatch, AURA_GITHUB_ENABLED="true", AURA_GITHUB_TOKEN="ghp_admin"
    )
    resolver = container.github_client_resolver
    assert resolver is not None

    client_a = await resolver(UserId("alice"))
    client_b = await resolver(UserId("bob"))
    assert client_a is client_b
    assert client_a is container.github_client


async def test_oauth_path_returns_none_for_an_unconnected_user(monkeypatch) -> None:
    container = _build_container(monkeypatch, **_OAUTH_ENV)
    resolver = container.github_client_resolver
    assert resolver is not None
    assert await resolver(UserId("nobody")) is None


async def test_oauth_path_returns_a_client_for_a_connected_user(monkeypatch) -> None:
    container = _build_container(monkeypatch, **_OAUTH_ENV)
    now = datetime.now(timezone.utc)
    await container.github_identities.upsert(
        UserId("alice"), _identity(token_expires_at=now + timedelta(hours=1))
    )

    assert await container.github_client_resolver(UserId("alice")) is not None


async def test_oauth_path_refreshes_an_expired_token(monkeypatch) -> None:
    async def fake_refresh(*, client_id, client_secret, refresh_token):
        assert client_id == "client-id"
        assert refresh_token == "ghr_valid"
        return github_oauth.GitHubOAuthToken(
            access_token="ghu_refreshed", refresh_token="ghr_valid",
            expires_in=3600, refresh_token_expires_in=None, scope="repo",
        )

    # Patched before the Container is built: `_build_github_client_resolver` does
    # `from .adapters.github_oauth import refresh_access_token as oauth_refresh` once, at
    # construction time, and the closure keeps that bound name -- patching the module attribute
    # afterwards would not reach it.
    monkeypatch.setattr(github_oauth, "refresh_access_token", fake_refresh)

    container = _build_container(monkeypatch, **_OAUTH_ENV)
    now = datetime.now(timezone.utc)
    await container.github_identities.upsert(
        UserId("alice"),
        _identity(
            access_token="ghu_expired",
            refresh_token="ghr_valid",
            token_expires_at=now - timedelta(hours=1),
            refresh_token_expires_at=now + timedelta(days=180),
        ),
    )

    client = await container.github_client_resolver(UserId("alice"))
    assert client is not None

    stored = await container.github_identities.get(UserId("alice"))
    assert stored.access_token == "ghu_refreshed"
    assert stored.token_expires_at is not None and stored.token_expires_at > now


async def test_oauth_path_returns_none_when_expired_with_no_refresh_token(monkeypatch) -> None:
    container = _build_container(monkeypatch, **_OAUTH_ENV)
    now = datetime.now(timezone.utc)
    await container.github_identities.upsert(
        UserId("alice"),
        _identity(access_token="ghu_expired", token_expires_at=now - timedelta(hours=1)),
    )

    assert await container.github_client_resolver(UserId("alice")) is None
