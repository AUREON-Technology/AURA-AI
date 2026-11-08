"""PP-04 (provider performance audit, 2026-11-08): shadow mirroring stays off the turn's path.

`AIKernel` mirrors each turn to registered shadow model versions (ADR-008) so a candidate can be
compared on real traffic. That call used to be awaited inline -- after the answer, before the
turn returned -- so one registered shadow added a whole extra generation to every turn and held
the stream's ``done`` frame back for as long. These pin the fix: the turn finishes while the
shadow is still running, at most one shadow runs at a time, it never queues ahead of a person or
counts against their quota, and an artefact that is not a servable model tag is never sent.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from aura_core.domain.identifiers import UserId
from aura_core.domain.llm import CompletionRequest, CompletionResponse
from aura_core.kernel import TurnRequest
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider

SHADOW_TAG = "aura-ft-shadow:v2"


class _GatedShadowProvider(EchoProvider):
    """Echo for real turns; a shadow-model call blocks until the test releases it."""

    def __init__(self) -> None:
        super().__init__(name="gated")
        self.release = asyncio.Event()
        self.shadow_requests: list[CompletionRequest] = []
        self.shadow_started = asyncio.Event()

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if request.model == SHADOW_TAG:
            self.shadow_requests.append(request)
            self.shadow_started.set()
            await self.release.wait()
        return await super().complete(request)


class _Models:
    """The slice of the model registry the kernel uses, with one shadow version."""

    def __init__(self, artefact_uri: str = SHADOW_TAG) -> None:
        self._shadow = SimpleNamespace(
            model_id="aura-ft", version="v2", artefact_uri=artefact_uri, key="aura-ft@v2"
        )
        self.metrics: list[dict[str, float]] = []

    async def assign(self, model_id: str, *, subject: str):
        return None  # no canary: real turns use the provider default

    async def shadow_versions(self, model_id: str):
        return [self._shadow]

    async def record_metrics(self, model_id: str, version: str, metrics: dict[str, float]) -> None:
        self.metrics.append(dict(metrics))


@pytest.fixture
def wired(container):
    provider = _GatedShadowProvider()
    models = _Models()
    container.kernel._deps.router = ProviderRouter([provider])
    container.kernel._deps.models = models
    return container.kernel, provider, models


def _turn(message: str = "hello there") -> TurnRequest:
    return TurnRequest(message=message, user_id=UserId("test-user"))


async def _release(kernel, provider) -> None:
    provider.release.set()
    await asyncio.wait_for(kernel.wait_for_shadow(), timeout=5)


class TestShadowOffCriticalPath:
    async def test_the_stream_finishes_while_the_shadow_is_still_running(self, wired) -> None:
        kernel, provider, models = wired
        frames = [f async for f in kernel.stream(_turn())]
        assert frames[-1]["type"] == "done"
        # The turn is over; the shadow generation started and is still blocked.
        await asyncio.wait_for(provider.shadow_started.wait(), timeout=5)
        assert models.metrics == []
        await _release(kernel, provider)
        assert models.metrics and models.metrics[-1]["shadow_ok"] == 1.0

    async def test_run_returns_while_the_shadow_is_still_running(self, wired) -> None:
        kernel, provider, models = wired
        result = await asyncio.wait_for(kernel.run(_turn()), timeout=5)
        assert result.text
        await asyncio.wait_for(provider.shadow_started.wait(), timeout=5)
        assert models.metrics == []
        await _release(kernel, provider)

    async def test_only_one_shadow_generation_runs_at_a_time(self, wired) -> None:
        kernel, provider, _ = wired
        for message in ("first question", "second question", "third question"):
            [f async for f in kernel.stream(_turn(message))]
        await asyncio.wait_for(provider.shadow_started.wait(), timeout=5)
        assert len(provider.shadow_requests) == 1
        await _release(kernel, provider)

    async def test_a_shadow_never_queues_ahead_of_a_person_or_uses_their_quota(self, wired) -> None:
        kernel, provider, _ = wired
        [f async for f in kernel.stream(_turn())]
        await asyncio.wait_for(provider.shadow_started.wait(), timeout=5)
        hint = provider.shadow_requests[0].workload
        assert hint is not None
        assert hint.user_key is None
        assert hint.priority == 9
        await _release(kernel, provider)

    async def test_an_artefact_that_is_not_a_model_tag_is_never_sent(self, container) -> None:
        """A file URI 404s on every call, and each failure would count against the breaker of
        the provider real turns use."""
        provider = _GatedShadowProvider()
        models = _Models(artefact_uri="s3://bucket/adapters/aura-ft-v2")
        container.kernel._deps.router = ProviderRouter([provider])
        container.kernel._deps.models = models
        kernel = container.kernel
        [f async for f in kernel.stream(_turn())]
        await asyncio.wait_for(kernel.wait_for_shadow(), timeout=5)
        assert all(call.model is None for call in provider.calls)
        assert models.metrics == []
