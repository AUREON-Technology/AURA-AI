"""Kernel orchestration under partial failure, and the router's failover boundary.

The kernel's job when something breaks is to produce the best answer it still can and *say* what
degraded, rather than failing the turn. The exception is the streaming failover boundary: once a
token has reached the client, switching providers would duplicate output, so the router must
refuse to fail over and surface the error instead.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain import CompletionRequest, Conversation, Message, SamplingParams
from aura_core.domain.errors import AllProvidersFailedError, ProviderError
from aura_core.domain.identifiers import DocumentId, SessionId
from aura_core.domain.llm import ProviderCapabilities, RoutingPolicy
from aura_core.domain.telemetry import TurnOutcome
from aura_core.kernel import AIKernel, TurnRequest
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn


def _request(message: str = "How many days of annual leave do I get?", **overrides):
    from aura_core.domain.identifiers import UserId

    base = dict(message=message, user_id=UserId("test-user"))
    base.update(overrides)
    return TurnRequest(**base)


class TestTurnTelemetry:
    async def test_a_successful_turn_reports_content_free_telemetry(self, container) -> None:
        """Telemetry is widely readable and long-lived; prompts and answers must not enter it."""
        await container.startup()
        result = await container.kernel.run(_request("my salary is 95000 EGP"))
        payload = result.telemetry.as_dict()

        import json

        assert "95000" not in json.dumps(payload)
        assert payload["latency"]["total_ms"] >= 0.0

    async def test_stage_timings_are_recorded(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(_request())
        timings = result.telemetry.timings

        assert timings.generation >= 0.0
        assert timings.overhead >= 0.0

    async def test_a_correlation_id_is_always_present(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(_request())
        assert result.telemetry.correlation_id.value


class TestDegradation:
    async def test_a_provider_outage_is_a_typed_failure_not_a_crash(self, container) -> None:
        await container.startup()
        container.router._providers.clear()

        result = await container.kernel.run(_request())
        assert result.telemetry.outcome is not TurnOutcome.SUCCESS
        assert result.telemetry.error_kind

    async def test_retrieval_is_skipped_when_the_caller_opts_out(self, container, handbook,
                                                                 user) -> None:
        await container.startup()
        await container.knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        result = await container.kernel.run(_request(use_knowledge=False))
        assert result.citations == ()

    async def test_memory_is_skipped_when_the_caller_opts_out(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(_request(use_memory=False))
        assert result.telemetry.outcome is TurnOutcome.SUCCESS

    async def test_tools_are_withheld_when_the_caller_opts_out(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(_request(use_tools=False))
        assert result.tool_calls == ()

    async def test_a_session_persists_across_turns(self, container) -> None:
        await container.startup()
        session = SessionId("s-1")
        await container.kernel.run(_request("My name is Ahmed.", session_id=session))
        second = await container.kernel.run(_request("What did I just say?", session_id=session))

        assert second.telemetry.outcome is TurnOutcome.SUCCESS

    async def test_an_unknown_agent_falls_back_to_the_orchestrator_prompt(
        self, container
    ) -> None:
        """A typo'd agent name must not produce a turn with no system prompt at all."""
        await container.startup()
        result = await container.kernel.run(_request(agent="nonexistent-agent"))
        assert result.telemetry.outcome is TurnOutcome.SUCCESS


class TestServableModelTag:
    def test_ollama_tags_pass_through(self) -> None:
        assert AIKernel._servable_model_tag("qwen2.5:3b-instruct-q4_K_M") == (
            "qwen2.5:3b-instruct-q4_K_M"
        )

    def test_scripted_and_path_artefacts_are_ignored(self) -> None:
        assert AIKernel._servable_model_tag("scripted://aura-ft/v1") is None
        assert AIKernel._servable_model_tag("/var/lib/aura/datasets/x") is None
        assert AIKernel._servable_model_tag("") is None


