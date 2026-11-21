"""F-24 / CB-14 / CB-15: degraded turns say so, the stream path is observable, and HTTP has metrics.

Before this, a planner timeout (the planner swallows it and returns a one-step "just answer"
plan) produced a turn that reported plain success, with no counter moving -- and the model,
shown only the user's "add a task", could answer "Done" when nothing ran. ``kernel.stream`` --
the client's only chat path -- recorded no telemetry at all, so admin quality dashboards saw
none of the real users' turns.
"""

from __future__ import annotations

import pytest

from aura_core.domain.errors import AllProvidersFailedError
from aura_core.domain.identifiers import UserId
from aura_core.kernel import TurnRequest
from aura_core.kernel.kernel import PLANNING_FAILED_NOTE
from aura_core.observability.metrics import PROMETHEUS_AVAILABLE, Metrics

needs_prometheus = pytest.mark.skipif(
    not PROMETHEUS_AVAILABLE, reason="prometheus_client is not installed"
)

#: Plans with the default install: "add a task" plus the propose_* tools (see
#: test_planning_execution's ``test_short_add_task_plans_when_personal_os_tools_are_offered``).
ADD_TASK = "add a task to buy milk tomorrow"


class _FailingRouter:
    """The planner's router when the CPU model times out: every call raises."""

    async def complete(self, request, **_):
        raise AllProvidersFailedError("planner generation timed out")


def _request(message: str = ADD_TASK) -> TurnRequest:
    return TurnRequest(message=message, user_id=UserId("test-user"))


def _sample(container, name: str, labels: dict | None = None) -> float:
    value = container.metrics.registry.get_sample_value(name, labels or {})
    return value or 0.0


@pytest.fixture
def container(container):
    """The in-memory container with an enabled, private metric registry, so counter values are
    assertable (the suite's default registry is disabled; see conftest's ``metrics``)."""
    metrics = Metrics(enabled=True)
    container.metrics = metrics
    container.kernel._metrics = metrics
    return container


def _model_saw(container) -> str:
    """Everything the generating model was shown on its most recent call."""
    calls = [
        call for provider in container.router._providers.values()
        for call in getattr(provider, "calls", ())
    ]
    return "\n".join(m.content for m in calls[-1].conversation)


async def _break_the_planner(container) -> None:
    await container.startup()
    assert container.planner is not None, "the default install wires a planner"
    container.planner._router = _FailingRouter()


