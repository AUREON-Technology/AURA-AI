"""Guard precision and recall on a labelled corpus (EV-7, GAP-022, CB-24).

The guards in :mod:`aura_core.safety.guards` are rule-based, and until now nothing measured how
often they are right. The one time they ran on real model output (load test A4, 2026-09-14) the
system-prompt-leak guard blocked 3 of 15 turns and the groundedness guard flagged 9, and whether
those were true or false positives was never established.

This scores each guard against cases whose correct verdict a person decided in advance:

* ``system_prompt_leak`` -- positive when the answer reproduces the operator's instructions.
  Cases name a real persona template from :data:`~aura_core.prompts.library.BUILTIN_PROMPTS`, so
  the guard is compared against the prompt production actually renders, not a stand-in.
* ``groundedness`` -- positive when the answer contains a claim its sources do not support.
* ``prompt_injection`` -- positive when a retrieved document or tool result tries to instruct
  the assistant. Scored at the ``CONTEXT`` stage, where a hit quarantines the chunk.
* ``jailbreak`` -- positive when the user's message tries to switch the assistant's rules off.

"Positive" means the guard's *consequential* verdict for that guard: ``BLOCK`` for the leak guard,
``FLAG`` for groundedness, ``QUARANTINE`` for injection, a ``jailbreak_attempt`` flag for
jailbreak. A weak-signal ``FLAG`` that changes nothing is not a positive.

The guards are the ones :meth:`SafetyPipeline.from_settings` builds, with the thresholds the
settings carry, so a threshold change shows up here. Metrics per guard: ``precision``, ``recall``
and ``specificity`` (1 minus the false-positive rate), all "higher is better" so
:class:`~aura_core.evaluation.harness.RegressionGate` can gate them.

What this is **not**: real model output. The cases are written by hand to cover the shapes that
matter (verbatim extraction, paraphrase, an answer quoting its sources, a security article that
discusses injection). EV-7's exit criterion is precision on *real* outputs; this corpus is the
harness that a captured, labelled set of real outputs runs through, and the deterministic floor
that keeps a guard change from silently making things worse in the meantime.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..domain.safety import GuardAction, GuardStage, GuardVerdict

__all__ = [
    "GUARDS",
    "GuardCase",
    "GuardCaseResult",
    "GuardSuiteResult",
    "GuardEvaluator",
    "load_guard_cases",
]

#: The guards this suite scores, with the stage each is judged at.
GUARDS: Mapping[str, GuardStage] = {
    "system_prompt_leak": GuardStage.OUTPUT,
    "groundedness": GuardStage.OUTPUT,
    "prompt_injection": GuardStage.CONTEXT,
    "jailbreak": GuardStage.INPUT,
}


def is_positive(guard: str, verdict: GuardVerdict) -> bool:
    """Whether ``verdict`` is the guard's consequential outcome (see the module docstring)."""
    if guard == "system_prompt_leak":
        return verdict.action is GuardAction.BLOCK
    if guard == "groundedness":
        return verdict.action is GuardAction.FLAG
    if guard == "prompt_injection":
        return verdict.action is GuardAction.QUARANTINE
    if guard == "jailbreak":
        return verdict.reason == "jailbreak_attempt"
    raise ValueError(f"unknown guard {guard!r}")


@dataclass(frozen=True, slots=True)
class GuardCase:
    id: str
    guard: str
    text: str
    positive: bool
    #: Leak cases: the persona template to render, and its variables.
    template: str | None = None
    variables: Mapping[str, str] = field(default_factory=dict)
    #: Groundedness cases: the retrieved sources the answer should be supported by.
    sources: tuple[str, ...] = ()
    #: Why the case is labelled the way it is, and which failure shape it represents.
    note: str = ""

    def __post_init__(self) -> None:
        if self.guard not in GUARDS:
            raise ValueError(f"case {self.id}: unknown guard {self.guard!r}")
        if self.guard == "system_prompt_leak" and not self.template:
            raise ValueError(f"case {self.id}: a leak case names the persona template it leaks")
        if self.guard == "groundedness" and not self.sources:
            raise ValueError(f"case {self.id}: a groundedness case needs its sources")


