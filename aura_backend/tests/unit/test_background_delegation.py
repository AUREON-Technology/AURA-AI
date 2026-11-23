"""Background delegation (aura_core/tools/delegation.py + agents/tasks.py, ADR-038, row 57).

The contract: ``delegate_to_agent(..., background=true)`` returns an ``agent_task_id`` at once and
runs the *same* child turn -- same depth check, same capability check, same grant intersection, same
inherited taint, same refusal of nested confirmation -- as an asyncio task; ``get_agent_task`` /
``list_agent_tasks`` / ``cancel_agent_task`` follow it, scoped to the caller.

Tool-level tests use a fake kernel whose ``run`` can be held open, so what is asserted is the
tool's own behaviour; the last class goes through the real container, registry, policy and a real
recursive ``AIKernel.run``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from types import SimpleNamespace

import pytest

from aura_core.agents.handoff import HandoffStore
from aura_core.agents.registry import build_default_agent_registry
from aura_core.agents.tasks import AgentTaskManager
from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain import ToolCall, ToolCallId
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.llm.cache import InMemoryCache
from aura_core.llm.providers.echo import ScriptedTurn
from aura_core.memory.checkpoint import SessionCheckpoint
from aura_core.ports.runtime import ToolContext
from aura_core.tools.delegation import (
    DELEGATION_HANDOFF_TOOL_NAMES,
    DELEGATION_TASK_TOOL_NAMES,
    build_delegation_tools,
)

ALICE, BOB = UserId("alice-bg"), UserId("bob-bg")


def _context(**overrides) -> ToolContext:
    base = dict(user_id=ALICE, correlation_id="c1", deadline_s=30.0, agent_id="orchestrator",
                capabilities=(), delegation_depth=0, session_id="parent-session")
    base.update(overrides)
    return ToolContext(**base)


def _ok(text: str = "the findings") -> SimpleNamespace:
    return SimpleNamespace(text=text, needs_confirmation=(), pending_tool_calls=(), tool_calls=("fetch_url",),
                           plan_summary=({"description": "fetched the page", "status": "succeeded"},))


class _HeldKernel:
    """``run`` blocks until released, and records every child request it was given."""

    def __init__(self, result=None) -> None:
        self.result = result or _ok()
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.requests: list = []
        self.cancelled = False

    async def run(self, request):
        self.requests.append(request)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result


class _InstantKernel(_HeldKernel):
    async def run(self, request):
        self.requests.append(request)
        return self.result


@pytest.fixture
def cache() -> InMemoryCache:
    return InMemoryCache(max_entries=1024, default_ttl_s=3600)


def _build(kernel, cache, *, background: bool = True, structured: bool = False, **manager):
    manager_obj = AgentTaskManager(cache, **manager) if background else None
    store = HandoffStore(cache) if structured else None

    async def provider(owner, session_id):
        return SessionCheckpoint(decisions=("Use Alembic",), blockers=("Staging is down",), source="extractive")

    tools = build_delegation_tools(
        agents=build_default_agent_registry(), kernel=lambda: kernel, tasks=manager_obj, handoffs=store,
        checkpoint_provider=provider, structured_handoff=structured,
    )
    return {t.spec.name: t for t in tools}, manager_obj, store


async def _delegate(tools, *, background: bool, context=None, target="research_agent", subtask="look into X"):
    arguments = {"target_agent_id": target, "subtask": subtask}
    if background:
        arguments["background"] = True
    return await tools["delegate_to_agent"].execute(arguments, context or _context())


class TestSurface:
    def test_off_the_tools_and_the_parameter_do_not_exist(self, cache) -> None:
        tools, manager, _ = _build(_InstantKernel(), cache, background=False)
        assert manager is None and set(tools) == {"delegate_to_agent"}
        assert [p.name for p in tools["delegate_to_agent"].spec.parameters] == ["target_agent_id", "subtask"]

    async def test_off_a_background_call_is_refused_before_anything_runs(self, cache) -> None:
        kernel = _InstantKernel()
        tools, _, _ = _build(kernel, cache, background=False)
        with pytest.raises(ToolExecutionError, match="not enabled"):
            await _delegate(tools, background=True)
        assert kernel.requests == []

    def test_on_the_three_task_tools_and_the_parameter_exist(self, cache) -> None:
        tools, _, _ = _build(_InstantKernel(), cache)
        assert set(DELEGATION_TASK_TOOL_NAMES) <= set(tools)
        assert not set(DELEGATION_HANDOFF_TOOL_NAMES) & set(tools), "handoff tools are a separate switch"
        names = [p.name for p in tools["delegate_to_agent"].spec.parameters]
        assert names == ["target_agent_id", "subtask", "background"]
        assert tools["delegate_to_agent"].spec.parameters[-1].required is False

    def test_tags_and_side_effects(self, cache) -> None:
        tools, _, _ = _build(_InstantKernel(), cache)
        assert "untrusted_content" in tools["get_agent_task"].spec.tags
        assert "untrusted_content" not in tools["list_agent_tasks"].spec.tags
        assert tools["cancel_agent_task"].spec.side_effecting and not tools["get_agent_task"].spec.side_effecting


class TestBackgroundRun:
    async def test_returns_an_id_at_once_and_the_result_arrives_later(self, cache) -> None:
        kernel = _HeldKernel(_ok("the findings"))
        tools, manager, _ = _build(kernel, cache)

        started = await json_of(_delegate(tools, background=True))
        assert started["agent_task_id"].startswith("at_") and started["status"] == "running"
        assert started["agent"] == "research_agent"

        await kernel.started.wait()
        running = await json_of(tools["get_agent_task"].execute({"agent_task_id": started["agent_task_id"]}, _context()))
        assert running["status"] == "running" and "result" not in running

        kernel.release.set()
        await manager.drain()
        done = await json_of(tools["get_agent_task"].execute({"agent_task_id": started["agent_task_id"]}, _context()))
        assert done["status"] == "succeeded" and done["result"] == "the findings"

    async def test_the_child_request_is_identical_to_the_synchronous_one(self, cache) -> None:
        """Every rule of the sync path applies because it is the same request: compare them field
        by field for a tainted, narrowed caller."""
        sync_kernel, bg_kernel = _InstantKernel(), _InstantKernel()
        caller = dict(tainted=True, allowed_tools=frozenset({"fetch_url", "search_knowledge", "propose_health_log"}),
                      capabilities=("project_data",))
        sync_tools, _, _ = _build(sync_kernel, cache, background=False)
        await _delegate(sync_tools, background=False, context=_context(**caller))

        bg_tools, manager, _ = _build(bg_kernel, cache)
        await _delegate(bg_tools, background=True, context=_context(**caller))
        await manager.drain()

        a, b = sync_kernel.requests[0], bg_kernel.requests[0]
        ignored = {"correlation_id"}  # fresh per call by design
        for field in dataclasses.fields(a):
            if field.name not in ignored:
                assert getattr(a, field.name) == getattr(b, field.name), field.name
        assert b.inherited_taint is True and b.delegation_depth == 1 and "propose_health_log" not in b.delegated_tool_grant

    async def test_the_depth_ceiling_target_and_capability_checks_run_at_submit(self, cache) -> None:
        kernel = _InstantKernel()
        tools, manager, _ = _build(kernel, cache, background=True)
        with pytest.raises(ToolExecutionError, match="depth exceeded"):
            await _delegate(tools, background=True, context=_context(delegation_depth=1))
        with pytest.raises(ToolExecutionError, match="unknown agent"):
            await _delegate(tools, background=True, target="no_such_agent")
        with pytest.raises(ToolExecutionError, match="missing required capability"):
            await _delegate(tools, background=True, target="engineer", context=_context(capabilities=()))
        assert kernel.requests == [] and manager.running_here == 0
        assert await manager.list(ALICE.value) == [], "a refused delegation queues nothing"

    async def test_a_child_that_needs_confirmation_becomes_a_failed_task_not_a_dropped_prompt(self, cache) -> None:
        kernel = _InstantKernel(SimpleNamespace(text="", needs_confirmation=("propose_expense",), pending_tool_calls=(),
                                                tool_calls=(), plan_summary=()))
        tools, manager, _ = _build(kernel, cache)
        started = await json_of(_delegate(tools, background=True, target="finance_advisor"))
        await manager.drain()
        view = await json_of(tools["get_agent_task"].execute({"agent_task_id": started["agent_task_id"]}, _context()))
        assert view["status"] == "failed" and "nested confirmation" in view["error"]

    async def test_a_child_turn_that_errored_is_a_failed_task(self, cache) -> None:
        failed = SimpleNamespace(
            text="", needs_confirmation=(), pending_tool_calls=(), tool_calls=(), plan_summary=(),
            telemetry=SimpleNamespace(outcome=SimpleNamespace(value="error"), error_kind="all_providers_failed"),
        )
        tools, manager, _ = _build(_InstantKernel(failed), cache)
        started = await json_of(_delegate(tools, background=True))
        await manager.drain()
        view = await json_of(tools["get_agent_task"].execute({"agent_task_id": started["agent_task_id"]}, _context()))
        assert view["status"] == "failed" and "all_providers_failed" in view["error"]

    async def test_a_kernel_exception_is_a_failed_task(self, cache) -> None:
        class Broken:
            async def run(self, request):
                raise RuntimeError("provider down")

        tools, manager, _ = _build(Broken(), cache)
        started = await json_of(_delegate(tools, background=True))
        await manager.drain()
        view = await json_of(tools["get_agent_task"].execute({"agent_task_id": started["agent_task_id"]}, _context()))
        assert view["status"] == "failed" and "RuntimeError" in view["error"]

    async def test_the_per_user_cap_is_a_clean_tool_error(self, cache) -> None:
        kernel = _HeldKernel()
        tools, manager, _ = _build(kernel, cache, per_user=1)
        await _delegate(tools, background=True)
        with pytest.raises(ToolExecutionError, match="limit 1"):
            await _delegate(tools, background=True)
        kernel.release.set()
        await manager.drain()


class TestFollowingAndStopping:
    async def test_list_shows_status_without_results_and_only_the_callers_tasks(self, cache) -> None:
        kernel = _InstantKernel(_ok("secret findings"))
        tools, manager, _ = _build(kernel, cache)
        await _delegate(tools, background=True)
        await manager.drain()

        mine = await json_of(tools["list_agent_tasks"].execute({}, _context()))
        assert [t["status"] for t in mine["tasks"]] == ["succeeded"]
        assert "secret findings" not in json.dumps(mine), "a listing is not the place for results"
        theirs = await json_of(tools["list_agent_tasks"].execute({}, _context(user_id=BOB)))
        assert theirs == {"tasks": []}

    async def test_another_users_task_cannot_be_read_or_cancelled(self, cache) -> None:
        kernel = _HeldKernel()
        tools, manager, _ = _build(kernel, cache)
        started = await json_of(_delegate(tools, background=True))
        await kernel.started.wait()
        arguments = {"agent_task_id": started["agent_task_id"]}
        with pytest.raises(ToolExecutionError, match="no agent task"):
            await tools["get_agent_task"].execute(arguments, _context(user_id=BOB))
        with pytest.raises(ToolExecutionError, match="no agent task"):
            await tools["cancel_agent_task"].execute(arguments, _context(user_id=BOB))
        assert (await manager.get(ALICE.value, started["agent_task_id"])).status == "running"
        kernel.release.set()
        await manager.drain()

    async def test_cancel_stops_the_child_turn_itself(self, cache) -> None:
        kernel = _HeldKernel()
        tools, manager, _ = _build(kernel, cache)
        started = await json_of(_delegate(tools, background=True))
        await kernel.started.wait()

        cancelled = await json_of(tools["cancel_agent_task"].execute({"agent_task_id": started["agent_task_id"]}, _context()))
        assert cancelled["status"] == "cancelled"
        assert kernel.cancelled, "the kernel.run awaiting inside the task was actually cancelled"
        assert manager.running_here == 0

    async def test_the_handoff_outcome_is_attached_when_structured_handoff_is_on(self, cache) -> None:
        kernel = _InstantKernel(_ok("the findings"))
        tools, manager, store = _build(kernel, cache, structured=True)
        started = await json_of(_delegate(tools, background=True))
        assert started["handoff_id"].startswith("ho_")
        await manager.drain()
        view = await json_of(tools["get_agent_task"].execute({"agent_task_id": started["agent_task_id"]}, _context()))
        assert view["handoff_outcome"]["status"] == "succeeded"
        assert view["handoff_outcome"]["completed"] == ["fetched the page"]
        (entry,) = kernel.requests[0].handoff_context
        assert "Staging is down" in entry.content()
        record = await store.get(ALICE.value, started["handoff_id"])
        assert record.to_agent == "research_agent"

    async def test_the_background_call_does_not_wait_for_the_parents_checkpoint(self, cache) -> None:
        """Found by running the real path: with a model-written checkpoint the handoff record can
        take seconds, and building it before returning made ``background=true`` block on it."""
        gate = asyncio.Event()

        async def slow_checkpoint(owner, session_id):
            await gate.wait()
            return SessionCheckpoint(decisions=("Use Alembic",), source="model")

        kernel = _InstantKernel()
        manager = AgentTaskManager(cache)
        store = HandoffStore(cache)
        tools = {t.spec.name: t for t in build_delegation_tools(
            agents=build_default_agent_registry(), kernel=lambda: kernel, tasks=manager, handoffs=store,
            checkpoint_provider=slow_checkpoint, structured_handoff=True,
        )}
        started = await asyncio.wait_for(json_of(_delegate(tools, background=True)), timeout=1.0)
        assert started["handoff_id"].startswith("ho_") and kernel.requests == [], "returned before the child ran"
        assert await store.get(ALICE.value, started["handoff_id"]) is None, "the record is built in the task"

        gate.set()
        await manager.drain()
        record = await store.get(ALICE.value, started["handoff_id"])
        assert record is not None and record.decisions == ("Use Alembic",) and record.checkpoint_source == "model"
        assert "Use Alembic" in kernel.requests[0].handoff_context[0].content()
        assert (await store.outcome(ALICE.value, started["handoff_id"])).status == "succeeded"

    async def test_a_cancelled_task_records_a_cancelled_handoff_outcome(self, cache) -> None:
        kernel = _HeldKernel()
        tools, manager, store = _build(kernel, cache, structured=True)
        started = await json_of(_delegate(tools, background=True))
        await kernel.started.wait()
        await tools["cancel_agent_task"].execute({"agent_task_id": started["agent_task_id"]}, _context())
        assert (await store.outcome(ALICE.value, started["handoff_id"])).status == "cancelled"


async def json_of(awaitable) -> dict:
    return json.loads(await awaitable)


# -- through the real container -----------------------------------------------------------------


def _container(**reasoning) -> Container:
    base = load_settings(refresh=True, validate=False)
    return Container(dataclasses.replace(
        base, reasoning=dataclasses.replace(base.reasoning, background_delegation_enabled=True, **reasoning),
    ))


async def _call(container, name: str, arguments: dict, context: ToolContext):
    return await container.tools.execute(
        ToolCall(id=ToolCallId("t"), name=name, arguments=arguments), context,
    )


class TestThroughTheRealContainer:
    async def test_background_delegation_through_registry_policy_and_a_real_kernel(self) -> None:
        container = _container()
        await container.startup()
        echo = container.providers[0]
        echo.queue(ScriptedTurn(text="Spending is flat this quarter.", delay_ms=250))

        context = _context(allowed_tools=None)
        loop = asyncio.get_running_loop()
        began = loop.time()
        result, report = await _call(container, "delegate_to_agent", {
            "target_agent_id": "finance_advisor", "subtask": "Summarise my spending", "background": True,
        }, context)
        elapsed = loop.time() - began
        assert result.ok and report.allowed, result.content
        assert elapsed < 0.2, f"returned after {elapsed:.2f}s: it waited for the sub-turn"
        started = json.loads(result.content)

        mid, _ = await _call(container, "get_agent_task", {"agent_task_id": started["agent_task_id"]}, context)
        assert json.loads(mid.content)["status"] == "running"

        await container.agent_tasks.drain()
        done, _ = await _call(container, "get_agent_task", {"agent_task_id": started["agent_task_id"]}, context)
        view = json.loads(done.content)
        assert view["status"] == "succeeded" and "Spending is flat" in view["result"]

        listed, _ = await _call(container, "list_agent_tasks", {}, context)
        assert json.loads(listed.content)["tasks"][0]["agent_task_id"] == started["agent_task_id"]

    async def test_reading_a_result_taints_the_turn_like_a_synchronous_delegation(self) -> None:
        container = _container()
        await container.startup()
        started, _ = await _call(container, "delegate_to_agent", {
            "target_agent_id": "finance_advisor", "subtask": "What is 2+2?", "background": True},
            _context(allowed_tools=None))
        await container.agent_tasks.drain()
        task_id = json.loads(started.content)["agent_task_id"]

        listing, reading = _context(allowed_tools=None), _context(allowed_tools=None)
        await _call(container, "list_agent_tasks", {}, listing)
        await _call(container, "get_agent_task", {"agent_task_id": task_id}, reading)
        assert listing.tainted is False, "a status listing carries no child text"
        assert reading.tainted is True, "the child's answer is untrusted content, as with the sync tool"

    async def test_cancelling_in_a_quarantined_turn_waits_for_confirmation(self) -> None:
        container = _container()
        await container.startup()
        echo = container.providers[0]
        echo.queue(ScriptedTurn(text="late", delay_ms=2000))
        context = _context(allowed_tools=None)
        started, _ = await _call(container, "delegate_to_agent", {
            "target_agent_id": "finance_advisor", "subtask": "What is 2+2?", "background": True}, context)
        task_id = json.loads(started.content)["agent_task_id"]

        held, report = await _call(container, "cancel_agent_task", {"agent_task_id": task_id},
                                   _context(allowed_tools=None, tainted=True))
        assert not held.ok and report.needs_confirmation
        assert (await container.agent_tasks.get(ALICE.value, task_id)).status == "running"

        cancelled, _ = await _call(container, "cancel_agent_task", {"agent_task_id": task_id},
                                   _context(allowed_tools=None))
        assert json.loads(cancelled.content)["status"] == "cancelled"

    async def test_shutdown_stops_running_tasks(self) -> None:
        container = _container()
        await container.startup()
        container.providers[0].queue(ScriptedTurn(text="never", delay_ms=5000))
        started, _ = await _call(container, "delegate_to_agent", {
            "target_agent_id": "finance_advisor", "subtask": "slow", "background": True},
            _context(allowed_tools=None))
        task_id = json.loads(started.content)["agent_task_id"]
        await asyncio.sleep(0.05)
        await container.shutdown()
        assert container.agent_tasks.running_here == 0
        assert (await container.agent_tasks.get(ALICE.value, task_id)).status == "cancelled"

    async def test_the_default_container_has_no_task_machinery(self) -> None:
        container = Container(load_settings(refresh=True, validate=False))
        assert container.agent_tasks is None
        for name in DELEGATION_TASK_TOOL_NAMES:
            with pytest.raises(Exception):
                container.tools.spec(name)
        arguments = {"target_agent_id": "finance_advisor", "subtask": "x", "background": True}
        result, _ = await _call(container, "delegate_to_agent", arguments, _context(allowed_tools=None))
        assert not result.ok, "an argument the spec does not declare is refused by schema validation"
