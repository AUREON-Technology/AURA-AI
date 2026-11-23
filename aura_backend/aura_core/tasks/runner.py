"""Running a task: the owner's stored request, through the same kernel path as ``/chat`` (ADR-041).

:func:`turn_to_spec` / :func:`spec_to_turn` carry a validated :class:`TurnRequest` across the wait
between acceptance and execution. They carry exactly what the gateway built at submission --
identity, capability grant, persona, flags, evidence -- so a task runs *as the person who
submitted it, with what they held then*; nothing is looked up again or widened. What they
deliberately do not carry, because it is per-request and must not be stored:

* connector sign-ins (``X-Aura-Connector-Token``): a background task has no live caller to hold
  them, so it cannot act with a person's delegated connectors;
* images and ``response_schema``: not accepted on the task route at all.

:class:`KernelTaskRunner` runs the turn with ``kernel.run`` -- the only execution path -- and maps
its result to a :class:`TaskOutcome`, reading failures exactly as ``POST /chat`` does (the same
error kinds, the same retryability).
"""

from __future__ import annotations

from typing import Any, Mapping

from ..domain.caller_context import CallerContext
from ..domain.identifiers import CorrelationId, SessionId, UserId
from ..domain.llm import RoutingPolicy
from ..domain.reasoning import ReasoningStrategy
from ..kernel.kernel import TurnRequest
from ..observability.logging import get_logger
from .models import AgentTask, TaskOutcome, TaskStatus

__all__ = ["KernelTaskRunner", "turn_to_spec", "spec_to_turn", "SPEC_VERSION"]

logger = get_logger("tasks.runner")

SPEC_VERSION = 1
#: The answer kept on the task record, and the short form clients show in a list.
MAX_RESULT_CHARS = 16_000
SUMMARY_CHARS = 280


def turn_to_spec(turn: TurnRequest) -> dict[str, Any]:
    return {
        "v": SPEC_VERSION,
        "message": turn.message,
        "user_id": turn.user_id.value,
        "knowledge_owner": turn.knowledge_owner.value if turn.knowledge_owner else None,
        "session_id": turn.session_id.value if turn.session_id else None,
        "agent": turn.agent,
        "use_knowledge": turn.use_knowledge,
        "use_memory": turn.use_memory,
        "use_tools": turn.use_tools,
        "plan": turn.plan,
        "strategy": turn.strategy.value if turn.strategy else None,
        "routing_policy": turn.routing_policy.value if turn.routing_policy else None,
        "require_local": turn.require_local,
        "capabilities": list(turn.capabilities),
        "approval_ids": list(turn.approval_ids),
        "max_output_tokens": turn.max_output_tokens,
        "temperature": turn.temperature,
        "desktop_task_isolation": turn.desktop_task_isolation,
        "workspace_root": turn.workspace_root,
        "origin": turn.origin,
        "delegated_tool_grant": (
            sorted(turn.delegated_tool_grant) if turn.delegated_tool_grant is not None else None
        ),
        "caller_context": [
            {"id": c.id, "text": c.text.raw(), "source": c.source} for c in turn.caller_context
        ],
        "preferred_language": turn.preferred_language,
    }


