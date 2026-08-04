"""The AI metric catalogue (ADR-009).

One module owns every metric name, so dashboards and alerts are derived from a catalogue rather
than invented per panel, and so cardinality is reviewable in one place.

``prometheus_client`` is **optional**. When it is absent every metric becomes a no-op with the
same interface, which keeps ``aura_core`` importable on bare CPython and keeps the test suite
free of a metrics dependency. This is the standard adapter pattern applied to instrumentation.

Cardinality rules, enforced by review and by the label sets below:
  * ``user_id`` is never a label. Per-user numbers live in the telemetry table, not in Prometheus.
  * ``model``/``provider`` are bounded by configuration (single digits).
  * free-form strings (error messages, tool arguments, queries) never become labels.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

__all__ = ["Metrics", "get_metrics", "reset_metrics", "PROMETHEUS_AVAILABLE"]

try:  # pragma: no cover - exercised implicitly by whichever branch the environment takes
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    CollectorRegistry = None  # type: ignore[assignment]
    Counter = Gauge = Histogram = None  # type: ignore[assignment]
    generate_latest = None  # type: ignore[assignment]
    PROMETHEUS_AVAILABLE = False


# Buckets chosen for what is actually being measured rather than the library defaults.
# Pipeline overhead is sub-second and its interesting range is 1-250 ms; end-to-end turns and
# time-to-first-token live in seconds.
_MS_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
_TURN_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0)
_TTFT_BUCKETS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.5, 3.0, 6.0, 12.0)
_TOKEN_BUCKETS = (64, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768)
_SCORE_BUCKETS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


class _NoopMetric:
    """Stand-in with the prometheus_client surface, doing nothing.

    Returning ``self`` from ``labels()`` means call sites need no branching -- the decision to
    instrument is made once, here, not at every measurement point.
    """

    __slots__ = ()

    def labels(self, *args: Any, **kwargs: Any) -> "_NoopMetric":
        return self

    def inc(self, amount: float = 1) -> None:
        return None

    def dec(self, amount: float = 1) -> None:
        return None

    def set(self, value: float) -> None:
        return None

    def observe(self, value: float) -> None:
        return None


_NOOP = _NoopMetric()


class Metrics:
    """The catalogue.

    Constructed once and injected. A private registry (rather than the global default) means
    tests can build a fresh instance per case, and means a second ``Metrics`` in the same
    process -- a Celery worker importing the same module -- does not raise duplicate-timeseries
    errors.
    """

    def __init__(self, *, enabled: bool = True, namespace: str = "aura") -> None:
        self.enabled = enabled and PROMETHEUS_AVAILABLE
        self.namespace = namespace
        self.registry = CollectorRegistry() if self.enabled else None
        self._build()

    # -- construction helpers ----------------------------------------------------------

    def _counter(self, name: str, doc: str, labels: Sequence[str] = ()) -> Any:
        if not self.enabled:
            return _NOOP
        return Counter(f"{self.namespace}_{name}", doc, list(labels), registry=self.registry)

    def _gauge(self, name: str, doc: str, labels: Sequence[str] = ()) -> Any:
        if not self.enabled:
            return _NOOP
        return Gauge(f"{self.namespace}_{name}", doc, list(labels), registry=self.registry)

    def _histogram(
        self, name: str, doc: str, labels: Sequence[str] = (), buckets: Sequence[float] = _MS_BUCKETS
    ) -> Any:
        if not self.enabled:
            return _NOOP
        return Histogram(
            f"{self.namespace}_{name}", doc, list(labels), buckets=tuple(buckets), registry=self.registry
        )

    def _build(self) -> None:
        # -- turns ---------------------------------------------------------------------
        self.turns_total = self._counter(
            "turns_total", "Completed turns by route and outcome.", ("route", "outcome")
        )
        self.turn_duration = self._histogram(
            "turn_duration_seconds", "End-to-end turn duration.", ("route",), _TURN_BUCKETS
        )
        self.stage_duration = self._histogram(
            "stage_duration_seconds", "Duration of one pipeline stage.", ("stage",), _MS_BUCKETS
        )
        self.turn_degraded_total = self._counter(
            "turn_degraded_total",
            "Turns that completed on a degraded path (fallback, taint, empty retrieval, tool error).",
            ("reason",),
        )
        self.turns_in_flight = self._gauge("turns_in_flight", "Turns currently executing.")

        # -- llm -----------------------------------------------------------------------
        self.llm_requests_total = self._counter(
            "llm_requests_total", "Provider calls by outcome.", ("provider", "model", "outcome")
        )
        self.llm_ttft = self._histogram(
            "llm_ttft_seconds", "Time to first token.", ("provider", "model"), _TTFT_BUCKETS
        )
        self.llm_duration = self._histogram(
            "llm_duration_seconds", "Full generation duration.", ("provider", "model"), _TURN_BUCKETS
        )
        self.llm_tokens_total = self._counter(
            "llm_tokens_total", "Tokens consumed.", ("provider", "model", "direction")
        )
        self.llm_prompt_tokens = self._histogram(
            "llm_prompt_tokens", "Prompt size distribution.", ("route",), _TOKEN_BUCKETS
        )
        self.llm_cost_usd_total = self._counter(
            "llm_cost_usd_total", "Estimated spend.", ("provider", "model")
        )
        self.llm_fallbacks_total = self._counter(
            "llm_fallbacks_total", "Failovers between providers.", ("from_provider", "reason")
        )
        self.llm_inflight = self._gauge(
            "llm_inflight", "In-flight generations per provider.", ("provider",)
        )
        self.llm_queue_depth = self._gauge(
            "llm_queue_depth", "Requests waiting for a provider slot.", ("provider",)
        )
        self.provider_healthy = self._gauge(
            "provider_healthy", "1 when the circuit breaker is closed.", ("provider",)
        )
        self.provider_circuit_opens_total = self._counter(
            "provider_circuit_opens_total", "Circuit breaker openings.", ("provider",)
        )

        # -- cache ---------------------------------------------------------------------
        self.cache_lookups_total = self._counter(
            "cache_lookups_total", "Cache lookups.", ("cache", "tier", "result")
        )
        self.cache_entries = self._gauge("cache_entries", "Entries held in a cache.", ("cache",))

        # -- retrieval -----------------------------------------------------------------
        self.retrieval_total = self._counter(
            "retrieval_total", "Retrieval operations.", ("strategy", "result")
        )
        self.retrieval_duration = self._histogram(
            "retrieval_duration_seconds", "Retrieval latency.", ("strategy",), _MS_BUCKETS
        )
        self.retrieval_top_score = self._histogram(
            "retrieval_top_score", "Best score of each retrieval.", ("strategy",), _SCORE_BUCKETS
        )
        self.retrieval_empty_total = self._counter(
            "retrieval_empty_total",
            "Retrievals that searched but returned nothing above threshold.",
        )
        self.retrieval_returned = self._histogram(
            "retrieval_returned_chunks", "Chunks returned per retrieval.", (), (0, 1, 2, 3, 5, 8, 13, 21)
        )

        # -- embeddings ----------------------------------------------------------------
        self.embedding_requests_total = self._counter(
            "embedding_requests_total", "Embedding batches.", ("provider", "outcome")
        )
        self.embedding_duration = self._histogram(
            "embedding_duration_seconds", "Embedding batch latency.", ("provider",), _MS_BUCKETS
        )
        self.embedding_failures_total = self._counter(
            "embedding_failures_total", "Embedding failures.", ("provider", "reason")
        )
        self.embedding_texts_total = self._counter(
            "embedding_texts_total", "Texts embedded.", ("provider",)
        )

        # -- memory --------------------------------------------------------------------
        self.memory_recalls_total = self._counter(
            "memory_recalls_total", "Memory recall operations.", ("kind",)
        )
        self.memory_recalled_records = self._histogram(
            "memory_recalled_records", "Records returned per recall.", (), (0, 1, 2, 3, 5, 8, 13)
        )
        self.memory_writes_total = self._counter(
            "memory_writes_total", "Memory records written.", ("kind",)
        )
        self.memory_records = self._gauge("memory_records", "Stored records by kind.", ("kind",))
        self.memory_consolidation_total = self._counter(
            "memory_consolidation_total", "Consolidation outcomes.", ("action",)
        )

        # -- knowledge -----------------------------------------------------------------
        self.documents_ingested_total = self._counter(
            "documents_ingested_total", "Documents ingested.", ("source_type", "outcome")
        )
        self.chunks_indexed_total = self._counter("chunks_indexed_total", "Chunks written to an index.")
        self.knowledge_generation = self._gauge(
            "knowledge_generation", "Current promoted knowledge generation number."
        )
        self.knowledge_age_seconds = self._gauge(
            "knowledge_age_seconds", "Seconds since the knowledge index was last promoted."
        )
        self.refresh_last_success_timestamp = self._gauge(
            "refresh_last_success_timestamp_seconds", "Unix time of the last successful refresh."
        )
        self.refresh_duration = self._histogram(
            "refresh_duration_seconds", "Nightly refresh duration.", (), (1, 10, 30, 60, 300, 900, 3600)
        )
        self.refresh_gate_failures_total = self._counter(
            "refresh_gate_failures_total", "Promotion gate failures.", ("gate",)
        )
        self.index_switches_total = self._counter(
            "index_switches_total", "Alias switches.", ("alias", "reason")
        )

        # -- safety --------------------------------------------------------------------
        self.guard_verdicts_total = self._counter(
            "guard_verdicts_total", "Guard verdicts.", ("guard", "stage", "action")
        )
        self.guard_duration = self._histogram(
            "guard_duration_seconds", "Guard evaluation latency.", ("stage",), _MS_BUCKETS
        )
        self.turns_tainted_total = self._counter(
            "turns_tainted_total", "Turns whose context contained quarantined content."
        )
        self.ungrounded_claims_total = self._counter(
            "ungrounded_claims_total", "Claims with no supporting citation (hallucination proxy)."
        )
        self.groundedness_score = self._histogram(
            "groundedness_score", "Groundedness per grounded turn.", (), _SCORE_BUCKETS
        )
        self.insecure_mode = self._gauge(
            "insecure_mode",
            "1 when an auth/attestation/safety bypass is active. Alert on this being non-zero.",
            ("bypass",),
        )

        # -- tools ---------------------------------------------------------------------
        self.tool_calls_total = self._counter(
            "tool_calls_total", "Tool invocations.", ("tool", "outcome")
        )
        self.tool_duration = self._histogram(
            "tool_duration_seconds", "Tool execution latency.", ("tool",), _TURN_BUCKETS
        )
        self.tool_denied_total = self._counter(
            "tool_denied_total", "Tool calls refused by policy.", ("tool", "reason")
        )

        # -- planning / reasoning ------------------------------------------------------
        self.plans_created_total = self._counter("plans_created_total", "Plans created.", ("agent",))
        self.plan_steps = self._histogram(
            "plan_steps", "Steps per plan.", (), (1, 2, 3, 5, 8, 13, 21)
        )
        self.plan_revisions_total = self._counter(
            "plan_revisions_total", "Replanning events.", ("reason",)
        )
        self.reasoning_strategy_total = self._counter(
            "reasoning_strategy_total", "Turns per reasoning strategy.", ("strategy",)
        )
        self.confidence_score = self._histogram(
            "confidence_score", "Calibrated answer confidence.", ("strategy",), _SCORE_BUCKETS
        )
        self.verification_total = self._counter(
            "verification_total", "Verification outcomes.", ("outcome",)
        )

        # -- evaluation / drift --------------------------------------------------------
        self.eval_score = self._gauge("eval_score", "Latest evaluation score.", ("suite", "metric"))
        self.eval_runs_total = self._counter("eval_runs_total", "Evaluation runs.", ("suite", "outcome"))
        self.drift_score = self._gauge("drift_score", "Population stability index by signal.", ("signal",))
        self.feedback_total = self._counter("feedback_total", "User feedback.", ("rating",))

        # -- platform ------------------------------------------------------------------
        self.rate_limit_rejections_total = self._counter(
            "rate_limit_rejections_total", "Requests rejected by the limiter.", ("scope",)
        )
        self.budget_exhausted_total = self._counter(
            "budget_exhausted_total", "Budget exhaustions.", ("scope", "budget")
        )
        self.event_handler_failures_total = self._counter(
            "event_handler_failures_total", "Event subscribers that raised.", ("event",)
        )
        self.dependency_up = self._gauge(
            "dependency_up", "1 when a dependency's health probe passes.", ("dependency",)
        )
        self.gpu_memory_used_bytes = self._gauge(
            "gpu_memory_used_bytes", "GPU memory in use, when a probe is available.", ("device",)
        )
        self.gpu_utilisation = self._gauge(
            "gpu_utilisation_ratio", "GPU utilisation 0-1, when a probe is available.", ("device",)
        )

    # -- convenience -------------------------------------------------------------------

    def observe_turn(self, telemetry: Any) -> None:
        """Fan one :class:`~aura_core.domain.telemetry.TurnTelemetry` out to the catalogue.

        Kept here rather than in the kernel so the domain layer stays free of any metrics
        dependency (ADR-009: engines return timings, the observability layer records them).
        """
        route = telemetry.route
        self.turns_total.labels(route=route, outcome=str(telemetry.outcome)).inc()
        self.turn_duration.labels(route=route).observe(telemetry.total_ms / 1000.0)
        for stage, ms in telemetry.timings.as_dict().items():
            if ms:
                self.stage_duration.labels(stage=stage).observe(ms / 1000.0)

        if telemetry.provider and telemetry.model:
            labels = {"provider": telemetry.provider, "model": telemetry.model}
            self.llm_tokens_total.labels(**labels, direction="input").inc(telemetry.prompt_tokens)
            self.llm_tokens_total.labels(**labels, direction="output").inc(telemetry.completion_tokens)
            if telemetry.cost_usd:
                self.llm_cost_usd_total.labels(**labels).inc(telemetry.cost_usd)
            if telemetry.ttft_ms is not None:
                self.llm_ttft.labels(**labels).observe(telemetry.ttft_ms / 1000.0)
        self.llm_prompt_tokens.labels(route=route).observe(telemetry.prompt_tokens)

        self.cache_lookups_total.labels(
            cache="llm",
            tier=telemetry.cache.tier or "none",
            result="hit" if telemetry.cache.hit else "miss",
        ).inc()

        retrieval = telemetry.retrieval
        if retrieval.candidates:
            self.retrieval_total.labels(
                strategy=retrieval.strategy, result="hit" if retrieval.returned else "empty"
            ).inc()
            self.retrieval_top_score.labels(strategy=retrieval.strategy).observe(retrieval.top_score)
            self.retrieval_returned.observe(retrieval.returned)
            if retrieval.returned_nothing:
                self.retrieval_empty_total.inc()

        if telemetry.memories_recalled:
            self.memory_recalled_records.observe(telemetry.memories_recalled)

        for guard in telemetry.guards:
            self.guard_verdicts_total.labels(
                guard=guard.get("guard", "unknown"),
                stage=guard.get("stage", "unknown"),
                action=guard.get("action", "unknown"),
            ).inc()
        if telemetry.tainted:
            self.turns_tainted_total.inc()

        for tool in telemetry.tools:
            outcome = "ok" if tool.ok else "error"
            if not tool.allowed:
                outcome = "denied"
            self.tool_calls_total.labels(tool=tool.name, outcome=outcome).inc()
            self.tool_duration.labels(tool=tool.name).observe(tool.duration_ms / 1000.0)

        if telemetry.plan_steps:
            self.plan_steps.observe(telemetry.plan_steps)
        self.reasoning_strategy_total.labels(strategy=telemetry.strategy).inc()
        if telemetry.confidence is not None:
            self.confidence_score.labels(strategy=telemetry.strategy).observe(telemetry.confidence)
        if telemetry.groundedness is not None:
            self.groundedness_score.observe(telemetry.groundedness)

        if telemetry.fallback_count:
            self.turn_degraded_total.labels(reason="provider_fallback").inc()
        if telemetry.tainted:
            self.turn_degraded_total.labels(reason="tainted_context").inc()
        if retrieval.returned_nothing:
            self.turn_degraded_total.labels(reason="empty_retrieval").inc()
        if any(not t.ok for t in telemetry.tools):
            self.turn_degraded_total.labels(reason="tool_failure").inc()

    def set_insecure_modes(self, bypasses: Mapping[str, bool]) -> None:
        """Publish which security bypasses are active.

        SEC-05 was invisible partly because nothing reported it. ``aura_insecure_mode != 0`` is
        a one-line alert rule that would have caught a production deploy with ``debug`` on.
        """
        for name, active in bypasses.items():
            self.insecure_mode.labels(bypass=name).set(1 if active else 0)

    def render(self) -> bytes:
        """Prometheus exposition text for the ``/metrics`` endpoint."""
        if not self.enabled or generate_latest is None:
            return b"# metrics disabled: prometheus_client not installed or metrics_enabled=false\n"
        return generate_latest(self.registry)

    @property
    def content_type(self) -> str:
        return "text/plain; version=0.0.4; charset=utf-8"


_instance: Metrics | None = None


def get_metrics(*, enabled: bool = True, namespace: str = "aura") -> Metrics:
    """Process-wide metrics singleton.

    A singleton specifically because Prometheus collectors are process-global by nature --
    registering the same timeseries twice raises. Tests use :func:`reset_metrics`.
    """
    global _instance
    if _instance is None:
        _instance = Metrics(enabled=enabled, namespace=namespace)
    return _instance


def reset_metrics() -> None:
    """Drop the singleton so the next :func:`get_metrics` builds a fresh registry."""
    global _instance
    _instance = None
