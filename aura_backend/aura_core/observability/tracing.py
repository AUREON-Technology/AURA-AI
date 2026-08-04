"""Tracing with a stdlib fallback.

When OpenTelemetry is installed and configured, spans are real. When it is not, :func:`span`
still works: it times the block and returns the duration, which is what the pipeline needs for
:class:`~aura_core.domain.telemetry.StageTiming` regardless of whether an OTLP collector exists.

That dual behaviour is deliberate -- stage timings are a *product* requirement (ADR-009 measures
pipeline overhead), while distributed tracing is an *operations* choice. Coupling them would
make timing data unavailable in the default configuration.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

__all__ = ["Tracer", "get_tracer", "span", "OTEL_AVAILABLE"]

try:  # pragma: no cover - depends on environment
    from opentelemetry import trace as _otel_trace

    OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _otel_trace = None  # type: ignore[assignment]
    OTEL_AVAILABLE = False


class SpanTimer:
    """Handle yielded by :func:`span`. Carries elapsed time and accepts attributes."""

    __slots__ = ("_start", "_end", "_otel_span", "attributes")

    def __init__(self, otel_span: Any = None) -> None:
        self._start = time.perf_counter()
        self._end: float | None = None
        self._otel_span = otel_span
        self.attributes: dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self.attributes[key] = value
        if self._otel_span is not None:  # pragma: no cover - requires otel
            try:
                self._otel_span.set_attribute(key, value)
            except Exception:
                pass

    def stop(self) -> None:
        if self._end is None:
            self._end = time.perf_counter()

    @property
    def elapsed_ms(self) -> float:
        end = self._end if self._end is not None else time.perf_counter()
        return (end - self._start) * 1000.0


class Tracer:
    """Thin wrapper so call sites never branch on whether OTel is present."""

    def __init__(self, *, service_name: str = "aura-core", enabled: bool = False) -> None:
        self.service_name = service_name
        self.enabled = enabled and OTEL_AVAILABLE
        self._tracer = (
            _otel_trace.get_tracer(service_name) if self.enabled and _otel_trace else None
        )

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[SpanTimer]:
        if self._tracer is None:
            timer = SpanTimer()
            for key, value in attributes.items():
                timer.attributes[key] = value
            try:
                yield timer
            finally:
                timer.stop()
            return

        with self._tracer.start_as_current_span(name) as otel_span:  # pragma: no cover
            timer = SpanTimer(otel_span)
            for key, value in attributes.items():
                timer.set(key, value)
            try:
                yield timer
            finally:
                timer.stop()

    def configure_exporter(self, endpoint: str) -> bool:
        """Install an OTLP exporter. Returns whether it succeeded.

        Returns rather than raises: a missing collector must degrade tracing, never prevent the
        service from starting.
        """
        if not OTEL_AVAILABLE or not endpoint:  # pragma: no cover
            return False
        try:  # pragma: no cover - requires otel packages
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=Resource({SERVICE_NAME: self.service_name}))
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            _otel_trace.set_tracer_provider(provider)
            self._tracer = _otel_trace.get_tracer(self.service_name)
            self.enabled = True
            return True
        except Exception:
            return False


_tracer: Tracer | None = None


def get_tracer(*, service_name: str = "aura-core", enabled: bool = False) -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer(service_name=service_name, enabled=enabled)
    return _tracer


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[SpanTimer]:
    """Module-level convenience over the process tracer."""
    with get_tracer().span(name, **attributes) as timer:
        yield timer


def annotate(timer: SpanTimer, attributes: Mapping[str, Any]) -> None:
    for key, value in attributes.items():
        timer.set(key, value)
