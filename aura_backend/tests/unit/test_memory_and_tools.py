"""Memory lifecycle (ADR-004) and tool dispatch safety (SEC-07)."""

from __future__ import annotations

import pytest

from aura_core.domain import (
    MemoryKind,
    MemoryQuery,
    ToolCall,
    ToolCallId,
    UserId,
)
from aura_core.domain.errors import MemoryStoreError, ValidationError
from aura_core.domain.memory import MEMORY_POLICIES, MemoryId, MemoryRecord, Provenance
from aura_core.domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
from aura_core.ports.runtime import ToolContext
from aura_core.tools import ToolPolicy, ToolRegistry, param, tool, validate_arguments


class TestMemoryPolicies:
    def test_semantic_memory_weights_similarity_over_recency(self) -> None:
        """A fact from a year ago is still a fact."""
        policy = MEMORY_POLICIES[MemoryKind.SEMANTIC]
        assert policy.w_similarity > policy.w_recency * 3

    def test_episodic_memory_weights_recency_heavily(self) -> None:
        """What was said five minutes ago matters more than last month."""
        policy = MEMORY_POLICIES[MemoryKind.EPISODIC]
        assert policy.w_recency > MEMORY_POLICIES[MemoryKind.SEMANTIC].w_recency * 3

    def test_procedural_memory_is_driven_by_reinforcement(self) -> None:
        """A preference is established by repetition, not by a single mention."""
        policy = MEMORY_POLICIES[MemoryKind.PROCEDURAL]
        assert policy.w_reinforcement > policy.w_recency

    def test_recency_decays_with_the_configured_half_life(self) -> None:
        from datetime import timedelta

        policy = MEMORY_POLICIES[MemoryKind.SEMANTIC]
        assert policy.recency_factor(timedelta(0)) == 1.0
        assert policy.recency_factor(policy.half_life) == pytest.approx(0.5)


class TestMemoryEngine:
    async def test_write_and_recall(self, memory, user) -> None:
        await memory.remember(
            owner_id=user, content="The user works in the Cairo office.",
            subject="location", source="turn:1",
        )
        hits = await memory.recall(MemoryQuery(owner_id=user, text="where does the user work"))
        assert hits and "Cairo" in hits[0].record.content

    async def test_restating_a_fact_reinforces_rather_than_duplicates(self, memory, user) -> None:
        text = "The user prefers concise answers."
        first = await memory.remember(owner_id=user, content=text, source="turn:1")
        second = await memory.remember(owner_id=user, content=text, source="turn:2")

        assert second.id == first.id
        assert second.access_count > first.access_count
        assert second.confidence > first.confidence

    async def test_provenance_enables_targeted_forgetting(self, memory, user) -> None:
        """"Forget everything you learned from document X" -- impossible without provenance."""
        await memory.remember(owner_id=user, content="Fact from the handbook.", source="doc:handbook")
        await memory.remember(owner_id=user, content="Fact the user stated.", source="turn:9")

        removed = await memory.forget_source(owner_id=user, source="doc:handbook")
        assert removed == 1
        remaining = await memory.recall(MemoryQuery(owner_id=user, text="fact", min_score=0.0))
        assert all("handbook" not in h.record.provenance.source for h in remaining)

    async def test_tenant_isolation(self, memory, user) -> None:
        await memory.remember(owner_id=user, content="Private detail about this user.", source="t")
        assert not await memory.recall(
            MemoryQuery(owner_id=UserId("other"), text="private detail", min_score=0.0)
        )

    async def test_empty_content_is_rejected(self, memory, user) -> None:
        with pytest.raises(ValidationError):
            await memory.remember(owner_id=user, content="   ", source="t")

    async def test_quota_is_enforced(self, vectors, embedder, user) -> None:
        """Unbounded growth makes recall slow and consolidation expensive; failing loudly beats
        silently evicting a user's memories."""
        from aura_core.adapters import InMemoryMemoryStore
        from aura_core.memory import MemoryEngine

        engine = MemoryEngine(
            store=InMemoryMemoryStore(), vectors=vectors, embedder=embedder,
            index_alias="quota_test", max_records_per_owner=2,
        )
        await engine.remember(owner_id=user, content="First distinct fact here.", source="t")
        await engine.remember(owner_id=user, content="Second unrelated statement.", source="t")
        with pytest.raises(MemoryStoreError):
            await engine.remember(owner_id=user, content="Third completely different note.", source="t")

    async def test_consolidation_supersedes_rather_than_deletes(self, memory, user) -> None:
        """The user may be correcting the *newer* fact; the older one stays auditable."""
        await memory.remember(
            owner_id=user, content="The user lives in Cairo.", subject="home", source="t1"
        )
        await memory.remember(
            owner_id=user, content="The user lives in Berlin.", subject="home", source="t2"
        )
        outcome = await memory.consolidate(user, kinds=[MemoryKind.SEMANTIC])
        assert outcome.superseded >= 1

    def test_retrieval_score_blends_three_signals(self) -> None:
        record = MemoryRecord(
            id=MemoryId.new(), kind=MemoryKind.SEMANTIC, owner_id=UserId("u"),
            content="fact", provenance=Provenance(source="t"), confidence=1.0,
        )
        fresh = record.retrieval_score(0.8)
        used = record.touched().touched().touched().retrieval_score(0.8)
        assert used > fresh, "reinforcement did not raise the score"


