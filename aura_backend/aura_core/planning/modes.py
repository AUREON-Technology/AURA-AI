"""Planning modes: ``auto | plan | fast | review`` (ADR-040, inventory row 63).

Until now ``/chat`` had one dial -- ``plan: bool | None`` -- that no client ever sent, and a
heuristic behind ``None`` that the Engineer persona overrode by always planning. This module turns
that dial into four named modes a person can choose, and makes the choice visible in the response:

* ``plan``   -- plan first, then run the plan (what ``plan=True`` always meant).
* ``fast``   -- do not plan. A read-only turn is answered directly. If the request *asks for a
  side effect* and the deployment offers a tool that has one, ``fast`` does not silently drop it:
  it escalates to ``plan`` (and the response says so), because an answer that says "done" when no
  tool ran is the failure ``PLANNING_FAILED_NOTE`` exists for. (Only for an explicit ``plan_mode``;
  the legacy ``plan: false`` keeps meaning "never plan", unchanged.)
* ``auto``   -- decide per turn: plan when the persona runs an agent loop, when the existing
  multi-step heuristics fire, when the message scores as complex, or when it asks for a side effect
  that a tool could perform; otherwise answer directly.
* ``review`` -- plan, then **stop**: the planned steps go back to the user, and nothing runs until
  they approve (``kernel/kernel.py``, through the existing confirmation store).

No mode requested and no ``AURA_REASONING_DEFAULT_PLAN_MODE``: the decision is today's, bit for
bit (``mode="default"``). ``plan: true`` / ``plan: false`` keep working and map to ``plan`` /
``fast`` -- with exactly their old effect. Stdlib only; the model is never asked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["PLAN_MODES", "PlanDecision", "decide_plan", "has_action_intent", "normalise_mode"]

PLAN_MODES = ("auto", "plan", "fast", "review")

# Verbs that ask for something to be *done* rather than answered. Whole words, English and Arabic.
# Deliberately about intent only: whether a tool exists for it is a separate check.
_ACTION = re.compile(
    r"\b(?:create|add|delete|remove|update|edit|write|rewrite|send|email|post|push|pull|commit|merge|"
    r"deploy|run|execute|install|uninstall|rename|move|schedule|book|pay|buy|log|save|apply|fix|"
    r"refactor|migrate|drop|reset|publish|clone|checkout|open|close|cancel|set|change|generate|"
    r"upload|download|build|test|revert|rebase|tag|release|archive|clear|wipe)\b"
    r"|(?:اعمل|أنشئ|انشئ|امسح|احذف|عدل|عدّل|ابعت|ارسل|أرسل|شغل|شغّل|نفذ|نفّذ|ضيف|أضف|اضف|حدث|حدّث|"
    r"ادفع|احجز|سجل|سجّل|احفظ|ارفع|نزل|الغ|ألغ|غير|غيّر|ابني|اصلح|أصلح)",
    re.I,
)
# A question about the thing is not a request to do it: "how do I delete a branch?" is read-only.
_QUESTION_LEAD = re.compile(
    r"^\s*(?:how|what|why|when|where|which|who|can you explain|could you explain|explain|"
    r"is it|are there|does|do you|did|should i|كيف|ايه|إيه|ليه|لماذا|متى|فين|مين|هل)\b", re.I
)


def has_action_intent(message: str) -> bool:
    """Whether ``message`` reads as a request to *do* something (create, send, delete, run ...)."""
    text = (message or "").strip()
    if not text or _QUESTION_LEAD.match(text):
        return False
    return bool(_ACTION.search(text))


def normalise_mode(value: Any) -> str | None:
    """A recognised mode name, or ``None`` (absent or unknown -- never an error here; the API
    layer is what rejects an unknown value)."""
    mode = str(value).strip().lower() if isinstance(value, str) else ""
    return mode if mode in PLAN_MODES else None


@dataclass(frozen=True, slots=True)
class PlanDecision:
    """What the kernel does about planning this turn, and why -- also what the client is shown."""

    #: Whether to plan (and, unless ``review``, execute) this turn.
    plan: bool
    #: ``default`` (no mode asked for: today's behaviour) or the mode in force.
    mode: str
    #: What the caller asked for: a mode name, ``"plan: true"`` / ``"plan: false"`` for the legacy
    #: flag, or ``None``.
    requested: str | None
    #: A short, stable slug.
    reason: str
    #: Plan, then hold for the user's approval instead of executing.
    review: bool = False
    #: The requested mode when the decision moved off it (``fast`` -> ``plan``).
    escalated_from: str | None = None

    def as_dict(self, **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {
            "requested": self.requested, "effective": self.mode, "reason": self.reason,
            "planned": self.plan,
        }
        if self.escalated_from:
            out["escalated_from"] = self.escalated_from
        out.update(extra)
        return out


def decide_plan(
    *,
    requested_mode: str | None,
    plan_flag: bool | None,
    default_mode: str,
    message: str,
    side_effecting_tools: bool,
    agentic_persona: bool,
    heuristic: Callable[[bool | None], bool],
    complex_message: bool,
) -> PlanDecision:
    """The planning decision for one turn.

    ``heuristic(force)`` is ``PlanningEngine.needs_plan`` for this message and tool set (so it also
    answers "is planning enabled at all"); ``side_effecting_tools`` is whether any offered tool has
    a side effect; ``agentic_persona`` is whether the persona runs the replanning loop (the
    Engineer); ``complex_message`` is the ADR-033 complexity score against its threshold.
    """
    mode = normalise_mode(requested_mode)
    if mode is not None:
        return _by_mode(mode, mode, message, side_effecting_tools, agentic_persona, heuristic,
                        complex_message, escalate_fast=True)
    if plan_flag is not None:
        # The old flag, kept to the letter: ``needs_plan(force=flag)``. ``true`` is ``plan``,
        # ``false`` is ``fast`` without the escalation (it always meant "never plan").
        label = f"plan: {'true' if plan_flag else 'false'}"
        return PlanDecision(
            plan=heuristic(plan_flag), mode="plan" if plan_flag else "fast", requested=label,
            reason="requested" if plan_flag else "requested_no_planning",
        )
    default = normalise_mode(default_mode)
    if default is not None:
        return _by_mode(default, None, message, side_effecting_tools, agentic_persona, heuristic,
                        complex_message, escalate_fast=True)
    # Today's behaviour, unchanged: the agent-loop persona plans, everyone else by heuristic.
    force: bool | None = True if agentic_persona else None
    return PlanDecision(
        plan=heuristic(force), mode="default", requested=None,
        reason="agentic_persona" if agentic_persona else "heuristics",
    )


def _by_mode(
    mode: str, requested: str | None, message: str, side_effecting_tools: bool,
    agentic_persona: bool, heuristic: Callable[[bool | None], bool], complex_message: bool,
    *, escalate_fast: bool,
) -> PlanDecision:
    asks_for_action = side_effecting_tools and has_action_intent(message)
    if mode == "plan":
        return PlanDecision(plan=heuristic(True), mode="plan", requested=requested, reason="requested")
    if mode == "review":
        planned = heuristic(True)
        return PlanDecision(
            plan=planned, mode="review", requested=requested, review=planned,
            reason="requested" if planned else "planning_disabled",
        )
    if mode == "fast":
        if escalate_fast and asks_for_action:
            return PlanDecision(
                plan=heuristic(True), mode="plan", requested=requested,
                reason="side_effecting_request", escalated_from="fast",
            )
        return PlanDecision(plan=False, mode="fast", requested=requested, reason="read_only")
    # auto
    if agentic_persona:
        return PlanDecision(plan=heuristic(True), mode="auto", requested=requested, reason="agentic_persona")
    if heuristic(None):
        return PlanDecision(plan=True, mode="auto", requested=requested, reason="multi_step")
    if complex_message:
        return PlanDecision(plan=heuristic(True), mode="auto", requested=requested, reason="complexity")
    if asks_for_action:
        return PlanDecision(
            plan=heuristic(True), mode="auto", requested=requested, reason="side_effecting_request",
        )
    return PlanDecision(plan=False, mode="auto", requested=requested, reason="simple_read_only")
