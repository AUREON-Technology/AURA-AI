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

import os
from pathlib import Path
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
# Turns, TTFT and HTTP requests reach 600 s / 300 s for the same reason (F-24): a CPU-hosted
# turn routinely runs past 60 s and a queued request can wait minutes for its first token, so
# buckets that stopped at 60 s and 12 s put the production p95 in +Inf, where no quantile can be
# read from it. 600 s is the nginx read timeout on /api/v3/chat, so nothing slower can complete.
_TURN_BUCKETS = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0, 120.0, 180.0, 300.0, 600.0,
)
_TTFT_BUCKETS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.5, 3.0, 6.0, 12.0, 30.0, 60.0, 120.0, 300.0)
_HTTP_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0,
)
_TOKEN_BUCKETS = (64, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768)
_SCORE_BUCKETS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
# Generation-phase buckets reach 240 s because CPU inference really does take that long (Phase A4
# measured a 70 s p95); buckets that stop at 60 s would put every slow turn in +Inf.
_GEN_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0, 120.0, 240.0)
_QUEUE_BUCKETS = (0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)
_TPS_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 320.0)


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


def _planned_label(telemetry: Any) -> str:
    """``"true"`` when the turn's planning stage ran, else ``"false"`` (KAN-108).

    Read from what the kernel recorded, not re-derived from the message: planning ran when it
    produced steps, when it failed (``planning_failed``), or when the planning stage has a
    duration at all -- a plan that came back as a single RESPOND step still cost a generation.
    Two values only, so the label cannot grow the series count.
    """
    timings = getattr(telemetry, "timings", None)
    ran = (
        bool(getattr(telemetry, "plan_steps", 0))
        or bool(getattr(telemetry, "planning_failed", False))
        or bool(getattr(timings, "planning", 0.0))
    )
    return "true" if ran else "false"