class TestArgumentValidation:
    @pytest.fixture
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="book",
            description="Book a room.",
            parameters=(
                ParameterSpec("room", "string", "Room name", max_length=40),
                ParameterSpec("hours", "integer", "Duration", minimum=1, maximum=8),
                ParameterSpec("recurring", "boolean", "Repeat weekly", required=False),
                ParameterSpec("guests", "array", "Attendees", required=False, item_type="string"),
            ),
        )

    def test_unknown_parameters_are_rejected(self, spec: ToolSpec) -> None:
        """``services/agent.py:282`` splats model output into a callable, so a hallucinated key
        raises TypeError inside a stream."""
        with pytest.raises(ValidationError) as excinfo:
            validate_arguments(spec, {"room": "A", "hours": 2, "hallucinated": True})
        assert "hallucinated" in str(excinfo.value)

    def test_missing_required_parameters_are_reported_by_name(self, spec: ToolSpec) -> None:
        with pytest.raises(ValidationError) as excinfo:
            validate_arguments(spec, {"room": "A"})
        assert "hours" in str(excinfo.value)

    def test_numeric_strings_are_coerced(self, spec: ToolSpec) -> None:
        """Models quote numbers routinely; refusing wastes a generation for no safety benefit."""
        assert validate_arguments(spec, {"room": "A", "hours": "3"})["hours"] == 3

    def test_booleans_are_not_accepted_as_numbers(self, spec: ToolSpec) -> None:
        """``bool`` subclasses ``int`` in Python; True must not silently become 1."""
        with pytest.raises(ValidationError):
            validate_arguments(spec, {"room": "A", "hours": True})

    def test_range_constraints_are_enforced(self, spec: ToolSpec) -> None:
        with pytest.raises(ValidationError):
            validate_arguments(spec, {"room": "A", "hours": 99})

    def test_strings_are_never_parsed_into_structures(self, spec: ToolSpec) -> None:
        """Coercion widens what is accepted; it must never reinterpret shape."""
        with pytest.raises(ValidationError):
            validate_arguments(spec, {"room": "A", "hours": 1, "guests": '["a","b"]'})

    def test_unparsed_provider_output_is_surfaced_correctably(self, spec: ToolSpec) -> None:
        with pytest.raises(ValidationError) as excinfo:
            validate_arguments(spec, {"_unparsed": "{not json"})
        assert "valid JSON" in str(excinfo.value)


