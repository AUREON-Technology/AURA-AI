"""Data-sensitivity routing (ADR-030 Phase 4).

The property under test: content may reach a provider only if that provider's ceiling permits
its classification. A hosted open-weight provider defaults to INTERNAL, and anything drawn from
what the user stored or connected (documents, memory, goals, tool output, Engineer workspaces,
caller evidence, a session that was sensitive before) is SENSITIVE and stays local. With nothing
local left, the turn fails instead of sending it.

Everything runs on echo providers and the in-memory container. "Hosted" here is an echo provider
whose capabilities say ``third_party=True`` with an INTERNAL ceiling, which is exactly what the
registry builds for a hosted base URL (asserted separately in ``TestConfiguration``).
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.adapters.runtime import InMemoryAuditSink
from aura_core.config.external_inference import HOSTED_OPEN_WEIGHT_HOSTS
from aura_core.config.settings import ProviderConfig
from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.errors import AllProvidersFailedError, ConfigurationError
from aura_core.domain.identifiers import DocumentId, SessionId, ToolCallId, UserId
from aura_core.domain.llm import DataClassification, RoutingPolicy, SamplingParams
from aura_core.domain.messages import ToolCall
from aura_core.domain.tools import ToolSpec
from aura_core.kernel import TurnRequest
from aura_core.llm import ProviderRouter, capabilities_from_config
from aura_core.llm.providers.echo import EchoProvider
from aura_core.llm.sensitivity import (
    current_data_scope,
    data_scope,
    effective_classification,
    escalate,
)
from aura_core.ports.runtime import ToolContext
from aura_core.tools.base import FunctionTool
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

#: Built from the registry rather than written out: the fitness test that keeps hosted endpoints
#: out of Python source would otherwise (correctly) flag this file.
_HOSTED_URL = f"https://{HOSTED_OPEN_WEIGHT_HOSTS[0]}/openai/v1"

PUBLIC = DataClassification.PUBLIC
INTERNAL = DataClassification.INTERNAL
SENSITIVE = DataClassification.SENSITIVE


def _request(classification: DataClassification | None = None) -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("hello there")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=64),
        data_classification=classification,
    )


def _hosted() -> EchoProvider:
    return EchoProvider(
        name="hosted",
        capabilities=ProviderCapabilities(
            model_id="openai/gpt-oss-120b", context_window=8192, max_output_tokens=512,
            supports_tools=True, quality_tier=3,
            max_data_classification=INTERNAL, third_party=True,
        ),
    )


def _local() -> EchoProvider:
    return EchoProvider(
        name="local",
        capabilities=ProviderCapabilities(
            model_id="qwen2.5:3b", context_window=8192, max_output_tokens=512,
            supports_tools=True, quality_tier=1, is_local=True,
        ),
    )


def _router(*providers, audit=None) -> ProviderRouter:
    return ProviderRouter(list(providers), default_policy=RoutingPolicy.QUALITY_FIRST, audit=audit)


# -- the classification scope ----------------------------------------------------------


class TestClassificationScope:
    def test_an_unscoped_unclassified_generation_is_sensitive(self) -> None:
        """Fail closed: background work that forgot to classify stays local."""
        assert effective_classification(_request()) is SENSITIVE

    def test_an_unscoped_request_may_declare_itself(self) -> None:
        assert effective_classification(_request(PUBLIC)) is PUBLIC

    def test_a_request_can_raise_but_never_lower_the_turn(self) -> None:
        with data_scope(INTERNAL):
            assert effective_classification(_request()) is INTERNAL
            assert effective_classification(_request(PUBLIC)) is INTERNAL
            assert effective_classification(_request(SENSITIVE)) is SENSITIVE

    def test_escalation_is_monotonic(self) -> None:
        with data_scope(INTERNAL) as scope:
            escalate(SENSITIVE, "documents")
            escalate(INTERNAL, "noise")
            assert scope.level is SENSITIVE
            assert "documents" in scope.reasons

    def test_escalating_without_a_scope_is_harmless(self) -> None:
        escalate(SENSITIVE, "tool_output")
        assert current_data_scope() is None

    def test_a_nested_scope_inherits_and_reports_back(self) -> None:
        """A delegated sub-turn cannot launder its parent's content, nor hide its own."""
        with data_scope(SENSITIVE, "memory") as parent:
            with data_scope(INTERNAL) as child:
                assert child.level is SENSITIVE
        assert parent.level is SENSITIVE

        with data_scope(INTERNAL) as parent:
            with data_scope(INTERNAL):
                escalate(SENSITIVE, "tool_output")
            assert parent.level is SENSITIVE
            assert "tool_output" in parent.reasons

    async def test_escalation_reaches_concurrent_child_tasks(self) -> None:
        """Tasks copy the context, and with it the same scope object."""
        with data_scope(INTERNAL) as scope:
            seen: list[DataClassification] = []

            async def worker() -> None:
                await asyncio.sleep(0)
                seen.append(effective_classification(_request()))

            task = asyncio.ensure_future(worker())
            escalate(SENSITIVE, "documents")
            await task
            assert seen == [SENSITIVE]
            assert scope.level is SENSITIVE

    def test_permits_is_an_ordering(self) -> None:
        assert INTERNAL.permits(PUBLIC) and INTERNAL.permits(INTERNAL)
        assert not INTERNAL.permits(SENSITIVE)
        assert SENSITIVE.permits(SENSITIVE)


