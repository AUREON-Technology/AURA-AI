"""SaaS P1-A: the workspace domain -- scopes, invite tokens, and who may do what in a team."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_core.domain.errors import ValidationError
from aura_core.domain.identifiers import is_reserved_principal
from aura_core.domain.tenancy import (
    INVITE_TTL,
    Invite,
    WorkspaceRole,
    can,
    clean_workspace_name,
    invite_digest,
    new_invite_token,
    new_workspace_id,
    parse_invite_token,
    workspace_scope,
)

OWNER, ADMIN, MEMBER = WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER
T0 = datetime(2026, 11, 16, tzinfo=timezone.utc)


def test_scope_is_reserved() -> None:
    wid = new_workspace_id()
    assert len(wid) == 32 and all(c in "0123456789abcdef" for c in wid)
    scope = workspace_scope(wid)
    assert scope == f"workspace:{wid}"
    # No person's subject can ever equal a team's scope.
    assert is_reserved_principal(scope)


def test_invite_token_roundtrip() -> None:
    invite_id, token = new_invite_token()
    assert token.startswith(f"auraiv_{invite_id}_")
    assert parse_invite_token(token) == invite_id
    digest = invite_digest(token)
    assert digest != token and len(digest) == 64
    for bad in ("", "auraiv_", "aurask_abc_def", f"auraiv_{invite_id}", "auraiv_x y_z"):
        with pytest.raises(ValidationError):
            parse_invite_token(bad)


@pytest.mark.parametrize(
    "role, action, target, allowed",
    [
        (OWNER, "view", None, True),
        (OWNER, "invite", None, True),
        (OWNER, "remove_member", ADMIN, True),
        (OWNER, "change_role", ADMIN, True),
        (OWNER, "transfer", None, True),
        (OWNER, "delete", None, True),
        (OWNER, "manage_knowledge", None, True),
        (ADMIN, "view", None, True),
        (ADMIN, "invite", None, True),
        (ADMIN, "manage_knowledge", None, True),
        (ADMIN, "remove_member", MEMBER, True),
        (ADMIN, "change_role", MEMBER, True),
        (ADMIN, "remove_member", ADMIN, False),
        (ADMIN, "remove_member", OWNER, False),
        (ADMIN, "change_role", ADMIN, False),
        (ADMIN, "transfer", None, False),
        (ADMIN, "delete", None, False),
        (MEMBER, "view", None, True),
        (MEMBER, "invite", None, False),
        (MEMBER, "manage_knowledge", None, False),
        (MEMBER, "remove_member", MEMBER, False),
        (MEMBER, "change_role", MEMBER, False),
        (MEMBER, "transfer", None, False),
        (MEMBER, "delete", None, False),
        (OWNER, "launch_rockets", None, False),
    ],
)
def test_role_matrix(role, action, target, allowed) -> None:
    assert can(role, action, target_role=target) is allowed


def _invite(**overrides) -> Invite:
    fields = dict(
        invite_id="i1", workspace_id="w" * 32, email="a@example.com", role=MEMBER, digest="d",
        created_by="u1", created_at=T0, expires_at=T0 + INVITE_TTL, accepted_at=None, accepted_by=None,
    )
    fields.update(overrides)
    return Invite(**fields)


def test_invite_open() -> None:
    assert INVITE_TTL == timedelta(days=7)
    assert _invite().is_open(T0 + timedelta(days=1))
    assert not _invite().is_open(T0 + INVITE_TTL)
    assert not _invite(accepted_at=T0, accepted_by="u2").is_open(T0 + timedelta(hours=1))


def test_workspace_name_rules() -> None:
    assert clean_workspace_name("  Acme  Team ") == "Acme  Team"
    assert clean_workspace_name("ｆｕｌｌ") == "full"  # NFKC
    for bad in ("", "   ", "x" * 81, "bad\x00name", "‮evil"):
        with pytest.raises(ValidationError):
            clean_workspace_name(bad)
    assert clean_workspace_name("x" * 80) == "x" * 80
