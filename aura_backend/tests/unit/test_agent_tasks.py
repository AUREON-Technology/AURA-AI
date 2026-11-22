"""Agent runs as tasks: paged history, status, logs, cancel and retry (migration 0036).

An agent "task" is an ``AgentRun``: it exists because a chat turn planned, and runs inside that
request. So what a person can do about one is read it (status, logs, history), ask it to stop, and
link a later attempt to it. What these pin: the history pages exactly (no skips, no repeats, a
cursor is only good for the list it came from), a log carries no content and can be followed from
where the caller left off, a cancel stops *further* work and never anyone else's, and a retry is a
link the caller is entitled to make. The SQL is ``tests/load/test_agent_tasks_postgres.py``'s.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from aura_core.adapters.memory_stores import InMemoryEngineerRunStore
from aura_core.api import Principal, build_app
from aura_core.api.agent_runs import (
    STALE_RUN_S,
    decode_run_cursor,
    encode_run_cursor,
    run_list_fingerprint,
)
from aura_core.domain.agent_runs import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    run_metrics,
)
from aura_core.domain.errors import AuthenticationError, ValidationError
from aura_core.domain.identifiers import AgentRunId, AgentStepId, PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.execution.agentic_loop import AgenticExecutor, RunBudgetGate
from aura_core.execution.cancellation import RunCancellation
from aura_core.execution.engine import ExecutionOutcome
from aura_core.execution.run_recorder import AgentRunContext
from aura_core.llm.cache import InMemoryCache
from aura_core.kernel import TurnRequest

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
ALICE, BOB = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}


def _run(owner: str = "alice", *, minutes: int = 0, status: str = AgentRunStatus.FAILED, agent: str = "orchestrator",
         retry_of: str | None = None, run_id: str | None = None, updated: datetime | None = None) -> AgentRun:
    at = NOW + timedelta(minutes=minutes)
    return AgentRun(
        id=AgentRunId(run_id or str(uuid.uuid4())), user_id=UserId(owner), agent_id=agent, turn_id="t",
        correlation_id="c", status=status, created_at=at, updated_at=updated or at, retry_of=retry_of,
        finished_at=at if status in AgentRunStatus.TERMINAL else None,
    )


def _step(run: AgentRun, seq: int, **kw) -> AgentStep:
    return AgentStep(id=AgentStepId(str(uuid.uuid4())), run_id=run.id, user_id=run.user_id, seq=seq, iteration=1,
                     phase="execute", kind="tool_call", created_at=NOW, **kw)


# ------------------------------------------------------------------------------------- the domain


class TestStatuses:
    def test_a_cancelled_run_is_finished_and_retryable_a_completed_one_is_neither_retryable(self) -> None:
        assert AgentRunStatus.CANCELLED in AgentRunStatus.TERMINAL
        assert AgentRunStatus.RETRYABLE == {"partial", "failed", "budget_exhausted", "cancelled"}
        assert AgentRunStatus.COMPLETED not in AgentRunStatus.RETRYABLE
        assert AgentRunStatus.AWAITING_APPROVAL not in AgentRunStatus.RETRYABLE, "resumed by approval ids, not retried"
        assert AgentRunStatus.RUNNING not in AgentRunStatus.RETRYABLE and AgentRunStatus.RUNNING not in AgentRunStatus.TERMINAL

    def test_the_link_and_the_metrics_survive_a_cancelled_run(self) -> None:
        run = _run(status=AgentRunStatus.CANCELLED, retry_of="earlier")
        assert run.as_dict()["retry_of"] == "earlier" and run.is_terminal
        assert run_metrics(run) is not None


# ------------------------------------------------------------------------------- the in-memory store


class TestRunStore:
    async def _store(self) -> tuple[InMemoryEngineerRunStore, list[AgentRun]]:
        store = InMemoryEngineerRunStore()
        runs = [_run(minutes=i, status=s, agent=a) for i, (s, a) in enumerate([
            ("failed", "orchestrator"), ("completed", "task_planner"), ("partial", "orchestrator"),
            ("cancelled", "task_planner"), ("failed", "orchestrator")])]
        for r in runs:
            await store.save_run(r)
        await store.save_run(_run("bob", minutes=2))
        return store, runs

    async def test_filters_and_ordering(self) -> None:
        store, runs = await self._store()
        alice = UserId("alice")
        assert [r.id for r in await store.list_runs(alice)] == [r.id for r in reversed(runs)]
        assert {r.status for r in await store.list_runs(alice, status="failed")} == {"failed"}
        assert len(await store.list_runs(alice, agent_id="task_planner")) == 2
        since, until = NOW + timedelta(minutes=1), NOW + timedelta(minutes=4)
        assert [r.id for r in await store.list_runs(alice, since=since, until=until)] == [runs[3].id, runs[2].id, runs[1].id]
        assert all(r.user_id == alice for r in await store.list_runs(alice, limit=100)), "another owner's run listed"

    async def test_keyset_paging_walks_every_run_once_even_with_equal_creation_times(self) -> None:
        store = InMemoryEngineerRunStore()
        ids = []
        for i in range(7):
            run = _run(minutes=i // 2)  # pairs share a created_at: the id breaks the tie
            ids.append(run.id)
            await store.save_run(run)
        seen, before = [], None
        for _ in range(10):
            page = list(await store.list_runs(UserId("alice"), limit=3, before=before))
            seen += [r.id for r in page]
            if len(page) < 3:
                break
            before = (page[-1].created_at, page[-1].id.value)
        assert sorted(r.value for r in seen) == sorted(i.value for i in ids) and len(seen) == len(set(seen)) == 7

    async def test_retry_of_filter_and_steps_after_a_seq(self) -> None:
        store = InMemoryEngineerRunStore()
        first = _run(minutes=0)
        retry = _run(minutes=5, retry_of=first.id.value)
        for r in (first, retry):
            await store.save_run(r)
        assert [r.id for r in await store.list_runs(UserId("alice"), retry_of=first.id.value)] == [retry.id]
        for seq in range(5):
            await store.append_step(_step(first, seq))
        assert [s.seq for s in await store.list_steps(first.id, user_id=UserId("alice"))] == [0, 1, 2, 3, 4]
        assert [s.seq for s in await store.list_steps(first.id, user_id=UserId("alice"), after_seq=2)] == [3, 4]
        assert await store.list_steps(first.id, user_id=UserId("bob")) == [], "another owner's steps listed"


# --------------------------------------------------------------------------------------- the cursor


class TestCursor:
    FP = run_list_fingerprint(agent=None, status=None, since=None, until=None, retry_of=None)

    def test_round_trip_and_refusal_for_another_list(self) -> None:
        token = encode_run_cursor(self.FP, created_at=NOW, run_id="r1")
        assert decode_run_cursor(token, self.FP) == (NOW, "r1")
        for changed in (dict(status="failed"), dict(agent="task_planner"), dict(retry_of="x"),
                        dict(since=NOW), dict(until=NOW)):
            other = run_list_fingerprint(**{**dict(agent=None, status=None, since=None, until=None, retry_of=None), **changed})
            with pytest.raises(ValidationError):
                decode_run_cursor(token, other)

    @pytest.mark.parametrize("junk", ["", "not-a-cursor", "e30", "%%%", "eyJxIjoxfQ"])
    def test_junk_is_a_422_not_a_crash(self, junk) -> None:
        with pytest.raises(ValidationError):
            decode_run_cursor(junk, self.FP)


# ---------------------------------------------------------------------------------- cancel registry


class TestRunCancellation:
    async def test_a_request_reaches_a_watch_in_this_process_at_once(self) -> None:
        reg = RunCancellation(InMemoryCache(), poll_interval_s=60)  # the poll must not be what does it
        watch = reg.watch("alice", "run-1")
        assert reg.is_live("alice", "run-1") and watch.cancelled is False
        await reg.request("alice", "run-1")
        assert watch.cancelled is True
        await watch.close()
        assert not reg.is_live("alice", "run-1")

    async def test_a_request_made_on_another_worker_arrives_through_the_cache(self) -> None:
        cache = InMemoryCache()
        here, elsewhere = RunCancellation(cache, poll_interval_s=0.02), RunCancellation(cache)
        watch = here.watch("alice", "run-2")
        await elsewhere.request("alice", "run-2")  # a different process: only the cache links them
        for _ in range(100):
            if watch.cancelled:
                break
            await asyncio.sleep(0.02)
        assert watch.cancelled is True, "the poll never saw the cancel"
        await watch.close()

    async def test_a_cancel_asked_for_before_the_run_began_is_still_seen(self) -> None:
        cache = InMemoryCache()
        await RunCancellation(cache).request("alice", "run-3")
        watch = RunCancellation(cache, poll_interval_s=0.02).watch("alice", "run-3")
        for _ in range(100):
            if watch.cancelled:
                break
            await asyncio.sleep(0.02)
        assert watch.cancelled is True
        await watch.close()

    async def test_it_is_scoped_to_the_owner(self) -> None:
        cache = InMemoryCache()
        reg = RunCancellation(cache, poll_interval_s=0.02)
        watch = reg.watch("alice", "run-4")
        await reg.request("bob", "run-4")  # Bob naming Alice's run id
        await asyncio.sleep(0.15)
        assert watch.cancelled is False, "Bob cancelled Alice's run"
        await watch.close()

    async def test_a_failing_cache_neither_cancels_nor_crashes_the_run(self) -> None:
        class Broken:
            async def get(self, key):
                raise ConnectionError("down")

            async def set(self, key, value, *, ttl_s=None):
                raise ConnectionError("down")

        reg = RunCancellation(Broken(), poll_interval_s=0.01)
        watch = reg.watch("alice", "run-5")
        await asyncio.sleep(0.08)
        assert watch.cancelled is False
        await watch.close()
        await watch.close()  # idempotent

    async def test_without_a_cache_a_local_cancel_still_works(self) -> None:
        reg = RunCancellation(None)
        watch = reg.watch("alice", "run-6")
        await reg.request("alice", "run-6")
        assert watch.cancelled is True
        await watch.close()


# ------------------------------------------------------------------------- the gate and the loop


class TestCancelStopsFurtherWork:
    def test_the_gate_refuses_every_dispatch_once_cancelled(self) -> None:
        from aura_core.domain.agent_runs import AgentBudget

        gate = RunBudgetGate(AgentBudget())
        step = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="calculator", description="x", arguments={})
        assert gate.admit(step, None) is None, "control: admitted before the cancel"
        flag = {"cancelled": False}
        gate.cancelled = lambda: flag["cancelled"]
        assert gate.admit(step, None) is None
        flag["cancelled"] = True
        assert gate.admit(step, None) == "run_cancelled"
        assert gate.take_refusals()[0].reason == "run_cancelled"

    async def test_the_loop_stops_at_the_pass_boundary_and_records_a_cancelled_run(self) -> None:
        from tests.unit.test_agentic_loop import _FakePlanner, _plan

        reg = RunCancellation(InMemoryCache(), poll_interval_s=60)
        runs = InMemoryEngineerRunStore()
        calls = {"n": 0}

        class Executor:
            async def execute(self, plan, *, context, capabilities=(), confirmations=None, on_event=None):
                calls["n"] += 1
                for key in list(reg._live):  # the owner asks for the stop while pass one is running
                    _, user, run = key.split(":")
                    await reg.request(user, run)
                steps = tuple(s.failed("boom", 1.0) for s in plan.steps)
                return ExecutionOutcome(plan=plan.with_steps(steps), results={}, succeeded=False, blocked_reason=None)

        loop = AgenticExecutor(planner=_FakePlanner(max_replans=5), executor=Executor(), runs=runs, cancellation=reg)
        from aura_core.ports.runtime import ToolContext

        outcome = await loop.run(
            _plan(), context=ToolContext(user_id=UserId("alice"), correlation_id="c1", deadline_s=30.0), max_iterations=5,
            run_context=AgentRunContext(user_id=UserId("alice"), agent_id="orchestrator", turn_id="t",
                                        correlation_id="c", goal="do the thing"),
        )
        assert calls["n"] == 1, "the loop replanned after a cancel was requested"
        [run] = await runs.list_runs(UserId("alice"))
        assert run.status == AgentRunStatus.CANCELLED and run.stop_reason == "run_cancelled"
        assert run.is_terminal and run.finished_at is not None and outcome.run_id == run.id.value
        assert not reg.is_live("alice", run.id.value), "the watch was left open"

    async def test_a_run_nobody_cancels_is_unaffected_and_its_watch_is_closed(self) -> None:
        from tests.unit.test_agentic_loop import _FakePlanner, _plan, _ScriptedExecutor
        from aura_core.ports.runtime import ToolContext

        reg, runs = RunCancellation(InMemoryCache(), poll_interval_s=0.01), InMemoryEngineerRunStore()
        loop = AgenticExecutor(planner=_FakePlanner(), executor=_ScriptedExecutor([True]), runs=runs, cancellation=reg)
        await loop.run(_plan(), context=ToolContext(user_id=UserId("alice"), correlation_id="c1", deadline_s=30.0), max_iterations=2,
                       run_context=AgentRunContext(user_id=UserId("alice"), agent_id="orchestrator", turn_id="t",
                                                   correlation_id="c", goal="g"))
        [run] = await runs.list_runs(UserId("alice"))
        assert run.status == AgentRunStatus.COMPLETED and not reg._live

    async def test_a_loop_that_raises_still_closes_its_watch(self) -> None:
        from tests.unit.test_agentic_loop import _FakePlanner, _plan
        from aura_core.ports.runtime import ToolContext

        class Boom:
            async def execute(self, plan, *, context, **kw):
                raise RuntimeError("boom")

        reg, runs = RunCancellation(InMemoryCache(), poll_interval_s=0.01), InMemoryEngineerRunStore()
        loop = AgenticExecutor(planner=_FakePlanner(), executor=Boom(), runs=runs, cancellation=reg)
        with pytest.raises(RuntimeError):
            await loop.run(_plan(), context=ToolContext(user_id=UserId("alice"), correlation_id="c1", deadline_s=30.0), max_iterations=1,
                           run_context=AgentRunContext(user_id=UserId("alice"), agent_id="orchestrator", turn_id="t",
                                                       correlation_id="c", goal="g"))
        assert not reg._live, "a crashed run left its watch running"
        assert (await runs.list_runs(UserId("alice")))[0].status == AgentRunStatus.FAILED

    async def test_a_real_turn_whose_run_is_cancelled_executes_no_further_tool(self, container, monkeypatch) -> None:
        """Through the kernel and the real execution engine: the watch says cancelled from the start,
        so the gate refuses the planned calculator call and the run ends cancelled."""
        from tests.unit.test_kernel_degradation import _FixedPlanner, _tool_plan

        await container.startup()
        container.kernel._deps.planner = _FixedPlanner(_tool_plan("calculator", {"expression": "2+2"}))
        plain = await container.kernel.run(TurnRequest(message="What is 2+2?", user_id=UserId("test-user"), plan=True))
        assert any(t.name == "calculator" and t.ok for t in plain.telemetry.tools), "control: it runs normally"

        original = container.run_cancellation.watch

        def cancelled_from_the_start(user_id, run_id):
            watch = original(user_id, run_id)
            watch.cancelled = True
            return watch

        monkeypatch.setattr(container.run_cancellation, "watch", cancelled_from_the_start)
        result = await container.kernel.run(TurnRequest(message="What is 2+2?", user_id=UserId("test-user"), plan=True))
        assert not any(t.name == "calculator" and t.ok for t in result.telemetry.tools), "a cancelled run executed a tool"
        runs = await container.agent_runs.list_runs(UserId("test-user"))
        assert runs[0].status == AgentRunStatus.CANCELLED, [r.status for r in runs]


# --------------------------------------------------------------------------------------- over HTTP


def _app(container):
    async def authenticator(request):
        name = request.headers.get("authorization", "").removeprefix("Bearer ")
        if name not in ("alice", "bob"):
            raise AuthenticationError("unknown principal")
        return Principal(user_id=UserId(name), email=f"{name}@example.com", email_verified=True)

    return build_app(container, authenticator=authenticator)


@pytest.fixture
def client(container):
    with TestClient(_app(container)) as c:
        c.container = container
        yield c


def _seed(client, *runs, steps=()):
    store = client.container.agent_runs

    async def go():
        for r in runs:
            await store.save_run(r)
        for s in steps:
            await store.append_step(s)

    client.portal.call(go)


class TestHistory:
    def test_newest_first_filtered_and_paged_without_skips_or_repeats(self, client) -> None:
        runs = [_run(minutes=i, status="failed" if i % 2 else "completed",
                     agent="task_planner" if i % 3 == 0 else "orchestrator") for i in range(9)]
        _seed(client, *runs, _run("bob", minutes=1))
        ids = [r.id.value for r in reversed(runs)]

        whole = client.get("/api/v3/agents/runs?limit=100", headers=ALICE).json()
        assert [r["id"] for r in whole["runs"]] == ids and whole["next_cursor"] is None
        assert all("metrics" in r for r in whole["runs"]), "the existing response shape is kept"

        for query in ("", "status=failed", "agent=task_planner", f"since={(NOW + timedelta(minutes=3)).isoformat().replace('+', '%2B')}"):
            full = [r["id"] for r in client.get(f"/api/v3/agents/runs?limit=100&{query}", headers=ALICE).json()["runs"]]
            seen, cursor = [], None
            for _ in range(10):
                body = client.get(f"/api/v3/agents/runs?limit=2&{query}" + (f"&cursor={cursor}" if cursor else ""), headers=ALICE).json()
                seen += [r["id"] for r in body["runs"]]
                cursor = body["next_cursor"]
                if cursor is None:
                    break
            assert seen == full and len(seen) == len(set(seen)), f"paging disagrees with the unpaged list for {query!r}"

        assert client.get("/api/v3/agents/runs", headers=BOB).json()["runs"][0]["id"] != ids[0]
        assert len(client.get("/api/v3/agents/runs", headers=BOB).json()["runs"]) == 1, "another person's runs listed"

    def test_a_cursor_is_only_good_for_the_list_it_came_from(self, client) -> None:
        _seed(client, *[_run(minutes=i) for i in range(5)])
        cursor = client.get("/api/v3/agents/runs?limit=2", headers=ALICE).json()["next_cursor"]
        assert cursor
        for other in ("status=failed", "agent=task_planner", "retry_of=x"):
            assert client.get(f"/api/v3/agents/runs?limit=2&{other}&cursor={cursor}", headers=ALICE).status_code == 422
        assert client.get("/api/v3/agents/runs?cursor=junk", headers=ALICE).status_code == 422

    def test_the_retries_of_a_run_are_listed_and_linked(self, client) -> None:
        first = _run(minutes=0)
        retry = _run(minutes=5, retry_of=first.id.value)
        _seed(client, first, retry, _run(minutes=9))
        body = client.get(f"/api/v3/agents/runs?retry_of={first.id.value}", headers=ALICE).json()
        assert [r["id"] for r in body["runs"]] == [retry.id.value] and body["runs"][0]["retry_of"] == first.id.value


class TestStatusAndLogs:
    def test_status_is_small_and_says_whether_to_keep_polling(self, client) -> None:
        live, done = _run(minutes=0, status="running"), _run(minutes=1, status="cancelled")
        _seed(client, live, done)
        body = client.get(f"/api/v3/agents/runs/{live.id.value}/status", headers=ALICE).json()
        assert body["status"] == "running" and body["terminal"] is False and body["finished_at"] is None
        assert client.get(f"/api/v3/agents/runs/{done.id.value}/status", headers=ALICE).json()["terminal"] is True
        assert "steps" not in body and "goal_digest" not in body

    def test_logs_follow_a_run_from_where_the_caller_left_off(self, client) -> None:
        run = _run(minutes=0, status="running")
        _seed(client, run, steps=[_step(run, i, tool="calculator", status="ok", arguments_digest="abc123") for i in range(5)])
        first = client.get(f"/api/v3/agents/runs/{run.id.value}/logs?limit=2", headers=ALICE).json()
        assert [s["seq"] for s in first["logs"]] == [0, 1] and first["next_after_seq"] == 1 and first["terminal"] is False
        second = client.get(f"/api/v3/agents/runs/{run.id.value}/logs?limit=2&after_seq=1", headers=ALICE).json()
        assert [s["seq"] for s in second["logs"]] == [2, 3] and second["next_after_seq"] == 3
        last = client.get(f"/api/v3/agents/runs/{run.id.value}/logs?after_seq=3", headers=ALICE).json()
        assert [s["seq"] for s in last["logs"]] == [4] and last["next_after_seq"] is None
        assert client.get(f"/api/v3/agents/runs/{run.id.value}/logs?after_seq=4", headers=ALICE).json()["logs"] == []
        assert client.get(f"/api/v3/agents/runs/{run.id.value}/logs?limit=0", headers=ALICE).status_code == 422

    def test_a_log_line_carries_no_content_only_structure(self, client) -> None:
        run = _run(minutes=0)
        _seed(client, run, steps=[_step(run, 0, tool="calculator", status="ok", arguments_digest="abc123")])
        line = client.get(f"/api/v3/agents/runs/{run.id.value}/logs", headers=ALICE).json()["logs"][0]
        assert set(line) <= {"id", "run_id", "seq", "iteration", "phase", "kind", "status", "tool", "call_id",
                             "error_kind", "arguments_digest", "risk_cost", "detail", "created_at"}, set(line)
        assert not {"message", "prompt", "content", "arguments", "text", "response"} & set(line)

    def test_another_persons_run_reads_as_missing_on_every_route(self, client) -> None:
        mine = _run(minutes=0, status="running")
        _seed(client, mine, steps=[_step(mine, 0)])
        missing = "00000000-0000-4000-8000-000000000000"
        for suffix in ("status", "logs"):
            theirs = client.get(f"/api/v3/agents/runs/{mine.id.value}/{suffix}", headers=BOB)
            gone = client.get(f"/api/v3/agents/runs/{missing}/{suffix}", headers=BOB)
            assert theirs.status_code == gone.status_code == 404 and theirs.json()["error"] == gone.json()["error"]
        assert client.get("/api/v3/agents/runs/not%20an%20id/status", headers=ALICE).status_code == 404

    def test_a_signed_in_caller_is_required(self, client) -> None:
        run = _run(minutes=0)
        _seed(client, run)
        for method, path in (("GET", f"/api/v3/agents/runs/{run.id.value}/status"), ("GET", f"/api/v3/agents/runs/{run.id.value}/logs"),
                             ("POST", f"/api/v3/agents/runs/{run.id.value}/cancel")):
            assert client.request(method, path).status_code == 401, (method, path)


class TestCancelOverHttp:
    def test_a_live_run_is_asked_to_stop_and_the_request_reaches_every_worker(self, client) -> None:
        run = _run(minutes=0, status="running", updated=datetime.now(timezone.utc))
        _seed(client, run)
        response = client.post(f"/api/v3/agents/runs/{run.id.value}/cancel", headers=ALICE)
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "cancel_requested" and response.json()["effective"] == "at the next step boundary"
        flag = client.portal.call(lambda: client.container.cache_backend.get(f"agent_run_cancel:alice:{run.id.value}"))
        assert flag, "the request was not left where another worker can see it"
        assert client.post(f"/api/v3/agents/runs/{run.id.value}/cancel", headers=ALICE).status_code == 202, "asking twice is fine"

    def test_a_finished_run_cannot_be_cancelled(self, client) -> None:
        for status in ("completed", "failed", "cancelled", "partial", "budget_exhausted", "awaiting_approval"):
            run = _run(minutes=0, status=status)
            _seed(client, run)
            refused = client.post(f"/api/v3/agents/runs/{run.id.value}/cancel", headers=ALICE)
            assert refused.status_code == 409, status
            assert refused.json()["details"] == {"reason": "already_finished", "status": status}

    def test_a_run_whose_worker_died_is_closed_instead_of_waiting_forever(self, client) -> None:
        # Fixed ages, not STALE_RUN_S arithmetic: a test that derived "old" from the constant would
        # pass whatever the constant said.
        old = datetime.now(timezone.utc) - timedelta(minutes=30)
        run = _run(minutes=0, status="running", updated=old)
        _seed(client, run)
        closed = client.post(f"/api/v3/agents/runs/{run.id.value}/cancel", headers=ALICE)
        assert closed.status_code == 200 and closed.json() == {"id": run.id.value, "status": "cancelled", "orphaned": True}
        after = client.get(f"/api/v3/agents/runs/{run.id.value}/status", headers=ALICE).json()
        assert after["status"] == "cancelled" and after["terminal"] is True and after["stop_reason"] == "orphaned"

    def test_a_run_updated_minutes_ago_is_not_mistaken_for_an_orphan(self, client) -> None:
        """A live run is rewritten on every step; five quiet minutes is a slow step, not a dead worker."""
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        run = _run(minutes=0, status="running", updated=recent)
        _seed(client, run)
        response = client.post(f"/api/v3/agents/runs/{run.id.value}/cancel", headers=ALICE)
        assert response.status_code == 202 and response.json()["status"] == "cancel_requested"
        assert client.get(f"/api/v3/agents/runs/{run.id.value}/status", headers=ALICE).json()["status"] == "running"

    def test_the_orphan_threshold_sits_between_a_slow_step_and_a_dead_worker(self) -> None:
        assert timedelta(minutes=5).total_seconds() < STALE_RUN_S < timedelta(minutes=30).total_seconds()

    def test_bob_cannot_cancel_alices_run_and_leaves_no_flag(self, client) -> None:
        run = _run(minutes=0, status="running", updated=datetime.now(timezone.utc))
        _seed(client, run)
        missing = client.post("/api/v3/agents/runs/00000000-0000-4000-8000-000000000000/cancel", headers=BOB)
        theirs = client.post(f"/api/v3/agents/runs/{run.id.value}/cancel", headers=BOB)
        assert theirs.status_code == missing.status_code == 404 and theirs.json()["error"] == missing.json()["error"]
        for owner in ("alice", "bob"):
            assert not client.portal.call(
                lambda o=owner: client.container.cache_backend.get(f"agent_run_cancel:{o}:{run.id.value}")
            ), f"a cancel flag was left for {owner}"
        assert client.get(f"/api/v3/agents/runs/{run.id.value}/status", headers=ALICE).json()["status"] == "running"


class TestRetry:
    def _plan_a_tool(self, container):
        from tests.unit.test_kernel_degradation import _FixedPlanner, _tool_plan

        container.kernel._deps.planner = _FixedPlanner(_tool_plan("calculator", {"expression": "2+2"}))

    def test_a_retry_is_recorded_on_the_new_run_and_listed_under_the_old_one(self, client, container) -> None:
        self._plan_a_tool(container)
        failed = _run(minutes=-10, status="failed")
        _seed(client, failed)
        reply = client.post("/api/v3/chat", headers=ALICE, json={"message": "What is 2+2?", "plan": True, "retry_of": failed.id.value})
        assert reply.status_code == 200, reply.text
        retries = client.get(f"/api/v3/agents/runs?retry_of={failed.id.value}", headers=ALICE).json()["runs"]
        assert len(retries) == 1 and retries[0]["retry_of"] == failed.id.value and retries[0]["id"] == reply.json()["agent_run_id"]
        assert client.get(f"/api/v3/agents/runs/{reply.json()['agent_run_id']}", headers=ALICE).json()["retry_of"] == failed.id.value

    def test_only_a_run_that_ended_short_can_be_retried(self, client, container) -> None:
        self._plan_a_tool(container)
        for status in ("partial", "failed", "budget_exhausted", "cancelled"):
            run = _run(minutes=-5, status=status)
            _seed(client, run)
            assert client.post("/api/v3/chat", headers=ALICE, json={"message": "hi", "plan": True, "retry_of": run.id.value}).status_code == 200, status
        for status in ("completed", "running", "awaiting_approval"):
            run = _run(minutes=-5, status=status)
            _seed(client, run)
            refused = client.post("/api/v3/chat", headers=ALICE, json={"message": "hi", "plan": True, "retry_of": run.id.value})
            assert refused.status_code == 409 and refused.json()["details"]["reason"] == "not_retryable", status

    def test_another_persons_run_and_a_missing_one_both_read_as_missing(self, client, container) -> None:
        self._plan_a_tool(container)
        theirs = _run("bob", minutes=-5, status="failed")
        _seed(client, theirs)
        echo = container.router.get("echo")
        calls = len(echo.calls)
        a = client.post("/api/v3/chat", headers=ALICE, json={"message": "hi", "plan": True, "retry_of": theirs.id.value})
        b = client.post("/api/v3/chat", headers=ALICE, json={"message": "hi", "plan": True, "retry_of": "00000000-0000-4000-8000-000000000000"})
        c = client.post("/api/v3/chat", headers=ALICE, json={"message": "hi", "plan": True, "retry_of": "not an id!"})
        assert a.status_code == b.status_code == c.status_code == 404
        assert a.json()["error"] == b.json()["error"] == "not_found"
        assert len(echo.calls) == calls, "a model was called for a retry that should have been refused"
        assert client.get(f"/api/v3/agents/runs?retry_of={theirs.id.value}", headers=ALICE).json()["runs"] == []

    def test_a_turn_without_retry_of_is_unchanged(self, client, container) -> None:
        self._plan_a_tool(container)
        reply = client.post("/api/v3/chat", headers=ALICE, json={"message": "What is 2+2?", "plan": True})
        assert reply.status_code == 200
        assert client.get(f"/api/v3/agents/runs/{reply.json()['agent_run_id']}", headers=ALICE).json()["retry_of"] is None
