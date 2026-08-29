"""Provider router: selection, failover, load balancing, budgets (ADR-003).

The single most important component in the platform, because generation concurrency is the
binding constraint at every scaling stage (Scalability Report §6).

Responsibilities, in order:

1. **Filter** candidates by declared capability (tools, streaming, JSON mode, context window,
   quality tier, locality) and by health -- an open circuit removes a provider from candidacy.
2. **Order** them by the request's :class:`RoutingPolicy`.
3. **Attempt** each in turn, up to ``max_fallbacks``, with per-attempt timeout.
4. **Record** latency, tokens, cost, and failures so the next decision is better informed.

Two rules that are not obvious and matter a great deal:

* **Failover happens only before the first token.** Once a stream has emitted output, switching
  providers would duplicate or contradict what the user already sees. The legacy code retries a
  whole generation after tokens have been sent (``services/agent.py:308``); this does not.
* **Exhausting candidates raises.** There is no synthetic answer of last resort. Fabricating a
  response that is indistinguishable from a real one -- what ``services/agent.py:349`` does --
  is worse than an honest 503.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from ..domain.errors import (
    AllProvidersFailedError,
    BudgetExceededError,
    ProviderError,
    ValidationError,
)
from ..domain.events import ProviderFailedOver, ProviderHealthChanged
from ..domain.llm import (
    CompletionRequest,
    CompletionResponse,
    RoutingPolicy,
    StreamChunk,
)
from ..ports.llm import LLMProvider
from .circuit import CircuitBreaker, CircuitState, LatencyTracker, ProviderStats

__all__ = ["ProviderRouter", "RoutingDecision", "TurnBudget"]

# Prior used to rank a provider with no latency history. Deliberately mid-range: a new provider
# should neither win by default (unknown == 0) nor be excluded (unknown == infinity).
_LATENCY_PRIOR_MS = 1500.0


def _attempt_summary(attempts: Sequence[Mapping[str, Any]]) -> str:
    """Compact `` (name:error, ...)`` suffix so SSE error text names the failure, not just the count."""
    if not attempts:
        return ""
    parts: list[str] = []
    for attempt in attempts:
        name = attempt.get("provider") or "?"
        error = attempt.get("error") or attempt.get("reason") or "unknown"
        parts.append(f"{name}:{error}")
    return f" ({', '.join(parts)})"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Why this ordering was chosen -- surfaced in telemetry and the admin API."""

    candidates: tuple[str, ...]
    policy: RoutingPolicy
    excluded: Mapping[str, str] = field(default_factory=dict)

    @property
    def primary(self) -> str | None:
        return self.candidates[0] if self.candidates else None


@dataclass
class TurnBudget:
    """Per-turn generation and cost ceiling.

    Replaces the legacy ``services/budget.py``, which caps Gemini calls at **2 per request** via
    a ``ContextVar`` -- making any agentic task needing three tool round-trips structurally
    impossible. Here the ceiling is expressed in generations *and* dollars, is per turn rather
    than global, and is configurable.
    """

    max_generations: int = 8
    max_cost_usd: float = 0.0  # 0 disables the cost ceiling
    generations_used: int = 0
    cost_used_usd: float = 0.0

    def check(self) -> None:
        if self.generations_used >= self.max_generations:
            raise BudgetExceededError(
                "turn exceeded its generation budget",
                details={"limit": self.max_generations, "used": self.generations_used},
            )
        if self.max_cost_usd and self.cost_used_usd >= self.max_cost_usd:
            raise BudgetExceededError(
                "turn exceeded its cost budget",
                details={"limit_usd": self.max_cost_usd, "used_usd": round(self.cost_used_usd, 6)},
            )

    def consume(self, *, cost_usd: float = 0.0) -> None:
        self.generations_used += 1
        self.cost_used_usd += cost_usd

    @property
    def remaining_generations(self) -> int:
        return max(0, self.max_generations - self.generations_used)


