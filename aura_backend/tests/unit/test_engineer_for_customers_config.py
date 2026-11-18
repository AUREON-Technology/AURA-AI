"""SaaS P1-E: the customer Engineer switch, what it refuses to run beside, and the desktop role cap."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aura_core.config.settings import AuraSettings
from aura_core.domain.errors import ConfigurationError


def _on(**sections):
    settings = AuraSettings.from_env()
    settings = replace(settings, security=replace(settings.security, engineer_for_customers=True))
    for name, changes in sections.items():
        settings = replace(settings, **{name: replace(getattr(settings, name), **changes)})
    return settings


def _refused(settings, word: str) -> None:
    with pytest.raises(ConfigurationError) as exc:
        settings.validate()
    assert any(word in problem for problem in exc.value.details["problems"]), exc.value.details


def test_switch_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("AURA_ENGINEER_FOR_CUSTOMERS", raising=False)
    assert AuraSettings.from_env().security.engineer_for_customers is False
    monkeypatch.setenv("AURA_ENGINEER_FOR_CUSTOMERS", "true")
    assert AuraSettings.from_env().security.engineer_for_customers is True


def test_switch_alone_is_valid() -> None:
    _on().validate()


def test_switch_refuses_server_sandbox() -> None:
    _refused(_on(sandbox={"enabled": True}), "SANDBOX")


def test_switch_refuses_shared_github_token() -> None:
    _refused(_on(github={"enabled": True, "oauth_enabled": False}), "GITHUB")
    _on(github={"enabled": False, "oauth_enabled": True}).validate()


def test_switch_refuses_mcp() -> None:
    _refused(_on(mcp={"enabled": True}), "MCP")


def test_switch_refuses_relaxed_confirmation() -> None:
    _refused(_on(tools={"require_confirmation_for_side_effects": False}), "CONFIRMATION")


def test_consumer_desktop_is_capped_at_user(monkeypatch) -> None:
    from services.rbac import CONSUMER_CHANNELS, CONSUMER_DESKTOP_CHANNEL, get_user_role

    monkeypatch.setenv("SUPERADMIN_EMAILS", "boss@example.com")
    identity = {"uid": "u", "email": "boss@example.com", "email_verified": True, "sign_in_provider": "google.com"}
    assert get_user_role(dict(identity)) == "SuperAdmin"
    assert get_user_role({**identity, "channel": CONSUMER_DESKTOP_CHANNEL}) == "User"
    assert CONSUMER_DESKTOP_CHANNEL == "consumer_desktop" and "consumer_web" in CONSUMER_CHANNELS


def test_switch_refuses_user_in_engineer_roles() -> None:
    """Plan E review #6: a role grant to every User would skip every entitlement gate."""
    _refused(_on(security={"engineer_for_customers": True, "engineer_agent_roles": ("User",)}), "ENGINEER_AGENT_ROLES")