def _multiproc_dir() -> str | None:
    """Return the multiprocess mmap directory when the operator has opted in.

    Unset (the pytest default) keeps a private in-process registry so tests
    stay isolated. The env var must be set *before* the interpreter starts in
    production so prometheus_client selects MultiProcessValue at import.
    """
    path = os.environ.get("PROMETHEUS_MULTIPROC_DIR") or os.environ.get(
        "prometheus_multiproc_dir"
    )
    if not path:
        return None
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


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
        # Private registry when multiprocess is off (pytest isolation). In
        # multiprocess mode metrics write to mmap files and must *not* also
        # register on a registry later used by MultiProcessCollector — that
        # would export each series twice.
        self._multiproc_dir = _multiproc_dir() if self.enabled else None
        if self.enabled and not self._multiproc_dir:
            self.registry = CollectorRegistry()
        else:
            self.registry = None
        self._build()

    # -- construction helpers ----------------------------------------------------------

    def _counter(self, name: str, doc: str, labels: Sequence[str] = ()) -> Any:
        if not self.enabled:
            return _NOOP
        return Counter(f"{self.namespace}_{name}", doc, list(labels), registry=self.registry)

    def _gauge(
        self,
        name: str,
        doc: str,
        labels: Sequence[str] = (),
        *,
        multiprocess_mode: str = "livesum",
    ) -> Any:
        if not self.enabled:
            return _NOOP
        kwargs: dict[str, Any] = {"registry": self.registry}
        if self._multiproc_dir:
            # livesum: in-flight style gauges add across uvicorn workers.
            # Callers can pass livemax/livemin for 0/1 snapshots.
            kwargs["multiprocess_mode"] = multiprocess_mode
        return Gauge(f"{self.namespace}_{name}", doc, list(labels), **kwargs)

    def _histogram(
        self, name: str, doc: str, labels: Sequence[str] = (), buckets: Sequence[float] = _MS_BUCKETS
    ) -> Any:
        if not self.enabled:
            return _NOOP
        return Histogram(
            f"{self.namespace}_{name}", doc, list(labels), buckets=tuple(buckets), registry=self.registry
        )

    def _build(self) -> None:
        # Audit FSA-094: plaintext rows read under a configured data key -- what
        # scripts/reencrypt_fields.py has left to seal. Zero after a complete run.
        self.field_crypto_legacy_reads = self._counter(
            "field_crypto_legacy_plaintext_reads_total",
            "Values read in legacy plaintext while a data-encryption key is configured",
            ["kind"],
        )
        # -- turns ---------------------------------------------------------------------
        self.turns_total = self._counter(
            "turns_total", "Completed turns by route and outcome.", ("route", "outcome")
        )
        # ``planned`` ("true"/"false", KAN-108): a planned turn pays one planning generation
        # (and any tool calls) before its answer, so its latency is a different distribution.
        # Mixed into one series, a p95 moves with the planned share rather than with speed.
        self.turn_duration = self._histogram(
            "turn_duration_seconds", "End-to-end turn duration.", ("route", "planned"),
            _TURN_BUCKETS,
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

        # -- http (F-24) ---------------------------------------------------------------
        # ``route`` is the matched route *template* (``/api/v3/sessions/{session_id}``), never
        # the raw path, and ``unmatched`` for a 404 -- so a scanner cannot mint series.
        self.http_requests_total = self._counter(
            "http_requests_total", "HTTP requests by route and status.",
            ("method", "route", "status"),
        )
        self.http_request_duration = self._histogram(
            "http_request_duration_seconds",
            "HTTP request duration, to the last body byte (a whole stream, for SSE).",
            ("method", "route"),
            _HTTP_BUCKETS,
        )

        # -- event outbox (F-24) -------------------------------------------------------
        # -- billing and email (SaaS P1-H, readiness item 5) --------------------------------
        # No label carries a workspace, person, amount or reference.
        self.billing_notifications_total = self._counter(
            "billing_notifications_total",
            "Payment notifications by source (fawry/apple/google) and outcome.",
            ("source", "result"),
        )
        # increase() cannot see a series' first sample, so every series an alert watches exists
        # at 0 from the start; otherwise the first crash on a customer's payment would not page.
        if self.enabled:
            for source in ("fawry", "apple", "google"):
                for result in ("error", "invalid_signature", "malformed", "too_large", "store_unavailable"):
                    self.billing_notifications_total.labels(source=source, result=result)
        self.email_sends_total = self._counter(
            "email_sends_total", "Transactional emails by kind and outcome (ok/failed).", ("tag", "result")
        )
        if self.enabled:
            for tag in ("invite", "receipt", "renewal_due", "past_due"):
                self.email_sends_total.labels(tag=tag, result="failed")
        # The cycle runs in a cron process nobody scrapes: these are read from its heartbeat in the
        # shared cache at scrape time (refresh_scrape_gauges), like the outbox gauges below.
        # "livemostrecent": each uvicorn worker sets them only when it serves a scrape, so the
        # newest write is the true reading; max would pin an error count after it went down.
        self.billing_enabled = self._gauge(
            "billing_enabled", "1 when AURA_BILLING_ENABLED is on.", multiprocess_mode="livemostrecent"
        )
        self.billing_cycle_last_run = self._gauge(
            "billing_cycle_last_run_timestamp_seconds",
            "Unix time the billing cycle last ran to completion; 0 when it never has.",
            multiprocess_mode="livemostrecent",
        )
        self.billing_cycle_last_errors = self._gauge(
            "billing_cycle_last_errors", "Payments or renewals the last cycle could not process.",
            multiprocess_mode="livemostrecent",
        )
        self.billing_cycle_last_email_failures = self._gauge(
            "billing_cycle_last_email_failures", "Emails the last cycle could not send.",
            multiprocess_mode="livemostrecent",
        )
        # Read from the outbox at scrape time, not pushed by the drain worker: the worker is a
        # different process (often a different host) from the one Prometheus scrapes.
        self.outbox_pending = self._gauge(
            "outbox_pending", "Durable events waiting for delivery.", multiprocess_mode="livemax"
        )
        self.outbox_dead_lettered = self._gauge(
            "outbox_dead_lettered", "Durable events that exhausted their delivery attempts.",
            multiprocess_mode="livemax",
        )
        self.outbox_oldest_age = self._gauge(
            "outbox_oldest_pending_age_seconds",
            "Age of the oldest event waiting for delivery (0 when none is).",
            multiprocess_mode="livemax",
        )

        # -- vision (ADR-031) ----------------------------------------------------------
        # Labels are bounded enums only: ``outcome`` is vlm/ocr_fallback/unavailable and
        # ``reason`` a normalizer refusal (unsupported_type, image_too_large, ...). Never a
        # digest, a size or anything else about a particular image.
        self.vision_requests = self._counter(
            "vision_requests_total", "Image perception attempts by outcome.", ("outcome",)
        )
        self.vision_latency = self._histogram(
            "vision_latency_seconds", "Image perception duration.", ("mode",), _TURN_BUCKETS
        )
        self.vision_rejections = self._counter(
            "vision_rejections_total", "Attached images refused before perception.", ("reason",)
        )

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
            "provider_healthy",
            "1 when the circuit breaker is closed.",
            ("provider",),
            multiprocess_mode="livemax",
        )
        self.provider_circuit_opens_total = self._counter(
            "provider_circuit_opens_total", "Circuit breaker openings.", ("provider",)
        )
        # ADR-030 Phase 2. `reason` is one of a fixed set (rate_limited, quota_rpm, quota_tpm,
        # quota_rpd, quota_tpd, quota_request_too_large), so the series count stays bounded.
        self.llm_quota_events_total = self._counter(
            "llm_quota_events_total",
            "Requests skipped by a provider's declared quota, and 429 cooldowns applied.",
            ("provider", "reason"),
        )

        # -- llm admission and latency decomposition (roadmap P2.4/P2.5) ------------------
        # `group` is a capacity group (one serving process); `pool` is the ModelPool the request
        # asked for, or "default". Both are bounded by configuration/enum, and `priority` by the
        # five workload classes -- no user id ever becomes a label.
        self.llm_admission_total = self._counter(
            "llm_admission_total",
            "Admission decisions per capacity group: admitted, queue_full, queue_timeout, "
            "preempted, user_quota, cancelled.",
            ("group", "outcome", "priority"),
        )
        self.llm_group_capacity = self._gauge(
            "llm_group_capacity",
            "Concurrent generation slots per capacity group (summed across processes).",
            ("group",),
        )
        self.llm_group_inflight = self._gauge(
            "llm_group_inflight",
            "Generations holding an admission slot per capacity group.",
            ("group",),
        )
        self.llm_generation_queue_wait = self._histogram(
            "llm_generation_queue_wait_seconds",
            "Time a generation waited for an admission slot.",
            ("provider", "model", "pool"),
            _QUEUE_BUCKETS,
        )
        self.llm_generation_ttft = self._histogram(
            "llm_generation_ttft_seconds",
            "Admission to first streamed token (prefill + first decode step).",
            ("provider", "model", "pool"),
            _GEN_BUCKETS,
        )
        self.llm_generation_decode = self._histogram(
            "llm_generation_decode_seconds",
            "First to last streamed token.",
            ("provider", "model", "pool"),
            _GEN_BUCKETS,
        )
        self.llm_generation_total = self._histogram(
            "llm_generation_total_seconds",
            "Queue wait plus provider time for one attempt.",
            ("provider", "model", "pool", "outcome"),
            _GEN_BUCKETS,
        )
        self.llm_generation_tokens_total = self._counter(
            "llm_generation_tokens_total",
            "Tokens per provider attempt, as reported (or estimated) by the provider.",
            ("provider", "model", "pool", "direction"),
        )
        self.llm_generation_tokens_per_second = self._histogram(
            "llm_generation_tokens_per_second",
            "Output tokens per second over the decode phase.",
            ("provider", "model", "pool"),
            _TPS_BUCKETS,
        )

        # -- cache ---------------------------------------------------------------------
        self.cache_lookups_total = self._counter(
            "cache_lookups_total", "Cache lookups.", ("cache", "tier", "result")
        )
        self.cache_entries = self._gauge(
            "cache_entries",
            "Entries held in a cache.",
            ("cache",),
            multiprocess_mode="livemax",
        )

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
        self.memory_records = self._gauge(
            "memory_records", "Stored records by kind.", ("kind",), multiprocess_mode="livemax"
        )
        self.memory_consolidation_total = self._counter(
            "memory_consolidation_total", "Consolidation outcomes.", ("action",)
        )

        # -- knowledge -----------------------------------------------------------------
        self.documents_ingested_total = self._counter(
            "documents_ingested_total", "Documents ingested.", ("source_type", "outcome")
        )
        self.chunks_indexed_total = self._counter("chunks_indexed_total", "Chunks written to an index.")
        self.knowledge_generation = self._gauge(
            "knowledge_generation",
            "Current promoted knowledge generation number.",
            multiprocess_mode="livemax",
        )
        self.knowledge_age_seconds = self._gauge(
            "knowledge_age_seconds",
            "Seconds since the knowledge index was last promoted.",
            multiprocess_mode="livemax",
        )
        self.refresh_last_success_timestamp = self._gauge(
            "refresh_last_success_timestamp_seconds",
            "Unix time of the last successful refresh.",
            multiprocess_mode="livemax",
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
            multiprocess_mode="livemax",
        )
        self.connector_health = self._gauge(
            "connector_health",
            "Last health check of each Track AP connector: 1 healthy, 0.5 degraded, 0 unhealthy. "
            "One install serves one project with a handful of connectors (ADR-019), so the "
            "connector label is bounded.",
            ("connector", "kind"),
            multiprocess_mode="livemax",
        )
        self.discovery_runs = self._counter(
            "discovery_runs_total",
            "Track AP discovery runs by connector kind and outcome (ok, truncated, timeout, "
            "unsupported, error).",
            ("kind", "outcome"),
        )
        self.discovery_seconds = self._histogram(
            "discovery_seconds", "Wall time of one discovery run.", ("kind",),
            buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
        )
        self.discovery_artifacts = self._gauge(
            "discovery_artifacts",
            "Artifacts found by the last successful discovery of each connector (bounded: one "
            "install, a handful of connectors -- ADR-019).",
            ("connector",),
            multiprocess_mode="livemax",
        )
        self.twin_labeling_coverage = self._gauge(
            "twin_labeling_coverage",
            "Share of label-eligible Project Twin elements carrying a model label after the last "
            "snapshot (0 in degraded mode: no labeler, or it failed -- ADR-026).",
            (),
            multiprocess_mode="livemax",
        )
        self.twin_labeling_elements = self._counter(
            "twin_labeling_elements_total",
            "Label-eligible twin elements by how they were labeled (model, reused, degraded).",
            ("outcome",),
        )
        self.twin_labeler_seconds_per_element = self._histogram(
            "twin_labeler_seconds_per_element",
            "Labeling model latency per element of a batch.",
            (),
            buckets=(0.1, 0.5, 1, 2.5, 5, 10, 20, 40),
        )
        self.capability_plan_selected = self._gauge(
            "capability_plan_selected",
            "Selections per capability primitive in the last capability plan (bounded by the "
            "registry: a dozen primitives, ADR-023).",
            ("capability",),
            multiprocess_mode="livemax",
        )
        self.capability_plan_evidence = self._histogram(
            "capability_plan_evidence",
            "AUSM elements cited as evidence per capability selection.",
            (),
            buckets=(1, 2, 3, 4, 6, 8, 12, 16, 24),
        )
        self.blueprint_artifacts = self._gauge(
            "blueprint_artifacts",
            "Artifacts in the last compiled blueprint by kind (templates, tools, agents, "
            "workflows, views, eval_cases, dropped).",
            ("kind",),
            multiprocess_mode="livemax",
        )
        self.blueprint_active_version = self._gauge(
            "blueprint_active_version",
            "The blueprint version live in this worker (0: core personas only).",
            (),
            multiprocess_mode="livemax",
        )
        self.blueprint_swap_seconds = self._histogram(
            "blueprint_swap_seconds", "Time to register tools, move the pointer and swap agents.", (),
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
        )
        self.blueprint_gate_pass_rate = self._gauge(
            "blueprint_gate_pass_rate",
            "Pass rate per suite of the latest evaluation-gate run (AP8), over the cases that ran.",
            ("suite",),
            multiprocess_mode="livemax",
        )
        self.blueprint_gate_coverage = self._gauge(
            "blueprint_gate_coverage",
            "Share of each suite's cases the latest gate run could execute (the rest had no replica).",
            ("suite",),
            multiprocess_mode="livemax",
        )
        self.blueprint_gate_runs = self._counter(
            "blueprint_gate_runs_total",
            "Evaluation-gate runs by outcome: passed, failed (blocked) or failed_advisory.",
            ("outcome",),
        )
        self.generated_tool_calls = self._counter(
            "generated_tool_calls_total",
            "Calls of blueprint-generated read tools by outcome (ok, error); the error rate is the "
            "connected systems' availability as AURA sees it.",
            ("outcome",),
        )
        self.agent_workspaces = self._gauge(
            "agent_workspaces",
            "Durable engineer-agent workspaces by status. Only `hot` costs memory (a warm "
            "workspace is a stopped container), so hot * the per-workspace memory limit is this "
            "feature's whole RAM footprint -- alert on it approaching the host's headroom.",
            ("status",),
            multiprocess_mode="livemax",
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
        # -- agent runs (GAP-024) -------------------------------------------------------
        # `agent` is the resolved persona id -- a bounded set from the AgentRegistry, never the
        # caller's raw request value, so it is safe as a label.
        self.agent_runs_total = self._counter(
            "agent_runs_total", "Agent runs by persona and terminal status.", ("agent", "status")
        )
        self.agent_run_iterations = self._histogram(
            "agent_run_iterations", "Execution passes per agent run.", ("agent",),
            (1, 2, 3, 4, 5, 6, 7, 8, 9, 12),
        )
        self.agent_budget_exhausted_total = self._counter(
            "agent_budget_exhausted_total",
            "Agent runs stopped by a budget (iterations, tokens, time, tools, risk).",
            ("agent", "budget"),
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
        self.eval_score = self._gauge(
            "eval_score",
            "Latest evaluation score.",
            ("suite", "metric"),
            multiprocess_mode="livemax",
        )
        self.eval_runs_total = self._counter("eval_runs_total", "Evaluation runs.", ("suite", "outcome"))
        self.drift_score = self._gauge(
            "drift_score",
            "Population stability index by signal.",
            ("signal",),
            multiprocess_mode="livemax",
        )
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
            "dependency_up",
            "1 when a dependency's health probe passes.",
            ("dependency",),
            multiprocess_mode="livemin",
        )
        self.gpu_memory_used_bytes = self._gauge(
            "gpu_memory_used_bytes",
            "GPU memory in use, when a probe is available.",
            ("device",),
            multiprocess_mode="livemax",
        )
        self.gpu_utilisation = self._gauge(
            "gpu_utilisation_ratio",
            "GPU utilisation 0-1, when a probe is available.",
            ("device",),
            multiprocess_mode="livemax",
        )

    # -- convenience -------------------------------------------------------------------

    def observe_legacy_plaintext_read(self, kind: str) -> None:
        self.field_crypto_legacy_reads.labels(kind=kind).inc()

    def observe_turn(self, telemetry: Any) -> None:
        """Fan one :class:`~aura_core.domain.telemetry.TurnTelemetry` out to the catalogue.

        Kept here rather than in the kernel so the domain layer stays free of any metrics
        dependency (ADR-009: engines return timings, the observability layer records them).
        """
        route = telemetry.route
        self.turns_total.labels(route=route, outcome=str(telemetry.outcome)).inc()
        self.turn_duration.labels(route=route, planned=_planned_label(telemetry)).observe(
            telemetry.total_ms / 1000.0
        )
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
        if telemetry.planning_failed:
            self.turn_degraded_total.labels(reason="planning_failed").inc()

    def observe_generation(self, timing: Any) -> None:
        """Fan one :class:`~aura_core.domain.llm.GenerationTiming` out to the catalogue.

        The router builds the value; this is the only place it becomes series (ADR-009). No
        content is available here to leak -- the value carries durations and counts only.
        """
        labels = {"provider": timing.provider, "model": timing.model, "pool": timing.pool}
        self.llm_generation_queue_wait.labels(**labels).observe(timing.queue_wait_ms / 1000.0)
        self.llm_generation_total.labels(**labels, outcome=timing.outcome).observe(
            timing.total_ms / 1000.0
        )
        if timing.outcome != "success":
            return
        if timing.ttft_ms is not None:
            self.llm_generation_ttft.labels(**labels).observe(timing.ttft_ms / 1000.0)
        if timing.decode_ms is not None:
            self.llm_generation_decode.labels(**labels).observe(timing.decode_ms / 1000.0)
        if timing.prompt_tokens:
            self.llm_generation_tokens_total.labels(**labels, direction="input").inc(
                timing.prompt_tokens
            )
        if timing.completion_tokens:
            self.llm_generation_tokens_total.labels(**labels, direction="output").inc(
                timing.completion_tokens
            )
        tps = timing.tokens_per_second
        if tps is not None:
            self.llm_generation_tokens_per_second.labels(**labels).observe(tps)

    def observe_http(self, method: str, route: str, status: int, seconds: float) -> None:
        self.http_requests_total.labels(method=method, route=route, status=str(status)).inc()
        self.http_request_duration.labels(method=method, route=route).observe(seconds)

    def set_billing(self, *, enabled: bool, last_run: float, errors: int, email_failures: int) -> None:
        self.billing_enabled.set(1 if enabled else 0)
        self.billing_cycle_last_run.set(last_run)
        self.billing_cycle_last_errors.set(errors)
        self.billing_cycle_last_email_failures.set(email_failures)

    def set_outbox(self, *, pending: int, dead_lettered: int, oldest_age_s: float) -> None:
        self.outbox_pending.set(pending)
        self.outbox_dead_lettered.set(dead_lettered)
        self.outbox_oldest_age.set(oldest_age_s)

    def set_insecure_modes(self, bypasses: Mapping[str, bool]) -> None:
        """Publish which security bypasses are active.

        SEC-05 was invisible partly because nothing reported it. ``aura_insecure_mode != 0`` is
        a one-line alert rule that would have caught a production deploy with ``debug`` on.
        """
        for name, active in bypasses.items():
            self.insecure_mode.labels(bypass=name).set(1 if active else 0)

    def set_agent_workspaces(self, counts: Mapping[str, int]) -> None:
        """Publish the workspace census by status.

        Every known status is written on every call, including the zeroes: a gauge that is only
        set when non-zero keeps reporting the last count it saw after the workspaces are gone,
        which would make the memory headroom alert fire against workspaces that no longer exist.
        """
        for status in ("hot", "warm"):
            self.agent_workspaces.labels(status=status).set(counts.get(status, 0))

    def observe_discovery(self, connector: str, kind: str, outcome: str, seconds: float) -> None:
        self.discovery_runs.labels(kind=kind, outcome=outcome).inc()
        self.discovery_seconds.labels(kind=kind).observe(seconds)

    def set_discovery_artifacts(self, connector: str, count: int) -> None:
        self.discovery_artifacts.labels(connector=connector).set(count)

    def observe_labeling(self, summary) -> None:
        self.twin_labeling_coverage.set(summary.coverage)
        for outcome in ("model", "reused", "degraded"):
            count = getattr(summary, outcome)
            if count:
                self.twin_labeling_elements.labels(outcome=outcome).inc(count)

    def observe_labeler_latency(self, seconds_per_element: float) -> None:
        self.twin_labeler_seconds_per_element.observe(seconds_per_element)

    def observe_capability_plan(self, plan) -> None:
        counts = plan.summary()["by_capability"]
        for capability in {s.capability for s in plan.selected} | set(counts):
            self.capability_plan_selected.labels(capability=capability).set(counts.get(capability, 0))
        for selection in plan.selected:
            self.capability_plan_evidence.observe(len(selection.evidence))

    def observe_blueprint(self, summary) -> None:
        for kind in ("templates", "tools", "agents", "workflows", "views", "eval_cases", "dropped"):
            self.blueprint_artifacts.labels(kind=kind).set(summary.get(kind, 0))

    def set_blueprint_active(self, version: int) -> None:
        self.blueprint_active_version.set(version)

    def observe_blueprint_swap(self, seconds: float) -> None:
        self.blueprint_swap_seconds.observe(seconds)

    def observe_gate(self, report: Any) -> None:
        for suite in report.suites:
            self.blueprint_gate_pass_rate.labels(suite=suite.name).set(suite.pass_rate)
            self.blueprint_gate_coverage.labels(suite=suite.name).set(suite.coverage)
        outcome = "passed" if report.passed else ("failed_advisory" if report.mode.value == "advisory" else "failed")
        self.blueprint_gate_runs.labels(outcome=outcome).inc()

    def observe_generated_tool(self, outcome: str) -> None:
        self.generated_tool_calls.labels(outcome=outcome).inc()

    def set_connector_health(self, connector: str, kind: str, status: str) -> None:
        value = {"healthy": 1.0, "degraded": 0.5}.get(status, 0.0)
        self.connector_health.labels(connector=connector, kind=kind).set(value)

    def render(self) -> bytes:
        """Prometheus exposition text for the ``/metrics`` endpoint."""
        if not self.enabled or generate_latest is None:
            return b"# metrics disabled: prometheus_client not installed or metrics_enabled=false\n"
        if self._multiproc_dir:
            from prometheus_client import multiprocess

            try:
                registry = CollectorRegistry(support_collectors_without_names=True)
            except TypeError:
                registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry, path=self._multiproc_dir)
            return generate_latest(registry)
        return generate_latest(self.registry)

    def mark_process_dead(self) -> None:
        """Drop this worker's live-gauge mmap files (uvicorn/Gunicorn child_exit)."""
        if not self._multiproc_dir:
            return
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(os.getpid(), path=self._multiproc_dir)

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