# -- configuration ---------------------------------------------------------------------


class TestConfiguration:
    def test_a_hosted_provider_defaults_to_internal_and_is_third_party(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
        config = ProviderConfig(
            name="groq", kind="openai_compatible", model="openai/gpt-oss-120b",
            base_url=_HOSTED_URL,
        )
        caps = capabilities_from_config(config)
        assert caps.third_party is True
        assert caps.max_data_classification is INTERNAL

    def test_a_self_hosted_provider_accepts_everything(self) -> None:
        config = ProviderConfig(
            name="local", kind="openai_compatible", model="m", base_url="http://ollama:11434/v1"
        )
        caps = capabilities_from_config(config)
        assert caps.third_party is False
        assert caps.max_data_classification is SENSITIVE

    def test_rented_compute_is_not_third_party(self, monkeypatch) -> None:
        """Your weights on a Space is a different consent (ADR-030 §1); not restricted here."""
        monkeypatch.setenv("AURA_ALLOW_REMOTE_INFERENCE", "true")
        config = ProviderConfig(
            name="space", kind="openai_compatible", model="m",
            base_url="https://me-aura.hf.space/v1",
        )
        assert capabilities_from_config(config).third_party is False

    def test_the_ceiling_can_be_declared(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
        monkeypatch.setenv("AURA_PROVIDER_GROQ_BASE_URL", _HOSTED_URL)
        monkeypatch.setenv("AURA_PROVIDER_GROQ_MODEL", "openai/gpt-oss-120b")
        monkeypatch.setenv("AURA_PROVIDER_GROQ_MAX_DATA_CLASSIFICATION", "PUBLIC")
        config = ProviderConfig.from_env("groq")
        assert capabilities_from_config(config).max_data_classification is PUBLIC

    def test_an_unknown_ceiling_is_refused(self) -> None:
        with pytest.raises(ConfigurationError):
            ProviderConfig(name="p", kind="echo", model="m", max_data_classification="secret")


# -- routing ---------------------------------------------------------------------------


class TestRouting:
    async def test_internal_content_may_use_the_hosted_provider(self) -> None:
        hosted, local = _hosted(), _local()
        router = _router(hosted, local)
        with data_scope(INTERNAL):
            response = await router.complete(_request())
        assert response.provider == "hosted"

    async def test_sensitive_content_stays_local(self) -> None:
        hosted, local = _hosted(), _local()
        router = _router(hosted, local)
        with data_scope(INTERNAL):
            escalate(SENSITIVE, "documents")
            decision = router.plan(_request())
            response = await router.complete(_request())
        assert decision.excluded == {"hosted": "data_classification"}
        assert decision.data_classification == "sensitive"
        assert response.provider == "local"
        assert hosted.call_count == 0

    async def test_unscoped_generations_stay_local(self) -> None:
        hosted, local = _hosted(), _local()
        router = _router(hosted, local)
        assert (await router.complete(_request())).provider == "local"
        assert hosted.call_count == 0

    async def test_with_nothing_local_the_turn_fails_rather_than_sends(self) -> None:
        hosted = _hosted()
        router = _router(hosted)
        with data_scope(SENSITIVE, "memory"), pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(_request())
        assert {"provider": "hosted", "reason": "data_classification"} in excinfo.value.attempts
        assert hosted.call_count == 0

    async def test_streaming_obeys_the_same_rule(self) -> None:
        hosted, local = _hosted(), _local()
        router = _router(hosted, local)
        with data_scope(SENSITIVE, "goals"):
            chunks = [c async for c in router.stream(_request())]
        assert {c.provider for c in chunks} == {"local"}
        assert hosted.call_count == 0

    def test_restriction_is_detectable_and_absent_when_self_hosted(self) -> None:
        assert _router(_hosted(), _local()).restricts_data_classification is True
        assert _router(_local()).restricts_data_classification is False

    def test_stats_expose_the_ceiling(self) -> None:
        stats = {s["provider"]: s for s in _router(_hosted(), _local()).stats()}
        assert stats["hosted"]["max_data_classification"] == "internal"
        assert stats["hosted"]["third_party"] is True
        assert stats["local"]["max_data_classification"] == "sensitive"


class TestAudit:
    async def test_every_third_party_generation_is_audited_without_content(self) -> None:
        audit = InMemoryAuditSink()
        router = _router(_hosted(), _local(), audit=audit)
        with data_scope(INTERNAL):
            await router.complete(_request())
            _ = [c async for c in router.stream(_request())]

        events = await audit.query(action="llm.third_party_generation")
        assert len(events) == 2
        for event in events:
            assert event.resource == "hosted"
            assert event.detail["data_classification"] == "internal"
            assert event.detail["model"] == "openai/gpt-oss-120b"
            assert event.detail["prompt_tokens"] is not None
            assert "hello there" not in repr(event.detail)
        assert {e.detail["streamed"] for e in events} == {True, False}

    async def test_self_hosted_generations_are_not_audited(self) -> None:
        audit = InMemoryAuditSink()
        router = _router(_local(), audit=audit)
        await router.complete(_request())
        assert await audit.query(action="llm.third_party_generation") == []


class TestToolOutputEscalates:
    async def test_any_tool_output_makes_the_turn_sensitive(self, audit) -> None:
        registry = ToolRegistry(policy=ToolPolicy(), audit=audit)

        async def handler() -> str:
            return "Meeting with Dr. Hassan at 4pm"

        registry.register(FunctionTool(ToolSpec("calendar_today", "Today's events."), handler))
        context = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=10.0)
        with data_scope(INTERNAL) as scope:
            result, _ = await registry.execute(
                ToolCall(ToolCallId("c1"), "calendar_today", {}), context
            )
        assert result.ok
        assert scope.level is SENSITIVE
        assert "tool_output" in scope.reasons

    async def test_a_tool_that_never_ran_does_not_escalate(self, audit) -> None:
        registry = ToolRegistry(policy=ToolPolicy(), audit=audit)
        context = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=10.0)
        with data_scope(INTERNAL) as scope:
            result, _ = await registry.execute(ToolCall(ToolCallId("c1"), "missing", {}), context)
        assert not result.ok
        assert scope.level is INTERNAL


