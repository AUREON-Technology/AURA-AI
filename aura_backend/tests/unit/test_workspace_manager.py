"""`WorkspaceManager`: the hot/warm lifecycle, admission control, LRU, and reconciliation.

Run against the real in-memory substrate -- `InMemorySandboxRunner` really creates directories
that really persist across `open_workspace` calls, and `InMemoryWorkspaceStore` really enforces
the one-workspace-per-(user, repo) index. Nothing here is mocked, so these exercise the same code
production runs, minus the container.

The capacity claim this whole design rests on is that an idle workspace costs no memory because
its container is *stopped*. What can be proved here is the bookkeeping: that pausing happens when
it should, that it does not refresh the idle clock, and that the running count is what the
ceiling is applied to. That a stopped container actually releases RAM is a property of Docker and
belongs to `tests/load/test_docker_sandbox_live.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.adapters.memory_stores import InMemoryWorkspaceStore
from aura_core.domain.engineering import WorkspaceStatus
from aura_core.domain.identifiers import UserId
from aura_core.tools.workspaces import (
    WorkspaceCapacityError,
    WorkspaceManager,
    available_memory_mb,
    workspace_key,
)

_ALICE = UserId("alice")
_BOB = UserId("bob")


class _Clock:
    """Advanceable clock, so a 72-hour TTL is testable without waiting three days."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


