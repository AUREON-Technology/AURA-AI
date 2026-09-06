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
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping, Sequence

from ..context.engine import ContextEngine, ContextRequest, AssembledContext
from ..domain.errors import (
    AllProvidersFailedError,
    AuraError,
    BudgetExceededError,
    SafetyViolationError,
)
from ..domain.events import TurnCompleted, TurnFailed, TurnStarted
from ..domain.context import user_facing_citations
from ..domain.engineering import EngineerRun, EngineerRunStatus
from ..domain.identifiers import CorrelationId, EngineerRunId, SessionId, TurnId, UserId
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
from ..domain.tools import ToolSensitivity
from ..domain.telemetry import (
    CacheOutcome,
    RetrievalTelemetry,
    StageTiming,
    ToolTelemetry,
    TurnOutcome,
    TurnTelemetry,
)
from ..execution.engine import ExecutionOutcome, StepEvent
from ..llm.router import TurnBudget
from ..observability.logging import LogContext, get_logger
from ..ports.runtime import ToolContext
from ..prompts.library import AGENT_PROMPT_IDS
from ..agents.registry import AgentRegistry, AgentSpec
from ..reasoning.engine import ReasoningRequest
from ..tools.personal_os import actions_from_tool_results

__all__ = ["AIKernel", "TurnRequest", "TurnResult", "KernelDependencies"]

logger = get_logger("kernel")

