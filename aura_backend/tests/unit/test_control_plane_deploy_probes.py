"""Deploy Control Plane's probes must match the routes the gateway serves (audit FSA-048 / F2-11).

The workflow polled `vars.RAILWAY_HEALTH_URL` for the string `"status"` and then derived the
version URL as `${RAILWAY_HEALTH_URL%/health}/health/version`. No value of the variable satisfies
both: `/health/ready` never contains `"status"`; `/health/live` does but then the version URL
becomes `…/health/live/health/version` (404, "revision mismatch"); `/health` is not a route at all.
The workflow could not pass once Railway was configured -- and it has failed on every run.

Now the workflow derives the origin from whatever health URL the operator stored, polls
`$origin/health/live` for `"alive"`, and verifies `$origin/health/version`. The derivation is one
line of shell; this runs it, through bash, for every shape the variable has plausibly had.
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


def test_readiness_polls_the_liveness_route_for_the_string_it_serves() -> None:
    wait = _steps()["Wait for readiness"]
    assert '"$origin/health/live"' in wait, "the poll must hit /health/live, the deploy gate route"
    assert "alive" in wait, "/health/live answers {\"status\":\"alive\"}; poll for that"


def test_the_revision_check_reads_the_version_route_from_the_same_origin() -> None:
    verify = _steps()["Verify the deployed revision"]
    assert '"$origin/health/version"' in verify
    assert "%/health}" not in verify, "the old suffix-stripping derivation is back"
