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
import json
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping, Sequence

from ..context.engine import ContextEngine, ContextRequest, AssembledContext
from ..domain.errors import (
    AllProvidersFailedError,
    AuraError,
    BudgetExceededError,
    DependencyUnavailableError,
    OutputInvalidError,
    OverloadedError,
    PlanningError,
    SafetyViolationError,
    UserConcurrencyLimitError,
)
from ..domain.events import TurnCompleted, TurnFailed, TurnStarted
from ..domain.caller_context import CallerContext
from ..domain.images import ImageInput
from ..domain.connectors import Credential, connector_token_key
from ..domain.context import user_facing_citations
from ..domain.engineering import EngineerRun, EngineerRunStatus
from ..domain.identifiers import CorrelationId, EngineerRunId, SessionId, TurnId, UserId
from ..domain.knowledge import RetrievalResult
from ..domain.llm import (
    CompletionRequest,
    CompletionResponse,
    DataClassification,
    RoutingPolicy,
    SamplingParams,
    TokenUsage,
    WorkloadClass,
    WorkloadHint,
)
from ..domain.memory import MemoryHit, MemoryKind, MemoryQuery, readable_scopes
from ..domain.output_schema import parse_structured
from ..domain.messages import Conversation, Message
from ..domain.planning import StepKind, StepStatus
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
from ..execution.agentic_loop import AgentRunContext
from ..execution.confirmations import PendingConfirmationStore, ResumedTurn
from ..execution.engine import ExecutionOutcome, StepEvent
from ..llm.cache import InMemoryCache
from ..llm.router import TurnBudget
from ..llm.sensitivity import DataScope, data_scope, escalate
from ..llm.workload import workload_for_turn, workload_scope
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
#: ADR-030 Phase 4: a session is "clean" while none of its turns was SENSITIVE. Stored as a
#: positive marker so that losing it (eviction, a Redis restart) fails *closed*: a session whose
#: marker is missing but which has history is treated as having been sensitive.
_SESSION_CLEAN_KEY = "llm:session-clean:v1:{user}:{session}"
_SESSION_CLEAN_TTL_S = 30 * 86400


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
    #: Server-issued ids (``pending_tool_calls[].approval_id``) of the held calls the user
    #: approved. A request carrying them *resumes that pending turn*: its stored plan runs -- it
    #: is not re-planned -- and only a call identical to an approved one is dispatched as
    #: confirmed, once (``execution/confirmations.py``; ADR-006, F-04). Tool names and
    #: positional call ids no longer confirm anything.
    approval_ids: tuple[str, ...] = ()
    max_output_tokens: int = 1024
    temperature: float = 0.3
    route: str = "chat"
    #: How many `delegate_to_agent` hops produced this turn (aura_core/tools/delegation.py).
    #: `0` for every ordinary, non-delegated turn. Checked against
    #: `settings.reasoning.max_delegation_depth` before a further delegation is allowed.
    delegation_depth: int = 0
    #: Set by `delegate_to_agent` on the child `TurnRequest` it builds: the intersection of the
    #: delegating persona's own grant and the target persona's declared grant. `_tool_context()`
    #: and `_tool_declarations()` intersect this again with the resolved `AgentSpec`'s own grant
    #: (defense in depth -- a bug in the tool handler cannot hand a sub-turn a broader grant than
    #: its own persona ever declares). `None` for every ordinary turn (no override).
    delegated_tool_grant: frozenset[str] | None = None
    delegated_risk_ceiling: ToolSensitivity | None = None
    #: Set by `delegate_to_agent` to the delegating turn's own taint (``ToolContext.tainted``).
    #: OR'd into the child turn's taint before planning, so ADR-006 holds across kernel hops: a
    #: parent whose context was quarantined (a poisoned chunk, `fetch_url` output) cannot launder
    #: that by delegating to a persona whose `create_*`/`update_*`/`delete_*` tools would then run
    #: without confirmation because the child's *own* inputs looked clean. `False` for every
    #: ordinary, non-delegated turn.
    inherited_taint: bool = False
    #: D8 (docs/architecture/015-desktop-ide.md): opt-in per-task isolated git worktree, Desktop
    #: IDE only. `False` for every other caller -- see `tools/coding.py::_SandboxSessionCache`'s
    #: `create_task` call for where this actually takes effect.
    desktop_task_isolation: bool = False
    #: The Desktop IDE's active workspace root (audit FSA-006, second half), in the user's own
    #: machine's path syntax. Opaque here: it rides `ToolContext.metadata` to `run_command`, which
    #: hands it to the sandbox runner as `cwd`; only the paired daemon can say whether it lies
    #: inside a granted root, and refuses it otherwise. `None` for every caller without an open
    #: desktop workspace, which leaves the daemon on its "first granted root" fallback.
    workspace_root: str | None = None
    #: Admission class for every generation this turn makes (roadmap P2.4). ``None`` derives it
    #: from ``route``/``agent`` (``llm/workload.py``): Engineer -> coder pool at priority 2,
    #: voice and chat -> general-fast at priority 1. Background callers set it explicitly.
    workload: WorkloadClass | None = None
    #: Evidence the calling system supplied for this turn (AR-5). DATA channel only: guarded
    #: like retrieved chunks, fenced, and its presence taints the turn.
    caller_context: tuple[CallerContext, ...] = ()
    #: Normalized images the user attached (ADR-031). Never sent to the answering model: the
    #: kernel has ``deps.vision`` describe them first, and the description joins
    #: ``caller_context`` -- DATA, guarded, fenced and tainting -- before anything else runs.
    #: Non-empty makes the turn SENSITIVE. Never persisted.
    images: tuple[ImageInput, ...] = ()
    #: Structured output (AR-6): the answer must be JSON matching this schema (the subset in
    #: ``domain/output_schema.py``), repaired at most once, or the turn fails ``output_invalid``.
    response_schema: Mapping[str, Any] | None = None
    #: The caller's own tokens for ``DELEGATED`` connectors, by connector id (AP10, ADR-021).
    #: Redacting ``Credential`` values, carried for this turn only into ``ToolContext.credentials``
    #: and from there to that one connector's reads; nothing stores a ``TurnRequest``.
    connector_tokens: Mapping[str, Credential] | None = None