class TestToolDispatch:
    async def test_calculator_is_not_an_eval(self, tools, user) -> None:
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        result, _ = await tools.execute(
            ToolCall(id=ToolCallId("1"), name="calculator",
                     arguments={"expression": "__import__('os').system('id')"}),
            context,
        )
        assert not result.ok

    async def test_calculator_bounds_resource_use(self, tools, user) -> None:
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        result, _ = await tools.execute(
            ToolCall(id=ToolCallId("1"), name="calculator", arguments={"expression": "2**999999"}),
            context,
        )
        assert not result.ok

    async def test_calculator_computes(self, tools, user) -> None:
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        result, _ = await tools.execute(
            ToolCall(id=ToolCallId("1"), name="calculator",
                     arguments={"expression": "(1250 * 0.15) + 30"}),
            context,
        )
        assert result.ok and "217.5" in result.content

    async def test_unknown_tools_return_a_correctable_message(self, tools, user) -> None:
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)
        result, report = await tools.execute(
            ToolCall(id=ToolCallId("1"), name="definitely_not_a_tool", arguments={}), context
        )
        assert not result.ok and not report.allowed
        assert "Available tools" in result.content

    async def test_capability_grants_fail_closed(self, user) -> None:
        @tool(
            "read_email", "Read the user's email.",
            param("folder", "string", "Mailbox folder"),
            sensitivity=ToolSensitivity.CONFIDENTIAL,
            required_capability="email.read",
        )
        async def read_email(folder: str) -> str:
            return "messages"

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(read_email)
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5)

        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="read_email", arguments={"folder": "inbox"}),
            context,
            capabilities=(),
        )
        assert not result.ok and "missing_capability" in report.policy_reason

        granted, _ = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="read_email", arguments={"folder": "inbox"}),
            context,
            capabilities=("email.read",),
        )
        assert granted.ok

    async def test_tools_the_caller_cannot_use_are_not_advertised(self, user) -> None:
        """Advertising an unusable tool wastes a generation and teaches the model a capability
        it does not have."""
        @tool("privileged", "Do a privileged thing.", required_capability="admin.act")
        async def privileged() -> str:
            return "done"

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(privileged)
        assert "privileged" not in [s.name for s in registry.available(capabilities=())]
        assert "privileged" in [s.name for s in registry.available(capabilities=("admin.act",))]

    async def test_timeouts_are_enforced(self, user) -> None:
        import asyncio

        @tool("slow", "Sleep forever.", timeout_s=0.05)
        async def slow() -> str:
            await asyncio.sleep(5)
            return "never"

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(slow)
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="slow", arguments={}),
            ToolContext(user_id=user, correlation_id="c", deadline_s=5),
        )
        assert not result.ok and result.error_kind == "tool_timeout"

    async def test_implementation_bugs_do_not_end_the_turn(self, user) -> None:
        @tool("buggy", "Raise unexpectedly.")
        async def buggy() -> str:
            raise RuntimeError("boom")

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(buggy)
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="buggy", arguments={}),
            ToolContext(user_id=user, correlation_id="c", deadline_s=5),
        )
        assert not result.ok
        assert result.error_kind == "tool_execution_error"
        assert "buggy" in result.content
        # The handler's own message reaches the model so it can react -- not just the exception
        # type name, which erased every actionable detail (e.g. a SandboxError's "no desktop app
        # is connected... pair it") before it ever got there. The stack trace still does not.
        assert "boom" in result.content
        assert "Traceback" not in result.content

    async def test_results_are_truncated_and_marked(self, user) -> None:
        @tool("verbose", "Return a lot of text.", max_result_chars=100)
        async def verbose() -> str:
            return "x" * 5000

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(verbose)
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="verbose", arguments={}),
            ToolContext(user_id=user, correlation_id="c", deadline_s=5),
        )
        assert result.ok and "truncated" in result.content

    async def test_every_attempt_is_audited_including_refusals(self, user, audit) -> None:
        registry = ToolRegistry(policy=ToolPolicy(), audit=audit)
        await registry.execute(
            ToolCall(id=ToolCallId("1"), name="nonexistent", arguments={}),
            ToolContext(user_id=user, correlation_id="c", deadline_s=5),
        )
        events = await audit.query(action="tool.")
        assert events and events[0].outcome == "denied"

    async def test_audit_records_a_digest_not_the_arguments(self, tools, user, audit) -> None:
        """Arguments can contain the user's private data; an audit log is long-lived."""
        await tools.execute(
            ToolCall(id=ToolCallId("1"), name="calculator",
                     arguments={"expression": "1+1"}),
            ToolContext(user_id=user, correlation_id="c", deadline_s=5),
        )
        events = await audit.query(action="tool.calculator")
        assert events
        assert "arguments_digest" in events[0].detail
        assert "1+1" not in str(events[0].detail)


class TestToolSpecValidation:
    def test_side_effecting_confidential_tools_force_confirmation(self) -> None:
        """A tool that changes the world and cannot be confirmed is a footgun; the combination
        is resolved at declaration time rather than discovered in production."""
        spec = ToolSpec(
            name="wire_transfer", description="Move money.",
            sensitivity=ToolSensitivity.CONFIDENTIAL, side_effecting=True,
        )
        assert spec.requires_confirmation

    def test_tool_names_must_be_provider_safe(self) -> None:
        with pytest.raises(ValueError):
            ToolSpec(name="Send Email!", description="d")

    def test_parameters_must_be_described(self) -> None:
        """The model selects tools on descriptions; an undescribed parameter is unusable."""
        with pytest.raises(ValueError):
            ParameterSpec("x", "string", "")

    def test_duplicate_parameter_names_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            ToolSpec(
                name="t", description="d",
                parameters=(
                    ParameterSpec("a", "string", "first"),
                    ParameterSpec("a", "string", "second"),
                ),
            )
