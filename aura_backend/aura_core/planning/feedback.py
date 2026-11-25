"""Free-text feedback on a plan that is held for approval (ADR-047, row 51).

A ``review``-mode turn (ADR-040) stops at the gate: the plan is stored server-side and the client
gets ``approval_id`` s. Until now the user had two answers, approve (send the ids back) or walk
away. This lets them *talk to the plan*: "remove the docs step", "do the migration first", "I don't
like this, use the staging database". One feedback comment does exactly this, in order:

1. **Read it** with the step editor the ``apply_plan_feedback`` tool uses (so the tool and the
   gate never disagree about what a comment means): its intent, and what it does to the current
   steps when it names steps.
2. **Decide whether to touch the plan.** An approval with no change, or a question, leaves the
   held plan (and its approval ids) exactly as it was. Anything else is a revision.
3. **Replan once** through :meth:`PlanningEngine.replan` -- the planner writes the revised plan
   (real tools, arguments, dependencies), told the current plan, the person's words and, when the
   comment named steps, the outline those edits produce. Succeeded steps are kept, as in every
   replan. ``replan`` refuses once the plan has used its revision allowance, so feedback is
   bounded by ``AURA_PLANNING_MAX_REPLANS`` however often it is sent.
4. **Hold the revised plan** under *new* approval ids, then consume the old hold. The old ids stop
   working, so an approval given for the plan the person is no longer looking at cannot run it;
   if the replan fails the old hold is untouched and still approvable.

Owner- and session-scoped by the confirmation store (a stranger gets the same refusal as for a bad
approval), and audited: ``plan.feedback`` records who, which intent, and whether it changed
anything -- never the plan text or the comment (audit records hold no content).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..domain.errors import PlanningError, ValidationError
from ..domain.planning import Plan, StepKind, StepStatus
from ..execution.confirmations import PendingCall, PendingConfirmationStore
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent
from ..tools.open.artifact_feedback_ingestor import ArtifactFeedbackIngestor
from ..tools.open_pure import apply_step_feedback

__all__ = ["PlanFeedbackService", "PlanFeedbackResult", "MAX_FEEDBACK_CHARS"]

logger = get_logger("planning.feedback")

MAX_FEEDBACK_CHARS = 2_000
#: Steps shown to the planner as "the current plan"; a plan is capped well below this already.
_MAX_STEPS_SHOWN = 40


@dataclass(frozen=True, slots=True)
class PlanFeedbackResult:
    """What one comment did. ``pending_tool_calls`` carry the ids to approve *now*: the new ones
    after a revision, the original ones when the plan was left as it was."""

    intent: str
    revised: bool
    approved: bool
    plan_summary: tuple[Mapping[str, Any], ...]
    pending_tool_calls: tuple[Mapping[str, Any], ...] = ()
    actions: tuple[str, ...] = ()
    revision: int = 0
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent, "revised": self.revised, "approved": self.approved,
            "plan": [dict(s) for s in self.plan_summary],
            "pending_tool_calls": [dict(c) for c in self.pending_tool_calls],
            "actions": list(self.actions), "revision": self.revision, "note": self.note,
        }


def _summary(plan: Plan) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        {
            "id": step.id.value, "kind": str(step.kind), "status": str(step.status),
            "description": step.description,
            **({"tool": step.tool} if step.kind is StepKind.TOOL and step.tool else {}),
        }
        for step in plan.steps
    )


class PlanFeedbackService:
    """Revises a held plan from free-text feedback. Stateless over the confirmation store."""

    def __init__(
        self,
        *,
        confirmations: PendingConfirmationStore,
        planner: Callable[[], Any],
        audit: Any = None,
        clock: Any = None,
    ) -> None:
        self._confirmations = confirmations
        #: A callable, not the planner: the kernel's planner can be swapped after construction.
        self._planner = planner
        self._audit = audit
        self._clock = clock

    async def revise(
        self,
        *,
        approval_ids: Sequence[str],
        feedback: str,
        user_id: str,
        session_id: str | None,
        tools: Sequence[Mapping[str, Any]],
        correlation_id: str = "",
        turn_id: str | None = None,
    ) -> PlanFeedbackResult:
        text = (feedback or "").strip()
        if not text:
            raise ValidationError("feedback is empty", details={"field": "feedback"})
        if len(text) > MAX_FEEDBACK_CHARS:
            raise ValidationError("feedback is too long",
                                  details={"field": "feedback", "max_chars": MAX_FEEDBACK_CHARS})
        held = await self._confirmations.peek(approval_ids, user_id=user_id, session_id=session_id)
        plan = held.plan
        descriptions = [s.description for s in plan.steps][:_MAX_STEPS_SHOWN]
        intent = ArtifactFeedbackIngestor.classify_feedback_intent(text)["intent"]
        edited = apply_step_feedback(descriptions, text, len(plan.revisions))

        if (intent == "APPROVE" and not edited["changed"]) or intent == "CLARIFICATION_QUESTION":
            # Nothing to change: the held plan and its ids stay valid.
            note = ("Approved as written: send the approval_ids back to run it."
                    if intent == "APPROVE" else
                    "That reads as a question, not a change; the plan is unchanged and still awaiting approval.")
            await self._record(user_id, intent, False, correlation_id, len(plan.revisions))
            return PlanFeedbackResult(
                intent=intent, revised=False, approved=intent == "APPROVE",
                plan_summary=_summary(plan), pending_tool_calls=(),
                revision=len(plan.revisions), note=note, extra={"approval_ids": list(approval_ids)},
            )

        planner = self._planner()
        if planner is None:
            raise PlanningError("planning is not available on this deployment")
        lines = self._feedback_lines(descriptions, text, edited)
        # One replan per comment. PlanningError("replanning limit reached") propagates: the held
        # plan is untouched, the user can still approve it.
        revised = await planner.replan(
            plan, reason="user_feedback", available_tools=tools, feedback=lines,
        )

        fresh = [s for s in revised.steps if s.status is not StepStatus.SUCCEEDED]
        if len(fresh) == 1 and fresh[0].kind is StepKind.RESPOND:
            # The planner's own "I could not plan" fallback (a timeout, unusable JSON). Replacing
            # the person's plan with a bare "respond" step would silently discard it.
            raise PlanningError("the planner could not revise the plan; the original is unchanged "
                                "and still awaiting approval", details={"reason": "replan_fallback"})
        calls = [
            PendingCall(
                step_id=step.id.value, call_id=f"step_{step.id.value}", tool=str(step.tool),
                arguments=dict(step.arguments),
            )
            for step in revised.steps
            if step.kind is StepKind.TOOL and step.tool and step.status is not StepStatus.SUCCEEDED
        ]
        # Issue the new hold first, consume the old one second: if the consume loses a race (a
        # concurrent approval or feedback), the new hold is an orphan nobody holds the ids of and
        # simply expires; the reverse order could leave the person with no hold at all.
        issued = await self._confirmations.issue(
            user_id=user_id, session_id=session_id, agent=held.agent, message=held.message,
            plan=revised, tainted=held.tainted, calls=calls, turn_id=turn_id,
            workspace_root=held.workspace_root,
        )
        await self._confirmations.claim(approval_ids, user_id=user_id, session_id=session_id)
        pending = tuple(
            {
                "call_id": call.call_id, "tool": call.tool, "arguments": dict(call.arguments),
                "diff_preview": None, "approval_id": issued[call.call_id], "review": True,
            }
            for call in calls if call.call_id in issued
        )
        await self._record(user_id, intent, True, correlation_id, len(revised.revisions))
        return PlanFeedbackResult(
            intent=intent, revised=True, approved=False, plan_summary=_summary(revised),
            pending_tool_calls=pending, actions=tuple(edited["actions"][:20]),
            revision=len(revised.revisions),
            note="The plan was revised and is awaiting approval under new approval_ids; the old ones no longer work.",
        )

    @staticmethod
    def _feedback_lines(descriptions: list[str], feedback: str, edited: Mapping[str, Any]) -> list[str]:
        numbered = "\n".join(f"{i}. {d}" for i, d in enumerate(descriptions, start=1))
        lines = [
            f"The user reviewed the plan below before it ran and asked for a change.\n"
            f"Current plan:\n{numbered}",
            f"What the user said: {feedback!r}",
        ]
        if edited.get("changed"):
            outline = "\n".join(f"{i}. {d}" for i, d in enumerate(edited["revised_plan"], start=1))
            lines.append(
                "Applying the named edits literally gives this outline (rebuild it as a full plan "
                f"with the right tools and arguments):\n{outline}"
            )
        if edited.get("unhandled_clauses"):
            lines.append("These parts could not be applied mechanically, so use your judgement: "
                         + "; ".join(str(c) for c in edited["unhandled_clauses"][:5]))
        return lines

    async def _record(
        self, user_id: str, intent: str, revised: bool, correlation_id: str, revisions: int
    ) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(AuditEvent(
                action="plan.feedback", actor=user_id, outcome="revised" if revised else "unchanged",
                resource="plan:held", correlation_id=correlation_id,
                at=self._clock.now() if self._clock is not None else None,
                detail={"intent": intent, "revisions": revisions},
            ))
        except Exception:  # pragma: no cover - audit trouble never fails the user's request
            logger.warning("could not audit plan feedback", exc_info=True)
