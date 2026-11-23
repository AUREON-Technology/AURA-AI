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
    ConsentRequiredError,
    DependencyUnavailableError,
    OutputInvalidError,
    OverloadedError,
    PlanningError,
    SafetyViolationError,
    TurnStoppedError,
    UserConcurrencyLimitError,
    UserQuotaExhaustedError,
    ValidationError,
)
from ..domain.events import TurnCompleted, TurnFailed, TurnStarted
from ..domain.caller_context import CallerContext
from ..domain.images import ImageInput
from ..domain.connectors import Credential, connector_token_key
from ..domain.context import user_facing_citations
from ..domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION, EngineerRun, EngineerRunStatus
from ..domain.identifiers import CorrelationId, EngineerRunId, SessionId, TurnId, UserId
from ..domain.knowledge import RetrievalResult, RetrievedChunk
from ..domain.lessons import LessonStep, LessonTurn
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
from ..domain.tools import ToolSensitivity, intersect_tool_grants, tool_grant_permits
from ..domain.telemetry import (
    CacheOutcome,
    RetrievalTelemetry,
    StageTiming,
    ToolTelemetry,
    TurnOutcome,
    TurnTelemetry,
)
from ..execution.acceptance import AcceptanceReport, check_acceptance
from ..execution.agentic_loop import AgentRunContext
from ..execution.confirmations import PendingCall, PendingConfirmationStore, ResumedTurn
from ..execution.critic import critique_answer
from ..execution.engine import ExecutionOutcome, StepEvent
from ..execution.loop_events import LoopEvent
from ..execution.runner_output import observe_plan
from ..llm.cache import InMemoryCache
from ..llm.complexity import assess_complexity
from ..llm.deadline import generation_budget, no_generation_budget
from ..llm.router import TurnBudget, current_turn_budget, no_turn_budget, turn_budget_scope
from ..llm.sensitivity import DataScope, data_scope, escalate
from ..llm.workload import effective_workload, workload_for_turn, workload_scope
from ..observability.logging import LogContext, get_logger
from ..observability.tracing import record_span
from ..ports.runtime import ToolContext
from ..prompts.language import reply_language
from ..prompts.library import AGENT_PROMPT_IDS
from ..agents.registry import AgentRegistry, AgentSpec
from ..planning.feedback import PlanFeedbackService
from ..planning.modes import PlanDecision, decide_plan, normalise_mode
from ..reasoning.engine import ReasoningRequest
from ..safety.guards import redact_secrets
from ..tools.personal_os import actions_from_tool_results
from .active_turns import ActiveTurns

__all__ = ["AIKernel", "TurnRequest", "TurnResult", "KernelDependencies"]

logger = get_logger("kernel")

# Matches `tools/coding.py::_ENGINEER_CAPABILITY` -- not imported directly since `kernel/` sits
# above `tools/` in the layering and must not import a concrete tool module.
_ENGINEER_AGENT_CAPABILITY = "engineer_agent"
_URL_RE = re.compile(r"https?://\S+")
#: One line of a replanned goal's "Progress so far" block (`PlanningEngine.replan`).
_FAILED_LINE = re.compile(r"^Failed: (.{1,200}?) \(([a-z_]{1,48})\)\s*$", re.M)
# `github_create_branch`'s result text ends "... (<sha[:7]>)" -- best-effort extraction, not a
# guarantee every tool's prose embeds a SHA in this exact shape.
_SHA_RE = re.compile(r"\(([0-9a-f]{7,40})\)\s*$")
#: ADR-030 Phase 4: a session is "clean" while none of its turns was SENSITIVE. Stored as a
#: positive marker so that losing it (eviction, a Redis restart) fails *closed*: a session whose
#: marker is missing but which has history is treated as having been sensitive.
_SESSION_CLEAN_KEY = "llm:session-clean:v1:{user}:{session}"
#: Written by every SENSITIVE turn and never removed by a clean one (F32). A session reads as
#: clean only while the clean mark is present *and* this one is absent, so two overlapping turns
#: cannot leave it clean in whichever order their writes land. Same lifetime as the clean mark:
#: it can never expire while a clean mark written alongside it survives.
_SESSION_SENSITIVE_KEY = "llm:session-sensitive:v1:{user}:{session}"
_SESSION_CLEAN_TTL_S = 30 * 86400


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """One turn's input."""

    message: str
    user_id: UserId
    session_id: SessionId | None = None
    #: Owner for shareable data -- knowledge retrieval, CAG and the knowledge tools (ADR-034):
    #: the team's ``workspace:<id>`` scope inside a team workspace. ``None`` means ``user_id``.
    #: Memory, history, goals and Personal-OS always use ``user_id``.
    knowledge_owner: UserId | None = None
    agent: str = "orchestrator"
    correlation_id: CorrelationId | None = None
    use_knowledge: bool = True
    use_memory: bool = True
    use_tools: bool = True
    plan: bool | None = None
    #: ADR-038 (row 63): ``auto`` | ``plan`` | ``fast`` | ``review`` (``planning/modes.py``). Wins
    #: over ``plan``; both ``None`` is today's behaviour. ``review`` plans and then holds the plan
    #: for the user's approval instead of executing it.
    plan_mode: str | None = None
    #: ADR-045 (row 41): acceptance criteria the caller wants the run checked against (at most
    #: 20 of 300 characters, ``execution/acceptance.py``). Empty is today's behaviour exactly.
    #: With criteria the planner is told them, and ``agent_loop.acceptance`` reports
    #: ``{status, met, unmet, unverified}`` read from the run's own tool results.
    success_criteria: tuple[str, ...] = ()
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
    #: The prefix half of that same intersected grant (audit T-03: the engineer's ``mcp_``).
    #: Meaningless when ``delegated_tool_grant`` is ``None``; otherwise empty means "names only",
    #: so a names-only override strips any prefix the resolved persona itself holds.
    delegated_tool_prefixes: frozenset[str] = frozenset()
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
    #: Where the person driving this turn is (module readiness WP8, D-10): ``"desktop"`` for the
    #: desktop app (its access token carries the server-signed ``platform: desktop`` claim),
    #: ``"remote"`` for phone and web. ``None`` (internal callers, evals) behaves as desktop. A
    #: remote turn's work on the paired desktop is always held for confirmation, and the daemon
    #: refuses it unless remote control was switched on at the computer itself.
    origin: str | None = None
    #: Admission class for every generation this turn makes (roadmap P2.4). ``None`` derives it
    #: from ``route``/``agent`` (``llm/workload.py``): Engineer -> coder pool at priority 2,
    #: voice and chat -> general-fast at priority 1. Background callers set it explicitly.
    workload: WorkloadClass | None = None
    #: Evidence the calling system supplied for this turn (AR-5). DATA channel only: guarded
    #: like retrieved chunks, fenced, and its presence taints the turn.
    caller_context: tuple[CallerContext, ...] = ()
    #: A structured agent handoff (ADR-038, ``agents/handoff.py``), set only by
    #: ``delegate_to_agent`` on the child turn it builds. Rides the DATA channel like
    #: ``caller_context`` -- guarded, fenced with the turn's nonce, dropped if quarantined -- but
    #: does *not* taint the turn merely by being present (the child already inherits its parent's
    #: taint through ``inherited_taint``); a block the guards quarantine does taint it. Never
    #: reachable from the HTTP API: ``to_turn`` does not set it.
    handoff_context: tuple[CallerContext, ...] = ()
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
    #: The language the app is displaying (``"ar"``/``"en"``), from ``X-Client-Language``. Only a
    #: fallback: the reply mirrors the language the message is written in, and this decides only
    #: when the message has no clear language (a link, a number, code). ``None`` -- an old app,
    #: a service key -- leaves such a message to the model, as before.
    preferred_language: str | None = None
    #: The agent task (``aura_core/tasks``, ADR-041) this turn runs for, set only by the task
    #: queue. It changes nothing about how the turn runs; it is carried onto the turn's usage
    #: record so spend can be grouped by task.
    task_id: str | None = None


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
    #: The agent loop's account of itself (ADR-037, ``execution/loop_events.py::LoopReport``): why
    #: it stopped, the states it visited, any stuck verdict and escalation, the critic's findings.
    #: ``None`` for a turn that ran no loop.
    agent_loop: Mapping[str, Any] | None = None
    #: How planning was decided for this turn (``PlanDecision.as_dict``): ``requested``,
    #: ``effective`` (``default`` | ``auto`` | ``plan`` | ``fast`` | ``review`` | ``resumed``),
    #: ``reason``, ``planned``, and ``awaiting_approval`` for a ``review`` turn that stopped at
    #: the gate. ``None`` only for a turn that failed before planning was considered.
    plan_mode: Mapping[str, Any] | None = None

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
    #: `EngineerConsentStore` (`ports/coding.py`, D-03). When wired, an Engineer turn from someone
    #: without a current consent is refused before anything is planned or sent anywhere. `None`
    #: (a bare test kernel) checks nothing -- the container always wires it.
    engineer_consent: Any = None
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
    #: ADR-038 (row 60): ``async (LessonTurn) -> Any``, called in the background after an Engineer
    #: turn completes so lessons can be derived and queued for the owner's review. ``None`` -- the
    #: default, and what the container wires unless ``AURA_AUTO_LESSONS_ENABLED`` -- does nothing.
    lesson_sink: Any = None
    #: `AutonomyStore` (`tools/autonomy.py`, ADR-039): the user's review profile, read once per
    #: turn into `ToolContext.autonomy`. `None` means every turn runs as `standard`.
    autonomy: Any = None
    #: `UsageLedger` (`history/usage.py`, ADR-041): one compact per-user record per finished turn,
    #: for the usage API. `None` records nothing. Writing never fails a turn.
    usage_ledger: Any = None
    #: `RunSearchIndex` (`history/run_search.py`, ADR-041): keeps the opt-in redacted goal preview
    #: of an agent run (`AURA_AGENT_RUNS_STORE_PREVIEW`). `None`, or the setting off, stores nothing.
    run_index: Any = None
    #: The audit sink (ADR-045): ``plan.feedback`` is recorded when a person revises a held plan.
    #: `None` records nothing.
    audit: Any = None


