"""Per-provider request and token quotas, and rate-limit cooldowns (ADR-030 Phase 2).

Hosted providers fail differently from self-hosted ones. A local vLLM that is out of capacity
is *slow*; a free hosted tier that is out of quota answers ``429`` instantly and keeps doing so
until a window resets -- a minute for a per-minute limit, hours for a daily one. Treating that
like any other failure goes wrong in two ways at once:

* **It spends quota to discover quota.** The circuit breaker opens after N consecutive failures
  and half-opens after a fixed interval, so on an exhausted daily tier every half-open probe is
  one more request burned learning that the day has not reset.
* **It reads as an outage.** Consecutive ``429``s open the breaker, the health gauge drops, and a
  quota day looks on the dashboard exactly like a dead provider.

So this module answers two separate questions, neither of which is health:

1. **Would this request exceed a declared budget?** ``AURA_PROVIDER_<NAME>_{RPM,RPD,TPM,TPD}``
   declare the provider's published limits. Before sending, the router asks
   :meth:`QuotaTracker.try_reserve`; a request that would exceed one is skipped without being
   sent. Every limit defaults to 0, meaning undeclared, and an undeclared limit never refuses
   anything -- a configuration written before this existed routes exactly as it did.
2. **Did the provider tell us to back off?** A ``429`` puts the provider in cooldown until the
   reset its own headers name (:func:`retry_after_seconds`), or ``default_cooldown_s`` when it
   names none. Cooldown is *not* a circuit-breaker failure.

Windows: per-minute limits use an exact sliding 60s window. Per-day limits use the UTC calendar
day, which is what Cloudflare and OpenRouter reset on. Groq's daily window is rolling, so after
UTC midnight this may admit a request Groq still refuses. That request costs one ``429``, which
then sets a cooldown from Groq's own reset header; it never costs a loop.

Token accounting is conservative. A reservation counts the prompt estimate *plus* the full
``max_output_tokens`` the request allows, because that is the most the provider can charge, and
it is settled to the provider's reported usage when the response arrives. A request whose
worst case alone exceeds the per-minute token limit is refused outright: that provider can never
serve it.

Persistence is best-effort through the :class:`~aura_core.ports.storage.Cache` port: the daily
counters and any active cooldown, so a gateway restart does not forget it already spent the
day. Per-minute windows are not persisted; a restart loses at most a minute of history. With
several gateway replicas the stored counters are last-writer-wins and therefore approximate. The
provider's own ``429`` remains the authority, and this module only avoids asking needlessly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping

__all__ = [
    "QuotaLimits",
    "QuotaReservation",
    "QuotaTracker",
    "retry_after_seconds",
]

logger = logging.getLogger(__name__)

_MINUTE_S = 60.0
#: A reset further away than this is treated as a header we misread rather than a real one; the
#: cooldown is capped here instead of benching a provider for weeks on a parse surprise.
_MAX_COOLDOWN_S = 26 * 3600.0
_MIN_COOLDOWN_S = 1.0
_KEY_PREFIX = "llm:quota:v1"


@dataclass(frozen=True, slots=True)
class QuotaLimits:
    """A provider's declared limits. ``0`` means undeclared, never "zero allowed"."""

    rpm: int = 0
    rpd: int = 0
    tpm: int = 0
    tpd: int = 0

    def __post_init__(self) -> None:
        if min(self.rpm, self.rpd, self.tpm, self.tpd) < 0:
            raise ValueError("quota limits cannot be negative")

    @property
    def declared(self) -> bool:
        return bool(self.rpm or self.rpd or self.tpm or self.tpd)


@dataclass(slots=True)
class _Entry:
    """One request inside the per-minute window. Mutable so settling can correct its tokens."""

    at: float
    tokens: int


@dataclass(slots=True)
class QuotaReservation:
    """A request counted against a provider's budget, to be settled once usage is known."""

    provider: str
    day: str
    estimated_tokens: int
    _entry: _Entry | None = None
    settled: bool = False


@dataclass(slots=True)
class _State:
    limits: QuotaLimits
    window: deque[_Entry] = field(default_factory=deque)
    day: str = ""
    day_requests: int = 0
    day_tokens: int = 0
    cooldown_until: float = 0.0
    cooldown_reason: str = ""


def _utc_day(wall: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(wall))


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def _parse_duration(value: str) -> float | None:
    """Parse a Go-style duration such as ``2m59.56s``, ``7.66s``, ``1h2m`` or ``250ms``.

    This is the shape Groq uses in ``x-ratelimit-reset-requests`` / ``-tokens``. A bare number is
    read as seconds.
    """
    value = value.strip().lower()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    parts = _DURATION_PART.findall(value)
    if not parts or "".join(n + u for n, u in parts) != value:
        return None
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    return sum(float(n) * scale[u] for n, u in parts)


