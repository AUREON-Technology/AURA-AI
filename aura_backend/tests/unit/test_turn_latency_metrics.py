"""KAN-108: the turn-duration histogram separates planned from unplanned turns.

A planned turn pays a planning generation (and any tool calls) before it answers, so its latency
is a different distribution. In one series, a p95 moves with the share of planned turns rather
than with how fast either kind is. The buckets already reach 600 s (CB-15); this pins both.
"""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import CorrelationId, TurnId, UserId
from aura_core.domain.telemetry import StageTiming, TurnTelemetry
from aura_core.kernel import TurnRequest
from aura_core.observability.metrics import PROMETHEUS_AVAILABLE, Metrics

pytestmark = pytest.mark.skipif(
    not PROMETHEUS_AVAILABLE, reason="prometheus_client is not installed"
)

#: Plans with the default install (the propose_* Personal-OS tools are offered).
ADD_TASK = "add a task to buy milk tomorrow"


@pytest.fixture
def container(container):
    metrics = Metrics(enabled=True)
    container.metrics = metrics
    container.kernel._metrics = metrics
    return container


def _count(metrics: Metrics, planned: str) -> float:
    value = metrics.registry.get_sample_value(
        "aura_turn_duration_seconds_count", {"route": "chat", "planned": planned}
    )
    return value or 0.0


def _telemetry(**kwargs) -> TurnTelemetry:
    return TurnTelemetry(correlation_id=CorrelationId.new(), turn_id=TurnId.new(), **kwargs)


class TestPlannedLabel:
    def test_a_turn_with_no_planning_stage_is_unplanned(self) -> None:
        metrics = Metrics(enabled=True)
        metrics.observe_turn(_telemetry(total_ms=1200.0))
        assert _count(metrics, "false") == 1
        assert _count(metrics, "true") == 0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"plan_steps": 2},
            {"planning_failed": True},
            # A plan that came back as one RESPOND step still cost a planning generation.
            {"timings": StageTiming(planning=850.0)},
        ],
    )
    def test_any_trace_of_planning_makes_it_planned(self, kwargs) -> None:
        metrics = Metrics(enabled=True)
        metrics.observe_turn(_telemetry(total_ms=1200.0, **kwargs))
        assert _count(metrics, "true") == 1
        assert _count(metrics, "false") == 0

    async def test_the_kernel_records_both_kinds(self, container) -> None:
        await container.startup()
        user = UserId("latency-user")
        await container.kernel.run(
            TurnRequest(message="What is the capital of France?", user_id=user)
        )
        await container.kernel.run(TurnRequest(message=ADD_TASK, user_id=user))
        assert _count(container.metrics, "false") == 1
        assert _count(container.metrics, "true") == 1

    def test_buckets_reach_past_a_minute(self) -> None:
        metrics = Metrics(enabled=True)
        metrics.observe_turn(_telemetry(total_ms=1.0))
        bounds = [
            float(sample.labels["le"])
            for family in metrics.registry.collect()
            for sample in family.samples
            if sample.name == "aura_turn_duration_seconds_bucket" and sample.labels["le"] != "+Inf"
        ]
        assert max(bounds) >= 600
        assert any(60 < b < 600 for b in bounds)
