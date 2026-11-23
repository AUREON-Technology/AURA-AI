"""`delegate_to_agent`: let one persona hand a self-contained sub-task to another persona.

This is the only inter-agent delegation mechanism in the codebase. It is deliberately just a
`Tool`, dispatched through the exact same `ToolRegistry.execute` -> `ToolPolicy.evaluate` ->
`Tool.execute` path as every other tool (ADR-006): a model's own emitted `tool_calls` never
execute anything, only planning/execution does, and this tool is reached through that path like
any other. Recursion happens by calling `AIKernel.run()` again with a *narrower* `TurnRequest` --
never by any code path that bypasses the kernel.

Two things bound the blast radius of a delegated sub-turn, deliberately redundant with each
other:

* **Grant intersection, never union.** The sub-turn's `allowed_tools`/`risk_ceiling` is the
  intersection of the delegating persona's own (already-resolved) grant and the target persona's
  declared grant -- computed here, and re-intersected again inside `AIKernel._tool_context()`/
  `_tool_declarations()` against the resolved `AgentSpec` (see `TurnRequest.delegated_tool_grant`
  in `kernel/kernel.py`). A bug in either place cannot, on its own, let a sub-turn exceed either
  persona's declared grant.
* **A hard recursion ceiling** (`settings.reasoning.max_delegation_depth`, default `1`), checked
  before anything else in the handler below. Combined with only `orchestrator` holding this tool
  in `aura_core/agents/registry.py`, delegation is capped at exactly one hop by construction in
  the default configuration -- the depth counter is defense in depth for if that ever changes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping
from uuid import uuid4

from ..agents.handoff import (
    HandoffStore,
    build_handoff,
    new_handoff_id,
    outcome_from_result,
    render_handoff,
)
from ..agents.registry import AgentRegistry
from ..agents.tasks import AgentTaskLimitError, AgentTaskManager, TaskResult
from ..domain.caller_context import CallerContext
from ..domain.connectors import CONNECTOR_TOKEN_PREFIX
from ..domain.errors import ToolExecutionError
from ..domain.identifiers import CorrelationId
from ..domain.tools import NESTED_DISPATCH_TAG, ToolCategory, ToolSensitivity, intersect_tool_grants
from ..observability.logging import get_logger
from ..ports.runtime import Tool, ToolContext
from .base import param, tool

if TYPE_CHECKING:
    from ..kernel.kernel import AIKernel

logger = get_logger("tools.delegation")

__all__ = ["build_delegation_tools", "DELEGATION_TASK_TOOL_NAMES", "DELEGATION_HANDOFF_TOOL_NAMES"]

DELEGATION_TASK_TOOL_NAMES = ("list_agent_tasks", "get_agent_task", "cancel_agent_task")
DELEGATION_HANDOFF_TOOL_NAMES = ("get_handoff", "list_handoffs")


def build_delegation_tools(
    *,
    agents: AgentRegistry,
    kernel: Callable[[], "AIKernel"],
    max_depth: int = 1,
    deadline_s: float = 180.0,
    tasks: AgentTaskManager | None = None,
    handoffs: HandoffStore | None = None,
    checkpoint_provider: Callable[[Any, str], Awaitable[Any]] | None = None,
    structured_handoff: bool = False,
) -> list[Tool]:
    """Build the delegation tools.

    ``kernel`` is a zero-arg resolver rather than a live ``AIKernel`` instance, because
    ``Container._build_tools()`` runs before ``self.kernel`` is constructed in
    ``Container.__init__`` -- the same reason ``Container._build_github_client_resolver()``
    resolves lazily rather than closing over a client built too early. Call this only once the
    kernel itself is guaranteed to exist by the time a request actually reaches the handler
    (i.e. register the returned tool(s) after ``self.kernel = AIKernel(...)``, not inside
    ``_build_tools()``).

    ``max_depth``/``deadline_s`` come from ``settings.reasoning.max_delegation_depth`` /
    ``delegation_deadline_s`` so a deployment can tune them without a code change.

    With only the arguments above this returns the single synchronous ``delegate_to_agent`` it
    always did. ``tasks`` adds the ``background`` parameter and ``list_agent_tasks`` /
    ``get_agent_task`` / ``cancel_agent_task``. ``handoffs`` with ``structured_handoff=True`` makes
    every delegation carry a structured handoff (``checkpoint_provider`` is
    ``async (owner: UserId, session_id: str) -> checkpoint | None``) and adds ``get_handoff`` /
    ``list_handoffs``.
    """
    use_handoff = bool(structured_handoff and handoffs is not None)

    async def _make_handoff(
        *, context: ToolContext, target_spec: Any, subtask: str, handoff_id: str | None = None
    ) -> Any:
        """The stored handoff record for this delegation, or ``None``. Never fails the call: a
        handoff is context for the child, and a delegation without it is still a delegation."""
        try:
            checkpoint = None
            if checkpoint_provider is not None and context.session_id:
                try:
                    checkpoint = await checkpoint_provider(context.user_id, context.session_id)
                except Exception:
                    logger.warning("handoff: the parent checkpoint could not be read", exc_info=True)
            record = build_handoff(
                owner_id=context.user_id.value,
                from_agent=context.agent_id or "orchestrator",
                to_agent=target_spec.id, goal=subtask, checkpoint=checkpoint,
                parent_session_id=context.session_id,
                delegation_depth=context.delegation_depth + 1,
                handoff_id=handoff_id,
            )
            await handoffs.put(record)
            return record
        except Exception:
            logger.warning("handoff: could not build or store the record", exc_info=True)
            return None

    parameters = [
        param(
            "target_agent_id", "string", "The persona to delegate to.",
            # Placeholder: _LiveTargetsTool.spec replaces it with the registry's current ids.
            enum=["orchestrator"],
        ),
        param(
            "subtask", "string",
            "A self-contained description of the sub-task, including any facts the target "
            "persona needs -- it will not see anything else.",
            max_length=4_000,
        ),
    ]
    description = (
        "Hand off a self-contained sub-task to a specialist persona and return its answer. "
        "The target persona sees only the `subtask` text, not the ongoing conversation -- write "
        "it as a standalone instruction with all the context it needs. Does not support a "
        "sub-task whose plan would need the user's own confirmation (e.g. one that logs an "
        "expense or creates a task): such a call fails cleanly rather than silently dropping the "
        "pending confirmation, so prefer proposing that action directly instead of delegating it."
    )
    if use_handoff:
        description = description.replace(
            "The target persona sees only the `subtask` text, not the ongoing conversation -- "
            "write it as a standalone instruction with all the context it needs.",
            "The target persona sees the `subtask` text plus a short structured summary of where "
            "this conversation stands (decisions, finished work, blockers, next steps); write the "
            "subtask as a standalone instruction anyway.",
        )
    if tasks is not None:
        parameters.append(param(
            "background", "boolean",
            "true to start the sub-task in the background and return an `agent_task_id` at once "
            "instead of waiting for the answer; read it later with get_agent_task. Use for slow, "
            "independent work.",
            required=False, default=False,
        ))
        description += (
            " With background=true it returns immediately with an agent_task_id; use "
            "get_agent_task to read the result, list_agent_tasks to see running ones and "
            "cancel_agent_task to stop one."
        )

    @tool(
        "delegate_to_agent",
        description,
        *parameters,
        category=ToolCategory.SYSTEM,
        sensitivity=ToolSensitivity.SYSTEM,
        # Not side_effecting: the tool itself never mutates anything directly, and a sub-turn
        # that tries to (any plan step needing confirmation) is refused below rather than run --
        # so this call can never produce an uncommunicated side effect. Marking it side_effecting
        # anyway would auto-set requires_confirmation=True (ToolSpec.__post_init__, since this
        # tool's own sensitivity is SYSTEM), turning every delegation into a confirmation prompt
        # and defeating the point of orchestrator delegating transparently within its own plan.
        timeout_s=deadline_s,
        # The sub-turn's answer may reflect tainted retrieval/tool output the sub-persona itself
        # pulled in (e.g. research_agent's fetch_url) -- same reasoning as fetch_url's own tag.
        # NESTED_DISPATCH_TAG: the sub-turn dispatches its own tool calls through the registry, so
        # this call must not hold one of the registry's concurrency slots while it waits for them.
        tags=("untrusted_content", NESTED_DISPATCH_TAG),
    )
    async def delegate_to_agent(
        target_agent_id: str, subtask: str, context: ToolContext, background: bool = False,
    ) -> str:
        if context.delegation_depth >= max_depth:
            raise ToolExecutionError(
                f"delegation depth exceeded (max {max_depth})",
                tool="delegate_to_agent",
            )

        if background and tasks is None:
            raise ToolExecutionError(
                "background delegation is not enabled on this deployment", tool="delegate_to_agent",
            )

        if target_agent_id not in _target_ids(agents):
            # Checked before resolve(): its fallback would turn an unknown id into an
            # orchestrator sub-turn, and the mismatch below would misreport it as a missing
            # capability. The registry's schema check normally refuses this first.
            raise ToolExecutionError(
                f"cannot delegate to '{target_agent_id}': unknown agent",
                tool="delegate_to_agent",
            )

        target_spec = agents.resolve(target_agent_id, capabilities=context.capabilities)
        if target_spec.id != target_agent_id:
            # AgentRegistry.resolve() silently falls back to orchestrator when the caller's
            # capabilities don't satisfy the target's required_capabilities (registry.py's own
            # documented anti-escalation behaviour). Delegation must not inherit that silently --
            # a caller asking for `engineer` without the `engineer_agent` capability should see a
            # clear refusal, not an unexplained orchestrator sub-turn.
            raise ToolExecutionError(
                f"cannot delegate to '{target_agent_id}': missing required capability",
                tool="delegate_to_agent",
            )

        # Names and prefixes together (audit T-03): the engineer's `mcp_` prefix reaches the
        # child only when the caller holds it too, so delegating to the engineer never hands
        # MCP tools to a caller that could not call them itself.
        child_tools, child_prefixes = intersect_tool_grants(
            context.allowed_tools, context.allowed_tool_prefixes,
            target_spec.allowed_tools, target_spec.allowed_tool_prefixes,
        )

        caller_ceiling = context.max_sensitivity
        child_ceiling = (
            target_spec.risk_ceiling if caller_ceiling is None
            else min(caller_ceiling, target_spec.risk_ceiling)
        )

        # Deferred: kernel/kernel.py is another engine module, imported lazily here rather than
        # at module scope so this module never depends on import order relative to it.
        from ..kernel.kernel import TurnRequest

        child_request = TurnRequest(
            message=subtask,
            user_id=context.user_id,
            knowledge_owner=context.knowledge_owner,
            session_id=None,  # stateless sub-turn: the target sees only `subtask`, nothing else
            agent=target_spec.id,
            correlation_id=CorrelationId(f"{context.correlation_id}:{uuid4().hex[:8]}"),
            capabilities=context.capabilities,
            delegation_depth=context.delegation_depth + 1,
            delegated_tool_grant=child_tools,
            delegated_tool_prefixes=child_prefixes,
            delegated_risk_ceiling=child_ceiling,
            # ADR-006 across the hop: a tainted parent produces a tainted child, so a persona
            # whose CRUD tools would run unconfirmed on clean input still needs confirmation
            # here -- which this tool then refuses below rather than silently dropping.
            inherited_taint=context.tainted,
            # WP8: a delegated sub-turn acts for the same device as its parent, so a phone cannot
            # reach the desktop unheld by going through the orchestrator.
            origin=(context.metadata or {}).get("origin"),
            # The same person's delegated-connector tokens (AP10): a sub-turn reads as its caller.
            connector_tokens={key.removeprefix(CONNECTOR_TOKEN_PREFIX): token
                              for key, token in context.credentials.items()
                              if key.startswith(CONNECTOR_TOKEN_PREFIX)},
        )

        def with_handoff(request: Any, record: Any) -> Any:
            # DATA channel, as context: fenced with the child turn's nonce and put through the
            # context guards like a retrieved chunk. Never `message` (USER) and never the system
            # prompt. Kept off `caller_context` itself, which taints a turn by being present: the
            # child already inherits its parent's taint above, and the kernel taints it further if
            # the guards quarantine this block.
            return dataclasses.replace(
                request,
                handoff_context=(
                    CallerContext.of(
                        id=f"handoff:{record.id}", text=render_handoff(record), source="handoff",
                    ),
                ),
            )

        handoff = None
        # A background call hands its id out at once, so the record -- which may need a model call
        # for the parent's checkpoint -- is built inside the task, not before the tool returns.
        preassigned = new_handoff_id() if (use_handoff and background) else None
        if use_handoff and not background:
            handoff = await _make_handoff(context=context, target_spec=target_spec, subtask=subtask)
            if handoff is not None:
                child_request = with_handoff(child_request, handoff)

        owner = context.user_id.value

        async def finish(result: Any, *, error: str | None = None) -> None:
            known = handoff_ref[0]
            if known is not None:
                await _record_outcome(handoffs, owner, known.id, result, error=error)

        handoff_ref: list[Any] = [handoff]

        def nested_refusal(result: Any) -> str:
            pending = ", ".join(result.needs_confirmation) or "a pending action"
            return (
                f"{target_spec.name} needs the user's own confirmation for: {pending}; "
                "delegation does not support nested confirmation"
            )

        if background:
            async def runner() -> TaskResult:
                request = child_request
                if preassigned is not None:
                    record = await _make_handoff(
                        context=context, target_spec=target_spec, subtask=subtask, handoff_id=preassigned,
                    )
                    if record is not None:
                        handoff_ref[0] = record
                        request = with_handoff(request, record)
                try:
                    result = await kernel().run(request)
                except asyncio.CancelledError:
                    if handoff_ref[0] is not None:
                        await _record_cancelled(handoffs, owner, handoff_ref[0].id)
                    raise
                except Exception as exc:
                    await finish(None, error=str(getattr(exc, "message", None) or type(exc).__name__))
                    raise
                if result.needs_confirmation or result.pending_tool_calls:
                    refusal = nested_refusal(result)
                    await finish(result, error=refusal)
                    return TaskResult(ok=False, error=refusal)
                failure = _turn_failure(result)
                if failure is not None:
                    await finish(result, error=failure)
                    return TaskResult(ok=False, error=failure)
                await finish(result)
                return TaskResult(ok=True, text=result.text)

            try:
                task = await tasks.submit(
                    owner_id=owner, agent_id=target_spec.id, subtask=subtask, runner=runner,
                    handoff_id=preassigned,
                )
            except AgentTaskLimitError as exc:
                raise ToolExecutionError(str(exc), tool="delegate_to_agent") from exc
            return json.dumps({
                "agent_task_id": task.id, "status": task.status, "agent": task.agent_id,
                **({"handoff_id": preassigned} if preassigned is not None else {}),
                "next": "call get_agent_task with this id to read the result when it is done",
            })

        try:
            result = await kernel().run(child_request)
        except Exception as exc:
            await finish(None, error=str(getattr(exc, "message", None) or type(exc).__name__))
            raise

        if result.needs_confirmation or result.pending_tool_calls:
            refusal = nested_refusal(result)
            await finish(result, error=refusal)
            raise ToolExecutionError(refusal, tool="delegate_to_agent")
        await finish(result)
        return result.text

    out: list[Tool] = [_LiveTargetsTool(delegate_to_agent, agents)]
    if tasks is not None:
        out += _build_task_tools(tasks, handoffs)
    if use_handoff:
        out += _build_handoff_tools(handoffs)
    return out


def _turn_failure(result: Any) -> str | None:
    """Why a finished child turn did not produce an answer, or ``None`` when it did. Only the
    background path asks: a synchronous caller has always been handed ``result.text`` as-is."""
    telemetry = getattr(result, "telemetry", None)
    outcome = getattr(getattr(telemetry, "outcome", None), "value", None)
    if outcome is None or outcome == "success":
        return None
    return f"the sub-turn did not complete ({getattr(telemetry, 'error_kind', None) or outcome})"


async def _record_outcome(
    handoffs: HandoffStore, owner: str, handoff_id: str, result: Any, *, error: str | None = None
) -> None:
    try:
        await handoffs.record_outcome(owner, outcome_from_result(handoff_id, result, error=error))
    except Exception:
        logger.warning("handoff: could not store the outcome", exc_info=True)


async def _record_cancelled(handoffs: HandoffStore, owner: str, handoff_id: str) -> None:
    try:
        await handoffs.record_outcome(owner, outcome_from_result(handoff_id, None, cancelled=True))
    except Exception:
        logger.warning("handoff: could not store the cancelled outcome", exc_info=True)


def _task_view(task: Any, outcome: Any = None) -> dict[str, Any]:
    view = {
        "agent_task_id": task.id, "agent": task.agent_id, "status": task.status,
        "subtask": task.subtask, "created_at": task.created_at, "finished_at": task.finished_at,
        "cancel_requested": task.cancel_requested,
    }
    if task.result is not None:
        view["result"] = task.result
    if task.error:
        view["error"] = task.error
    if task.handoff_id:
        view["handoff_id"] = task.handoff_id
    if outcome is not None:
        view["handoff_outcome"] = outcome.as_dict()
    return view


def _build_task_tools(tasks: AgentTaskManager, handoffs: HandoffStore | None) -> list[Tool]:
    """``list_agent_tasks`` / ``get_agent_task`` / ``cancel_agent_task``, owner-scoped by
    ``context.user_id`` inside the manager -- a task id is never a capability."""

    @tool(
        "list_agent_tasks",
        "List your background agent tasks (started with delegate_to_agent background=true), "
        "newest first, with their status: running, succeeded, failed or cancelled.",
        param("limit", "integer", "Maximum tasks to return (1-20).", required=False,
              minimum=1, maximum=20, default=10),
        category=ToolCategory.SYSTEM,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=10.0,
    )
    async def list_agent_tasks(context: ToolContext, limit: int = 10) -> str:
        found = await tasks.list(context.user_id.value, limit=limit)
        return json.dumps({"tasks": [
            {k: v for k, v in _task_view(t).items() if k != "result"} for t in found
        ]})

    @tool(
        "get_agent_task",
        "Read one background agent task: its status and, once it has succeeded, its result. "
        "While it is still running, say so and carry on rather than waiting.",
        param("agent_task_id", "string", "The id delegate_to_agent returned.", max_length=64),
        category=ToolCategory.SYSTEM,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=10.0,
        # The result is what a sub-persona answered, possibly from untrusted retrieval or tool
        # output it pulled in: reading it taints the turn exactly as `delegate_to_agent` does.
        tags=("untrusted_content",),
    )
    async def get_agent_task(agent_task_id: str, context: ToolContext) -> str:
        task = await tasks.get(context.user_id.value, agent_task_id)
        if task is None:
            raise ToolExecutionError(f"no agent task {agent_task_id!r}", tool="get_agent_task")
        outcome = None
        if handoffs is not None and task.handoff_id:
            outcome = await handoffs.outcome(context.user_id.value, task.handoff_id)
        return json.dumps(_task_view(task, outcome))

    @tool(
        "cancel_agent_task",
        "Stop a background agent task that is still running. Has no effect on a finished one.",
        param("agent_task_id", "string", "The id delegate_to_agent returned.", max_length=64),
        category=ToolCategory.SYSTEM,
        sensitivity=ToolSensitivity.PERSONAL,
        # Changes state (stops work the user asked for), so in a turn whose context was
        # quarantined it waits for confirmation (ADR-006), like any other side effect.
        side_effecting=True,
        timeout_s=15.0,
    )
    async def cancel_agent_task(agent_task_id: str, context: ToolContext) -> str:
        task = await tasks.cancel(context.user_id.value, agent_task_id)
        if task is None:
            raise ToolExecutionError(f"no agent task {agent_task_id!r}", tool="cancel_agent_task")
        return json.dumps(_task_view(task))

    return [list_agent_tasks, get_agent_task, cancel_agent_task]


def _build_handoff_tools(handoffs: HandoffStore) -> list[Tool]:
    """``get_handoff`` / ``list_handoffs``: the structured record of what was handed to a child and
    what came back."""

    @tool(
        "get_handoff",
        "Read one handoff made by delegate_to_agent: what was handed over (goal, decisions, "
        "finished work, blockers, pending steps, files) and the child's structured outcome.",
        param("handoff_id", "string", "A handoff id (ho_...) from delegate_to_agent or get_agent_task.",
              max_length=64),
        category=ToolCategory.SYSTEM,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=10.0,
        # The outcome carries the child's answer text.
        tags=("untrusted_content",),
    )
    async def get_handoff(handoff_id: str, context: ToolContext) -> str:
        owner = context.user_id.value
        record = await handoffs.get(owner, handoff_id)
        if record is None:
            raise ToolExecutionError(f"no handoff {handoff_id!r}", tool="get_handoff")
        outcome = await handoffs.outcome(owner, handoff_id)
        return json.dumps({
            "handoff": {k: v for k, v in record.as_dict().items() if k != "owner_id"},
            "outcome": outcome.as_dict() if outcome is not None else None,
        })

    @tool(
        "list_handoffs",
        "List your recent delegation handoffs, newest first, with each child's outcome status.",
        param("limit", "integer", "Maximum handoffs to return (1-20).", required=False,
              minimum=1, maximum=20, default=10),
        category=ToolCategory.SYSTEM,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=10.0,
    )
    async def list_handoffs(context: ToolContext, limit: int = 10) -> str:
        found = await handoffs.recent(context.user_id.value, limit=limit)
        return json.dumps({"handoffs": [
            {
                "id": record.id, "to_agent": record.to_agent, "goal": record.goal,
                "created_at": record.created_at,
                "status": outcome.status if outcome is not None else "in_progress",
            }
            for record, outcome in found
        ]})

    return [get_handoff, list_handoffs]


def _target_ids(agents: AgentRegistry) -> tuple[str, ...]:
    return tuple(sorted(spec.id for spec in agents if spec.id != "orchestrator"))


class _LiveTargetsTool:
    """``delegate_to_agent`` with a ``target_agent_id`` enum read from the registry on demand.

    The registry is not fixed at boot: with Track AP on it is an ``ActiveBlueprint`` whose
    snapshot a blueprint deploy swaps (``adaptation/runtime.py``). An enum computed once here
    would offer the model, and validate against, the boot-time personas only, so a generated
    ``bp_*`` agent could never be a target (audit 2026-11-16, F6). ``ToolRegistry`` reads
    ``tool.spec`` on every listing and every call, so recomputing it here is enough; the spec is
    cached per target set, so an unchanged registry yields the identical object.
    """

    def __init__(self, inner: Any, agents: AgentRegistry) -> None:
        self._inner = inner
        self._agents = agents
        self._cached: tuple[tuple[str, ...], Any] | None = None

    @property
    def spec(self):
        ids = _target_ids(self._agents)
        if self._cached is None or self._cached[0] != ids:
            base = self._inner.spec
            parameters = tuple(
                dataclasses.replace(p, enum=ids) if p.name == "target_agent_id" else p
                for p in base.parameters
            )
            self._cached = (ids, dataclasses.replace(base, parameters=parameters))
        return self._cached[1]

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> str:
        return await self._inner.execute(arguments, context)
