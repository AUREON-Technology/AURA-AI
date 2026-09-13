"""Planning engine: goal decomposition, dependency graphs, replanning.

Nothing resembling this exists today -- the "agent" is a persona string plus a keyword ladder,
with no notion of a goal, a plan, or a step.

Three decisions shape this module:

**Planning is optional and cheap to skip.** Most turns are a question with an answer, and
decomposing "what's my leave balance?" into a five-step plan wastes a generation and adds a
second of latency. :meth:`PlanningEngine.needs_plan` decides, and the default answer is no.

**Plans are DAGs.** Independent steps ("check the calendar", "check the weather") run
concurrently; ordering is declared only where output genuinely feeds input. A list cannot express
that, and a false dependency costs real wall-clock time.

**Replanning is bounded and auditable.** Every revision records why it happened. An agent that
silently rewrites its plan on each failure is impossible to debug and can loop indefinitely, so
revisions are capped and counted.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping, Sequence

from ..domain.errors import PlanningError
from ..domain.identifiers import PlanId, StepId
from ..domain.llm import CompletionRequest, SamplingParams
from ..domain.messages import Conversation, Message
from ..domain.planning import Goal, Plan, PlanStep, Priority, StepKind, StepStatus
from ..observability.logging import get_logger
from ..tools.coding import matching_repository_tool
from ..tools.personal_os import matching_personal_os_tool

__all__ = ["PlanningEngine"]

logger = get_logger("planning")

# Signals that a request is multi-step. Deliberately narrow: a false positive costs a wasted
# planning generation on every simple question, which is the more common case by far.
_MULTI_STEP_MARKERS = (
    re.compile(r"\b(?:and then|after that|once you|followed by|finally)\b", re.I),
    re.compile(r"\b(?:first|step 1|step one)\b.{0,80}\b(?:then|second|next)\b", re.I | re.S),
    re.compile(r"\bcompare\b.{0,60}\b(?:and|with|versus|vs)\b", re.I),
    re.compile(r"\b(?:research|investigate|analyse|analyze|audit|review)\b.{0,60}"
               r"\b(?:then|and|before|after)\b", re.I),
    re.compile(r"\bfor each\b|\bevery one of\b", re.I),
)
_STEP_KINDS = {k.value for k in StepKind}


def _render_tool_line(tool: Mapping[str, Any]) -> str:
    """One prompt line per tool: name, its argument signature, then description.

    ``available_tools`` (``ToolSpec.declaration()``) already carries a full JSON-Schema
    ``parameters`` object -- built for provider tool-call payloads -- that this prompt used to
    discard entirely, showing the model only a name and a one-line description. A small,
    CPU-quantized model with no argument shape to go on has nothing to imitate for the
    ``"arguments": {}`` field ``task.plan_decomposition`` asks it to fill in, which is exactly
    the kind of gap that makes zero-shot JSON tool-calling unreliable: the model either avoids
    a tool step entirely or fills ``arguments`` with a guess ``_parse_steps``/the executor then
    rejects. ``*`` marks a required parameter, matching the note added to the prompt body.
    """
    name = tool.get("name", "")
    description = tool.get("description", "")
    properties = ((tool.get("parameters") or {}).get("properties")) or {}
    if not properties:
        return f"- {name}: {description}"
    required = set((tool.get("parameters") or {}).get("required") or ())
    args = ", ".join(
        f"{pname}{'*' if pname in required else ''}:{pschema.get('type', 'string')}"
        for pname, pschema in properties.items()
    )
    return f"- {name}({args}): {description}"


class PlanningEngine:
    def __init__(
        self,
        *,
        router: Any,
        prompts: Any,
        max_steps: int = 12,
        max_replans: int = 2,
        enabled: bool = True,
        metrics: Any = None,
        policy: Any = None,
        request_timeout_s: float = 60.0,
    ) -> None:
        self._router = router
        self._prompts = prompts
        self._max_steps = max_steps
        self._max_replans = max_replans
        self._enabled = enabled
        self._metrics = metrics
        self._policy = policy
        # Must match settings.llm.request_timeout_s (AURA_LLM_TIMEOUT_S), the same value
        # kernel.py's _completion_timeout_s() uses for chat completions. Left at
        # CompletionRequest's own 60s default, a cold prefill on a CPU-only provider silently
        # loses the router's only attempt to AllProvidersFailedError, and the caught exception
        # in plan() below downgrades every such turn to a tool-less single RESPOND step -- for
        # the engineer persona specifically, that means no tool call is ever considered.
        self._request_timeout_s = request_timeout_s

    # -- gating ------------------------------------------------------------------------

    def needs_plan(
        self, message: str, *, available_tools: Sequence[str] = (), force: bool | None = None
    ) -> bool:
        """Decide whether planning earns its cost for this turn.

        Returns False for short questions and for anything with no tools available -- without
        tools, a plan can only contain generate/respond steps, which is what a direct answer
        already is. Short personal-OS imperatives ("add a task to buy milk") are the exception:
        they only fire when a matching ``propose_*`` tool is in ``available_tools``, so ordinary
        Q&A does not pay for a planning generation.
        """
        if force is not None:
            return force and self._enabled
        if not self._enabled or not available_tools:
            return False
        text = message.strip()
        if matching_personal_os_tool(text, available_tools):
            return True
        # Same exception, for the engineer agent's repository tools: "clone <url> and analyse it"
        # puts its verb before the conjunction, so `_MULTI_STEP_MARKERS` misses it, and it can
        # be under 40 characters. Skipping planning there does not produce a worse answer, it
        # produces the model explaining it cannot reach a repository -- with the tools that
        # could have sitting unused. Only fires when such a tool is actually offered.
        if matching_repository_tool(text, available_tools):
            return True
        if len(text) < 40:
            return False
        if any(pattern.search(text) for pattern in _MULTI_STEP_MARKERS):
            return True
        # An explicitly enumerated request ("1. ... 2. ...") is a plan the user already wrote.
        return len(re.findall(r"^\s*(?:[-*]|\d+[.)])\s+\S", text, re.M)) >= 2

    # -- planning ----------------------------------------------------------------------

    async def plan(
        self,
        goal: Goal,
        *,
        available_tools: Sequence[Mapping[str, Any]] = (),
        max_steps: int | None = None,
    ) -> Plan:
        """Decompose a goal into a step DAG.

        A malformed or empty model response falls back to a single ``RESPOND`` step rather than
        raising: failing the user's turn because the planner produced bad JSON would be a worse
        outcome than answering directly, which is what would have happened without planning.
        """
        started = time.perf_counter()
        limit = min(max_steps or self._max_steps, self._max_steps)

        tool_lines = "\n".join(_render_tool_line(t) for t in available_tools)
        rendered = self._prompts.render(
            "task.plan_decomposition",
            {"goal": goal.description, "tools": tool_lines, "max_steps": str(limit)},
        )

        try:
            response = await self._router.complete(
                CompletionRequest(
                    conversation=Conversation.of([Message.user(rendered.text)]),
                    # A production incident on the self-hosted CPU provider measured this exact
                    # call sustaining ~5 tokens/s: at 1200 it can need up to 240s to finish, well
                    # past even the deployed 180s request_timeout_s, so the provider call was
                    # hard-cancelled mid-JSON every time -- raising instead of returning, which
                    # skips _parse_steps entirely and starves the "no usable steps" diagnostic
                    # below of the response preview it exists to capture. 700 tokens fits inside
                    # that budget with margin on this hardware; a plan that still doesn't fit
                    # degrades the same safe way truncated JSON always has (falls through to a
                    # single RESPOND step below), just via a fast, parseable return instead of a
                    # 180s wait ending in an exception.
                    sampling=SamplingParams(temperature=0.0, max_output_tokens=700),
                    policy=self._policy,
                    timeout_s=self._request_timeout_s,
                )
            )
            steps = self._parse_steps(response.text, available_tools={t.get("name") for t in available_tools})
            if not steps:
                # The router call succeeded -- this is not the except-block's "provider is
                # down" case -- but the response still produced nothing usable. Without this,
                # a model that legitimately chose to just answer and a model whose JSON
                # _parse_steps rejected outright log identically (both fall through to the same
                # single RESPOND step below), which made a real production incident
                # undiagnosable from logs alone: the raw response is the only way to tell them
                # apart, and once this call returns it is gone.
                logger.info(
                    "planning produced no usable steps; falling back to a direct response",
                    extra={"response_preview": response.text[:300]},
                )
        except Exception as exc:
            logger.warning("planning failed; falling back to a direct response",
                           extra={"error": type(exc).__name__})
            steps = []

        if not steps:
            steps = [
                PlanStep(
                    id=StepId("s1"),
                    kind=StepKind.RESPOND,
                    description=goal.description,
                    priority=goal.priority,
                )
            ]

        plan = Plan(id=PlanId.new(), goal=goal, steps=tuple(steps[:limit]), max_steps=self._max_steps)
        if self._metrics is not None:
            self._metrics.plans_created_total.labels(agent="planner").inc()
            self._metrics.plan_steps.observe(len(plan.steps))
        logger.info(
            "plan created",
            extra={"steps": len(plan.steps), "ms": round((time.perf_counter() - started) * 1000, 1)},
        )
        return plan

    def _parse_steps(self, text: str, *, available_tools: set[str | None]) -> list[PlanStep]:
        """Parse and *validate* the model's plan.

        Three things are enforced here, each of which the model gets wrong regularly:
        unknown tool names are rejected, dependencies on non-existent steps are dropped rather
        than raising, and a step id is normalised so ``"Step 1"`` and ``"s1"`` do not become two
        different nodes.
        """
        payload = _parse_json_object(text)
        raw_steps = payload.get("steps") or []
        if not isinstance(raw_steps, list):
            return []

        declared: dict[str, str] = {}
        for index, raw in enumerate(raw_steps):
            if isinstance(raw, Mapping):
                declared[str(raw.get("id", f"s{index + 1}"))] = f"s{index + 1}"

        steps: list[PlanStep] = []
        for index, raw in enumerate(raw_steps):
            if not isinstance(raw, Mapping):
                continue
            kind_value = str(raw.get("kind", "generate")).lower()
            if kind_value not in _STEP_KINDS:
                kind_value = "generate"
            kind = StepKind(kind_value)

            tool = raw.get("tool") or None
            if kind is StepKind.TOOL and tool not in available_tools:
                # A step naming a tool that does not exist can never succeed. Demoting it to a
                # generate step lets the plan still make progress.
                logger.debug("plan step referenced an unknown tool", extra={"tool": tool})
                kind, tool = StepKind.GENERATE, None
            if kind is not StepKind.TOOL:
                tool = None

            depends = tuple(
                StepId(declared[str(d)])
                for d in (raw.get("depends_on") or [])
                if str(d) in declared and declared[str(d)] != f"s{index + 1}"
            )

            description = str(raw.get("description", "")).strip()
            if not description:
                continue

            steps.append(
                PlanStep(
                    id=StepId(f"s{index + 1}"),
                    kind=kind,
                    description=description[:500],
                    depends_on=depends,
                    tool=tool,
                    arguments=dict(raw.get("arguments") or {}),
                    priority=Priority.NORMAL,
                )
            )
        return steps

    # -- replanning --------------------------------------------------------------------

    def can_replan(self, plan: Plan, *, limit: int | None = None) -> bool:
        """Whether another revision is allowed.

        ``limit`` lets a caller with its own iteration budget (``AgenticExecutor``, keyed off an
        ``AgentSpec.max_plan_iterations``) override this engine's own ``max_replans`` default
        rather than being capped by it -- the two are independent knobs, one per-deployment and
        one per-agent-persona.
        """
        effective = self._max_replans if limit is None else limit
        return len(plan.revisions) < effective

    async def replan(
        self,
        plan: Plan,
        *,
        reason: str,
        available_tools: Sequence[Mapping[str, Any]] = (),
        limit: int | None = None,
    ) -> Plan:
        """Produce a revised plan after a failure.

        Succeeded steps are preserved -- rerunning a successful tool call is wasteful and, for a
        side-effecting tool, actively wrong. Only the unfinished remainder is re-planned.

        ``limit`` mirrors :meth:`can_replan`'s override -- a caller with its own iteration budget
        (``AgenticExecutor``) passes its ``max_plan_iterations`` through here too, so this
        method's own guard cannot reject a revision that its caller already confirmed was allowed.
        """
        if not self.can_replan(plan, limit=limit):
            raise PlanningError(
                "replanning limit reached",
                details={
                    "revisions": len(plan.revisions),
                    "limit": self._max_replans if limit is None else limit,
                },
            )

        completed = [s for s in plan.steps if s.status is StepStatus.SUCCEEDED]
        failed = plan.failed_steps
        context = "\n".join(
            [
                f"Completed: {s.description}" for s in completed
            ] + [
                f"Failed: {s.description} ({s.error_kind})" for s in failed
            ]
        )
        revised_goal = Goal(
            description=(
                f"{plan.goal.description}\n\nProgress so far:\n{context}\n\n"
                f"Plan the remaining work only. Do not repeat completed steps."
            ),
            success_criteria=plan.goal.success_criteria,
            constraints=plan.goal.constraints,
            priority=plan.goal.priority,
        )

        fresh = await self.plan(
            revised_goal,
            available_tools=available_tools,
            max_steps=self._max_steps - len(completed),
        )
        # Renumber the new steps so they cannot collide with the preserved ones.
        offset = len(completed)
        renumbered = [
            PlanStep(
                id=StepId(f"s{offset + i + 1}"),
                kind=step.kind,
                description=step.description,
                depends_on=tuple(
                    StepId(f"s{offset + int(d.value[1:])}") for d in step.depends_on
                ),
                tool=step.tool,
                arguments=step.arguments,
                priority=step.priority,
            )
            for i, step in enumerate(fresh.steps)
        ]

        if self._metrics is not None:
            self._metrics.plan_revisions_total.labels(reason=reason[:40]).inc()
        return plan.revised(steps=[*completed, *renumbered], reason=reason)

    @staticmethod
    def goal_from_message(message: str, *, criteria: Sequence[str] = ()) -> Goal:
        return Goal(description=message.strip(), success_criteria=tuple(criteria))


def _parse_json_object(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction -- models wrap objects in prose and fences, and on a slow
    CPU-only provider a long plan can also get hard-cut by ``max_output_tokens`` mid-JSON
    (measured live: qwen2.5:3b given 21 tools and "Analyze the VELVET project." ran the full
    12-step ceiling and hit ``finish_reason: "length"`` before its closing brackets). A plan
    whose first N steps are complete, valid JSON is a working plan for those N steps -- discarding
    all of it because step N+1 never finished is strictly worse than executing what's there, so
    this salvages every complete ``{...}`` element out of a ``[...]``/``"steps": [...]`` array
    even when that array's own closing bracket never arrived. The same qwen response also showed
    the model dropping the ``{"steps": [...]}`` envelope and returning the bare array directly --
    :func:`_salvage_array_elements` covers both shapes at once, since a bare array and an
    unclosed ``"steps"`` array are the same recovery problem once the envelope is missing either
    way.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.S).strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"steps": parsed}
    except json.JSONDecodeError:
        pass

    obj_start = stripped.find("{")
    arr_start = stripped.find("[")
    if arr_start >= 0 and (obj_start < 0 or arr_start < obj_start):
        elements = _salvage_array_elements(stripped, arr_start)
        if elements:
            return {"steps": elements}
    if obj_start < 0:
        return {}

    depth = 0
    for index in range(obj_start, len(stripped)):
        if stripped[index] == "{":
            depth += 1
        elif stripped[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(stripped[obj_start : index + 1])
                except json.JSONDecodeError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}

    # The top-level object never closed -- most commonly `{"steps": [ ... ` cut off before its
    # own `]}`. The array inside it may still have complete elements worth keeping.
    steps_key = stripped.find('"steps"', obj_start)
    if steps_key >= 0:
        inner_arr = stripped.find("[", steps_key)
        if inner_arr >= 0:
            elements = _salvage_array_elements(stripped, inner_arr)
            if elements:
                return {"steps": elements}
    return {}


def _salvage_array_elements(text: str, start: int) -> list[Any]:
    """Every complete ``{...}`` object inside the array opened by ``text[start]`` (a literal
    ``[``), stopping at -- and discarding -- the first element that never closes. A half-written
    object is corrupt data, not a partial step; only whole elements are usable.
    """
    elements: list[Any] = []
    index = start + 1
    length = len(text)
    while index < length:
        while index < length and text[index] in " \t\r\n,":
            index += 1
        if index >= length or text[index] != "{":
            break
        elem_start = index
        depth = 0
        while index < length:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if depth != 0:
            break
        try:
            elements.append(json.loads(text[elem_start : index + 1]))
        except json.JSONDecodeError:
            break
        index += 1
    return elements
