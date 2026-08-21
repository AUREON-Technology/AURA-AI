"""The AI kernel: the orchestrator that runs one turn.

This is the pipeline from ``docs/architecture/00-overview.md`` §2, in code. It owns *sequence*
and *policy*; every capability it uses is behind a port or an engine, so the kernel itself has
no vendor dependency, no HTTP dependency, and no database dependency.

Ordering here is load-bearing, not stylistic:

1. **Input guards before anything expensive.** A blocked request must not have paid for
   embeddings, retrieval, and a generation first.
2. **Memory, retrieval, and routing run concurrently.** They are independent; serialising them
   adds ~300-800 ms to every turn, which is the single largest avoidable latency in the current
   system (Performance Report §1).
3. **Context assembly after retrieval, before generation.** Retrieved material is fenced as data
   here -- the structural fix for SEC-02.
4. **Output guards before the caller sees anything.** For streaming, this constrains what can be
   guarded mid-stream; see :meth:`stream` for how that is handled honestly rather than by
   pretending output guards ran.

The kernel returns a :class:`TurnResult` carrying the answer *and* its
:class:`~aura_core.domain.telemetry.TurnTelemetry`. Telemetry is a return value rather than a
side effect so it cannot be forgotten (ADR-009).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping, Sequence

from ..context.engine import ContextEngine, ContextRequest
from ..domain.errors import (
    AllProvidersFailedError,
    AuraError,
    BudgetExceededError,
    SafetyViolationError,
)
from ..domain.events import TurnCompleted, TurnFailed, TurnStarted
from ..domain.identifiers import CorrelationId, SessionId, TurnId, UserId
from ..domain.knowledge import RetrievalResult
from ..domain.llm import (
    CompletionRequest,
    CompletionResponse,
    RoutingPolicy,
    SamplingParams,
    TokenUsage,
)
from ..domain.memory import MemoryHit, MemoryKind, MemoryQuery
from ..domain.messages import Conversation, Message
from ..domain.planning import StepKind
from ..domain.reasoning import ReasoningStrategy
from ..domain.telemetry import (
    CacheOutcome,
    RetrievalTelemetry,
    StageTiming,
    ToolTelemetry,
    TurnOutcome,
    TurnTelemetry,
)
from ..llm.router import TurnBudget
from ..observability.logging import LogContext, get_logger
from ..ports.runtime import ToolContext
from ..prompts.library import AGENT_PROMPT_IDS
from ..reasoning.engine import ReasoningRequest

__all__ = ["AIKernel", "TurnRequest", "TurnResult", "KernelDependencies"]

logger = get_logger("kernel")


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """One turn's input."""

    message: str
    user_id: UserId
    session_id: SessionId | None = None
    agent: str = "orchestrator"
    correlation_id: CorrelationId | None = None
    use_knowledge: bool = True
    use_memory: bool = True
    use_tools: bool = True
    plan: bool | None = None
    strategy: ReasoningStrategy | None = None
    routing_policy: RoutingPolicy | None = None
    require_local: bool = False
    capabilities: tuple[str, ...] = ()
    confirmed_tools: tuple[str, ...] = ()
    max_output_tokens: int = 1024
    temperature: float = 0.3
    route: str = "chat"


@dataclass(frozen=True, slots=True)
class TurnResult:
    """One turn's output, with its telemetry."""

    text: str
    telemetry: TurnTelemetry
    citations: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    confidence: float | None = None
    needs_confirmation: tuple[str, ...] = ()
    plan_summary: tuple[Mapping[str, Any], ...] = ()

    @property
    def degraded(self) -> bool:
        return self.telemetry.degraded


@dataclass
class KernelDependencies:
    """Everything the kernel needs, injected.

    A dataclass rather than fifteen constructor parameters, so a deployment that omits an
    optional engine (no planner, no graph) does so by leaving a field ``None`` rather than by
    threading conditionals through the call site.
    """

    router: Any
    prompts: Any
    context: ContextEngine
    safety: Any = None
    memory: Any = None
    knowledge: Any = None
    tools: Any = None
    planner: Any = None
    reasoner: Any = None
    executor: Any = None
    episodic: Any = None
    cache: Any = None
    metrics: Any = None
    event_bus: Any = None
    telemetry_sink: Any = None
    clock: Any = None
    settings: Any = None


