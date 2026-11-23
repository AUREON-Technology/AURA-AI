"""The self-reflection critic: check the plan before it runs and the outcome before it is claimed
(ADR-037).

A planner is a model, and models write plans that look right: they name a tool that does not exist,
edit a file they never read, change code and never run the tests. The loop used to execute whatever
came back and report whatever happened. The critic is the missing second look, and it is
**deterministic first**: every check below is a rule over the plan and the tool results, so it
costs no generation, never disagrees with itself between runs, and can be tested by constructing
the situation. An optional LLM critic (:class:`LLMCritic`, off by default) can add advisory
findings on top; it can never block or force a replan on its own.

Three places it runs, each producing :class:`CriticFinding` rows:

* **Plan, before execution** (:func:`critique_plan`): unknown tool; a side-effecting step with no
  read before it; a code-changing plan with no verification step. A *blocking* finding is fed back
  into one replan (``PlanningEngine.replan(feedback=...)``) before anything runs.
* **Outcome, before the final answer** (:func:`critique_outcome`): a code change that no later
  step verified (blocking: one corrective pass), and a test failure nobody resolved (a caveat).
* **Answer, after generation** (:func:`critique_answer`): the answer claims success while a step
  failed or tests are red, or cites a file or test that no tool result, argument or the request
  itself mentions. These become caveats appended to the answer, never a silent rewrite.

Stdlib-only, no engine imports beyond ``domain``; the tool registry is reached through a
``spec_for`` callable the caller supplies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

from ..domain.planning import Plan, PlanStep, StepKind, StepStatus
from ..observability.logging import get_logger
from .runner_output import (
    OBS_TESTS_FAILED,
    ToolObservation,
    is_test_command,
)

__all__ = [
    "CriticFinding",
    "Critic",
    "LLMCritic",
    "critique_plan",
    "critique_outcome",
    "critique_answer",
    "feedback_lines",
]

logger = get_logger("critic")

#: Tools that change the user's code or repository state. Names, not specs: the planner's own
#: vocabulary is what a plan is checked against.
CODE_CHANGE_TOOLS = frozenset({
    "edit_file", "write_file", "write_files", "github_commit_files",
})
#: ...of which these *create* rather than modify, so "no read first" is not a defect.
CREATE_TOOLS = frozenset({
    "write_file", "write_files", "clone_repository", "open_workspace", "git_checkout_branch",
    "github_create_branch", "github_create_draft_pull_request", "remember_fact",
})
#: Tools whose purpose is to check the work.
VERIFY_TOOLS = frozenset({
    "generate_and_run_tests", "analyze_test_quality", "evaluate_quality_gate",
    "verify_python_symbols", "scan_code_security", "review_code_diff", "git_diff",
})
_CODE_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".dart", ".go", ".rs", ".java", ".kt", ".c", ".cc",
    ".cpp", ".h", ".hpp", ".rb", ".php", ".cs", ".swift", ".sh", ".sql",
})

_REFERENCE = re.compile(
    r"(?<![\w/.:-])(?:[\w.-]+/)*[\w.-]+\.(?:py|js|jsx|ts|tsx|dart|go|rs|java|kt|c|cc|cpp|h|rb|"
    r"php|cs|swift|md|json|ya?ml|toml|sh|sql|html|css)\b|\btest_[A-Za-z0-9_]+\b"
)
_SUCCESS_CLAIM = re.compile(
    r"\ball\s+(?:the\s+)?tests?\s+(?:now\s+)?pass(?:es|ed|ing)?\b|"
    r"\btests?\s+(?:are\s+)?(?:now\s+)?(?:all\s+)?(?:passing|green)\b|"
    r"\b(?:everything|it)\s+(?:now\s+)?works\b|\bsuccessfully\b|"
    r"\b(?:has|have)\s+been\s+(?:fixed|implemented|completed|resolved)\b|"
    r"\bI(?:'ve| have)\s+(?:fixed|implemented|completed|resolved)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class CriticFinding:
    code: str
    severity: str                  # error | warn | info
    message: str
    step_id: str | None = None
    #: Whether the loop should spend a (bounded) revision on this before proceeding.
    blocking: bool = False
    source: str = "rules"          # rules | llm

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message,
                "step_id": self.step_id, "blocking": self.blocking, "source": self.source}


def feedback_lines(findings: Iterable[CriticFinding]) -> list[str]:
    """The findings as lines for a replan's context block."""
    return [f"Critic ({f.severity}, {f.code}): {f.message}" for f in findings]