#: Told to the model when planning or tool execution failed (CB-14). Without it the model saw
#: only the user's request and would answer "Done, I created the task" when nothing had run.
PLANNING_FAILED_NOTE = (
    "Tool planning failed for this turn, so no tool ran and no action was taken. Do not say "
    "that anything was created, changed, sent or scheduled; tell the user it could not be done "
    "right now and that they can try again."
)


#: Told to the model when a ``review``-mode turn stopped at the approval gate (ADR-038).
PLAN_REVIEW_NOTE = (
    "Plan review: a plan was prepared for this request and is waiting for the user's approval. No "
    "step has run, so nothing was created, changed, sent, scheduled or read by a tool. Describe "
    "the planned steps briefly in plain language, tell the user they can approve or decline them, "
    "and do not say or imply that any of it has been done."
)


@dataclass(frozen=True, slots=True)
class _ReviewHold:
    """The outcome of composing a plan in ``review`` mode.

    ``held`` is true when the plan has tool steps and was stored for approval. ``plan`` is the
    composed plan (``None`` when the planner decided no plan was needed); a plan with no tool step
    has nothing to approve, so it is not held and runs as usual.
    """

    plan: Any = None
    held: bool = False
    summary: tuple[Mapping[str, Any], ...] = ()
    pending: tuple[Mapping[str, Any], ...] = ()


