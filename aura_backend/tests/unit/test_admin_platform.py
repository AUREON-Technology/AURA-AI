"""SuperAdmin-only Access guards: last SuperAdmin cannot be demoted or forgotten."""

from api.admin_platform import last_superadmin_block


def test_admin_may_be_changed() -> None:
    assert (
        last_superadmin_block(
            target_role="Admin",
            remaining=1,
            new_role="Developer",
            action="demote",
        )
        is None
    )


def test_last_superadmin_cannot_be_demoted() -> None:
    assert (
        last_superadmin_block(
            target_role="SuperAdmin",
            remaining=1,
            new_role="Admin",
            action="demote",
        )
        == "cannot demote the last SuperAdmin"
    )


def test_superadmin_cannot_change_own_role_when_peers_exist() -> None:
    assert (
        last_superadmin_block(
            target_role="SuperAdmin",
            remaining=2,
            actor_email="root@aura.ai",
            target_email="root@aura.ai",
            new_role="Admin",
            action="demote",
        )
        == "cannot change your own SuperAdmin role"
    )


def test_peer_superadmin_can_be_demoted_when_another_remains() -> None:
    assert (
        last_superadmin_block(
            target_role="SuperAdmin",
            remaining=2,
            actor_email="root@aura.ai",
            target_email="ops@aura.ai",
            new_role="Admin",
            action="demote",
        )
        is None
    )


def test_last_superadmin_cannot_be_forgotten() -> None:
    assert (
        last_superadmin_block(target_role="SuperAdmin", remaining=1, action="forget")
        == "cannot forget the last SuperAdmin"
    )


def test_operator_directory_failure_is_an_error_not_an_empty_list(monkeypatch) -> None:
    """Audit INT-034: a database failure used to answer 200 with ``[]``, which the Access page
    shows as "nobody has access" -- indistinguishable from a real, empty directory."""
    import asyncio

    import pytest
    from fastapi import HTTPException

    import infrastructure.db.base as base
    from api.admin_platform import list_operators

    def broken_factory():
        raise RuntimeError("database is down")

    monkeypatch.setattr(base, "is_configured", lambda: True)
    monkeypatch.setattr(base, "get_session_factory", broken_factory)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(list_operators(user_info={"role": "Admin"}))
    assert caught.value.status_code == 503
    assert "database is down" not in str(caught.value.detail)