class ProviderRouter:
    """Routes completion requests across a pool of providers."""

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        default_policy: RoutingPolicy = RoutingPolicy.BALANCED,
        max_fallbacks: int = 2,
        circuit_failure_threshold: int = 5,
        circuit_open_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
        metrics: Any = None,
        event_bus: Any = None,
    ) -> None:
        if not providers:
            raise ValidationError("ProviderRouter requires at least one provider")
        self._providers: dict[str, LLMProvider] = {p.name: p for p in providers}
        if len(self._providers) != len(providers):
            raise ValidationError("provider names must be unique")

        self._default_policy = default_policy
        self._max_fallbacks = max_fallbacks
        self._clock = clock or time.monotonic
        self._metrics = metrics
        self._events = event_bus

        self._stats: dict[str, ProviderStats] = {
            name: ProviderStats(
                name=name,
                breaker=CircuitBreaker(
                    failure_threshold=circuit_failure_threshold,
                    open_seconds=circuit_open_seconds,
                    now=self._clock,
                    on_change=self._make_circuit_observer(name),
                ),
                latency=LatencyTracker(),
                ttft=LatencyTracker(),
            )
            for name in self._providers
        }
        self._slots: dict[str, asyncio.Semaphore] = {
            name: asyncio.Semaphore(max(1, provider.capabilities.max_concurrency))
            for name, provider in self._providers.items()
        }
        self._publish_health()

    # -- observation -------------------------------------------------------------------

    def _make_circuit_observer(self, name: str) -> Callable[[CircuitState], None]:
        def observe(state: CircuitState) -> None:
            if self._metrics is not None:
                self._metrics.provider_healthy.labels(provider=name).set(
                    0 if state is CircuitState.OPEN else 1
                )
                if state is CircuitState.OPEN:
                    self._metrics.provider_circuit_opens_total.labels(provider=name).inc()
            if self._events is not None:
                stats = self._stats.get(name)
                self._schedule_event(
                    ProviderHealthChanged(
                        provider=name,
                        healthy=state is not CircuitState.OPEN,
                        circuit_state=str(state),
                        consecutive_failures=stats.breaker.consecutive_failures if stats else 0,
                    )
                )

        return observe

    def _schedule_event(self, event: Any) -> None:
        """Publish without awaiting.

        The circuit-breaker observer is called from synchronous code, and an event bus must never
        be able to fail or delay a routing decision. If no loop is running (unit tests
        constructing a router outside async context), the event is simply dropped.
        """
        if self._events is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._events.publish(event))

    def _publish_health(self) -> None:
        if self._metrics is None:
            return
        for name, stats in self._stats.items():
            self._metrics.provider_healthy.labels(provider=name).set(1 if stats.healthy else 0)

    # -- selection ---------------------------------------------------------------------

    def _capability_filter(self, request: CompletionRequest, provider: LLMProvider) -> str | None:
        """Return a rejection reason, or ``None`` if the provider qualifies."""
        caps = provider.capabilities
        if request.require_local and not caps.is_local:
            return "not_local"
        if request.needs_tools and not caps.supports_tools:
            return "no_tool_support"
        if request.needs_json and not caps.supports_json_mode:
            return "no_json_mode"
        if caps.quality_tier < request.min_quality_tier:
            return "quality_tier_too_low"
        prompt_tokens = request.conversation.approx_tokens()
        needed = prompt_tokens + (request.sampling.max_output_tokens or 512)
        if caps.context_window < needed:
            return "context_window_too_small"
        return None

    def _score(self, request: CompletionRequest, stats: ProviderStats, policy: RoutingPolicy) -> float:
        """Lower is better. Each policy is a different objective over the same signals."""
        provider = self._providers[stats.name]
        caps = provider.capabilities
        latency = stats.latency.estimate_ms(_LATENCY_PRIOR_MS)
        prompt_tokens = request.conversation.approx_tokens()
        cost = caps.projected_cost_usd(prompt_tokens, request.sampling.max_output_tokens or 512)
        # Saturation matters as much as raw speed: a fast provider with a full queue is slow.
        load = stats.inflight / max(1, caps.max_concurrency)

        if policy is RoutingPolicy.LOCAL_FIRST:
            return (0.0 if caps.is_local else 1000.0) + load * 10.0 + latency / 1000.0
        if policy is RoutingPolicy.CHEAPEST:
            return cost * 1000.0 + load * 0.5
        if policy is RoutingPolicy.FASTEST:
            return latency * (1.0 + load)
        if policy is RoutingPolicy.QUALITY_FIRST:
            return (3 - caps.quality_tier) * 1000.0 + cost * 100.0 + load * 5.0
        # BALANCED: normalise each signal to roughly [0, 1] and weight by what users notice.
        # Latency dominates perceived quality; cost is the operational constraint; tier is a
        # tiebreak rather than a driver.
        return (
            0.45 * min(latency / 4000.0, 1.0)
            + 0.30 * min(cost / 0.05, 1.0)
            + 0.15 * load
            + 0.10 * ((3 - caps.quality_tier) / 2.0)
        )

    def plan(self, request: CompletionRequest) -> RoutingDecision:
        """Compute the candidate ordering without executing anything.

        Exposed separately so the admin API can answer "what would you do with this request?"
        and so routing can be tested without a provider call.
        """
        policy = request.policy or self._default_policy
        excluded: dict[str, str] = {}
        eligible: list[ProviderStats] = []

        for name, provider in self._providers.items():
            reason = self._capability_filter(request, provider)
            if reason:
                excluded[name] = reason
                continue
            stats = self._stats[name]
            if stats.breaker.state is CircuitState.OPEN:
                excluded[name] = "circuit_open"
                continue
            eligible.append(stats)

        # Stable secondary sort by name keeps ordering deterministic when scores tie, which
        # makes routing tests reproducible.
        eligible.sort(key=lambda s: (self._score(request, s, policy), s.name))
        return RoutingDecision(
            candidates=tuple(s.name for s in eligible), policy=policy, excluded=excluded
        )

    # -- execution ---------------------------------------------------------------------

    def _record_success(self, name: str, *, latency_ms: float, ttft_ms: float | None,
                        response: CompletionResponse | None) -> None:
        stats = self._stats[name]
        stats.breaker.record_success()
        stats.latency.observe(latency_ms)
        if ttft_ms is not None:
            stats.ttft.observe(ttft_ms)
        stats.total_requests += 1
        if response is not None:
            stats.total_tokens += response.usage.total_tokens
            stats.total_cost_usd += response.cost_usd
        if self._metrics is not None:
            model = response.model if response else self._providers[name].capabilities.model_id
            self._metrics.llm_requests_total.labels(
                provider=name, model=model, outcome="success"
            ).inc()
            self._metrics.llm_duration.labels(provider=name, model=model).observe(latency_ms / 1000.0)

    def _record_failure(self, name: str, error: BaseException) -> None:
        stats = self._stats[name]
        stats.breaker.record_failure()
        stats.total_requests += 1
        stats.total_failures += 1
        if self._metrics is not None:
            self._metrics.llm_requests_total.labels(
                provider=name,
                model=self._providers[name].capabilities.model_id,
                outcome="error",
            ).inc()

    async def complete(
        self, request: CompletionRequest, *, budget: TurnBudget | None = None
    ) -> CompletionResponse:
        """Generate, failing over across candidates.

        Raises :class:`AllProvidersFailedError` when every candidate fails, with the per-attempt
        detail an operator needs to tell "all nodes down" from "this request is malformed".
        """
        if budget is not None:
            budget.check()

        decision = self.plan(request)
        if not decision.candidates:
            raise AllProvidersFailedError(
                "no provider satisfies this request's requirements",
                attempts=[{"provider": n, "reason": r} for n, r in decision.excluded.items()],
            )

        attempts: list[dict[str, Any]] = []
        limit = min(len(decision.candidates), self._max_fallbacks + 1)

        for index, name in enumerate(decision.candidates[:limit]):
            stats = self._stats[name]
            if not stats.breaker.allows_request():
                attempts.append({"provider": name, "error": "circuit_open"})
                continue

            provider = self._providers[name]
            started = self._clock()
            stats.inflight += 1
            if self._metrics is not None:
                self._metrics.llm_inflight.labels(provider=name).set(stats.inflight)
            try:
                async with self._slots[name]:
                    response = await asyncio.wait_for(
                        provider.complete(request), timeout=request.timeout_s
                    )
                elapsed = (self._clock() - started) * 1000.0
                self._record_success(name, latency_ms=elapsed, ttft_ms=None, response=response)
                if budget is not None:
                    budget.consume(cost_usd=response.cost_usd)
                if index > 0:
                    # Attribute the fallback to the provider that actually failed, not to the
                    # one that succeeded -- otherwise the metric points at the healthy node.
                    self._note_fallback(decision.candidates[index - 1], attempts)
                return response
            except asyncio.TimeoutError as exc:
                self._record_failure(name, exc)
                attempts.append({"provider": name, "error": "timeout", "timeout_s": request.timeout_s})
            except ProviderError as exc:
                self._record_failure(name, exc)
                attempts.append({"provider": name, "error": exc.kind, "detail": exc.message})
                if not exc.retryable:
                    # A malformed request fails identically everywhere; trying more providers
                    # just multiplies latency and cost.
                    break
            except Exception as exc:  # adapter bug or unexpected transport failure
                self._record_failure(name, exc)
                attempts.append({"provider": name, "error": type(exc).__name__})
            finally:
                stats.inflight -= 1
                if self._metrics is not None:
                    self._metrics.llm_inflight.labels(provider=name).set(stats.inflight)

        raise AllProvidersFailedError(
            f"all {len(attempts)} provider attempt(s) failed"
            + _attempt_summary(attempts),
            attempts=attempts,
        )

    def _note_fallback(self, failed_provider: str, attempts: Sequence[Mapping[str, Any]]) -> None:
        reason = "unknown"
        for attempt in attempts:
            if attempt.get("provider") == failed_provider:
                reason = str(attempt.get("error", "unknown"))
                break
        if self._metrics is not None:
            self._metrics.llm_fallbacks_total.labels(
                from_provider=failed_provider, reason=reason
            ).inc()
        self._schedule_event(
            ProviderFailedOver(from_provider=failed_provider, error_kind=reason)
        )

    async def stream(
        self, request: CompletionRequest, *, budget: TurnBudget | None = None
    ) -> AsyncIterator[StreamChunk]:
        """Stream, failing over **only before the first chunk**.

        The provider port requires adapters to establish their upstream connection before
        yielding, which is what makes a clean pre-first-token failover possible. After the first
        chunk reaches the caller, a failure propagates as an error -- the caller has already seen
        partial output and silently restarting would corrupt it.
        """
        if budget is not None:
            budget.check()

        decision = self.plan(request)
        if not decision.candidates:
            raise AllProvidersFailedError(
                "no provider satisfies this request's requirements",
                attempts=[{"provider": n, "reason": r} for n, r in decision.excluded.items()],
            )

        attempts: list[dict[str, Any]] = []
        limit = min(len(decision.candidates), self._max_fallbacks + 1)

        for index, name in enumerate(decision.candidates[:limit]):
            stats = self._stats[name]
            if not stats.breaker.allows_request():
                attempts.append({"provider": name, "error": "circuit_open"})
                continue

            provider = self._providers[name]
            started = self._clock()
            first_chunk_at: float | None = None
            emitted = False
            stats.inflight += 1
            if self._metrics is not None:
                self._metrics.llm_inflight.labels(provider=name).set(stats.inflight)

            try:
                iterator = provider.stream(request).__aiter__()
                # Pull the first chunk here, inside the failover loop: an error raised now is
                # still recoverable because nothing has reached the caller.
                try:
                    first = await asyncio.wait_for(iterator.__anext__(), timeout=request.timeout_s)
                except StopAsyncIteration:
                    first = None

                if index > 0:
                    self._note_fallback(decision.candidates[index - 1], attempts)

                usage = None
                if first is not None:
                    first_chunk_at = self._clock()
                    emitted = True
                    usage = first.usage
                    yield first
                    async for chunk in iterator:
                        if chunk.usage is not None:
                            usage = chunk.usage
                        yield chunk

                elapsed = (self._clock() - started) * 1000.0
                ttft = (first_chunk_at - started) * 1000.0 if first_chunk_at else None
                stats.breaker.record_success()
                stats.latency.observe(elapsed)
                if ttft is not None:
                    stats.ttft.observe(ttft)
                    if self._metrics is not None:
                        self._metrics.llm_ttft.labels(
                            provider=name, model=provider.capabilities.model_id
                        ).observe(ttft / 1000.0)
                stats.total_requests += 1
                if usage is not None:
                    stats.total_tokens += usage.total_tokens
                if self._metrics is not None:
                    self._metrics.llm_requests_total.labels(
                        provider=name, model=provider.capabilities.model_id, outcome="success"
                    ).inc()
                if budget is not None:
                    caps = provider.capabilities
                    cost = (
                        usage.cost_usd(caps.cost_per_1k_input_usd, caps.cost_per_1k_output_usd)
                        if usage
                        else 0.0
                    )
                    budget.consume(cost_usd=cost)
                return
            except Exception as exc:
                # asyncio.CancelledError is a BaseException and deliberately not caught -- a
                # cancelled turn must unwind, not fail over to another provider.
                self._record_failure(name, exc)
                kind = getattr(exc, "kind", None) or (
                    "timeout" if isinstance(exc, asyncio.TimeoutError) else type(exc).__name__
                )
                attempts.append({"provider": name, "error": kind})
                if emitted:
                    # Output already reached the caller. Failover here would duplicate text.
                    raise ProviderError(
                        f"{name}: stream failed after partial output",
                        provider=name,
                        model=provider.capabilities.model_id,
                        cause=exc,
                        retryable=False,
                        details={"partial_output": True},
                    ) from exc
                if isinstance(exc, ProviderError) and not exc.retryable:
                    break
            finally:
                stats.inflight -= 1
                if self._metrics is not None:
                    self._metrics.llm_inflight.labels(provider=name).set(stats.inflight)

        raise AllProvidersFailedError(
            f"all {len(attempts)} provider attempt(s) failed before streaming began"
            + _attempt_summary(attempts),
            attempts=attempts,
        )

    # -- introspection and lifecycle ---------------------------------------------------

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def get(self, name: str) -> LLMProvider | None:
        return self._providers.get(name)

    def stats(self) -> list[dict[str, Any]]:
        return [s.snapshot() for s in self._stats.values()]

    async def health(self) -> dict[str, Any]:
        """Probe every provider concurrently, updating the health gauge."""
        async def probe(name: str, provider: LLMProvider) -> tuple[str, dict[str, Any]]:
            try:
                status = await asyncio.wait_for(provider.health(), timeout=10.0)
                return name, {
                    "healthy": status.healthy,
                    "detail": status.detail,
                    "latency_ms": round(status.latency_ms, 1),
                }
            except Exception as exc:
                return name, {"healthy": False, "detail": type(exc).__name__, "latency_ms": None}

        results = await asyncio.gather(*(probe(n, p) for n, p in self._providers.items()))
        report = dict(results)
        if self._metrics is not None:
            for name, status in report.items():
                self._metrics.dependency_up.labels(dependency=f"provider:{name}").set(
                    1 if status["healthy"] else 0
                )
        return report

    def reset_circuit(self, name: str) -> None:
        """Manual recovery, exposed to admins for when a provider is known to be fixed."""
        stats = self._stats.get(name)
        if stats is None:
            raise ValidationError(f"unknown provider {name!r}", details={"known": list(self._providers)})
        stats.breaker.reset()

    async def close(self) -> None:
        await asyncio.gather(
            *(p.close() for p in self._providers.values()), return_exceptions=True
        )
