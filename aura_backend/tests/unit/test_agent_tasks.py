"""``AgentTaskManager`` (aura_core/agents/tasks.py, ADR-040, inventory row 57).

The manager is exercised with real asyncio tasks and the real in-memory ``Cache`` adapter. The only
stand-in is the *runner* -- the coroutine a sub-turn would be -- because what is under test is the
lifecycle around it: caps, ownership, deadline, cancellation (local and from another worker),
expiry and loss of a worker.
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.agents.tasks import AgentTaskLimitError, AgentTaskManager, TaskResult
from aura_core.llm.cache import InMemoryCache

ALICE, BOB = "alice", "bob"


@pytest.fixture
def cache() -> InMemoryCache:
    return InMemoryCache(max_entries=512, default_ttl_s=3600)


class Gate:
    """A runner that blocks until released, so a test controls when a task finishes."""

    def __init__(self, text: str = "done") -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.text = text

    async def __call__(self) -> TaskResult:
        self.started.set()
        await self.release.wait()
        return TaskResult(ok=True, text=self.text)


async def _submit(manager: AgentTaskManager, runner, *, owner: str = ALICE, agent: str = "research_agent"):
    return await manager.submit(owner_id=owner, agent_id=agent, subtask="look into it", runner=runner)


class TestLifecycle:
    async def test_submit_returns_at_once_and_the_result_is_readable_when_done(self, cache) -> None:
        manager = AgentTaskManager(cache)
        gate = Gate("the findings")
        task = await _submit(manager, gate)
        assert task.status == "running" and task.id.startswith("at_")

        await gate.started.wait()
        assert (await manager.get(ALICE, task.id)).status == "running"

        gate.release.set()
        await manager.drain()
        done = await manager.get(ALICE, task.id)
        assert done.status == "succeeded" and done.result == "the findings" and done.finished_at

    async def test_a_runner_that_raises_is_a_failed_task_not_a_crash(self, cache) -> None:
        manager = AgentTaskManager(cache)

        async def boom() -> TaskResult:
            raise RuntimeError("provider exploded")

        task = await _submit(manager, boom)
        await manager.drain()
        failed = await manager.get(ALICE, task.id)
        assert failed.status == "failed" and "RuntimeError" in failed.error

    async def test_a_runner_reporting_failure_keeps_its_message(self, cache) -> None:
        manager = AgentTaskManager(cache)

        async def refuse() -> TaskResult:
            return TaskResult(ok=False, error="needs the user's own confirmation for: propose_expense")

        task = await _submit(manager, refuse)
        await manager.drain()
        assert (await manager.get(ALICE, task.id)).error.startswith("needs the user's own confirmation")

    async def test_the_deadline_ends_a_runaway_task(self, cache) -> None:
        manager = AgentTaskManager(cache, deadline_s=1.0)  # floor of the setting is 1s
        manager._deadline_s = 0.05  # shorter than the public floor, for test speed

        async def forever() -> TaskResult:
            await asyncio.sleep(30)
            return TaskResult(ok=True)

        task = await _submit(manager, forever)
        await manager.drain()
        timed_out = await manager.get(ALICE, task.id)
        assert timed_out.status == "failed" and "deadline" in timed_out.error

    async def test_the_result_is_capped(self, cache) -> None:
        manager = AgentTaskManager(cache)
        gate = Gate("x" * 50_000)
        task = await _submit(manager, gate)
        gate.release.set()
        await manager.drain()
        assert len((await manager.get(ALICE, task.id)).result) == 8_000


class TestOwnership:
    async def test_another_users_task_is_not_found_by_get_cancel_or_list(self, cache) -> None:
        manager = AgentTaskManager(cache)
        gate = Gate()
        task = await _submit(manager, gate)
        await gate.started.wait()

        assert await manager.get(BOB, task.id) is None
        assert await manager.cancel(BOB, task.id) is None
        assert await manager.list(BOB) == []
        assert (await manager.get(ALICE, task.id)).status == "running", "Bob's cancel must not touch it"

        gate.release.set()
        await manager.drain()

    async def test_ids_with_colons_cannot_collide_across_owners(self, cache) -> None:
        manager = AgentTaskManager(cache)
        gate = Gate()
        task = await _submit(manager, gate, owner="a:b")
        assert await manager.get("a", task.id) is None
        assert await manager.get("a:b", task.id) is not None
        gate.release.set()
        await manager.drain()


class TestCaps:
    async def test_per_user_cap_refuses_the_next_and_frees_on_completion(self, cache) -> None:
        manager = AgentTaskManager(cache, per_user=2)
        gates = [Gate(), Gate(), Gate()]
        await _submit(manager, gates[0])
        await _submit(manager, gates[1])
        with pytest.raises(AgentTaskLimitError) as refused:
            await _submit(manager, gates[2])
        assert refused.value.scope == "user"

        # Another user is not held back by Alice's tasks.
        other = Gate()
        await _submit(manager, other, owner=BOB)

        gates[0].release.set()
        await asyncio.sleep(0.05)
        await _submit(manager, gates[2])  # a slot was freed
        for gate in (*gates, other):
            gate.release.set()
        await manager.drain()

    async def test_the_cap_counts_tasks_another_worker_started(self, cache) -> None:
        """Two managers over one cache stand for two workers: the per-user cap reads the shared
        records, so a user cannot exceed it by landing on different workers."""
        one, two = AgentTaskManager(cache, per_user=1), AgentTaskManager(cache, per_user=1)
        gate = Gate()
        await _submit(one, gate)
        with pytest.raises(AgentTaskLimitError) as refused:
            await _submit(two, Gate())
        assert refused.value.scope == "user"
        gate.release.set()
        await one.drain()

    async def test_global_cap_is_per_process(self, cache) -> None:
        manager = AgentTaskManager(cache, per_user=10, global_cap=2)
        gates = [Gate(), Gate(), Gate()]
        await _submit(manager, gates[0], owner="u1")
        await _submit(manager, gates[1], owner="u2")
        with pytest.raises(AgentTaskLimitError) as refused:
            await _submit(manager, gates[2], owner="u3")
        assert refused.value.scope == "global"
        for gate in gates:
            gate.release.set()
        await manager.drain()


class TestCancellation:
    async def test_cancelling_a_local_task_stops_it_and_records_cancelled(self, cache) -> None:
        manager = AgentTaskManager(cache)
        ran_to_end = False

        async def long() -> TaskResult:
            nonlocal ran_to_end
            await asyncio.sleep(30)
            ran_to_end = True
            return TaskResult(ok=True)

        task = await _submit(manager, long)
        cancelled = await manager.cancel(ALICE, task.id)
        assert cancelled.status == "cancelled" and not ran_to_end
        assert manager.running_here == 0

    async def test_cancelling_a_finished_task_changes_nothing(self, cache) -> None:
        manager = AgentTaskManager(cache)
        gate = Gate("kept")
        task = await _submit(manager, gate)
        gate.release.set()
        await manager.drain()
        again = await manager.cancel(ALICE, task.id)
        assert again.status == "succeeded" and again.result == "kept"

    async def test_a_cancel_asked_on_another_worker_reaches_the_owning_one(self, cache) -> None:
        owner_worker = AgentTaskManager(cache, poll_s=0.01)
        other_worker = AgentTaskManager(cache, poll_s=0.01)
        started = asyncio.Event()

        async def long() -> TaskResult:
            started.set()
            await asyncio.sleep(30)
            return TaskResult(ok=True)

        task = await _submit(owner_worker, long)
        await started.wait()
        assert other_worker.running_here == 0

        flagged = await other_worker.cancel(ALICE, task.id)
        assert flagged.cancel_requested and flagged.status == "running"

        await owner_worker.drain()
        final = await other_worker.get(ALICE, task.id)
        assert final.status == "cancelled"

    async def test_a_late_result_does_not_overwrite_a_cancel(self, cache) -> None:
        manager = AgentTaskManager(cache)
        gate = Gate("too late")
        task = await _submit(manager, gate)
        await gate.started.wait()
        await manager.cancel(ALICE, task.id)
        gate.release.set()
        await manager.drain()
        assert (await manager.get(ALICE, task.id)).status == "cancelled"

    async def test_shutdown_cancels_what_this_process_runs(self, cache) -> None:
        manager = AgentTaskManager(cache)
        gate = Gate()
        task = await _submit(manager, gate)
        await gate.started.wait()
        await manager.shutdown()
        assert manager.running_here == 0
        assert (await manager.get(ALICE, task.id)).status == "cancelled"


class TestExpiryAndLoss:
    async def test_a_task_whose_worker_died_is_reported_lost_not_running_forever(self, cache) -> None:
        now = [1_000.0]
        dead_worker = AgentTaskManager(cache, deadline_s=60.0, clock=lambda: now[0])
        gate = Gate()
        task = await _submit(dead_worker, gate)
        await gate.started.wait()

        # Another worker reads the shared record long after its deadline and grace period.
        reader = AgentTaskManager(cache, deadline_s=60.0, clock=lambda: now[0])
        assert (await reader.get(ALICE, task.id)).status == "running"
        now[0] += 60.0 + 61.0
        lost = await reader.get(ALICE, task.id)
        assert lost.status == "failed" and lost.error.startswith("lost")

        # ... and it no longer counts against the user's cap.
        capped = AgentTaskManager(cache, per_user=1, clock=lambda: now[0])
        replacement = Gate()
        await _submit(capped, replacement)
        replacement.release.set()
        await capped.drain()
        gate.release.set()
        await dead_worker.drain()

    async def test_records_expire_and_the_index_is_pruned(self, cache) -> None:
        manager = AgentTaskManager(cache, ttl_s=60)
        gate = Gate()
        task = await _submit(manager, gate)
        gate.release.set()
        await manager.drain()
        assert [t.id for t in await manager.list(ALICE)] == [task.id]

        # Age every cached entry past its expiry, as the cache's own TTL eventually does.
        for key, (_expires, value) in list(cache._data.items()):
            cache._data[key] = (1.0, value)
        assert await manager.get(ALICE, task.id) is None
        assert await manager.list(ALICE) == []

    async def test_list_is_newest_first_and_limited(self, cache) -> None:
        manager = AgentTaskManager(cache, per_user=10)
        gates = []
        ids = []
        for _ in range(4):
            gate = Gate()
            gates.append(gate)
            ids.append((await _submit(manager, gate)).id)
        listed = await manager.list(ALICE, limit=3)
        assert [t.id for t in listed] == list(reversed(ids))[:3]
        for gate in gates:
            gate.release.set()
        await manager.drain()


class TestContextIsolation:
    async def test_the_background_task_does_not_inherit_the_callers_context_variables(self, cache) -> None:
        import contextvars

        marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")
        seen: list[str] = []

        async def reads() -> TaskResult:
            seen.append(marker.get())
            return TaskResult(ok=True)

        manager = AgentTaskManager(cache)
        marker.set("parent-turn-state")
        await _submit(manager, reads)
        await manager.drain()
        assert seen == ["unset"]
