"""Provider router: selection, failover, load balancing, budgets (ADR-003).

The single most important component in the platform, because generation concurrency is the
binding constraint at every scaling stage (Scalability Report §6).

Responsibilities, in order:

1. **Filter** candidates by declared capability (tools, streaming, JSON mode, context window,
   quality tier, locality, and -- from the request's :class:`WorkloadHint` -- pool, modality,
   language, reasoning, minimum context) and by health -- an open circuit removes a provider.
2. **Order** them by the request's :class:`RoutingPolicy`, within pool tiers: providers that
   declare the requested pool first, generalists after them as fallback (P2.3).
3. **Admit** through the capacity group's bounded priority queue (P2.4, ``llm/admission.py``).
   Saturation raises :class:`OverloadedError` -- it does **not** fail over, because spilling to
   the next candidate whenever the preferred one is busy would silently turn ``local_first`` into
   "whichever is idle", sending turns to a provider the operator ranked last on purpose.
4. **Attempt** each candidate in turn, up to ``max_fallbacks``, with per-attempt timeout.
5. **Record** queue wait, time to first token, decode time, tokens and failures (P2.5) as a
   :class:`GenerationTiming` value handed to the metric catalogue, so the next decision is
   better informed and the latency is decomposed on the dashboard.

Data classification (ADR-030 Phase 4, ``llm/sensitivity.py``) is a step-1 filter like any
capability: a provider whose ``max_data_classification`` is below the request's is excluded
before ordering, and when that leaves nothing the turn fails rather than sending the content
somewhere it may not go. Every generation a third-party provider serves is written to the audit
log with its classification and token counts, never its content.

Quotas (ADR-030 Phase 2, ``llm/quota.py``) touch steps 1 and 4 without being health. A provider
whose declared budget this request would exceed, or which is cooling down after a ``429``, is
excluded at planning time and skipped if its budget runs out between planning and sending. A
``429`` benches the provider until the reset it named and moves on to the next candidate. It
never counts against the circuit breaker, because "come back in an hour" is not "broken".

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
import contextlib
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Callable, Iterator, Mapping, Sequence

from ..domain.errors import (
    AllProvidersFailedError,
    AuraError,
    BudgetExceededError,
    CapabilityUnavailableError,
    ProviderError,
    UserQuotaExhaustedError,
    ValidationError,
)
from ..domain.events import ProviderFailedOver, ProviderHealthChanged
from ..domain.llm import (
    FALLBACK_POOLS,
    CompletionRequest,
    CompletionResponse,
    DataClassification,
    GenerationTiming,
    Modality,
    RoutingPolicy,
    StreamChunk,
    TokenUsage,
    WorkloadHint,
)
from ..context.tokenizer import Tokenizer, tokenizer_for
from ..observability.tracing import record_span
from ..ports.llm import LLMProvider
from .admission import AdmissionController, AdmissionTicket, PoolLimits
from .circuit import CircuitBreaker, CircuitState, LatencyTracker, ProviderStats
from .deadline import time_left
from .quota import QuotaReservation, QuotaTracker
from .sensitivity import effective_classification
from .workload import effective_workload

__all__ = [
    "GENERATION_PURPOSES",
    "ProviderRouter",
    "PurposeUsage",
    "RoutingDecision",
    "TurnBudget",
    "TurnUsage",
    "current_generation_purpose",
    "current_turn_budget",
    "generation_purpose",
    "no_turn_budget",
    "turn_budget_scope",
]

logger = logging.getLogger(__name__)

#: HTTP statuses that say this provider's credentials or permissions are wrong (F15): a fault of
#: the deployment's configuration for that one provider, not of the request and not of its health.
_CREDENTIAL_STATUSES = frozenset({401, 403})


def _fault_kind(exc: BaseException) -> str:
    """What a failed attempt says, and so what the router may learn from it (F15).

    * ``"health"`` -- the provider is down or struggling: 5xx, timeouts, transport failures, an
      adapter crash. Only these count toward the circuit breaker.
    * ``"credentials"`` -- 401/403, or a provider with no usable key. Says nothing about the
      request (another provider can serve it) nor about the provider's health.
    * ``"request"`` -- any other non-retryable failure (400, 404, 413, 422, ...): the request was
      refused. Counting it toward the breaker would let one malformed request take a healthy
      provider away from every other caller.
    """
    if not isinstance(exc, ProviderError):
        return "health"
    status = exc.details.get("status")
    if status in _CREDENTIAL_STATUSES or exc.details.get("operation") == "configure":
        return "credentials"
    if exc.retryable or status == 408:
        return "health"
    return "request"


def _image_tokens(request: CompletionRequest) -> int:
    """A coarse per-image estimate, so an image request is not routed to a provider whose
    context window the encoded image alone would fill."""
    return sum(i.approx_tokens for i in request.images)


#: Prompt counts memoised per (conversation, tokenizer): ``plan`` counts one request for every
#: candidate's filter, score and quota check, and the conversation is immutable.
_PROMPT_MEMO_LIMIT = 512

# Prior used to rank a provider with no latency history. Deliberately mid-range: a new provider
# should neither win by default (unknown == 0) nor be excluded (unknown == infinity). Scaled by the
# declared speed tier (tier 2, the default, keeps exactly this value).
_LATENCY_PRIOR_MS = 1500.0
_SPEED_TIER_PRIOR = {1: 2.0, 2: 1.0, 3: 0.5}


#: Exclusions caused by what a request *contains* rather than by a provider's health or
#: abilities. When one of these took a provider out and nothing else answered, the turn failed
#: for lack of capacity for this content, not because inference is down (CapabilityUnavailable).
_CONTENT_EXCLUSIONS = frozenset({"data_classification", "third_party_image"})


def _exhausted(
    message: str,
    decision: "RoutingDecision",
    attempts: Sequence[Mapping[str, Any]],
    quota: QuotaTracker | None = None,
) -> AuraError:
    """The error for a request no provider answered, naming content refusals when there were any.

    A provider excluded for content would have been tried had the turn not contained what it
    does, so "the model is not answering, retry" is the wrong thing to tell the user: retrying
    sends the same content to the same refusal.
    """
    over_allowance = sorted(
        {n for n, r in decision.excluded.items() if r == "user_quota_tpd"}
        | {str(a.get("provider")) for a in attempts if a.get("error") == "user_quota_tpd"}
    )
    if over_allowance and quota is not None:
        # Checked before content refusals: the provider this person's allowance took out would
        # otherwise have answered, so "come back after the reset" is the true answer (WP2).
        return UserQuotaExhaustedError(
            f"{message}; today's token allowance for this user is spent",
            details=quota.user_quota_details(over_allowance),
        )
    refused = sorted(n for n, r in decision.excluded.items() if r in _CONTENT_EXCLUSIONS)
    if refused:
        return CapabilityUnavailableError(
            f"{message}; no provider permitted to receive this turn's content answered",
            attempts=list(attempts),
            details={
                "refused_for_content": refused,
                "data_classification": decision.data_classification,
            },
        )
    return AllProvidersFailedError(message, attempts=list(attempts))


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
    #: The requested pool ("default" when none), and which candidates are pool members rather
    #: than generalist fallbacks -- both surfaced so an operator can see a fallback happening.
    pool: str = "default"
    pool_members: tuple[str, ...] = ()
    #: The request's effective data classification (ADR-030 §4), so an operator can see why a
    #: hosted provider was skipped.
    data_classification: str = ""

    @property
    def primary(self) -> str | None:
        return self.candidates[0] if self.candidates else None


#: Why a generation was made: the bounded label on usage breakdowns and on the
#: ``aura_llm_generation_*`` series (KAN-106/107). ``answer``/``repair`` are the kernel's own
#: generations (counted against ``max_generations``); the rest are made on the turn's behalf by
#: other engines (counted against ``max_engine_generations``). ``engine`` is any unlabelled one.
GENERATION_PURPOSES = frozenset(
    {"answer", "repair", "planner", "reasoning", "summary", "engine"}
)

_AMBIENT_PURPOSE: ContextVar[str | None] = ContextVar("aura_generation_purpose", default=None)


@contextlib.contextmanager
def generation_purpose(purpose: str) -> Iterator[None]:
    """Label every generation in the block (unless its request names its own purpose in
    ``metadata["purpose"]``). Ambient, like the turn budget, so the planner and the reasoner
    need no new argument."""
    token = _AMBIENT_PURPOSE.set(purpose)
    try:
        yield
    finally:
        try:
            _AMBIENT_PURPOSE.reset(token)
        except ValueError:  # pragma: no cover - finalised from another context (see deadline)
            pass


def current_generation_purpose() -> str | None:
    """The ambient purpose label, if a :func:`generation_purpose` block is active."""
    return _AMBIENT_PURPOSE.get()


def _purpose_of(request: CompletionRequest, explicit: bool) -> str:
    named = request.metadata.get("purpose") if request.metadata else None
    purpose = named or _AMBIENT_PURPOSE.get() or ("answer" if explicit else "engine")
    return purpose if purpose in GENERATION_PURPOSES else "engine"


@dataclass
class PurposeUsage:
    """Tokens and generations of one purpose within a turn."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    generations: int = 0
    estimated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "generations": self.generations,
            "estimated": self.estimated,
        }


