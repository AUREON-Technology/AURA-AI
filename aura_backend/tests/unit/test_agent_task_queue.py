"""The agent task queue: priority, fairness, concurrency caps, bounds, scheduling, shutdown
(ADR-041, roadmap row 87).

The queue is the real one over the real task store (on the in-memory cache). The only stand-in is
the *runner*: an async function that records when a task starts and waits on a gate the test opens,
which is what lets a test hold slots busy and observe what the queue picks next. Kernel execution
itself is exercised through the HTTP gateway in ``test_agent_task_gateway.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.config.settings import AgentTaskSettings
from aura_core.llm.cache import InMemoryCache
from aura_core.tasks import (
    AgentTask,
    AgentTaskQueue,
    AgentTaskStore,
    TaskOutcome,
    TaskPriority,
    TaskQueueFullError,
    TaskStatus,
    is_stale,
)

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


class Harness:
    """A queue whose runner parks every task on a gate until the test releases it."""

    def __init__(self, *, clock: FrozenClock | None = None, **settings) -> None:
        defaults = dict(max_concurrent=1, max_concurrent_per_user=1, max_queued=50,
                        max_queued_per_user=50, aging_s=60.0, heartbeat_s=0.2, stale_after_s=5.0)
        defaults.update(settings)
        self.settings = AgentTaskSettings(**defaults)
        self.clock = clock or FrozenClock(T0)
        self.store = AgentTaskStore(InMemoryCache(max_entries=10_000))
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.live = 0
        self.peak = 0
        self.queue = AgentTaskQueue(
            store=self.store, runner=self._run, settings=self.settings, clock=self.clock
        )

    async def _run(self, task: AgentTask, spec) -> TaskOutcome:
        self.started.append(task.goal)
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await self.gates.setdefault(task.goal, asyncio.Event()).wait()
        except asyncio.CancelledError:
            self.cancelled.append(task.goal)
            raise
        finally:
            self.live -= 1
        return TaskOutcome(status=TaskStatus.SUCCEEDED, result={"message": "ok", "summary": "ok"})

    async def submit(self, goal: str, owner: str = "u", priority: str = TaskPriority.NORMAL,
                     run_at: datetime | None = None) -> AgentTask:
        now = self.clock.now()
        task = AgentTask(
            id=str(uuid.uuid4()), owner=owner, goal=goal, persona="orchestrator",
            status=TaskStatus.QUEUED, priority=priority, created_at=now, updated_at=now,
            run_at=run_at, heartbeat_at=now, attempts=1,
        )
        return await self.queue.submit(task, {"message": goal})

    def release(self, goal: str) -> None:
        self.gates.setdefault(goal, asyncio.Event()).set()

    async def settle(self) -> None:
        for _ in range(6):
            self.queue.poke()
            await asyncio.sleep(0.02)

    async def status(self, task: AgentTask) -> str:
        return (await self.store.get(task.owner, task.id)).status


@pytest.fixture
async def h():
    harness = Harness()
    yield harness
    await harness.queue.stop()


class TestPriorityAndOrder:
    async def test_high_before_normal_before_low(self, h: Harness) -> None:
        await h.submit("blocker")
        await h.settle()
        await h.submit("low", priority=TaskPriority.LOW)
        await h.submit("normal")
        await h.submit("high", priority=TaskPriority.HIGH)
        await h.settle()
        assert h.started == ["blocker"]
        for goal in ("blocker", "high", "normal"):
            h.release(goal)
            await h.settle()
        assert h.started == ["blocker", "high", "normal", "low"]

    async def test_arrival_order_among_equals(self, h: Harness) -> None:
        await h.submit("blocker")
        await h.settle()
        for goal in ("a", "b", "c"):
            await h.submit(goal)
        for goal in ("blocker", "a", "b"):
            h.release(goal)
            await h.settle()
        assert h.started == ["blocker", "a", "b", "c"]

    async def test_a_waiting_low_task_ages_past_newer_normal_ones(self, h: Harness) -> None:
        await h.submit("blocker")
        await h.settle()
        await h.submit("old-low", priority=TaskPriority.LOW)
        h.clock.advance(130)  # two aging periods: low -> high
        await h.submit("fresh-normal")
        h.release("blocker")
        await h.settle()
        assert h.started == ["blocker", "old-low"]

    async def test_position_reports_the_pickers_order(self, h: Harness) -> None:
        await h.submit("blocker")
        await h.settle()
        low = await h.submit("low", priority=TaskPriority.LOW)
        high = await h.submit("high", priority=TaskPriority.HIGH)
        assert h.queue.position(high.id) == 1 and h.queue.position(low.id) == 2


class TestConcurrencyAndFairness:
    async def test_the_global_cap_is_never_exceeded(self) -> None:
        h = Harness(max_concurrent=2, max_concurrent_per_user=2)
        try:
            for i in range(6):
                await h.submit(f"t{i}", owner=f"u{i}")
            await h.settle()
            assert h.live == 2
            for i in range(6):
                h.release(f"t{i}")
                await h.settle()
            assert h.peak == 2 and len(h.started) == 6
        finally:
            await h.queue.stop()

    async def test_one_user_cannot_hold_every_slot(self) -> None:
        h = Harness(max_concurrent=3, max_concurrent_per_user=1)
        try:
            for i in range(3):
                await h.submit(f"a{i}", owner="alice")
            await h.settle()
            assert h.started == ["a0"], "the per-user cap let one person run more than one task"
            await h.submit("b0", owner="bob")
            await h.settle()
            assert h.started == ["a0", "b0"]
        finally:
            await h.queue.stop()

    async def test_a_late_users_task_is_not_stuck_behind_anothers_backlog(self) -> None:
        h = Harness(max_concurrent=2, max_concurrent_per_user=1)
        try:
            for i in range(4):
                await h.submit(f"a{i}", owner="alice")
            await h.submit("b0", owner="bob")
            await h.settle()
            assert h.started == ["a0", "b0"]
            h.release("a0")
            await h.settle()
            assert h.started[2] == "a1"
        finally:
            await h.queue.stop()

    async def test_fewest_running_goes_first_among_equal_priority(self) -> None:
        h = Harness(max_concurrent=2, max_concurrent_per_user=2)
        try:
            await h.submit("a0", owner="alice")
            await h.submit("a1", owner="alice")
            await h.settle()
            assert h.started == ["a0", "a1"]
            await h.submit("a2", owner="alice")
            await h.submit("b0", owner="bob")
            h.release("a0")
            await h.settle()
            # alice still runs one, bob none: bob's later task goes before alice's earlier one.
            assert h.started[2] == "b0"
        finally:
            await h.queue.stop()


class TestBounds:
    async def test_a_full_queue_refuses_with_a_retry_after(self) -> None:
        h = Harness(max_queued=2)
        try:
            await h.submit("running")
            await h.settle()
            await h.submit("w1", owner="a")
            await h.submit("w2", owner="b")
            with pytest.raises(TaskQueueFullError) as refused:
                await h.submit("w3", owner="c")
            assert refused.value.http_status == 429 and refused.value.retry_after >= 1
            assert refused.value.details["scope"] == "global"
            # Nothing was accepted: no record exists for the refused task.
            assert len(await h.store.list_tasks("c")) == 0
        finally:
            await h.queue.stop()

    async def test_one_user_s_waiting_tasks_are_bounded_separately(self) -> None:
        h = Harness(max_queued_per_user=2)
        try:
            await h.submit("running", owner="other")
            await h.settle()
            await h.submit("w1", owner="alice")
            await h.submit("w2", owner="alice")
            with pytest.raises(TaskQueueFullError) as refused:
                await h.submit("w3", owner="alice")
            assert refused.value.details["scope"] == "user"
            await h.submit("w4", owner="bob")  # someone else is unaffected
        finally:
            await h.queue.stop()


class TestScheduledStart:
    async def test_run_at_keeps_a_task_waiting_until_its_time(self, h: Harness) -> None:
        task = await h.submit("later", run_at=T0 + timedelta(seconds=100))
        await h.settle()
        assert h.started == [] and await h.status(task) == TaskStatus.QUEUED
        h.clock.advance(101)
        await h.settle()
        assert h.started == ["later"]

    async def test_a_scheduled_task_does_not_block_an_immediate_one(self, h: Harness) -> None:
        await h.submit("later", run_at=T0 + timedelta(hours=1))
        await h.submit("now")
        await h.settle()
        assert h.started == ["now"]


class TestLifecycle:
    async def test_completion_records_the_result_and_frees_the_slot(self, h: Harness) -> None:
        first = await h.submit("one")
        second = await h.submit("two")
        await h.settle()
        assert await h.status(first) == TaskStatus.RUNNING and await h.status(second) == TaskStatus.QUEUED
        h.release("one")
        await h.settle()
        done = await h.store.get("u", first.id)
        assert done.status == TaskStatus.SUCCEEDED and done.result["message"] == "ok"
        assert done.finished_at is not None and done.started_at is not None
        assert h.started == ["one", "two"]

    async def test_cancelling_a_waiting_task_stops_it_ever_starting(self, h: Harness) -> None:
        await h.submit("running")
        waiting = await h.submit("waiting")
        await h.settle()
        cancelled = await h.queue.cancel(await h.store.get("u", waiting.id))
        assert cancelled.status == TaskStatus.CANCELLED
        h.release("running")
        await h.settle()
        assert h.started == ["running"] and h.queue.stats()["queued"] == 0

    async def test_cancelling_a_running_task_cancels_its_turn(self, h: Harness) -> None:
        task = await h.submit("running")
        await h.settle()
        cancelled = await h.queue.cancel(await h.store.get("u", task.id))
        assert cancelled.status == TaskStatus.CANCELLED
        assert h.cancelled == ["running"] and h.queue.stats()["running"] == 0

    async def test_a_cancel_made_on_another_worker_is_honoured_by_the_owner(self, h: Harness) -> None:
        task = await h.submit("running")
        await h.settle()
        # What another worker's cancel route writes: the flag, on the shared record.
        record = await h.store.get("u", task.id)
        await h.store.save(record.with_(cancel_requested=True))
        await h.queue._beat()
        await h.settle()
        assert h.cancelled == ["running"]
        assert await h.status(task) == TaskStatus.CANCELLED

    async def test_a_cancel_that_landed_first_stays_a_cancel(self, h: Harness) -> None:
        task = await h.submit("running")
        await h.settle()
        record = await h.store.get("u", task.id)
        await h.store.save(record.with_(status=TaskStatus.CANCELLED))
        h.release("running")  # the turn finishes anyway
        await h.settle()
        assert await h.status(task) == TaskStatus.CANCELLED

    async def test_shutdown_cancels_running_work_and_marks_every_task(self) -> None:
        h = Harness()
        running = await h.submit("running")
        waiting = await h.submit("waiting")
        await h.settle()
        await h.queue.stop()
        assert h.cancelled == ["running"]
        for task in (running, waiting):
            final = await h.store.get("u", task.id)
            assert final.status == TaskStatus.FAILED
            assert final.error["kind"] == "server_shutdown" and final.error["retryable"] is True
        assert h.queue.stats() == {"running": 0, "queued": 0, "max_concurrent": 1, "max_queued": 50}

    async def test_a_crashing_runner_fails_the_task_and_not_the_queue(self) -> None:
        h = Harness()

        async def boom(task, spec):
            if task.goal == "bad":
                raise RuntimeError("kaboom")
            return TaskOutcome(status=TaskStatus.SUCCEEDED, result={"message": "ok"})

        h.queue._runner = boom
        try:
            bad = await h.submit("bad")
            good = await h.submit("good")
            await h.settle()
            assert (await h.store.get("u", bad.id)).error["kind"] == "internal_error"
            assert await h.status(good) == TaskStatus.SUCCEEDED
        finally:
            await h.queue.stop()

    async def test_a_task_over_its_time_limit_fails_with_timeout(self) -> None:
        h = Harness(task_timeout_s=0.3)
        try:
            task = await h.submit("slow")  # its gate is never opened
            await asyncio.sleep(0.7)
            final = await h.store.get("u", task.id)
            assert final.status == TaskStatus.FAILED and final.error["kind"] == "timeout"
            assert h.cancelled == ["slow"] and h.queue.stats()["running"] == 0
        finally:
            await h.queue.stop()

    async def test_heartbeats_keep_a_live_task_from_reading_stale(self, h: Harness) -> None:
        task = await h.submit("running")
        await h.settle()
        h.clock.advance(4)
        await h.queue._beat()
        record = await h.store.get("u", task.id)
        assert record.heartbeat_at == h.clock.now()
        assert not is_stale(record, now=h.clock.now(), stale_after_s=5.0)
        # A record nobody is vouching for any more is stale.
        assert is_stale(record, now=h.clock.now() + timedelta(seconds=60), stale_after_s=5.0)
        assert not is_stale(
            record.with_(status=TaskStatus.AWAITING_APPROVAL), now=h.clock.now() + timedelta(days=1),
            stale_after_s=5.0,
        )
