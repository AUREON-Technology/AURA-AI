"""KAN-109: a turn resolves its desktop session, workload hint and registry model once.

Before, a chat turn asked the host-agent registry twice (tool list, system prompt) -- each a
relay round trip on a multi-worker gateway -- re-scored the message's complexity for every
workload question, and read the model store three times to learn that nothing was registered.
The fakes here sit at the port boundary (the registry and the model store); the kernel, the
container and the registry are the real ones.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.domain.identifiers import UserId
from aura_core.kernel import TurnRequest
from aura_core.kernel import kernel as kernel_module
from aura_core.kernel.turn_scope import current_turn_lookups, turn_lookup_scope
from aura_core.mlops.model_registry import (
    SERVING_SNAPSHOT_TTL_S,
    InMemoryModelStore,
    ModelRegistry,
    ModelStage,
    ModelVersion,
)
from aura_core.ports.runtime import ToolContext

USER = UserId("lookup-user")


class CountingHostRegistry:
    """A host-agent registry that counts lookups; ``session`` is what ``resolve`` returns."""

    def __init__(self, session: Any = None, *, delay_s: float = 0.0) -> None:
        self.session = session
        self.delay_s = delay_s
        self.resolves = 0

    async def resolve(self, user_id: UserId) -> Any:
        self.resolves += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.session


class CountingModelStore(InMemoryModelStore):
    """The in-memory model store, counting reads and optionally paying a round trip each."""

    def __init__(self, *, delay_s: float = 0.0) -> None:
        super().__init__()
        self.delay_s = delay_s
        self.reads = 0

    async def _pay(self) -> None:
        self.reads += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)

    async def get(self, model_id: str, version: str):
        await self._pay()
        return await super().get(model_id, version)

    async def list(self, *, model_id: str | None = None, stage: ModelStage | None = None):
        await self._pay()
        return await super().list(model_id=model_id, stage=stage)


def _turn(message: str = "hello there") -> TurnRequest:
    return TurnRequest(message=message, user_id=USER)


async def _drain(stream) -> list[dict]:
    return [frame async for frame in stream]


class TestDesktopSessionResolvedOnce:
    @pytest.mark.parametrize("paired", [False, True])
    async def test_run_resolves_once_per_turn(self, container, paired: bool) -> None:
        await container.startup()
        registry = CountingHostRegistry(session=object() if paired else None)
        container.kernel._deps.host_agent_registry = registry

        await container.kernel.run(_turn())
        assert registry.resolves == 1
        await container.kernel.run(_turn())
        assert registry.resolves == 2  # once per turn, not once per process

    async def test_stream_resolves_once_per_turn(self, container) -> None:
        await container.startup()
        registry = CountingHostRegistry(session=object())
        container.kernel._deps.host_agent_registry = registry

        frames = await _drain(container.kernel.stream(_turn()))
        assert frames[-1]["type"] == "done"
        assert registry.resolves == 1

    async def test_a_tool_call_reuses_the_turns_yes(self, container) -> None:
        registry = CountingHostRegistry(session=object())
        container.host_agent_registry = registry
        context = ToolContext(user_id=USER, correlation_id="c", deadline_s=30.0)
        with turn_lookup_scope() as lookups:
            lookups.desktop[USER.value] = True
            assert await container._host_routed(context) is True
            assert await container._host_routed(context) is True
        assert registry.resolves == 0

    async def test_a_tool_call_never_reuses_a_no(self, container) -> None:
        """A desktop that connected mid-turn must be seen: a stale "no" would apply the sandbox's
        looser side-effect rule to the user's own machine."""
        registry = CountingHostRegistry(session=object())
        container.host_agent_registry = registry
        context = ToolContext(user_id=USER, correlation_id="c", deadline_s=30.0)
        with turn_lookup_scope() as lookups:
            lookups.desktop[USER.value] = False
            assert await container._host_routed(context) is True
            assert registry.resolves == 1
            # ... and once seen, later calls in the same turn reuse it.
            assert await container._host_routed(context) is True
            assert registry.resolves == 1

    async def test_outside_a_turn_nothing_is_memoised(self, container) -> None:
        registry = CountingHostRegistry(session=None)
        container.host_agent_registry = registry
        context = ToolContext(user_id=USER, correlation_id="c", deadline_s=30.0)
        assert current_turn_lookups() is None
        await container._host_routed(context)
        await container._host_routed(context)
        assert registry.resolves == 2


