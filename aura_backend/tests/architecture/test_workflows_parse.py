"""Every GitHub Actions workflow must parse, and must declare what triggers it.

``eval-ci.yml`` shipped with ``AURA_DATABASE_URL: sqlite+aiosqlite:///:memory:`` unquoted. The
trailing ``:`` makes that invalid YAML ("mapping values are not allowed here"), so GitHub recorded
a failed run for the file on every push and the workflow itself never executed once -- and nothing
in the repository noticed, because no test ever loaded the workflow files. This one does.

PyYAML implements YAML 1.1, where the bare key ``on`` is read as boolean ``True``; either spelling
is accepted as the trigger key.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.y*ml"))


def test_workflow_directory_is_found() -> None:
    assert WORKFLOWS, f"no workflow files under {REPO / '.github' / 'workflows'}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_is_valid_yaml_with_triggers_and_jobs(path: Path) -> None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - the failure message is the point
        pytest.fail(f"{path.name} is not valid YAML: {exc}")

    assert isinstance(payload, dict), f"{path.name} does not parse to a mapping"
    assert "on" in payload or True in payload, f"{path.name} has no `on:` trigger key"
    assert payload.get("jobs"), f"{path.name} declares no jobs"


def test_eval_gate_runs_on_pull_requests() -> None:
    """Phase 14: the evaluation gate must run on PRs, not only on manual dispatch."""
    payload = yaml.safe_load((REPO / ".github/workflows/eval-ci.yml").read_text(encoding="utf-8"))
    triggers = payload.get("on", payload.get(True))
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers
    gate = payload["jobs"]["gate"]
    assert "if" not in gate, "the gate job must not be conditional on the event type"
    commands = " ".join(step.get("run", "") for step in gate["steps"])
    assert "evaluation.gate" in commands