class TestStreaming:
    async def test_a_stream_emits_deltas_and_a_terminal_frame(self, container) -> None:
        await container.startup()
        frames = [frame async for frame in container.kernel.stream(_request())]

        assert frames[-1]["type"] == "done"
        assert any(f["type"] == "token" for f in frames)

    async def test_a_stream_carries_citations_on_the_terminal_frame(
        self, container, user, handbook
    ) -> None:
        await container.startup()
        await container.knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        frames = [frame async for frame in container.kernel.stream(_request())]
        done = frames[-1]
        assert done["citations"]
        assert any("handbook@v1" in c for c in done["citations"])
        assert "episodic_summary" not in done["citations"]
        assert done["searched_but_empty"] is False

    async def test_a_stream_reports_searched_but_empty_on_done(
        self, container, user, handbook
    ) -> None:
        await container.startup()
        await container.knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        frames = [
            frame async for frame in container.kernel.stream(
                _request("the melting point of tungsten")
            )
        ]
        done = frames[-1]
        assert done["type"] == "done"
        assert done["searched_but_empty"] is True
        assert done["degraded"] is True

    async def test_a_provider_outage_mid_stream_yields_an_error_frame(
        self, container
    ) -> None:
        """The client must be told, not left with a stream that simply stops."""
        await container.startup()
        container.router._providers.clear()

        frames = [frame async for frame in container.kernel.stream(_request())]
        assert frames[-1]["type"] == "error"
        assert frames[-1]["error"]
        assert "details" in frames[-1]

    async def test_generation_uses_the_configured_llm_timeout(
        self, container
    ) -> None:
        """AURA_LLM_TIMEOUT_S must reach CompletionRequest.timeout_s.

        The router first-token wait uses that field, not the provider HTTP client timeout.
        Leaving the 60s dataclass default is what surfaces as
        ``all 1 provider attempt(s) failed before streaming began`` on CPU Ollama.
        """
        from dataclasses import replace

        await container.startup()
        container.kernel._deps.settings = replace(
            container.settings,
            llm=replace(container.settings.llm, request_timeout_s=180.0),
        )
        echo = next(
            provider for provider in container.providers if hasattr(provider, "calls")
        )
        echo.calls.clear()

        frames = [frame async for frame in container.kernel.stream(_request())]
        assert frames[-1]["type"] == "done"
        assert echo.calls
        assert echo.calls[-1].timeout_s == 180.0