# -- helpers -----------------------------------------------------------------------------------


def _spec(spec_for: Callable[[str], Any] | None, name: str | None) -> Any:
    if spec_for is None or not name:
        return None
    try:
        return spec_for(name)
    except Exception:
        return None


def _paths_in(arguments: Mapping[str, Any]) -> list[str]:
    paths: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, Mapping):
            for key in ("path", "file_path", "filename"):
                if isinstance(value.get(key), str):
                    paths.append(value[key])
            if all(isinstance(k, str) and "." in k for k in value):  # {path: content}
                paths.extend(value)
        elif isinstance(value, (list, tuple)):
            for item in value[:50]:
                add(item)

    for key in ("path", "file_path", "filename", "paths", "files", "file"):
        if key in arguments:
            add(arguments[key])
    return paths


def _changes_code(step: PlanStep) -> bool:
    if step.kind is not StepKind.TOOL or step.tool not in CODE_CHANGE_TOOLS:
        return False
    paths = _paths_in(step.arguments or {})
    if not paths:
        return False  # no path to judge by: do not assume it is code
    return any(
        "." in p and "." + p.rsplit(".", 1)[-1].lower() in _CODE_SUFFIXES for p in paths
    )


def _is_verification(step: PlanStep) -> bool:
    if step.kind is StepKind.VERIFY:
        return True
    if step.kind is not StepKind.TOOL:
        return False
    if step.tool in VERIFY_TOOLS:
        return True
    return step.tool == "run_command" and is_test_command((step.arguments or {}).get("command"))


def _is_read(step: PlanStep, spec_for: Callable[[str], Any] | None) -> bool:
    if step.kind in (StepKind.RETRIEVE, StepKind.RECALL):
        return True
    if step.kind is not StepKind.TOOL:
        return False
    if step.tool == "run_command":
        return True  # `cat`, `ls`, `grep`: the Engineer's general-purpose way to look
    spec = _spec(spec_for, step.tool)
    return spec is not None and not getattr(spec, "side_effecting", True)


# -- plan --------------------------------------------------------------------------------------


def critique_plan(
    plan: Plan,
    *,
    available_tools: Collection[str] = (),
    spec_for: Callable[[str], Any] | None = None,
) -> list[CriticFinding]:
    """Findings on a plan that has not run yet. Pure; ``[]`` means nothing to say."""
    findings: list[CriticFinding] = []
    known = {t for t in available_tools if t}
    steps = list(plan.steps)

    for step in steps:
        if step.status is not StepStatus.PENDING:
            continue  # a preserved step from an earlier pass: already ran
        if step.kind is StepKind.TOOL and step.tool and known and step.tool not in known:
            findings.append(CriticFinding(
                "unknown_tool", "error",
                f"step {step.id.value} calls `{step.tool}`, which is not an available tool "
                f"(available: {', '.join(sorted(known)[:12])}{'...' if len(known) > 12 else ''}); "
                "it will be refused when it runs",
                step_id=step.id.value, blocking=True,
            ))

    for index, step in enumerate(steps):
        if step.status is not StepStatus.PENDING or step.kind is not StepKind.TOOL:
            continue
        spec = _spec(spec_for, step.tool)
        if spec is None or not getattr(spec, "side_effecting", False):
            continue
        if step.tool in CREATE_TOOLS or step.tool == "run_command" or _is_verification(step):
            continue  # creating, running a command (its own kind of look) or checking is not editing
        if any(_is_read(prior, spec_for) for prior in steps[:index]):
            continue
        findings.append(CriticFinding(
            "write_without_read", "warn",
            f"step {step.id.value} changes state with `{step.tool}` but nothing earlier in the "
            "plan reads or inspects what it is changing",
            step_id=step.id.value, blocking=step.tool == "edit_file",
        ))

    last_change = max(
        (i for i, s in enumerate(steps) if s.status is StepStatus.PENDING and _changes_code(s)),
        default=None,
    )
    if last_change is not None and not any(
        _is_verification(s) for s in steps[last_change + 1:]
    ):
        findings.append(CriticFinding(
            "no_verification", "warn",
            "the plan changes code but has no step afterwards that runs the tests or otherwise "
            "checks the change; add one (for example run the project's tests) after the edit",
            step_id=steps[last_change].id.value, blocking=True,
        ))
    return findings


