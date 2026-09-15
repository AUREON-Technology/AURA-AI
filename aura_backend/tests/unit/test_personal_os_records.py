"""Server-side personal-OS CRUD tools (Phase C2): real writes to a real store, not a client-applied
proposal, kept off the zero-knowledge encrypted legacy tables."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from aura_core.adapters import InMemoryPersonalOsStore
from aura_core.domain import ToolCall, ToolCallId
from aura_core.domain.identifiers import PersonalOsRecordId, UserId
from aura_core.domain.personal_os import PersonalOsRecord, RecordKind
from aura_core.kernel import TurnRequest
from aura_core.ports.runtime import ToolContext
from aura_core.tools import ToolPolicy, ToolRegistry
from aura_core.tools.records import build_personal_os_crud_tools


def _request(message: str = "hello", **overrides):
    base = dict(message=message, user_id=UserId("test-user"))
    base.update(overrides)
    return TurnRequest(**base)


@pytest.fixture
def store() -> InMemoryPersonalOsStore:
    return InMemoryPersonalOsStore()


@pytest.fixture
def registry(store) -> ToolRegistry:
    reg = ToolRegistry(policy=ToolPolicy())
    reg.register_all(build_personal_os_crud_tools(store))
    return reg


class TestPersonalOsStore:
    async def test_put_then_get(self, store: InMemoryPersonalOsStore) -> None:
        owner = UserId("u1")
        now = datetime.now(timezone.utc)
        record = PersonalOsRecord(
            id=PersonalOsRecordId.new(), owner_id=owner, kind=RecordKind.TASK,
            fields={"title": "Buy milk"}, created_at=now, updated_at=now,
        )
        await store.put(record)
        fetched = await store.get(record.id, owner_id=owner)
        assert fetched is not None and fetched.fields["title"] == "Buy milk"

    async def test_get_is_owner_scoped(self, store: InMemoryPersonalOsStore) -> None:
        now = datetime.now(timezone.utc)
        record = PersonalOsRecord(
            id=PersonalOsRecordId.new(), owner_id=UserId("owner"), kind=RecordKind.TASK,
            fields={"title": "secret"}, created_at=now, updated_at=now,
        )
        await store.put(record)
        assert await store.get(record.id, owner_id=UserId("someone-else")) is None

    async def test_list_for_owner_filters_by_kind(self, store: InMemoryPersonalOsStore) -> None:
        owner = UserId("u1")
        now = datetime.now(timezone.utc)
        await store.put(PersonalOsRecord(
            id=PersonalOsRecordId.new(), owner_id=owner, kind=RecordKind.TASK,
            fields={"title": "t"}, created_at=now, updated_at=now,
        ))
        await store.put(PersonalOsRecord(
            id=PersonalOsRecordId.new(), owner_id=owner, kind=RecordKind.HEALTH_RECORD,
            fields={"weight_kg": 70}, created_at=now, updated_at=now,
        ))
        tasks = await store.list_for_owner(owner, kind=RecordKind.TASK)
        assert len(tasks) == 1 and tasks[0].kind == RecordKind.TASK

    async def test_delete_is_owner_scoped(self, store: InMemoryPersonalOsStore) -> None:
        now = datetime.now(timezone.utc)
        record = PersonalOsRecord(
            id=PersonalOsRecordId.new(), owner_id=UserId("owner"), kind=RecordKind.TASK,
            fields={}, created_at=now, updated_at=now,
        )
        await store.put(record)
        assert await store.delete(record.id, owner_id=UserId("not-owner")) is False
        assert await store.delete(record.id, owner_id=UserId("owner")) is True
        assert await store.get(record.id, owner_id=UserId("owner")) is None

    async def test_update_persists_a_merged_record(self, store: InMemoryPersonalOsStore) -> None:
        owner = UserId("u1")
        now = datetime.now(timezone.utc)
        record = PersonalOsRecord(
            id=PersonalOsRecordId.new(), owner_id=owner, kind=RecordKind.TASK,
            fields={"title": "Buy milk", "priority": "low"}, created_at=now, updated_at=now,
        )
        await store.put(record)
        merged = record.with_fields({"priority": "high"}, updated_at=now)
        await store.update(merged)
        fetched = await store.get(record.id, owner_id=owner)
        assert fetched.fields == {"title": "Buy milk", "priority": "high"}


class TestPersonalOsCrudTools:
    async def test_create_task_persists_and_list_tasks_reads_it_back(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        user = UserId("u1")
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        created, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="create_task",
                     arguments={"title": "Buy milk", "priority": "low"}),
            context,
        )
        assert created.ok and report.allowed

        listed, _ = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="list_tasks", arguments={}), context,
        )
        assert listed.ok and "Buy milk" in listed.content

    async def test_create_writes_a_real_record_a_second_reader_can_see(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        """Not a client-applied proposal: the record exists in the store itself."""
        user = UserId("u1")
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        await registry.execute(
            ToolCall(id=ToolCallId("1"), name="create_finance_record",
                     arguments={"type": "expense", "amount": 42.5, "category": "groceries"}),
            context,
        )
        records = await store.list_for_owner(user, kind=RecordKind.FINANCE_RECORD)
        assert len(records) == 1
        assert records[0].fields["amount"] == 42.5 and records[0].fields["type"] == "expense"

    async def test_records_are_owner_isolated(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        alice = ToolContext(user_id=UserId("alice"), correlation_id="c1", deadline_s=5)
        bob = ToolContext(user_id=UserId("bob"), correlation_id="c2", deadline_s=5)
        await registry.execute(
            ToolCall(id=ToolCallId("1"), name="create_task", arguments={"title": "Alice's task"}),
            alice,
        )
        listed, _ = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="list_tasks", arguments={}), bob,
        )
        assert "Alice's task" not in listed.content

    async def test_created_records_are_tagged_with_the_acting_agent(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        user = UserId("u1")
        context = ToolContext(
            user_id=user, correlation_id="c", deadline_s=5, agent_id="task_planner",
            allowed_tools=frozenset({"create_task", "list_tasks"}),
        )
        await registry.execute(
            ToolCall(id=ToolCallId("1"), name="create_task", arguments={"title": "Plan the trip"}),
            context,
        )
        records = await store.list_for_owner(user, kind=RecordKind.TASK)
        assert records[0].source == "agent:task_planner"

    async def test_create_health_record_outside_health_companions_grant_is_denied(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        """The Phase C1 exit criterion, exercised against a real Phase C2 tool: an out-of-grant
        call fails closed rather than silently succeeding because the tool itself exists."""
        context = ToolContext(
            user_id=UserId("u1"), correlation_id="c", deadline_s=5,
            agent_id="finance_advisor", allowed_tools=frozenset({"create_finance_record"}),
        )
        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="create_health_record",
                     arguments={"weight_kg": 70}),
            context,
        )
        assert not result.ok and report.policy_reason == "outside_agent_grant"
        assert await store.list_for_owner(UserId("u1"), kind=RecordKind.HEALTH_RECORD) == []


class TestPersonalOsUpdateAndDeleteTools:
    """Phase C3: the update/delete half of Phase C2's exit criterion."""

    async def _create(self, registry: ToolRegistry, context: ToolContext, **arguments: Any) -> str:
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("create"), name="create_task", arguments=arguments), context,
        )
        assert result.ok
        return result.content.split()[-1].rstrip(".")

    async def test_update_changes_only_the_supplied_fields(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        user = UserId("u1")
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        record_id = await self._create(registry, context, title="Buy milk", priority="low")

        result, report = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="update_task",
                     arguments={"record_id": record_id, "priority": "high"}),
            context,
        )
        assert result.ok and report.allowed and "Updated task" in result.content

        record = await store.get(PersonalOsRecordId(record_id), owner_id=user)
        assert record.fields == {"title": "Buy milk", "priority": "high"}

    async def test_update_with_no_fields_is_a_reported_no_op(
        self, registry: ToolRegistry
    ) -> None:
        user = UserId("u1")
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        record_id = await self._create(registry, context, title="Buy milk")

        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="update_task", arguments={"record_id": record_id}),
            context,
        )
        assert result.ok and "Nothing to update" in result.content

    async def test_update_an_unknown_id_reports_not_found_rather_than_raising(
        self, registry: ToolRegistry
    ) -> None:
        context = ToolContext(user_id=UserId("u1"), correlation_id="c", deadline_s=5)
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="update_task",
                     arguments={"record_id": "does-not-exist", "priority": "high"}),
            context,
        )
        assert result.ok and "No task found" in result.content

    async def test_update_cannot_reach_a_record_of_a_different_kind(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        """``PersonalOsStore.get``/``update`` are scoped by id and owner only, not kind -- without
        the tool layer's own check, ``update_task`` given a calendar event's id would merge
        task-shaped fields into it."""
        user = UserId("u1")
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        event_result, _ = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="create_calendar_event",
                     arguments={"title": "Standup", "start_time": "2026-01-01T09:00:00",
                                "end_time": "2026-01-01T09:15:00"}),
            context,
        )
        event_id = event_result.content.split()[-1].rstrip(".")

        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="update_task",
                     arguments={"record_id": event_id, "priority": "high"}),
            context,
        )
        assert result.ok and "No task found" in result.content

        event = await store.get(PersonalOsRecordId(event_id), owner_id=user)
        assert "priority" not in event.fields
        assert event.fields["title"] == "Standup"

    async def test_update_is_owner_scoped(self, registry: ToolRegistry) -> None:
        owner = ToolContext(user_id=UserId("owner"), correlation_id="c1", deadline_s=5)
        record_id = await self._create(registry, owner, title="Owner's task")

        other = ToolContext(user_id=UserId("someone-else"), correlation_id="c2", deadline_s=5)
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="update_task",
                     arguments={"record_id": record_id, "priority": "high"}),
            other,
        )
        assert result.ok and "No task found" in result.content

    async def test_delete_removes_the_record(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        user = UserId("u1")
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        record_id = await self._create(registry, context, title="Buy milk")

        result, report = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="delete_task", arguments={"record_id": record_id}),
            context,
        )
        assert result.ok and report.allowed and "Deleted task" in result.content
        assert await store.get(PersonalOsRecordId(record_id), owner_id=user) is None

    async def test_delete_is_idempotent_rather_than_raising_on_a_second_call(
        self, registry: ToolRegistry
    ) -> None:
        context = ToolContext(user_id=UserId("u1"), correlation_id="c", deadline_s=5)
        record_id = await self._create(registry, context, title="Buy milk")

        first, _ = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="delete_task", arguments={"record_id": record_id}),
            context,
        )
        second, _ = await registry.execute(
            ToolCall(id=ToolCallId("3"), name="delete_task", arguments={"record_id": record_id}),
            context,
        )
        assert "Deleted task" in first.content
        assert "No task found" in second.content

    async def test_delete_cannot_reach_a_record_of_a_different_kind(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        user = UserId("u1")
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        event_result, _ = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="create_calendar_event",
                     arguments={"title": "Standup", "start_time": "2026-01-01T09:00:00",
                                "end_time": "2026-01-01T09:15:00"}),
            context,
        )
        event_id = event_result.content.split()[-1].rstrip(".")

        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="delete_task", arguments={"record_id": event_id}),
            context,
        )
        assert result.ok and "No task found" in result.content
        assert await store.get(PersonalOsRecordId(event_id), owner_id=user) is not None

    async def test_delete_is_owner_scoped(self, registry: ToolRegistry, store: InMemoryPersonalOsStore) -> None:
        owner = ToolContext(user_id=UserId("owner"), correlation_id="c1", deadline_s=5)
        record_id = await self._create(registry, owner, title="Owner's task")

        other = ToolContext(user_id=UserId("someone-else"), correlation_id="c2", deadline_s=5)
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="delete_task", arguments={"record_id": record_id}),
            other,
        )
        assert result.ok and "No task found" in result.content
        assert await store.get(PersonalOsRecordId(record_id), owner_id=UserId("owner")) is not None

    async def test_update_task_outside_task_planners_grant_is_denied(
        self, registry: ToolRegistry, store: InMemoryPersonalOsStore
    ) -> None:
        context = ToolContext(user_id=UserId("u1"), correlation_id="c", deadline_s=5)
        record_id = await self._create(registry, context, title="Buy milk")

        gated = ToolContext(
            user_id=UserId("u1"), correlation_id="c", deadline_s=5,
            agent_id="finance_advisor", allowed_tools=frozenset({"create_finance_record"}),
        )
        result, report = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="update_task",
                     arguments={"record_id": record_id, "priority": "high"}),
            gated,
        )
        assert not result.ok and report.policy_reason == "outside_agent_grant"
        record = await store.get(PersonalOsRecordId(record_id), owner_id=UserId("u1"))
        assert record.fields["priority"] != "high"


