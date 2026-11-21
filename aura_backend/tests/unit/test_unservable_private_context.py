"""KAN-112: don't fetch private context for a turn that could then not be served.

On the chat-only launch the only reachable provider is a hosted slot with an INTERNAL ceiling
(its self-hosted fallback is down). Memory and retrieval ran before classification; whatever
they returned made the turn SENSITIVE, the hosted slot refused it, and the turn failed
``capability_unavailable`` after paying for the search. Now, when the router's own plan says
nothing could take the turn at SENSITIVE but something can at INTERNAL, those sources are
skipped and the turn is answered as plain chat, with ``private_context_skipped`` in ``done``.

What must not change: hosted providers still never receive SENSITIVE content, and a turn that
is SENSITIVE from the start still fails rather than being sent.
"""

from __future__ import annotations

import pytest

from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.caller_context import CallerContext
from aura_core.domain.errors import ProviderError
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.domain.llm import DataClassification, RoutingPolicy, SamplingParams
from aura_core.kernel import TurnRequest
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn
from aura_core.llm.sensitivity import data_scope

USER = UserId("test-user")
QUESTION = "How many days of annual leave do I get?"


def _hosted() -> EchoProvider:
    return EchoProvider(
        name="hosted",
        capabilities=ProviderCapabilities(
            model_id="openai/gpt-oss-120b", context_window=8192, max_output_tokens=512,
            supports_tools=True, quality_tier=3,
            max_data_classification=DataClassification.INTERNAL, third_party=True,
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


def _use(container, router: ProviderRouter) -> None:
    deps = container.kernel._deps
    deps.router = router
    for engine in (deps.reasoner, deps.planner):
        if engine is not None and hasattr(engine, "_router"):
            engine._router = router


def _turn(**overrides) -> TurnRequest:
    base = dict(message=QUESTION, user_id=USER, use_tools=False, use_knowledge=True)
    base.update(overrides)
    return TurnRequest(**base)


def _shown(provider: EchoProvider) -> str:
    return "\n".join(m.content for call in provider.calls for m in call.conversation)


def _sensitive_probe() -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("x")]),
        sampling=SamplingParams(max_output_tokens=64),
        data_classification=DataClassification.SENSITIVE,
    )


async def _open_breaker(router: ProviderRouter, provider: EchoProvider) -> None:
    """Fail the provider until the router's breaker takes it out, as a 503ing Space does."""
    for _ in range(20):
        if not router.plan(_sensitive_probe()).candidates:
            return
        provider.queue(ScriptedTurn(raise_error=ProviderError("HTTP 503", provider=provider.name,
                                                               retryable=True)))
        with data_scope(DataClassification.SENSITIVE, "test"):
            try:
                await router.complete(_sensitive_probe())
            except Exception:
                pass
    raise AssertionError("the breaker never opened")


@pytest.fixture
async def handbook_container(container, handbook):
    await container.startup()
    await container.knowledge.ingest(
        content=handbook, title="Handbook", document_id=DocumentId("handbook"), owner_id=USER,
    )
    return container


class TestHostedOnly:
    async def test_the_turn_is_answered_without_private_context(self, handbook_container) -> None:
        container = handbook_container
        hosted = _hosted()
        _use(container, ProviderRouter([hosted], default_policy=RoutingPolicy.QUALITY_FIRST))

        result = await container.kernel.run(_turn())

        assert result.telemetry.error_kind is None
        assert result.telemetry.provider == "hosted"
        assert result.telemetry.retrieval.candidates == 0  # retrieval never ran
        assert result.citations == ()

    async def test_the_hosted_provider_never_sees_the_document(
        self, handbook_container, handbook
    ) -> None:
        container = handbook_container
        hosted = _hosted()
        _use(container, ProviderRouter([hosted], default_policy=RoutingPolicy.QUALITY_FIRST))
        await container.kernel.run(_turn())
        assert hosted.call_count >= 1
        first_line = next(line for line in handbook.splitlines() if len(line.strip()) > 30)
        assert first_line.strip() not in _shown(hosted)

    async def test_the_stream_says_so(self, handbook_container) -> None:
        container = handbook_container
        _use(container, ProviderRouter([_hosted()], default_policy=RoutingPolicy.QUALITY_FIRST))
        frames = [f async for f in container.kernel.stream(_turn())]
        done = frames[-1]
        assert done["type"] == "done"
        assert "private_context_skipped" in done["degraded_reasons"]
        assert done["degraded"] is True
        assert done["citations"] == []

    async def test_a_turn_sensitive_from_the_start_still_fails(self, handbook_container) -> None:
        """Caller evidence makes it SENSITIVE before any context: nothing to skip, so it is
        refused exactly as before -- never sent to the hosted slot."""
        container = handbook_container
        hosted = _hosted()
        _use(container, ProviderRouter([hosted], default_policy=RoutingPolicy.QUALITY_FIRST))
        evidence = (CallerContext.of(id="sample-42", text="pH 7.1", source="lab"),)
        result = await container.kernel.run(_turn(caller_context=evidence))
        assert result.telemetry.error_kind == "capability_unavailable"
        assert hosted.call_count == 0


class TestFallbackStates:
    async def test_a_healthy_local_provider_keeps_the_context(self, handbook_container) -> None:
        container = handbook_container
        hosted, local = _hosted(), _local()
        _use(container, ProviderRouter([hosted, local], default_policy=RoutingPolicy.QUALITY_FIRST))

        result = await container.kernel.run(_turn())

        assert result.citations  # grounded in the document, as before
        assert result.telemetry.provider == "local"
        assert hosted.call_count == 0

    async def test_a_local_provider_taken_out_by_its_breaker_counts_as_gone(
        self, handbook_container
    ) -> None:
        """The live topology: the Space keeps 503ing until the breaker opens."""
        container = handbook_container
        hosted, local = _hosted(), _local()
        router = ProviderRouter([hosted, local], default_policy=RoutingPolicy.QUALITY_FIRST)
        _use(container, router)
        await _open_breaker(router, local)

        result = await container.kernel.run(_turn())

        assert result.telemetry.error_kind is None
        assert result.telemetry.provider == "hosted"
        assert result.citations == ()

    async def test_self_hosted_deployments_are_untouched(self, handbook_container) -> None:
        container = handbook_container
        _use(container, ProviderRouter([_local()], default_policy=RoutingPolicy.QUALITY_FIRST))
        result = await container.kernel.run(_turn())
        assert result.citations
