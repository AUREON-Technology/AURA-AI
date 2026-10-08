"""Circuit breaker and latency tracking for provider selection.

A dead provider must cost the *first* request its latency budget and no others. Without a
breaker, every request pays the full timeout of every failing candidate before reaching a
working one -- which is how a single unhealthy inference node turns into a site-wide latency
incident.

Also holds the EWMA latency estimate the ``FASTEST`` and ``BALANCED`` routing policies rank on.
EWMA rather than a simple average because provider performance changes (a node warms up, a
region degrades) and an all-time mean takes hours to forget.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Callable

__all__ = ["CircuitState", "CircuitBreaker", "LatencyTracker", "ProviderStats"]


class CircuitState(str, enum.Enum):
    CLOSED = "closed"      # normal operation
    OPEN = "open"          # failing; candidate is skipped
    HALF_OPEN = "half_open"  # probing with a single request

    def __str__(self) -> str:
        return self.value


class CircuitBreaker:
    """Consecutive-failure breaker with a half-open probe.

    Deliberately counts *consecutive* failures rather than a failure rate over a window: with
    low traffic (a single-VPS deployment overnight), a rate needs a minimum-sample rule to avoid
    tripping on one bad request, and that rule is another tunable to get wrong. Consecutive
    failures behave correctly at any traffic level.
    """

    __slots__ = ("_threshold", "_open_seconds", "_now", "_state", "_failures", "_opened_at",
                 "_half_open_inflight", "_opens", "_on_change")

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
        now: Callable[[], float],
        on_change: Callable[[CircuitState], None] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._threshold = failure_threshold
        self._open_seconds = open_seconds
        self._now = now
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = False
        self._opens = 0
        self._on_change = on_change

    @property
    def state(self) -> CircuitState:
        # Lazily transition OPEN -> HALF_OPEN so no background timer is needed; the state is
        # only ever observed during a routing decision anyway.
        if self._state is CircuitState.OPEN and self._now() - self._opened_at >= self._open_seconds:
            self._transition(CircuitState.HALF_OPEN)
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    @property
    def open_count(self) -> int:
        return self._opens

    def allows_request(self) -> bool:
        """Whether a request may be attempted right now.

        In HALF_OPEN exactly one probe is admitted at a time. Letting the full load through on a
        recovering provider is how a breaker turns a partial outage into a thundering herd.
        """
        state = self.state
        if state is CircuitState.CLOSED:
            return True
        if state is CircuitState.HALF_OPEN:
            if self._half_open_inflight:
                return False
            self._half_open_inflight = True
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._half_open_inflight = False
        if self._state is not CircuitState.CLOSED:
            self._transition(CircuitState.CLOSED)

    def record_failure(self) -> None:
        self._failures += 1
        self._half_open_inflight = False
        if self._state is CircuitState.HALF_OPEN:
            # A failed probe re-opens immediately and restarts the clock.
            self._open()
            return
        if self._failures >= self._threshold:
            self._open()

    def reset(self) -> None:
        self._failures = 0
        self._half_open_inflight = False
        self._transition(CircuitState.CLOSED)

    def _open(self) -> None:
        self._opened_at = self._now()
        self._opens += 1
        self._transition(CircuitState.OPEN)

    def _transition(self, state: CircuitState) -> None:
        if state is self._state:
            return
        self._state = state
        if self._on_change is not None:
            self._on_change(state)


class LatencyTracker:
    """Exponentially-weighted moving average of observed latency.

    ``alpha`` of 0.2 gives a ~9-sample effective window: responsive enough to notice a degrading
    node within a handful of requests, stable enough that one slow response does not reorder the
    provider ranking.
    """

    __slots__ = ("_alpha", "_value", "_samples")

    def __init__(self, *, alpha: float = 0.2, initial_ms: float | None = None) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be within (0, 1]")
        self._alpha = alpha
        self._value = initial_ms
        self._samples = 0

    def observe(self, latency_ms: float) -> None:
        if latency_ms < 0 or math.isnan(latency_ms):
            return
        self._samples += 1
        self._value = latency_ms if self._value is None else (
            self._alpha * latency_ms + (1 - self._alpha) * self._value
        )

    @property
    def value_ms(self) -> float | None:
        return self._value

    @property
    def samples(self) -> int:
        return self._samples

    def estimate_ms(self, default: float) -> float:
        """EWMA if we have data, otherwise the supplied prior.

        A provider with no history should not sort first purely because "unknown" reads as zero
        -- the caller passes a neutral prior instead.
        """
        return self._value if self._value is not None else default


@dataclass
class ProviderStats:
    """Live per-provider state consulted on every routing decision."""

    name: str
    breaker: CircuitBreaker
    latency: LatencyTracker = field(default_factory=LatencyTracker)
    ttft: LatencyTracker = field(default_factory=LatencyTracker)
    inflight: int = 0
    total_requests: int = 0
    total_failures: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    @property
    def healthy(self) -> bool:
        return self.breaker.state is not CircuitState.OPEN

    @property
    def failure_rate(self) -> float:
        return self.total_failures / self.total_requests if self.total_requests else 0.0

    def snapshot(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "state": str(self.breaker.state),
            "healthy": self.healthy,
            "inflight": self.inflight,
            "requests": self.total_requests,
            "failures": self.total_failures,
            "failure_rate": round(self.failure_rate, 4),
            "latency_ms": round(self.latency.value_ms, 1) if self.latency.value_ms else None,
            "ttft_ms": round(self.ttft.value_ms, 1) if self.ttft.value_ms else None,
            "tokens": self.total_tokens,
            "cost_usd": round(self.total_cost_usd, 6),
            "circuit_opens": self.breaker.open_count,
        }
