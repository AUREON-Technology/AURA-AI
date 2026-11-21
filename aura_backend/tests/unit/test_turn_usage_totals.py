"""Turn-wide usage totals, with estimates marked (KAN-106).

Before: ``done.usage.prompt_tokens`` was 0 whenever the provider sent no usage while telemetry
used the assembled count; ``completion_tokens`` fell back to the number of streamed *chunks*;
and ``cost_usd`` covered every generation of the turn while the tokens covered the answer only.
Now the router's ``TurnBudget`` -- which already saw every generation for cost (F30) -- keeps a
``TurnUsage`` ledger with a per-purpose breakdown, and ``done.usage`` and ``TurnTelemetry``
both report it. These pin that, with the real kernel and router; only the model is scripted.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from aura_core.domain import UserId
from aura_core.domain.llm import (
    CompletionRequest,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
)
from aura_core.domain.messages import Conversation, Message
from aura_core.kernel import TurnRequest
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider
from aura_core.llm.router import (
    TurnBudget,
    current_generation_purpose,
    generation_purpose,
    turn_budget_scope,
)

#: Plans with the default install (see test_degraded_turns_and_stream_telemetry).
ADD_TASK = "add a task to buy milk tomorrow"
SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "additionalProperties": False,
    "properties": {"ok": {"type": "boolean"}},
}


class _Ledger:
    """Makes the container's echo provider report a known, distinct usage on every call, and
    records each one with the purpose it was made for."""

    def __init__(self, provider: EchoProvider) -> None:
        self.provider = provider
        self.calls: list[tuple[str, TokenUsage]] = []
        self._complete = provider.complete
        self._stream = provider.stream

    def _purpose(self, request: CompletionRequest) -> str:
        return (request.metadata or {}).get("purpose") or current_generation_purpose() or "answer"

    def _usage(self, purpose: str) -> TokenUsage:
        n = len(self.calls) + 1
        usage = TokenUsage(prompt_tokens=100 * n + 1, completion_tokens=10 * n + 1)
        self.calls.append((purpose, usage))
        return usage

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        purpose = self._purpose(request)
        response = await self._complete(request)
        text = response.text
        if request.metadata.get("purpose") == "repair":
            text = json.dumps({"ok": True})
        elif purpose == "answer":
            text = "not json at all"
        from dataclasses import replace

        return replace(response, text=text, usage=self._usage(purpose))

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        purpose = self._purpose(request)
        async for chunk in self._stream(request):
            if chunk.usage is not None:
                from dataclasses import replace

                chunk = replace(chunk, usage=self._usage(purpose))
            yield chunk

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "_Ledger":
        monkeypatch.setattr(self.provider, "complete", self.complete)
        monkeypatch.setattr(self.provider, "stream", self.stream)
        return self

    def total(self) -> tuple[int, int]:
        return (
            sum(u.prompt_tokens for _, u in self.calls),
            sum(u.completion_tokens for _, u in self.calls),
        )

    def by_purpose(self) -> dict[str, tuple[int, int, int]]:
        out: dict[str, tuple[int, int, int]] = {}
        for purpose, usage in self.calls:
            p, c, g = out.get(purpose, (0, 0, 0))
            out[purpose] = (p + usage.prompt_tokens, c + usage.completion_tokens, g + 1)
        return out


def _breakdown(by_purpose: dict[str, Any]) -> dict[str, tuple[int, int, int]]:
    return {
        k: (v["prompt_tokens"], v["completion_tokens"], v["generations"])
        for k, v in by_purpose.items()
    }


@pytest.fixture
def ledger(container, monkeypatch) -> _Ledger:
    provider = container.router.get("echo")
    provider.reset()
    return _Ledger(provider).install(monkeypatch)


class TestRunTotals:
    async def test_planner_answer_and_repair_are_summed(self, container, ledger) -> None:
        await container.startup()
        result = await container.kernel.run(TurnRequest(
            message=ADD_TASK, user_id=UserId("u-usage"), response_schema=SCHEMA,
        ))
        purposes = [p for p, _ in ledger.calls]
        assert "planner" in purposes and "answer" in purposes and "repair" in purposes, purposes

        telemetry = result.telemetry
        assert (telemetry.prompt_tokens, telemetry.completion_tokens) == ledger.total()
        assert telemetry.generations == len(ledger.calls)
        assert _breakdown(telemetry.usage_by_purpose) == ledger.by_purpose()
        assert telemetry.usage_estimated is False
        tokens = telemetry.as_dict()["tokens"]
        assert tokens["total"] == sum(ledger.total()) and tokens["estimated"] is False
        assert telemetry.tokenizer and telemetry.context_tokens_exact is False


class TestStreamTotals:
    async def test_done_usage_and_telemetry_are_the_turn_total(self, container, ledger) -> None:
        await container.startup()
        sink = container.telemetry_sink
        frames = [f async for f in container.kernel.stream(
            TurnRequest(message=ADD_TASK, user_id=UserId("u-usage"))
        )]
        done = frames[-1]
        assert done["type"] == "done", done
        purposes = [p for p, _ in ledger.calls]
        assert "planner" in purposes and "answer" in purposes, purposes

        usage = done["usage"]
        assert (usage["prompt_tokens"], usage["completion_tokens"]) == ledger.total()
        assert usage["total_tokens"] == sum(ledger.total())
        assert usage["generations"] == len(ledger.calls)
        assert usage["estimated"] is False
        assert _breakdown(usage["by_purpose"]) == ledger.by_purpose()

        # A streamed turn writes its telemetry after `done` (KAN-110); the record itself is
        # built before `done` from the same ledger, so it must be the turn total `done` reported.
        at_done = ledger.total()
        await container.kernel.wait_for_post_turn()
        telemetry = sink._turns[-1]
        assert (telemetry.prompt_tokens, telemetry.completion_tokens) == at_done
        assert telemetry.usage_estimated is False


class _Silent(EchoProvider):
    """A provider that reports no usage at all."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        response = await super().complete(request)
        from dataclasses import replace

        return replace(response, usage=TokenUsage())

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        async for chunk in super().stream(request):
            if chunk.usage is not None:
                from dataclasses import replace

                chunk = replace(chunk, usage=None)
            yield chunk