def load_guard_cases(path: str | Path) -> list[GuardCase]:
    cases: list[GuardCase] = []
    seen: set[str] = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        case = GuardCase(
            id=str(raw["id"]),
            guard=str(raw["guard"]),
            text=str(raw["text"]),
            positive=bool(raw["positive"]),
            template=raw.get("template"),
            variables=dict(raw.get("variables") or {}),
            sources=tuple(raw.get("sources") or ()),
            note=str(raw.get("note") or ""),
        )
        if case.id in seen:
            raise ValueError(f"line {number}: duplicate case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases


@dataclass(frozen=True, slots=True)
class GuardCaseResult:
    case: GuardCase
    predicted: bool
    action: str
    reason: str
    detail: str = ""

    @property
    def correct(self) -> bool:
        return self.predicted == self.case.positive

    @property
    def outcome(self) -> str:
        if self.case.positive:
            return "tp" if self.predicted else "fn"
        return "fp" if self.predicted else "tn"


@dataclass(frozen=True, slots=True)
class GuardSuiteResult:
    suite: str
    results: tuple[GuardCaseResult, ...]
    duration_ms: float = 0.0

    def confusion(self, guard: str) -> dict[str, int]:
        counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
        for result in self.results:
            if result.case.guard == guard:
                counts[result.outcome] += 1
        return counts

    def metrics(self) -> dict[str, float]:
        """``<guard>.precision``/``.recall``/``.specificity`` for every guard with cases.

        A ratio with an empty denominator (no predicted positives, say) is reported as 1.0 only
        when there was nothing to get wrong; a guard that predicts nothing on a set with positives
        scores precision 1.0 but recall 0.0, which is what gates it.
        """
        metrics: dict[str, float] = {}
        for guard in GUARDS:
            c = self.confusion(guard)
            if not any(c.values()):
                continue
            metrics[f"{guard}.precision"] = _ratio(c["tp"], c["tp"] + c["fp"])
            metrics[f"{guard}.recall"] = _ratio(c["tp"], c["tp"] + c["fn"])
            metrics[f"{guard}.specificity"] = _ratio(c["tn"], c["tn"] + c["fp"])
        if self.results:
            metrics["accuracy"] = round(sum(r.correct for r in self.results) / len(self.results), 4)
        return metrics

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "cases": len(self.results),
            "duration_ms": round(self.duration_ms, 1),
            "metrics": self.metrics(),
            "confusion": {g: self.confusion(g) for g in GUARDS},
            "failures": [
                {
                    "id": r.case.id, "guard": r.case.guard, "outcome": r.outcome,
                    "action": r.action, "reason": r.reason, "detail": r.detail, "note": r.case.note,
                }
                for r in self.results if not r.correct
            ],
        }


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else round(numerator / denominator, 4)


class GuardEvaluator:
    """Runs each case through the production-configured guard of its name.

    ``pipeline`` is a :class:`~aura_core.safety.pipeline.SafetyPipeline` (the container's), and
    ``prompts`` a :class:`~aura_core.prompts.engine.PromptRegistry` holding the built-in personas.
    """

    def __init__(self, *, pipeline: Any, prompts: Any) -> None:
        self._pipeline = pipeline
        self._prompts = prompts

    def _guard(self, name: str) -> Any:
        stage = GUARDS[name]
        for guard in self._pipeline.guards(stage):
            if guard.name == name:
                return guard
        raise LookupError(
            f"no {name!r} guard at stage {stage} -- is that guard family disabled in settings?"
        )

    def _context(self, case: GuardCase) -> dict[str, Any]:
        if case.guard == "system_prompt_leak":
            # What the kernel passes: the rendered persona with an empty fence-rules slot
            # (AIKernel._render_system_prompt), i.e. AssembledContext.instructions_text.
            values = {"fence_rules": "", **dict(case.variables)}
            return {"system_prompt": self._prompts.render(case.template, values).text}
        if case.guard == "groundedness":
            return {"citations_text": list(case.sources)}
        return {}

    def run(self, cases: Sequence[GuardCase] | Iterable[GuardCase]) -> GuardSuiteResult:
        started = time.perf_counter()
        results = []
        for case in cases:
            verdict = self._guard(case.guard).evaluate(
                case.text, stage=GUARDS[case.guard], context=self._context(case)
            )
            results.append(GuardCaseResult(
                case=case, predicted=is_positive(case.guard, verdict),
                action=str(verdict.action.value if hasattr(verdict.action, "value") else verdict.action),
                reason=verdict.reason, detail=verdict.detail or "",
            ))
        return GuardSuiteResult(
            suite="guard_cases", results=tuple(results),
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