# -- the kernel ------------------------------------------------------------------------


def _turn(message: str = "Suggest a name for a cat", **overrides) -> TurnRequest:
    base = dict(
        message=message, user_id=UserId("test-user"),
        use_memory=False, use_knowledge=False, use_tools=False,
    )
    base.update(overrides)
    return TurnRequest(**base)


@pytest.fixture
async def hosted_kernel(container):
    """The container's kernel, routing between a hosted and a local echo provider."""
    await container.startup()
    hosted, local = _hosted(), _local()
    router = _router(hosted, local, audit=container.audit)
    deps = container.kernel._deps
    deps.router = router
    # The reasoner and planner hold their own reference to the router they were built with.
    for engine in (deps.reasoner, deps.planner):
        if engine is not None and hasattr(engine, "_router"):
            engine._router = router
    return container, hosted, local


class TestKernelClassifiesTurns:
    async def test_a_plain_turn_may_use_the_hosted_provider(self, hosted_kernel) -> None:
        container, hosted, local = hosted_kernel
        await container.kernel.run(_turn())
        assert hosted.call_count >= 1
        assert local.call_count == 0

    async def test_a_turn_grounded_in_documents_stays_local(
        self, hosted_kernel, handbook, user
    ) -> None:
        container, hosted, local = hosted_kernel
        await container.knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("handbook"), owner_id=user,
        )
        result = await container.kernel.run(
            _turn("How many days of annual leave do I get?", use_knowledge=True)
        )
        assert result.citations  # the turn really did draw on the document
        assert hosted.call_count == 0
        assert local.call_count >= 1

    async def test_a_turn_with_caller_evidence_stays_local(self, hosted_kernel) -> None:
        from aura_core.domain.caller_context import CallerContext

        container, hosted, _ = hosted_kernel
        evidence = (CallerContext.of(id="sample-42", text="pH 7.1", source="lab"),)
        await container.kernel.run(_turn(caller_context=evidence))
        assert hosted.call_count == 0

    async def test_streaming_turns_are_classified_too(self, hosted_kernel, handbook, user) -> None:
        container, hosted, _ = hosted_kernel
        await container.knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("handbook"), owner_id=user,
        )
        frames = [
            f async for f in container.kernel.stream(
                _turn("How many days of annual leave do I get?", use_knowledge=True)
            )
        ]
        assert frames[-1]["type"] == "done"
        assert hosted.call_count == 0