@dataclass(frozen=True, slots=True)
class TurnResult:
    """One turn's output, with its telemetry."""

    text: str
    telemetry: TurnTelemetry
    citations: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    confidence: float | None = None
    needs_confirmation: tuple[str, ...] = ()
    #: One entry per confirm-required tool call this turn, `{"call_id", "tool", "arguments",
    #: "diff_preview", "approval_id"}`. `approval_id` is what a caller sends back (in
    #: `TurnRequest.approval_ids`) to approve exactly that call; it is absent only when the
    #: pending turn could not be stored, and then the call cannot be confirmed at all.
    #: Not deduplicated by tool name (unlike `needs_confirmation`) -- two distinct calls to the
    #: same tool must surface as two separate pending calls, not collapse into one.
    pending_tool_calls: tuple[Mapping[str, Any], ...] = ()
    plan_summary: tuple[Mapping[str, Any], ...] = ()
    #: The persona id `AgentRegistry.resolve()` actually used -- may differ from
    #: `TurnRequest.agent` when the caller lacked that persona's `required_capabilities` and the
    #: registry silently fell back to `orchestrator` (see `AgentRegistry.resolve()`'s docstring).
    #: Lets a caller detect that fallback instead of it being invisible.
    resolved_agent: str | None = None
    #: Set when the turn was refused for lack of generation capacity (``OverloadedError``), so
    #: the HTTP layer can send ``Retry-After`` instead of leaving the client to guess.
    retry_after_s: int | None = None
    #: The durable agent run this turn's plan/execute loop recorded (GAP-024), readable via
    #: ``GET /api/v3/agents/runs/{id}``; ``None`` when the turn planned nothing.
    agent_run_id: str | None = None
    #: The validated JSON value when the turn asked for structured output (AR-6); never set
    #: from an answer that failed its schema.
    data: Any = None
    #: Machine-readable detail of a failed turn that is safe to return (``output_invalid``'s
    #: error paths). ``None`` for every other failure.
    error_details: Mapping[str, Any] | None = None
    #: How attached images were understood (``VisionEngine.Perception.as_dict``), or ``None``
    #: for a turn without images.
    vision: Mapping[str, Any] | None = None

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
    #: Per-run agent state (GAP-024) is recorded by `agentic_executor` itself, through the
    #: `AgentRunStore` extension of this same port.
    engineer_runs: Any = None
    episodic: Any = None
    cache: Any = None
    #: Key/value store (the ``Cache`` port, not ``LLMCache``) for ADR-030 Phase 4's per-session
    #: "clean" marker. ``None`` means every session with history counts as having been
    #: SENSITIVE once any provider restricts content -- fail closed.
    session_marks: Any = None
    metrics: Any = None
    event_bus: Any = None
    telemetry_sink: Any = None
    clock: Any = None
    settings: Any = None
    models: Any = None
    #: `HostAgentRegistry` (`adapters/host_agent.py`) -- `None` disables the desktop-pairing check
    #: entirely, so `_tool_declarations` behaves exactly as before (matching every other optional
    #: dependency's "absent means off" contract).
    host_agent_registry: Any = None
    #: `PendingConfirmationStore` (`execution/confirmations.py`) holding confirm-gated turns
    #: until the user approves them. `None` falls back to a process-local store, which is
    #: correct for one process; the container wires one over the shared cache (Redis).
    tool_confirmations: Any = None
    #: `VisionEngine` (`vision/engine.py`, ADR-031). `None` means image turns fail with
    #: `dependency_unavailable`; text turns are unaffected.
    vision: Any = None


#: Told to the model when planning or tool execution failed (CB-14). Without it the model saw
#: only the user's request and would answer "Done, I created the task" when nothing had run.
PLANNING_FAILED_NOTE = (
    "Tool planning failed for this turn, so no tool ran and no action was taken. Do not say "
    "that anything was created, changed, sent or scheduled; tell the user it could not be done "
    "right now and that they can try again."
)


def _stream_error_outcome(exc: AuraError) -> TurnOutcome:
    """The telemetry outcome for an error ending a streamed turn, as ``run`` maps them."""
    if isinstance(exc, UserConcurrencyLimitError):
        return TurnOutcome.RATE_LIMITED
    if isinstance(exc, SafetyViolationError):
        return TurnOutcome.BLOCKED
    if isinstance(exc, BudgetExceededError):
        return TurnOutcome.BUDGET_EXCEEDED
    return TurnOutcome.ERROR


