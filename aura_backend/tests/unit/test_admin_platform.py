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