def _consume_shadow_result(task: "asyncio.Task[None]") -> None:
    """Retrieve a finished shadow task's exception so asyncio never logs it as unhandled.

    `_run_shadow` already records its own failures; anything reaching here escaped that, and is
    logged at debug for the same reason: shadow traffic must never be noisy on the user path.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug("shadow task failed", exc_info=exc)


def _clear_cancellation() -> None:
    """Swallow a cancellation this turn was *asked* to take (the kill switch), so that what it
    awaits next -- the response write -- is not cancelled again. ``Task.uncancel`` is 3.11+."""
    task = asyncio.current_task()
    uncancel = getattr(task, "uncancel", None)
    if uncancel is not None:
        while task.cancelling() > 0:
            uncancel()


def _stream_error_outcome(exc: AuraError) -> TurnOutcome:
    """The telemetry outcome for an error ending a streamed turn, as ``run`` maps them."""
    if isinstance(exc, (UserConcurrencyLimitError, UserQuotaExhaustedError)):
        return TurnOutcome.RATE_LIMITED
    if isinstance(exc, SafetyViolationError):
        return TurnOutcome.BLOCKED
    if isinstance(exc, BudgetExceededError):
        return TurnOutcome.BUDGET_EXCEEDED
    return TurnOutcome.ERROR


def _trace_turn(telemetry: TurnTelemetry) -> None:
    """One ``aura.turn`` span per finished turn (Phase R2), built from its telemetry record.

    Bounded labels, durations and counts only. No session, user or conversation id, no prompt
    or answer text: spans leave for a third-party collector, and the per-user detail stays in
    AURA's own telemetry store. The stage durations are attributes rather than child spans:
    the kernel measures them as values, not as nested blocks.
    """
    t = telemetry.timings
    record_span(
        "aura.turn",
        duration_ms=telemetry.total_ms,
        attributes={
            "aura.route": telemetry.route,
            "aura.agent": telemetry.agent,
            "aura.strategy": telemetry.strategy,
            "aura.outcome": str(telemetry.outcome),
            "aura.error_kind": telemetry.error_kind,
            "aura.provider": telemetry.provider,
            "aura.model": telemetry.model,
            "aura.fallback_count": telemetry.fallback_count,
            "aura.prompt_tokens": telemetry.prompt_tokens,
            "aura.completion_tokens": telemetry.completion_tokens,
            "aura.ttft_ms": None if telemetry.ttft_ms is None else round(telemetry.ttft_ms, 1),
            "aura.degraded": telemetry.degraded,
            "aura.tainted": telemetry.tainted,
            "aura.retrieval.strategy": telemetry.retrieval.strategy,
            "aura.retrieval.returned": telemetry.retrieval.returned,
            **{f"aura.stage.{name}_ms": round(getattr(t, name), 1) for name in _TRACED_STAGES},
        },
        error=telemetry.error_kind is not None,
    )


#: The `StageTiming` fields a turn span reports.
_TRACED_STAGES = (
    "guards_in", "memory", "retrieval", "context", "planning", "reasoning", "tools",
    "generation", "guards_out",
)


def _vision_step_outcome(perception: Any) -> dict[str, str]:
    """Status and wording for the finished vision step (ADR-031).

    ``done`` when a vision model described the images. ``degraded`` when only OCR read them:
    the usual cause is a vision server that sleeps when idle and is still waking. The step says
    so, so the user knows to send the image again rather than trust a text-only reading.
    ``degraded`` is a status value added for this; a client that does not know it still shows
    the description.
    """
    if getattr(perception, "mode", None) == "ocr_fallback":
        return {
            "status": "degraded",
            "description": (
                "Only the text in the image(s) could be read: the vision model is unavailable "
                "or still starting. Send it again in a minute for a full look."
            ),
        }
    return {"status": "done", "description": "Looked at the image(s)"}


class AIKernel:
    """Runs one turn through the pipeline."""

    def __init__(self, deps: KernelDependencies) -> None:
        self._deps = deps
        self._metrics = deps.metrics
        self._confirmations = deps.tool_confirmations or PendingConfirmationStore(
            InMemoryCache(max_entries=1024, default_ttl_s=3600)
        )
        #: The one shadow generation allowed in flight (see `_dispatch_shadow`). Holding the
        #: reference also keeps the task from being garbage-collected mid-run.
        self._shadow_task: asyncio.Task[None] | None = None
        #: Lesson-extraction tasks in flight (strong references; see `_dispatch_lessons`).
        self._lesson_tasks: set[asyncio.Task[None]] = set()
        #: The turns each user has running here, so ``POST /agents/stop-all`` can cancel them
        #: (ADR-039). Process-local, like the host-agent registry without its relay.
        self.active_turns = ActiveTurns()

    # -- public API --------------------------------------------------------------------

    async def run(self, request: TurnRequest) -> TurnResult:
        """Execute a turn and return the complete answer.

        A turn the user stops (``stop-all``) comes back as a normal failed result
        (``error_kind="turn_stopped"``), not as a raised cancellation: the caller asked for it to
        end, and the route should answer it rather than drop the connection."""
        turn_id = TurnId.new()
        if request.delegation_depth > 0:
            # A delegated sub-turn runs inside its parent's task and stops with it.
            return await self._run_untracked(request, turn_id)
        with self.active_turns.track(request.user_id.value, turn_id.value, kind="run") as entry:
            started = time.perf_counter()
            try:
                return await self._run_untracked(request, turn_id)
            except asyncio.CancelledError:
                if not entry.stopped:
                    raise  # the client went away, or the process is shutting down
                _clear_cancellation()
                return self._error_result(
                    request, request.correlation_id or CorrelationId.new(), turn_id, started,
                    TurnStoppedError("the turn was stopped by the user"), TurnOutcome.ERROR,
                )

    async def _run_untracked(self, request: TurnRequest, turn_id: TurnId) -> TurnResult:
        correlation = request.correlation_id or CorrelationId.new()

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
                ) as scope, generation_budget(
                    self._turn_generation_budget_s(request)
                ), turn_budget_scope(self._new_turn_budget()) as budget:
                    result = await self._run_turn(
                        request, correlation, turn_id, started, budget
                    )
                    await self._remember_session_classification(request, scope)
                    return result
            except (UserConcurrencyLimitError, UserQuotaExhaustedError) as exc:
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
        budget: TurnBudget,
    ) -> TurnResult:
        deps = self._deps
        resume = await self._claim_confirmation(request)
        if resume is not None:
            request = self._resumed_request(request, resume)
        resolved_spec = self._resolve_agent(request)
        await self._require_engineer_consent(request)
        resolved_agent = resolved_spec.id if resolved_spec is not None else request.agent
        timings: dict[str, float] = {}

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

        # 5. Guard retrieved content and recalled memories ----------------------------
        stage_start = time.perf_counter()
        retrieval, memories, tainted, quarantined_count = await self._guard_context(
            request, retrieval, memories, turn_id
        )
        if deps.safety is not None:
            timings["guards_in"] = timings.get("guards_in", 0.0) + (
                time.perf_counter() - stage_start
            ) * 1000.0
        caller_context, context_quarantined = await self._guard_caller_context(request, turn_id)
        quarantined_count += context_quarantined
        handoff_context, handoff_quarantined = await self._guard_caller_context(
            request, turn_id, entries=request.handoff_context
        )
        quarantined_count += handoff_quarantined
        caller_context = (*caller_context, *handoff_context)
        tainted = (
            tainted or bool(request.caller_context) or bool(resume and resume.tainted)
            or request.inherited_taint or handoff_quarantined > 0
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
        agent_loop: Mapping[str, Any] | None = None
        exec_outcome: Any = None
        planning_failed = False
        plan_outcome: Any = None

        tool_declarations = await self._tool_declarations(request)
        plan_decision = self._plan_decision(request, tool_declarations, resumed=resume is not None)
        held: _ReviewHold | None = None
        if resume is not None or plan_decision.plan:
            stage_start = time.perf_counter()
            try:
                if plan_decision.review and resume is None:
                    held = await self._hold_plan_for_review(
                        request, message, tool_declarations, tainted, turn_id
                    )
                if held is not None and (held.held or held.plan is None):
                    outcome = None  # stopped at the gate, or nothing needed planning
                else:
                    outcome = await self._plan_and_execute(
                        request, message, tool_declarations, tainted,
                        turn_id=turn_id, correlation=correlation, resume=resume,
                        precomposed=held.plan if held is not None else None,
                    )
            except Exception:
                logger.warning("planning or execution failed; answering directly", exc_info=True)
                outcome = None
                planning_failed = True
                execution_notes.append(PLANNING_FAILED_NOTE)
            timings["planning"] = (time.perf_counter() - stage_start) * 1000.0
            if outcome is not None:
                exec_outcome = outcome
                plan_outcome = outcome
                agent_run_id = getattr(outcome, "run_id", None)
                await self._relieve_pressure(request, outcome)
                agent_loop = self._loop_summary(outcome)
                await self._index_run(request, agent_run_id, message)
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
        if held is not None and held.held:
            plan_summary = list(held.summary)
            pending_tool_calls = list(held.pending)
            needs_confirmation = [str(call["tool"]) for call in held.pending]
            plan_steps = len(held.summary)
            execution_notes.append(PLAN_REVIEW_NOTE)

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
            self._dispatch_shadow(shadow_completion)
        except Exception:
            logger.debug("shadow dispatch failed", exc_info=True)

        # 8b. The critic's check of the answer against what the tools reported (ADR-037) ------
        acceptance = (
            None if held is not None and held.held else self._acceptance(request, exec_outcome)
        )
        caveat, _caveat_findings = self._answer_caveat(
            exec_outcome, answer, request.message, acceptance
        )
        if caveat:
            answer = f"{answer}\n\n{caveat}"

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

        # 10. Persist session state --------------------------------------------------
        if deps.episodic is not None and request.session_id is not None:
            try:
                await deps.episodic.append(
                    request.session_id, request.user_id,
                    user_message=self._persistable(self._history_message(request)),
                    assistant_message=self._persistable(answer),
                )
            except Exception:
                # Losing session persistence degrades the next turn; failing this one would be
                # a worse trade.
                logger.warning("failed to persist session state")

        self._dispatch_lessons(
            request, resolved_agent, message, answer, plan_outcome,
            tainted=tainted, planning_failed=planning_failed, turn_id=turn_id,
        )

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
            # Every generation the turn made -- planning, reasoning, the answer, a repair --
            # was charged to its budget (F30), not just the last one.
            cost_usd=budget.cost_used_usd,
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
        await self._record(telemetry, request)

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
            agent_loop=self._with_acceptance(agent_loop, acceptance),
            plan_mode=plan_decision.as_dict(
                awaiting_approval=bool(held is not None and held.held), planning_failed=planning_failed,
            ),
        )

    # -- streaming ---------------------------------------------------------------------

    async def stream(self, request: TurnRequest) -> AsyncIterator[Mapping[str, Any]]:
        """Stream a turn as typed frames. A turn the user stops (``stop-all``) ends with an
        ``error`` frame of kind ``turn_stopped`` rather than a dropped connection (ADR-039).

        Tracked for the kill switch here, then delegated to :meth:`_stream_untracked`, which
        holds the pipeline and its own docstring.
        """
        turn_id = TurnId.new()
        if request.delegation_depth > 0:
            async for frame in self._stream_untracked(request, turn_id):
                yield frame
            return
        with self.active_turns.track(request.user_id.value, turn_id.value, kind="stream") as entry:
            started = time.perf_counter()
            inner = self._stream_untracked(request, turn_id)
            try:
                async for frame in inner:
                    yield frame
            except asyncio.CancelledError:
                if not entry.stopped:
                    raise
                _clear_cancellation()
                exc = TurnStoppedError("the turn was stopped by the user")
                self._error_result(
                    request, request.correlation_id or CorrelationId.new(), turn_id, started,
                    exc, TurnOutcome.ERROR,
                )
                yield {"type": "error", "error": exc.kind, "message": exc.message,
                       "retryable": False}
            finally:
                await inner.aclose()

    async def _stream_untracked(
        self, request: TurnRequest, turn_id: TurnId
    ) -> AsyncIterator[Mapping[str, Any]]:
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
        ), data_scope(*self._baseline_classification(request)) as scope, generation_budget(
            self._turn_generation_budget_s(request)
        ), turn_budget_scope(self._new_turn_budget()) as budget:
            try:
                deps = self._deps
                timings: dict[str, float] = {}
                guard_records: list[Mapping[str, Any]] = []
                resume = await self._claim_confirmation(request)
                if resume is not None:
                    request = self._resumed_request(request, resume)
                resolved_spec = self._resolve_agent(request)
                await self._require_engineer_consent(request)
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
                        **_vision_step_outcome(perception),
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

                retrieval, memories, tainted, quarantined_count = await self._guard_context(
                    request, retrieval, memories, turn_id
                )
                caller_context, context_quarantined = await self._guard_caller_context(
                    request, turn_id
                )
                quarantined_count += context_quarantined
                handoff_context, handoff_quarantined = await self._guard_caller_context(
                    request, turn_id, entries=request.handoff_context
                )
                quarantined_count += handoff_quarantined
                caller_context = (*caller_context, *handoff_context)
                tainted = (
                    tainted or bool(request.caller_context) or bool(resume and resume.tainted)
                    or request.inherited_taint or handoff_quarantined > 0
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
                plan_decision = self._plan_decision(request, tool_declarations, resumed=resume is not None)
                held: _ReviewHold | None = None
                if resume is not None or plan_decision.plan:
                    stage_start = time.perf_counter()
                    try:
                        if plan_decision.review and resume is None:
                            held = await self._hold_plan_for_review(
                                request, message, tool_declarations, tainted, turn_id
                            )
                        if held is None or not (held.held or held.plan is None):
                            async for item in self._iter_execution(
                                request, message, tool_declarations, tainted,
                                turn_id=turn_id, correlation=correlation, resume=resume,
                                precomposed=held.plan if held is not None else None,
                            ):
                                if isinstance(item, StepEvent):
                                    yield {"type": "step", **item.as_dict()}
                                elif isinstance(item, LoopEvent):
                                    # ADR-037: `budget`, `context_pressure`, `critic`, `stuck`.
                                    yield item.as_frame()
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
                    await self._index_run(request, getattr(outcome, "run_id", None), message)
                    tool_results = outcome.summary_for_context()
                    # ADR-037: what the loop learned (stopped as stuck, tests still red) -- only
                    # the loop's own notes; the step notes are `_run_turn`'s, unchanged here.
                    execution_notes.extend(self._loop_notes(outcome))
                    await self._relieve_pressure(request, outcome)
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
                if held is not None and held.held:
                    plan_summary = list(held.summary)
                    pending_tool_calls = list(held.pending)
                    needs_confirmation = [str(call["tool"]) for call in held.pending]
                    execution_notes.append(PLAN_REVIEW_NOTE)

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
                # The critic's check of the answer against what the tools reported, and the
                # loop's own escalation (ADR-037). Appended as more tokens, never as a rewrite.
                acceptance = (
                    None if held is not None and held.held else self._acceptance(request, outcome)
                )
                caveat, caveat_findings = self._answer_caveat(outcome, answer, message, acceptance)
                if caveat:
                    if caveat_findings:
                        yield {"type": "critic", "stage": "answer",
                               "findings": [f.as_dict() for f in caveat_findings]}
                    caveat_text = f"\n\n{caveat}"
                    collected.append(caveat_text)
                    answer += caveat_text
                    yield {"type": "token", "text": caveat_text}
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
                            # And from here on the redacted text *is* the answer (F26), as in
                            # `run`: session history must not keep what the guard took out, or
                            # the next turn's prompt -- possibly to a hosted provider -- would.
                            answer = decision.text
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
                            user_message=self._persistable(self._history_message(request)),
                            assistant_message=self._persistable(answer),
                        )
                    except Exception:
                        logger.warning("failed to persist session state")

                self._dispatch_lessons(
                    request, resolved_agent, message, answer, outcome,
                    tainted=tainted, planning_failed=planning_failed, turn_id=turn_id,
                )

                # Fire-and-forget shadow, off the turn's critical path (PP-04): failures must not
                # fail the turn, and its latency must not delay the `done` frame.
                try:
                    self._dispatch_shadow(completion)
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
                    plan_steps=(
                        len(outcome.plan.steps) if outcome is not None
                        else len(held.summary) if held is not None and held.held else 0
                    ),
                    plan_revisions=len(outcome.plan.revisions) if outcome is not None else 0,
                    planning_failed=planning_failed,
                )
                await self._record(telemetry, request)
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
                    # Why the loop stopped and what it concluded (ADR-037); null with no loop.
                    "agent_loop": self._with_acceptance(self._loop_summary(outcome), acceptance),
                    "plan_mode": plan_decision.as_dict(
                        awaiting_approval=bool(held is not None and held.held),
                        planning_failed=planning_failed,
                    ),
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

    # -- lessons (ADR-038, row 60) ------------------------------------------------------

    def _dispatch_lessons(
        self,
        request: TurnRequest,
        resolved_agent: str,
        message: str,
        answer: str,
        outcome: Any,
        *,
        tainted: bool,
        planning_failed: bool,
        turn_id: TurnId,
    ) -> None:
        """Hand a finished Engineer turn to the lesson sink, in the background. Never raises.

        ``message`` is the user's text *after* the input guards (redactions applied), never the raw
        request: a lesson must not be a way around what those guards took out.

        Off the turn's path -- the answer is already written -- and a no-op unless a sink is wired
        (``AURA_AUTO_LESSONS_ENABLED``). The record carries the *shape* of the plan (tool names,
        step descriptions, statuses, error kinds), never a step's arguments or output: those hold
        third-party text. A resumed or delegated turn counts like any other; a turn whose context
        was tainted is flagged so the extractor skips it.
        """
        sink = self._deps.lesson_sink
        if sink is None or resolved_agent != "engineer":
            return
        try:
            steps: list[LessonStep] = []
            revisions = 0
            plan = getattr(outcome, "plan", None)
            if plan is not None:
                revisions = len(plan.revisions)
                steps = [
                    LessonStep(
                        tool=str(step.tool), description=str(step.description),
                        status=str(step.status), error_kind=step.error_kind, attempts=int(step.attempts),
                    )
                    for step in plan.steps if step.kind is StepKind.TOOL and step.tool
                ]
            failed = planning_failed or any(s.status == "failed" for s in steps)
            if plan is not None:
                # The agent loop builds a *new* plan on each replan, so a pass that failed and was
                # fixed is no longer among `plan.steps` (and the new plan carries no revisions);
                # its goal still names the failure ("Failed: <description> (<error_kind>)", written
                # by `PlanningEngine.replan`). Only that structured shape is taken: the step's own
                # description and its error kind. A goal that names one is a replanned goal.
                named: dict[tuple[str, str], None] = {}
                for text in (str(plan.goal.description), *(str(st.description) for st in plan.steps)):
                    for description, kind in _FAILED_LINE.findall(text):
                        named.setdefault((description.strip(), kind))
                for description, kind in named:
                    steps.append(LessonStep(
                        tool="", description=description, status="failed", error_kind=kind,
                    ))
                if named:
                    revisions = max(revisions, 1)
            turn = LessonTurn(
                user_id=request.user_id, agent_id=resolved_agent, message=message,
                answer=answer, outcome="failure" if failed else "success",
                session_id=request.session_id.value if request.session_id else None,
                turn_id=turn_id.value, steps=tuple(steps), revisions=revisions, tainted=tainted,
            )
            task = asyncio.get_running_loop().create_task(self._run_lesson_sink(sink, turn))
        except Exception:
            logger.debug("lesson dispatch failed", exc_info=True)
            return
        self._lesson_tasks.add(task)
        task.add_done_callback(self._lesson_tasks.discard)

    @staticmethod
    async def _run_lesson_sink(sink: Any, turn: LessonTurn) -> None:
        try:
            await sink(turn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("lesson extraction failed", exc_info=True)

    async def wait_for_lessons(self) -> None:
        """Wait for lesson extraction in flight (tests, graceful shutdown)."""
        while self._lesson_tasks:
            await asyncio.gather(*list(self._lesson_tasks), return_exceptions=True)

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
                return await deps.knowledge.retrieve(message, owner_id=request.knowledge_owner or request.user_id)
            except Exception:
                logger.warning("knowledge retrieval failed; continuing without it")
                return None

        async def load_history() -> tuple[Conversation, str]:
            if deps.episodic is None or request.session_id is None:
                return Conversation(), ""
            try:
                loaded = await deps.episodic.history(request.session_id, request.user_id)
            except Exception:
                logger.warning("session history load failed; continuing without it")
                return Conversation(), ""
            return self._redact_loaded_history(*loaded)

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
                return await deps.cag.prefix_for(request.knowledge_owner or request.user_id)
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

    def _safety_setting(self, name: str) -> bool:
        return bool(getattr(getattr(self._deps.settings, "safety", None), name, False))

    def _redact_loaded_history(self, history: Conversation, summary: str) -> tuple[Conversation, str]:
        """Session history as the prompt sees it, with credentials redacted when
        ``AURA_SAFETY_REDACT_CONTEXT_SECRETS`` is on. History written before the persisted-
        transcript switch existed still holds whatever was typed then; this is what keeps it from
        reaching the model."""
        if not self._safety_setting("redact_secrets_in_context") or self._deps.safety is None:
            return history, summary
        messages = []
        for message in history.messages:
            cleaned, _ = redact_secrets(message.content)
            messages.append(message if cleaned == message.content else replace(message, content=cleaned))
        return Conversation(tuple(messages)), redact_secrets(summary)[0] if summary else summary

    def _persistable(self, text: str) -> str:
        """``text`` as session history stores it: credentials redacted when
        ``AURA_SAFETY_REDACT_HISTORY_SECRETS`` is on, unchanged otherwise."""
        if not self._safety_setting("redact_secrets_in_history"):
            return text
        return redact_secrets(text)[0]

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

    def _session_sensitive_key(self, request: TurnRequest) -> str | None:
        if request.session_id is None:
            return None
        return _SESSION_SENSITIVE_KEY.format(
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
        sensitive_key = self._session_sensitive_key(request)
        cache = self._deps.session_marks
        if key is None or sensitive_key is None or cache is None:
            return False
        try:
            found = await cache.get_many([key, sensitive_key])
        except Exception:
            return False
        # Monotonic (F32): a sensitive mark wins over a clean one, whichever was written last.
        return key in found and sensitive_key not in found

    async def _remember_session_classification(
        self, request: TurnRequest, scope: DataScope
    ) -> None:
        """Mark the session clean after an INTERNAL turn, or sensitive after a SENSITIVE one.

        Once sensitive, a session stays sensitive (F32). Turns of one session can overlap: a
        clean turn that started before a sensitive one and finished after it used to re-set the
        clean mark the sensitive one had just removed, and the next turn's history -- carrying
        the sensitive answer -- could go to a hosted provider. The sensitive mark is written and
        never removed by a clean turn, and reads honour it over the clean mark.
        """
        if not self._restricts_data():
            return
        key = self._session_clean_key(request)
        sensitive_key = self._session_sensitive_key(request)
        cache = self._deps.session_marks
        if key is None or sensitive_key is None or cache is None:
            return
        try:
            if scope.level.rank <= DataClassification.INTERNAL.rank:
                await cache.set(key, b"1", ttl_s=_SESSION_CLEAN_TTL_S)
            else:
                # Sensitive first: if the delete below then fails, the session already reads
                # as not clean.
                await cache.set(sensitive_key, b"1", ttl_s=_SESSION_CLEAN_TTL_S)
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
        llm = getattr(self._deps.settings, "llm", None)
        threshold = (
            float(getattr(llm, "complexity_threshold", 0.5))
            if getattr(llm, "complexity_routing", False)
            else None
        )
        return workload_for_turn(
            route=request.route,
            agent=spec.id if spec is not None else request.agent,
            user_key=request.user_id.value,
            workload=request.workload,
            message=request.message,
            complexity_threshold=threshold,
        )

    async def _require_engineer_consent(self, request: TurnRequest) -> None:
        """Refuse an Engineer turn from someone without a current Anthropic consent (D-03, WP1).

        Keyed on the same thing the Claude slot is fenced on -- the turn's workload being
        ENGINEER, from the *resolved* persona (WP6a) -- so consent covers exactly the turns that
        can reach Anthropic: chat and stream, an approval resume (checked after the held turn's
        own persona is restored) and a delegated sub-turn (delegation runs the kernel again).
        Called before planning, so a refused turn sends nothing anywhere.
        """
        store = self._deps.engineer_consent
        if store is None or self._workload_hint(request).workload is not WorkloadClass.ENGINEER:
            return
        consent = await store.get(request.user_id)
        if consent is None or not consent.is_current(ENGINEER_CONSENT_NOTICE_VERSION):
            raise ConsentRequiredError(
                "The Engineer sends your code to Anthropic; agree to that in the app first.",
                details={"current_version": ENGINEER_CONSENT_NOTICE_VERSION},
            )

    async def preflight(self, request: TurnRequest) -> None:
        """Refuse, before anything is queued or run, a turn the kernel would refuse at its start:
        today an Engineer turn without a current consent (D-03). Used by the agent task gateway
        (ADR-041), which accepts a task now and runs it later -- a person who has not agreed
        should hear so when they submit, not find a failed task afterwards. The turn itself still
        re-checks when it runs. Raises the same :class:`ConsentRequiredError` the turn would."""
        await self._require_engineer_consent(request)

    async def revise_held_plan(self, request: TurnRequest) -> Any:
        """ADR-045 (row 51): apply the person's free-text feedback (``request.message``) to the plan
        held for them (``request.approval_ids``) and return a ``PlanFeedbackResult``.

        Planned only: nothing runs, whatever the comment says. The plan is replanned at most once
        per comment (``PlanningEngine.replan``, bounded by its revision allowance), held again under
        new approval ids, and the old ids are consumed -- see ``planning/feedback.py``. Owner and
        session are enforced by the confirmation store; the tools offered to the planner are those
        the held turn's persona and this caller's grant allow, as for any turn."""
        held = await self._confirmations.peek(
            request.approval_ids,
            user_id=request.user_id.value,
            session_id=request.session_id.value if request.session_id else None,
        )
        tools = await self._tool_declarations(replace(request, agent=held.agent))
        service = PlanFeedbackService(
            confirmations=self._confirmations, planner=lambda: self._deps.planner,
            audit=self._deps.audit, clock=self._deps.clock,
        )
        return await service.revise(
            approval_ids=request.approval_ids, feedback=request.message,
            user_id=request.user_id.value,
            session_id=request.session_id.value if request.session_id else None,
            tools=tools,
            correlation_id=(request.correlation_id or CorrelationId.new()).value,
            turn_id=TurnId.new().value,
        )

    async def _index_run(self, request: TurnRequest, run_id: str | None, goal: str) -> None:
        """Hand the run's goal to the opt-in preview index (ADR-041). A no-op unless the operator
        enabled previews; never raises."""
        index = self._deps.run_index
        if index is None or not run_id:
            return
        try:
            await index.note(request.user_id, run_id, goal)
        except Exception:  # pragma: no cover - the index already swallows its own failures
            logger.debug("run preview not stored", exc_info=True)

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
                 "git_checkout_branch", "workspace_snapshot", "workspace_restore")
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

    async def _relieve_pressure(self, request: TurnRequest, outcome: Any) -> None:
        """Answer a ``context_pressure`` signal from the agent loop (ADR-037) with the existing
        history compression: fold the session's older turns into its summary now, so the next turn
        carries the summary instead of the verbatim turns. The loop already shortened the tool
        output it held; this is the other half, and it runs at most once per turn because the loop
        signals pressure at most once per run. A failure here never fails the turn."""
        loop = getattr(outcome, "loop", None)
        episodic = self._deps.episodic
        if loop is None or loop.pressure is None or episodic is None or request.session_id is None:
            return
        compact = getattr(episodic, "compact", None)
        if compact is None:
            return
        try:
            loop.history_compacted = bool(await compact(request.session_id, request.user_id))
        except Exception:
            logger.warning("compacting session history under context pressure failed", exc_info=True)

    @staticmethod
    def _acceptance(request: TurnRequest, outcome: Any) -> AcceptanceReport | None:
        """ADR-045 (row 41): the run's tool results checked against the caller's
        ``success_criteria``; ``None`` when none were supplied. Never raises."""
        if not request.success_criteria:
            return None
        try:
            return check_acceptance(request.success_criteria, getattr(outcome, "plan", None))
        except Exception:  # pragma: no cover - a check is an aid, never a failed turn
            logger.warning("acceptance check failed", exc_info=True)
            return None

    @staticmethod
    def _with_acceptance(
        loop: Mapping[str, Any] | None, acceptance: AcceptanceReport | None
    ) -> Mapping[str, Any] | None:
        """``loop`` with ``acceptance`` added; unchanged without one (an existing request's
        ``agent_loop`` keeps exactly its old shape)."""
        if acceptance is None:
            return loop
        return {**(loop or {}), "acceptance": acceptance.as_dict()}

    @staticmethod
    def _loop_summary(outcome: Any) -> Mapping[str, Any] | None:
        """The agent loop's report for a done frame / response, or ``None`` with no loop."""
        loop = getattr(outcome, "loop", None)
        return loop.as_dict() if loop is not None else None

    @staticmethod
    def _loop_notes(outcome: Any) -> list[str]:
        """SYSTEM-channel notes from the agent loop (ADR-037), beside the step notes.

        The user is told the full escalation separately and deterministically (``_answer_caveat``);
        this only keeps the model from writing an answer that contradicts what the loop concluded.
        """
        loop = getattr(outcome, "loop", None)
        if loop is None:
            return []
        notes: list[str] = []
        # Short on purpose: the scratchpad section is capped at a tenth of a small prompt budget,
        # and a note that does not fit is dropped, not shortened.
        if loop.stop_reason == "stuck" and loop.stuck:
            notes.append(
                "Execution note (system): the run was stopped because it was repeating itself. "
                "Do not say the task succeeded; say what was tried and what could not be done."
            )
        for finding in loop.findings:
            if finding.get("code") == "unresolved_tests_failed":
                notes.append(
                    "Execution note (system): the last test run failed. "
                    "Do not state or imply that the tests pass."
                )
                break
        return notes

    @staticmethod
    def _answer_caveat(
        outcome: Any, answer: str, message: str, acceptance: AcceptanceReport | None = None,
    ) -> tuple[str, list[Any]]:
        """Caveat text to append to the answer, and the critic findings behind it (ADR-037).

        Two sources, both deterministic. The loop's own escalation -- what it gave up on and what
        to try -- is always appended when there is one, so a stopped run is never presented as a
        finished one. The critic's answer checks (a success claim the tool results do not support;
        a file or test no tool result mentions) run only when the critic is on for this loop.
        """
        loop = getattr(outcome, "loop", None)
        # ADR-045: a run that did not meet its criteria says so, loop or no loop.
        acceptance_note = acceptance.caveat() if acceptance is not None else ""
        if loop is None:
            return acceptance_note, []
        parts: list[str] = []
        findings: list[Any] = []
        if loop.escalation:
            parts.append(loop.escalation)
        if loop.critic_active:
            try:
                findings = [
                    f for f in critique_answer(
                        answer, outcome.plan, user_message=message,
                        observations=observe_plan(outcome.plan),
                    )
                ]
            except Exception:  # pragma: no cover - a critic bug must not fail the turn
                logger.debug("answer critic failed", exc_info=True)
            for finding in findings:
                prefix = "Caution" if finding.severity == "error" else "Unverified"
                parts.append(f"{prefix} (checked against the tool results): {finding.message}.")
        if acceptance_note:
            parts.append(acceptance_note)
        return "\n\n".join(parts), findings

    @staticmethod
    def _execution_notes(outcome: Any) -> list[str]:
        """The step notes (below) plus the agent loop's own (ADR-037)."""
        return AIKernel._step_notes(outcome) + AIKernel._loop_notes(outcome)

    @staticmethod
    def _step_notes(outcome: Any) -> list[str]:
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
        base_prefixes = agent.allowed_tool_prefixes if agent is not None else frozenset()
        base_ceiling = agent.risk_ceiling if agent is not None else None
        # One intersection for names *and* prefixes (domain/tools.py), the same function
        # `delegate_to_agent` uses: a prefix survives only if both sides hold it (audit T-03).
        effective_tools, effective_prefixes = intersect_tool_grants(
            base_tools, base_prefixes,
            request.delegated_tool_grant, request.delegated_tool_prefixes,
        )
        if request.delegated_risk_ceiling is None:
            effective_ceiling = base_ceiling
        elif base_ceiling is None:
            effective_ceiling = request.delegated_risk_ceiling
        else:
            effective_ceiling = min(base_ceiling, request.delegated_risk_ceiling)
        return effective_tools, effective_prefixes, effective_ceiling

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
        allowed_tools, allowed_prefixes, max_sensitivity = self._delegation_grant(request, agent)
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
        return tuple(
            d for d in declarations
            if tool_grant_permits(str(d.get("name", "")), allowed_tools, allowed_prefixes)
        )

    def _is_side_effecting(self, name: str) -> bool:
        try:
            return bool(self._deps.tools.spec(name).side_effecting)
        except Exception:
            return False

    def _plan_decision(
        self, request: TurnRequest, tools: Sequence[Mapping[str, Any]], *, resumed: bool = False
    ) -> PlanDecision:
        """Whether to plan this turn, in which mode, and why (``planning/modes.py``, ADR-038).

        With no ``plan_mode``, no ``plan`` and no ``AURA_REASONING_DEFAULT_PLAN_MODE`` this is
        today's rule exactly: an agent-loop persona (the Engineer) plans, everyone else by the
        planner's own heuristics, and ``plan`` forces it either way. A resumed confirmation never
        depends on any of it: it executes its stored plan whatever would be decided now.
        """
        deps = self._deps
        asked = normalise_mode(request.plan_mode)
        if resumed:
            return PlanDecision(plan=True, mode="resumed", requested=asked, reason="approved_plan")
        if deps.planner is None or deps.executor is None or not tools:
            return PlanDecision(plan=False, mode=asked or "default", requested=asked,
                                reason="no_tools_or_planner")
        agent = self._resolve_agent(request)
        agentic = agent is not None and agent.max_plan_iterations > 1
        # An agentic persona exists to do multi-step tool work, and planning is the only path that
        # runs a tool at all. Gating it behind heuristics tuned for conversational personas is what
        # makes such a persona answer "I can't do that" while holding the exact tool for the job.
        # The cost is one planning generation on a turn that may not need it; a plan that comes
        # back as a single RESPOND step still falls through to the direct answer (`_compose_plan`).
        names = [str(t.get("name", "")) for t in tools]
        settings = deps.settings
        default_mode = str(getattr(getattr(settings, "reasoning", None), "default_plan_mode", "") or "")
        moded = asked is not None or normalise_mode(default_mode) is not None
        side_effecting = moded and any(self._is_side_effecting(n) for n in names)
        threshold = float(getattr(getattr(settings, "llm", None), "complexity_threshold", 0.5) or 0.5)
        return decide_plan(
            requested_mode=request.plan_mode, plan_flag=request.plan, default_mode=default_mode,
            message=request.message, side_effecting_tools=side_effecting, agentic_persona=agentic,
            heuristic=lambda force: deps.planner.needs_plan(
                request.message, available_tools=names, force=force
            ),
            complex_message=moded and assess_complexity(request.message).is_complex(threshold),
        )

    def _should_plan(self, request: TurnRequest, tools: Sequence[Mapping[str, Any]]) -> bool:
        return self._plan_decision(request, tools).plan

    async def _user_autonomy(self, request: TurnRequest) -> str:
        """The calling user's review profile (ADR-039), ``standard`` when none is stored.

        Never raises: a store that cannot answer must not fail a turn, and the answer it falls
        back to is the one that changes nothing."""
        store = self._deps.autonomy
        user_id = getattr(request, "user_id", None)
        if store is None or user_id is None:
            return "standard"
        try:
            return await store.get(user_id)
        except Exception:
            logger.warning("autonomy profile lookup failed; using standard", exc_info=True)
            return "standard"

    def _tool_context(
        self, request: TurnRequest, tainted: bool, autonomy: str = "standard"
    ) -> ToolContext:
        agent = self._resolve_agent(request)
        allowed_tools, allowed_prefixes, max_sensitivity = self._delegation_grant(request, agent)
        deadline_s = 60.0
        reasoning = getattr(self._deps.settings, "reasoning", None)
        if agent is not None and agent.max_plan_iterations > 1:
            # An agentic persona (today: `engineer`) runs builds and test suites: `run_command`
            # declares 605 s, and on a paired desktop the daemon may also wait for a human's
            # approval first. The flat 60 s cut those off long before their own budgets
            # (`ToolRegistry.call_timeout`, tool audit T-01). The loop's own deadline is the
            # right ceiling: it is sized for the longest such call plus the approval window, and
            # no single call can usefully outlive the loop that made it.
            deadline_s = getattr(reasoning, "engineer_loop_deadline_s", 960.0)
        elif agent is not None and agent.permits_tool("delegate_to_agent"):
            # A turn whose persona can delegate may itself be dispatching a recursive kernel.run()
            # (aura_core/tools/delegation.py) rather than a simple API call -- the flat 60s every
            # other tool gets would truncate that sub-turn well before its own budgets do.
            deadline_s = getattr(reasoning, "delegation_deadline_s", 180.0)
        return ToolContext(
            user_id=request.user_id,
            knowledge_owner=request.knowledge_owner,
            correlation_id=(request.correlation_id or CorrelationId.new()).value,
            deadline_s=deadline_s,
            tainted=tainted,
            agent_id=agent.id if agent is not None else None,
            allowed_tools=allowed_tools,
            allowed_tool_prefixes=allowed_prefixes,
            max_sensitivity=max_sensitivity,
            memory_scope=agent.memory_scope if agent is not None else "shared",
            capabilities=request.capabilities,
            delegation_depth=request.delegation_depth,
            session_id=request.session_id.value if request.session_id else None,
            autonomy=autonomy,
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
                "origin": request.origin,
                # The user's own words for this turn, for `remember_fact` to check a fact against
                # (tools/fact_grounding.py). A delegated turn's message is another agent's task
                # text, not something the user said, so it carries none.
                "user_message": (request.message or "")[:8000] if request.delegation_depth == 0 else None,
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
        goal = deps.planner.goal_from_message(message, criteria=request.success_criteria)
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
        # TOOLS_MAX_CALLS_PER_TURN (audit FSA-112): the per-turn tool cap for a persona that
        # declares no tool_budget of its own; 0 keeps "not bounded by this". A persona's own
        # figure (engineer: 216) is never overridden.
        per_turn = int(getattr(getattr(deps.settings, "tools", None), "max_calls_per_turn", 0) or 0)
        if budget.tool_budget is None and per_turn > 0:
            budget = replace(budget, tool_budget=per_turn)
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
        precomposed: Any = None,
    ) -> Any:
        """The execution outcome, or ``None`` when the turn needs no plan. Raises when planning
        or execution fails; ``_run_turn`` turns that into a degraded direct answer."""
        deps = self._deps
        plan, confirmations = await self._plan_for(
            request, message, tools, resume, precomposed
        )
        if plan is None:
            return None
        context = self._tool_context(request, tainted, await self._user_autonomy(request))
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
        precomposed: Any = None,
    ) -> AsyncIterator[StepEvent | LoopEvent | ExecutionOutcome]:
        """Yield step progress, then the outcome. Prefer ``executor.stream``."""
        deps = self._deps
        plan, confirmations = await self._plan_for(
            request, message, tools, resume, precomposed
        )
        if plan is None:
            return
        context = self._tool_context(request, tainted, await self._user_autonomy(request))
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

    async def _hold_plan_for_review(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
        tainted: bool,
        turn_id: TurnId,
    ) -> _ReviewHold:
        """``review`` mode: compose the plan, store it, and return it for the user to approve.

        Nothing runs. The plan is held in the same ``PendingConfirmationStore`` a confirm-gated
        call uses (ADR-006, F-04), so approving is the existing flow: the client sends the
        ``approval_id`` s back in ``approval_ids`` and the kernel executes *that stored plan*, never
        a re-plan. Each tool step gets an approval id bound to its tool and arguments. A step whose
        arguments are not known until an earlier step has run (``${s1.output}``) cannot match the
        digest taken now, so it is held again for its own confirmation when its turn comes -- the
        user approves the plan, and the data-dependent calls individually.

        A plan with no tool step has nothing to approve and is returned unheld. Failing to store
        the pending turn raises: a review that cannot be approved must not look like one that can.
        """
        plan = await self._compose_plan(request, message, tools)
        if plan is None:
            return _ReviewHold()
        tool_steps = [step for step in plan.steps if step.kind is StepKind.TOOL and step.tool]
        if not tool_steps:
            return _ReviewHold(plan=plan)
        calls = [
            PendingCall(
                step_id=step.id.value, call_id=f"step_{step.id.value}", tool=str(step.tool),
                arguments=dict(step.arguments),
            )
            for step in tool_steps
        ]
        approval_ids = await self._confirmations.issue(
            user_id=request.user_id.value,
            session_id=request.session_id.value if request.session_id else None,
            agent=request.agent,
            message=request.message,
            plan=plan,
            tainted=tainted,
            calls=calls,
            turn_id=turn_id.value,
            workspace_root=request.workspace_root,
        )
        summary = tuple(
            {
                "id": step.id.value, "kind": str(step.kind), "status": str(step.status),
                "description": step.description,
                **({"tool": step.tool} if step.kind is StepKind.TOOL and step.tool else {}),
            }
            for step in plan.steps
        )
        pending = tuple(
            {
                "call_id": call.call_id, "tool": call.tool, "arguments": dict(call.arguments),
                "diff_preview": None, "approval_id": approval_ids[call.call_id], "review": True,
            }
            for call in calls if call.call_id in approval_ids
        )
        return _ReviewHold(plan=plan, held=True, summary=summary, pending=pending)

    async def _plan_for(
        self,
        request: TurnRequest,
        message: str,
        tools: Sequence[Mapping[str, Any]],
        resume: ResumedTurn | None,
        precomposed: Any = None,
    ) -> tuple[Any, Any]:
        """The plan to execute, and the confirmations it may use.

        A resumed turn executes the plan stored when it was held -- never a fresh one: a re-plan
        is not deterministic and can be steered, so it could produce a different call from the
        one the user approved (F-04). Only a resumed turn carries a grant.
        """
        if resume is not None:
            return resume.plan, resume.grant
        if precomposed is not None:
            # A plan already composed this turn (``review`` mode found nothing to approve in it).
            return precomposed, None
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
        """The held turn's own message, persona and workspace root, not whatever the resuming
        request carried: the plan, its grants and the answer all belong to the turn the user
        approved, and so does the place its commands run (FSA-006) -- the client may have opened
        another workspace since the hold, and an approved `git reset --hard` must not follow it."""
        return replace(
            request,
            message=resume.message or request.message,
            agent=resume.agent,
            workspace_root=resume.workspace_root,
        )

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
                    workspace_root=request.workspace_root,
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

    def _new_turn_budget(self) -> TurnBudget:
        """This turn's generation and cost ceiling (F30).

        Charged by every generation made inside the turn, not only the kernel's own: the router
        reads it ambiently. A sub-turn (delegation) also charges the turn it was started from.
        ``max_cost_per_turn_usd`` of 0 (the default) leaves the cost ceiling off.
        """
        llm = getattr(self._deps.settings, "llm", None)
        return TurnBudget(
            max_generations=getattr(llm, "max_generations_per_turn", 8),
            max_cost_usd=float(getattr(llm, "max_cost_per_turn_usd", 0.0) or 0.0),
            parent=current_turn_budget(),
        )

    async def _guard_context(
        self,
        request: TurnRequest,
        retrieval: RetrievalResult | None,
        memories: Sequence[MemoryHit],
        turn_id: TurnId,
    ) -> tuple[RetrievalResult | None, list[MemoryHit], bool, int]:
        """Retrieved chunks *and* recalled memories through the context guards (F35).

        Returns them with the guards' sanitised text in place of the original -- stripping
        invisible characters is pointless if the stripped text is then thrown away -- minus
        anything quarantined, plus whether the turn is now tainted and how many were dropped.
        A memory is third-party text as much as a document is: it can have been written from a
        poisoned page or tool output, so it gets the same treatment.
        """
        memories = list(memories)
        safety = self._deps.safety
        chunks = tuple(retrieval.chunks) if retrieval is not None else ()
        if safety is None or not (chunks or memories):
            return retrieval, memories, False, 0
        items = [(f"chunk:{i}:{c.citation()}", c.chunk.content) for i, c in enumerate(chunks)]
        items += [(f"memory:{i}", h.record.content) for i, h in enumerate(memories)]
        accepted, quarantined, tainted = await safety.guard_context_items(
            items, user_id=request.user_id, turn_id=turn_id
        )
        sanitised = dict(accepted)

        kept_chunks: list[RetrievedChunk] = []
        dropped_chunks: list[RetrievedChunk] = []
        for i, chunk in enumerate(chunks):
            text = sanitised.get(items[i][0])
            if text is None or not text.strip():
                dropped_chunks.append(chunk)
            elif text == chunk.chunk.content:
                kept_chunks.append(chunk)
            else:
                kept_chunks.append(replace(chunk, chunk=replace(chunk.chunk, content=text)))
        kept_memories: list[MemoryHit] = []
        for i, hit in enumerate(memories):
            text = sanitised.get(items[len(chunks) + i][0])
            if text is None or not text.strip():
                continue
            kept_memories.append(
                hit if text == hit.record.content
                else replace(hit, record=replace(hit.record, content=text))
            )

        changed = bool(dropped_chunks) or any(a is not b for a, b in zip(kept_chunks, chunks))
        if retrieval is not None and changed:
            retrieval = RetrievalResult(
                chunks=tuple(kept_chunks),
                query=retrieval.query,
                candidates_considered=retrieval.candidates_considered,
                below_threshold=retrieval.below_threshold,
                quarantined=tuple(dropped_chunks),
                strategy=retrieval.strategy,
                duration_ms=retrieval.duration_ms,
            )
        return retrieval, kept_memories, tainted, len(quarantined)

    async def _guard_caller_context(
        self, request: TurnRequest, turn_id: TurnId,
        entries: tuple[CallerContext, ...] | None = None,
    ) -> tuple[tuple[CallerContext, ...], int]:
        """Caller-supplied evidence through the same context guards as retrieved chunks (AR-5).

        Returns the entries that passed -- carrying the guards' sanitised text (F35) -- and how
        many were quarantined. Whatever this returns, the caller taints the turn when any
        context was *sent*: a side effect must never be unlocked by evidence a calling system
        put in front of the model.
        """
        entries = request.caller_context if entries is None else entries
        if not entries or self._deps.safety is None:
            return entries, 0
        keys = [f"{i}:{e.id}" for i, e in enumerate(entries)]
        accepted, quarantined, _ = await self._deps.safety.guard_context_items(
            [(key, e.content()) for key, e in zip(keys, entries)],
            user_id=request.user_id, turn_id=turn_id,
        )
        sanitised = dict(accepted)
        kept: list[CallerContext] = []
        for key, entry in zip(keys, entries):
            text = sanitised.get(key)
            if text is None:
                continue
            if text != entry.content():
                prefix = f"{entry.source}: " if entry.source else ""
                body = text[len(prefix):] if prefix and text.startswith(prefix) else text
                try:
                    entry = CallerContext.of(
                        id=entry.id, text=body, source=entry.source if prefix and
                        text.startswith(prefix) else "",
                    )
                except ValidationError:
                    continue  # nothing left once sanitised
            kept.append(entry)
        return tuple(kept), len(quarantined)

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
                    "arabic_reply": (
                        "true"
                        if reply_language(request.message, request.preferred_language) == "ar"
                        else ""
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

    def _turn_generation_budget_s(self, request: TurnRequest) -> float | None:
        """The generation budget for this turn (PP-06), or ``None`` for no budget.

        Only for turns a person is waiting on in real time -- chat and voice. An Engineer loop
        runs many generations over minutes under its own deadline (`engineer_loop_deadline_s`),
        which a chat-sized budget would cut off; background workloads keep their per-attempt
        timeouts only. A sub-turn delegated from a chat turn shares the parent's budget
        (`generation_budget` never replaces an open one).
        """
        llm = getattr(self._deps.settings, "llm", None) if self._deps.settings is not None else None
        seconds = float(getattr(llm, "turn_generation_budget_s", 0.0) or 0.0)
        if seconds <= 0:
            return None
        if self._workload_hint(request).workload not in (
            WorkloadClass.INTERACTIVE, WorkloadClass.VOICE,
        ):
            return None
        return seconds

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

    def _dispatch_shadow(self, completion: CompletionRequest) -> None:
        """Start shadow mirroring in the background and return at once (PP-04).

        It used to be awaited inline, after the answer was generated but before the turn
        returned -- so a registered shadow version added a whole extra generation (up to the
        router's timeout, times its fallbacks) to every turn, and held the stream's ``done``
        frame back for as long. The task copies the turn's context, so the shadow call keeps
        the turn's data classification (a hosted provider still cannot see sensitive content).

        At most one shadow generation runs at a time. Shadow traffic is a sample for comparing
        versions, not a queue to drain: when one is already running, this turn is not mirrored.
        """
        deps = self._deps
        if deps.models is None or deps.router is None:
            return
        if self._shadow_task is not None and not self._shadow_task.done():
            logger.debug("shadow generation already running; not mirroring this turn")
            return
        task = asyncio.get_running_loop().create_task(self._run_shadow(completion))
        self._shadow_task = task
        task.add_done_callback(_consume_shadow_result)

    async def wait_for_shadow(self) -> None:
        """Wait for a running shadow generation, if any. For tests and graceful shutdown."""
        task = self._shadow_task
        if task is not None and not task.done():
            await asyncio.wait({task})

    async def _run_shadow(self, completion: CompletionRequest) -> None:
        """Mirror traffic to shadow versions; never surface their output.

        Runs with no generation budget: the turn that started it is over, and its spent budget
        would otherwise cut the shadow call short (PP-06).
        """
        with no_generation_budget(), no_turn_budget():
            await self._mirror_to_shadows(completion)

    async def _mirror_to_shadows(self, completion: CompletionRequest) -> None:
        deps = self._deps
        if deps.models is None or deps.router is None:
            return
        try:
            shadows = await deps.models.shadow_versions(self._registry_model_id())
        except Exception:
            return
        # Lowest admission priority, and no user key: a shadow call must never wait ahead of a
        # person's turn, nor count against that person's concurrent-generation quota.
        hint = replace(effective_workload(completion), user_key=None, priority_override=9)
        for shadow in shadows:
            # The same check the live assignment path applies (`_assigned_model`). A URI that
            # is not a servable tag fails on every call, and each failure would count against
            # the circuit breaker of the provider real turns are using.
            model = self._servable_model_tag(shadow.artefact_uri)
            if model is None:
                continue
            try:
                started = time.perf_counter()
                shadow_req = CompletionRequest(
                    conversation=completion.conversation,
                    model=model,
                    sampling=completion.sampling,
                    policy=completion.policy,
                    require_local=completion.require_local,
                    tools=(),
                    workload=hint,
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
        # Per person *and* per data scope (ADR-034): a team turn's answer rests on the team's
        # corpus, so it must never be served to (or from) the same person's personal turns -- or,
        # after they leave the team, at all. Personal turns keep their pre-workspace key.
        namespace = request.user_id.value
        if request.knowledge_owner is not None and request.knowledge_owner != request.user_id:
            namespace = f"{namespace}|{request.knowledge_owner.value}"
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
        asyncio.ensure_future(self._record(telemetry, request))
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
            error_details=(
                dict(exc.details)
                if isinstance(exc, (OutputInvalidError, ConsentRequiredError, UserQuotaExhaustedError))
                else None
            ),
        )

    async def _record(self, telemetry: TurnTelemetry, request: TurnRequest | None = None) -> None:
        _trace_turn(telemetry)
        ledger = self._deps.usage_ledger
        if ledger is not None and request is not None:
            # Per user, which the content-free telemetry above deliberately is not. Never raises.
            await ledger.record(request.user_id.value, telemetry, task_id=request.task_id)
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