class TestPlanningFailureIsDegraded:
    async def test_run_marks_the_turn_degraded_with_a_reason(self, container) -> None:
        await _break_the_planner(container)
        result = await container.kernel.run(_request())

        assert result.telemetry.planning_failed is True
        assert result.degraded is True
        assert "planning_failed" in result.telemetry.degraded_reasons
        assert result.telemetry.as_dict()["degraded_reasons"] == ["planning_failed"]
        assert result.tool_calls == ()

    async def test_the_model_is_told_nothing_ran(self, container) -> None:
        """The note reaching the prompt is the thing that stops a false "Done"."""
        await _break_the_planner(container)
        await container.kernel.run(_request())
        assert "no tool ran and no action was taken" in _model_saw(container)

    async def test_stream_reports_degraded_and_why(self, container) -> None:
        await _break_the_planner(container)
        frames = [frame async for frame in container.kernel.stream(_request())]
        done = frames[-1]

        assert done["type"] == "done"
        assert done["degraded"] is True
        assert "planning_failed" in done["degraded_reasons"]
        assert "no tool ran and no action was taken" in _model_saw(container)

    @needs_prometheus
    async def test_the_degraded_counter_increments(self, container) -> None:
        await _break_the_planner(container)
        before = _sample(container, "aura_turn_degraded_total", {"reason": "planning_failed"})
        [frame async for frame in container.kernel.stream(_request())]
        await container.kernel.run(_request())
        after = _sample(container, "aura_turn_degraded_total", {"reason": "planning_failed"})
        assert after - before == 2

    async def test_a_turn_that_needs_no_plan_is_not_degraded_by_it(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(_request("What is the capital of France?"))
        assert result.telemetry.planning_failed is False
        assert "planning_failed" not in result.telemetry.degraded_reasons

    def test_the_note_forbids_claiming_success(self) -> None:
        assert "Do not say that anything was created" in PLANNING_FAILED_NOTE


class TestStreamTelemetry:
    async def test_a_streamed_turn_is_recorded(self, container) -> None:
        await container.startup()
        sink = container.telemetry_sink
        before = len(sink)
        frames = [frame async for frame in container.kernel.stream(_request("hello there"))]
        assert frames[-1]["type"] == "done"
        # The sink write runs after `done` (KAN-110); the record must still land.
        await container.kernel.wait_for_post_turn()
        assert len(sink) == before + 1

    @needs_prometheus
    async def test_a_streamed_turn_feeds_the_turn_metrics(self, container) -> None:
        await container.startup()
        [frame async for frame in container.kernel.stream(_request("hello there"))]

        assert _sample(
            container, "aura_turns_total", {"route": "chat", "outcome": "success"}
        ) == 1
        assert _sample(
            container, "aura_turn_duration_seconds_count", {"route": "chat", "planned": "false"}
        ) == 1
        assert _sample(container, "aura_turns_in_flight") == 0

    @needs_prometheus
    async def test_a_failed_stream_is_recorded_as_an_error(self, container) -> None:
        await container.startup()
        container.router._providers.clear()
        frames = [frame async for frame in container.kernel.stream(_request("hello there"))]
        assert frames[-1]["type"] == "error"

        import asyncio

        await asyncio.sleep(0)  # the failure record is scheduled, as on the run path
        assert _sample(
            container, "aura_turns_total", {"route": "chat", "outcome": "error"}
        ) == 1
        assert _sample(container, "aura_turns_in_flight") == 0


@needs_prometheus
class TestBuckets:
    """Production turns live above 60 s; buckets that stopped there put p95 in +Inf."""

    def _upper_bounds(self, container, name: str) -> list[float]:
        bounds = []
        for metric in container.metrics.registry.collect():
            for sample in metric.samples:
                if sample.name == f"{name}_bucket" and sample.labels["le"] != "+Inf":
                    bounds.append(float(sample.labels["le"]))
        return bounds

    async def test_turn_and_ttft_buckets_reach_the_production_range(self, container) -> None:
        await container.startup()
        [frame async for frame in container.kernel.stream(_request("hello there"))]
        assert max(self._upper_bounds(container, "aura_turn_duration_seconds")) >= 600
        assert max(self._upper_bounds(container, "aura_llm_ttft_seconds")) >= 300


@needs_prometheus
class TestHttpMetrics:
    def _client(self, container):
        from fastapi.testclient import TestClient

        from aura_core.api.v3 import build_app

        async def anonymous(request):
            from aura_core.domain.errors import AuthenticationError

            raise AuthenticationError("no credentials in this test")

        return TestClient(build_app(container, authenticator=anonymous))

    def test_requests_are_counted_by_route_template_and_status(self, container) -> None:
        with self._client(container) as client:
            assert client.get("/health/live").status_code == 200
            client.get("/health/live")
        assert _sample(
            container, "aura_http_requests_total",
            {"method": "GET", "route": "/health/live", "status": "200"},
        ) == 2
        assert _sample(
            container, "aura_http_request_duration_seconds_count",
            {"method": "GET", "route": "/health/live"},
        ) == 2

    def test_an_unknown_path_cannot_mint_a_series(self, container) -> None:
        with self._client(container) as client:
            assert client.get("/no/such/path/abc123").status_code == 404
        assert _sample(
            container, "aura_http_requests_total",
            {"method": "GET", "route": "unmatched", "status": "404"},
        ) == 1
        rendered = container.metrics.render().decode()
        assert "abc123" not in rendered

    def test_a_path_parameter_is_labelled_by_its_template(self, container) -> None:
        with self._client(container) as client:
            client.get("/api/v3/knowledge/graph/entities/zzz-unique-id")
        rendered = container.metrics.render().decode()
        assert "zzz-unique-id" not in rendered
        assert 'route="/api/v3/knowledge/graph/entities/{entity_key}"' in rendered


class TestOutboxGauges:
    async def test_in_memory_outbox_reports_its_backlog(self) -> None:
        from aura_core.adapters.runtime import InMemoryEventOutbox
        from aura_core.domain.events import TurnStarted
        from aura_core.domain.identifiers import CorrelationId, TurnId

        outbox = InMemoryEventOutbox()
        assert (await outbox.stats())["pending"] == 0
        assert (await outbox.stats())["oldest_age_s"] == 0.0

        first = await outbox.enqueue(
            TurnStarted(correlation_id=CorrelationId.new(), turn_id=TurnId.new())
        )
        await outbox.enqueue(TurnStarted(correlation_id=CorrelationId.new(), turn_id=TurnId.new()))
        await outbox.mark_failed(first, error="boom", max_attempts=1)

        stats = await outbox.stats()
        assert stats["pending"] == 1
        assert stats["dead_lettered"] == 1
        assert stats["oldest_age_s"] >= 0.0

    @needs_prometheus
    async def test_a_scrape_refreshes_the_outbox_gauges(self, container) -> None:
        from aura_core.adapters.runtime import InMemoryEventOutbox
        from aura_core.api.v3 import refresh_scrape_gauges
        from aura_core.domain.events import TurnStarted
        from aura_core.domain.identifiers import CorrelationId, TurnId

        outbox = InMemoryEventOutbox()
        container.event_outbox = outbox
        for _ in range(3):
            await outbox.enqueue(
                TurnStarted(correlation_id=CorrelationId.new(), turn_id=TurnId.new())
            )
        await refresh_scrape_gauges(container)
        assert _sample(container, "aura_outbox_pending") == 3
        assert _sample(container, "aura_outbox_dead_lettered") == 0

    async def test_a_broken_outbox_does_not_break_the_scrape(self, container) -> None:
        from aura_core.api.v3 import refresh_scrape_gauges

        class _Down:
            async def stats(self):
                raise ConnectionError("database is down")

        container.event_outbox = _Down()
        await refresh_scrape_gauges(container)  # must not raise
