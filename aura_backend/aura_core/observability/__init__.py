"""Observability: structured logging, the AI metric catalogue, and tracing.

Per ADR-009, AI telemetry is a first-class output of the kernel rather than a logging
afterthought. The kernel produces a :class:`~aura_core.domain.telemetry.TurnTelemetry` value;
this package is what turns that into Prometheus series, log lines, and spans -- which is why the
domain layer needs no metrics dependency.
"""

from .logging import (
    LogContext,
    bind_context,
    configure_logging,
    correlation_id_var,
    current_context,
    get_logger,
)
from .metrics import PROMETHEUS_AVAILABLE, Metrics, get_metrics, reset_metrics
from .tracing import Tracer, get_tracer, install_tracer, record_span, span

__all__ = [
    "configure_logging",
    "get_logger",
    "LogContext",
    "bind_context",
    "current_context",
    "correlation_id_var",
    "Metrics",
    "get_metrics",
    "reset_metrics",
    "PROMETHEUS_AVAILABLE",
    "Tracer",
    "get_tracer",
    "install_tracer",
    "record_span",
    "span",
]
