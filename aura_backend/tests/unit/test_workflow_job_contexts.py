"""No workflow may reference `secrets` from a job-level `if:` (BLOCK-04).

GitHub exposes only `github`, `needs`, `vars` and `inputs` to ``jobs.<job_id>.if``. A `secrets`
reference there does not evaluate to false -- it makes the whole file unevaluable, so the run
ends as a *startup failure* with zero jobs created and no log to read. Every job in the file
dies, including ones that never mentioned a secret.

Two deploy workflows shipped that way and failed silently in 0 seconds on every push for weeks.
It is a uniquely expensive mistake because it looks like a deploy problem, produces no
diagnostics, and a YAML linter accepts the file -- the syntax is valid, the *context* is not.

The pattern it replaces is a `preflight` job that tests the secret inside a `run:` step, where
`secrets` is available, and publishes a boolean output the dependent jobs gate on. This checks
the mistake cannot return, and that a deploy which cannot run is still legible rather than
silently absent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def _workflow_files() -> list[Path]:
    files = sorted(_WORKFLOWS.glob("*.yml")) + sorted(_WORKFLOWS.glob("*.yaml"))
    assert files, f"no workflow files found under {_WORKFLOWS}"
    return files


def _ids(path: Path) -> str:
    return path.name


@pytest.mark.parametrize("path", _workflow_files(), ids=_ids)
def test_no_job_level_if_references_secrets(path: Path) -> None:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = (workflow or {}).get("jobs") or {}

    offenders = [
        name
        for name, job in jobs.items()
        if isinstance(job, dict) and re.search(r"\bsecrets\s*\.", str(job.get("if", "")))
    ]

    assert not offenders, (
        f"{path.name}: job-level `if:` cannot read the `secrets` context "
        f"(jobs: {offenders}). This makes the entire workflow a startup failure with zero jobs "
        "created. Move the check into a `preflight` job's `run:` step and gate on its output -- "
        "see control-plane-deploy.yml."
    )


@pytest.mark.parametrize("path", _workflow_files(), ids=_ids)
def test_job_level_if_uses_only_available_contexts(path: Path) -> None:
    """The same trap with a different name: `env` and `steps` are not available there either."""
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = (workflow or {}).get("jobs") or {}
    forbidden = ("secrets", "env", "steps", "job", "runner")

    for name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        condition = str(job.get("if", ""))
        for context in forbidden:
            assert not re.search(rf"\b{context}\s*\.", condition), (
                f"{path.name}:{name} reads the `{context}` context in a job-level `if:`, which "
                f"GitHub does not provide there (only github, needs, vars, inputs)."
            )


class TestDeployPreflight:
    """A deploy that cannot run must say so out loud, not conclude `skipped`.

    `skipped` renders as a grey tick and satisfies branch protection, so a deploy path that
    stopped working stays invisible -- production served a two-week-old build while `Deploy VPS`
    reported `skipped` on every merge.
    """

    @pytest.mark.parametrize(
        "workflow,switch",
        [
            ("control-plane-deploy.yml", "CONTROL_PLANE_DEPLOY_REQUIRED"),
            ("inference-plane-deploy.yml", "INFERENCE_DEPLOY_REQUIRED"),
        ],
    )
    def test_a_required_deploy_fails_when_unconfigured(self, workflow: str, switch: str) -> None:
        body = (_WORKFLOWS / workflow).read_text(encoding="utf-8")

        assert f"vars.{switch}" in body, f"{workflow} has no required-deploy switch"
        assert "::error::" in body, f"{workflow} never fails loudly on a missing setting"

    @pytest.mark.parametrize(
        "workflow", ["control-plane-deploy.yml", "inference-plane-deploy.yml"]
    )
    def test_the_preflight_publishes_an_output_the_deploy_gates_on(self, workflow: str) -> None:
        spec = yaml.safe_load((_WORKFLOWS / workflow).read_text(encoding="utf-8"))
        jobs = spec["jobs"]

        assert "preflight" in jobs, f"{workflow} has no preflight job"
        assert "configured" in (jobs["preflight"].get("outputs") or {})
        assert "needs.preflight.outputs.configured" in str(jobs["deploy"]["if"])

    @pytest.mark.parametrize(
        "workflow", ["control-plane-deploy.yml", "inference-plane-deploy.yml"]
    )
    def test_the_preflight_never_prints_the_secret(self, workflow: str) -> None:
        """It may name which settings are missing; it may not reveal a value.

        The preflight has to handle the secret to test it, which makes "does it only test it"
        worth pinning: an `echo "$HF_TOKEN"` added while debugging would publish the token to a
        log that outlives the run.
        """
        spec = yaml.safe_load((_WORKFLOWS / workflow).read_text(encoding="utf-8"))
        steps = spec["jobs"]["preflight"]["steps"]

        secret_vars = [
            name
            for step in steps
            for name, value in (step.get("env") or {}).items()
            if "secrets." in str(value)
        ]
        assert secret_vars, f"{workflow} preflight tests no secret at all"

        for step in steps:
            script = step.get("run", "")
            for var in secret_vars:
                # Only ever as `[ -n "$VAR" ]`. Any other interpolation could reach a log,
                # an output, or the step summary.
                for hit in re.findall(rf'\$\{{?{var}\}}?|"\${var}"', script):
                    context = script[max(0, script.find(hit) - 12) : script.find(hit) + len(hit)]
                    assert "-n " in context or "-z " in context, (
                        f"{workflow} preflight uses ${var} outside an emptiness test: "
                        f"...{context}..."
                    )


def _triggers(spec: dict) -> dict:
    # PyYAML (YAML 1.1) reads a bare `on:` key as the boolean True.
    on = spec.get("on", spec.get(True)) or {}
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {name: None for name in on}
    return on


def _workflow_run_workflows() -> list[Path]:
    return [
        path
        for path in _workflow_files()
        if "workflow_run" in _triggers(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    ]


class TestWorkflowRunTrust:
    """A `workflow_run` job runs only for a push to main in this repository (F-38 / SEC-11).

    `workflow_run.branches: [main]` matches the head branch *name*. A pull request from a fork
    whose branch is called `main` matches it, its Backend CI run completes, and the triggered
    workflow then runs in the base repository -- with the production environment and its
    secrets. The event, the branch and the head repository all have to be checked.
    """

    _REQUIRED = (
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_branch == 'main'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
    )

    def test_the_deploy_workflows_are_the_ones_checked(self) -> None:
        names = {path.name for path in _workflow_run_workflows()}
        assert {"deploy-backend.yml", "control-plane-deploy.yml"} <= names

    @pytest.mark.parametrize("path", _workflow_run_workflows(), ids=_ids)
    def test_every_job_requires_a_trusted_push(self, path: Path) -> None:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in (spec.get("jobs") or {}).items():
            condition = " ".join(str(job.get("if", "")).split())
            missing = [check for check in self._REQUIRED if check not in condition]
            assert not missing, (
                f"{path.name}:{name} can be started by a workflow_run that is not a push to "
                f"main in this repository; its `if:` lacks {missing}."
            )