class TestSessionsRememberSensitivity:
    """An earlier answer can quote a document; the history then carries it into later turns."""

    async def test_a_clean_session_stays_eligible(self, hosted_kernel) -> None:
        container, hosted, _ = hosted_kernel
        session = SessionId("s-clean")
        await container.kernel.run(_turn(session_id=session))
        await container.kernel.run(_turn("And a name for a dog?", session_id=session))
        assert hosted.call_count >= 2

    async def test_after_a_sensitive_turn_the_session_stays_local(
        self, hosted_kernel, handbook, user
    ) -> None:
        container, hosted, local = hosted_kernel
        await container.knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("handbook"), owner_id=user,
        )
        session = SessionId("s-tainted")
        await container.kernel.run(
            _turn("How many days of annual leave do I get?", session_id=session, use_knowledge=True)
        )
        calls_before = hosted.call_count
        # No document this time -- but the history now holds the previous answer.
        await container.kernel.run(_turn("Thanks, summarise that for me", session_id=session))
        assert hosted.call_count == calls_before

    async def test_a_lost_marker_fails_closed(self, hosted_kernel) -> None:
        container, hosted, _ = hosted_kernel
        session = SessionId("s-evicted")
        await container.kernel.run(_turn(session_id=session))
        calls_after_first = hosted.call_count
        await container.cache_backend.delete(
            f"llm:session-clean:v1:test-user:{session.value}"
        )
        await container.kernel.run(_turn("And a name for a dog?", session_id=session))
        assert hosted.call_count == calls_after_first

    async def test_self_hosted_deployments_do_no_marker_io(self, container) -> None:
        await container.startup()
        session = SessionId("s-local")
        await container.kernel.run(_turn(session_id=session))
        key = f"llm:session-clean:v1:test-user:{session.value}"
        assert await container.cache_backend.get(key) is None
