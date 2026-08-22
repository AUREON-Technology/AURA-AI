"""AURA-019: Prometheus multiprocess aggregation vs private-registry isolation."""

from __future__ import annotations

import os

import pytest

prometheus_client = pytest.importorskip("prometheus_client")


def _sample_value(body: str, metric: str, **labels: str) -> float | None:
    for line in body.splitlines():
        if not line.startswith(metric + "{"):
            continue
        if any(f'{key}="{value}"' not in line for key, value in labels.items()):
            continue
        return float(line.rsplit(" ", 1)[-1])
    return None


@pytest.mark.skipif(
    bool(os.environ.get("PROMETHEUS_MULTIPROC_DIR") or os.environ.get("prometheus_multiproc_dir")),
    reason="host already opted into multiprocess; isolation test would not be valid",
)
def test_private_registry_when_multiproc_unset() -> None:
    from aura_core.observability.metrics import Metrics, PROMETHEUS_AVAILABLE, reset_metrics

    if not PROMETHEUS_AVAILABLE:
        pytest.skip("prometheus_client not installed")

    reset_metrics()
    try:
        first = Metrics(enabled=True, namespace="aura")
        second = Metrics(enabled=True, namespace="aura")
        first.turns_total.labels(route="chat", outcome="ok").inc(5)
        first_body = first.render().decode("utf-8")
        second_body = second.render().decode("utf-8")
        assert _sample_value(first_body, "aura_turns_total", route="chat", outcome="ok") == 5.0
        # Unused labelsets are omitted from exposition; either way the increment
        # must not leak into the second private registry.
        assert (_sample_value(second_body, "aura_turns_total", route="chat", outcome="ok") or 0.0) == 0.0
    finally:
        reset_metrics()


def test_multiprocess_aggregates_counters_from_two_pids(tmp_path, monkeypatch) -> None:
    if os.name == "nt":
        pytest.skip("prometheus_client mmap files are not reliable on Windows; CI runs this on Linux")
    from prometheus_client import values
    from prometheus_client.values import MultiProcessValue

    from aura_core.observability.metrics import Metrics, PROMETHEUS_AVAILABLE, reset_metrics

    if not PROMETHEUS_AVAILABLE:
        pytest.skip("prometheus_client not installed")

    original = values.ValueClass
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    reset_metrics()
    try:
        values.ValueClass = MultiProcessValue(lambda: 101)
        first = Metrics(enabled=True, namespace="aura")
        assert first._multiproc_dir == str(tmp_path)
        first.turns_total.labels(route="chat", outcome="ok").inc()

        values.ValueClass = MultiProcessValue(lambda: 202)
        second = Metrics(enabled=True, namespace="aura")
        second.turns_total.labels(route="chat", outcome="ok").inc()

        body = first.render().decode("utf-8")
        assert _sample_value(body, "aura_turns_total", route="chat", outcome="ok") == 2.0
    finally:
        values.ValueClass = original
        reset_metrics()