class TestKernelWiring:
    async def test_default_agent_registry_grants_name_real_tools(self, container) -> None:
        """The registry's ``allowed_tools`` are just strings -- this fails if a persona's grant
        and ``records.py``'s actual tool names ever drift apart."""
        await container.startup()
        registered = {s.name for s in container.tools.available()}
        # fetch_url is deliberately named in research_agent's grant even when not registered --
        # it needs AURA_HTTP_ALLOWED_HOSTS, which this default container leaves unset. The
        # engineer agent's sandbox/git tools are absent from a capability-less `available()` for
        # a different reason: `required_capability="engineer_agent"` gates them regardless of
        # registration (`ToolRegistry.available`) -- asserted for real below, since registration
        # itself no longer depends on Docker/`sandbox.enabled` at all (see that assertion). The
        # GitHub tools are the original story: they need AURA_GITHUB_ENABLED plus a token,
        # neither of which this default container configures, so they are genuinely unregistered.
        conditionally_registered = {
            "fetch_url",
            "write_file", "edit_file", "run_command", "clone_repository",
            "git_diff", "git_commit", "git_checkout_branch", "git_status", "git_push",
            # Persistent workspaces (ADR-013) additionally need a workspace manager, which this
            # default container has no sandbox runner to build one from.
            "open_workspace", "list_workspaces", "destroy_workspace",
            "github_list_repository_files", "github_get_file_contents", "github_get_issue",
            "github_get_pull_request", "github_create_branch", "github_commit_files",
            "github_create_draft_pull_request",
        }
        # The sandbox/git tools (minus `clone_repository`, which additionally needs a GitHub
        # resolver this default container does not configure) are unconditionally *registered*
        # now: `sandbox_runner` (container.py::_build_sandbox_runner) is always a
        # `CompositeSandboxRunner`, never `None`, because a user's paired desktop app can serve
        # them with no server-side Docker sandbox configured at all. `create_task` is what fails
        # at call time if neither a desktop session nor `sandbox.enabled` is actually usable.
        engineer_sandbox_tools = {
            "write_file", "edit_file", "run_command",
            "git_diff", "git_commit", "git_checkout_branch",
        }
        registered_for_engineer = {
            s.name for s in container.tools.available(capabilities=("engineer_agent",))
        }
        assert engineer_sandbox_tools <= registered_for_engineer
        for spec in container.agents:
            if spec.allowed_tools is None:
                continue
            missing = spec.allowed_tools - registered - conditionally_registered
            assert not missing, f"{spec.id} grants unregistered tools: {missing}"
        assert "create_finance_record" in container.agents.get("finance_advisor").allowed_tools
        assert "create_finance_record" in registered

    async def test_finance_advisor_can_create_a_real_record_through_the_kernels_own_context(
        self, container, user
    ) -> None:
        await container.startup()
        context = container.kernel._tool_context(
            _request(agent="finance_advisor", user_id=user), tainted=False
        )
        result, report = await container.tools.execute(
            ToolCall(id=ToolCallId("1"), name="create_finance_record",
                     arguments={"type": "income", "amount": 100}),
            context,
        )
        assert result.ok and report.allowed
        records = await container.personal_os_store.list_for_owner(
            user, kind=RecordKind.FINANCE_RECORD
        )
        assert len(records) == 1 and records[0].fields["amount"] == 100
