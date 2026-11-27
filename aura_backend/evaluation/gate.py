#!/usr/bin/env python
"""The merge-gating evaluation run: deterministic, model-free, in-memory.

``evaluation/runner.py`` runs one agent suite against whatever the environment configures. This
module is the other half: the fixed set of suites that ``Agent Evaluation`` (``eval-ci.yml``)
runs on every pull request touching the engines, each checked by
:class:`~aura_core.evaluation.harness.RegressionGate` against the baseline committed next to it
in ``evaluation/suites/``.

Every gate here is deterministic by construction, which is the only way a PR check can be
trusted to mean "this change regressed quality" rather than "the model sampled differently":

* ``agent_tasks`` -- the 50-case round trip through ``AIKernel`` with the default
  ``EchoProvider``. A smoke gate on the kernel/planning/tool-execution loop, not on answer
  quality (see ``tests/e2e/test_agent_tasks_gate.py`` for why echo can only check that).
* ``docs_corpus_retrieval`` -- recall@5/MRR/nDCG over the repository's own ADRs with the default
  hash embedder (Phase A3).
* ``docs_corpus_answer`` -- groundedness and citation coverage through the full kernel with
  ``ContextGroundedProvider``, a scripted provider that answers from the fenced retrieved content.
* ``agent_runs`` -- run-level agent-loop metrics (GAP-024): completion, partial completion, tool
  accuracy, iteration headroom, budget adherence, replan success and unsafe-action prevention,
  read from the durable ``AgentRun`` each scripted scenario records
  (``aura_core/evaluation/agent_runs.py``, ``evaluation/suites/agent_runs.jsonl``).
* ``guard_precision`` -- precision, recall and specificity of the leak, groundedness, injection
  and jailbreak guards on a hand-labelled corpus (EV-7, ``aura_core/evaluation/guards.py``,
  ``evaluation/suites/guard_cases.jsonl``), run through the guards the container's settings build.

The wiring deliberately mirrors ``tests/e2e/test_docs_corpus_gate.py`` and
``test_agent_tasks_gate.py`` (same container construction, same ``k``, same tolerances) so the
pytest suite and the PR check can never disagree about what "the baseline" means.

It refuses to run against anything but the in-memory substrate and the echo provider: a gate that
silently picked up a configured Postgres or Ollama would stop being deterministic, and its
baseline would stop meaning anything.

Usage (from ``aura_backend/``)::

    python -m evaluation.gate                                   # all gates, committed baselines
    python -m evaluation.gate --output eval-gate.json --summary "$GITHUB_STEP_SUMMARY"
    python -m evaluation.gate --baseline-dir /tmp/other-baselines   # gate against a copy

Exit status: 0 all gates pass, 1 a gate regressed (or a baseline is missing/incomplete),
2 the environment is not the deterministic one this gate is defined over.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SUITE_DIR = BACKEND_ROOT / "evaluation" / "suites"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Same profile tests/conftest.py pins, set before any aura_core import so settings resolve to it.
os.environ.setdefault("AURA_ENVIRONMENT", "local")
os.environ.setdefault("AURA_LOG_LEVEL", "WARNING")
os.environ.setdefault("AURA_LOG_FORMAT", "text")
os.environ.setdefault("AURA_METRICS_ENABLED", "false")

from aura_core.config import load_settings  # noqa: E402
from aura_core.container import Container  # noqa: E402
from aura_core.domain import DocumentId  # noqa: E402
from aura_core.evaluation.harness import (  # noqa: E402
    AgentEvaluator,
    AnswerEvaluator,
    EvalSuite,
    RegressionGate,
    RetrievalEvaluator,
    load_agent_cases,
)
from aura_core.adapters.runtime import FrozenClock  # noqa: E402
from aura_core.evaluation.agent_runs import (  # noqa: E402
    AgentRunEvaluator,
    load_agent_run_scenarios,
)
from aura_core.evaluation.guards import GuardEvaluator, load_guard_cases  # noqa: E402
from aura_core.llm.providers.echo import ContextGroundedProvider  # noqa: E402

from evaluation.suites.docs_corpus_sources import load_docs_corpus_sources  # noqa: E402

#: Retrieval depth the docs-corpus baseline was recorded at.
RETRIEVAL_K = 5
#: Matches TestDocsCorpusAnswerGate: groundedness/cited vary slightly with context-budget ties.
ANSWER_TOLERANCES: Mapping[str, float] = {"groundedness": 0.05, "cited": 0.05}
#: Prompt cap the answer gate measures under. At the shipped 400-token default no retrieved chunk
#: reaches the model (docs/reports/PROMPT_BUDGET_EVAL_2026-11-22.md), so answers carry no evidence
#: and groundedness is never measured. This gate therefore says nothing about the default cap.
ANSWER_PROMPT_TOKENS = 3000
#: The agent-run suite is fully scripted (planner responses, tool outcomes, a FrozenClock), so
#: any movement is a real behaviour change, not noise: the tolerance is only float rounding.
AGENT_RUN_TOLERANCES: Mapping[str, float] = {
    metric: 0.001 for metric in (
        "pass_rate", "completion_rate", "partial_completion", "tool_accuracy",
        "iteration_headroom", "budget_adherence", "replan_success", "unsafe_prevention_rate",
    )
}

#: Guards are pure functions of their input, so any movement is a real change in what they
#: decide -- the tolerance is only float rounding.
GUARD_TOLERANCES: Mapping[str, float] = {
    f"{guard}.{metric}": 0.001
    for guard in ("system_prompt_leak", "groundedness", "prompt_injection", "jailbreak")
    for metric in ("precision", "recall", "specificity")
} | {"accuracy": 0.001}


@dataclass(slots=True)
class GateOutcome:
    """One suite scored against its committed baseline."""

    name: str
    baseline_file: str
    tolerances: Mapping[str, float]
    measured: dict[str, float] = field(default_factory=dict)
    baseline: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    cases: int = 0
    duration_ms: float = 0.0
    case_failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def tolerance(self, metric: str) -> float:
        # RegressionGate's own fallback for a metric it has no explicit tolerance for.
        return self.tolerances.get(metric, 0.05)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.name,
            "ok": self.ok,
            "baseline_file": self.baseline_file,
            "cases": self.cases,
            "duration_ms": round(self.duration_ms, 1),
            "measured": self.measured,
            "baseline": self.baseline,
            "tolerances": {m: self.tolerance(m) for m in self.baseline},
            "failures": self.failures,
            "case_failures": self.case_failures,
        }


# --------------------------------------------------------------------------------------
# Environment pinning
# --------------------------------------------------------------------------------------


def deterministic_environment_problems(settings: Any) -> list[str]:
    """Why ``settings`` is not the model-free, in-memory profile this gate is defined over."""
    problems = []
    for provider in settings.llm.enabled_providers:
        if provider.kind != "echo":
            problems.append(
                f"LLM provider {provider.name!r} is kind {provider.kind!r}; the gate needs echo only "
                "(unset AURA_LLM_PROVIDERS)"
            )
    infra = settings.infrastructure
    for attr in ("database_url", "redis_url", "qdrant_url"):
        if getattr(infra, attr):
            problems.append(f"infrastructure.{attr} is set; the gate runs on in-memory adapters only")
    for attr in ("vector_backend", "document_backend", "memory_backend", "cache_backend"):
        if getattr(infra, attr) != "memory":
            problems.append(f"infrastructure.{attr}={getattr(infra, attr)!r}; expected 'memory'")
    return problems


def with_answer_prompt_cap(settings: Any) -> Any:
    """``settings`` with the prompt cap the answer gate measures under."""
    return replace(settings, llm=replace(settings.llm, max_prompt_tokens=ANSWER_PROMPT_TOKENS))


def _new_container(configure: Callable[[Any], Any] | None = None) -> Container:
    """What tests/conftest.py's ``container`` fixture builds, plus a gate's own settings change."""
    settings = load_settings(refresh=True, validate=False)
    return Container(configure(settings) if configure else settings)


# --------------------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------------------


def _load_baseline(path: Path) -> dict[str, float]:
    return RegressionGate.load_baseline(path.read_text(encoding="utf-8"))


def _docs_corpus_suite() -> EvalSuite:
    lines = (SUITE_DIR / "docs_corpus.jsonl").read_text(encoding="utf-8").splitlines()
    return EvalSuite.from_jsonl("docs_corpus", lines)


async def _ingest_docs_corpus(container: Container) -> None:
    for document_id, title, content in load_docs_corpus_sources():
        await container.knowledge.ingest(
            content=content, title=title, document_id=DocumentId(document_id), owner_id=None,
        )


async def _score_agent_tasks(container: Container) -> Any:
    cases = load_agent_cases(SUITE_DIR / "agent_tasks.jsonl")
    return await AgentEvaluator(container.kernel).run(cases)


async def _score_docs_retrieval(container: Container) -> Any:
    await _ingest_docs_corpus(container)
    return await RetrievalEvaluator(container.knowledge, k=RETRIEVAL_K).run(_docs_corpus_suite())


async def _score_docs_answer(container: Container) -> Any:
    await _ingest_docs_corpus(container)
    # Same override TestDocsCorpusAnswerGate applies: the scripted provider under echo's name.
    container.router._providers["echo"] = ContextGroundedProvider(name="echo", model="echo-1")
    return await AnswerEvaluator(container.kernel).run(_docs_corpus_suite())


async def _score_agent_runs(container: Container) -> Any:
    scenarios = load_agent_run_scenarios(SUITE_DIR / "agent_runs.jsonl")
    evaluator = AgentRunEvaluator.for_container(container, clock=FrozenClock())
    return await evaluator.run(scenarios)


async def _score_guards(container: Container) -> Any:
    cases = load_guard_cases(SUITE_DIR / "guard_cases.jsonl")
    return GuardEvaluator(pipeline=container.safety, prompts=container.prompts).run(cases)


@dataclass(frozen=True, slots=True)
class GateSpec:
    name: str
    baseline_file: str
    score: Callable[[Container], Awaitable[Any]]
    tolerances: Mapping[str, float] = field(default_factory=lambda: dict(RegressionGate().tolerances))
    configure: Callable[[Any], Any] | None = None


GATES: tuple[GateSpec, ...] = (
    GateSpec("agent_tasks", "agent_tasks_baseline.json", _score_agent_tasks),
    GateSpec("docs_corpus_retrieval", "docs_corpus_baseline.json", _score_docs_retrieval),
    GateSpec(
        "docs_corpus_answer", "docs_corpus_answer_baseline.json", _score_docs_answer,
        tolerances=ANSWER_TOLERANCES, configure=with_answer_prompt_cap,
    ),
    GateSpec(
        "agent_runs", "agent_runs_baseline.json", _score_agent_runs,
        tolerances=AGENT_RUN_TOLERANCES,
    ),
    GateSpec(
        "guard_precision", "guard_cases_baseline.json", _score_guards,
        tolerances=GUARD_TOLERANCES,
    ),
)


async def run_gate(spec: GateSpec, baseline_dir: Path) -> GateOutcome:
    baseline_path = baseline_dir / spec.baseline_file
    outcome = GateOutcome(
        name=spec.name, baseline_file=str(baseline_path), tolerances=dict(spec.tolerances)
    )
    if not baseline_path.is_file():
        outcome.failures.append(
            f"no baseline at {baseline_path}; record one with RegressionGate.save_baseline() "
            "from a genuine run before gating on it"
        )
        return outcome
    outcome.baseline = _load_baseline(baseline_path)

    container = _new_container(spec.configure)
    await container.startup()
    try:
        result = await spec.score(container)
    finally:
        await container.shutdown()

    outcome.measured = result.metrics()
    outcome.cases = len(result.results)
    outcome.duration_ms = result.duration_ms
    outcome.case_failures = result.as_dict().get("failures", [])

    # RegressionGate skips a baselined metric the run no longer produces. For a merge gate that
    # is a hole -- a metric vanishing (renamed, or its evaluator path no longer reached) would
    # read as "no regression" -- so it is a failure here.
    for metric in outcome.baseline:
        if metric not in outcome.measured:
            outcome.failures.append(f"{metric} is in the baseline but was not measured by this run")

    ok, failures = RegressionGate(tolerances=spec.tolerances).check(result, outcome.baseline)
    outcome.failures.extend(failures)
    return outcome


async def run_gates(baseline_dir: Path, only: set[str] | None = None) -> list[GateOutcome]:
    outcomes = []
    for spec in GATES:
        if only and spec.name not in only:
            continue
        outcomes.append(await run_gate(spec, baseline_dir))
    return outcomes


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------


def render_markdown(outcomes: list[GateOutcome]) -> str:
    overall = all(o.ok for o in outcomes)
    lines = [
        f"## Evaluation regression gate: {'PASS' if overall else 'FAIL'}",
        "",
        "Deterministic, model-free (echo / `ContextGroundedProvider`), in-memory. "
        "A metric fails when it drops below its committed baseline by more than its tolerance.",
        "",
        "| Gate | Metric | Baseline | Current | Delta | Tolerance | Status |",
        "|---|---|---:|---:|---:|---:|:---:|",
    ]
    for o in outcomes:
        metrics = list(o.baseline) + [m for m in sorted(o.measured) if m not in o.baseline]
        if not metrics:
            lines.append(f"| `{o.name}` | - | - | - | - | - | FAIL |")
        for metric in metrics:
            base = o.baseline.get(metric)
            cur = o.measured.get(metric)
            if base is None:
                status, delta, tol = "info", "", ""
            elif cur is None:
                status, delta, tol = "FAIL", "", f"{o.tolerance(metric):.3f}"
            else:
                drop = base - cur
                status = "FAIL" if drop > o.tolerance(metric) else "pass"
                delta = f"{cur - base:+.4f}"
                tol = f"{o.tolerance(metric):.3f}"
            lines.append(
                f"| `{o.name}` | {metric} | {'' if base is None else f'{base:.4f}'} | "
                f"{'' if cur is None else f'{cur:.4f}'} | {delta} | {tol} | {status} |"
            )
    lines.append("")
    for o in outcomes:
        if o.failures:
            lines.append(f"**{o.name}** failures:")
            lines.extend(f"- {f}" for f in o.failures)
            lines.append("")
    lines.append(
        "Metrics marked `info` are reported but not baselined. To accept an intentional change, "
        "re-record the baseline from a genuine run and commit it with the change that moved it."
    )
    return "\n".join(lines) + "\n"


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic evaluation merge gate")
    parser.add_argument("--baseline-dir", type=Path, default=SUITE_DIR,
                        help="Directory holding the *_baseline.json files (default: committed ones)")
    parser.add_argument("--only", action="append", choices=[g.name for g in GATES],
                        help="Run only this gate (repeatable)")
    parser.add_argument("--output", type=Path, help="Write the full JSON result here")
    parser.add_argument("--summary", type=Path,
                        help="Append the Markdown metric table here (e.g. $GITHUB_STEP_SUMMARY)")
    args = parser.parse_args(argv)

    problems = deterministic_environment_problems(load_settings(refresh=True, validate=False))
    if problems:
        print("Refusing to run: the evaluation gate is defined over the in-memory, echo-only "
              "profile.", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    outcomes = await run_gates(args.baseline_dir, set(args.only) if args.only else None)
    markdown = render_markdown(outcomes)
    print(markdown)

    if args.output:
        args.output.write_text(
            json.dumps({"ok": all(o.ok for o in outcomes), "gates": [o.as_dict() for o in outcomes]},
                       indent=2),
            encoding="utf-8",
        )
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(markdown)

    return 0 if all(o.ok for o in outcomes) else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    sys.exit(main())