# Matches `tools/coding.py::_ENGINEER_CAPABILITY` -- not imported directly since `kernel/` sits
# above `tools/` in the layering and must not import a concrete tool module.
_ENGINEER_AGENT_CAPABILITY = "engineer_agent"
_URL_RE = re.compile(r"https?://\S+")
# `github_create_branch`'s result text ends "... (<sha[:7]>)" -- best-effort extraction, not a
# guarantee every tool's prose embeds a SHA in this exact shape.
_SHA_RE = re.compile(r"\(([0-9a-f]{7,40})\)\s*$")


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
    #: Precise per-call confirmation: a `ToolCallId` value (e.g. `"step_<plan-step-id>"`) rather
    #: than a tool name, so two pending calls to the same tool in one turn can be confirmed
    #: independently. Checked before `confirmed_tools`; either alone is sufficient.
    confirmed_call_ids: tuple[str, ...] = ()
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
    #: One entry per confirm-required tool call this turn, `{"call_id", "tool", "arguments"}`.
    #: Not deduplicated by tool name (unlike `needs_confirmation`) -- two distinct calls to the
    #: same tool must surface as two separate pending calls, not collapse into one.
    pending_tool_calls: tuple[Mapping[str, Any], ...] = ()
    plan_summary: tuple[Mapping[str, Any], ...] = ()
    #: The persona id `AgentRegistry.resolve()` actually used -- may differ from
    #: `TurnRequest.agent` when the caller lacked that persona's `required_capabilities` and the
    #: registry silently fell back to `orchestrator` (see `AgentRegistry.resolve()`'s docstring).
    #: Lets a caller detect that fallback instead of it being invisible.
    resolved_agent: str | None = None

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
    agents: AgentRegistry | None = None
    safety: Any = None
    memory: Any = None
    knowledge: Any = None
    cag: Any = None
    goals: Any = None
    tools: Any = None
    planner: Any = None
    reasoner: Any = None
    executor: Any = None
    #: Optional bounded plan-execute-observe-replan loop (`execution/agentic_loop.py`), used
    #: instead of the single `executor.execute()` call when the turn's resolved agent declares
    #: `max_plan_iterations > 1`. `None` preserves the pre-agentic-loop behaviour exactly.
    agentic_executor: Any = None
    #: `EngineerRunStore` (`ports/coding.py`) -- `None` disables recording engineer-agent tool
    #: outcomes entirely (matching every other optional dependency's "absent means off" contract).
    engineer_runs: Any = None
    episodic: Any = None
    cache: Any = None
    metrics: Any = None
    event_bus: Any = None
    telemetry_sink: Any = None
    clock: Any = None
    settings: Any = None
    models: Any = None


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
        resolved_spec = self._resolve_agent(request)
        resolved_agent = resolved_spec.id if resolved_spec is not None else request.agent
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
        memories, retrieval, history, history_summary, goals, cag_prefix = await self._gather_context(
            request, message
        )
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
        pending_tool_calls: list[Mapping[str, Any]] = []
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
                        pending_tool_calls.append({
                            "call_id": report.call_id,
                            "tool": report.tool,
                            "arguments": dict(report.arguments),
                        })
                await self._record_engineer_runs(
                    request, turn_id, correlation, resolved_agent, outcome
                )

        # 7. Assemble context --------------------------------------------------------
        stage_start = time.perf_counter()
        system_prompt = self._render_system_prompt(request, tool_declarations)
        assembled = deps.context.assemble(
            ContextRequest(
                system_prompt=system_prompt,
                user_message=message,
                history=history,
                memories=memories,
                goals=goals,
                retrieval=retrieval,
                cag_prefix=cag_prefix,
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
            await self._generate(request, assembled, retrieval, budget)
        )
        timings["generation"] = (time.perf_counter() - stage_start) * 1000.0

        try:
            # Rebuild the completion shape for shadow (model override only).
            shadow_completion = CompletionRequest(
                conversation=assembled.conversation,
                sampling=SamplingParams(
                    temperature=request.temperature,
                    max_output_tokens=request.max_output_tokens,
                ),
                policy=request.routing_policy,
                require_local=request.require_local,
            )
            await self._run_shadow(request, shadow_completion)
        except Exception:
            logger.debug("shadow dispatch failed", exc_info=True)

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
            cag_tokens=cag_prefix.tokens if cag_prefix is not None else 0,
            cag_docs=len(cag_prefix.docs) if cag_prefix is not None else 0,
            cag_identity=cag_prefix.identity.value if cag_prefix is not None else None,
            cag_cache_hit=bool(cag_prefix.cache_hit) if cag_prefix is not None else False,
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
            citations=self._resolve_citations(assembled, retrieval),
            tool_calls=tuple(t.name for t in tool_telemetry),
            confidence=confidence,
            needs_confirmation=tuple(dict.fromkeys(needs_confirmation)),
            pending_tool_calls=tuple(pending_tool_calls),
            plan_summary=tuple(plan_summary),
            resolved_agent=resolved_agent,
        )

    # -- streaming ---------------------------------------------------------------------

    async def stream(self, request: TurnRequest) -> AsyncIterator[Mapping[str, Any]]:
        """Stream a turn as typed frames.

        Frames: ``{"type": "token"|"step"|"actions"|"citations"|"tool_calls"|"revision"|"done"|"error", ...}``.
        Typed rather than raw text so the client never has to string-split a marker out of the
        token stream (TD-12). Planning/execution is the only tool-execute path (ADR-006);
        ``tool_calls`` frames from the model are informational and are not dispatched.

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
                resolved_spec = self._resolve_agent(request)
                resolved_agent = resolved_spec.id if resolved_spec is not None else request.agent
                message = request.message
                if deps.safety is not None:
                    decision = await deps.safety.guard_input(
                        message, user_id=request.user_id, turn_id=turn_id
                    )
                    message = decision.text

                memories, retrieval, history, history_summary, goals, cag_prefix = await self._gather_context(
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
                        below_threshold=retrieval.below_threshold,
                        quarantined=tuple(
                            c for c in retrieval.chunks if c.citation() not in accepted_citations
                        ),
                        strategy=retrieval.strategy,
                        duration_ms=retrieval.duration_ms,
                    )

                tool_declarations = self._tool_declarations(request)
                tool_results: list[tuple[str, str]] = []
                needs_confirmation: list[str] = []
                pending_tool_calls: list[Mapping[str, Any]] = []
                plan_summary: list[Mapping[str, Any]] = []
                outcome: ExecutionOutcome | None = None
                if self._should_plan(request, tool_declarations):
                    try:
                        async for item in self._iter_execution(
                            request, message, tool_declarations, tainted
                        ):
                            if isinstance(item, StepEvent):
                                yield {"type": "step", **item.as_dict()}
                            elif isinstance(item, ExecutionOutcome):
                                outcome = item
                    except Exception:
                        logger.warning("planning or execution failed; answering directly")
                        outcome = None
                if outcome is not None:
                    tool_results = outcome.summary_for_context()
                    plan_summary = [
                        {"id": s.id.value, "kind": str(s.kind), "status": str(s.status),
                         "description": s.description}
                        for s in outcome.plan.steps
                    ]
                    for report in outcome.tool_reports:
                        if report.needs_confirmation:
                            needs_confirmation.append(report.tool)
                            pending_tool_calls.append({
                                "call_id": report.call_id,
                                "tool": report.tool,
                                "arguments": dict(report.arguments),
                            })
                    await self._record_engineer_runs(
                        request, turn_id, correlation, resolved_agent, outcome
                    )

                actions = actions_from_tool_results(tool_results)
                if actions:
                    yield {"type": "actions", "actions": actions}

                assembled = deps.context.assemble(
                    ContextRequest(
                        system_prompt=self._render_system_prompt(request, tool_declarations),
                        user_message=message,
                        history=history,
                        memories=memories,
                        goals=goals,
                        retrieval=retrieval,
                        cag_prefix=cag_prefix,
                        tool_results=tool_results,
                        history_summary=history_summary,
                        context_window=self._context_window(),
                        max_output_tokens=request.max_output_tokens,
                        max_prompt_tokens=self._max_prompt_tokens(),
                    )
                )

                visible_citations = list(
                    self._resolve_citations(assembled, retrieval, user_facing=True)
                )
                if visible_citations:
                    yield {"type": "citations", "citations": visible_citations}

                collected: list[str] = []
                saw_tool_calls = False
                last_usage = None
                assigned = await self._assigned_model(request)
                # Prose-only generation (ADR-006): tools execute via planning above, not via
                # native tool_calls from the model. Advertising tools here makes local models
                # answer with tool-calls-only and no text.
                completion = CompletionRequest(
                    conversation=assembled.conversation,
                    model=assigned,
                    sampling=SamplingParams(
                        temperature=request.temperature,
                        max_output_tokens=request.max_output_tokens,
                    ),
                    policy=request.routing_policy,
                    require_local=request.require_local,
                    tools=(),
                    timeout_s=self._completion_timeout_s(),
                )

                async for chunk in deps.router.stream(completion):
                    if chunk.usage is not None:
                        last_usage = chunk.usage
                    if chunk.delta:
                        collected.append(chunk.delta)
                        yield {"type": "token", "text": chunk.delta}
                    if chunk.tool_calls:
                        # Informational only. Dispatching these would bypass taint/confirmation
                        # (ADR-006); planning/execution above is the only execute path.
                        saw_tool_calls = True
                        yield {
                            "type": "tool_calls",
                            "calls": [
                                {"name": c.name, "arguments": dict(c.arguments)}
                                for c in chunk.tool_calls
                            ],
                        }

                answer = "".join(collected)
                empty_generation_fallback = False
                if not answer.strip() and saw_tool_calls:
                    # The model answered with a native tool-call and no text. Those calls are
                    # informational only here (ADR-006) and were never executed, so without this
                    # the turn would otherwise end as a bare "success" with an empty answer -- the
                    # client has no way to tell that apart from a genuine failure.
                    logger.warning(
                        "generation returned tool-calls-only with no text; synthesizing fallback",
                        extra={"needs_confirmation": bool(needs_confirmation)},
                    )
                    empty_generation_fallback = True
                    answer = (
                        "I have a suggested action ready for your confirmation below."
                        if needs_confirmation
                        else "I wasn't able to put together a reply for that -- "
                        "could you rephrase your message or try again?"
                    )
                    yield {"type": "token", "text": answer}

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

                # Fire-and-forget shadow; failures must not fail the turn.
                try:
                    await self._run_shadow(request, completion)
                except Exception:
                    logger.debug("shadow dispatch failed", exc_info=True)

                searched_empty = bool(
                    retrieval is not None and retrieval.is_empty_but_searched
                )
                yield {
                    "type": "done",
                    "tainted": tainted,
                    "guards_complete": guards_complete,
                    "citations": visible_citations,
                    "needs_confirmation": list(dict.fromkeys(needs_confirmation)),
                    "pending_tool_calls": pending_tool_calls,
                    "plan_summary": plan_summary,
                    "resolved_agent": resolved_agent,
                    "turn_id": turn_id.value,
                    "correlation_id": correlation.value,
                    # Stream does not run the reasoner; null is honest, not a fake score.
                    "confidence": None,
                    "degraded": bool(tainted or searched_empty or empty_generation_fallback),
                    "searched_but_empty": searched_empty,
                    "empty_generation_fallback": empty_generation_fallback,
                    "usage": {
                        "prompt_tokens": last_usage.prompt_tokens if last_usage else 0,
                        "completion_tokens": (
                            last_usage.completion_tokens if last_usage else len(collected)
                        ),
                        "cost_usd": 0.0,
                    },
                }
            except AuraError as exc:
                frame: dict[str, Any] = {
                    "type": "error",
                    "error": exc.kind,
                    "message": exc.message,
                    "retryable": exc.retryable,
                }
                if exc.details:
                    frame["details"] = exc.details
                yield frame
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("unhandled error during stream")
                yield {"type": "error", "error": "internal_error",
                       "message": type(exc).__name__, "retryable": False}

    # -- stages ------------------------------------------------------------------------

    async def _gather_context(
        self, request: TurnRequest, message: str
    ) -> tuple[
        list[MemoryHit],
        RetrievalResult | None,
        Conversation,
        str,
        Sequence[GoalSnapshot],
        Any,
    ]:
        """Run memory recall, knowledge retrieval, history, goals, and CAG concurrently.

        These are independent. Running them in sequence is the single largest avoidable latency
        in the legacy pipeline, which additionally embeds the same message four separate times
        (Performance Report §1).
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

        async def load_goals() -> Sequence[GoalSnapshot]:
            if deps.goals is None:
                return ()
            try:
                return await deps.goals.active_summaries(request.user_id)
            except Exception:
                logger.warning("goal lookup failed; continuing without it")
                return ()

        async def load_cag() -> Any:
            if deps.cag is None:
                return None
            try:
                return await deps.cag.prefix_for(request.user_id)
            except Exception:
                logger.warning("cag prefix load failed; continuing without it")
                return None

        memories, retrieval, (history, summary), goals, cag_prefix = await asyncio.gather(
            recall(), retrieve(), load_history(), load_goals(), load_cag()
        )
        return memories, retrieval, history, summary, goals, cag_prefix

    def _resolve_agent(self, request: TurnRequest) -> AgentSpec | None:
        """The turn's resolved persona (Phase C1), or ``None`` with no registry configured.

        ``None`` preserves the pre-Phase-C1 behaviour exactly: every tool the caller's
        capabilities allow, gated only by :data:`AGENT_PROMPT_IDS` for the prompt text. A
        registry turns that into a real per-agent grant everywhere this is consulted.
        """
        if self._deps.agents is None:
            return None
        return self._deps.agents.resolve(request.agent, capabilities=request.capabilities)

    async def _record_engineer_runs(
        self,
        request: TurnRequest,
        turn_id: TurnId,
        correlation: CorrelationId,
        resolved_agent: str,
        outcome: ExecutionOutcome | None,
    ) -> None:
        """Durable record of what an engineer-agent tool call did, for the client's history view.

        Only tools gated behind `required_capability="engineer_agent"` are recorded -- every
        other tool's outcome already has a home (telemetry, audit) that isn't specific to this
        one persona. Fires for every terminal report (succeeded, failed, or still pending
        confirmation), not just successes, so a rejected/failed attempt is visible too.
        """
        deps = self._deps
        if deps.engineer_runs is None or outcome is None or not outcome.tool_reports:
            return
        steps_by_call_id = {f"step_{s.id.value}": s for s in outcome.plan.steps}
        now = datetime.now(timezone.utc)
        for report in outcome.tool_reports:
            try:
                spec = deps.tools.spec(report.tool) if deps.tools is not None else None
            except Exception:
                spec = None
            if spec is None or spec.required_capability != _ENGINEER_AGENT_CAPABILITY:
                continue
            step = steps_by_call_id.get(report.call_id)
            text = (step.result or "") if step is not None else ""
            status = (
                EngineerRunStatus.SUCCEEDED if report.ok
                else EngineerRunStatus.PENDING if report.needs_confirmation
                else EngineerRunStatus.FAILED
            )
            pr_url = None
            if report.tool == "github_create_draft_pull_request":
                match = _URL_RE.search(text)
                pr_url = match.group(0) if match else None
            commit_sha = None
            if report.tool in ("github_create_branch", "git_commit"):
                match = _SHA_RE.search(text)
                commit_sha = match.group(1) if match else None
            sandbox_transcript = (
                text if report.tool in
                ("write_file", "edit_file", "run_command", "git_diff", "git_commit",
                 "git_checkout_branch")
                else None
            )
            try:
                await deps.engineer_runs.record(EngineerRun(
                    id=EngineerRunId.new(),
                    user_id=request.user_id,
                    turn_id=turn_id.value,
                    correlation_id=correlation.value,
                    persona=resolved_agent,
                    tool=report.tool,
                    call_id=report.call_id,
                    status=status,
                    created_at=now,
                    updated_at=now,
                    pr_url=pr_url,
                    branch=dict(report.arguments).get("branch"),
                    commit_sha=commit_sha,
                    sandbox_transcript=sandbox_transcript,
                ))
            except Exception:
                logger.warning("failed to record engineer run", extra={"tool": report.tool})

    def _tool_declarations(self, request: TurnRequest) -> tuple[Mapping[str, Any], ...]:
        deps = self._deps
        if not (request.use_tools and deps.tools is not None):
            return ()
        agent = self._resolve_agent(request)
        max_sensitivity = agent.risk_ceiling if agent is not None else ToolSensitivity.SYSTEM
        declarations = deps.tools.declarations(
            capabilities=request.capabilities, max_sensitivity=max_sensitivity
        )
        if agent is None or agent.allowed_tools is None:
            return declarations
        # Not advertised, not requested: a tool outside the agent's grant should not appear in
        # the model's tool list, same reasoning as ToolRegistry.available()'s own capability
        # filter -- offering it only produces a call that policy.evaluate() then refuses.
        return tuple(d for d in declarations if d.get("name") in agent.allowed_tools)

    def _should_plan(self, request: TurnRequest, tools: Sequence[Mapping[str, Any]]) -> bool:
        deps = self._deps
        if deps.planner is None or deps.executor is None or not tools:
            return False
        force = request.plan
        if force is None and (request.confirmed_tools or request.confirmed_call_ids):
            # A caller supplying confirmed_tools/confirmed_call_ids is resuming a tool the user just approved;
            # that must run through planning/execution (the only sanctioned execute path,
            # ADR-006) regardless of whether the confirmation reply itself matches the
            # short-message heuristic below.
            force = True
        return deps.planner.needs_plan(
            request.message, available_tools=[t.get("name", "") for t in tools], force=force
        )

    def _tool_context(self, request: TurnRequest, tainted: bool) -> ToolContext:
        agent = self._resolve_agent(request)
        return ToolContext(
            user_id=request.user_id,
            correlation_id=(request.correlation_id or CorrelationId.new()).value,
            deadline_s=60.0,
            tainted=tainted,
            agent_id=agent.id if agent is not None else None,
            allowed_tools=agent.allowed_tools if agent is not None else None,
            max_sensitivity=agent.risk_ceiling if agent is not None else None,
            memory_scope=agent.memory_scope if agent is not None else "shared",
        )

    async def _compose_plan(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> Any:
        deps = self._deps
        try:
            goal = deps.planner.goal_from_message(message)
            plan = await deps.planner.plan(goal, available_tools=tools)
            if not plan.steps:
                return None
            if len(plan.steps) <= 1 and plan.steps[0].kind is StepKind.RESPOND:
                # A one-step "just answer" plan is what the direct path already does; running the
                # executor for it costs latency and buys nothing.
                return None
            return plan
        except Exception:
            logger.warning("planning failed; answering directly")
            return None

    def _agentic_loop_deadline_s(self) -> float:
        return float(
            getattr(getattr(self._deps.settings, "reasoning", None), "engineer_loop_deadline_s", 600.0)
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
            plan = await self._compose_plan(request, message, tools)
            if plan is None:
                return None
            context = self._tool_context(request, tainted)
            agent = self._resolve_agent(request)
            max_iterations = agent.max_plan_iterations if agent is not None else 1
            if max_iterations > 1 and deps.agentic_executor is not None:
                return await deps.agentic_executor.run(
                    plan,
                    context=context,
                    capabilities=request.capabilities,
                    confirmed_tools=request.confirmed_tools,
                    confirmed_call_ids=request.confirmed_call_ids,
                    available_tools=tools,
                    max_iterations=max_iterations,
                    deadline_s=self._agentic_loop_deadline_s(),
                )
            return await deps.executor.execute(
                plan,
                context=context,
                capabilities=request.capabilities,
                confirmed_tools=request.confirmed_tools,
                confirmed_call_ids=request.confirmed_call_ids,
            )
        except Exception:
            logger.warning("planning or execution failed; answering directly")
            return None

    async def _iter_execution(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
        tainted: bool,
    ) -> AsyncIterator[StepEvent | ExecutionOutcome]:
        """Yield step progress, then the outcome. Prefer ``executor.stream``."""
        deps = self._deps
        plan = await self._compose_plan(request, message, tools)
        if plan is None:
            return
        context = self._tool_context(request, tainted)
        agent = self._resolve_agent(request)
        max_iterations = agent.max_plan_iterations if agent is not None else 1
        if max_iterations > 1 and deps.agentic_executor is not None:
            async for item in deps.agentic_executor.stream(
                plan,
                context=context,
                capabilities=request.capabilities,
                confirmed_tools=request.confirmed_tools,
                confirmed_call_ids=request.confirmed_call_ids,
                available_tools=tools,
                max_iterations=max_iterations,
                deadline_s=self._agentic_loop_deadline_s(),
            ):
                yield item
            return
        stream = getattr(deps.executor, "stream", None)
        if callable(stream):
            async for item in stream(
                plan,
                context=context,
                capabilities=request.capabilities,
                confirmed_tools=request.confirmed_tools,
                confirmed_call_ids=request.confirmed_call_ids,
            ):
                yield item
            return
        yield await deps.executor.execute(
            plan,
            context=context,
            capabilities=request.capabilities,
            confirmed_tools=request.confirmed_tools,
            confirmed_call_ids=request.confirmed_call_ids,
        )

    @staticmethod
    def _format_tool_capabilities(
        tools: Sequence[Mapping[str, Any]], *, max_names: int = 5
    ) -> str:
        """Keep orchestrator prompts within the CPU prefill budget.

        Listing every registered tool name can consume most of the 400-token cap and
        evict retrieved knowledge from the packed context (no citations, no grounding).
        """
        names = [str(tool.get("name") or "") for tool in tools if tool.get("name")]
        if not names:
            return ""
        if len(names) <= max_names:
            return ", ".join(names)
        shown = ", ".join(names[:max_names])
        return f"{shown}, ... ({len(names)} tools total)"

    @staticmethod
    def _resolve_citations(
        assembled: AssembledContext,
        retrieval: RetrievalResult | None,
        *,
        user_facing: bool = False,
    ) -> tuple[str, ...]:
        packed = assembled.user_citations if user_facing else assembled.citations
        if packed:
            return packed
        if retrieval and retrieval.chunks:
            cites = retrieval.citations()
            return user_facing_citations(cites) if user_facing else cites
        return ()

    def _render_system_prompt(
        self, request: TurnRequest, tools: Sequence[Mapping[str, Any]]
    ) -> str:
        agent = self._resolve_agent(request)
        template_id = (
            agent.prompt_template_id if agent is not None
            else AGENT_PROMPT_IDS.get(request.agent, AGENT_PROMPT_IDS["orchestrator"])
        )
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
                    "capabilities": self._format_tool_capabilities(tools),
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

    def _completion_timeout_s(self) -> float:
        """Router first-token deadline. Must match ``AURA_LLM_TIMEOUT_S``.

        ``CompletionRequest.timeout_s`` defaults to 60s. The CPU Ollama stack is configured
        for 180s (``AURA_PROVIDER_LOCAL_TIMEOUT_S``), but that only covers the HTTP client.
        The router wraps the first streamed chunk in ``asyncio.wait_for(..., timeout=request.timeout_s)``,
        so leaving the dataclass default in place kills a cold 3B prefill and surfaces as
        ``all 1 provider attempt(s) failed before streaming began``.
        """
        llm = getattr(self._deps.settings, "llm", None) if self._deps.settings is not None else None
        raw = getattr(llm, "request_timeout_s", 60.0) if llm is not None else 60.0
        try:
            timeout = float(raw)
        except (TypeError, ValueError):
            return 60.0
        return timeout if timeout > 0 else 60.0

    def _registry_model_id(self) -> str:
        learning = getattr(self._deps.settings, "learning", None) if self._deps.settings else None
        return str(getattr(learning, "finetune_model_id", "aura-ft") or "aura-ft")

    async def _assigned_model(self, request: TurnRequest) -> str | None:
        """Resolve a registry canary/production artefact for this user, if any."""
        deps = self._deps
        if deps.models is None:
            return None
        try:
            assignment = await deps.models.assign(
                self._registry_model_id(), subject=request.user_id.value
            )
        except Exception:
            logger.debug("model registry assign failed", exc_info=True)
            return None
        if assignment is None:
            return None
        try:
            version = await deps.models.get(assignment.model_id, assignment.version)
        except Exception:
            return None
        return self._servable_model_tag(version.artefact_uri)

    @staticmethod
    def _servable_model_tag(artefact_uri: str | None) -> str | None:
        """Use a registry artefact only when it is an inference tag, not a file URI.

        ScriptedTrainer writes ``scripted://...`` placeholders. Sending those as
        ``CompletionRequest.model`` makes Ollama 404 before the first token.
        """
        if not artefact_uri or not artefact_uri.strip():
            return None
        uri = artefact_uri.strip()
        if "://" in uri or uri.startswith(("/", "\\")):
            logger.warning(
                "registry artefact is not a servable model tag; using the provider default"
            )
            return None
        return uri

    async def _run_shadow(
        self, request: TurnRequest, completion: CompletionRequest
    ) -> None:
        """Mirror traffic to shadow versions; never surface their output."""
        deps = self._deps
        if deps.models is None or deps.router is None:
            return
        try:
            shadows = await deps.models.shadow_versions(self._registry_model_id())
        except Exception:
            return
        for shadow in shadows:
            if not shadow.artefact_uri:
                continue
            try:
                started = time.perf_counter()
                shadow_req = CompletionRequest(
                    conversation=completion.conversation,
                    model=shadow.artefact_uri,
                    sampling=completion.sampling,
                    policy=completion.policy,
                    require_local=completion.require_local,
                    tools=(),
                )
                await deps.router.complete(shadow_req)
                elapsed = (time.perf_counter() - started) * 1000.0
                await deps.models.record_metrics(
                    shadow.model_id,
                    shadow.version,
                    {"shadow_latency_ms": elapsed, "shadow_ok": 1.0},
                )
            except Exception as exc:
                try:
                    await deps.models.record_metrics(
                        shadow.model_id,
                        shadow.version,
                        {"shadow_ok": 0.0},
                    )
                except Exception:
                    pass
                logger.debug(
                    "shadow generation failed",
                    extra={"model": shadow.key, "error": type(exc).__name__},
                )

    async def _generate(
        self,
        request: TurnRequest,
        assembled: Any,
        retrieval: RetrievalResult | None,
        budget: TurnBudget,
    ) -> tuple[str, str | None, str | None, Any, float, bool, str | None, int, Any, float | None]:
        deps = self._deps
        namespace = request.user_id.value

        assigned = await self._assigned_model(request)
        completion = CompletionRequest(
            conversation=assembled.conversation,
            model=assigned,
            sampling=SamplingParams(
                temperature=request.temperature, max_output_tokens=request.max_output_tokens
            ),
            policy=request.routing_policy,
            require_local=request.require_local,
            tools=(),
            timeout_s=self._completion_timeout_s(),
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
