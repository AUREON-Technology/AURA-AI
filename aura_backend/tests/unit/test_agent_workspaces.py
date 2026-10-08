"""`AgentWorkspace` and the `WorkspaceStore` port's contract.

Run against `InMemoryWorkspaceStore`, which is a real implementation rather than a mock -- it
enforces the same one-workspace-per-(user, repo) rule the Postgres unique index does, so a
violation fails here too instead of only in production.

Expiry is driven by passing explicit datetimes rather than by sleeping: the TTL is three days,
and a test that waited for it would never finish.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.memory_stores import InMemoryWorkspaceStore
from aura_core.domain.engineering import AgentWorkspace, WorkspaceStatus
from aura_core.domain.identifiers import UserId, WorkspaceId

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
_ALICE = UserId("alice")
_BOB = UserId("bob")


def _workspace(
    wid: str,
    *,
    user: UserId = _ALICE,
    repo: str = "acme/widgets",
    status: str = WorkspaceStatus.WARM,
    last_active: datetime = _NOW,
) -> AgentWorkspace:
    return AgentWorkspace(
        id=WorkspaceId(wid),
        user_id=user,
        repo=repo,
        workspace_path=f"/srv/sandboxes/{wid}",
        status=status,
        created_at=_NOW,
        last_active_at=last_active,
    )


@pytest.fixture
def store() -> InMemoryWorkspaceStore:
    return InMemoryWorkspaceStore()


class TestExpiry:
    def test_idle_past_the_ttl_is_expired(self) -> None:
        workspace = _workspace("w1", last_active=_NOW - timedelta(hours=73))
        assert workspace.is_expired(now=_NOW, ttl_hours=72)

    def test_idle_inside_the_ttl_is_not(self) -> None:
        workspace = _workspace("w1", last_active=_NOW - timedelta(hours=71))
        assert not workspace.is_expired(now=_NOW, ttl_hours=72)

    def test_expiry_measures_inactivity_not_age(self) -> None:
        """A workspace created a month ago but used a minute ago is not stale. Measuring from
        created_at instead would delete the checkout of whoever uses this feature most."""
        old_but_busy = AgentWorkspace(
            id=WorkspaceId("w1"), user_id=_ALICE, repo="acme/widgets",
            workspace_path="/srv/sandboxes/w1", status=WorkspaceStatus.WARM,
            created_at=_NOW - timedelta(days=30),
            last_active_at=_NOW - timedelta(minutes=1),
        )
        assert not old_but_busy.is_expired(now=_NOW, ttl_hours=72)


class TestStoreContract:
    async def test_round_trip_by_user_and_repo(self, store) -> None:
        await store.upsert(_workspace("w1"))

        found = await store.get(_ALICE, "acme/widgets")
        assert found is not None and found.id == WorkspaceId("w1")

    async def test_missing_workspace_is_none_not_an_error(self, store) -> None:
        assert await store.get(_ALICE, "acme/widgets") is None
        assert await store.get_by_id(WorkspaceId("nope")) is None

    async def test_one_workspace_per_user_and_repo(self, store) -> None:
        """The isolation guarantee depends on this: two rows for one checkout would mean two
        containers bind-mounting the same directory and racing on the same files."""
        await store.upsert(_workspace("w1"))

        with pytest.raises(ValueError, match="already exists"):
            await store.upsert(_workspace("w2"))

    async def test_a_second_repo_gets_its_own_workspace(self, store) -> None:
        """Per-(user, repo) scoping is what stops a hostile repository reading another
        repository's checkout -- they are different containers and different filesystems."""
        await store.upsert(_workspace("w1", repo="acme/widgets"))
        await store.upsert(_workspace("w2", repo="acme/gadgets"))

        assert {w.id.value for w in await store.list_for_user(_ALICE)} == {"w1", "w2"}

    async def test_users_do_not_see_each_others_workspaces(self, store) -> None:
        await store.upsert(_workspace("w1", user=_ALICE))
        await store.upsert(_workspace("w2", user=_BOB))

        assert [w.id.value for w in await store.list_for_user(_ALICE)] == ["w1"]
        assert [w.id.value for w in await store.list_for_user(_BOB)] == ["w2"]

    async def test_same_repo_for_different_users_is_allowed(self, store) -> None:
        await store.upsert(_workspace("w1", user=_ALICE))
        await store.upsert(_workspace("w2", user=_BOB))

        assert (await store.get(_ALICE, "acme/widgets")).id == WorkspaceId("w1")
        assert (await store.get(_BOB, "acme/widgets")).id == WorkspaceId("w2")

    async def test_upsert_replaces_its_own_row(self, store) -> None:
        await store.upsert(_workspace("w1"))
        await store.upsert(_workspace("w1", status=WorkspaceStatus.HOT))

        assert (await store.get_by_id(WorkspaceId("w1"))).status == WorkspaceStatus.HOT

    async def test_list_for_user_is_most_recently_active_first(self, store) -> None:
        await store.upsert(_workspace("old", repo="a/one", last_active=_NOW - timedelta(days=2)))
        await store.upsert(_workspace("new", repo="a/two", last_active=_NOW))

        assert [w.id.value for w in await store.list_for_user(_ALICE)] == ["new", "old"]