def spec_to_turn(spec: Mapping[str, Any], *, task: AgentTask) -> TurnRequest:
    grant = spec.get("delegated_tool_grant")
    return TurnRequest(
        message=str(spec["message"]),
        user_id=UserId(str(spec["user_id"])),
        knowledge_owner=UserId(spec["knowledge_owner"]) if spec.get("knowledge_owner") else None,
        session_id=SessionId(spec["session_id"]) if spec.get("session_id") else None,
        agent=str(spec.get("agent") or "orchestrator"),
        correlation_id=CorrelationId(task.correlation_id) if task.correlation_id else None,
        use_knowledge=bool(spec.get("use_knowledge", True)),
        use_memory=bool(spec.get("use_memory", True)),
        use_tools=bool(spec.get("use_tools", True)),
        plan=spec.get("plan"),
        strategy=ReasoningStrategy(spec["strategy"]) if spec.get("strategy") else None,
        routing_policy=RoutingPolicy(spec["routing_policy"]) if spec.get("routing_policy") else None,
        require_local=bool(spec.get("require_local", False)),
        capabilities=tuple(spec.get("capabilities") or ()),
        approval_ids=tuple(spec.get("approval_ids") or ()),
        max_output_tokens=int(spec.get("max_output_tokens") or 1024),
        temperature=float(spec.get("temperature") if spec.get("temperature") is not None else 0.3),
        desktop_task_isolation=bool(spec.get("desktop_task_isolation", False)),
        workspace_root=spec.get("workspace_root"),
        origin=spec.get("origin"),
        delegated_tool_grant=frozenset(grant) if grant is not None else None,
        caller_context=tuple(
            CallerContext.of(id=c["id"], text=c["text"], source=c.get("source", ""))
            for c in spec.get("caller_context") or ()
        ),
        preferred_language=spec.get("preferred_language"),
        task_id=task.id,
    )


class KernelTaskRunner:
    def __init__(self, kernel: Any) -> None:
        self._kernel = kernel

    async def __call__(self, task: AgentTask, spec: Mapping[str, Any]) -> TaskOutcome:
        try:
            turn = spec_to_turn(spec, task=task)
        except Exception as exc:
            logger.warning("a task's stored request is unusable", exc_info=True)
            return TaskOutcome(
                status=TaskStatus.FAILED, release_reservation=True,
                error={"kind": "task_request_invalid", "retryable": False, "details": {},
                       "message": f"the stored request could not be rebuilt ({type(exc).__name__})"},
            )
        result = await self._kernel.run(turn)
        telemetry = result.telemetry
        turn_id = telemetry.turn_id.value
        outcome_kind = telemetry.outcome.value

        if outcome_kind != "success":
            kind = telemetry.error_kind or "turn_failed"
            # Read exactly as POST /chat reads it: a saturated pool or a rate limit is worth
            # trying again; a refusal that needs the person to act first (consent, today's
            # allowance, a stale approval) is not.
            status = {"blocked": 400, "budget_exceeded": 429, "rate_limited": 429}.get(outcome_kind, 503)
            retryable = status in (429, 503) and kind not in (
                "consent_required", "user_quota_exhausted", "confirmation_invalid", "output_invalid",
            )
            details = dict(result.error_details or {})
            if result.retry_after_s:
                details["retry_after"] = result.retry_after_s
            return TaskOutcome(
                status=TaskStatus.FAILED, turn_id=turn_id, release_reservation=True,
                error={"kind": kind, "message": "The turn did not complete.",
                       "retryable": retryable, "details": details},
            )

        text = result.text or ""
        pending = [dict(p) for p in result.pending_tool_calls]
        body = {
            "message": text[:MAX_RESULT_CHARS],
            "summary": " ".join(text.split())[:SUMMARY_CHARS],
            "truncated": len(text) > MAX_RESULT_CHARS,
            "citations": list(result.citations),
            "tools_used": list(result.tool_calls),
            "needs_confirmation": list(result.needs_confirmation),
            "pending_tool_calls": pending,
            "plan": [dict(p) for p in result.plan_summary],
            "usage": {
                "prompt_tokens": telemetry.prompt_tokens,
                "completion_tokens": telemetry.completion_tokens,
                "cost_usd": round(telemetry.cost_usd, 6),
            },
            "provider": telemetry.provider,
            "model": telemetry.model,
            "degraded": result.degraded,
            "resolved_agent": result.resolved_agent,
            "agent_run_id": result.agent_run_id,
        }
        return TaskOutcome(
            status=TaskStatus.AWAITING_APPROVAL if pending else TaskStatus.SUCCEEDED,
            result=body, run_id=result.agent_run_id, turn_id=turn_id,
        )
