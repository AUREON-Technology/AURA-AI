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

__all__ = ["Tracer", "get_tracer", "install_tracer", "record_span", "span", "OTEL_AVAILABLE"]

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

    def rename(self, name: str) -> None:
        """Rename the span once something learned later names it better (the HTTP route)."""
        if self._otel_span is not None:  # pragma: no cover - requires otel
            try:
                self._otel_span.update_name(name)
            except Exception:
                pass

    def mark_error(self, description: str = "") -> None:
        if self._otel_span is not None:  # pragma: no cover - requires otel
            try:
                from opentelemetry.trace import Status, StatusCode

                self._otel_span.set_status(Status(StatusCode.ERROR, description or None))
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
        self._provider: Any = None

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
        """Install an OTLP/HTTP exporter. Returns whether it succeeded.

        ``endpoint`` is the full traces URL (``.../v1/traces``). Credentials are not a setting:
        the exporter reads ``OTEL_EXPORTER_OTLP_HEADERS`` (or ``..._TRACES_HEADERS``) itself, so
        a collector's API key lives in the platform's secret store, never in AURA's config.

        Returns rather than raises: a missing collector must degrade tracing, never prevent the
        service from starting.
        """
        if not OTEL_AVAILABLE or not endpoint:  # pragma: no cover
            return False
        try:  # pragma: no cover - requires otel packages
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            return self.configure(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except Exception:
            return False

    def configure(self, processor: Any) -> bool:
        """Send spans through ``processor`` (an OpenTelemetry ``SpanProcessor``).

        The provider is this tracer's own rather than OpenTelemetry's global one, which can be
        set only once per process. Nothing else in AURA reads the global provider, and parent
        spans still nest through OpenTelemetry's context, which is per task.
        """
        if not OTEL_AVAILABLE:  # pragma: no cover
            return False
        try:
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
            from opentelemetry.sdk.trace import TracerProvider

            provider = TracerProvider(resource=Resource({SERVICE_NAME: self.service_name}))
            provider.add_span_processor(processor)
            self._provider = provider
            self._tracer = provider.get_tracer(self.service_name)
            self.enabled = True
            return True
        except Exception:  # pragma: no cover - defensive
            return False

    def record(
        self,
        name: str,
        *,
        duration_ms: float,
        attributes: Mapping[str, Any] | None = None,
        error: bool = False,
    ) -> None:
        """Record a span for work that has just finished and took ``duration_ms``.

        For code that measures its own work and reports it as a value (the router's
        ``GenerationTiming``, the kernel's ``TurnTelemetry``) rather than running inside a
        ``with`` block. The span ends now, starts ``duration_ms`` earlier, and nests under
        whatever span is current, normally the HTTP request's. ``None`` attributes are dropped:
        OpenTelemetry refuses them.
        """
        if self._tracer is None:
            return
        try:  # pragma: no cover - exercised only with otel installed
            end = time.time_ns()
            start = end - int(max(duration_ms, 0.0) * 1_000_000)
            clean = {k: v for k, v in (attributes or {}).items() if v is not None}
            otel_span = self._tracer.start_span(name, start_time=start, attributes=clean)
            if error:
                from opentelemetry.trace import Status, StatusCode

                otel_span.set_status(Status(StatusCode.ERROR))
            otel_span.end(end_time=end)
        except Exception:
            pass


_tracer: Tracer | None = None


def get_tracer(*, service_name: str = "aura-core", enabled: bool = False) -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer(service_name=service_name, enabled=enabled)
    return _tracer


def install_tracer(tracer: Tracer) -> Tracer | None:
    """Make ``tracer`` the process tracer and return the one it replaces.

    :func:`get_tracer` creates the process tracer on first use with whatever arguments that
    first caller passed, so a container asking for an *enabled* tracer after anything else had
    touched it got the earlier, disabled one. The container installs its own instead.
    """
    global _tracer
    previous, _tracer = _tracer, tracer
    return previous


def record_span(
    name: str,
    *,
    duration_ms: float,
    attributes: Mapping[str, Any] | None = None,
    error: bool = False,
) -> None:
    """Module-level convenience over :meth:`Tracer.record`."""
    get_tracer().record(name, duration_ms=duration_ms, attributes=attributes, error=error)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[SpanTimer]:
    """Module-level convenience over the process tracer."""
    with get_tracer().span(name, **attributes) as timer:
        yield timer


def annotate(timer: SpanTimer, attributes: Mapping[str, Any]) -> None:
    for key, value in attributes.items():
        timer.set(key, value)