@pytest.fixture
def clock() -> _Clock:
    return _Clock(datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def runner() -> InMemorySandboxRunner:
    return InMemorySandboxRunner()


@pytest.fixture
def store() -> InMemoryWorkspaceStore:
    return InMemoryWorkspaceStore()


@pytest.fixture
def manager(runner, store, clock) -> WorkspaceManager:
    return WorkspaceManager(
        runner=runner, store=store, clock=clock,
        max_workspaces=3, max_running=2, ttl_hours=72.0, hot_idle_s=900.0,
        # The memory floor is exercised explicitly below; the default would make every other
        # test here depend on how much RAM the machine running it happens to have free.
        min_free_memory_mb=0,
    )


class TestPersistence:
    async def test_the_same_repo_resumes_the_same_workspace(self, manager) -> None:
        """The point of the feature: a later turn continues where the last one left off."""
        first = await manager.open(_ALICE, "acme/widgets")
        await manager.pause(first.record.id)
        second = await manager.open(_ALICE, "acme/widgets")

        assert second.record.id == first.record.id

    async def test_files_survive_a_pause_and_resume(self, manager, runner) -> None:
        opened = await manager.open(_ALICE, "acme/widgets")
        await runner.write_file(opened.handle, "notes.txt", "work in progress")

        await manager.pause(opened.record.id)
        resumed = await manager.open(_ALICE, "acme/widgets")

        assert await runner.read_file(resumed.handle, "notes.txt") == "work in progress"

    async def test_a_second_repo_gets_its_own_workspace(self, manager) -> None:
        """Isolation by construction: a hostile repository cannot read the other's checkout
        because it is a different container over a different directory."""
        one = await manager.open(_ALICE, "acme/widgets")
        two = await manager.open(_ALICE, "acme/gadgets")

        assert one.record.id != two.record.id

    async def test_two_users_on_one_repo_do_not_share(self, manager) -> None:
        alice = await manager.open(_ALICE, "acme/widgets")
        bob = await manager.open(_BOB, "acme/widgets")

        assert alice.record.id != bob.record.id

    def test_the_key_is_stable_and_scoped(self) -> None:
        assert workspace_key(_ALICE, "a/b") == workspace_key(_ALICE, "a/b")
        assert workspace_key(_ALICE, "a/b") != workspace_key(_BOB, "a/b")
        assert workspace_key(_ALICE, "a/b") != workspace_key(_ALICE, "a/c")
        # Becomes a Docker label value and a directory name, so it must not carry the slash a
        # repository name always has.
        assert "/" not in workspace_key(_ALICE, "a/b")


class TestHotWarmLifecycle:
    async def test_opening_makes_a_workspace_hot(self, manager, store) -> None:
        opened = await manager.open(_ALICE, "acme/widgets")

        assert (await store.get_by_id(opened.record.id)).status == WorkspaceStatus.HOT

    async def test_pausing_makes_it_warm_and_stops_the_container(
        self, manager, store, runner
    ) -> None:
        opened = await manager.open(_ALICE, "acme/widgets")

        await manager.pause(opened.record.id)

        assert (await store.get_by_id(opened.record.id)).status == WorkspaceStatus.WARM
        assert runner.is_paused(opened.record.id.value), (
            "a warm workspace whose container is still running costs exactly the memory this "
            "design claims it does not"
        )

    async def test_pausing_does_not_refresh_the_idle_clock(self, manager, store, clock) -> None:
        """Pausing is housekeeping, not use. If it touched last_active_at, an abandoned
        workspace would be kept alive forever by the very sweep meant to reclaim it."""
        opened = await manager.open(_ALICE, "acme/widgets")
        opened_at = (await store.get_by_id(opened.record.id)).last_active_at

        clock.advance(hours=5)
        await manager.pause(opened.record.id)

        assert (await store.get_by_id(opened.record.id)).last_active_at == opened_at

    async def test_touch_moves_the_idle_clock(self, manager, store, clock) -> None:
        opened = await manager.open(_ALICE, "acme/widgets")

        clock.advance(hours=5)
        await manager.touch(opened.record.id)

        assert (await store.get_by_id(opened.record.id)).last_active_at == clock.now()

    async def test_reopening_a_hot_workspace_is_a_no_op(self, manager, store) -> None:
        first = await manager.open(_ALICE, "acme/widgets")
        second = await manager.open(_ALICE, "acme/widgets")

        assert second.record.id == first.record.id
        assert second.evicted == ()


class TestRunningCeiling:
    async def test_a_third_concurrent_workspace_pauses_the_lru_one(
        self, manager, store, clock
    ) -> None:
        """This is the ceiling that bounds memory: peak RAM is max_running * memory_limit, and
        it must not grow just because a third repository was asked for."""
        first = await manager.open(_ALICE, "a/one")
        clock.advance(minutes=1)
        second = await manager.open(_ALICE, "a/two")
        clock.advance(minutes=1)

        await manager.open(_ALICE, "a/three")

        assert (await store.get_by_id(first.record.id)).status == WorkspaceStatus.WARM
        assert (await store.get_by_id(second.record.id)).status == WorkspaceStatus.HOT

    async def test_hot_count_never_exceeds_max_running(self, manager, store, clock) -> None:
        for index, repo in enumerate(("a/one", "a/two", "a/three")):
            clock.advance(minutes=index + 1)
            await manager.open(_ALICE, repo)

        hot = [w for w in await store.list_all() if w.status == WorkspaceStatus.HOT]
        assert len(hot) <= 2

    async def test_eviction_is_reported_rather_than_silent(self, manager, clock) -> None:
        """A workspace that goes warm between turns is fine; one that vanishes with no
        explanation is how a user loses track of what the agent is doing."""
        await manager.open(_ALICE, "a/one")
        clock.advance(minutes=1)
        await manager.open(_ALICE, "a/two")
        clock.advance(minutes=1)

        third = await manager.open(_ALICE, "a/three")

        assert any("paused" in note for note in third.evicted)

    async def test_resuming_a_warm_workspace_does_not_evict_itself(
        self, manager, store, clock
    ) -> None:
        target = await manager.open(_ALICE, "a/one")
        await manager.pause(target.record.id)
        clock.advance(minutes=1)
        await manager.open(_ALICE, "a/two")
        clock.advance(minutes=1)

        resumed = await manager.open(_ALICE, "a/one")

        assert resumed.record.id == target.record.id
        assert (await store.get_by_id(target.record.id)).status == WorkspaceStatus.HOT


class TestWorkspaceCeiling:
    async def test_beyond_the_limit_the_lru_workspace_is_removed(
        self, manager, store, clock
    ) -> None:
        oldest = await manager.open(_ALICE, "a/one")
        await manager.pause(oldest.record.id)
        clock.advance(hours=1)
        await manager.open(_ALICE, "a/two")
        clock.advance(hours=1)
        await manager.open(_ALICE, "a/three")
        clock.advance(hours=1)

        await manager.open(_ALICE, "a/four")

        assert await store.get_by_id(oldest.record.id) is None
        assert len(await store.list_all()) <= 3

    async def test_removing_a_checkout_is_always_reported(self, manager, clock) -> None:
        """Deleting a real checkout is the one eviction a user cannot recover from by waiting,
        so it is never allowed to be silent."""
        for repo in ("a/one", "a/two", "a/three"):
            clock.advance(hours=1)
            opened = await manager.open(_ALICE, repo)
            await manager.pause(opened.record.id)
        clock.advance(hours=1)

        fourth = await manager.open(_ALICE, "a/four")

        assert any("removed" in note for note in fourth.evicted)


class TestAdmissionControl:
    async def test_refuses_when_the_host_is_short_on_memory(self, runner, store, clock) -> None:
        """Refusing beats starting it anyway: the largest process on this host is the model
        server, so letting the OOM killer choose means an unrelated user's chat dies for a build.
        """
        manager = WorkspaceManager(
            runner=runner, store=store, clock=clock,
            memory_limit="512m", min_free_memory_mb=10_000_000,
        )

        with pytest.raises(WorkspaceCapacityError, match="not enough free memory"):
            await manager.open(_ALICE, "acme/widgets")

    async def test_refusal_leaves_nothing_half_created(self, runner, store, clock) -> None:
        manager = WorkspaceManager(
            runner=runner, store=store, clock=clock,
            memory_limit="512m", min_free_memory_mb=10_000_000,
        )

        with pytest.raises(WorkspaceCapacityError):
            await manager.open(_ALICE, "acme/widgets")

        assert await store.list_all() == []
        assert await runner.workspace_keys() == []

    async def test_admits_when_memory_cannot_be_measured(self, runner, store, clock) -> None:
        """A missing metric is not evidence of pressure. Failing closed here would make the
        feature unusable on any platform without /proc/meminfo, for no safety gain."""
        assert available_memory_mb("/nonexistent/meminfo") is None

        manager = WorkspaceManager(
            runner=runner, store=store, clock=clock, min_free_memory_mb=10_000_000,
        )
        manager._check_memory = lambda: None  # what a None reading does inside open()

        assert (await manager.open(_ALICE, "acme/widgets")).record is not None

    def test_available_memory_reads_mem_available(self, tmp_path) -> None:
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(
            "MemTotal:       16000000 kB\nMemFree:          100000 kB\n"
            "MemAvailable:    2097152 kB\n"
        )

        assert available_memory_mb(str(meminfo)) == pytest.approx(2048.0)

    def test_available_memory_prefers_available_over_free(self, tmp_path) -> None:
        """MemFree on a busy host is near zero because the page cache uses the rest; refusing on
        that basis would refuse everything, forever."""
        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemFree: 1024 kB\nMemAvailable: 8388608 kB\n")

        assert available_memory_mb(str(meminfo)) == pytest.approx(8192.0)


class TestExpiry:
    async def test_expired_selects_only_workspaces_past_the_ttl(
        self, manager, clock
    ) -> None:
        stale = await manager.open(_ALICE, "a/stale")
        await manager.pause(stale.record.id)
        clock.advance(hours=73)
        fresh = await manager.open(_ALICE, "a/fresh")

        expired = await manager.expired()

        assert [w.id for w in expired] == [stale.record.id]
        assert fresh.record.id not in {w.id for w in expired}

    async def test_continuous_use_keeps_a_workspace_alive(self, manager, clock) -> None:
        """Three days of *inactivity*, not three days of age."""
        opened = await manager.open(_ALICE, "a/one")

        for _ in range(5):
            clock.advance(hours=20)
            await manager.touch(opened.record.id)

        assert await manager.expired() == []

    async def test_stale_hot_finds_workspaces_left_running(self, manager, clock) -> None:
        """A crashed or abandoned turn leaves a container holding memory nobody is using."""
        opened = await manager.open(_ALICE, "a/one")
        clock.advance(seconds=901)

        assert [w.id for w in await manager.stale_hot()] == [opened.record.id]

    async def test_stale_hot_ignores_a_workspace_in_active_use(self, manager, clock) -> None:
        opened = await manager.open(_ALICE, "a/one")
        clock.advance(seconds=901)
        await manager.touch(opened.record.id)

        assert await manager.stale_hot() == []


class TestReconciliation:
    async def test_a_container_with_no_row_is_removed(self, manager, runner) -> None:
        """Orphans are unreachable -- nothing can find them again -- so they are pure leak."""
        await runner.open_workspace("orphan-key")

        summary = await manager.reconcile()

        assert summary["orphan_containers"] == 1
        assert await runner.workspace_keys() == []

    async def test_a_row_whose_container_vanished_is_downgraded(
        self, manager, store, runner
    ) -> None:
        """Resolved towards the store rather than deleted: the next open recreates the container
        instead of failing, which is the recoverable direction."""
        opened = await manager.open(_ALICE, "a/one")
        await runner.destroy_workspace(opened.handle)

        summary = await manager.reconcile()

        assert summary["missing_containers"] == 1
        assert (await store.get_by_id(opened.record.id)).status == WorkspaceStatus.WARM

    async def test_reconciliation_leaves_a_consistent_pair_alone(self, manager, store) -> None:
        opened = await manager.open(_ALICE, "a/one")

        summary = await manager.reconcile()

        assert summary == {"orphan_containers": 0, "missing_containers": 0}
        assert await store.get_by_id(opened.record.id) is not None

    async def test_reconciliation_survives_an_unavailable_runner(self, store, clock) -> None:
        """Startup must not be blocked by a Docker daemon that is not answering yet."""

        class _Broken:
            async def workspace_keys(self):
                raise RuntimeError("daemon not reachable")

        manager = WorkspaceManager(runner=_Broken(), store=store, clock=clock)

        assert await manager.reconcile() == {"orphan_containers": 0, "missing_containers": 0}


class TestReaper:
    """`reap()` is the only thing bounding this feature's disk and memory over time."""

    async def test_expired_workspaces_are_removed(self, manager, store, runner, clock) -> None:
        opened = await manager.open(_ALICE, "a/one")
        await manager.pause(opened.record.id)
        clock.advance(hours=73)

        summary = await manager.reap()

        assert summary["expired"] == 1
        assert await store.get_by_id(opened.record.id) is None
        assert await runner.workspace_keys() == []

    async def test_a_workspace_in_use_is_never_reaped(self, manager, store, clock) -> None:
        """The failure that would matter most: deleting a checkout somebody is working in."""
        opened = await manager.open(_ALICE, "a/one")
        for _ in range(6):
            clock.advance(hours=20)
            await manager.touch(opened.record.id)

        await manager.reap()

        assert await store.get_by_id(opened.record.id) is not None

    async def test_idle_hot_workspaces_are_paused_not_deleted(
        self, manager, store, clock
    ) -> None:
        """An abandoned turn holds memory until this runs; pausing returns it and loses nothing,
        so it happens long before the TTL is anywhere near."""
        opened = await manager.open(_ALICE, "a/one")
        clock.advance(seconds=901)

        summary = await manager.reap()

        assert summary["paused"] == 1
        assert summary["expired"] == 0
        record = await store.get_by_id(opened.record.id)
        assert record is not None and record.status == WorkspaceStatus.WARM

    async def test_pausing_does_not_postpone_expiry(self, manager, store, clock) -> None:
        """If the pause refreshed last_active_at, the sweep meant to reclaim an abandoned
        workspace would keep it alive forever instead."""
        opened = await manager.open(_ALICE, "a/one")
        clock.advance(seconds=901)
        await manager.reap()

        clock.advance(hours=72)
        await manager.reap()

        assert await store.get_by_id(opened.record.id) is None

    async def test_reap_is_idempotent(self, manager, store, clock) -> None:
        """Beat restarts and manual triggers overlap; two passes must converge, not conflict."""
        opened = await manager.open(_ALICE, "a/one")
        await manager.pause(opened.record.id)
        clock.advance(hours=73)

        first = await manager.reap()
        second = await manager.reap()

        assert first["expired"] == 1
        assert second["expired"] == 0
        assert second["errors"] == 0

    async def test_one_wedged_workspace_does_not_abandon_the_rest(
        self, store, clock, runner
    ) -> None:
        """Precisely when the sweep matters most: a container that will not die must not stop
        the others from being reclaimed."""

        class _PartlyBroken(InMemorySandboxRunner):
            async def destroy_workspace(self, handle):
                if handle.task_id == wedged_id:
                    raise RuntimeError("container will not die")
                await super().destroy_workspace(handle)

        broken = _PartlyBroken()
        manager = WorkspaceManager(
            runner=broken, store=store, clock=clock, min_free_memory_mb=0, max_workspaces=8
        )
        wedged = await manager.open(_ALICE, "a/wedged")
        wedged_id = wedged.record.id.value
        healthy = await manager.open(_ALICE, "a/healthy")
        await manager.pause(wedged.record.id)
        await manager.pause(healthy.record.id)
        clock.advance(hours=73)

        summary = await manager.reap()

        assert summary["errors"] == 1
        assert summary["expired"] == 1
        assert await store.get_by_id(healthy.record.id) is None
        assert await store.get_by_id(wedged.record.id) is not None

    async def test_expiry_is_recorded_in_the_run_history(self, manager, clock) -> None:
        """A workspace that simply is not there one day, with no record of why, is worse than
        one that expired -- so the reap writes to the same history the agent's actions use."""
        from aura_core.adapters.memory_stores import InMemoryEngineerRunStore

        runs = InMemoryEngineerRunStore()
        opened = await manager.open(_ALICE, "a/one")
        await manager.pause(opened.record.id)
        clock.advance(hours=73)

        await manager.reap(run_sink=runs)

        recorded = await runs.list_for_user(_ALICE)
        assert len(recorded) == 1
        assert "a/one" in recorded[0].sandbox_transcript
        assert "expired" in recorded[0].sandbox_transcript

    async def test_history_failure_never_blocks_reclaiming_disk(self, manager, clock) -> None:
        class _BrokenSink:
            async def record(self, run):
                raise RuntimeError("history unavailable")

        opened = await manager.open(_ALICE, "a/one")
        await manager.pause(opened.record.id)
        clock.advance(hours=73)

        summary = await manager.reap(run_sink=_BrokenSink())

        assert summary["expired"] == 1