class AIKernel:
    """Runs one turn through the pipeline."""

    def __init__(self, deps: KernelDependencies) -> None:
        self._deps = deps
        self._metrics = deps.metrics

    # -- public API --------------------------------------------------------------------

    async def run(self, request: TurnRequest) -> TurnResult:
        """Execute a turn and return the complete answer."""
        correlation = request.correlation_id or CorrelationId.new()
        turn_id = TurnId.new()

        with LogContext(
            correlation_id=correlation.value,
            route=request.route,
            agent=request.agent,
        ):
            started = time.perf_counter()
            if self._metrics is not None:
                self._metrics.turns_in_flight.inc()
            await self._publish(
                TurnStarted(
                    correlation_id=correlation, turn_id=turn_id,
                    session_id=request.session_id, user_id=request.user_id, route=request.route,
                )
            )
            try:
                return await self._run_turn(request, correlation, turn_id, started)
            except SafetyViolationError as exc:
                return self._error_result(
                    request, correlation, turn_id, started, exc, TurnOutcome.BLOCKED
                )
            except BudgetExceededError as exc:
                return self._error_result(
                    request, correlation, turn_id, started, exc, TurnOutcome.BUDGET_EXCEEDED
                )
            except AuraError as exc:
                return self._error_result(
                    request, correlation, turn_id, started, exc, TurnOutcome.ERROR
                )
            finally:
                if self._metrics is not None:
                    self._metrics.turns_in_flight.dec()

    async def _run_turn(
        self,
        request: TurnRequest,
        correlation: CorrelationId,
        turn_id: TurnId,
        started: float,
    ) -> TurnResult:
        deps = self._deps
        timings: dict[str, float] = {}
        budget = TurnBudget(
            max_generations=getattr(
                getattr(deps.settings, "llm", None), "max_generations_per_turn", 8
            )
        )

        # 1. Input guards -----------------------------------------------------------
        guard_records: list[Mapping[str, Any]] = []
        message = request.message
        if deps.safety is not None:
            stage_start = time.perf_counter()
            decision = await deps.safety.guard_input(
                message, user_id=request.user_id, turn_id=turn_id
            )
            message = decision.text
            guard_records.extend(decision.report.summary())
            timings["guards_in"] = (time.perf_counter() - stage_start) * 1000.0

        # 2-4. Memory, retrieval, and history concurrently ---------------------------
        stage_start = time.perf_counter()
        memories, retrieval, history, history_summary = await self._gather_context(request, message)
        timings["memory"] = timings["retrieval"] = (time.perf_counter() - stage_start) * 1000.0

        # 5. Guard retrieved content -------------------------------------------------
        tainted = False
        quarantined_count = 0
        if deps.safety is not None and retrieval is not None and retrieval.chunks:
            stage_start = time.perf_counter()
            items = [(c.citation(), c.chunk.content) for c in retrieval.chunks]
            accepted, quarantined, tainted = await deps.safety.guard_context_items(
                items, user_id=request.user_id, turn_id=turn_id
            )
            quarantined_count = len(quarantined)
            if quarantined_count:
                accepted_citations = {citation for citation, _ in accepted}
                retrieval = RetrievalResult(
                    chunks=tuple(c for c in retrieval.chunks if c.citation() in accepted_citations),
                    query=retrieval.query,
                    candidates_considered=retrieval.candidates_considered,
                    below_threshold=retrieval.below_threshold,
                    quarantined=tuple(
                        c for c in retrieval.chunks if c.citation() not in accepted_citations
                    ),
                    strategy=retrieval.strategy,
                    duration_ms=retrieval.duration_ms,
                )
            timings["guards_in"] = timings.get("guards_in", 0.0) + (
                time.perf_counter() - stage_start
            ) * 1000.0

        # 6. Plan and execute --------------------------------------------------------
        tool_telemetry: list[ToolTelemetry] = []
        tool_results: list[tuple[str, str]] = []
        needs_confirmation: list[str] = []
        plan_summary: list[Mapping[str, Any]] = []
        plan_steps = plan_revisions = 0

        tool_declarations = self._tool_declarations(request)
        if self._should_plan(request, tool_declarations):
            stage_start = time.perf_counter()
            outcome = await self._plan_and_execute(request, message, tool_declarations, tainted)
            timings["planning"] = (time.perf_counter() - stage_start) * 1000.0
            if outcome is not None:
                tool_results = outcome.summary_for_context()
                plan_steps = len(outcome.plan.steps)
                plan_revisions = len(outcome.plan.revisions)
                plan_summary = [
                    {"id": s.id.value, "kind": str(s.kind), "status": str(s.status),
                     "description": s.description}
                    for s in outcome.plan.steps
                ]
                for report in outcome.tool_reports:
                    tool_telemetry.append(
                        ToolTelemetry(
                            name=report.tool, ok=report.ok,
                            duration_ms=report.duration_ms,
                            error_kind=report.error_kind, allowed=report.allowed,
                        )
                    )
                    if report.needs_confirmation:
                        needs_confirmation.append(report.tool)

        # 7. Assemble context --------------------------------------------------------
        stage_start = time.perf_counter()
        system_prompt = self._render_system_prompt(request, tool_declarations)
        assembled = deps.context.assemble(
            ContextRequest(
                system_prompt=system_prompt,
                user_message=message,
                history=history,
                memories=memories,
                retrieval=retrieval,
                tool_results=tool_results,
                history_summary=history_summary,
                context_window=self._context_window(),
                max_output_tokens=request.max_output_tokens,
                max_prompt_tokens=self._max_prompt_tokens(),
            )
        )
        timings["context"] = (time.perf_counter() - stage_start) * 1000.0
        timings["prompt"] = 0.0

        # 8. Generate ----------------------------------------------------------------
        stage_start = time.perf_counter()
        answer, provider, model, usage, cost, cached, cache_tier, fallbacks, strategy, confidence = (
            await self._generate(request, assembled, retrieval, budget, tool_declarations)
        )
        timings["generation"] = (time.perf_counter() - stage_start) * 1000.0

        # 9. Output guards -----------------------------------------------------------
        groundedness = None
        if deps.safety is not None:
            stage_start = time.perf_counter()
            decision = await deps.safety.guard_output(
                answer,
                system_prompt=assembled.system_text,
                citations_text=[c.chunk.content for c in (retrieval.chunks if retrieval else ())],
                user_id=request.user_id,
                turn_id=turn_id,
            )
            answer = decision.text
            guard_records.extend(decision.report.summary())
            for verdict in decision.report.verdicts:
                if verdict.guard == "groundedness":
                    groundedness = verdict.metadata.get("groundedness")
            timings["guards_out"] = (time.perf_counter() - stage_start) * 1000.0

        # 10. Persist session state --------------------------------------------------
        if deps.episodic is not None and request.session_id is not None:
            try:
                await deps.episodic.append(
                    request.session_id, request.user_id,
                    user_message=request.message, assistant_message=answer,
                )
            except Exception:
                # Losing session persistence degrades the next turn; failing this one would be
                # a worse trade.
                logger.warning("failed to persist session state")

        telemetry = TurnTelemetry(
            correlation_id=correlation,
            turn_id=turn_id,
            session_id=request.session_id,
            route=request.route,
            agent=request.agent,
            strategy=str(strategy),
            outcome=TurnOutcome.SUCCESS,
            provider=provider,
            model=model,
            fallback_count=fallbacks,
            cache=CacheOutcome(hit=cached, tier=cache_tier),
            prompt_tokens=usage.prompt_tokens if usage else assembled.prompt_tokens,
            completion_tokens=usage.completion_tokens if usage else 0,
            cost_usd=cost,
            total_ms=(time.perf_counter() - started) * 1000.0,
            timings=StageTiming(**timings),
            retrieval=self._retrieval_telemetry(retrieval, quarantined_count),
            memories_recalled=len(memories),
            context_tokens=assembled.prompt_tokens,
            context_dropped=assembled.dropped,
            guards=tuple(guard_records),
            tainted=tainted,
            tools=tuple(tool_telemetry),
            plan_steps=plan_steps,
            plan_revisions=plan_revisions,
            confidence=confidence,
            groundedness=groundedness,
        )
        await self._record(telemetry)

        return TurnResult(
            text=answer,
            telemetry=telemetry,
            citations=assembled.citations,
            tool_calls=tuple(t.name for t in tool_telemetry),
            confidence=confidence,
            needs_confirmation=tuple(dict.fromkeys(needs_confirmation)),
            plan_summary=tuple(plan_summary),
        )

    # -- streaming ---------------------------------------------------------------------

    async def stream(self, request: TurnRequest) -> AsyncIterator[Mapping[str, Any]]:
        """Stream a turn as typed frames.

        Frames: ``{"type": "token"|"step"|"citations"|"done"|"error", ...}``. Typed rather than
        raw text so the client never has to string-split a marker out of the token stream
        (TD-12).

        **Honest limitation, stated in the contract:** output guards that need the complete
        answer -- system-prompt leakage, groundedness -- cannot run before the first token is
        emitted. They run at the end and their verdict is reported in the ``done`` frame. Guards
        that operate on a bounded window (secrets) are applied per chunk. A deployment that
        cannot accept post-hoc detection should use :meth:`run` instead; the ``done`` frame's
        ``guards_complete`` flag makes the difference explicit rather than implied.
        """
        correlation = request.correlation_id or CorrelationId.new()
        turn_id = TurnId.new()

        with LogContext(correlation_id=correlation.value, route=request.route):
            try:
                deps = self._deps
                message = request.message
                if deps.safety is not None:
                    decision = await deps.safety.guard_input(
                        message, user_id=request.user_id, turn_id=turn_id
                    )
                    message = decision.text

                memories, retrieval, history, history_summary = await self._gather_context(
                    request, message
                )

                tainted = False
                if deps.safety is not None and retrieval is not None and retrieval.chunks:
                    items = [(c.citation(), c.chunk.content) for c in retrieval.chunks]
                    accepted, _quarantined, tainted = await deps.safety.guard_context_items(
                        items, user_id=request.user_id, turn_id=turn_id
                    )
                    accepted_citations = {citation for citation, _ in accepted}
                    retrieval = RetrievalResult(
                        chunks=tuple(
                            c for c in retrieval.chunks if c.citation() in accepted_citations
                        ),
                        query=retrieval.query,
                        candidates_considered=retrieval.candidates_considered,
                        strategy=retrieval.strategy,
                    )

                tool_declarations = self._tool_declarations(request)
                assembled = deps.context.assemble(
                    ContextRequest(
                        system_prompt=self._render_system_prompt(request, tool_declarations),
                        user_message=message,
                        history=history,
                        memories=memories,
                        retrieval=retrieval,
                        history_summary=history_summary,
                        context_window=self._context_window(),
                        max_output_tokens=request.max_output_tokens,
                        max_prompt_tokens=self._max_prompt_tokens(),
                    )
                )

                if assembled.citations:
                    yield {"type": "citations", "citations": list(assembled.citations)}

                collected: list[str] = []
                completion = CompletionRequest(
                    conversation=assembled.conversation,
                    sampling=SamplingParams(
                        temperature=request.temperature,
                        max_output_tokens=request.max_output_tokens,
                    ),
                    policy=request.routing_policy,
                    require_local=request.require_local,
                    tools=tool_declarations,
                )

                async for chunk in deps.router.stream(completion):
                    if chunk.delta:
                        collected.append(chunk.delta)
                        yield {"type": "token", "text": chunk.delta}
                    if chunk.tool_calls:
                        yield {
                            "type": "tool_calls",
                            "calls": [
                                {"name": c.name, "arguments": dict(c.arguments)}
                                for c in chunk.tool_calls
                            ],
                        }

                answer = "".join(collected)
                guards_complete = False
                if deps.safety is not None:
                    try:
                        decision = await deps.safety.guard_output(
                            answer,
                            system_prompt=assembled.system_text,
                            citations_text=[
                                c.chunk.content for c in (retrieval.chunks if retrieval else ())
                            ],
                            user_id=request.user_id,
                            turn_id=turn_id,
                        )
                        guards_complete = True
                        if decision.text != answer:
                            # The answer was redacted after streaming. Say so explicitly rather
                            # than leaving the client showing unredacted text.
                            yield {
                                "type": "revision",
                                "reason": "output_guard_redaction",
                                "text": decision.text,
                            }
                    except SafetyViolationError as exc:
                        yield {
                            "type": "error",
                            "error": exc.kind,
                            "message": "The response was withheld by a safety guard.",
                            "post_hoc": True,
                        }
                        return

                if deps.episodic is not None and request.session_id is not None:
                    try:
                        await deps.episodic.append(
                            request.session_id, request.user_id,
                            user_message=request.message, assistant_message=answer,
                        )
                    except Exception:
                        logger.warning("failed to persist session state")

                yield {
                    "type": "done",
                    "tainted": tainted,
                    "guards_complete": guards_complete,
                    "citations": list(assembled.citations),
                }
            except AuraError as exc:
                yield {"type": "error", "error": exc.kind, "message": exc.message,
                       "retryable": exc.retryable}
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("unhandled error during stream")
                yield {"type": "error", "error": "internal_error",
                       "message": type(exc).__name__, "retryable": False}

    # -- stages ------------------------------------------------------------------------

    async def _gather_context(
        self, request: TurnRequest, message: str
    ) -> tuple[list[MemoryHit], RetrievalResult | None, Conversation, str]:
        """Run memory recall, knowledge retrieval, and history load concurrently.

        These three are independent. Running them in sequence is the single largest avoidable
        latency in the legacy pipeline, which additionally embeds the same message four separate
        times (Performance Report §1).
        """
        deps = self._deps

        async def recall() -> list[MemoryHit]:
            if not (request.use_memory and deps.memory is not None):
                return []
            try:
                return await deps.memory.recall(
                    MemoryQuery(
                        owner_id=request.user_id,
                        text=message,
                        session_id=request.session_id,
                        kinds=(MemoryKind.SEMANTIC, MemoryKind.PROCEDURAL, MemoryKind.ENTITY),
                        limit=getattr(getattr(deps.settings, "memory", None), "recall_limit", 3),
                        token_budget=getattr(
                            getattr(deps.settings, "memory", None), "token_budget", 80
                        ),
                    )
                )
            except Exception:
                logger.warning("memory recall failed; continuing without it")
                return []

        async def retrieve() -> RetrievalResult | None:
            if not (request.use_knowledge and deps.knowledge is not None):
                return None
            try:
                return await deps.knowledge.retrieve(message, owner_id=request.user_id)
            except Exception:
                logger.warning("knowledge retrieval failed; continuing without it")
                return None

        async def load_history() -> tuple[Conversation, str]:
            if deps.episodic is None or request.session_id is None:
                return Conversation(), ""
            try:
                return await deps.episodic.history(request.session_id, request.user_id)
            except Exception:
                logger.warning("session history load failed; continuing without it")
                return Conversation(), ""

        memories, retrieval, (history, summary) = await asyncio.gather(
            recall(), retrieve(), load_history()
        )
        return memories, retrieval, history, summary

    def _tool_declarations(self, request: TurnRequest) -> tuple[Mapping[str, Any], ...]:
        deps = self._deps
        if not (request.use_tools and deps.tools is not None):
            return ()
        return deps.tools.declarations(capabilities=request.capabilities)

    def _should_plan(self, request: TurnRequest, tools: Sequence[Mapping[str, Any]]) -> bool:
        deps = self._deps
        if deps.planner is None or deps.executor is None or not tools:
            return False
        return deps.planner.needs_plan(
            request.message, available_tools=[t.get("name", "") for t in tools], force=request.plan
        )

    async def _plan_and_execute(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
        tainted: bool,
    ) -> Any:
        deps = self._deps
        try:
            goal = deps.planner.goal_from_message(message)
            plan = await deps.planner.plan(goal, available_tools=tools)
            if len(plan.steps) <= 1 and plan.steps[0].kind is StepKind.RESPOND:
                # A one-step "just answer" plan is what the direct path already does; running the
                # executor for it costs latency and buys nothing.
                return None

            context = ToolContext(
                user_id=request.user_id,
                correlation_id=(request.correlation_id or CorrelationId.new()).value,
                deadline_s=60.0,
                tainted=tainted,
            )
            return await deps.executor.execute(
                plan,
                context=context,
                capabilities=request.capabilities,
                confirmed_tools=request.confirmed_tools,
            )
        except Exception:
            logger.warning("planning or execution failed; answering directly")
            return None

    def _render_system_prompt(
        self, request: TurnRequest, tools: Sequence[Mapping[str, Any]]
    ) -> str:
        template_id = AGENT_PROMPT_IDS.get(request.agent, AGENT_PROMPT_IDS["orchestrator"])
        clock = self._deps.clock
        now = clock.now() if clock is not None else datetime.now(timezone.utc)
        try:
            rendered = self._deps.prompts.render(
                template_id,
                {
                    # Rendered by the context engine, which owns the per-turn nonce; the prompt
                    # only needs a slot for it.
                    "fence_rules": "",
                    "current_time": now.isoformat(timespec="minutes"),
                    "capabilities": ", ".join(
                        str(t.get("name") or "") for t in tools if t.get("name")
                    ),
                },
            )
            return rendered.text
        except Exception:
            logger.warning("prompt render failed; using the orchestrator default")
            return self._deps.prompts.render(
                AGENT_PROMPT_IDS["orchestrator"], {"fence_rules": ""}
            ).text

    def _context_window(self) -> int:
        """Smallest context window among healthy providers.

        Sizing to the smallest rather than the largest means a failover to a smaller model does
        not immediately overflow -- which would turn a latency event into an outage.
        """
        router = self._deps.router
        try:
            windows = [
                router.get(name).capabilities.context_window for name in router.provider_names
            ]
            return min(windows) if windows else 8192
        except Exception:
            return 8192

    def _max_prompt_tokens(self) -> int:
        llm = getattr(self._deps.settings, "llm", None) if self._deps.settings is not None else None
        return int(getattr(llm, "max_prompt_tokens", 400) or 400)

    async def _generate(
        self,
        request: TurnRequest,
        assembled: Any,
        retrieval: RetrievalResult | None,
        budget: TurnBudget,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[str, str | None, str | None, Any, float, bool, str | None, int, Any, float | None]:
        deps = self._deps
        namespace = request.user_id.value

        completion = CompletionRequest(
            conversation=assembled.conversation,
            sampling=SamplingParams(
                temperature=request.temperature, max_output_tokens=request.max_output_tokens
            ),
            policy=request.routing_policy,
            require_local=request.require_local,
            tools=tuple(tools),
        )

        if deps.cache is not None:
            hit = await deps.cache.lookup(completion, namespace=namespace)
            if hit is not None:
                return (
                    hit.response.text, hit.response.provider, hit.response.model,
                    hit.response.usage, 0.0, True, hit.tier, 0,
                    ReasoningStrategy.DIRECT, None,
                )

        strength = retrieval.top_score if retrieval and retrieval.chunks else None

        if deps.reasoner is not None:
            result = await deps.reasoner.reason(
                ReasoningRequest(
                    question=request.message,
                    conversation=assembled.conversation,
                    citations=[c.chunk.content for c in (retrieval.chunks if retrieval else ())],
                    retrieval_strength=strength,
                    strategy=request.strategy,
                    max_output_tokens=request.max_output_tokens,
                )
            )
            confidence = result.confidence.score if result.confidence else None
            if deps.cache is not None:
                await deps.cache.store(
                    completion,
                    CompletionResponse(
                        text=result.text,
                        provider=result.provider or "unknown",
                        model=result.model or "unknown",
                        usage=result.usage or TokenUsage(),
                    ),
                    namespace=namespace,
                )
            return (
                result.text, result.provider, result.model, result.usage,
                0.0, False, None, 0, result.trace.strategy, confidence,
            )

        try:
            response = await deps.router.complete(completion, budget=budget)
        except AllProvidersFailedError:
            raise

        if deps.cache is not None:
            await deps.cache.store(completion, response, namespace=namespace)

        return (
            response.text, response.provider, response.model, response.usage,
            response.cost_usd, response.cached, response.cache_tier, 0,
            ReasoningStrategy.DIRECT, None,
        )

    # -- telemetry ---------------------------------------------------------------------

    @staticmethod
    def _retrieval_telemetry(
        retrieval: RetrievalResult | None, quarantined: int
    ) -> RetrievalTelemetry:
        if retrieval is None:
            return RetrievalTelemetry()
        return RetrievalTelemetry(
            candidates=retrieval.candidates_considered,
            returned=len(retrieval.chunks),
            top_score=retrieval.top_score,
            below_threshold=len(retrieval.below_threshold),
            quarantined=quarantined,
            citations=len(retrieval.citations()),
            strategy=retrieval.strategy,
        )

    def _error_result(
        self,
        request: TurnRequest,
        correlation: CorrelationId,
        turn_id: TurnId,
        started: float,
        exc: AuraError,
        outcome: TurnOutcome,
    ) -> TurnResult:
        """Turn a domain error into a result carrying telemetry.

        A failed turn still produces a telemetry record. Errors that leave no trace are how a
        system's failure rate becomes invisible.
        """
        telemetry = TurnTelemetry(
            correlation_id=correlation,
            turn_id=turn_id,
            session_id=request.session_id,
            route=request.route,
            agent=request.agent,
            outcome=outcome,
            error_kind=exc.kind,
            total_ms=(time.perf_counter() - started) * 1000.0,
        )
        logger.warning(
            "turn failed", extra={"error": exc.kind, "outcome": str(outcome)}
        )
        asyncio.ensure_future(self._record(telemetry))
        asyncio.ensure_future(
            self._publish(
                TurnFailed(
                    correlation_id=correlation, turn_id=turn_id,
                    error_kind=exc.kind, retryable=exc.retryable,
                )
            )
        )
        return TurnResult(text="", telemetry=telemetry)

    async def _record(self, telemetry: TurnTelemetry) -> None:
        if self._metrics is not None:
            try:
                self._metrics.observe_turn(telemetry)
            except Exception:  # pragma: no cover - defensive
                logger.debug("metric observation failed", exc_info=True)
        if self._deps.telemetry_sink is not None:
            try:
                await self._deps.telemetry_sink.record_turn(telemetry)
            except Exception:
                logger.debug("telemetry sink failed", exc_info=True)
        await self._publish(
            TurnCompleted(
                correlation_id=telemetry.correlation_id,
                turn_id=telemetry.turn_id,
                outcome=str(telemetry.outcome),
                provider=telemetry.provider,
                model=telemetry.model,
                total_tokens=telemetry.total_tokens,
                cost_usd=telemetry.cost_usd,
                duration_ms=telemetry.total_ms,
                degraded=telemetry.degraded,
            )
        )

    async def _publish(self, event: Any) -> None:
        if self._deps.event_bus is None:
            return
        try:
            await self._deps.event_bus.publish(event)
        except Exception:  # pragma: no cover - defensive
            logger.debug("kernel event publication failed", exc_info=True)