@dataclass
class TurnUsage:
    """Every generation the turn made, summed, with a per-purpose breakdown (KAN-106).

    ``estimated`` is true when any generation's usage was AURA's own estimate rather than the
    provider's report, so a total is never presented as a measurement it is not.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    generations: int = 0
    estimated: bool = False
    by_purpose: dict[str, PurposeUsage] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, usage: TokenUsage, purpose: str) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.generations += 1
        self.estimated = self.estimated or usage.estimated
        part = self.by_purpose.setdefault(purpose, PurposeUsage())
        part.prompt_tokens += usage.prompt_tokens
        part.completion_tokens += usage.completion_tokens
        part.generations += 1
        part.estimated = part.estimated or usage.estimated

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "generations": self.generations,
            "estimated": self.estimated,
            "by_purpose": {k: v.as_dict() for k, v in sorted(self.by_purpose.items())},
        }


@dataclass
class TurnBudget:
    """Per-turn generation and cost ceiling, and the turn's usage ledger.

    Replaces the legacy ``services/budget.py``, which caps Gemini calls at **2 per request** via
    a ``ContextVar`` -- making any agentic task needing three tool round-trips structurally
    impossible. Here the ceiling is expressed in generations *and* dollars, is per turn rather
    than global, and is configurable.

    Two generation counts, so neither kind can starve the other (KAN-107): ``max_generations``
    bounds the kernel's own (the answer, a schema repair); ``max_engine_generations`` bounds
    those other engines make on the turn's behalf (planning and replanning, reasoning samples
    and tree-of-thoughts nodes, summaries). Before it, the second kind was charged its cost but
    never counted, so a turn's generation count had no ceiling at all. 0 = no engine cap.
    """

    max_generations: int = 8
    max_cost_usd: float = 0.0  # 0 disables the cost ceiling
    generations_used: int = 0
    cost_used_usd: float = 0.0
    max_engine_generations: int = 0
    engine_generations_used: int = 0
    #: Every generation charged to this turn (and to the turns it was started from), summed.
    usage: TurnUsage = field(default_factory=TurnUsage)
    #: The budget of the turn this one was started from (a delegated sub-turn), which is
    #: charged every dollar and token this one spends: the same person is paying for both.
    parent: "TurnBudget | None" = field(default=None, repr=False, compare=False)

    def check(self) -> None:
        if self.generations_used >= self.max_generations:
            raise BudgetExceededError(
                "turn exceeded its generation budget",
                details={"limit": self.max_generations, "used": self.generations_used},
            )
        self.check_cost()

    def start_engine_generation(self) -> None:
        """Count one engine generation before it is sent, or refuse it (KAN-107).

        Counted at the start, not on success, so concurrent samples (self-consistency gathers
        its samples at once) cannot all pass the check and overshoot the cap together.

        Checked and charged on this turn and on every turn it was started from, like cost
        (F30): a delegated sub-turn gets a fresh budget of its own, so without walking the
        parent chain each delegation would reset the root turn's engine ceiling.
        """
        chain: list[TurnBudget] = []
        budget: TurnBudget | None = self
        while budget is not None:
            chain.append(budget)
            budget = budget.parent
        for budget in chain:
            if (
                budget.max_engine_generations
                and budget.engine_generations_used >= budget.max_engine_generations
            ):
                raise BudgetExceededError(
                    "turn exceeded its engine generation budget",
                    details={
                        "limit": budget.max_engine_generations,
                        "used": budget.engine_generations_used,
                        "scope": "engine",
                    },
                )
        for budget in chain:
            budget.engine_generations_used += 1

    def check_cost(self) -> None:
        """The cost ceiling alone, of this turn and of every turn it was started from (F30)."""
        budget: TurnBudget | None = self
        while budget is not None:
            if budget.max_cost_usd and budget.cost_used_usd >= budget.max_cost_usd:
                raise BudgetExceededError(
                    "turn exceeded its cost budget",
                    details={
                        "limit_usd": budget.max_cost_usd,
                        "used_usd": round(budget.cost_used_usd, 6),
                    },
                )
            budget = budget.parent

    def consume(
        self,
        *,
        cost_usd: float = 0.0,
        generation: bool = True,
        usage: TokenUsage | None = None,
        purpose: str = "answer",
    ) -> None:
        """Charge one generation's cost and usage; count it against ``max_generations`` if
        ``generation``.

        The count bounds the generations the kernel makes itself (the answer, a schema repair).
        Generations made on the turn's behalf by other engines (``generation=False``) are
        counted against ``max_engine_generations`` instead, when they start
        (:meth:`start_engine_generation`), so a long tool loop cannot leave the answer with no
        generation left. Cost and usage go to this turn and every turn it was started from.
        """
        if generation:
            self.generations_used += 1
        budget: TurnBudget | None = self
        while budget is not None:
            budget.cost_used_usd += cost_usd
            if usage is not None:
                budget.usage.add(usage, purpose)
            budget = budget.parent

    @property
    def remaining_generations(self) -> int:
        return max(0, self.max_generations - self.generations_used)


_AMBIENT_TURN_BUDGET: ContextVar[TurnBudget | None] = ContextVar(
    "aura_turn_budget", default=None
)


def current_turn_budget() -> TurnBudget | None:
    return _AMBIENT_TURN_BUDGET.get()


@contextlib.contextmanager
def turn_budget_scope(budget: TurnBudget) -> Iterator[TurnBudget]:
    """Make ``budget`` the turn budget every generation in the block is charged to (F30).

    Ambient, like the generation deadline and the data classification: the planner, the
    reasoner and the agentic loop call the router without a budget argument, so a budget only
    the kernel's own calls carried left the turn's cost ceiling unenforced for every other
    generation, and the turn's reported cost was that of its last generation alone.
    """
    token = _AMBIENT_TURN_BUDGET.set(budget)
    try:
        yield budget
    finally:
        try:
            _AMBIENT_TURN_BUDGET.reset(token)
        except ValueError:  # pragma: no cover - finalised from another context (see deadline)
            pass


@contextlib.contextmanager
def no_turn_budget() -> Iterator[None]:
    """Run the block charged to no turn: for work that outlives the turn that started it."""
    token = _AMBIENT_TURN_BUDGET.set(None)
    try:
        yield
    finally:
        try:
            _AMBIENT_TURN_BUDGET.reset(token)
        except ValueError:  # pragma: no cover - see `turn_budget_scope`
            pass


def _resolve_budget(budget: TurnBudget | None) -> tuple[TurnBudget | None, bool]:
    """The budget a call is charged to, and whether the caller passed it explicitly.

    An explicit budget is checked in full (generations and cost); the ambient one for cost and
    for its engine-generation cap, which this call then counts against (KAN-107).
    """
    if budget is not None:
        budget.check()
        return budget, True
    ambient = _AMBIENT_TURN_BUDGET.get()
    if ambient is not None:
        ambient.check_cost()
        ambient.start_engine_generation()
    return ambient, False


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
        admission: AdmissionController | None = None,
        default_max_queue: int = -1,
        default_queue_timeout_s: float = 0.0,
        group_limits: Mapping[str, PoolLimits] | None = None,
        user_max_concurrent: int = 0,
        quota: QuotaTracker | None = None,
        audit: Any = None,
        default_timeout_s: float = 60.0,
        stream_max_s: float = 0.0,
    ) -> None:
        """``default_max_queue``/``default_queue_timeout_s``/``user_max_concurrent`` default to
        the pre-P2.4 behaviour (unbounded wait, no per-user quota). Each capacity group's slot
        count is the smallest ``max_concurrency`` among its providers unless ``group_limits``
        names it explicitly. ``quota`` defaults to a tracker with no declared limits: nothing is
        refused by budget, and a ``429`` still benches its provider instead of tripping the
        breaker."""
        if not providers:
            raise ValidationError("ProviderRouter requires at least one provider")
        #: The deadline for a request that names none (``CompletionRequest.timeout_s=None``).
        self._default_timeout_s = default_timeout_s
        #: The most a stream may run after its first chunk (PP-07); 0 = no total limit. Each
        #: chunk is separately bounded by the request's own timeout, whatever this is.
        self._stream_max_s = stream_max_s
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
        self._groups: dict[str, str] = {
            name: (p.capabilities.capacity_group or name) for name, p in self._providers.items()
        }
        if admission is None:
            limits: dict[str, PoolLimits] = {}
            for name, provider in self._providers.items():
                group = self._groups[name]
                cap = max(1, provider.capabilities.max_concurrency)
                if group in limits:
                    cap = min(cap, limits[group].max_concurrent)
                limits[group] = PoolLimits(
                    max_concurrent=cap,
                    max_queue=default_max_queue,
                    queue_timeout_s=default_queue_timeout_s,
                )
            limits.update(group_limits or {})
            admission = AdmissionController(
                limits,
                user_max_concurrent=user_max_concurrent,
                clock=self._clock,
                metrics=metrics,
            )
        self._admission = admission
        self._quota = quota or QuotaTracker(metrics=metrics)
        self._audit = audit
        #: Each provider's own tokenizer (KAN-103/104): the context-window check, the routing
        #: score, the quota reservation and the usage fallback all count with it.
        self._tokenizers: dict[str, Tokenizer] = {
            name: tokenizer_for(p.capabilities.tokenizer, model_id=p.capabilities.model_id)
            for name, p in self._providers.items()
        }
        self._prompt_memo: dict[tuple[int, int], tuple[Any, int]] = {}
        self._quota.track(self._providers)
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

    # -- token estimation (KAN-104) -----------------------------------------------------

    def tokenizer(self, provider: str | LLMProvider | None = None) -> Tokenizer:
        """``provider``'s tokenizer; the first provider's when none is named."""
        name = provider.name if provider is not None and not isinstance(provider, str) else provider
        if name is None:
            name = next(iter(self._providers))
        tokenizer = self._tokenizers.get(name)
        if tokenizer is None:
            caps = self._providers[name].capabilities
            tokenizer = tokenizer_for(caps.tokenizer, model_id=caps.model_id)
        return tokenizer

    def prompt_tokens(
        self, request: CompletionRequest, provider: str | LLMProvider | None = None
    ) -> int:
        """The prompt tokens ``request`` costs ``provider``, images included.

        The one figure the window check, the score and the quota reservation use. Counted with
        the provider's tokenizer exactly as the context engine counts an assembled prompt
        (``Tokenizer.count_messages``), so for the provider the context engine was built for the
        two agree to the token.
        """
        tokenizer = self.tokenizer(provider)
        conversation = request.conversation
        key = (id(conversation), id(tokenizer))
        hit = self._prompt_memo.get(key)
        if hit is not None and hit[0] is conversation:
            count = hit[1]
        else:
            count = tokenizer.count_messages(conversation.messages)
            if len(self._prompt_memo) >= _PROMPT_MEMO_LIMIT:
                self._prompt_memo.clear()
            self._prompt_memo[key] = (conversation, count)
        return count + _image_tokens(request)

    def _settled_usage(
        self,
        request: CompletionRequest,
        name: str,
        usage: TokenUsage | None,
        completion_text: str = "",
        *,
        reported_estimated: bool = False,
    ) -> TokenUsage:
        """The usage to charge the turn: the provider's, or AURA's marked estimate (KAN-106).

        A provider that reported nothing (no usage, or an all-zero one -- no real generation
        costs zero prompt tokens) is charged the router's own figure for the prompt and the
        provider's tokenizer's count of the text it produced, flagged ``estimated``.
        """
        if usage is not None and usage.total_tokens > 0:
            if reported_estimated and not usage.estimated:
                return replace(usage, estimated=True)
            return usage
        tokenizer = self.tokenizer(name)
        return TokenUsage(
            prompt_tokens=self.prompt_tokens(request, name),
            completion_tokens=tokenizer.count(completion_text),
            estimated=True,
        )

    # -- selection ---------------------------------------------------------------------

    def _capability_filter(
        self,
        request: CompletionRequest,
        provider: LLMProvider,
        hint: WorkloadHint | None = None,
        classification: DataClassification | None = None,
    ) -> str | None:
        """Return a rejection reason, or ``None`` if the provider qualifies.

        Every hint-driven check passes a provider that declares nothing, so a configuration
        written before model-level metadata existed filters exactly as it always did.
        """
        caps = provider.capabilities
        hint = hint or effective_workload(request)
        if not caps.is_generative:
            return "not_generative"
        # Before anything else content-related: an unhinted request is INTERACTIVE here
        # (DEFAULT_WORKLOAD), so a provider fenced to the Engineer never receives it (WP6a).
        if not caps.serves_workload(hint.workload):
            return "workload_not_allowed"
        if hint.modalities and not set(hint.modalities) <= caps.effective_modalities:
            return "modality_unsupported"
        if request.images:
            # Derived from the request itself, not the hint: a caller that attaches images and
            # forgets the hint must still never reach a text-only model (ADR-031).
            if Modality.IMAGE not in caps.effective_modalities:
                return "modality_unsupported"
            # A user's photos never go to a third party, whatever ceiling it was configured with.
            if caps.third_party:
                return "third_party_image"
        if hint.needs_reasoning and caps.pools and not caps.supports_reasoning:
            return "no_reasoning_support"
        if not caps.serves_language(hint.language):
            return "language_unsupported"
        if hint.min_context and caps.context_window < hint.min_context:
            return "context_window_too_small"
        if request.require_local and not caps.is_local:
            return "not_local"
        classification = classification or effective_classification(request)
        if not caps.max_data_classification.permits(classification):
            return "data_classification"
        if request.needs_tools and not caps.supports_tools:
            return "no_tool_support"
        if request.needs_json and not caps.supports_json_mode:
            return "no_json_mode"
        if caps.quality_tier < request.min_quality_tier:
            return "quality_tier_too_low"
        prompt_tokens = self.prompt_tokens(request, provider)
        # The output size this provider would actually be asked for (KAN-99): the payload is
        # clamped to its own max_output_tokens (`ProviderCapabilities.output_budget`, the same
        # call the payload makes), so checking the unclamped request excluded a provider that
        # could serve it -- a 7K-token prompt asking for 2048 on an 8K window whose model
        # answers at most 512. An unset size keeps the 512 assumption it always had.
        output = caps.output_budget(
            request.sampling.max_output_tokens or 512,
            request.preferred_max_output_tokens,
            request.timeout_s,
        )
        needed = prompt_tokens + output
        if caps.context_window < needed:
            return "context_window_too_small"
        return None

    def _score(self, request: CompletionRequest, stats: ProviderStats, policy: RoutingPolicy) -> float:
        """Lower is better. Each policy is a different objective over the same signals.

        The P2.3 metadata enters only through terms that are zero (or unchanged) at their
        defaults: ``speed_tier`` scales the latency *prior* (tier 2 = unchanged), and
        ``gpu_cost``/``memory_cost`` add a resource term (0 = unchanged). Everything self-hosted
        costs $0 per token, so without the resource term CHEAPEST could not tell a 7B from a 70B.
        """
        provider = self._providers[stats.name]
        caps = provider.capabilities
        prior = _LATENCY_PRIOR_MS * _SPEED_TIER_PRIOR.get(caps.speed_tier, 1.0)
        latency = stats.latency.estimate_ms(prior)
        prompt_tokens = self.prompt_tokens(request, provider)
        cost = caps.projected_cost_usd(prompt_tokens, request.sampling.max_output_tokens or 512)
        resource = caps.resource_cost
        # Saturation matters as much as raw speed: a fast provider with a full queue is slow.
        # Queued waiters count too -- they are load the provider has not started yet.
        group = self._groups[stats.name]
        capacity = max(1, self._admission.capacity(group))
        load = (self._admission.inflight(group) + self._admission.queued(group)) / capacity
        load = max(load, stats.inflight / max(1, caps.max_concurrency))

        if policy is RoutingPolicy.LOCAL_FIRST:
            return (0.0 if caps.is_local else 1000.0) + load * 10.0 + latency / 1000.0
        if policy is RoutingPolicy.CHEAPEST:
            return cost * 1000.0 + resource * 10.0 + load * 0.5
        if policy is RoutingPolicy.FASTEST:
            return latency * (1.0 + load)
        if policy is RoutingPolicy.QUALITY_FIRST:
            return (3 - caps.quality_tier) * 1000.0 + cost * 100.0 + resource + load * 5.0
        # BALANCED: normalise each signal to roughly [0, 1] and weight by what users notice.
        # Latency dominates perceived quality; cost is the operational constraint; tier is a
        # tiebreak rather than a driver.
        return (
            0.45 * min(latency / 4000.0, 1.0)
            + 0.30 * min(cost / 0.05, 1.0)
            + 0.15 * load
            + 0.10 * ((3 - caps.quality_tier) / 2.0)
            + 0.05 * min(resource / 10.0, 1.0)
        )

    def _attempt_timeout(self, request: CompletionRequest) -> tuple[float, bool]:
        """This attempt's deadline, and whether the turn's generation budget set it (PP-06).

        The budget only ever shortens an attempt. When it is the binding limit and fires, the
        provider was not given its normal deadline, so the timeout says nothing about its health:
        callers record it as ``turn_deadline``, not as a breaker failure.
        """
        assert request.timeout_s is not None  # filled in by `_with_timeout`
        left = time_left(self._clock)
        if left is not None and left < request.timeout_s:
            return max(left, 0.0), True
        return request.timeout_s, False

    def _budget_spent(self) -> bool:
        left = time_left(self._clock)
        return left is not None and left <= 0

    def _next_chunk_wait(self, request: CompletionRequest, started: float) -> float:
        """How long to wait for a stream's next chunk once output has begun (PP-07).

        A gap longer than the request's own deadline is a stall, whatever the provider's HTTP
        read timeout is (an SDK-based provider may have none). ``stream_max_s`` additionally
        caps the whole stream.
        """
        assert request.timeout_s is not None
        wait = request.timeout_s
        if self._stream_max_s > 0:
            wait = min(wait, self._stream_max_s - (self._clock() - started))
        return max(wait, 0.0)

    def _with_timeout(self, request: CompletionRequest) -> CompletionRequest:
        """``request`` with its deadline filled in: a request that names none gets this
        router's configured one, never a constant buried in the dataclass."""
        if request.timeout_s is not None:
            return request
        return replace(request, timeout_s=self._default_timeout_s)

    def plan(self, request: CompletionRequest) -> RoutingDecision:
        """Compute the candidate ordering without executing anything.

        Exposed separately so the admin API can answer "what would you do with this request?"
        and so routing can be tested without a provider call.
        """
        request = self._with_timeout(request)
        policy = request.policy or self._default_policy
        hint = effective_workload(request)
        classification = effective_classification(request)
        wanted = hint.pool
        excluded: dict[str, str] = {}
        members: list[ProviderStats] = []
        fallbacks: list[ProviderStats] = []

        for name, provider in self._providers.items():
            reason = self._capability_filter(request, provider, hint, classification)
            if reason:
                excluded[name] = reason
                continue
            caps = provider.capabilities
            # Pool tiering. With no pool requested, or a generalist provider (no pools
            # declared), there is nothing to tier on -- the legacy behaviour.
            if wanted is not None and caps.pools and wanted not in caps.pools:
                if hint.strict_pool or not any(p in FALLBACK_POOLS for p in caps.pools):
                    excluded[name] = "pool_mismatch"
                    continue
            stats = self._stats[name]
            if stats.breaker.state is CircuitState.OPEN:
                excluded[name] = "circuit_open"
                continue
            refusal = self._quota.refusal(
                name, self._estimated_tokens(request, provider), user_key=hint.user_key
            )
            if refusal is not None:
                excluded[name] = refusal
                continue
            if wanted is not None and wanted in caps.pools:
                members.append(stats)
            elif wanted is not None and hint.strict_pool:
                excluded[name] = "pool_mismatch"
            else:
                fallbacks.append(stats)

        # Stable secondary sort by name keeps ordering deterministic when scores tie, which
        # makes routing tests reproducible.
        def key(s: ProviderStats) -> tuple[float, str]:
            return (self._score(request, s, policy), s.name)

        members.sort(key=key)
        fallbacks.sort(key=key)
        return RoutingDecision(
            candidates=tuple(s.name for s in members + fallbacks),
            policy=policy,
            excluded=excluded,
            pool=hint.pool_label,
            pool_members=tuple(s.name for s in members),
            data_classification=classification.value,
        )

    def _estimated_tokens(self, request: CompletionRequest, provider: LLMProvider) -> int:
        """The most this request can cost ``provider`` in tokens: prompt plus the full output cap.

        Matches what the provider will actually be asked for (``BaseProvider``'s effective max
        output), so a budget check never admits a request the provider could then bill past it.
        The prompt is counted with ``provider``'s tokenizer (:meth:`prompt_tokens`), never
        ``len/4``: a ``USER_TPD`` allowance must refuse an Arabic request its real cost exceeds.
        """
        caps = provider.capabilities
        return self.prompt_tokens(request, provider) + caps.output_budget(
            request.sampling.max_output_tokens,
            request.preferred_max_output_tokens,
            request.timeout_s,
        )

    @staticmethod
    def _is_rate_limited(exc: BaseException) -> bool:
        return isinstance(exc, ProviderError) and exc.details.get("status") == 429

    def _record_rate_limited(self, name: str, exc: ProviderError) -> dict[str, Any]:
        """Bench ``name`` until the reset its ``429`` named. Not a health failure.

        Returns the attempt record, so the caller's error names the cooldown rather than reading
        like a crash.
        """
        retry_after = exc.details.get("retry_after_s")
        applied = self._quota.cooldown(name, retry_after)
        stats = self._stats[name]
        stats.total_requests += 1
        if self._metrics is not None:
            self._metrics.llm_requests_total.labels(
                provider=name,
                model=self._providers[name].capabilities.model_id,
                outcome="rate_limited",
            ).inc()
        return {"provider": name, "error": "rate_limited", "cooldown_s": round(applied, 1)}

    def _reserve(self, name: str, request: CompletionRequest) -> tuple[QuotaReservation | None, str | None]:
        return self._quota.try_reserve(
            name, self._estimated_tokens(request, self._providers[name]),
            user_key=effective_workload(request).user_key,
        )

    @property
    def restricts_data_classification(self) -> bool:
        """Whether any provider refuses some content (ADR-030 §4).

        False for every self-hosted deployment, which lets callers skip classification work that
        could not change a routing decision.
        """
        return any(
            p.capabilities.max_data_classification is not DataClassification.SENSITIVE
            for p in self._providers.values()
        )

    async def _audit_third_party(
        self,
        name: str,
        request: CompletionRequest,
        hint: WorkloadHint,
        usage: TokenUsage | None,
        *,
        streamed: bool,
        result: str = "success",
    ) -> None:
        """Record that a third party received this generation. Never its content.

        Called for every attempt that was dispatched, whatever became of it (F29): a request that
        reached a hosted provider and then failed, timed out or was cancelled still left the
        deployment. ``result`` is the attempt's outcome (``success``, ``error``, ``timeout``,
        ``rate_limited``, ``cancelled``, ``turn_deadline``).

        Best-effort: an audit sink that is down must not fail a generation that already
        happened. The record is the evidence an operator needs to answer "what left, when, for
        whom, at what classification", which is the question the opt-in exists to answer.
        """
        provider = self._providers[name]
        if self._audit is None or not provider.capabilities.third_party:
            return
        from ..ports.runtime import AuditEvent

        try:
            await self._audit.record(
                AuditEvent(
                    action="llm.third_party_generation",
                    actor=hint.user_key or "system",
                    outcome="success" if result == "success" else "failure",
                    resource=name,
                    detail={
                        "result": result,
                        "model": provider.capabilities.model_id,
                        "data_classification": effective_classification(request).value,
                        "prompt_tokens": usage.prompt_tokens if usage else None,
                        "completion_tokens": usage.completion_tokens if usage else None,
                        "streamed": streamed,
                        "workload": hint.workload.value,
                        "may_train_on_prompts": provider.capabilities.may_train_on_prompts,
                    },
                )
            )
        except Exception:  # pragma: no cover - audit must never fail a generation
            pass

    # -- execution ---------------------------------------------------------------------

    async def _admit(self, name: str, hint: WorkloadHint) -> AdmissionTicket:
        """Take a slot in ``name``'s capacity group, waiting in priority order if necessary.

        Raises :class:`OverloadedError` (or :class:`UserConcurrencyLimitError`) straight to the
        caller -- deliberately outside the failover loop's ``except``: saturation is not a
        provider failure, must not count against the circuit breaker, and must not reroute.
        """
        return await self._admission.acquire(
            self._groups[name], priority=hint.priority, user_key=hint.user_key
        )

    def _observe_timing(self, timing: GenerationTiming) -> None:
        # One span per provider attempt (Phase R2). GenerationTiming carries no prompt or
        # response content, so neither does the span.
        record_span(
            "llm.generate",
            duration_ms=timing.total_ms,
            attributes={
                "aura.llm.provider": timing.provider,
                "aura.llm.model": timing.model,
                "aura.llm.pool": timing.pool,
                "aura.llm.outcome": timing.outcome,
                "aura.llm.streamed": timing.streamed,
                "aura.llm.queue_wait_ms": round(timing.queue_wait_ms, 1),
                "aura.llm.ttft_ms": None if timing.ttft_ms is None else round(timing.ttft_ms, 1),
                "aura.llm.prompt_tokens": timing.prompt_tokens,
                "aura.llm.completion_tokens": timing.completion_tokens,
            },
            error=timing.outcome not in ("success", "rate_limited", "cancelled"),
        )
        if self._metrics is None:
            return
        observe = getattr(self._metrics, "observe_generation", None)
        if observe is None:
            return
        try:
            observe(timing)
        except Exception:  # pragma: no cover - metrics must never fail a generation
            pass

    def _timing(
        self,
        name: str,
        hint: WorkloadHint,
        *,
        outcome: str,
        streamed: bool,
        queue_wait_ms: float,
        service_ms: float,
        ttft_ms: float | None = None,
        decode_ms: float | None = None,
        usage: TokenUsage | None = None,
        model: str | None = None,
        purpose: str = "engine",
    ) -> GenerationTiming:
        return GenerationTiming(
            provider=name,
            model=model or self._providers[name].capabilities.model_id,
            pool=hint.pool_label,
            priority=hint.priority,
            outcome=outcome,
            streamed=streamed,
            queue_wait_ms=queue_wait_ms,
            service_ms=service_ms,
            ttft_ms=ttft_ms,
            decode_ms=decode_ms,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            purpose=purpose,
            usage_estimated=bool(usage.estimated) if usage else False,
        )

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

    @staticmethod
    def _release_probe(stats: ProviderStats, probe: bool) -> None:
        """Give back the half-open probe slot of an attempt that ended without a verdict.

        Only the attempt that took the slot releases it. A non-probe attempt that started while
        the circuit was closed and finished after it half-opened must not free the slot a later
        probe now holds, or two probes would run at once.
        """
        if probe:
            stats.breaker.release_probe()

    def _record_failure(self, name: str, error: BaseException, *, health: bool = True) -> None:
        """Count a failed attempt. Only a ``health`` failure is a verdict for the breaker (F15);
        the caller releases a held probe slot otherwise."""
        stats = self._stats[name]
        if health:
            stats.breaker.record_failure()
        stats.total_requests += 1
        stats.total_failures += 1
        if self._metrics is not None:
            self._metrics.llm_requests_total.labels(
                provider=name,
                model=self._providers[name].capabilities.model_id,
                outcome="error",
            ).inc()

    @staticmethod
    def _log_credential_fault(name: str, exc: BaseException) -> None:
        # An operator's problem, not a provider outage: say so loudly, once per attempt, without
        # opening the circuit for everyone (F15).
        details = getattr(exc, "details", None) or {}
        logger.warning(
            "provider refused its credentials; failing over without opening its circuit",
            extra={
                "provider": name,
                "status": details.get("status"),
                "operation": details.get("operation"),
            },
        )

    async def complete(
        self, request: CompletionRequest, *, budget: TurnBudget | None = None
    ) -> CompletionResponse:
        """Generate, failing over across candidates.

        Raises :class:`AllProvidersFailedError` when every candidate fails, with the per-attempt
        detail an operator needs to tell "all nodes down" from "this request is malformed".
        """
        budget, explicit = _resolve_budget(budget)

        request = self._with_timeout(request)
        await self._quota.ensure_hydrated()
        decision = self.plan(request)
        if not decision.candidates:
            raise _exhausted(
                "no provider satisfies this request's requirements",
                decision,
                [{"provider": n, "reason": r} for n, r in decision.excluded.items()],
                quota=self._quota,
            )

        attempts: list[dict[str, Any]] = []
        limit = min(len(decision.candidates), self._max_fallbacks + 1)
        hint = effective_workload(request)
        purpose = _purpose_of(request, explicit)

        for index, name in enumerate(decision.candidates[:limit]):
            if self._budget_spent():
                # The turn's generation budget is gone (PP-06): no attempt starts that could
                # only be cut short.
                attempts.append({"provider": name, "error": "turn_deadline"})
                break
            stats = self._stats[name]
            # Read before `allows_request`, which takes the half-open probe slot as a side
            # effect. Nothing awaits in between, so this is the attempt that holds the slot.
            probe = stats.breaker.state is CircuitState.HALF_OPEN
            if not stats.breaker.allows_request():
                attempts.append({"provider": name, "error": "circuit_open"})
                continue

            provider = self._providers[name]
            try:
                ticket = await self._admit(name, hint)
            except BaseException:
                self._release_probe(stats, probe)
                raise
            reservation, refusal = self._reserve(name, request)
            if reservation is None:
                # Another turn spent the budget between planning and admission. Skipped, not
                # failed: nothing was sent, so there is nothing for the breaker to learn.
                self._admission.release(ticket)
                self._release_probe(stats, probe)
                attempts.append({"provider": name, "error": refusal})
                continue
            started = self._clock()
            stats.inflight += 1
            if self._metrics is not None:
                self._metrics.llm_inflight.labels(provider=name).set(stats.inflight)
            outcome = "error"
            response: CompletionResponse | None = None
            used_tokens: int | None = None
            verdict = False
            attempt_timeout, budget_bound = self._attempt_timeout(request)
            try:
                response = await asyncio.wait_for(provider.complete(request), timeout=attempt_timeout)
                outcome = "success"
                used_tokens = response.usage.total_tokens
                elapsed = (self._clock() - started) * 1000.0
                verdict = True
                self._record_success(name, latency_ms=elapsed, ttft_ms=None, response=response)
                if budget is not None:
                    budget.consume(
                        cost_usd=response.cost_usd,
                        generation=explicit,
                        usage=self._settled_usage(
                            request, name, response.usage, response.text,
                            reported_estimated=bool(
                                (response.raw_metadata or {}).get("usage_estimated")
                            ),
                        ),
                        purpose=purpose,
                    )
                if index > 0:
                    # Attribute the fallback to the provider that actually failed, not to the
                    # one that succeeded -- otherwise the metric points at the healthy node.
                    self._note_fallback(decision.candidates[index - 1], attempts)
                return replace(response, queue_wait_ms=ticket.queue_wait_ms)
            except asyncio.TimeoutError as exc:
                if budget_bound:
                    # Cut short by the turn's budget, not by the provider's own deadline: no
                    # verdict on its health, and nothing left for a fallback either.
                    outcome = "turn_deadline"
                    attempts.append({
                        "provider": name, "error": "turn_deadline",
                        "timeout_s": round(attempt_timeout, 1),
                    })
                    break
                outcome = "timeout"
                verdict = True
                self._record_failure(name, exc)
                attempts.append({"provider": name, "error": "timeout", "timeout_s": request.timeout_s})
            except ProviderError as exc:
                if self._is_rate_limited(exc):
                    outcome = "rate_limited"
                    used_tokens = 0
                    attempts.append(self._record_rate_limited(name, exc))
                else:
                    fault = _fault_kind(exc)
                    verdict = fault == "health"
                    self._record_failure(name, exc, health=verdict)
                    attempts.append({"provider": name, "error": exc.kind, "detail": exc.message})
                    if fault == "credentials":
                        self._log_credential_fault(name, exc)
                        # This provider's configuration, not the request: another can serve it.
                        continue
                    if not exc.retryable:
                        # A malformed request fails identically everywhere; trying more
                        # providers just multiplies latency and cost.
                        break
            except Exception as exc:  # adapter bug or unexpected transport failure
                verdict = True
                self._record_failure(name, exc)
                attempts.append({"provider": name, "error": type(exc).__name__})
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            finally:
                if not verdict:
                    # Cancelled, rate-limited or refused as a bad request: nothing was learned
                    # about the provider's health, but a held probe slot must be given back.
                    self._release_probe(stats, probe)
                self._quota.settle(reservation, used_tokens)
                stats.inflight -= 1
                self._admission.release(ticket)
                if self._metrics is not None:
                    self._metrics.llm_inflight.labels(provider=name).set(stats.inflight)
                self._observe_timing(
                    self._timing(
                        name, hint, outcome=outcome, streamed=False, purpose=purpose,
                        queue_wait_ms=ticket.queue_wait_ms,
                        service_ms=(self._clock() - started) * 1000.0,
                        usage=response.usage if response is not None else None,
                        model=response.model if response is not None else None,
                    )
                )
                # Last, after every slot is given back: this await can itself be cancelled, and
                # a cancel landing here must not leak the admission ticket, quota or inflight.
                await self._audit_third_party(
                    name, request, hint, response.usage if response is not None else None,
                    streamed=False, result=outcome,
                )

        raise _exhausted(
            f"all {len(attempts)} provider attempt(s) failed" + _attempt_summary(attempts),
            decision,
            attempts,
            quota=self._quota,
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
        budget, explicit = _resolve_budget(budget)

        request = self._with_timeout(request)
        await self._quota.ensure_hydrated()
        decision = self.plan(request)
        if not decision.candidates:
            raise _exhausted(
                "no provider satisfies this request's requirements",
                decision,
                [{"provider": n, "reason": r} for n, r in decision.excluded.items()],
                quota=self._quota,
            )

        attempts: list[dict[str, Any]] = []
        limit = min(len(decision.candidates), self._max_fallbacks + 1)
        hint = effective_workload(request)
        purpose = _purpose_of(request, explicit)

        for index, name in enumerate(decision.candidates[:limit]):
            if self._budget_spent():
                attempts.append({"provider": name, "error": "turn_deadline"})
                break
            stats = self._stats[name]
            # As in `complete`: whether this attempt holds the half-open probe slot.
            probe = stats.breaker.state is CircuitState.HALF_OPEN
            if not stats.breaker.allows_request():
                attempts.append({"provider": name, "error": "circuit_open"})
                continue

            provider = self._providers[name]
            # The slot is held for the whole stream: decode is when the model server is busiest.
            # Released in `finally`, which also runs when the consumer stops iterating early
            # (client disconnect -> aclose()) -- that is what keeps a dropped stream from leaking.
            try:
                ticket = await self._admit(name, hint)
            except BaseException:
                self._release_probe(stats, probe)
                raise
            reservation, refusal = self._reserve(name, request)
            if reservation is None:
                self._admission.release(ticket)
                self._release_probe(stats, probe)
                attempts.append({"provider": name, "error": refusal})
                continue
            verdict = False
            used_tokens: int | None = None
            started = self._clock()
            first_chunk_at: float | None = None
            last_chunk_at: float | None = None
            emitted = False
            outcome = "error"
            usage: TokenUsage | None = None
            #: The streamed text, kept only to estimate completion tokens when the provider
            #: reports no usage (KAN-106).
            deltas: list[str] = []
            stats.inflight += 1
            if self._metrics is not None:
                self._metrics.llm_inflight.labels(provider=name).set(stats.inflight)
            attempt_timeout, budget_bound = self._attempt_timeout(request)

            try:
                iterator = provider.stream(request).__aiter__()
                # Pull the first chunk here, inside the failover loop: an error raised now is
                # still recoverable because nothing has reached the caller.
                try:
                    first = await asyncio.wait_for(iterator.__anext__(), timeout=attempt_timeout)
                except StopAsyncIteration:
                    first = None

                if index > 0:
                    self._note_fallback(decision.candidates[index - 1], attempts)

                if first is not None:
                    first_chunk_at = last_chunk_at = self._clock()
                    emitted = True
                    usage = first.usage
                    if first.delta:
                        deltas.append(first.delta)
                    yield first
                    while True:
                        # Bounded per chunk and in total (PP-07). The timeout scopes only the
                        # wait for the provider, never the `yield`, so time the consumer spends
                        # with a chunk is not charged to the provider.
                        try:
                            async with asyncio.timeout(self._next_chunk_wait(request, started)):
                                chunk = await iterator.__anext__()
                        except StopAsyncIteration:
                            break
                        last_chunk_at = self._clock()
                        if chunk.usage is not None:
                            usage = chunk.usage
                        if chunk.delta:
                            deltas.append(chunk.delta)
                        yield chunk

                outcome = "success"
                if usage is not None:
                    used_tokens = usage.total_tokens
                elapsed = (self._clock() - started) * 1000.0
                ttft = (first_chunk_at - started) * 1000.0 if first_chunk_at else None
                verdict = True
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
                    charged = self._settled_usage(request, name, usage, "".join(deltas))
                    cost = charged.cost_usd(
                        caps.cost_per_1k_input_usd, caps.cost_per_1k_output_usd
                    )
                    budget.consume(
                        cost_usd=cost, generation=explicit, usage=charged, purpose=purpose
                    )
                return
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except GeneratorExit:
                # The consumer closed the stream early (client went away). Not a provider
                # failure; the slot is released below.
                outcome = "cancelled"
                raise
            except Exception as exc:
                # asyncio.CancelledError is a BaseException and deliberately not caught -- a
                # cancelled turn must unwind, not fail over to another provider.
                if not emitted and self._is_rate_limited(exc):
                    # A 429 arrives before any output, so moving on is still clean.
                    outcome = "rate_limited"
                    used_tokens = 0
                    attempts.append(self._record_rate_limited(name, exc))
                    continue
                if not emitted and budget_bound and isinstance(exc, asyncio.TimeoutError):
                    # As in `complete`: the turn's budget cut this short, not the provider.
                    outcome = "turn_deadline"
                    attempts.append({
                        "provider": name, "error": "turn_deadline",
                        "timeout_s": round(attempt_timeout, 1),
                    })
                    break
                fault = _fault_kind(exc)
                verdict = fault == "health"
                self._record_failure(name, exc, health=verdict)
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
                if fault == "credentials":
                    self._log_credential_fault(name, exc)
                    continue
                if isinstance(exc, ProviderError) and not exc.retryable:
                    break
            finally:
                if not verdict:
                    # Cancelled, closed by the consumer, rate-limited or refused as a bad
                    # request: see `complete`.
                    self._release_probe(stats, probe)
                self._quota.settle(reservation, used_tokens)
                stats.inflight -= 1
                self._admission.release(ticket)
                if self._metrics is not None:
                    self._metrics.llm_inflight.labels(provider=name).set(stats.inflight)
                self._observe_timing(
                    self._timing(
                        name, hint, outcome=outcome, streamed=True, purpose=purpose,
                        queue_wait_ms=ticket.queue_wait_ms,
                        service_ms=(self._clock() - started) * 1000.0,
                        ttft_ms=(first_chunk_at - started) * 1000.0 if first_chunk_at else None,
                        decode_ms=(
                            (last_chunk_at - first_chunk_at) * 1000.0
                            if first_chunk_at is not None and last_chunk_at is not None
                            else None
                        ),
                        usage=usage,
                    )
                )
                # Last, after every slot is given back: see `complete`.
                await self._audit_third_party(
                    name, request, hint, usage, streamed=True, result=outcome
                )

        raise _exhausted(
            f"all {len(attempts)} provider attempt(s) failed before streaming began"
            + _attempt_summary(attempts),
            decision,
            attempts,
            quota=self._quota,
        )

    # -- introspection and lifecycle ---------------------------------------------------

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def get(self, name: str) -> LLMProvider | None:
        return self._providers.get(name)

    def stats(self) -> list[dict[str, Any]]:
        admission = self._admission.snapshot()
        out: list[dict[str, Any]] = []
        for name, s in self._stats.items():
            snap = s.snapshot()
            caps = self._providers[name].capabilities
            snap["pools"] = [p.value for p in caps.pools]
            snap["model_revision"] = caps.model_revision or None
            snap["capacity_group"] = self._groups[name]
            snap["admission"] = admission.get(self._groups[name])
            snap["quota"] = self._quota.snapshot(name)
            snap["max_data_classification"] = caps.max_data_classification.value
            snap["third_party"] = caps.third_party
            snap["may_train_on_prompts"] = caps.may_train_on_prompts
            out.append(snap)
        return out

    @property
    def admission(self) -> AdmissionController:
        return self._admission

    @property
    def quota(self) -> QuotaTracker:
        return self._quota

    async def health(self) -> dict[str, Any]:
        """Probe every provider concurrently, updating the health gauge."""
        async def probe(name: str, provider: LLMProvider) -> tuple[str, dict[str, Any]]:
            # `is_local` is what `require_local` filters on, so a caller that needs a local
            # provider can tell from readiness whether one is up.
            is_local = provider.capabilities.is_local
            if provider.capabilities.passive_health:
                return name, self._passive_health(name, is_local=is_local)
            try:
                status = await asyncio.wait_for(provider.health(), timeout=10.0)
                return name, {
                    "healthy": status.healthy,
                    "detail": status.detail,
                    "latency_ms": round(status.latency_ms, 1),
                    "is_local": is_local,
                }
            except Exception as exc:
                return name, {"healthy": False, "detail": type(exc).__name__, "latency_ms": None,
                              "is_local": is_local}

        results = await asyncio.gather(*(probe(n, p) for n, p in self._providers.items()))
        report = dict(results)
        if self._metrics is not None:
            for name, status in report.items():
                self._metrics.dependency_up.labels(dependency=f"provider:{name}").set(
                    1 if status["healthy"] else 0
                )
        return report

    def _passive_health(self, name: str, *, is_local: bool) -> dict[str, Any]:
        """What readiness may say about a provider it must not call (``health_probe=passive``).

        Only real generations move the breaker, so this is the provider's health as traffic
        last found it. A provider no turn has used yet reads healthy: "not known to be broken"
        is the honest reading of a sleeping server, and calling it to find out would wake it.
        The ``probe`` field tells a reader that this was not a live check.
        """
        stats = self._stats.get(name)
        if stats is None:
            return {"healthy": True, "detail": "passive: no traffic yet", "latency_ms": None,
                    "is_local": is_local, "probe": "passive"}
        latency = stats.latency.value_ms
        if stats.total_requests == 0:
            detail = "passive: no traffic yet"
        else:
            detail = (
                f"passive: circuit {stats.breaker.state}, "
                f"{stats.breaker.consecutive_failures} consecutive failures"
            )
        return {
            "healthy": stats.healthy,
            "detail": detail,
            "latency_ms": round(latency, 1) if latency else None,
            "is_local": is_local,
            "probe": "passive",
        }

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
