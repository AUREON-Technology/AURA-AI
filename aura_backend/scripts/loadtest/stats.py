"""Samples, aggregation, launch-threshold evaluation, and saturation (knee) detection.

Pure functions over plain data -- no I/O, no clock -- so every number the report prints can be
unit-tested against hand-computed values.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .config import Thresholds

# -- error classes ----------------------------------------------------------------------

ERR_4XX = "4xx"
ERR_5XX = "5xx"
ERR_429 = "overload_429"
ERR_503 = "unavailable_503"
ERR_TIMEOUT = "timeout"
ERR_CONNECTION = "connection"
ERR_BLOCKED = "blocked"

ERROR_CLASSES: tuple[str, ...] = (
    ERR_4XX, ERR_5XX, ERR_429, ERR_503, ERR_TIMEOUT, ERR_CONNECTION, ERR_BLOCKED,
)

#: Classes that count against the server-error threshold (see ``Thresholds``' docstring).
SERVER_FAILURE_CLASSES = frozenset({ERR_5XX, ERR_503, ERR_TIMEOUT, ERR_CONNECTION})

#: HTTP status each ``AuraError.kind`` maps to, for classifying a mid-stream ``error`` frame
#: (which arrives inside a 200 response). A copy of ``aura_core/domain/errors.py``'s table --
#: this package must not import ``aura_core`` (see ``__init__``) -- pinned against the original
#: by ``tests/unit/test_loadtest_harness.py`` so the two cannot drift silently.
ERROR_KIND_STATUS: Mapping[str, int] = {
    "configuration_error": 500,
    "validation_error": 422,
    "context_overflow": 413,
    "output_invalid": 422,
    "not_found": 404,
    "conflict": 409,
    "data_integrity_error": 500,
    "authentication_error": 401,
    "authorization_error": 403,
    "rate_limited": 429,
    "budget_exceeded": 429,
    "approval_required": 409,
    "confirmation_invalid": 409,
    "timeout": 504,
    "dependency_unavailable": 503,
    "provider_error": 502,
    "all_providers_failed": 503,
    "capability_unavailable": 503,
    "overloaded": 503,
    "user_concurrency_limit": 429,
    "consent_required": 403,
    "user_quota_exhausted": 429,
    "embedding_error": 502,
    "vector_store_error": 502,
    "memory_store_error": 502,
    "knowledge_error": 500,
    "index_not_ready": 503,
    "tool_error": 500,
    "tool_not_found": 404,
    "tool_execution_error": 502,
    "tool_timeout": 504,
    "safety_violation": 400,
    "planning_error": 500,
    "reasoning_error": 500,
    "execution_error": 500,
    "evaluation_error": 500,
    "internal_error": 500,
}


def classify_status(status: int | None) -> str | None:
    """HTTP status -> error class, or ``None`` for success."""
    if status is None:
        return ERR_CONNECTION
    if status < 400:
        return None
    if status == 429:
        return ERR_429
    if status == 503:
        return ERR_503
    if status >= 500:
        return ERR_5XX
    return ERR_4XX


def classify_stream_error(kind: str | None) -> str:
    """A mid-stream ``error`` frame -> error class.

    ``safety_violation`` is a policy outcome (the answer was withheld by a guard), classed
    ``blocked`` so it is visible without being mistaken for the server falling over.
    """
    if kind == "safety_violation":
        return ERR_BLOCKED
    status = ERROR_KIND_STATUS.get(kind or "", 500)
    return classify_status(status) or ERR_5XX


# -- records ----------------------------------------------------------------------------


@dataclass
class Sample:
    """One HTTP request as the load generator saw it."""

    scenario: str
    op: str
    #: ``llm`` (a chat turn), ``api`` (non-LLM API), ``speech`` (STT/TTS inference).
    kind: str
    vu: int
    started_s: float
    e2e_ms: float
    status: int | None
    error: str | None = None
    detail: str | None = None
    ttft_ms: float | None = None
    retries: int = 0
    completion_tokens: int | None = None
    tokens_per_s: float | None = None
    steady: bool = True
    frames: int = 0
    steps: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Iteration:
    """One execution of a scenario by one VU: possibly several requests (voice is three)."""

    scenario: str
    vu: int
    started_s: float
    e2e_ms: float
    ok: bool
    requests: int
    steady: bool = True
    ttft_ms: float | None = None


# -- percentiles ------------------------------------------------------------------------


def percentile(values: Iterable[float], q: float) -> float | None:
    """Linear-interpolated percentile (the "linear" / type-7 definition numpy defaults to).

    ``None`` for an empty population rather than 0 -- a p95 of "no samples" is not zero
    latency, and rendering it as such is how a broken scenario reads as a fast one.
    """
    data = sorted(v for v in values if v is not None and not math.isnan(v))
    if not data:
        return None
    if not 0 <= q <= 100:
        raise ValueError("q must be within [0, 100]")
    if len(data) == 1:
        return float(data[0])
    rank = (len(data) - 1) * (q / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(data[low])
    return float(data[low] + (data[high] - data[low]) * (rank - low))


def distribution(values: Iterable[float]) -> dict[str, float | None]:
    data = [v for v in values if v is not None]
    return {
        "n": len(data),
        "p50": percentile(data, 50),
        "p95": percentile(data, 95),
        "p99": percentile(data, 99),
        "max": max(data) if data else None,
        "mean": (sum(data) / len(data)) if data else None,
    }


# -- aggregation ------------------------------------------------------------------------


@dataclass
class Summary:
    """Aggregated numbers for one population of samples within one step."""

    requests: int = 0
    iterations: int = 0
    ok_iterations: int = 0
    window_s: float = 0.0
    rps: float = 0.0
    rpm: float = 0.0
    goodput_per_s: float = 0.0
    ttft_ms: dict[str, Any] = field(default_factory=dict)
    e2e_ms: dict[str, Any] = field(default_factory=dict)
    request_ms: dict[str, Any] = field(default_factory=dict)
    tokens_per_s: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    error_rate: float = 0.0
    server_failure_rate: float = 0.0
    retries: int = 0
    top_error_details: list[tuple[str, int]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(
    samples: Sequence[Sample], iterations: Sequence[Iteration], *, window_s: float
) -> Summary:
    """Aggregate the steady-state samples of one population.

    - ``rps``/``rpm``: HTTP requests started in the window, per second/minute of window.
    - ``goodput_per_s``: *successful* scenario iterations per second -- the throughput the knee
      detector uses, since failing fast is not throughput.
    - ``e2e_ms``: whole-iteration latency (every request of the scenario, retries included).
    - ``request_ms``: per-request latency; what the non-LLM API thresholds read.
    - ``ttft_ms``: time to the first ``token`` frame of each streamed turn.
    """
    steady = [s for s in samples if s.steady]
    steady_iters = [i for i in iterations if i.steady]
    window = max(window_s, 1e-9)
    errors = {cls: 0 for cls in ERROR_CLASSES}
    details: dict[str, int] = {}
    for s in steady:
        if s.error:
            errors[s.error] = errors.get(s.error, 0) + 1
            key = f"{s.op}: {s.error}" + (f" ({s.detail})" if s.detail else "")
            details[key] = details.get(key, 0) + 1
    failed = sum(errors.values())
    server_failed = sum(errors[c] for c in SERVER_FAILURE_CLASSES)
    ok_iters = [i for i in steady_iters if i.ok]
    return Summary(
        requests=len(steady),
        iterations=len(steady_iters),
        ok_iterations=len(ok_iters),
        window_s=round(window_s, 3),
        rps=len(steady) / window,
        rpm=len(steady) / window * 60.0,
        goodput_per_s=len(ok_iters) / window,
        ttft_ms=distribution(s.ttft_ms for s in steady if s.ttft_ms is not None),
        e2e_ms=distribution(i.e2e_ms for i in steady_iters),
        request_ms=distribution(s.e2e_ms for s in steady),
        tokens_per_s=distribution(s.tokens_per_s for s in steady if s.tokens_per_s is not None),
        errors=errors,
        error_rate=(failed / len(steady)) if steady else 0.0,
        server_failure_rate=(server_failed / len(steady)) if steady else 0.0,
        retries=sum(s.retries for s in steady),
        top_error_details=sorted(details.items(), key=lambda kv: (-kv[1], kv[0]))[:5],
    )


# -- thresholds -------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"
NO_DATA = "NO DATA"
NOT_EVALUATED = "NOT EVALUATED"


@dataclass
class ThresholdResult:
    name: str
    target: str
    observed: str
    verdict: str
    population: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fmt_ms(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f} ms"


def _latency_check(name: str, values: list[float], q: float, limit: float, population: str) -> ThresholdResult:
    observed = percentile(values, q)
    if observed is None:
        return ThresholdResult(name, f"< {limit:,.0f} ms", "no samples", NO_DATA, population)
    return ThresholdResult(
        name, f"< {limit:,.0f} ms", _fmt_ms(observed), PASS if observed < limit else FAIL, population
    )


def queue_verdict(series: Sequence[float], thresholds: Thresholds) -> tuple[str, str]:
    """Bounded-queue verdict from queue-depth samples taken during the step.

    Unbounded means "still growing when the step ended": the mean of the last third of the
    samples exceeds the mean of the first third by more than max(tolerance, 50% of the first
    third). A queue that rose during ramp-up and then held flat is bounded.
    """
    if len(series) < 3:
        return NOT_EVALUATED, "fewer than 3 queue-depth samples (is /metrics reachable?)"
    third = max(1, len(series) // 3)
    first = sum(series[:third]) / third
    last = sum(series[-third:]) / third
    peak = max(series)
    observed = f"first-third mean {first:.1f}, last-third mean {last:.1f}, max {peak:.0f}"
    if thresholds.max_queue_depth is not None and peak > thresholds.max_queue_depth:
        return FAIL, observed + f" (max > {thresholds.max_queue_depth:.0f})"
    if last - first > max(thresholds.queue_growth_tolerance, 0.5 * first):
        return FAIL, observed + " (still growing)"
    return PASS, observed


def evaluate_thresholds(
    samples: Sequence[Sample],
    iterations: Sequence[Iteration],
    thresholds: Thresholds,
    *,
    queue_series: Sequence[float] | None,
    queue_note: str = "/metrics not scraped",
) -> list[ThresholdResult]:
    steady = [s for s in samples if s.steady]
    api = [s.e2e_ms for s in steady if s.kind == "api" and s.ok]
    chat_ttft = [s.ttft_ms for s in steady if s.scenario == "chat" and s.ttft_ms is not None]
    chat_e2e = [i.e2e_ms for i in iterations if i.steady and i.scenario == "chat" and i.ok]

    results = [
        _latency_check("Non-LLM API p95", api, 95, thresholds.api_p95_ms, "successful `api` requests"),
        _latency_check("Non-LLM API p99", api, 99, thresholds.api_p99_ms, "successful `api` requests"),
        _latency_check("TTFT p95", chat_ttft, 95, thresholds.ttft_p95_ms, "scenario A (chat) streams"),
        _latency_check(
            "Ordinary response p95", chat_e2e, 95, thresholds.response_p95_ms,
            "successful scenario A (chat) turns",
        ),
    ]
    if steady:
        failed = sum(1 for s in steady if s.error in SERVER_FAILURE_CLASSES)
        rate = failed / len(steady)
        results.append(
            ThresholdResult(
                "5xx rate", f"< {thresholds.server_error_rate:.1%}",
                f"{rate:.2%} ({failed}/{len(steady)})",
                PASS if rate < thresholds.server_error_rate else FAIL,
                "all requests (5xx, 503, timeouts, dropped connections, mid-stream server errors)",
            )
        )
    else:
        results.append(
            ThresholdResult("5xx rate", f"< {thresholds.server_error_rate:.1%}", "no samples", NO_DATA,
                            "all requests")
        )
    if queue_series is None:
        results.append(
            ThresholdResult("Bounded queue", "not growing at end of step", queue_note,
                            NOT_EVALUATED, "server `aura_llm_queue_depth`")
        )
    else:
        verdict, observed = queue_verdict(queue_series, thresholds)
        results.append(
            ThresholdResult("Bounded queue", "not growing at end of step", observed, verdict,
                            "server `aura_llm_queue_depth`")
        )
    return results


# -- saturation -------------------------------------------------------------------------


@dataclass
class Knee:
    knee_concurrency: int
    saturated_at: int
    throughput_gain: float
    latency_rise: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_knee(
    points: Sequence[tuple[int, float, float | None]],
    *,
    min_throughput_gain: float = 0.10,
    min_latency_rise: float = 0.10,
) -> Knee | None:
    """Find where adding users stopped adding throughput and started adding latency.

    ``points`` are ``(concurrency, goodput_per_s, latency_ms)`` per matrix step. The knee is the
    last step *before* the first transition in which throughput grew by less than
    ``min_throughput_gain`` (relative) while latency rose by at least ``min_latency_rise`` --
    beyond it, more concurrency only queues. Steps with no latency (nothing succeeded) are
    treated as zero throughput and infinite latency, i.e. saturated. ``None`` when the matrix
    never saturated, which is itself a finding: the matrix did not go high enough.
    """
    ordered = sorted(points, key=lambda p: p[0])
    for (c0, tp0, lat0), (c1, tp1, lat1) in zip(ordered, ordered[1:]):
        if lat0 is None:
            continue  # the lower step itself had nothing to compare against
        gain = (tp1 - tp0) / tp0 if tp0 > 0 else (math.inf if tp1 > 0 else 0.0)
        rise = math.inf if lat1 is None else ((lat1 - lat0) / lat0 if lat0 > 0 else 0.0)
        if gain < min_throughput_gain and rise >= min_latency_rise:
            return Knee(
                knee_concurrency=c0,
                saturated_at=c1,
                throughput_gain=gain,
                latency_rise=rise,
                reason=(
                    f"{c0} -> {c1} users: goodput {tp0:.3g} -> {tp1:.3g}/s "
                    f"({gain:+.0%}), p95 latency "
                    f"{lat0:,.0f} -> {'n/a' if lat1 is None else f'{lat1:,.0f}'} ms"
                    f"{'' if lat1 is None else f' ({rise:+.0%})'}"
                ),
            )
    return None
