"""Role resolution: SUPERADMIN_EMAILS vs ADMIN_EMAILS, no silent upgrade."""

from __future__ import annotations

import os

from services.rbac import get_user_role, is_console_operator, stamp_resolved_role


def test_admin_emails_never_mint_superadmin(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")
    monkeypatch.delenv("SUPERADMIN_EMAILS", raising=False)
    assert get_user_role({"email": "ops@aura.ai"}) == "Admin"


def test_superadmin_emails_win(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")
    monkeypatch.setenv("SUPERADMIN_EMAILS", "ops@aura.ai")
    assert get_user_role({"email": "ops@aura.ai"}) == "SuperAdmin"


def test_persisted_superadmin_is_not_downgraded_by_admin_list(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")
    monkeypatch.delenv("SUPERADMIN_EMAILS", raising=False)
    assert get_user_role({"email": "ops@aura.ai", "db_role": "SuperAdmin"}) == "SuperAdmin"


def test_developer_can_open_the_console() -> None:
    assert is_console_operator("Developer")
    assert is_console_operator("Admin")
    assert is_console_operator("SuperAdmin")
    assert not is_console_operator("User")


def test_stamp_resolved_role_writes_claim(monkeypatch) -> None:
    monkeypatch.setenv("DEVELOPER_EMAILS", "dev@aura.ai")
    info = stamp_resolved_role({"email": "dev@aura.ai", "uid": "d1"})
    assert info["role"] == "Developer"