class TestTouch:
    async def test_touch_moves_last_active(self, store) -> None:
        """The hot path: every tool call touches, and this timestamp is the only thing keeping a
        workspace in continuous use from being reaped on its third day."""
        await store.upsert(_workspace("w1", last_active=_NOW - timedelta(hours=71)))

        await store.touch(WorkspaceId("w1"), at=_NOW)

        assert (await store.get_by_id(WorkspaceId("w1"))).last_active_at == _NOW

    async def test_touch_leaves_status_alone_by_default(self, store) -> None:
        await store.upsert(_workspace("w1", status=WorkspaceStatus.HOT))

        await store.touch(WorkspaceId("w1"), at=_NOW)

        assert (await store.get_by_id(WorkspaceId("w1"))).status == WorkspaceStatus.HOT

    async def test_touch_can_move_status_in_the_same_write(self, store) -> None:
        await store.upsert(_workspace("w1", status=WorkspaceStatus.WARM))

        await store.touch(WorkspaceId("w1"), at=_NOW, status=WorkspaceStatus.HOT)

        assert (await store.get_by_id(WorkspaceId("w1"))).status == WorkspaceStatus.HOT

    async def test_touching_a_deleted_workspace_is_not_an_error(self, store) -> None:
        """The reaper and a live turn can race: the workspace is gone by the time the tool call
        records activity on it. Raising there would fail a turn over lost scratch state."""
        await store.touch(WorkspaceId("never-existed"), at=_NOW)


class TestReaperQueries:
    async def test_list_idle_since_selects_only_stale_workspaces(self, store) -> None:
        await store.upsert(_workspace("stale", repo="a/one", last_active=_NOW - timedelta(days=4)))
        await store.upsert(_workspace("fresh", repo="a/two", last_active=_NOW))

        cutoff = _NOW - timedelta(hours=72)
        assert [w.id.value for w in await store.list_idle_since(cutoff)] == ["stale"]

    async def test_list_idle_since_is_oldest_first(self, store) -> None:
        """The reaper frees the longest-abandoned workspace first, which is also the LRU order
        eviction wants when the cap is reached."""
        await store.upsert(_workspace("older", repo="a/one", last_active=_NOW - timedelta(days=9)))
        await store.upsert(_workspace("newer", repo="a/two", last_active=_NOW - timedelta(days=4)))

        assert [w.id.value for w in await store.list_idle_since(_NOW)] == ["older", "newer"]

    async def test_list_all_spans_users(self, store) -> None:
        """Reaping and startup reconciliation are host-wide concerns; neither has a user to
        scope by."""
        await store.upsert(_workspace("w1", user=_ALICE))
        await store.upsert(_workspace("w2", user=_BOB))

        assert {w.id.value for w in await store.list_all()} == {"w1", "w2"}

    async def test_delete_removes_the_row(self, store) -> None:
        await store.upsert(_workspace("w1"))

        await store.delete(WorkspaceId("w1"))

        assert await store.get_by_id(WorkspaceId("w1")) is None
        assert await store.get(_ALICE, "acme/widgets") is None

    async def test_delete_frees_the_user_and_repo_slot(self, store) -> None:
        await store.upsert(_workspace("w1"))
        await store.delete(WorkspaceId("w1"))

        await store.upsert(_workspace("w2"))

        assert (await store.get(_ALICE, "acme/widgets")).id == WorkspaceId("w2")