class TestWorkloadHintComputedOnce:
    async def test_one_hint_per_turn(self, container, monkeypatch) -> None:
        await container.startup()
        calls = 0
        real = kernel_module.workload_for_turn

        def counting(**kwargs):
            nonlocal calls
            calls += 1
            return real(**kwargs)

        monkeypatch.setattr(kernel_module, "workload_for_turn", counting)
        await container.kernel.run(_turn())
        assert calls == 1
        await _drain(container.kernel.stream(_turn()))
        assert calls == 2


class TestAssignedModel:
    def _registry(self, store: CountingModelStore, clock: FrozenClock) -> ModelRegistry:
        return ModelRegistry(store=store, monotonic=clock.monotonic)

    async def test_an_empty_registry_costs_no_read_within_the_ttl(self, container) -> None:
        await container.startup()
        clock = FrozenClock()
        store = CountingModelStore()
        container.kernel._deps.models = self._registry(store, clock)

        for _ in range(3):
            await container.kernel.run(_turn())
            await container.kernel.wait_for_shadow()
        # One snapshot (production, canary and shadow lists, read concurrently) served all three
        # turns -- the assignment and the background shadow check alike.
        assert store.reads == 3

        clock.advance(SERVING_SNAPSHOT_TTL_S + 0.1)
        await container.kernel.run(_turn())
        await container.kernel.wait_for_shadow()
        assert store.reads == 6

    async def test_a_transition_here_applies_at_once(self) -> None:
        clock = FrozenClock()
        store = CountingModelStore()
        registry = self._registry(store, clock)
        assert await registry.serving_version("aura-ft", subject="u") is None

        await registry.register(
            ModelVersion(model_id="aura-ft", version="v1", provider_kind="ollama",
                         artefact_uri="aura-ft:v1")
        )
        await registry.transition("aura-ft", "v1", to=ModelStage.CANARY, canary_percent=100)
        served = await registry.serving_version("aura-ft", subject="u")
        assert served is not None and served.version == "v1"
        assignment = await registry.assign("aura-ft", subject="u")
        assert assignment is not None and assignment.version == served.version

    async def test_the_turn_uses_the_served_tag(self, container) -> None:
        await container.startup()
        clock = FrozenClock()
        registry = self._registry(CountingModelStore(), clock)
        await registry.register(
            ModelVersion(model_id="aura-ft", version="v1", provider_kind="ollama",
                         artefact_uri="aura-ft:v1")
        )
        await registry.transition("aura-ft", "v1", to=ModelStage.CANARY, canary_percent=100)
        container.kernel._deps.models = registry
        with turn_lookup_scope():
            assert await container.kernel._assigned_model(_turn()) == "aura-ft:v1"


class TestTimeToFirstToken:
    """With every lookup store paying a 50 ms round trip, the extra time to the first token stays
    under three round trips. Before: two relay lookups and three model-store reads, all ahead of
    the first token (~250 ms)."""

    RTT_S = 0.05

    async def _ttft(self, container) -> float:
        started = time.perf_counter()
        async for frame in container.kernel.stream(_turn()):
            if frame["type"] == "token":
                return time.perf_counter() - started
        raise AssertionError("no token frame")

    async def test_extra_ttft_is_at_most_two_round_trips(self, container) -> None:
        await container.startup()
        deps = container.kernel._deps
        deps.host_agent_registry = None
        deps.models = None
        baseline = min([await self._ttft(container) for _ in range(3)])

        deps.host_agent_registry = CountingHostRegistry(session=None, delay_s=self.RTT_S)
        deps.models = ModelRegistry(store=CountingModelStore(delay_s=self.RTT_S))
        slowed = await self._ttft(container)  # cold: nothing cached yet

        assert slowed - baseline < 3 * self.RTT_S, (baseline, slowed)
