"""SaaS P1-F1: sharing a chat with the team -- who may share, read and unshare."""

from __future__ import annotations

import pytest

from aura_core.adapters.sharing import InMemorySharedConversationStore
from aura_core.domain.errors import AuthorizationError, ValidationError
from aura_core.domain.sharing import MAX_SHARED_CHARS, MAX_SHARED_MESSAGES
from aura_core.domain.tenancy import WorkspaceRole
from aura_core.sharing import SharingService

MSGS = [{"role": "user", "text": "How do we deploy?"}, {"role": "assistant", "text": "With Railway."}]


@pytest.fixture
def sharing(container, clock):
    container.tenancy.clock = clock
    return SharingService(store=InMemorySharedConversationStore(), tenancy=container.tenancy, clock=clock,
                          audit=container.audit)


async def _team(container, *members):
    ws = await container.tenancy.create_team("own", "Acme")
    await container.plans.set_plan("sa", ws.id, "team", 10)
    for user, role in members:
        _i, token = await container.tenancy.invite("own", ws.id, f"{user}@example.com", role)
        await container.tenancy.accept(user, token)
    return ws.id


async def test_member_shares_and_every_member_reads(container, sharing) -> None:
    ws = await _team(container, ("amy", WorkspaceRole.MEMBER), ("bob", WorkspaceRole.MEMBER))
    shared = await sharing.share("amy", ws, "chat-1", "Deploys", MSGS)
    assert [c.id for c in await sharing.list("bob", ws)] == [shared.id]
    got = await sharing.get("own", ws, shared.id)
    assert got.shared_by == "amy" and got.messages[1].text == "With Railway."


async def test_resharing_replaces_not_duplicates(container, sharing) -> None:
    ws = await _team(container, ("amy", WorkspaceRole.MEMBER))
    await sharing.share("amy", ws, "chat-1", "v1", MSGS)
    await sharing.share("amy", ws, "chat-1", "v2", MSGS)
    assert [c.title for c in await sharing.list("amy", ws)] == ["v2"]


async def test_personal_workspace_cannot_share(container, sharing) -> None:
    ws = (await container.tenancy.ensure_personal("amy")).id
    with pytest.raises(ValidationError):
        await sharing.share("amy", ws, "chat-1", "x", MSGS)


async def test_unshare_by_sharer_owner_or_admin_only(container, sharing) -> None:
    ws = await _team(container, ("amy", WorkspaceRole.MEMBER), ("bob", WorkspaceRole.MEMBER), ("ada", WorkspaceRole.ADMIN))
    first = await sharing.share("amy", ws, "c1", "a", MSGS)
    with pytest.raises(AuthorizationError):
        await sharing.unshare("bob", ws, first.id)
    await sharing.unshare("amy", ws, first.id)
    second = await sharing.share("amy", ws, "c2", "b", MSGS)
    await sharing.unshare("ada", ws, second.id)
    third = await sharing.share("amy", ws, "c3", "c", MSGS)
    await sharing.unshare("own", ws, third.id)
    assert await sharing.list("amy", ws) == []


@pytest.mark.parametrize("messages", [
    [{"role": "system", "text": "x"}],
    [{"role": "user"}],
    [{"role": "user", "text": "x"}] * (MAX_SHARED_MESSAGES + 1),
    [{"role": "user", "text": "x" * (MAX_SHARED_CHARS + 1)}],
    [],
])
async def test_too_big_or_bad_role_is_rejected_and_stores_nothing(container, sharing, messages) -> None:
    ws = await _team(container, ("amy", WorkspaceRole.MEMBER))
    with pytest.raises(ValidationError):
        await sharing.share("amy", ws, "c1", "t", messages)
    assert await sharing.list("amy", ws) == []


async def test_removed_member_loses_access_but_share_stays(container, sharing) -> None:
    ws = await _team(container, ("amy", WorkspaceRole.MEMBER))
    shared = await sharing.share("amy", ws, "c1", "t", MSGS)
    await container.tenancy.remove("own", ws, "amy")
    with pytest.raises(AuthorizationError):
        await sharing.list("amy", ws)
    with pytest.raises(AuthorizationError):
        await sharing.unshare("amy", ws, shared.id)
    assert [c.id for c in await sharing.list("own", ws)] == [shared.id]


async def test_outsider_gets_the_same_403_as_for_a_missing_workspace(container, sharing) -> None:
    ws = await _team(container)
    with pytest.raises(AuthorizationError) as real:
        await sharing.list("eve", ws)
    with pytest.raises(AuthorizationError) as missing:
        await sharing.list("eve", "0" * 32)
    assert str(real.value) == str(missing.value)


async def test_sharing_is_audited_without_content(container, sharing) -> None:
    ws = await _team(container, ("amy", WorkspaceRole.MEMBER))
    await sharing.share("amy", ws, "c1", "Secret plan", MSGS)
    events = [e for e in await container.audit.query(limit=50) if e.action.startswith("workspace.share")]
    assert events and "Railway" not in repr([e.detail for e in events])