# -- outcome -----------------------------------------------------------------------------------


def critique_outcome(
    plan: Plan, observations: Sequence[tuple[str, ToolObservation]] = (),
) -> list[CriticFinding]:
    """Findings on a finished pass, before an answer is written.

    ``observations`` are ``(step_id, observation)`` pairs in plan order (see
    ``AgenticExecutor._observe``).
    """
    findings: list[CriticFinding] = []
    steps = list(plan.steps)
    changed = [
        i for i, s in enumerate(steps) if s.status is StepStatus.SUCCEEDED and _changes_code(s)
    ]
    if changed and not any(
        s.status is StepStatus.SUCCEEDED and _is_verification(s) for s in steps[changed[-1] + 1:]
    ):
        findings.append(CriticFinding(
            "unverified_change", "warn",
            "code was changed and no later step verified it; nothing in this run shows the "
            "change works",
            step_id=steps[changed[-1]].id.value, blocking=True,
        ))
    last_tests = [o for o in observations if o[1].family == "tests"]
    if last_tests and last_tests[-1][1].kind == OBS_TESTS_FAILED:
        sid, obs = last_tests[-1]
        findings.append(CriticFinding(
            "unresolved_tests_failed", "error",
            f"the last test run (step {sid}) failed and nothing afterwards fixed it: {obs.summary}",
            step_id=sid,
        ))
    failed = plan.failed_steps
    if failed:
        findings.append(CriticFinding(
            "failed_steps_unresolved", "warn",
            "steps still failed when the run ended: "
            + ", ".join(f"{s.id.value} ({s.error_kind or 'failed'})" for s in failed[:6]),
        ))
    return findings


# -- answer ------------------------------------------------------------------------------------


def _evidence(plan: Plan, user_message: str, extra: Iterable[str]) -> str:
    parts: list[str] = [user_message or "", plan.goal.description]
    for step in plan.steps:
        parts.append(step.description or "")
        if step.arguments:
            try:
                parts.append(json.dumps(dict(step.arguments), default=str))
            except (TypeError, ValueError):
                parts.append(str(step.arguments))
        if step.result:
            parts.append(step.result)
        if step.error_kind:
            parts.append(step.error_kind)
    parts.extend(extra)
    return "\n".join(parts).lower()


def critique_answer(
    answer: str,
    plan: Plan | None,
    *,
    user_message: str = "",
    observations: Sequence[tuple[str, ToolObservation]] = (),
    extra_evidence: Iterable[str] = (),
) -> list[CriticFinding]:
    """Findings on the generated answer, against what the tools actually reported."""
    if not answer or plan is None:
        return []
    findings: list[CriticFinding] = []

    tests = [o for o in observations if o[1].family == "tests"]
    red = bool(tests and tests[-1][1].kind == OBS_TESTS_FAILED)
    failed = list(plan.failed_steps)
    if (red or failed) and _SUCCESS_CLAIM.search(answer):
        reasons = []
        if red:
            reasons.append(f"the last test run failed ({tests[-1][1].summary})")
        if failed:
            reasons.append(
                "step(s) failed: " + ", ".join(s.id.value for s in failed[:5])
            )
        findings.append(CriticFinding(
            "success_claim_unsupported", "error",
            "the answer reads as though the work succeeded, but " + " and ".join(reasons),
        ))

    evidence = _evidence(plan, user_message, extra_evidence)
    missing: list[str] = []
    for match in _REFERENCE.finditer(answer):
        token = match.group(0)
        probe = token.lower()
        base = probe.rsplit("/", 1)[-1]
        if probe in evidence or base in evidence:
            continue
        if token not in missing:
            missing.append(token)
        if len(missing) >= 5:
            break
    if missing:
        findings.append(CriticFinding(
            "unsupported_reference", "info",
            "the answer mentions "
            + ", ".join(f"`{m}`" for m in missing)
            + ", which no tool result or request in this task mentions",
        ))
    return findings


