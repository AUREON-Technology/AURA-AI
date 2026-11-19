"""Traces that actually contain something (Phase R2).

`AURA_TRACING_ENABLED` configured an OTLP exporter, but nothing in the codebase ever opened a
span, so turning it on sent an empty stream. A request now produces one HTTP span, with the
router's `llm.generate` attempts and the kernel's `aura.turn` nested under it. These tests read
the real OpenTelemetry spans through the SDK's in-memory exporter.

Spans go to a third-party collector (Grafana Cloud), so the tests also check that they carry
no message text, no user id and no client address.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.trace import StatusCode  # noqa: E402

from aura_core.domain.identifiers import UserId  # noqa: E402
from aura_core.observability.tracing import (  # noqa: E402
    Tracer,
    get_tracer,
    install_tracer,
    record_span,
)

SECRET_MESSAGE = "my passport number is 4411-SECRET"
USER = "user-tracing-7f3a"


@pytest.fixture
def exporter():
    exporter = InMemorySpanExporter()
    tracer = Tracer(service_name="aura-test", enabled=True)
    assert tracer.configure(SimpleSpanProcessor(exporter))
    previous = install_tracer(tracer)
    yield exporter
    install_tracer(previous) if previous is not None else install_tracer(Tracer())


class TestRecord:
    def test_a_finished_span_has_its_duration_and_attributes(self, exporter) -> None:
        record_span("work", duration_ms=250.0, attributes={"a": 1, "skip": None})
        [span] = exporter.get_finished_spans()
        assert span.name == "work"
        assert (span.end_time - span.start_time) == 250_000_000
        assert dict(span.attributes) == {"a": 1}

    def test_an_error_span_is_marked(self, exporter) -> None:
        record_span("failed", duration_ms=1.0, error=True)
        [span] = exporter.get_finished_spans()
        assert span.status.status_code is StatusCode.ERROR

    def test_a_disabled_tracer_records_nothing_and_never_raises(self) -> None:
        Tracer(enabled=False).record("x", duration_ms=1.0, attributes={"a": None})


class TestInstall:
    def test_the_installed_tracer_is_the_process_tracer(self) -> None:
        mine = Tracer(service_name="mine")
        previous = install_tracer(mine)
        try:
            assert get_tracer() is mine
        finally:
            install_tracer(previous or Tracer())

    def test_the_container_installs_its_own(self, container) -> None:
        assert get_tracer() is container.tracer


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId(USER), email="t@example.com", role="User")

    return TestClient(build_app(container, authenticator=authenticator))


def _wait_for(exporter, name: str, timeout_s: float = 2.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        spans = [s for s in exporter.get_finished_spans() if s.name == name]
        if spans:
            return spans
        time.sleep(0.01)
    return []


class TestARequestIsOneTrace:
    async def test_chat_nests_generation_and_turn_under_the_http_span(
        self, container, exporter
    ) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.post(
                "/api/v3/chat",
                json={"message": SECRET_MESSAGE, "plan": False, "use_tools": False},
            )
        assert response.status_code == 200

        [http] = _wait_for(exporter, "POST /api/v3/chat")
        generations = _wait_for(exporter, "llm.generate")
        turns = _wait_for(exporter, "aura.turn")
        assert generations and turns

        assert http.attributes["http.route"] == "/api/v3/chat"
        assert http.attributes["http.response.status_code"] == 200
        trace_id = http.context.trace_id
        for child in [*generations, *turns]:
            assert child.context.trace_id == trace_id
            assert child.parent is not None and child.parent.span_id == http.context.span_id

        [turn] = turns
        assert turn.attributes["aura.route"] == "chat"
        assert turn.attributes["aura.outcome"] == "success"
        assert "aura.stage.generation_ms" in turn.attributes
        assert generations[0].attributes["aura.llm.provider"] == "echo"

    async def test_no_span_carries_content_identity_or_address(self, container, exporter) -> None:
        await container.startup()
        with _client(container) as client:
            client.post(
                "/api/v3/chat",
                json={"message": SECRET_MESSAGE, "plan": False, "use_tools": False},
                headers={"User-Agent": "ua-should-not-appear", "X-Forwarded-For": "203.0.113.9"},
            )
        _wait_for(exporter, "aura.turn")
        everything = " ".join(
            f"{s.name} {dict(s.attributes)}" for s in exporter.get_finished_spans()
        )
        for leaked in ("SECRET", USER, "203.0.113.9", "ua-should-not-appear", "t@example.com"):
            assert leaked not in everything

    async def test_health_and_metrics_are_not_traced(self, container, exporter) -> None:
        await container.startup()
        with _client(container) as client:
            client.get("/health/live")
            client.get("/health/ready")
        assert exporter.get_finished_spans() == ()

    async def test_an_unmatched_path_is_traced_by_template_not_raw_url(
        self, container, exporter
    ) -> None:
        await container.startup()
        with _client(container) as client:
            client.get("/api/v3/no-such-route/1234567?token=abc")
        [span] = exporter.get_finished_spans()
        assert span.name == "GET unmatched"
        assert "1234567" not in str(dict(span.attributes)) and "token" not in span.name
