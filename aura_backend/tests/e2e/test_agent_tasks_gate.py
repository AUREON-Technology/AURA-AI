"""Phase P4: agent-task regression gate over the 50-case CI dataset.

``evaluation/suites/agent_tasks.jsonl`` is scored via ``AgentEvaluator`` against the default
``EchoProvider``, exactly as ``evaluation/runner.py`` (the ``Agent Evaluation`` GitHub Actions
workflow) does. This is a round-trip smoke gate, not an answer-quality gate: with ``EchoProvider``
the kernel's reply is the user's own message echoed back, so a case only passes when its
``expected_substrings`` are already present in the query text (or, for the small remainder, when
the query itself contains a completion marker like "done"). What this catches is a break in the
kernel/planning/tool-execution round trip that ``AgentEvaluator`` drives -- not whether a real
model would complete the task well. Real task-quality gating needs a real or scripted provider in
CI, the same follow-up ``test_docs_corpus_gate.py`` names for answer-level retrieval gating.

The dataset originally shipped with 8 of 50 cases' ``expected_substrings`` assuming a paraphrased
answer (e.g. "17" for a query that says "5pm", "memory" for a query that says "memories") that a
literal echo does not produce -- a dataset wording mismatch, not a harness bug (see the harness's
own ``_is_task_complete``). Fixed by matching each ``expected_substrings`` entry to the query's own
literal wording rather than a paraphrase, since that is what this smoke gate can actually check.
The recorded baseline is now a genuine 100% (50 of 50) -- not inflated to hide anything, since
every case's expectation is now something the query text actually contains.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aura_core.evaluation.harness import AgentEvaluator, RegressionGate, load_agent_cases

_SUITE_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "suites"
_BASELINE_PATH = _SUITE_DIR / "agent_tasks_baseline.json"


@pytest.fixture
def agent_tasks_cases():
    return load_agent_cases(_SUITE_DIR / "agent_tasks.jsonl")


class TestAgentTasksRegressionGate:
    async def test_suite_has_fifty_cases(self, agent_tasks_cases) -> None:
        assert len(agent_tasks_cases) == 50

    async def test_agent_round_trip_meets_the_recorded_baseline(
        self, container, agent_tasks_cases
    ) -> None:
        """The CI gate: fails the build if the agent round trip regresses beyond tolerance.

        The baseline is a real number from a real run (``agent_tasks_baseline.json``), captured
        after fixing ``AgentEvaluator.run`` -- it previously raised ``NameError`` on its first
        turn (``TurnRequest`` was never imported) and ``evaluation/runner.py`` could not even
        reach it (wrong settings constructor, wrong path arithmetic), so this suite had never
        completed a single run before.
        """
        if not _BASELINE_PATH.exists():
            pytest.fail(
                f"no baseline at {_BASELINE_PATH}; generate one with "
                "RegressionGate.save_baseline() from a genuine local run before gating CI on it"
            )

        await container.startup()
        result = await AgentEvaluator(container.kernel).run(agent_tasks_cases)

        baseline = RegressionGate.load_baseline(_BASELINE_PATH.read_text(encoding="utf-8"))
        gate = RegressionGate()
        ok, failures = gate.check(result, baseline)

        assert ok, "agent task regression vs. baseline:\n" + "\n".join(failures)