class AIKernel:
    """Runs one turn through the pipeline."""

    def __init__(self, deps: KernelDependencies) -> None:
        self._deps = deps
        self._metrics = deps.metrics
        self._confirmations = deps.tool_confirmations or PendingConfirmationStore(
            InMemoryCache(max_entries=1024, default_ttl_s=3600)
        )

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
                with workload_scope(self._workload_hint(request)), data_scope(
                    *self._baseline_classification(request)
                ) as scope:
                    result = await self._run_turn(request, correlation, turn_id, started)
                    await self._remember_session_classification(request, scope)
                    return result
            except UserConcurrencyLimitError as exc:
                return self._error_result(
                    request, correlation, turn_id, started, exc, TurnOutcome.RATE_LIMITED
                )
            except OverloadedError as exc:
                return self._error_result(
                    request, correlation, turn_id, started, exc, TurnOutcome.ERROR
                )
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
        resume = await self._claim_confirmation(request)
        if resume is not None:
            request = self._resumed_request(request, resume)
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

        # 1b. Images become DATA evidence before anything reads the turn (ADR-031) ----
        request, perception = await self._perceive_images(request, message)

        # 2-4. Memory, retrieval, and history concurrently ---------------------------
        stage_start = time.perf_counter()
        memories, retrieval, history, history_summary, goals, cag_prefix = await self._gather_context(
            request, message
        )
        timings["memory"] = timings["retrieval"] = (time.perf_counter() - stage_start) * 1000.0
        await self._classify_context(request, memories, retrieval, history, goals, cag_prefix)

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
        caller_context, context_quarantined = await self._guard_caller_context(request, turn_id)
        quarantined_count += context_quarantined
        tainted = (
            tainted or bool(request.caller_context) or bool(resume and resume.tainted)
            or request.inherited_taint
        )

        # 6. Plan and execute --------------------------------------------------------
        tool_telemetry: list[ToolTelemetry] = []
        tool_results: list[tuple[str, str]] = []
        execution_notes: list[str] = []
        needs_confirmation: list[str] = []
        pending_tool_calls: list[Mapping[str, Any]] = []
        plan_summary: list[Mapping[str, Any]] = []
        plan_steps = plan_revisions = 0
        agent_run_id: str | None = None
        planning_failed = False

        tool_declarations = await self._tool_declarations(request)
        if resume is not None or self._should_plan(request, tool_declarations):
            stage_start = time.perf_counter()
            try:
                outcome = await self._plan_and_execute(
                    request, message, tool_declarations, tainted,
                    turn_id=turn_id, correlation=correlation, resume=resume,
                )
            except Exception:
                logger.warning("planning or execution failed; answering directly", exc_info=True)
                outcome = None
                planning_failed = True
                execution_notes.append(PLANNING_FAILED_NOTE)
            timings["planning"] = (time.perf_counter() - stage_start) * 1000.0
            if outcome is not None:
                agent_run_id = getattr(outcome, "run_id", None)
                tool_results = outcome.summary_for_context()
                plan_steps = len(outcome.plan.steps)
                plan_revisions = len(outcome.plan.revisions)
                plan_summary = [
                    {"id": s.id.value, "kind": str(s.kind), "status": str(s.status),
                     "description": s.description}
                    for s in outcome.plan.steps
                ]
                execution_notes.extend(self._execution_notes(outcome))
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
                pending_tool_calls = await self._hold_for_confirmation(
                    request, turn_id, outcome
                )
                await self._record_engineer_runs(
                    request, turn_id, correlation, resolved_agent, outcome
                )

        # 7. Assemble context --------------------------------------------------------
        stage_start = time.perf_counter()
        system_prompt = await self._render_system_prompt(request, tool_declarations)
        assembled = deps.context.assemble(
            ContextRequest(
                system_prompt=system_prompt,
                user_message=self._with_schema_instruction(message, request.response_schema),
                history=history,
                memories=memories,
                goals=goals,
                retrieval=retrieval,
                cag_prefix=cag_prefix,
                tool_results=tool_results,
                scratchpad=execution_notes,
                history_summary=history_summary,
                context_window=self._context_window(),
                max_output_tokens=request.max_output_tokens,
                max_prompt_tokens=self._max_prompt_tokens(),
                caller_context=caller_context,
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
                system_prompt=assembled.instructions_text,
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

        # 9b. Structured output (AR-6) -----------------------------------------------
        data = None
        if request.response_schema is not None:
            answer, data, repair_usage, repair_cost = await self._enforce_response_schema(
                request, assembled, answer, budget, turn_id
            )
            if repair_usage is not None:
                usage = (usage or TokenUsage(prompt_tokens=assembled.prompt_tokens)) + repair_usage
                cost += repair_cost

        # 10. Persist session state --------------------------------------------------
        if deps.episodic is not None and request.session_id is not None:
            try:
                await deps.episodic.append(
                    request.session_id, request.user_id,
                    user_message=self._history_message(request), assistant_message=answer,
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
            planning_failed=planning_failed,
            confidence=confidence,
            groundedness=groundedness,
        )
        await self._record(telemetry)

        return TurnResult(
            text=answer,
            telemetry=telemetry,
            # User-facing, as the stream path already was: `memory:`/`tool:`/`goal:` provenances
            # are packing bookkeeping, and a caller validating citations (AR-5) must only ever
            # see documents and the context ids it sent.
            citations=self._resolve_citations(assembled, retrieval, user_facing=True),
            tool_calls=tuple(t.name for t in tool_telemetry),
            confidence=confidence,
            needs_confirmation=tuple(dict.fromkeys(needs_confirmation)),
            pending_tool_calls=tuple(pending_tool_calls),
            plan_summary=tuple(plan_summary),
            resolved_agent=resolved_agent,
            agent_run_id=agent_run_id,
            data=data,
            vision=perception.as_dict() if perception is not None else None,
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
        started = time.perf_counter()
        # The stream is the client's only chat path, so it carries the same lifecycle as
        # ``run``: in-flight gauge, TurnStarted, a TurnBudget, and a recorded TurnTelemetry
        # (F-24). Before this, admin quality dashboards saw none of the real users' turns.
        if self._metrics is not None:
            self._metrics.turns_in_flight.inc()
        await self._publish(
            TurnStarted(
                correlation_id=correlation, turn_id=turn_id,
                session_id=request.session_id, user_id=request.user_id, route=request.route,
            )
        )

        with LogContext(correlation_id=correlation.value, route=request.route), workload_scope(
            self._workload_hint(request)
        ), data_scope(*self._baseline_classification(request)) as scope:
            try:
                deps = self._deps
                budget = TurnBudget(
                    max_generations=getattr(
                        getattr(deps.settings, "llm", None), "max_generations_per_turn", 8
                    )
                )
                timings: dict[str, float] = {}
                guard_records: list[Mapping[str, Any]] = []
                resume = await self._claim_confirmation(request)
                if resume is not None:
                    request = self._resumed_request(request, resume)
                resolved_spec = self._resolve_agent(request)
                resolved_agent = resolved_spec.id if resolved_spec is not None else request.agent
                message = request.message
                if deps.safety is not None:
                    stage_start = time.perf_counter()
                    decision = await deps.safety.guard_input(
                        message, user_id=request.user_id, turn_id=turn_id
                    )
                    message = decision.text
                    guard_records.extend(decision.report.summary())
                    timings["guards_in"] = (time.perf_counter() - stage_start) * 1000.0

                perception = None
                if request.images:
                    # The vision call can take tens of seconds on a CPU host; say what is
                    # happening rather than leaving the stream silent.
                    yield {
                        "type": "step", "step_id": "vision", "kind": "vision",
                        "status": "running", "description": "Looking at the image(s)",
                        "detail": {"images": len(request.images)}, "elapsed_ms": 0.0,
                    }
                    request, perception = await self._perceive_images(request, message)
                    yield {
                        "type": "step", "step_id": "vision", "kind": "vision",
                        "status": "done", "description": "Looked at the image(s)",
                        "detail": perception.as_dict(),
                        "elapsed_ms": round(perception.duration_ms, 2),
                    }

                stage_start = time.perf_counter()
                memories, retrieval, history, history_summary, goals, cag_prefix = await self._gather_context(
                    request, message
                )
                timings["memory"] = timings["retrieval"] = (
                    time.perf_counter() - stage_start
                ) * 1000.0
                await self._classify_context(
                    request, memories, retrieval, history, goals, cag_prefix
                )

                tainted = False
                quarantined_count = 0
                if deps.safety is not None and retrieval is not None and retrieval.chunks:
                    items = [(c.citation(), c.chunk.content) for c in retrieval.chunks]
                    accepted, _quarantined, tainted = await deps.safety.guard_context_items(
                        items, user_id=request.user_id, turn_id=turn_id
                    )
                    quarantined_count = len(_quarantined)
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
                caller_context, context_quarantined = await self._guard_caller_context(
                    request, turn_id
                )
                quarantined_count += context_quarantined
                tainted = (
                    tainted or bool(request.caller_context) or bool(resume and resume.tainted)
                    or request.inherited_taint
                )

                tool_declarations = await self._tool_declarations(request)
                tool_results: list[tuple[str, str]] = []
                needs_confirmation: list[str] = []
                pending_tool_calls: list[Mapping[str, Any]] = []
                plan_summary: list[Mapping[str, Any]] = []
                tool_telemetry: list[ToolTelemetry] = []
                execution_notes: list[str] = []
                planning_failed = False
                outcome: ExecutionOutcome | None = None
                if resume is not None or self._should_plan(request, tool_declarations):
                    stage_start = time.perf_counter()
                    try:
                        async for item in self._iter_execution(
                            request, message, tool_declarations, tainted,
                            turn_id=turn_id, correlation=correlation, resume=resume,
                        ):
                            if isinstance(item, StepEvent):
                                yield {"type": "step", **item.as_dict()}
                            elif isinstance(item, ExecutionOutcome):
                                outcome = item
                    except Exception:
                        logger.warning(
                            "planning or execution failed; answering directly", exc_info=True
                        )
                        outcome = None
                        planning_failed = True
                        execution_notes.append(PLANNING_FAILED_NOTE)
                    timings["planning"] = (time.perf_counter() - stage_start) * 1000.0
                if outcome is not None:
                    tool_results = outcome.summary_for_context()
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
                    pending_tool_calls = await self._hold_for_confirmation(
                        request, turn_id, outcome
                    )
                    await self._record_engineer_runs(
                        request, turn_id, correlation, resolved_agent, outcome
                    )

                actions = actions_from_tool_results(tool_results)
                if actions:
                    yield {"type": "actions", "actions": actions}

                stage_start = time.perf_counter()
                assembled = deps.context.assemble(
                    ContextRequest(
                        system_prompt=await self._render_system_prompt(request, tool_declarations),
                        user_message=message,
                        history=history,
                        memories=memories,
                        goals=goals,
                        retrieval=retrieval,
                        cag_prefix=cag_prefix,
                        tool_results=tool_results,
                        scratchpad=execution_notes,
                        history_summary=history_summary,
                        context_window=self._context_window(),
                        max_output_tokens=request.max_output_tokens,
                        max_prompt_tokens=self._max_prompt_tokens(),
                        caller_context=caller_context,
                    )
                )

                timings["context"] = (time.perf_counter() - stage_start) * 1000.0
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

                provider = model = None
                ttft_ms: float | None = None
                stage_start = time.perf_counter()
                async for chunk in deps.router.stream(completion, budget=budget):
                    if chunk.usage is not None:
                        last_usage = chunk.usage
                    provider = chunk.provider or provider
                    model = chunk.model or model
                    if chunk.delta:
                        if ttft_ms is None:
                            ttft_ms = (time.perf_counter() - started) * 1000.0
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

                timings["generation"] = (time.perf_counter() - stage_start) * 1000.0
                guards_complete = False
                if deps.safety is not None:
                    stage_start = time.perf_counter()
                    try:
                        decision = await deps.safety.guard_output(
                            answer,
                            system_prompt=assembled.instructions_text,
                            citations_text=[
                                c.chunk.content for c in (retrieval.chunks if retrieval else ())
                            ],
                            user_id=request.user_id,
                            turn_id=turn_id,
                        )
                        guards_complete = True
                        guard_records.extend(decision.report.summary())
                        timings["guards_out"] = (time.perf_counter() - stage_start) * 1000.0
                        if decision.text != answer:
                            # The answer was redacted after streaming. Say so explicitly rather
                            # than leaving the client showing unredacted text.
                            yield {
                                "type": "revision",
                                "reason": "output_guard_redaction",
                                "text": decision.text,
                            }
                    except SafetyViolationError as exc:
                        self._error_result(
                            request, correlation, turn_id, started, exc, TurnOutcome.BLOCKED
                        )
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
                            user_message=self._history_message(request),
                            assistant_message=answer,
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
                await self._remember_session_classification(request, scope)
                prompt_tokens = last_usage.prompt_tokens if last_usage else assembled.prompt_tokens
                completion_tokens = (
                    last_usage.completion_tokens if last_usage else len(collected)
                )
                telemetry = TurnTelemetry(
                    correlation_id=correlation,
                    turn_id=turn_id,
                    session_id=request.session_id,
                    route=request.route,
                    agent=request.agent,
                    outcome=TurnOutcome.SUCCESS,
                    provider=provider,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=budget.cost_used_usd,
                    total_ms=(time.perf_counter() - started) * 1000.0,
                    ttft_ms=ttft_ms,
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
                    plan_steps=len(outcome.plan.steps) if outcome is not None else 0,
                    plan_revisions=len(outcome.plan.revisions) if outcome is not None else 0,
                    planning_failed=planning_failed,
                )
                await self._record(telemetry)
                degraded_reasons = list(telemetry.degraded_reasons)
                if searched_empty and "empty_retrieval" not in degraded_reasons:
                    degraded_reasons.append("empty_retrieval")
                if empty_generation_fallback:
                    degraded_reasons.append("empty_generation_fallback")
                yield {
                    "type": "done",
                    "tainted": tainted,
                    "guards_complete": guards_complete,
                    "citations": visible_citations,
                    "needs_confirmation": list(dict.fromkeys(needs_confirmation)),
                    "pending_tool_calls": pending_tool_calls,
                    "plan_summary": plan_summary,
                    "resolved_agent": resolved_agent,
                    "agent_run_id": getattr(outcome, "run_id", None),
                    "turn_id": turn_id.value,
                    "correlation_id": correlation.value,
                    # Stream does not run the reasoner; null is honest, not a fake score.
                    "confidence": None,
                    "degraded": bool(degraded_reasons),
                    # Bounded slugs (``planning_failed``, ``tool_failure``, ...), the same ones
                    # ``aura_turn_degraded_total`` is labelled by, so a client can say *why*.
                    "degraded_reasons": degraded_reasons,
                    "searched_but_empty": searched_empty,
                    "empty_generation_fallback": empty_generation_fallback,
                    "vision": perception.as_dict() if perception is not None else None,
                    "usage": {
                        "prompt_tokens": last_usage.prompt_tokens if last_usage else 0,
                        "completion_tokens": completion_tokens,
                        "cost_usd": round(budget.cost_used_usd, 6),
                    },
                }
            except AuraError as exc:
                self._error_result(
                    request, correlation, turn_id, started, exc, _stream_error_outcome(exc)
                )
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
                self._error_result(
                    request, correlation, turn_id, started,
                    AuraError("unhandled error during stream"), TurnOutcome.ERROR,
                )
                yield {"type": "error", "error": "internal_error",
                       "message": type(exc).__name__, "retryable": False}
            finally:
                if self._metrics is not None:
                    self._metrics.turns_in_flight.dec()

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
        # GAP-024: a persona reads its own memory scope plus the shared one. No registry means
        # no personas, so every scope (the pre-scope behaviour).
        spec = self._resolve_agent(request)
        scopes = readable_scopes(spec.memory_scope) if spec is not None else None

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
                        scopes=scopes,
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

    # -- data classification (ADR-030 Phase 4) ----------------------------------------------

    # -- images (ADR-031) -----------------------------------------------------------------

    async def _perceive_images(self, request: TurnRequest, message: str) -> tuple[TurnRequest, Any]:
        """Describe the turn's images and add the description to its caller evidence.

        Returns the request with ``caller_context`` extended, so the guard, the fence, the taint
        and the SENSITIVE baseline that already apply to caller evidence (AR-5) apply to what
        the images contain. ``message`` is the guarded user text, given to the vision model so
        its description focuses on what was asked.
        """
        if not request.images:
            return request, None
        vision = self._deps.vision
        if vision is None:
            raise DependencyUnavailableError(
                "image understanding is not configured on this deployment", dependency="vision"
            )
        perception = await vision.perceive(request.images, message)
        return replace(request, caller_context=request.caller_context + perception.entries), perception

    @staticmethod
    def _history_message(request: TurnRequest) -> str:
        """The user turn as session history keeps it: the text, plus a marker for images.

        Neither the bytes nor the description are kept. The description is DATA; writing it
        into history would launder it into the USER channel on the next turn.
        """
        if not request.images:
            return request.message
        count = len(request.images)
        return f"{request.message}\n[{count} image{'s' if count != 1 else ''} attached]"

    def _baseline_classification(self, request: TurnRequest) -> tuple[DataClassification, str]:
        """What a turn is before any context is gathered.

        An Engineer turn works on a repository checkout and a turn carrying caller evidence
        works on a calling system's data, so both start SENSITIVE. Everything else starts at
        INTERNAL, the user's own words, and is escalated by :meth:`_classify_context` and by
        tool output.
        """
        if self._workload_hint(request).workload is WorkloadClass.ENGINEER:
            return DataClassification.SENSITIVE, "engineer_workspace"
        if request.caller_context:
            return DataClassification.SENSITIVE, "caller_evidence"
        if request.images:
            return DataClassification.SENSITIVE, "image_input"
        return DataClassification.INTERNAL, ""

    async def _classify_context(
        self,
        request: TurnRequest,
        memories: Sequence[MemoryHit],
        retrieval: RetrievalResult | None,
        history: Conversation,
        goals: Sequence[GoalSnapshot],
        cag_prefix: Any,
    ) -> None:
        """Escalate the turn for every private source its context drew on.

        Deliberately coarse: any retrieved chunk counts, shared or owned, because "this
        document was safe to send" is not something the index records. An operator who wants
        documents to reach a hosted provider raises that provider's ceiling instead.
        """
        if memories:
            escalate(DataClassification.SENSITIVE, "memory")
        if retrieval is not None and retrieval.chunks:
            escalate(DataClassification.SENSITIVE, "documents")
        if cag_prefix:
            escalate(DataClassification.SENSITIVE, "pinned_documents")
        if goals:
            escalate(DataClassification.SENSITIVE, "goals")
        if len(history) and not await self._session_was_clean(request):
            # An earlier turn's answer may quote a document or a memory; the history carries it.
            escalate(DataClassification.SENSITIVE, "session_history")

    def _restricts_data(self) -> bool:
        return bool(getattr(self._deps.router, "restricts_data_classification", False))

    def _session_clean_key(self, request: TurnRequest) -> str | None:
        if request.session_id is None:
            return None
        return _SESSION_CLEAN_KEY.format(
            user=request.user_id.value, session=request.session_id.value
        )

    async def _session_was_clean(self, request: TurnRequest) -> bool:
        """Whether every earlier turn of this session stayed below SENSITIVE.

        Answers True without any I/O when no provider restricts content (every self-hosted
        deployment), because then the answer cannot change a routing decision. Otherwise a
        missing marker or an unreachable cache means False -- fail closed.
        """
        if not self._restricts_data():
            return True
        key = self._session_clean_key(request)
        cache = self._deps.session_marks
        if key is None or cache is None:
            return False
        try:
            return await cache.get(key) is not None
        except Exception:
            return False

    async def _remember_session_classification(
        self, request: TurnRequest, scope: DataScope
    ) -> None:
        """Mark the session clean after an INTERNAL turn, or unmark it after a SENSITIVE one."""
        if not self._restricts_data():
            return
        key = self._session_clean_key(request)
        cache = self._deps.session_marks
        if key is None or cache is None:
            return
        try:
            if scope.level.rank <= DataClassification.INTERNAL.rank:
                await cache.set(key, b"1", ttl_s=_SESSION_CLEAN_TTL_S)
            else:
                await cache.delete(key)
        except Exception:
            # A marker that failed to be written reads as "not clean" next turn: safe.
            logger.warning("session classification marker update failed")

    def _workload_hint(self, request: TurnRequest) -> WorkloadHint:
        """The ambient admission/routing hint for this turn's generations (P2.3/P2.4).

        Uses the *resolved* persona, so a caller who asked for ``engineer`` without the
        capability (and was silently given the orchestrator) is not queued as Engineer work.
        """
        spec = self._resolve_agent(request)
        return workload_for_turn(
            route=request.route,
            agent=spec.id if spec is not None else request.agent,
            user_key=request.user_id.value,
            workload=request.workload,
        )

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

    @staticmethod
    def _execution_notes(outcome: Any) -> list[str]:
        """State, on the SYSTEM channel, which planned steps produced nothing.

        Found in production. A user with a folder open in AURA Desktop asked the Engineer to
        summarise its README. The plan's read step named a tool that did not exist, the planner
        demoted it to a kind nothing can execute, it was SKIPPED, the summarise step that
        depended on it was BLOCKED, and `summary_for_context()` -- SUCCEEDED steps only --
        returned nothing. Generation therefore ran with a context in which the read had simply
        never been mentioned. The model wrote "the command has been executed, but since the
        output is not shown here, I will assume the file exists", and produced a summary of a
        generic README it had never seen, for a real file sitting open in the editor beside it.

        The three upstream causes are fixed where they live (`planning/engine.py` no longer
        demotes, `agentic_loop.py` now replans on a blocked step). This is the backstop for the
        general shape of the failure: *an absence cannot be reasoned about*. A model given no
        tool output cannot distinguish "the tool returned nothing" from "no tool ran", and the
        second is the one where inventing an answer looks locally reasonable. Naming the missing
        step converts the absence into a fact -- and the engineer persona already instructs the
        model to "say so plainly when a tool you would need is not available".

        SYSTEM channel, via `ContextRequest.scratchpad`, not `tool_results`: this is the
        kernel's own account of what it just ran, not content a tool returned. Putting it on
        DATA would fence it as attacker-influenced alongside real tool output, which both
        weakens the note and misrepresents where it came from (ADR-006).
        """
        plan = getattr(outcome, "plan", None)
        if plan is None:
            return []
        unexecuted = plan.unexecuted_steps()
        failed = plan.failed_steps
        if not unexecuted and not failed:
            return []

        # Tool steps all ran and returned the results above; what is left are non-tool steps
        # ("select one result", "quote the id") that no executor handles because the final answer
        # is where that work happens. Telling the model it "did NOT see" them makes it refuse
        # an answer it has every input for (seen live: "cannot provide an evidence_id" beside a
        # successful search). With no successful tool step this stays the strict warning below.
        tool_steps = [s for s in plan.steps if s.kind is StepKind.TOOL]
        if (
            not failed
            and tool_steps
            and all(s.status is StepStatus.SUCCEEDED for s in tool_steps)
            and all(s.kind is not StepKind.TOOL for s in unexecuted)
        ):
            lines = [
                "Execution note (from the system, not from a tool). The tool steps ran and their "
                "results are in the context above. These remaining steps have no separate "
                "executor; they are work for your answer, so do them yourself now from those "
                "tool results, and answer the user directly:"
            ]
            lines.extend(f"- {step.description}" for step in unexecuted)
            lines.append(
                "Answer in plain text now. Do not call any tool and do not write out or describe "
                "a tool call; a call you write here is not executed. Quote the values, such as "
                "identifiers and titles, exactly as they appear in the tool results."
            )
            return ["\n".join(lines)]

        lines = [
            "Execution note (from the system, not from a tool). These planned steps produced "
            "no output, so you have NOT seen their results:"
        ]
        for step in (*unexecuted, *failed):
            lines.append(f"- {step.description} [{step.status}]")
        lines.append(
            "Do not state or imply that you read, ran, or saw anything these steps would have "
            "produced, and do not substitute typical or expected content for it. Say plainly "
            "what you could not do."
        )
        return ["\n".join(lines)]

    @staticmethod
    def _delegation_grant(
        request: TurnRequest, agent: AgentSpec | None
    ) -> tuple[frozenset[str] | None, ToolSensitivity | None]:
        """The agent's own grant, narrowed by a `delegate_to_agent`-supplied override, if any.

        `None` for either half of the agent's own grant means "unrestricted by this side" and is
        preserved unless the override itself is also `None` for that half -- an override only
        ever narrows, never widens, regardless of what the agent's own `AgentSpec` allows. This
        is the defense-in-depth re-intersection described on `TurnRequest.delegated_tool_grant`:
        even if `delegate_to_agent`'s own intersection logic had a bug, a sub-turn still cannot
        exceed its resolved persona's declared grant.
        """
        base_tools = agent.allowed_tools if agent is not None else None
        base_ceiling = agent.risk_ceiling if agent is not None else None
        if request.delegated_tool_grant is None:
            effective_tools = base_tools
        elif base_tools is None:
            effective_tools = request.delegated_tool_grant
        else:
            effective_tools = base_tools & request.delegated_tool_grant
        if request.delegated_risk_ceiling is None:
            effective_ceiling = base_ceiling
        elif base_ceiling is None:
            effective_ceiling = request.delegated_risk_ceiling
        else:
            effective_ceiling = min(base_ceiling, request.delegated_risk_ceiling)
        return effective_tools, effective_ceiling

    #: Durable-workspace tools (ADR-013, GitHub-repo-scoped) that make no sense once this user's
    #: turn is running against a paired desktop session (ADR-011, a real local folder): the model
    #: has nothing repository-shaped to give `open_workspace`'s required `repo` argument, and the
    #: folder it already has needs no explicit "open" step at all.
    _DESKTOP_ONLY_UNAVAILABLE = frozenset({"open_workspace", "list_workspaces", "destroy_workspace"})

    async def _desktop_active(self, user_id: Any) -> bool:
        registry = self._deps.host_agent_registry
        if registry is None or user_id is None:
            return False
        return await registry.resolve(user_id) is not None

    async def _tool_declarations(self, request: TurnRequest) -> tuple[Mapping[str, Any], ...]:
        deps = self._deps
        if not (request.use_tools and deps.tools is not None):
            return ()
        agent = self._resolve_agent(request)
        allowed_tools, max_sensitivity = self._delegation_grant(request, agent)
        declarations = deps.tools.declarations(
            capabilities=request.capabilities,
            max_sensitivity=max_sensitivity if max_sensitivity is not None else ToolSensitivity.SYSTEM,
        )
        if await self._desktop_active(getattr(request, "user_id", None)):
            declarations = tuple(
                d for d in declarations if d.get("name") not in self._DESKTOP_ONLY_UNAVAILABLE
            )
            if allowed_tools is not None:
                allowed_tools = allowed_tools - self._DESKTOP_ONLY_UNAVAILABLE
        if allowed_tools is None:
            return declarations
        # Not advertised, not requested: a tool outside the agent's grant should not appear in
        # the model's tool list, same reasoning as ToolRegistry.available()'s own capability
        # filter -- offering it only produces a call that policy.evaluate() then refuses.
        return tuple(d for d in declarations if d.get("name") in allowed_tools)

    def _should_plan(self, request: TurnRequest, tools: Sequence[Mapping[str, Any]]) -> bool:
        deps = self._deps
        if deps.planner is None or deps.executor is None or not tools:
            return False
        force = request.plan
        # A resumed confirmation never depends on this: it executes its stored plan whatever
        # the heuristics below would say (`_run_turn`/`stream`).
        if force is None:
            agent = self._resolve_agent(request)
            if agent is not None and agent.max_plan_iterations > 1:
                # An agentic persona (today: `engineer`) exists to do multi-step tool work, and
                # planning is the only path that runs a tool at all. Gating it behind heuristics
                # tuned for conversational personas is what makes such a persona answer "I can't
                # do that" while holding the exact tool for the job. The cost is one planning
                # generation on a turn that may not need it; a plan that comes back as a single
                # RESPOND step still falls through to the direct answer (`_compose_plan`).
                force = True
        return deps.planner.needs_plan(
            request.message, available_tools=[t.get("name", "") for t in tools], force=force
        )

    def _tool_context(self, request: TurnRequest, tainted: bool) -> ToolContext:
        agent = self._resolve_agent(request)
        allowed_tools, max_sensitivity = self._delegation_grant(request, agent)
        deadline_s = 60.0
        if agent is not None and (agent.allowed_tools is None or "delegate_to_agent" in agent.allowed_tools):
            # A turn whose persona can delegate may itself be dispatching a recursive kernel.run()
            # (aura_core/tools/delegation.py) rather than a simple API call -- the flat 60s every
            # other tool gets would truncate that sub-turn well before its own budgets do.
            deadline_s = getattr(
                getattr(self._deps.settings, "reasoning", None), "delegation_deadline_s", 180.0
            )
        return ToolContext(
            user_id=request.user_id,
            correlation_id=(request.correlation_id or CorrelationId.new()).value,
            deadline_s=deadline_s,
            tainted=tainted,
            agent_id=agent.id if agent is not None else None,
            allowed_tools=allowed_tools,
            max_sensitivity=max_sensitivity,
            memory_scope=agent.memory_scope if agent is not None else "shared",
            capabilities=request.capabilities,
            delegation_depth=request.delegation_depth,
            credentials={connector_token_key(c): token for c, token in (request.connector_tokens or {}).items()},
            # D8: carried via the existing free-form `metadata` field rather than a new
            # `ToolContext` slot -- see `ports/runtime.py::ToolContext`'s own doc comment on why
            # that field exists (policy inputs only get dedicated slots; this is caller data
            # specific to one tool family's needs). `session_id` is `None` for any turn that
            # doesn't carry one (e.g. a request with no `session_id`), which
            # `_SandboxSessionCache` treats as "isolation not possible, ignore the flag."
            metadata={
                "session_id": str(request.session_id) if request.session_id else None,
                "desktop_task_isolation": request.desktop_task_isolation,
                "workspace_root": request.workspace_root,
            },
        )

    async def _compose_plan(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> Any:
        deps = self._deps
        # A planner failure propagates: the caller answers directly *and* marks the turn
        # degraded (CB-14). Swallowing it here made "no plan was needed" and "planning broke"
        # the same ``None``, so the turn reported plain success either way.
        goal = deps.planner.goal_from_message(message)
        plan = await deps.planner.plan(goal, available_tools=tools)
        if plan.fallback_reason:
            # The planner already answered "respond directly" for us, but only because its own
            # generation failed (a timeout, on the CPU host). That is a degraded turn, not a
            # turn that needed no tools.
            raise PlanningError(
                "the planner could not plan this turn",
                details={"reason": plan.fallback_reason},
            )
        if not plan.steps:
            return None
        if len(plan.steps) <= 1 and plan.steps[0].kind is StepKind.RESPOND:
            # A one-step "just answer" plan is what the direct path already does; running the
            # executor for it costs latency and buys nothing.
            return None
        return plan

    def _agentic_loop_deadline_s(self) -> float:
        return float(
            getattr(getattr(self._deps.settings, "reasoning", None), "engineer_loop_deadline_s", 600.0)
        )

    def _loop_arguments(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
        tainted: bool,
        turn_id: TurnId | None,
        correlation: CorrelationId | None,
    ) -> dict[str, Any] | None:
        """How this turn runs through the standard agent loop (GAP-024), or ``None`` for the
        pre-registry direct path (no ``AgentRegistry`` or no ``agentic_executor`` wired).

        Every persona goes through the one loop; its ``AgentSpec.budget`` decides how far.
        ``engineer`` (``max_plan_iterations=8``) gets exactly the replan limit and deadline it
        always had; a single-pass persona gets ``max_iterations=0`` -- one pass, as before.
        """
        deps = self._deps
        agent = self._resolve_agent(request)
        if agent is None or deps.agentic_executor is None:
            return None
        budget = agent.budget
        return {
            "max_iterations": budget.max_iterations,
            "deadline_s": self._agentic_loop_deadline_s(),
            "budget": budget,
            "run_context": AgentRunContext(
                user_id=request.user_id,
                agent_id=agent.id,
                turn_id=(turn_id or TurnId.new()).value,
                correlation_id=(correlation or request.correlation_id or CorrelationId.new()).value,
                goal=message,
                tools_available=len(tools),
                tainted=tainted,
            ),
        }

    async def _plan_and_execute(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
        tainted: bool,
        *,
        turn_id: TurnId | None = None,
        correlation: CorrelationId | None = None,
        resume: ResumedTurn | None = None,
    ) -> Any:
        """The execution outcome, or ``None`` when the turn needs no plan. Raises when planning
        or execution fails; ``_run_turn`` turns that into a degraded direct answer."""
        deps = self._deps
        plan, confirmations = await self._plan_for(request, message, tools, resume)
        if plan is None:
            return None
        context = self._tool_context(request, tainted)
        loop = self._loop_arguments(request, message, tools, tainted, turn_id, correlation)
        if loop is not None:
            return await deps.agentic_executor.run(
                plan,
                context=context,
                capabilities=request.capabilities,
                confirmations=confirmations,
                available_tools=tools,
                **loop,
            )
        return await deps.executor.execute(
            plan,
            context=context,
            capabilities=request.capabilities,
            confirmations=confirmations,
        )

    async def _iter_execution(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
        tainted: bool,
        *,
        turn_id: TurnId | None = None,
        correlation: CorrelationId | None = None,
        resume: ResumedTurn | None = None,
    ) -> AsyncIterator[StepEvent | ExecutionOutcome]:
        """Yield step progress, then the outcome. Prefer ``executor.stream``."""
        deps = self._deps
        plan, confirmations = await self._plan_for(request, message, tools, resume)
        if plan is None:
            return
        context = self._tool_context(request, tainted)
        loop = self._loop_arguments(request, message, tools, tainted, turn_id, correlation)
        if loop is not None:
            async for item in deps.agentic_executor.stream(
                plan,
                context=context,
                capabilities=request.capabilities,
                confirmations=confirmations,
                available_tools=tools,
                **loop,
            ):
                yield item
            return
        stream = getattr(deps.executor, "stream", None)
        if callable(stream):
            async for item in stream(
                plan,
                context=context,
                capabilities=request.capabilities,
                confirmations=confirmations,
            ):
                yield item
            return
        yield await deps.executor.execute(
            plan,
            context=context,
            capabilities=request.capabilities,
            confirmations=confirmations,
        )

    async def _plan_for(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
        resume: ResumedTurn | None,
    ) -> tuple[Any, Any]:
        """The plan to execute, and the confirmations it may use.

        A resumed turn executes the plan stored when it was held -- never a fresh one: a re-plan
        is not deterministic and can be steered, so it could produce a different call from the
        one the user approved (F-04). Only a resumed turn carries a grant.
        """
        if resume is not None:
            return resume.plan, resume.grant
        return await self._compose_plan(request, message, tools), None

    # -- confirmations (ADR-006, F-04) --------------------------------------------------

    async def _claim_confirmation(self, request: TurnRequest) -> ResumedTurn | None:
        """Consume the pending turn ``request.approval_ids`` approve; ``None`` without ids.

        Raises :class:`~aura_core.domain.errors.ConfirmationInvalidError` when the ids do not
        name a pending turn this caller may resume -- and then nothing runs.
        """
        if not request.approval_ids:
            return None
        resume = await self._confirmations.claim(
            request.approval_ids,
            user_id=request.user_id.value,
            session_id=request.session_id.value if request.session_id else None,
        )
        logger.info(
            "resuming a confirmed turn",
            extra={"origin_turn_id": resume.origin_turn_id,
                   "approved_calls": len(resume.approved_call_ids)},
        )
        return resume

    @staticmethod
    def _resumed_request(request: TurnRequest, resume: ResumedTurn) -> TurnRequest:
        """The held turn's own message and persona, not whatever the resuming request carried:
        the plan, its grants and the answer all belong to the turn the user approved."""
        return replace(request, message=resume.message or request.message, agent=resume.agent)

    async def _hold_for_confirmation(
        self, request: TurnRequest, turn_id: TurnId, outcome: Any
    ) -> list[Mapping[str, Any]]:
        """Store this turn's confirm-gated calls; describe each, with its approval id."""
        reports = [r for r in outcome.tool_reports if r.needs_confirmation]
        if not reports:
            return []
        approval_ids: Mapping[str, str] = {}
        calls = list(getattr(outcome, "pending_calls", ()) or ())
        if calls:
            try:
                approval_ids = await self._confirmations.issue(
                    user_id=request.user_id.value,
                    session_id=request.session_id.value if request.session_id else None,
                    agent=request.agent,
                    message=request.message,
                    plan=outcome.plan,
                    tainted=bool(getattr(outcome, "tainted", False)),
                    calls=calls,
                    turn_id=turn_id.value,
                )
            except Exception:
                # Fails closed: with no stored pending turn there is nothing to approve, so the
                # held calls stay held rather than becoming confirmable some looser way.
                logger.warning("could not store pending confirmations; they cannot be approved")
        pending: list[Mapping[str, Any]] = []
        for report in reports:
            entry: dict[str, Any] = {
                "call_id": report.call_id,
                "tool": report.tool,
                "arguments": dict(report.arguments or {}),
                "diff_preview": report.diff_preview,
            }
            approval_id = approval_ids.get(report.call_id)
            if approval_id:
                entry["approval_id"] = approval_id
            pending.append(entry)
        return pending

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
    def _with_schema_instruction(message: str, schema: Mapping[str, Any] | None) -> str:
        """The user turn plus the output contract (AR-6). The schema is the caller's own input,
        so it rides on the caller's channel, USER -- never SYSTEM."""
        if schema is None:
            return message
        rendered = json.dumps(schema, sort_keys=True, separators=(",", ":"))
        return (f"{message}\n\nReply with only one JSON value that conforms to this JSON Schema -- "
                f"no prose, no code fence:\n{rendered}")

    def _json_mode_hint(self, schema: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        """The schema as a provider JSON-mode request, only if some provider can honour it.

        On a ``CompletionRequest`` a schema is a hard routing requirement (``no_json_mode``
        excludes a provider). AURA validates the answer itself either way, so a deployment whose
        model has no JSON mode still gets structured output -- prompt-led, then checked --
        rather than every such turn failing as ``all_providers_failed``.
        """
        if schema is None:
            return None
        router = self._deps.router
        for name in router.provider_names:
            provider = router.get(name)
            caps = getattr(provider, "capabilities", None)
            if caps is not None and caps.supports_json_mode:
                return schema
        return None

    async def _enforce_response_schema(
        self, request: TurnRequest, assembled: AssembledContext, answer: str, budget: TurnBudget,
        turn_id: TurnId,
    ) -> tuple[str, Any, TokenUsage | None, float]:
        """Validate the answer; on failure repair once, then fail closed (AR-6).

        The repair sees only error *paths* (``output_errors`` never echoes values) and its
        answer goes through the same output guards. It is skipped when the turn's generation
        or cost budget is spent -- the caller's ceiling is never exceeded to rescue a format.
        Raises :class:`OutputInvalidError`; unvalidated JSON is never returned as data.
        """
        schema = request.response_schema
        value, errors = parse_structured(answer, schema)
        if not errors:
            return answer, value, None, 0.0
        affordable = budget.remaining_generations > 0 and not (
            budget.max_cost_usd and budget.cost_used_usd >= budget.max_cost_usd
        )
        if not affordable:
            raise OutputInvalidError("the answer did not match the requested schema",
                                     details={"errors": errors, "repaired": False})
        deps = self._deps
        repair = CompletionRequest(
            conversation=assembled.conversation.append(
                Message.assistant(answer),
                Message.user("That reply did not match the schema at: " + "; ".join(errors)
                             + ". Reply again with only the corrected JSON value."),
            ),
            model=await self._assigned_model(request),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=request.max_output_tokens),
            policy=request.routing_policy,
            require_local=request.require_local,
            tools=(),
            timeout_s=self._completion_timeout_s(),
            response_schema=self._json_mode_hint(schema),
        )
        response = await deps.router.complete(repair, budget=budget)
        repaired = response.text
        if deps.safety is not None:
            decision = await deps.safety.guard_output(
                repaired, system_prompt=assembled.instructions_text, citations_text=[],
                user_id=request.user_id, turn_id=turn_id,
            )
            repaired = decision.text
        value, errors = parse_structured(repaired, schema)
        if errors:
            raise OutputInvalidError("the answer did not match the requested schema",
                                     details={"errors": errors, "repaired": True})
        return repaired, value, response.usage or TokenUsage(), response.cost_usd

    async def _guard_caller_context(
        self, request: TurnRequest, turn_id: TurnId
    ) -> tuple[tuple[CallerContext, ...], int]:
        """Caller-supplied evidence through the same context guards as retrieved chunks (AR-5).

        Returns the entries that passed and how many were quarantined. Whatever this returns,
        the caller taints the turn when any context was *sent*: a side effect must never be
        unlocked by evidence a calling system put in front of the model.
        """
        entries = request.caller_context
        if not entries or self._deps.safety is None:
            return entries, 0
        accepted, quarantined, _ = await self._deps.safety.guard_context_items(
            [(e.id, e.content()) for e in entries], user_id=request.user_id, turn_id=turn_id
        )
        kept = {provenance for provenance, _ in accepted}
        return tuple(e for e in entries if e.id in kept), len(quarantined)

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

    async def _render_system_prompt(
        self, request: TurnRequest, tools: Sequence[Mapping[str, Any]]
    ) -> str:
        agent = self._resolve_agent(request)
        template_id = (
            agent.prompt_template_id if agent is not None
            else AGENT_PROMPT_IDS.get(request.agent, AGENT_PROMPT_IDS["orchestrator"])
        )
        clock = self._deps.clock
        now = clock.now() if clock is not None else datetime.now(timezone.utc)
        # Only `system.engineer` declares these two, so passing them for every persona is
        # harmless (`PromptTemplate.render` ignores supplied keys a template didn't declare) --
        # see that template's own two-branch body for why the distinction matters: a desktop
        # session and a Docker-only sandbox need different instructions for reading a file and
        # for whether `open_workspace` exists at all.
        desktop_active = await self._desktop_active(getattr(request, "user_id", None))
        try:
            rendered = self._deps.prompts.render(
                template_id,
                {
                    # Rendered by the context engine, which owns the per-turn nonce; the prompt
                    # only needs a slot for it.
                    "fence_rules": "",
                    "current_time": now.isoformat(timespec="minutes"),
                    "capabilities": self._format_tool_capabilities(tools),
                    "desktop_paired": "true" if desktop_active else "",
                    "sandbox_mode": "" if desktop_active else "true",
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

        ``CompletionRequest.timeout_s`` used to default to 60s while the CPU Ollama stack is
        configured for 180s (``AURA_PROVIDER_LOCAL_TIMEOUT_S``). The router wraps the first
        streamed chunk in ``asyncio.wait_for(..., timeout=request.timeout_s)``, so the default
        killed a cold 3B prefill (``all 1 provider attempt(s) failed before streaming began``).
        The dataclass now defaults to ``None`` -- the router's configured timeout -- and this
        stays explicit so a turn's deadline is visible where the turn is built.
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

        Older registry rows hold ``scripted://...`` placeholders, and ``LocalLoraTrainer``
        registers a PEFT adapter *directory* (not merged, not an Ollama model). Sending either
        as ``CompletionRequest.model`` makes Ollama 404 before the first token.
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
        # An image turn's answer rests on pixels the cache key never sees: the semantic tier
        # matches on the user's text alone, so "what is in this picture?" would be answered from
        # a different picture (ADR-031). Such turns neither read nor write the cache.
        cache = None if request.images else deps.cache

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
            response_schema=self._json_mode_hint(request.response_schema),
        )

        if cache is not None:
            hit = await cache.lookup(completion, namespace=namespace)
            if hit is not None:
                return (
                    hit.response.text, hit.response.provider, hit.response.model,
                    hit.response.usage, 0.0, True, hit.tier, 0,
                    ReasoningStrategy.DIRECT, None,
                )

        strength = retrieval.top_score if retrieval and retrieval.chunks else None

        # Multi-sample strategies vote over prose; a structured answer is one direct generation.
        if deps.reasoner is not None and request.response_schema is None:
            result = await deps.reasoner.reason(
                ReasoningRequest(
                    question=request.message,
                    conversation=assembled.conversation,
                    citations=[c.chunk.content for c in (retrieval.chunks if retrieval else ())],
                    retrieval_strength=strength,
                    strategy=request.strategy,
                    max_output_tokens=request.max_output_tokens,
                    require_local=request.require_local,
                )
            )
            confidence = result.confidence.score if result.confidence else None
            if cache is not None:
                await cache.store(
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

        if cache is not None:
            await cache.store(completion, response, namespace=namespace)

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
        return TurnResult(
            text="", telemetry=telemetry, retry_after_s=getattr(exc, "retry_after", None),
            error_details=dict(exc.details) if isinstance(exc, OutputInvalidError) else None,
        )

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