def _parse_reset(value: str, now: float) -> float | None:
    """Seconds until a reset expressed as a duration, epoch seconds, or epoch milliseconds.

    OpenRouter's ``X-RateLimit-Reset`` is epoch **milliseconds**; Groq's reset headers are
    durations. A plain number is disambiguated by magnitude: anything past 1e12 is milliseconds
    since the epoch, past 1e9 seconds since the epoch, and smaller a relative number of seconds.
    """
    try:
        number = float(value.strip())
    except ValueError:
        return _parse_duration(value)
    if number > 1e12:
        return number / 1000.0 - now
    if number > 1e9:
        return number - now
    return number


def retry_after_seconds(headers: Mapping[str, str] | Any, *, now: float | None = None) -> float | None:
    """How long a ``429`` response says to wait, or ``None`` if it does not say.

    ``Retry-After`` wins when present (seconds or an HTTP date). Otherwise the *largest* of the
    ``x-ratelimit-reset*`` headers is used: a ``429`` does not say which limit tripped, and
    waiting for the later reset is the only choice that cannot immediately ``429`` again.
    """
    if headers is None:
        return None
    now = time.time() if now is None else now
    try:
        lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    except AttributeError:
        return None

    raw = lowered.get("retry-after")
    if raw:
        seconds = _parse_duration(raw)
        if seconds is None:
            try:
                seconds = parsedate_to_datetime(raw).timestamp() - now
            except (TypeError, ValueError, IndexError, OverflowError):
                seconds = None
        if seconds is not None:
            return max(0.0, seconds)

    resets = [
        parsed
        for key, value in lowered.items()
        if key.startswith("x-ratelimit-reset")
        and (parsed := _parse_reset(value, now)) is not None
    ]
    return max(0.0, max(resets)) if resets else None


