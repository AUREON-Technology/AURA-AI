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

from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from ..agents.registry import AgentRegistry
from ..domain.errors import ToolExecutionError
from ..domain.identifiers import CorrelationId
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .base import param, tool

if TYPE_CHECKING:
    from ..kernel.kernel import AIKernel

__all__ = ["build_delegation_tools"]


def build_delegation_tools(
    *,
    agents: AgentRegistry,
    kernel: Callable[[], "AIKernel"],
    max_depth: int = 1,
    deadline_s: float = 180.0,
) -> list[Tool]:
    """Build the `delegate_to_agent` tool.

    ``kernel`` is a zero-arg resolver rather than a live ``AIKernel`` instance, because
    ``Container._build_tools()`` runs before ``self.kernel`` is constructed in
    ``Container.__init__`` -- the same reason ``Container._build_github_client_resolver()``
    resolves lazily rather than closing over a client built too early. Call this only once the
    kernel itself is guaranteed to exist by the time a request actually reaches the handler
    (i.e. register the returned tool(s) after ``self.kernel = AIKernel(...)``, not inside
    ``_build_tools()``).

    ``max_depth``/``deadline_s`` come from ``settings.reasoning.max_delegation_depth`` /
    ``delegation_deadline_s`` so a deployment can tune them without a code change.
    """
    target_ids = tuple(sorted(spec.id for spec in agents if spec.id != "orchestrator"))

    @tool(
        "delegate_to_agent",
        "Hand off a self-contained sub-task to a specialist persona and return its answer. "
        "The target persona sees only the `subtask` text, not the ongoing conversation -- write "
        "it as a standalone instruction with all the context it needs. Does not support a "
        "sub-task whose plan would need the user's own confirmation (e.g. one that logs an "
        "expense or creates a task): such a call fails cleanly rather than silently dropping the "
        "pending confirmation, so prefer proposing that action directly instead of delegating it.",
        param(
            "target_agent_id", "string", "The persona to delegate to.",
            enum=list(target_ids),
        ),
        param(
            "subtask", "string",
            "A self-contained description of the sub-task, including any facts the target "
            "persona needs -- it will not see anything else.",
            max_length=4_000,
        ),
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
        tags=("untrusted_content",),
    )
    async def delegate_to_agent(target_agent_id: str, subtask: str, context: ToolContext) -> str:
        if context.delegation_depth >= max_depth:
            raise ToolExecutionError(
                f"delegation depth exceeded (max {max_depth})",
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

        caller_tools = context.allowed_tools
        if caller_tools is None:
            child_tools = target_spec.allowed_tools
        elif target_spec.allowed_tools is None:
            child_tools = caller_tools
        else:
            child_tools = caller_tools & target_spec.allowed_tools

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
            session_id=None,  # stateless sub-turn: the target sees only `subtask`, nothing else
            agent=target_spec.id,
            correlation_id=CorrelationId(f"{context.correlation_id}:{uuid4().hex[:8]}"),
            capabilities=context.capabilities,
            delegation_depth=context.delegation_depth + 1,
            delegated_tool_grant=child_tools,
            delegated_risk_ceiling=child_ceiling,
        )

        result = await kernel().run(child_request)

        if result.needs_confirmation or result.pending_tool_calls:
            pending = ", ".join(result.needs_confirmation) or "a pending action"
            raise ToolExecutionError(
                f"{target_spec.name} needs the user's own confirmation for: {pending}; "
                "delegation does not support nested confirmation",
                tool="delegate_to_agent",
            )
        return result.text

    return [delegate_to_agent]