class TestRouterFailover:
    def _provider(self, name: str, *, turns=(), **caps_overrides) -> EchoProvider:
        caps = ProviderCapabilities(
            model_id=f"{name}-1", context_window=8192, max_output_tokens=1024,
            supports_streaming=True,
            **{"is_local": True, "quality_tier": 1, **caps_overrides},
        )
        return EchoProvider(name=name, model=f"{name}-1", capabilities=caps, script=list(turns))

    def _completion(self) -> CompletionRequest:
        return CompletionRequest(
            conversation=Conversation.of([Message.user("hello")]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=32),
        )

    async def test_a_failing_provider_fails_over_before_the_first_token(self, clock) -> None:
        primary = self._provider("primary", turns=[ScriptedTurn(raise_error=ProviderError("down", provider="scripted"))])
        secondary = self._provider("secondary")
        router = ProviderRouter([primary, secondary], clock=clock.monotonic)

        response = await router.complete(self._completion())
        assert response.provider == "secondary"

    async def test_a_stream_that_fails_before_any_output_fails_over(self, clock) -> None:
        primary = self._provider("primary", turns=[ScriptedTurn(raise_error=ProviderError("down", provider="scripted"))])
        secondary = self._provider("secondary")
        router = ProviderRouter([primary, secondary], clock=clock.monotonic)

        chunks = [c async for c in router.stream(self._completion())]
        assert any(c.provider == "secondary" for c in chunks)

    async def test_every_provider_failing_raises_a_typed_error(self, clock) -> None:
        providers = [
            self._provider(name, turns=[ScriptedTurn(raise_error=ProviderError("down", provider="scripted"))])
            for name in ("a", "b")
        ]
        router = ProviderRouter(providers, clock=clock.monotonic)

        with pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(self._completion())
        assert len(excinfo.value.details["attempts"]) == 2
        assert "a:provider_error" in excinfo.value.message
        assert "b:provider_error" in excinfo.value.message

    async def test_a_non_retryable_error_stops_the_ladder(self, clock) -> None:
        """Trying every provider against a malformed request wastes the whole budget."""
        primary = self._provider(
            "primary",
            turns=[ScriptedTurn(raise_error=ProviderError("bad request", provider="scripted", retryable=False))],
        )
        secondary = self._provider("secondary")
        router = ProviderRouter([primary, secondary], clock=clock.monotonic)

        with pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(self._completion())
        assert len(excinfo.value.details["attempts"]) == 1

    async def test_stats_expose_the_circuit_state(self, clock) -> None:
        router = ProviderRouter([self._provider("only")], clock=clock.monotonic)
        await router.complete(self._completion())
        stats = router.stats()

        assert stats[0]["provider"] == "only"
        assert stats[0]["requests"] == 1
        assert stats[0]["state"] == "closed"

    async def test_a_circuit_can_be_reset_by_an_operator(self, clock) -> None:
        provider = self._provider(
            "flaky", turns=[ScriptedTurn(raise_error=ProviderError("down", provider="scripted"))] * 10
        )
        router = ProviderRouter([provider], clock=clock.monotonic)
        for _ in range(5):
            with pytest.raises(AllProvidersFailedError):
                await router.complete(self._completion())

        assert router.stats()[0]["state"] == "open"
        router.reset_circuit("flaky")
        assert router.stats()[0]["state"] == "closed"

    async def test_require_local_excludes_a_cloud_provider(self, clock) -> None:
        cloud = self._provider("cloud", is_local=False, quality_tier=3)
        local = self._provider("local", is_local=True)
        router = ProviderRouter([cloud, local], clock=clock.monotonic,
                                default_policy=RoutingPolicy.QUALITY_FIRST)

        decision = router.plan(self._completion())
        assert decision.primary == "cloud"  # quality_first prefers the higher tier

        response = await router.complete(
            CompletionRequest(
                conversation=Conversation.of([Message.user("hi")]),
                sampling=SamplingParams(temperature=0.0, max_output_tokens=16),
                require_local=True,
            )
        )
        assert response.provider == "local"

    async def test_a_quality_floor_excludes_a_provider_below_it(self, clock) -> None:
        weak = self._provider("weak", quality_tier=1)
        strong = self._provider("strong", quality_tier=3)
        router = ProviderRouter([weak, strong], clock=clock.monotonic)

        response = await router.complete(
            CompletionRequest(
                conversation=Conversation.of([Message.user("hi")]),
                sampling=SamplingParams(temperature=0.0, max_output_tokens=16),
                min_quality_tier=3,
            )
        )
        assert response.provider == "strong"

    async def test_an_impossible_requirement_raises_rather_than_silently_relaxing(
        self, clock
    ) -> None:
        """Silently serving a tier-1 model to a caller who demanded tier 3 is worse than an
        error: the caller cannot tell the difference from the response."""
        router = ProviderRouter([self._provider("weak", quality_tier=1)], clock=clock.monotonic)

        with pytest.raises(AllProvidersFailedError):
            await router.complete(
                CompletionRequest(
                    conversation=Conversation.of([Message.user("hi")]),
                    sampling=SamplingParams(temperature=0.0, max_output_tokens=16),
                    min_quality_tier=3,
                )
            )

    async def test_health_reports_every_provider(self, clock) -> None:
        router = ProviderRouter(
            [self._provider("a"), self._provider("b")], clock=clock.monotonic
        )
        health = await router.health()
        assert set(health) == {"a", "b"}


class _FixedPlanner:
    def __init__(self, plan) -> None:
        self._plan = plan

    def needs_plan(self, message, *, available_tools=(), force=None) -> bool:  # noqa: ANN001
        return True

    def goal_from_message(self, message, *, criteria=()):  # noqa: ANN001
        from aura_core.domain.planning import Goal

        return Goal(description=message)

    async def plan(self, goal, available_tools=()):  # noqa: ANN001
        return self._plan


class _CountingTools:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.names: list[str] = []

    def declarations(self, capabilities=(), **kwargs):  # noqa: ANN001
        return self._inner.declarations(capabilities=capabilities, **kwargs)

    def spec(self, name):  # noqa: ANN001
        return self._inner.spec(name)

    async def execute(self, call, context, **kwargs):  # noqa: ANN001
        self.names.append(call.name)
        return await self._inner.execute(call, context, **kwargs)


def _tool_plan(tool: str, arguments: dict) -> object:
    from aura_core.domain.identifiers import PlanId, StepId
    from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind

    return Plan(
        id=PlanId.new(),
        goal=Goal(description="test"),
        steps=(
            PlanStep(
                id=StepId("s1"),
                kind=StepKind.TOOL,
                tool=tool,
                description=f"run {tool}",
                arguments=arguments,
            ),
        ),
    )


