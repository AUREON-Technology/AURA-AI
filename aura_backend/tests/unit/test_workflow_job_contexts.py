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


def _job(workflow: str, job: str) -> dict:
    spec = yaml.safe_load((_WORKFLOWS / workflow).read_text(encoding="utf-8"))
    return spec["jobs"][job]


def _step(job: dict, name: str) -> dict:
    matches = [s for s in job["steps"] if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def _run_step(script: str, env: dict[str, str], tmp_path: Path) -> tuple[int, str]:
    """Run a workflow step's `run:` the way the runner does (bash -e), with GITHUB_OUTPUT."""
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    full_env = {"PATH": os.environ.get("PATH", ""), "GITHUB_OUTPUT": str(output), **env}
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env=full_env, capture_output=True, text=True, cwd=tmp_path, timeout=60,
    )
    return result.returncode, output.read_text(encoding="utf-8")


class TestDeployVpsTargetShaIsValidatedAndPassedSafely:
    """F47. The `sha` dispatch input is free text. It used to reach the host unvalidated, inside an
    ssh command line the remote shell re-parses (`ssh host env AURA_SHA="$TARGET_SHA" ...`), and
    then `git show "${AURA_SHA}:..."` -- so a crafted input was remote code execution as root, and
    any SHA, on main or not, could be shipped. It must be a full SHA, on origin/main, passed as
    data, and the scripts it selects must land in private temp directories."""

    _VALID = "0123456789abcdef0123456789abcdef01234567"

    def _resolve(self) -> str:
        return _step(_job("deploy-backend.yml", "deploy"), "Resolve target SHA")["run"]

    @pytest.mark.parametrize(
        "bad",
        [
            "main",
            "0123456",
            "x; touch /tmp/pwned",
            '$(touch /tmp/pwned)',
            "0123456789ABCDEF0123456789ABCDEF01234567",
            _VALID + "\n" + _VALID,
            _VALID + " ",
        ],
    )
    def test_a_dispatched_sha_that_is_not_a_full_hex_sha_fails(self, bad: str, tmp_path: Path) -> None:
        code, output = _run_step(
            self._resolve(),
            {"EVENT_NAME": "workflow_dispatch", "DISPATCH_SHA": bad, "RUN_SHA": self._VALID, "WORKFLOW_SHA": ""},
            tmp_path,
        )
        assert code != 0, f"{bad!r} was accepted"
        assert "sha=" not in output

    def test_a_full_sha_is_resolved(self, tmp_path: Path) -> None:
        code, output = _run_step(
            self._resolve(),
            {"EVENT_NAME": "workflow_dispatch", "DISPATCH_SHA": self._VALID, "RUN_SHA": "", "WORKFLOW_SHA": ""},
            tmp_path,
        )
        assert code == 0
        assert output.strip() == f"sha={self._VALID}"

    def test_the_runner_proves_the_sha_is_on_main_before_shipping_it(self) -> None:
        job = _job("deploy-backend.yml", "deploy")
        names = [s.get("name") for s in job["steps"]]
        assert "Verify the SHA is on main" in names
        assert names.index("Verify the SHA is on main") < names.index("Deploy")
        run = _step(job, "Verify the SHA is on main")["run"]
        assert "git merge-base --is-ancestor" in run and "origin/main" in run
        checkout = job["steps"][0]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout.get("with", {}).get("fetch-depth") == 0, "ancestry needs main's history"

    def test_nothing_is_interpolated_into_the_remote_command_line(self) -> None:
        run = _step(_job("deploy-backend.yml", "deploy"), "Deploy")["run"]
        assert 'AURA_SHA="$TARGET_SHA"' not in run
        assert "env AURA_SHA" not in run
        # Values travel as %q-quoted exports on stdin; the remote command is the literal `bash -s`.
        assert "printf 'export AURA_SHA=%q\\n'" in run
        ssh_line = next(line for line in run.splitlines() if line.strip().startswith('"${VPS_USER}@${VPS_HOST}"'))
        assert ssh_line.split() == ['"${VPS_USER}@${VPS_HOST}"', "bash", "-s"]

    def test_the_host_rechecks_ancestry_and_uses_a_private_temp_dir(self) -> None:
        run = _step(_job("deploy-backend.yml", "deploy"), "Deploy")["run"]
        assert "/tmp/ci-deploy.sh" not in run
        assert "mktemp -d" in run
        assert "merge-base --is-ancestor" in run

    def test_ci_deploy_uses_a_private_temp_dir(self) -> None:
        script = (_WORKFLOWS.parents[1] / "deploy" / "scripts" / "ci-deploy.sh").read_text(encoding="utf-8")
        assert "/tmp/vps-update.sh" not in script
        assert "mktemp -d" in script

    def test_ci_deploy_refuses_a_malformed_sha_before_doing_anything(self, tmp_path: Path) -> None:
        import shutil
        import subprocess

        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("bash is not available")
        script = _WORKFLOWS.parents[1] / "deploy" / "scripts" / "ci-deploy.sh"
        marker = tmp_path / "pwned"
        result = subprocess.run(
            [bash, str(script)],
            env={"PATH": "/usr/bin:/bin", "AURA_SHA": f"x$(touch {marker})", "AURA_REPO": str(tmp_path / "nope")},
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode != 0
        assert "AURA_SHA" in result.stderr
        assert not marker.exists()


class TestControlPlaneDeploysTheTestedCommit:
    """F48. On `workflow_run`, a bare checkout is the default branch's *current* head, not the
    commit Backend CI tested -- so `railway up` could upload code no CI run ever saw."""

    def test_the_deploy_job_checks_out_the_tested_sha(self) -> None:
        checkout = _job("control-plane-deploy.yml", "deploy")["steps"][0]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"]["ref"] == "${{ github.event.workflow_run.head_sha || github.sha }}"