def _request(text: str = "summarise the meeting notes please") -> CompletionRequest:
    return CompletionRequest(conversation=Conversation.of([Message.user(text)]))


class TestEstimatesAreMarked:
    async def test_a_silent_provider_is_charged_a_marked_estimate(self) -> None:
        router = ProviderRouter([_Silent(name="s")])
        budget = TurnBudget()
        request = _request()
        with turn_budget_scope(budget):
            await router.complete(request, budget=budget)
            chunks = [c async for c in router.stream(request, budget=budget)]
        assert budget.usage.estimated is True
        assert budget.usage.generations == 2
        # The router's own prompt figure, twice; completions counted from the produced text.
        assert budget.usage.prompt_tokens == 2 * router.prompt_tokens(request, "s")
        assert budget.usage.completion_tokens > 0
        assert all(c.usage is None for c in chunks)
        assert budget.usage.by_purpose["answer"].estimated is True

    async def test_a_reporting_provider_is_not_marked(self) -> None:
        router = ProviderRouter([EchoProvider(name="e")])
        budget = TurnBudget()
        await router.complete(_request(), budget=budget)
        assert budget.usage.estimated is False and budget.usage.generations == 1

    async def test_one_estimated_generation_marks_the_turn(self) -> None:
        reporting = ProviderRouter([EchoProvider(name="e")])
        silent = ProviderRouter([_Silent(name="s")])
        budget = TurnBudget()
        with turn_budget_scope(budget):
            await reporting.complete(_request(), budget=budget)  # the answer: reported
            with generation_purpose("summary"):
                await silent.complete(_request())  # an engine generation: estimated
        assert budget.usage.estimated is True
        assert budget.usage.by_purpose["answer"].estimated is False
        assert budget.usage.by_purpose["summary"].estimated is True

    async def test_stream_chunks_carry_the_estimated_flag(self) -> None:
        """An OpenAI-compatible stream with no usage: the final chunk's usage says estimated."""
        import httpx

        from aura_core.llm.providers.openai_compatible import OpenAICompatibleProvider

        body = (
            'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"world"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        provider = OpenAICompatibleProvider(
            name="oc", base_url="http://localhost:9/v1", model="qwen2.5",
        )
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
        provider._client = httpx.AsyncClient(
            base_url=provider._base_url(), transport=transport
        )
        chunks = [c async for c in provider.stream(_request())]
        final = chunks[-1]
        assert final.usage is not None and final.usage.estimated is True
        assert final.usage.prompt_tokens == ProviderRouter([provider]).prompt_tokens(_request(), "oc")