def _tainted_retrieve():
    from aura_core.domain.identifiers import DocumentId
    from aura_core.domain.knowledge import Chunk, ChunkId, RetrievalResult, RetrievedChunk

    async def retrieve(query, owner_id=None, **kwargs):  # noqa: ANN001
        chunk = Chunk(
            id=ChunkId("doc:v1:0"),
            document_id=DocumentId("doc"),
            document_version=1,
            index=0,
            content="retrieved context",
        )
        return RetrievalResult(
            chunks=(RetrievedChunk(chunk=chunk, score=0.9),),
            query=query,
            candidates_considered=1,
        )

    return retrieve


def _force_taint(safety):
    original = safety.guard_context_items

    async def force_taint(items, **kwargs):  # noqa: ANN001
        accepted, quarantined, _ = await original(items, **kwargs)
        return accepted, quarantined, True

    return force_taint


class TestStreamToolExecution:
    """AURA-007: kernel.stream runs the same plan/execute loop as run."""

    async def test_stream_yields_step_frames_and_feeds_tool_results(
        self, container
    ) -> None:
        await container.startup()
        container.kernel._deps.planner = _FixedPlanner(
            _tool_plan("calculator", {"expression": "2+2"})
        )

        frames = [
            frame async for frame in container.kernel.stream(
                _request("What is 2+2?", plan=True)
            )
        ]

        types = [f["type"] for f in frames]
        assert "step" in types
        assert "token" in types
        assert types[-1] == "done"
        done = frames[-1]
        assert done["turn_id"]
        assert done["correlation_id"]
        assert "confidence" in done
        assert "degraded" in done
        assert "searched_but_empty" in done
        assert "usage" in done
        echo = container.router.get("echo")
        blob = "\n".join(m.content for m in echo.calls[-1].conversation)
        assert "2+2 = 4" in blob

    async def test_token_stream_tool_calls_are_not_executed(self, container) -> None:
        """Model-emitted tool_calls are frames only; planning is the only execute path."""
        from aura_core.llm.providers.echo import ScriptedTurn

        await container.startup()
        counter = _CountingTools(container.tools)
        container.kernel._deps.tools = counter
        container.executor._tools = counter
        container.kernel._deps.planner = _FixedPlanner(
            _tool_plan("calculator", {"expression": "3+3"})
        )
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(
            text="done",
            tool_calls=[("calculator", {"expression": "9*9"})],
        ))

        frames = [
            frame async for frame in container.kernel.stream(
                _request("calculate then answer", plan=True)
            )
        ]
        assert any(f["type"] == "tool_calls" for f in frames)
        assert counter.names == ["calculator"]

    async def test_tainted_side_effect_does_not_run_without_confirmation(
        self, container
    ) -> None:
        await container.startup()
        container.kernel._deps.planner = _FixedPlanner(
            _tool_plan("propose_task", {"title": "Exfiltrate files"})
        )
        container.kernel._deps.knowledge.retrieve = _tainted_retrieve()
        container.kernel._deps.safety.guard_context_items = _force_taint(
            container.kernel._deps.safety
        )

        frames = [
            frame async for frame in container.kernel.stream(
                _request("add a task called Exfiltrate files", plan=True)
            )
        ]
        assert not any(f.get("type") == "actions" for f in frames)
        done = frames[-1]
        assert done["type"] == "done"
        assert "propose_task" in done.get("needs_confirmation", [])

    async def test_tainted_side_effect_runs_when_confirmed(self, container) -> None:
        await container.startup()
        container.kernel._deps.planner = _FixedPlanner(
            _tool_plan("propose_task", {"title": "Backup notes"})
        )
        container.kernel._deps.knowledge.retrieve = _tainted_retrieve()
        container.kernel._deps.safety.guard_context_items = _force_taint(
            container.kernel._deps.safety
        )

        frames = [
            frame async for frame in container.kernel.stream(
                _request(
                    "add a backup task",
                    plan=True,
                    confirmed_tools=("propose_task",),
                )
            )
        ]
        actions = next(f for f in frames if f["type"] == "actions")
        assert actions["actions"][0]["type"] == "add_task"
        assert actions["actions"][0]["params"]["title"] == "Backup notes"

    async def test_confirmed_tools_alone_forces_planning_without_plan_true(
        self, container
    ) -> None:
        """A confirmation reply resuming a tool the user just approved must reach planning
        even when the reply text itself ("yes, approve.") is too short/generic to match the
        needs_plan heuristic on its own -- confirmed_tools is a signal in its own right."""
        await container.startup()
        captured: dict[str, object] = {}
        original_needs_plan = container.kernel._deps.planner.needs_plan

        def spy_needs_plan(message, *, available_tools=(), force=None):  # noqa: ANN001
            captured["force"] = force
            return original_needs_plan(message, available_tools=available_tools, force=force)

        container.kernel._deps.planner.needs_plan = spy_needs_plan

        frames = [
            frame async for frame in container.kernel.stream(
                _request("yes, approve.", confirmed_tools=("propose_task",))
            )
        ]

        assert captured["force"] is True
        assert frames[-1]["type"] == "done"

    async def test_tool_calls_only_generation_does_not_surface_as_empty_success(
        self, container
    ) -> None:
        """A short confirmation reply skips planning; if the provider still answers with a
        native tool-call and no text, the done frame must not be a bare empty success --
        regression for the "No reply this turn" bug."""
        await container.startup()
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(
            text="",
            tool_calls=[("propose_health_log", {"summary": "ran 5k"})],
        ))

        frames = [
            frame async for frame in container.kernel.stream(_request("yes, approve."))
        ]

        assert not any(f["type"] == "error" for f in frames)
        token_text = "".join(f["text"] for f in frames if f["type"] == "token")
        assert token_text.strip(), "a fallback token frame must carry non-empty text"
        done = frames[-1]
        assert done["type"] == "done"
        assert done["empty_generation_fallback"] is True
        assert done["degraded"] is True

    async def test_short_add_task_stream_yields_actions_without_forcing_plan(
        self, container
    ) -> None:
        """Heuristic, not request.plan=True: a short imperative still reaches execute."""
        await container.startup()
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(text=json.dumps({
            "steps": [{
                "kind": "tool",
                "tool": "propose_task",
                "description": "Propose buying milk",
                "arguments": {"title": "Buy milk", "priority": "medium"},
            }]
        })))

        frames = [
            frame async for frame in container.kernel.stream(
                _request("add a task to buy milk")
            )
        ]
        actions = next(f for f in frames if f["type"] == "actions")
        assert actions["actions"][0]["type"] == "add_task"
        assert actions["actions"][0]["params"]["title"] == "Buy milk"
        assert any(f["type"] == "step" for f in frames)

    async def test_short_fact_question_stream_skips_planning(self, container) -> None:
        await container.startup()
        plan_calls = {"n": 0}
        original = container.kernel._deps.planner.plan

        async def wrapped(*args, **kwargs):  # noqa: ANN002, ANN003
            plan_calls["n"] += 1
            return await original(*args, **kwargs)

        container.kernel._deps.planner.plan = wrapped

        frames = [
            frame async for frame in container.kernel.stream(
                _request("what is my leave balance?")
            )
        ]
        assert plan_calls["n"] == 0
        assert not any(f["type"] in {"step", "actions"} for f in frames)
        assert frames[-1]["type"] == "done"

    async def test_stream_final_generation_does_not_advertise_tools(self, container) -> None:
        """Regression: advertising tools on the final generation pass made local models answer
        with native tool_calls and no text, which ADR-006 never executes."""
        await container.startup()
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(text="The engineer who built AURA knows the system's design."))

        frames = [
            frame async for frame in container.kernel.stream(
                _request("The engineer who created you knows.")
            )
        ]

        done = frames[-1]
        assert done["type"] == "done"
        assert done.get("empty_generation_fallback") is not True
        token_text = "".join(f["text"] for f in frames if f["type"] == "token")
        assert "wasn't able to put together" not in token_text
        assert echo.calls
        assert echo.calls[-1].tools == ()

    async def test_output_guard_violation_blocks_the_turn_not_a_silent_empty_success(
        self, container
    ) -> None:
        """run()'s output-guard step (unlike stream()'s) has no inline try/except around
        guard_output -- confirm that's fine because run() already catches SafetyViolationError
        one level up and reports BLOCKED, rather than ever returning a "successful" empty
        answer."""
        from aura_core.domain.errors import SafetyViolationError

        await container.startup()

        async def blocking_guard_output(*args, **kwargs):  # noqa: ANN002, ANN003
            raise SafetyViolationError("blocked", guard="test-guard", action="block")

        container.kernel._deps.safety.guard_output = blocking_guard_output

        result = await container.kernel.run(_request())

        assert result.telemetry.outcome is TurnOutcome.BLOCKED
        assert result.text == ""