class QuotaTracker:
    """Request/token budgets and cooldowns for every provider the router knows.

    Synchronous on the hot path on purpose: :meth:`ProviderRouter.plan` is synchronous (the
    admin API calls it to explain a routing decision), and a check-then-reserve with no
    ``await`` between them is atomic under asyncio, so concurrent turns cannot both take the last
    unit of a budget. Persistence is the only asynchronous part and never blocks a decision.
    """

    def __init__(
        self,
        limits: Mapping[str, QuotaLimits] | None = None,
        *,
        wall: Callable[[], float] | None = None,
        default_cooldown_s: float = 30.0,
        store: Any = None,
        metrics: Any = None,
    ) -> None:
        self._limits = dict(limits or {})
        self._wall = wall or time.time
        self._default_cooldown_s = max(_MIN_COOLDOWN_S, default_cooldown_s)
        self._store = store
        self._metrics = metrics
        self._states: dict[str, _State] = {}
        #: Every provider whose cooldown should survive a restart, declared limits or not. The
        #: router registers its whole pool; without that, a provider with no declared limits
        #: would be unknown at hydrate time and its stored cooldown silently dropped.
        self._known: set[str] = set(self._limits)
        self._hydrated = store is None
        self._hydrate_lock: asyncio.Lock | None = None
        self._pending: set[asyncio.Task[Any]] = set()

    # -- state -------------------------------------------------------------------------

    def track(self, names: Any) -> None:
        """Register provider names whose stored state :meth:`ensure_hydrated` should load."""
        self._known.update(names)

    def limits(self, name: str) -> QuotaLimits:
        return self._limits.get(name, QuotaLimits())

    def _state(self, name: str) -> _State:
        state = self._states.get(name)
        if state is None:
            state = _State(limits=self.limits(name), day=_utc_day(self._wall()))
            self._states[name] = state
        return state

    def _roll(self, state: _State, now: float) -> None:
        today = _utc_day(now)
        if state.day != today:
            state.day, state.day_requests, state.day_tokens = today, 0, 0
        horizon = now - _MINUTE_S
        while state.window and state.window[0].at <= horizon:
            state.window.popleft()

    # -- decisions ---------------------------------------------------------------------

    def refusal(self, name: str, estimated_tokens: int) -> str | None:
        """Why ``name`` must not be sent this request right now, or ``None`` if it may."""
        now = self._wall()
        state = self._states.get(name)
        if state is not None and state.cooldown_until > now:
            return "rate_limited"
        limits = self.limits(name)
        if not limits.declared:
            return None
        state = self._state(name)
        self._roll(state, now)
        estimated = max(0, estimated_tokens)
        if limits.tpm and estimated > limits.tpm:
            return "quota_request_too_large"
        if limits.rpd and state.day_requests >= limits.rpd:
            return "quota_rpd"
        if limits.tpd and state.day_tokens + estimated > limits.tpd:
            return "quota_tpd"
        if limits.rpm and len(state.window) >= limits.rpm:
            return "quota_rpm"
        if limits.tpm and sum(e.tokens for e in state.window) + estimated > limits.tpm:
            return "quota_tpm"
        return None

    def try_reserve(
        self, name: str, estimated_tokens: int
    ) -> tuple[QuotaReservation | None, str | None]:
        """Count a request against ``name``'s budget, or say why it cannot be sent."""
        reason = self.refusal(name, estimated_tokens)
        if reason is not None:
            self._count(name, reason)
            return None, reason
        if not self.limits(name).declared:
            return QuotaReservation(provider=name, day="", estimated_tokens=0), None
        now = self._wall()
        state = self._state(name)
        estimated = max(0, estimated_tokens)
        entry = _Entry(at=now, tokens=estimated)
        state.window.append(entry)
        state.day_requests += 1
        state.day_tokens += estimated
        self._share(name, state.day, requests=1, tokens=estimated)
        return QuotaReservation(
            provider=name, day=state.day, estimated_tokens=estimated, _entry=entry
        ), None

    def settle(self, reservation: QuotaReservation | None, actual_tokens: int | None) -> None:
        """Replace a reservation's estimate with what the provider reported.

        ``None`` keeps the estimate, the conservative choice when nothing was reported (a
        timeout may still have generated tokens). ``0`` is for a request the provider refused
        before generating anything, such as a ``429``.
        """
        if reservation is None or reservation.settled or reservation._entry is None:
            return
        reservation.settled = True
        if actual_tokens is None:
            return
        actual = max(0, actual_tokens)
        delta = actual - reservation._entry.tokens
        reservation._entry.tokens = actual
        state = self._states.get(reservation.provider)
        if state is not None and state.day == reservation.day:
            state.day_tokens = max(0, state.day_tokens + delta)
            self._share(reservation.provider, reservation.day, tokens=delta)

    def cooldown(self, name: str, seconds: float | None, *, reason: str = "rate_limited") -> float:
        """Bench ``name`` for ``seconds`` (the provider's own reset when it gave one).

        Returns the cooldown applied. Never shortens an existing cooldown: two concurrent
        ``429``s naming different resets must leave the later one in force.
        """
        wait = self._default_cooldown_s if seconds is None else seconds
        wait = min(_MAX_COOLDOWN_S, max(_MIN_COOLDOWN_S, wait))
        state = self._state(name)
        until = self._wall() + wait
        if until > state.cooldown_until:
            state.cooldown_until, state.cooldown_reason = until, reason
            self._persist(name)
        self._count(name, reason)
        return wait

    def cooldown_remaining(self, name: str) -> float:
        state = self._states.get(name)
        if state is None:
            return 0.0
        return max(0.0, state.cooldown_until - self._wall())

    def snapshot(self, name: str) -> dict[str, Any]:
        limits = self.limits(name)
        state = self._states.get(name)
        now = self._wall()
        if state is not None:
            self._roll(state, now)
        return {
            "limits": {"rpm": limits.rpm, "rpd": limits.rpd, "tpm": limits.tpm, "tpd": limits.tpd},
            "minute_requests": len(state.window) if state else 0,
            "minute_tokens": sum(e.tokens for e in state.window) if state else 0,
            "day_requests": state.day_requests if state else 0,
            "day_tokens": state.day_tokens if state else 0,
            "cooldown_remaining_s": round(self.cooldown_remaining(name), 1),
            "cooldown_reason": (state.cooldown_reason if state and state.cooldown_until > now else None),
        }

    def _count(self, name: str, reason: str) -> None:
        counter = getattr(self._metrics, "llm_quota_events_total", None)
        if counter is None:
            return
        try:
            counter.labels(provider=name, reason=reason).inc()
        except Exception:  # pragma: no cover - metrics must never fail a routing decision
            pass

    # -- persistence -------------------------------------------------------------------

    def _day_key(self, name: str, day: str) -> str:
        return f"{_KEY_PREFIX}:{name}:day:{day}"

    def _shared_key(self, name: str, day: str, kind: str) -> str:
        """One atomic counter per provider, day and kind (``requests``/``tokens``), shared by
        every process (audit FSA-025)."""
        return f"{_KEY_PREFIX}:{name}:day:{day}:{kind}"

    def _apply_shared(self, name: str, day: str, kind: str, total: int) -> None:
        """A process learns the global spend from the counter's returned total."""
        state = self._states.get(name)
        if state is None or state.day != day:
            return
        if kind == "requests":
            state.day_requests = max(state.day_requests, int(total))
        else:
            state.day_tokens = max(state.day_tokens, int(total))

    def _share(self, name: str, day: str, *, requests: int = 0, tokens: int = 0) -> None:
        """Add this process's spend to the shared counters and fold the totals back in. Fire and
        forget, like ``_persist``: the cache must never delay or fail a generation."""
        if self._store is None or (not requests and not tokens):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        store = self._store

        async def share() -> None:
            for kind, amount in (("requests", requests), ("tokens", tokens)):
                if not amount:
                    continue
                try:
                    total = await store.incr(self._shared_key(name, day, kind), amount=amount, ttl_s=2 * 86400)
                except Exception:  # best-effort: the Cache port's contract
                    return
                self._apply_shared(name, day, kind, total)

        task = loop.create_task(share())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _cooldown_key(self, name: str) -> str:
        return f"{_KEY_PREFIX}:{name}:cooldown"

    async def ensure_hydrated(self) -> None:
        """Load today's counters and live cooldowns from the store, once.

        Lazy, like every adapter's first network use, so constructing the router opens nothing.
        A store failure leaves the tracker empty: the cost is re-learning the day from ``429``s,
        which is what happened before this existed.
        """
        if self._hydrated:
            return
        if self._hydrate_lock is None:
            self._hydrate_lock = asyncio.Lock()
        async with self._hydrate_lock:
            if self._hydrated:
                return
            try:
                await self._hydrate()
            except Exception as exc:
                logger.warning("quota hydrate failed; starting from empty counters",
                               extra={"error": type(exc).__name__})
            self._hydrated = True

    async def _hydrate(self) -> None:
        now = self._wall()
        today = _utc_day(now)
        names = self._known | set(self._states)
        keys: dict[str, tuple[str, str]] = {}
        for name in names:
            keys[self._cooldown_key(name)] = (name, "cooldown")
            if self.limits(name).declared:
                keys[self._day_key(name, today)] = (name, "day")
                keys[self._shared_key(name, today, "requests")] = (name, "shared_requests")
                keys[self._shared_key(name, today, "tokens")] = (name, "shared_tokens")
        if not keys:
            return
        found = await self._store.get_many(list(keys))
        for key, raw in (found or {}).items():
            name, kind = keys[key]
            data = json.loads(raw)  # a bare integer for the shared counters, an object otherwise
            state = self._state(name)
            self._roll(state, now)
            if kind == "shared_requests":
                state.day_requests = max(state.day_requests, int(data))
            elif kind == "shared_tokens":
                state.day_tokens = max(state.day_tokens, int(data))
            elif kind == "day":
                # Written by processes before the shared counters existed; read until they age out.
                state.day_requests = max(state.day_requests, int(data.get("requests", 0)))
                state.day_tokens = max(state.day_tokens, int(data.get("tokens", 0)))
            elif float(data.get("until", 0.0)) > now and float(data["until"]) > state.cooldown_until:
                state.cooldown_until = float(data["until"])
                state.cooldown_reason = str(data.get("reason", "rate_limited"))

    def _persist(self, name: str) -> None:
        """Write ``name``'s daily counters and cooldown without awaiting.

        Fire-and-forget for the same reason the router publishes events that way: a cache must
        never be able to fail or delay a generation. Dropped when no loop is running.
        """
        if self._store is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        state = self._states.get(name)
        if state is None:
            return
        now = self._wall()
        writes: list[tuple[str, bytes, int]] = []
        # The day's spend is no longer written here: it is an atomic shared counter that every
        # process increments (``_share``, audit FSA-025), not a per-process figure to overwrite.
        if state.cooldown_until > now:
            payload = {"until": state.cooldown_until, "reason": state.cooldown_reason}
            ttl = max(1, int(state.cooldown_until - now) + 1)
            writes.append((self._cooldown_key(name), json.dumps(payload).encode(), ttl))
        if not writes:
            return

        async def write() -> None:
            for key, value, ttl in writes:
                try:
                    await self._store.set(key, value, ttl_s=ttl)
                except Exception:  # best-effort: the Cache port's contract
                    return

        task = loop.create_task(write())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def flush(self) -> None:
        """Wait for pending writes. For tests and orderly shutdown; never on the hot path."""
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)
