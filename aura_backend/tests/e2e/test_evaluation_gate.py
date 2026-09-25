"""The PR-time evaluation gate (``evaluation/gate.py``) fails when it should.

``test_docs_corpus_gate.py`` and ``test_agent_tasks_gate.py`` prove the committed baselines hold
on the current tree. What they cannot show is that the gate the ``Agent Evaluation`` workflow runs
would actually go red on a regression -- a gate that always passes looks identical to a working
one on a green tree. These tests feed it baselines it cannot meet and check that it says so.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import gate as eval_gate


def _copy_baselines(tmp_path: Path) -> Path:
    for spec in eval_gate.GATES:
        source = eval_gate.SUITE_DIR / spec.baseline_file
        (tmp_path / spec.baseline_file).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def _spec(name: str) -> eval_gate.GateSpec:
    return next(s for s in eval_gate.GATES if s.name == name)


async def test_committed_baselines_pass() -> None:
    outcome = await eval_gate.run_gate(_spec("docs_corpus_retrieval"), eval_gate.SUITE_DIR)
    assert outcome.ok, outcome.failures


async def test_a_baseline_above_what_the_tree_achieves_fails(tmp_path: Path) -> None:
    baseline_dir = _copy_baselines(tmp_path)
    path = baseline_dir / "docs_corpus_answer_baseline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["metrics"]["cited"] = 0.99
    path.write_text(json.dumps(payload), encoding="utf-8")

    outcome = await eval_gate.run_gate(_spec("docs_corpus_answer"), baseline_dir)

    assert not outcome.ok
    assert any(f.startswith("cited fell") for f in outcome.failures)
    assert "| FAIL |" in eval_gate.render_markdown([outcome])


async def test_a_missing_baseline_fails_rather_than_passing(tmp_path: Path) -> None:
    outcome = await eval_gate.run_gate(_spec("agent_tasks"), tmp_path)
    assert not outcome.ok
    assert "no baseline" in outcome.failures[0]


async def test_a_baselined_metric_the_run_no_longer_produces_fails(tmp_path: Path) -> None:
    baseline_dir = _copy_baselines(tmp_path)
    path = baseline_dir / "agent_tasks_baseline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["metrics"]["renamed_metric"] = 1.0
    path.write_text(json.dumps(payload), encoding="utf-8")

    outcome = await eval_gate.run_gate(_spec("agent_tasks"), baseline_dir)

    assert not outcome.ok
    assert any("renamed_metric" in f for f in outcome.failures)


@pytest.mark.parametrize(
    ("llm_kind", "infra", "expect_problem"),
    [
        ("echo", {}, False),
        ("ollama", {}, True),
        ("echo", {"database_url": "postgresql://db/aura"}, True),
        ("echo", {"vector_backend": "qdrant"}, True),
    ],
)
def test_refuses_a_non_deterministic_environment(llm_kind, infra, expect_problem) -> None:
    base_infra = {
        "database_url": "", "redis_url": "", "qdrant_url": "",
        "vector_backend": "memory", "document_backend": "memory",
        "memory_backend": "memory", "cache_backend": "memory",
    }
    settings = SimpleNamespace(
        llm=SimpleNamespace(enabled_providers=(SimpleNamespace(name="p", kind=llm_kind),)),
        infrastructure=SimpleNamespace(**{**base_infra, **infra}),
    )
    assert bool(eval_gate.deterministic_environment_problems(settings)) is expect_problem