# -- the optional model critic -------------------------------------------------------------------


class LLMCritic:
    """An advisory second opinion from the model, behind ``AURA_CRITIC_LLM_ENABLED`` (off).

    It receives a compact description of the plan or outcome and returns findings as JSON. Its
    findings are always ``source="llm"``, never ``blocking``: a generation may add a caveat, it may
    not spend the run's revisions. Any failure (timeout, unparseable reply, no provider) yields no
    findings; the deterministic critic is unaffected.
    """

    def __init__(self, *, router: Any, timeout_s: float = 30.0, max_output_tokens: int = 300) -> None:
        self._router = router
        self._timeout_s = timeout_s
        self._max_tokens = max_output_tokens

    async def review(self, stage: str, goal: str, steps: Sequence[str]) -> list[CriticFinding]:
        try:
            from ..domain.llm import CompletionRequest, SamplingParams
            from ..domain.messages import Conversation, Message

            prompt = (
                f"You are reviewing an AI agent's {stage}. Goal: {goal[:600]}\n"
                "Steps:\n" + "\n".join(f"- {line[:240]}" for line in steps[:20]) + "\n\n"
                "List concrete problems only (a missing check, a step that cannot work, a claim "
                "the steps do not support). If there are none, return an empty list. Reply with "
                'JSON only: {"findings":[{"code":"snake_case","message":"one sentence"}]}'
            )
            response = await self._router.complete(
                CompletionRequest(
                    conversation=Conversation.of([Message.user(prompt)]),
                    sampling=SamplingParams(temperature=0.0, max_output_tokens=self._max_tokens),
                    timeout_s=self._timeout_s,
                )
            )
            return _parse_llm_findings(getattr(response, "text", "") or "")
        except Exception:
            logger.info("LLM critic produced nothing", exc_info=True)
            return []


def _parse_llm_findings(text: str) -> list[CriticFinding]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, RecursionError):
        return []
    items = data.get("findings") if isinstance(data, Mapping) else None
    out: list[CriticFinding] = []
    for item in (items if isinstance(items, list) else [])[:5]:
        if not isinstance(item, Mapping) or not isinstance(item.get("message"), str):
            continue
        code = re.sub(r"[^a-z0-9_]", "", str(item.get("code", "llm_finding")).lower())[:40]
        out.append(CriticFinding(code or "llm_finding", "info", item["message"][:300],
                                 blocking=False, source="llm"))
    return out


# -- the aggregate the loop holds ----------------------------------------------------------------


@dataclass(slots=True)
class Critic:
    """What ``AgenticExecutor`` is given: rules on/off, an optional model critic, and the bounds."""

    enabled: bool = True
    llm: LLMCritic | None = None
    #: Revisions the critic may spend before the first pass (plan stage), per run.
    max_plan_revisions: int = 1
    #: Corrective passes it may request after a "successful" pass, per run.
    max_corrective_passes: int = 1

    async def plan_findings(
        self, plan: Plan, *, available_tools: Collection[str], spec_for: Callable[[str], Any] | None,
    ) -> list[CriticFinding]:
        if not self.enabled:
            return []
        findings = critique_plan(plan, available_tools=available_tools, spec_for=spec_for)
        if self.llm is not None:
            findings += await self.llm.review(
                "plan", plan.goal.description,
                [f"{s.id.value} [{s.kind}] {s.tool or ''} {s.description}" for s in plan.steps],
            )
        return findings

    async def outcome_findings(
        self, plan: Plan, observations: Sequence[tuple[str, ToolObservation]],
    ) -> list[CriticFinding]:
        if not self.enabled:
            return []
        findings = critique_outcome(plan, observations)
        if self.llm is not None:
            findings += await self.llm.review(
                "outcome", plan.goal.description,
                [f"{s.id.value} [{s.status}] {s.tool or s.kind}: {s.description}"
                 for s in plan.steps],
            )
        return findings
