"""``aura_service.py::_build_authenticator`` -- specifically the "engineer_agent" capability
grant (``aura_core/config/settings.py``'s ``SecuritySettings.engineer_agent_roles``). Before this,
nothing in the codebase ever granted that capability to any role, which meant every tool in
``tools/coding.py`` (and the ``engineer`` persona itself) was invisible to every user regardless
of how GitHub/sandbox were configured.

Mocks only the identity-verification boundary (``services.jwt_auth``/``services.rbac``) -- not
AURA's own capability-computation logic, which is exactly what's under test here.
"""

from __future__ import annotations

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_core.config.settings import load_settings
from aura_service import _build_authenticator


class _FakeHeaders:
    def __init__(self, value: str) -> None:
        self._value = value

    def get(self, key: str, default: str = "") -> str:
        return self._value if key == "authorization" else default


class _FakeRequest:
    def __init__(self, token: str = "faketoken") -> None:
        self.headers = _FakeHeaders(f"Bearer {token}")


def _settings_with_engineer_agent_roles(monkeypatch, *roles: str):
    monkeypatch.setenv("AURA_ENGINEER_AGENT_ROLES", ",".join(roles))
    return load_settings(refresh=True, validate=False)


@pytest.fixture(autouse=True)
def _patch_verify_access_token(monkeypatch):
    async def fake_verify_access_token(request, credentials):
        return {"uid": "user-1", "email": "user@example.com"}

    monkeypatch.setattr(jwt_auth, "verify_access_token", fake_verify_access_token)


def _patch_role(monkeypatch, role: str) -> None:
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: role)


async def test_role_in_engineer_agent_roles_grants_the_capability(monkeypatch) -> None:
    settings = _settings_with_engineer_agent_roles(monkeypatch, "Developer")
    _patch_role(monkeypatch, "Developer")
    authenticate = _build_authenticator(settings)

    principal = await authenticate(_FakeRequest())

    assert "engineer_agent" in principal.capabilities


async def test_role_not_listed_does_not_get_the_capability(monkeypatch) -> None:
    settings = _settings_with_engineer_agent_roles(monkeypatch, "Developer")
    _patch_role(monkeypatch, "User")
    authenticate = _build_authenticator(settings)

    principal = await authenticate(_FakeRequest())

    assert "engineer_agent" not in principal.capabilities


async def test_default_unset_grants_the_capability_to_nobody(monkeypatch) -> None:
    settings = _settings_with_engineer_agent_roles(monkeypatch)
    _patch_role(monkeypatch, "SuperAdmin")
    authenticate = _build_authenticator(settings)

    principal = await authenticate(_FakeRequest())

    assert "engineer_agent" not in principal.capabilities
    # SuperAdmin still gets its own unrelated grants -- confirms this is additive, not a
    # regression of the existing admin.act/admin.super path.
    assert "admin.super" in principal.capabilities
    assert "admin.act" in principal.capabilities
