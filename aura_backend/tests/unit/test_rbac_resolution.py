"""Role resolution: SUPERADMIN_EMAILS vs ADMIN_EMAILS, no silent upgrade."""

from __future__ import annotations

import os

from services.rbac import get_user_role, is_console_operator, stamp_resolved_role

#: A verified Google sign-in, as services.security.verify_identity_token reports it. Email-list
#: roles need both keys (SEC-08); tests/security/test_email_role_grants_require_verified_google.py
#: covers what happens without them.
_GOOGLE = {"email_verified": True, "sign_in_provider": "google.com"}


def test_admin_emails_never_mint_superadmin(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")
    monkeypatch.delenv("SUPERADMIN_EMAILS", raising=False)
    assert get_user_role({"email": "ops@aura.ai", **_GOOGLE}) == "Admin"


def test_superadmin_emails_win(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")
    monkeypatch.setenv("SUPERADMIN_EMAILS", "ops@aura.ai")
    assert get_user_role({"email": "ops@aura.ai", **_GOOGLE}) == "SuperAdmin"


def test_persisted_superadmin_is_not_downgraded_by_admin_list(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")
    monkeypatch.delenv("SUPERADMIN_EMAILS", raising=False)
    assert get_user_role({"email": "ops@aura.ai", "db_role": "SuperAdmin", **_GOOGLE}) == "SuperAdmin"


def test_developer_can_open_the_console() -> None:
    assert is_console_operator("Developer")
    assert is_console_operator("Admin")
    assert is_console_operator("SuperAdmin")
    assert not is_console_operator("User")


def test_stamp_resolved_role_writes_claim(monkeypatch) -> None:
    monkeypatch.setenv("DEVELOPER_EMAILS", "dev@aura.ai")
    info = stamp_resolved_role({"email": "dev@aura.ai", "uid": "d1", **_GOOGLE})
    assert info["role"] == "Developer"


def test_an_access_page_assignment_raises_the_role(monkeypatch) -> None:
    for name in ("SUPERADMIN_EMAILS", "ADMIN_EMAILS", "DEVELOPER_EMAILS"):
        monkeypatch.delenv(name, raising=False)
    assert get_user_role({"email": "dev@aura.ai"}, assigned_role="Developer") == "Developer"


def test_an_assignment_cannot_lower_what_the_env_grants(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")
    assert get_user_role({"email": "ops@aura.ai", **_GOOGLE}, assigned_role="Developer") == "Admin"


def test_granted_role_names_its_source(monkeypatch) -> None:
    from services.rbac import granted_role

    monkeypatch.delenv("SUPERADMIN_EMAILS", raising=False)
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")
    monkeypatch.setenv("DEVELOPER_EMAILS", "ops@aura.ai,dev@aura.ai")
    assert granted_role("ops@aura.ai", "u1") == ("Admin", "ADMIN_EMAILS")
    assert granted_role("dev@aura.ai", "u2") == ("Developer", "DEVELOPER_EMAILS")
    assert granted_role("nobody@aura.ai", "u3") == ("User", None)
