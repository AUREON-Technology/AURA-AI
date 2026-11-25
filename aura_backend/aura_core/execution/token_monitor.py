"""Token budget monitor: say how much of the budget a run has used, and act before it runs out
(ADR-039).

``AgentBudget.token_budget`` was enforced -- the loop refuses another replan once the planner's
generations alone have spent it -- but silently, and only that one number. Nothing told the client
(or the model) that a long Engineer task was filling its budget, and nothing did anything about
it until the wall was hit.

:class:`TokenMonitor` is the measuring half:

* **used** = planner generation tokens (what the run has *spent*) plus an estimate of the tool
  output the run is *holding* (characters / 4, the same rule of thumb the context engine falls
  back to), because tool output is what actually fills a long coding task's context;
* **status** each pass: used, remaining, percent -- emitted as a ``budget`` stream frame;
* **pressure** when ``used`` first crosses the threshold (default 80%): a ``context_pressure``
  frame with recommendations, and the compression step is triggered **once** (:func:`compress_results`
  shortens the tool output that earlier passes left in the plan, keeping the newest pass intact and
  each output's head and tail -- the same shape ``run_command`` itself uses when a result is too
  long).

What compression does and does not reach, stated plainly: the loop's own prompts (plan, replan)
carry step *descriptions*, never tool output, and the final answer is built from the last pass's
results (``ExecutionOutcome.results`` holds one pass). So the output this shortens is what a long
run *retains* -- the plan on the outcome, the critic's evidence, anything that reads plan steps --
not a prompt already on its way to the model. The other half is the session's history: when the
loop reports pressure the kernel asks ``EpisodicMemory.compact`` -- the existing summariser, on
demand -- to fold the older turns into the running summary, so the *next* turn carries it. The
``context_pressure`` frame's recommendations name those steps (and retrieval trimming, which is
left to the client) for anyone watching the stream.

The hard stop is unchanged: only generation tokens exhaust the budget (``_evaluate``); the
estimate of held output informs pressure and reporting, it does not end a run by itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from ..domain.planning import Plan, StepStatus

__all__ = ["TokenMonitor", "BudgetStatus", "compress_results", "digest_omitted", "estimate_tokens"]

#: Characters per token for the estimate. Conservative for code, which tokenises worse than prose.
_CHARS_PER_TOKEN = 4
_HEAD_CHARS = 1200
_TAIL_CHARS = 800

RECOMMENDATIONS = (
    "compress_tool_output",   # older tool results were shortened (done once, automatically)
    "summarise_history",      # fold earlier turns into a summary before continuing
    "trim_context",           # drop retrieval/memory that the remaining work does not need
)


def estimate_tokens(text: str | None) -> int:
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN if text else 0


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    budget: int
    used: int
    generation_tokens: int
    held_output_tokens: int
    iteration: int

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    @property
    def percent(self) -> float:
        return round(100.0 * self.used / self.budget, 1) if self.budget else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget, "used": self.used, "remaining": self.remaining,
            "percent": self.percent, "generation_tokens": self.generation_tokens,
            "held_output_tokens": self.held_output_tokens, "iteration": self.iteration,
        }


#: Lines that say something went wrong or needs attention. A run's output is mostly noise, but the
#: handful of these lines is what a later pass (or the critic) actually needs; head-and-tail alone
#: drops them whenever they sit in the middle (a failing test's name, a stack frame, a warning).
_SALIENT = re.compile(
    r"\b(?:errors?|fail(?:ed|ure|ures|ing)?|exceptions?|traceback|fatal|panic|assert(?:ion)?(?:error)?|"
    r"warnings?|denied|refused|timed? ?out|not found|cannot|undefined|unresolved)\b|[✗❌]|^E\s",
    re.I,
)
_DIGEST_CHARS = 500
_DIGEST_LINES = 8
_DIGEST_LINE_CHARS = 110
_OMITTED_MARK = "omitted to save context"


def digest_omitted(middle: str) -> str:
    """The salient lines of the part a clip drops, without a model (ADR-047, row 28).

    Distinct lines that look like failures or warnings, first occurrences, each cut to 110
    characters with a repeat count, at most 8 of them and 500 characters in all. ``""`` when the
    omitted part has none (plain noise needs no digest). Pure and bounded.
    """
    seen: dict[str, int] = {}
    for line in middle.splitlines()[:20_000]:
        text = " ".join(line.split())
        if text and _SALIENT.search(text):
            key = text[:_DIGEST_LINE_CHARS]
            seen[key] = seen.get(key, 0) + 1
    if not seen:
        return ""
    parts: list[str] = []
    used = 0
    for key, count in list(seen.items())[:_DIGEST_LINES]:
        piece = key + (f" (x{count})" if count > 1 else "")
        if used + len(piece) + 3 > _DIGEST_CHARS:
            break
        parts.append(piece)
        used += len(piece) + 3
    more = len(seen) - len(parts)
    return " | ".join(parts) + (f" | (+{more} more)" if more > 0 else "")


def _clip_middle(text: str, head: int = _HEAD_CHARS, tail: int = _TAIL_CHARS) -> str:
    if len(text) <= head + tail + 80 or _OMITTED_MARK in text[head:head + 200]:
        return text  # short enough, or already clipped (a second pass must save nothing)
    cut = len(text) - head - tail
    digest = digest_omitted(text[head:len(text) - tail])
    kept = f"\n[salient lines from the omitted part: {digest}]" if digest else ""
    return (
        f"{text[:head]}\n[... {cut} characters of earlier tool output {_OMITTED_MARK} ...]"
        f"{kept}\n{text[-tail:]}"
    )


def compress_results(
    plan: Plan, results: Mapping[str, str], *, keep_step_ids: frozenset[str] = frozenset(),
) -> tuple[Plan, dict[str, str], int]:
    """Shorten the stored output of every succeeded step not in ``keep_step_ids``.

    Returns ``(plan, results, characters_saved)``. Only the *text* is shortened (head and tail
    kept); status, tool, arguments and the step's place in the plan are untouched, so replanning
    and the run record see the same plan. A step already short enough is left alone.
    """
    saved = 0
    new_steps = []
    new_results = dict(results)
    for step in plan.steps:
        if (
            step.status is StepStatus.SUCCEEDED and step.result
            and step.id.value not in keep_step_ids
        ):
            clipped = _clip_middle(step.result)
            if len(clipped) < len(step.result):
                saved += len(step.result) - len(clipped)
                step = replace(step, result=clipped)
                if step.id.value in new_results:
                    new_results[step.id.value] = clipped
        new_steps.append(step)
    return (plan.with_steps(new_steps) if saved else plan), new_results, saved


@dataclass
class TokenMonitor:
    """One run's budget bookkeeping. ``budget=None`` makes every method a no-op."""

    budget: int | None
    threshold: float = 0.8
    _pressure_fired: bool = field(default=False, init=False)

    @property
    def active(self) -> bool:
        return bool(self.budget)

    def status(self, plan: Plan, *, generation_tokens: int, iteration: int) -> BudgetStatus | None:
        if not self.budget:
            return None
        held = sum(
            estimate_tokens(s.result) for s in plan.steps if s.status is StepStatus.SUCCEEDED
        )
        return BudgetStatus(
            budget=int(self.budget), used=int(generation_tokens) + held,
            generation_tokens=int(generation_tokens), held_output_tokens=held,
            iteration=iteration,
        )

    def pressure_due(self, status: BudgetStatus | None) -> bool:
        """True exactly once: the first time ``used`` is at or above the threshold."""
        if status is None or self._pressure_fired:
            return False
        if status.budget and status.used / status.budget >= self.threshold:
            self._pressure_fired = True
            return True
        return False
