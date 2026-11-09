"""Deploy Control Plane's probes must match the routes the gateway serves (audit FSA-048 / F2-11).

The workflow polled `vars.RAILWAY_HEALTH_URL` for the string `"status"` and then derived the
version URL as `${RAILWAY_HEALTH_URL%/health}/health/version`. No value of the variable satisfies
both: `/health/ready` never contains `"status"`; `/health/live` does but then the version URL
becomes `…/health/live/health/version` (404, "revision mismatch"); `/health` is not a route at all.
The workflow could not pass once Railway was configured -- and it has failed on every run.

Now the workflow derives the origin from whatever health URL the operator stored, polls
`$origin/health/version` until it reports the SHA this run deployed (Railway keeps the previous
deployment serving until the new one is healthy, so a bare liveness poll answers from the old
container), and verifies that route once more. The derivation is one line of shell; this runs it,
through bash, for every shape the variable has plausibly had, and runs the wait step itself with
a scripted `curl`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "control-plane-deploy.yml"


def _steps() -> dict[str, str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return {
        step["name"]: step["run"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if "name" in step and "run" in step
    }


def _origin_line() -> str:
    for script in _steps().values():
        for line in script.splitlines():
            if re.match(r"\s*origin=", line):
                return line.strip()
    raise AssertionError("the deploy job never derives an origin from RAILWAY_HEALTH_URL")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to run the workflow's shell")
@pytest.mark.parametrize("health_url", [
    "https://aureon-ai.tech/health",
    "https://aureon-ai.tech/health/live",
    "https://aureon-ai.tech/health/ready",
    "https://aureon-ai.tech",
    "https://aureon-ai.tech/",
])
def test_the_origin_is_the_same_whatever_health_path_the_variable_carries(health_url: str) -> None:
    script = _origin_line() + '\nprintf "%s" "$origin"'
    result = subprocess.run(
        ["bash", "-c", script], env={"RAILWAY_HEALTH_URL": health_url, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    )
    assert result.stdout == "https://aureon-ai.tech"


def _run_wait_step(tmp_path: Path, shas: list[str], expected: str) -> tuple[subprocess.CompletedProcess, int]:
    """Run the "Wait for readiness" script with a `curl` that answers `/health/version` with the
    given SHAs in turn (the last one repeating) and a `sleep` that returns at once."""
    shims = tmp_path / "bin"
    shims.mkdir()
    counter = tmp_path / "calls"
    counter.write_text("0")
    answers = "\n".join(shas)
    (shims / "curl").write_text(
        "#!/bin/bash\n"
        f"n=$(cat {counter}); n=$((n + 1)); echo $n > {counter}\n"
        f"sha=$(printf '%s\\n' '{answers}' | sed -n \"${{n}}p\")\n"
        f"[ -n \"$sha\" ] || sha=$(printf '%s\\n' '{answers}' | tail -1)\n"
        "printf '{\"git_sha\": \"%s\", \"status\": \"alive\"}' \"$sha\"\n",
        encoding="utf-8",
    )
    (shims / "sleep").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    for shim in (shims / "curl", shims / "sleep"):
        shim.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", _steps()["Wait for readiness"]],
        env={
            "RAILWAY_HEALTH_URL": "https://aureon-ai.tech/health/live",
            "EXPECTED_SHA": expected,
            "PATH": f"{shims}:/usr/bin:/bin",
        },
        capture_output=True, text=True,
    )
    return result, int(counter.read_text())


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to run the workflow's shell")
def test_readiness_waits_for_the_deployed_revision_not_for_any_live_container(tmp_path: Path) -> None:
    """`railway up --detach` returns at once and Railway keeps the previous deployment serving
    until the new one is healthy, so `/health/live` answers `"alive"` from the *old* container on
    the first poll. A wait that stops there hands the revision check a stale SHA and the job
    fails with "mismatch" whenever the build outlasts one poll. The wait must poll
    `/health/version` until it reports the SHA this run deployed."""
    result, calls = _run_wait_step(tmp_path, ["0ld0ld0", "0ld0ld0", "n3wn3wn"], expected="n3wn3wn")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls >= 3, "the wait stopped before the new revision was serving"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to run the workflow's shell")
def test_readiness_fails_when_the_deployed_revision_never_arrives(tmp_path: Path) -> None:
    result, _calls = _run_wait_step(tmp_path, ["0ld0ld0"], expected="n3wn3wn")
    assert result.returncode != 0
    assert "n3wn3wn" in result.stderr + result.stdout, "the failure must name the revision it waited for"


def test_the_revision_check_reads_the_version_route_from_the_same_origin() -> None:
    verify = _steps()["Verify the deployed revision"]
    assert '"$origin/health/version"' in verify
    assert "%/health}" not in verify, "the old suffix-stripping derivation is back"
