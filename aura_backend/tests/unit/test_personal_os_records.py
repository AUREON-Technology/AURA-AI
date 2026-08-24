"""Server-side personal-OS CRUD tools (Phase C2): real writes to a real store, not a client-applied
proposal, kept off the zero-knowledge encrypted legacy tables."""

from __future__ import annotations

from datetime import datetime, timezone

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


class TestKernelWiring:
    async def test_default_agent_registry_grants_name_real_tools(self, container) -> None:
        """The registry's ``allowed_tools`` are just strings -- this fails if a persona's grant
        and ``records.py``'s actual tool names ever drift apart."""
        await container.startup()
        registered = {s.name for s in container.tools.available()}
        # fetch_url is deliberately named in research_agent's grant even when not registered --
        # it needs AURA_HTTP_ALLOWED_HOSTS, which this default container leaves unset.
        conditionally_registered = {"fetch_url"}
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
