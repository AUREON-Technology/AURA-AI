"""Check a finished run against the acceptance criteria the caller supplied (ADR-045, row 41).

``Goal.success_criteria`` used to be empty on every turn, so "did it work?" had no answer. When a
request carries ``success_criteria`` this module reads the run's *own evidence* -- the tool results
the plan produced, and the test runs they contain -- and sorts each criterion into one of three
buckets:

* ``met``        -- some tool output says so affirmatively (a line that names the criterion and
                    carries a pass marker, or a green test run for a criterion about tests);
* ``unmet``      -- evidence says otherwise (the same, with a failure marker; a red test run; a
                    failed step that names the criterion);
* ``unverified`` -- nothing the run produced speaks to it. Never counted as met.

The same contract as ``tools/open/acceptance_criteria_verifier.py``: only affirmative evidence
passes, missing evidence is not a pass. The model's own final answer is deliberately **not**
evidence -- that would be the model grading its own vibes -- so a criterion the run never produced
proof for stays ``unverified`` however confidently the answer claims it.

Pure, stdlib-only, bounded and total: a result that cannot be read yields "no evidence", never an
exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain.planning import Plan, StepKind, StepStatus
from .runner_output import OBS_TESTS_FAILED, OBS_TESTS_PASSED, observe_plan

__all__ = [
    "AcceptanceReport", "check_acceptance", "normalise_criteria",
    "MAX_CRITERIA", "MAX_CRITERION_CHARS",
]

MAX_CRITERIA = 20
MAX_CRITERION_CHARS = 300
#: Output read per step and lines read per step -- the check is an aid, never a cost.
_MAX_RESULT_CHARS = 60_000
_MAX_LINES = 2_000

_PASS = re.compile(
    r"\b(?:pass(?:ed|es|ing)?|ok|success(?:ful(?:ly)?)?|succeeded|met|verified|satisfied|done|"
    r"confirmed|works?|working)\b|✅|✓|✔|\[x\]",
    re.I,
)
_FAIL = re.compile(
    r"\b(?:fail(?:ed|ing|s|ure|ures)?|errors?|not|no|never|missing|broken|unmet|pending|"
    r"incomplete|todo|regress(?:ed|ion|ions)?|timed out|refused|denied)\b|❌|✗|✘|\[ \]",
    re.I,
)
_TESTS = re.compile(r"\b(?:tests?|pytest|test suite|unit tests?|specs?)\b", re.I)


def normalise_criteria(raw: Sequence[Any] | None) -> tuple[str, ...]:
    """Trimmed, de-duplicated, bounded criteria (at most 20 of at most 300 characters)."""
    out: list[str] = []
    for item in raw or ():
        text = " ".join(str(item).split())[:MAX_CRITERION_CHARS]
        if text and text not in out:
            out.append(text)
        if len(out) >= MAX_CRITERIA:
            break
    return tuple(out)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[_\-]", " ", text).lower().split())


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    """How a run fared against its criteria."""

    criteria: tuple[str, ...]
    met: tuple[str, ...] = ()
    unmet: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()
    #: criterion -> a short phrase saying what the verdict rests on (never tool output verbatim).
    basis: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """``met`` only when every criterion is met; ``unmet`` if any is contradicted;
        otherwise ``unverified``."""
        if not self.criteria:
            return "none"
        if self.unmet:
            return "unmet"
        if self.unverified:
            return "unverified"
        return "met"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "met": list(self.met),
            "unmet": list(self.unmet),
            "unverified": list(self.unverified),
            "basis": dict(self.basis),
        }

    def caveat(self) -> str:
        """What the user is told when the run did not meet every criterion; empty when it did."""
        if self.status in ("met", "none"):
            return ""
        lines = ["Acceptance check (against the tool results, not the answer above): "
                 f"{len(self.met)} of {len(self.criteria)} criteria shown met."]
        if self.unmet:
            lines.append("Not met: " + "; ".join(self.unmet[:10]) + ".")
        if self.unverified:
            lines.append("No evidence either way: " + "; ".join(self.unverified[:10]) + ".")
        return " ".join(lines)


def check_acceptance(criteria: Sequence[str], plan: Plan | None) -> AcceptanceReport:
    """Sort ``criteria`` by what ``plan``'s step results show. ``plan=None`` (nothing ran) leaves
    every criterion unverified."""
    crits = normalise_criteria(criteria)
    if not crits:
        return AcceptanceReport(criteria=())
    results: list[tuple[str, str]] = []
    failed_steps: list[str] = []
    tests_state: str | None = None
    if plan is not None:
        for step in plan.steps:
            if step.kind is StepKind.TOOL and step.status is StepStatus.SUCCEEDED and step.result:
                results.append((step.tool or "", step.result[:_MAX_RESULT_CHARS]))
            elif step.status is StepStatus.FAILED:
                failed_steps.append(_norm(step.description))
        try:
            runs = [o for _sid, o in observe_plan(plan) if o.family == "tests"]
        except Exception:  # pragma: no cover - an observation is an aid
            runs = []
        if runs:  # the last test run decides, as the loop's own unresolved-failure rule does
            tests_state = "passed" if runs[-1].kind == OBS_TESTS_PASSED else (
                "failed" if runs[-1].kind == OBS_TESTS_FAILED else None)

    met: list[str] = []
    unmet: list[str] = []
    unverified: list[str] = []
    basis: dict[str, str] = {}
    for crit in crits:
        key = _norm(crit)
        pos = neg = 0
        for _tool, text in results:
            for line in text.splitlines()[:_MAX_LINES]:
                if not _contains(_norm(line), key):
                    continue
                if _FAIL.search(line):
                    neg += 1
                elif _PASS.search(line):
                    pos += 1
        if any(_contains(d, key) for d in failed_steps):
            neg += 1
        about_tests = bool(_TESTS.search(crit))
        if about_tests and tests_state is not None:
            if tests_state == "failed":
                neg += 1
                basis[crit] = "the last test run failed"
            elif not neg:
                pos += 1
                basis[crit] = "the last test run passed"
        if neg:
            unmet.append(crit)
            basis.setdefault(crit, "a tool result or failed step contradicts it")
        elif pos:
            met.append(crit)
            basis.setdefault(crit, "a tool result affirms it")
        else:
            unverified.append(crit)
            basis[crit] = "no tool result speaks to it"
    return AcceptanceReport(criteria=crits, met=tuple(met), unmet=tuple(unmet),
                            unverified=tuple(unverified), basis=basis)
