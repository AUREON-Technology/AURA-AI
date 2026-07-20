"""Observability (Doc/V2_BLUEPRINT.md §10) — structured logging, error
tracking, and distributed tracing. Every piece here is opt-in via config
and no-ops cleanly when unconfigured, so a plain local `python main.py`
with no `.env` observability settings behaves exactly as before this file
existed: text logs to stdout, no Sentry, no trace export. This mirrors the
graceful-degradation pattern already used throughout the codebase (Qdrant
Cloud → local disk, Redis → in-process fallback, Postgres → 503).
"""
import json
import logging
import sys

from config import settings

logger = logging.getLogger("aura_backend.observability")


class _JsonFormatter(logging.Formatter):
    """Minimal structured-log formatter — one JSON object per line, the
    format every log aggregator (Cloud Logging, Datadog, ELK) expects.
    Deliberately hand-rolled instead of adding a python-json-logger
    dependency for ~20 lines of formatting logic."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Any extra= fields passed to a log call get merged in verbatim,
        # e.g. logger.info("sync push", extra={"user_id": ..., "count": ...})
        reserved = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())
        for key, value in record.__dict__.items():
            if key not in reserved and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Call once at startup, before any other logging.basicConfig call."""
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def configure_sentry() -> None:
    if not settings.sentry_dsn:
        logger.info("SENTRY_DSN not configured — error tracking disabled.")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            integrations=[FastApiIntegration()],
            traces_sample_rate=0.1,
            send_default_pii=False,  # never send request bodies/headers by default — this backend handles auth tokens and encrypted user data
        )
        logger.info("Sentry error tracking initialized.")
    except Exception as e:
        logger.warning(f"Failed to initialize Sentry: {e}")


def configure_tracing(app) -> None:
    """Instruments the FastAPI app for distributed tracing — but only if
    OTEL_EXPORTER_OTLP_ENDPOINT is actually configured. Genuinely opt-in,
    matching Sentry's pattern: no endpoint means no tracer provider, no
    instrumentation, and no console-exporter noise flooding local dev runs
    or (worse) the test suite's stdout with trace JSON for every request
    TestClient makes. Console-exporting "by default" would look like it's
    working while providing no real value outside this one process."""
    if not settings.otel_exporter_otlp_endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not configured — tracing disabled.")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource(attributes={SERVICE_NAME: settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        logger.info(f"OpenTelemetry exporting traces to {settings.otel_exporter_otlp_endpoint}")
    except Exception as e:
        logger.warning(f"Failed to configure OpenTelemetry tracing: {e}")
