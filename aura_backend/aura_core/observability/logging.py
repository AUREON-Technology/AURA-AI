"""Structured logging with request correlation.

Two things this provides that the existing setup does not:

1. **Correlation without plumbing.** ``contextvars`` carry the correlation id, user reference,
   and route through the whole async call stack, so every log line inside a turn is joinable
   without threading a logger argument through fifteen functions.
2. **Content safety by default.** :class:`ContentRedactingFilter` strips values for keys known
   to hold prompts, documents, memories, or secrets before they reach a handler. Logging is the
   most common accidental exfiltration path in an LLM system, and "remember not to log the
   prompt" is not a control.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

__all__ = [
    "configure_logging",
    "get_logger",
    "LogContext",
    "bind_context",
    "current_context",
    "correlation_id_var",
]

correlation_id_var: ContextVar[str] = ContextVar("aura_correlation_id", default="")
_context_var: ContextVar[dict[str, Any]] = ContextVar("aura_log_context", default={})

# Keys whose values must never be logged. Matched case-insensitively against the *key*, so a
# nested ``{"api_key": ...}`` inside an ``extra`` dict is caught regardless of where it came from.
_SENSITIVE_KEYS = frozenset(
    {
        "prompt", "prompts", "content", "text", "message_content", "completion", "response_text",
        "document", "documents", "chunk", "chunks", "memory", "memories", "fact", "query",
        "password", "secret", "token", "access_token", "refresh_token", "id_token", "api_key",
        "apikey", "authorization", "credentials", "private_key", "session_key", "cookie",
    }
)

# Reserved LogRecord attributes; anything else on the record is a caller-supplied ``extra``.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{16,}\b"), "[redacted:api-key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"), "[redacted:google-key]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "[redacted:github-token]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "[redacted:jwt]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[redacted:private-key]"),
)


class LogContext:
    """Bind contextual fields for the duration of a block.

    ``with LogContext(correlation_id=..., user_ref=...):`` -- fields appear on every log record
    emitted inside, including from code that knows nothing about logging context. Restores the
    previous context on exit, so nesting works.
    """

    __slots__ = ("_fields", "_token", "_corr_token")

    def __init__(self, **fields: Any) -> None:
        self._fields = {k: v for k, v in fields.items() if v is not None}
        self._token: Any = None
        self._corr_token: Any = None

    def __enter__(self) -> "LogContext":
        merged = {**_context_var.get(), **self._fields}
        self._token = _context_var.set(merged)
        if "correlation_id" in self._fields:
            self._corr_token = correlation_id_var.set(str(self._fields["correlation_id"]))
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._token is not None:
            _context_var.reset(self._token)
        if self._corr_token is not None:
            correlation_id_var.reset(self._corr_token)


def bind_context(**fields: Any) -> None:
    """Add fields to the current context without a block. Use inside a task that owns its
    context; prefer :class:`LogContext` where scoping matters."""
    _context_var.set({**_context_var.get(), **{k: v for k, v in fields.items() if v is not None}})


def current_context() -> Mapping[str, Any]:
    return dict(_context_var.get())


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        for pattern, replacement in _SECRET_PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, Mapping):
        return {k: ("[redacted]" if k.lower() in _SENSITIVE_KEYS else _redact_value(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


class ContentRedactingFilter(logging.Filter):
    """Drops sensitive ``extra`` fields and scrubs secret-shaped substrings from messages.

    A filter rather than a formatter concern: filters run before *any* handler, so a deployment
    that adds a second handler (file, syslog, Sentry breadcrumb) cannot accidentally bypass it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(record.__dict__):
            if key in _RESERVED:
                continue
            if key.lower() in _SENSITIVE_KEYS:
                record.__dict__[key] = "[redacted]"
            else:
                record.__dict__[key] = _redact_value(record.__dict__[key])
        if isinstance(record.msg, str):
            record.msg = _redact_value(record.msg)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line -- what every aggregator expects.

    Hand-rolled rather than pulling in ``python-json-logger``: it is thirty lines, and the
    dependency would be the only thing standing between this package and a stdlib-only install.
    """

    def __init__(self, *, service: str = "aura-core") -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "message": record.getMessage(),
        }
        context = _context_var.get()
        if context:
            payload.update(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable format for local development, with the correlation id inline."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        correlation = correlation_id_var.get()
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED and k not in {"message", "asctime"}
        }
        suffix = ""
        if correlation:
            suffix += f" [cid={correlation[:8]}]"
        if extras:
            suffix += " " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        return base + suffix


def configure_logging(
    *, level: str = "INFO", fmt: str = "json", service: str = "aura-core"
) -> None:
    """Install handlers on the root logger. Call once, at startup, before anything else logs."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service=service) if fmt == "json" else TextFormatter())
    handler.addFilter(ContentRedactingFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # These libraries log a line per request at INFO, which drowns the signal at any real
    # traffic level and can echo URLs containing tokens.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "qdrant_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"aura.{name}" if not name.startswith("aura") else name)


def iter_sensitive_keys() -> Iterator[str]:
    """Exposed for the test that asserts the redaction list covers every field name the
    telemetry and safety modules can produce."""
    return iter(sorted(_SENSITIVE_KEYS))
