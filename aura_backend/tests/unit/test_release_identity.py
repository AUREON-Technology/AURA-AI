"""A running process must be able to name the commit it was built from (BLOCK-03).

A real production audit spent most of its effort answering a question that should cost one
request: *which build is live?* It could only be bounded indirectly -- by noticing `/health`
still advertised a settings field that current `main` had deleted -- and the answer turned out to
be a two-week-old image that every later merge had silently failed to replace.

These cover the four halves of the fix, because it only works if all of them hold:

* ``ReleaseSettings`` reads the injected identity and, crucially, admits when there isn't one
  rather than reporting something plausible.
* ``/health/version`` serves it, unauthenticated and without touching a dependency.
* ``validate()`` refuses to run an unidentified build in production.
* the Dockerfile accepts the build args and every compose service that builds the image passes
  them -- a settings field nothing injects would be identity theatre.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aura_core.config.settings import AuraSettings, Environment, ReleaseSettings
from aura_core.domain.errors import ConfigurationError
from aura_core.domain.identifiers import UserId

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND = Path(__file__).resolve().parents[2]
_COMPOSE_FILES = (
    _REPO_ROOT / "deploy" / "docker-compose.prod.yml",
    _REPO_ROOT / "deploy" / "docker-compose.dev.yml",
)
_BUILD_ARGS = ("GIT_SHA", "GIT_REF", "BUILD_TIME", "DEPLOYMENT_ID")

_SHA = "0123456789abcdef0123456789abcdef01234567"


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("release-caller"), role="User")

    return TestClient(build_app(container, authenticator=authenticator))


class TestReleaseSettings:
    def test_reads_the_injected_identity(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_GIT_SHA", _SHA)
        monkeypatch.setenv("AURA_GIT_REF", "refs/heads/main")
        monkeypatch.setenv("AURA_BUILD_TIME", "2026-09-27T18:00:00Z")
        monkeypatch.setenv("AURA_DEPLOYMENT_ID", "run-12345")

        release = ReleaseSettings.from_env()

        assert release.git_sha == _SHA
        assert release.git_ref == "refs/heads/main"
        assert release.build_time == "2026-09-27T18:00:00Z"
        assert release.deployment_id == "run-12345"
        assert release.identified is True
        assert release.revision == "0123456789ab"

    def test_admits_an_unidentified_build_rather_than_guessing(self, monkeypatch) -> None:
        """The failure mode worth protecting against is a confident wrong answer."""
        # CI and PaaS runners export their own SHA/ref/run variables, which from_env() falls back to.
        for key in ("AURA_GIT_SHA", "AURA_GIT_REF", "AURA_BUILD_TIME", "AURA_DEPLOYMENT_ID",
                    *ReleaseSettings.PLATFORM_SHA_VARS, *ReleaseSettings.PLATFORM_REF_VARS,
                    *ReleaseSettings.PLATFORM_DEPLOY_VARS):
            monkeypatch.delenv(key, raising=False)

        release = ReleaseSettings.from_env()

        assert release.identified is False
        assert release.revision == "unknown"
        assert release.git_sha == ""

    def test_whitespace_only_injection_does_not_count_as_identified(self, monkeypatch) -> None:
        """`--build-arg GIT_SHA=` and a stray newline both arrive as whitespace, not absence."""
        for key in ReleaseSettings.PLATFORM_SHA_VARS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("AURA_GIT_SHA", "  \n ")

        assert ReleaseSettings.from_env().identified is False

    def test_the_public_payload_carries_no_configuration(self) -> None:
        """Served unauthenticated, so what it may contain is a security property.

        Asserted as an allowlist rather than a denylist: a future field added to
        ReleaseSettings should have to be named here deliberately before it reaches the wire.
        """
        payload = ReleaseSettings(git_sha=_SHA).to_public_dict()

        assert set(payload) == {
            "app_version",
            "api_version",
            "revision",
            "git_sha",
            "git_ref",
            "build_time",
            "deployment_id",
            "inference_contract",
            "identified",
        }
        flat = " ".join(str(v).lower() for v in payload.values())
        for forbidden in ("secret", "token", "password", "key=", "dsn", "postgres://"):
            assert forbidden not in flat


def _problems(settings: AuraSettings) -> list[str]:
    """The validation findings, or an empty list.

    ``validate()`` raises with every problem collected in ``details``, and a bare
    ``AuraSettings`` is never fully valid (no LLM provider is configured by default). So these
    assert on the one finding under test rather than on whether the call raised at all --
    otherwise an unrelated rule tightening elsewhere would read as a release-identity
    regression, and this test would be measuring the wrong thing.
    """
    try:
        settings.validate()
    except ConfigurationError as exc:
        return list(exc.details.get("problems", []))
    return []


def _mentions_git_sha(problems: list[str]) -> bool:
    return any("release.git_sha" in problem for problem in problems)


class TestAnAnonymousBuildWarnsButStillServes:
    """The regression this class exists to prevent.

    This check was briefly fatal, and it caused an outage. Railway builds the Dockerfile through
    its own git integration and never passes `--build-arg GIT_SHA`, so `AURA_GIT_SHA` was empty,
    `validate()` raised, the container crash-looped, and a working production deployment served
    502 to every request for an hour.

    The severity never justified failing closed. Every other production check in `validate()`
    prevents an outcome *worse* than not serving -- mock auth, an in-memory system of record.
    An unidentified build is a diagnosability problem, and a feature whose entire purpose is to
    make incidents easier to diagnose must never be able to cause one.
    """

    @pytest.mark.parametrize(
        "environment",
        [Environment.PRODUCTION, Environment.STAGING, Environment.LOCAL, Environment.DEVELOPMENT],
    )
    def test_no_environment_refuses_to_boot_over_release_identity(
        self, environment: Environment
    ) -> None:
        problems = _problems(AuraSettings(environment=environment))

        assert not _mentions_git_sha(problems), (
            "release identity must never block a boot: an unidentified build is harder to debug, "
            "a process that will not start serves nobody."
        )

    def test_it_warns_loudly_instead(self, caplog) -> None:
        """Silence would be the other failure: the identity would just quietly read 'unknown'."""
        with caplog.at_level("WARNING", logger="aura_core.config"):
            _problems(AuraSettings(environment=Environment.PRODUCTION))

        assert any(
            "release identity is not injected" in record.message for record in caplog.records
        ), "an anonymous production build must still say so"

    def test_an_identified_build_does_not_warn(self, caplog) -> None:
        with caplog.at_level("WARNING", logger="aura_core.config"):
            _problems(
                AuraSettings(
                    environment=Environment.PRODUCTION, release=ReleaseSettings(git_sha=_SHA)
                )
            )

        assert not any("release identity" in r.message for r in caplog.records)


class TestPlatformMetadataFallback:
    """A PaaS that builds the Dockerfile itself never passes our build args, but it does export
    its own commit SHA. Reading it is what makes the identity populated by default rather than
    dependent on someone remembering to wire a build arg."""

    @pytest.mark.parametrize(
        "variable",
        ["RAILWAY_GIT_COMMIT_SHA", "RENDER_GIT_COMMIT", "HEROKU_SLUG_COMMIT", "GITHUB_SHA"],
    )
    def test_a_platform_commit_variable_is_picked_up(self, monkeypatch, variable: str) -> None:
        for name in ReleaseSettings.PLATFORM_SHA_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("AURA_GIT_SHA", raising=False)
        monkeypatch.setenv(variable, _SHA)

        release = ReleaseSettings.from_env()

        assert release.git_sha == _SHA
        assert release.identified is True

    def test_an_explicit_build_arg_wins_over_the_platform(self, monkeypatch) -> None:
        """Ours is the deliberate one, and on the VPS it names the commit actually checked out."""
        monkeypatch.setenv("AURA_GIT_SHA", _SHA)
        monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "f" * 40)

        assert ReleaseSettings.from_env().git_sha == _SHA

    def test_railway_deployment_id_is_picked_up(self, monkeypatch) -> None:
        for name in ReleaseSettings.PLATFORM_DEPLOY_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("AURA_DEPLOYMENT_ID", raising=False)
        monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep_abc123")

        assert ReleaseSettings.from_env().deployment_id == "dep_abc123"

    def test_the_platform_lists_are_constants_not_dataclass_fields(self) -> None:
        """ClassVar, or they would join the dataclass -- changing its equality, its repr, and
        what `to_public_dict` is asked to serialise."""
        import dataclasses

        names = {f.name for f in dataclasses.fields(ReleaseSettings)}

        assert not {"PLATFORM_SHA_VARS", "PLATFORM_REF_VARS", "PLATFORM_DEPLOY_VARS"} & names


class TestRailwayDeployGate:
    def test_the_healthcheck_is_liveness_not_readiness(self) -> None:
        """Railway gates a deploy on this path. `/health/ready` returns 503 whenever any
        dependency is unhealthy, so pointing the gate at it means no deploy can pass while the
        inference provider is down -- and a good build cannot replace a bad one exactly when
        that matters most."""
        import json

        config = json.loads((_BACKEND / "railway.json").read_text(encoding="utf-8"))

        assert config["deploy"]["healthcheckPath"] == "/health/live"


class TestRailwayPreDeployMigration:
    """CB-43: Railway does not run `preDeployCommand` in a shell. The inline
    `echo ... && alembic upgrade head && ...` it used to hold ran only the echo, passed, and left
    production two migrations behind the code across every deploy. These pin the fix."""

    _SCRIPT = _BACKEND / "scripts" / "railway-predeploy.sh"

    def _command(self) -> str:
        import json

        config = json.loads((_BACKEND / "railway.json").read_text(encoding="utf-8"))
        return config["deploy"]["preDeployCommand"]

    def test_the_command_has_no_shell_syntax(self) -> None:
        command = self._command()
        for operator in ("&&", "||", ";", "|", "(", ")", "$", "`", ">", "<"):
            assert operator not in command, f"{operator!r} in preDeployCommand is not evaluated on Railway"

    def test_the_command_runs_the_script_from_the_image_workdir(self) -> None:
        assert self._command() == "sh /app/scripts/railway-predeploy.sh"
        assert self._SCRIPT.is_file()

    def test_the_script_has_unix_line_endings(self) -> None:
        """A CRLF checkout (this repo is edited on Windows) would make `sh` fail on `set -eu\\r`."""
        assert b"\r\n" not in self._SCRIPT.read_bytes()

    def test_the_whole_budget_stays_under_railways(self) -> None:
        """Two `alembic current` calls, the upgrade and the flush pause must all finish before
        Railway's own limit, or the step is killed with nothing logged -- the failure this
        script exists to make visible."""
        import json
        import re

        config = json.loads((_BACKEND / "railway.json").read_text(encoding="utf-8"))
        text = self._SCRIPT.read_text()

        def default(name: str) -> int:
            return int(re.search(rf"{name}:-(\d+)", text).group(1))

        budget = (2 * default("AURA_MIGRATION_CURRENT_TIMEOUT_S") + default("AURA_MIGRATION_TIMEOUT_S")
                  + default("AURA_PREDEPLOY_LOG_FLUSH_S"))
        assert budget < config["deploy"]["preDeployTimeoutSeconds"]
        assert default("AURA_PREDEPLOY_LOG_FLUSH_S") > 0, "the flush pause is what keeps the last lines"

    def _run(self, tmp_path, alembic_body: str, timeout_s: str = "30", **extra_env: str):
        import os
        import shutil
        import subprocess

        if shutil.which("sh") is None or shutil.which("timeout") is None:
            pytest.skip("needs a POSIX sh and coreutils timeout")
        fake = tmp_path / "bin"
        fake.mkdir()
        log = tmp_path / "calls"
        alembic = fake / "alembic"
        alembic.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n{alembic_body}\n')
        alembic.chmod(0o755)
        env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}", "AURA_MIGRATION_TIMEOUT_S": timeout_s,
               "AURA_PREDEPLOY_LOG_FLUSH_S": "0", "AURA_DATABASE_URL": "postgresql://u:hunter2@db/aura",
               **extra_env}
        result = subprocess.run(["sh", str(self._SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls

    def test_it_actually_runs_the_upgrade(self, tmp_path) -> None:
        result, calls = self._run(tmp_path, 'case "$1" in current) echo 0016 ;; esac')

        assert result.returncode == 0, result.stdout + result.stderr
        assert "upgrade head" in calls
        assert "=== MIGRATION OK ===" in result.stdout
        assert "revision after: 0016" in result.stdout

    def test_a_failed_upgrade_fails_the_deploy(self, tmp_path) -> None:
        result, _ = self._run(tmp_path, '[ "$1" = upgrade ] && exit 3; exit 0')

        assert result.returncode == 1
        assert "=== MIGRATION FAILED (alembic exit 3) ===" in result.stdout
        assert "MIGRATION OK" not in result.stdout

    def test_a_hung_upgrade_fails_loudly_instead_of_being_killed_silently(self, tmp_path) -> None:
        result, _ = self._run(tmp_path, '[ "$1" = upgrade ] && sleep 30; exit 0', timeout_s="1")

        assert result.returncode == 1
        assert "timed out after 1s" in result.stdout

    def test_alembic_errors_reach_the_log_not_dev_null(self, tmp_path) -> None:
        result, _ = self._run(tmp_path, '[ "$1" = upgrade ] && { echo "connection refused: db:5432" >&2; exit 2; }; echo 0016')

        assert result.returncode == 1
        assert "connection refused: db:5432" in result.stdout
        assert "MIGRATION FAILED (alembic exit 2)" in result.stdout

    def test_a_database_that_does_not_answer_ends_in_failed(self, tmp_path) -> None:
        """`alembic current` hanging (an unreachable database) must not stall the step until
        Railway kills it silently: the revision is reported unknown and the upgrade then times
        out with a FAILED marker."""
        result, _ = self._run(tmp_path, "sleep 30", timeout_s="1", AURA_MIGRATION_CURRENT_TIMEOUT_S="1")

        assert result.returncode == 1
        assert "revision before: unknown (alembic current timed out after 1s)" in result.stdout
        assert "MIGRATION FAILED (timed out after 1s" in result.stdout

    def test_markers_are_timestamped_and_the_url_is_never_printed(self, tmp_path) -> None:
        import re

        result, _ = self._run(tmp_path, 'case "$1" in current) echo 0016 ;; esac')

        assert re.search(r"^\[\d{2}:\d{2}:\d{2}Z\] === MIGRATION OK ===$", result.stdout, re.M)
        assert "database url: set" in result.stdout
        assert "hunter2" not in result.stdout

    def test_an_unset_database_url_is_named(self, tmp_path) -> None:
        result, _ = self._run(tmp_path, 'case "$1" in current) echo 0016 ;; esac', AURA_DATABASE_URL="")

        assert "database url: UNSET" in result.stdout

    def test_the_flush_pause_runs_even_when_the_migration_fails(self, tmp_path) -> None:
        import time

        started = time.monotonic()
        result, _ = self._run(tmp_path, '[ "$1" = upgrade ] && exit 3; exit 0', AURA_PREDEPLOY_LOG_FLUSH_S="1")

        assert result.returncode == 1
        assert time.monotonic() - started >= 1.0


class TestVersionEndpoint:
    async def test_reports_the_build_it_is_running(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.get("/health/version")

        assert response.status_code == 200
        body = response.json()
        assert body["api_version"] == "v3"
        assert "revision" in body and "identified" in body

    async def test_answers_without_authentication(self, container) -> None:
        """A deploy smoke test has to be able to ask before any credential exists."""
        from fastapi.testclient import TestClient

        from aura_core.api import build_app

        async def refuse_everyone(request):
            raise AssertionError("/health/version must not invoke the authenticator")

        await container.startup()
        with TestClient(build_app(container, authenticator=refuse_everyone)) as client:
            assert client.get("/health/version").status_code == 200

    async def test_readiness_carries_the_release_too(self, container) -> None:
        """So an operator reading a 503 knows which build produced it, without a second call."""
        await container.startup()
        with _client(container) as client:
            body = client.get("/health/ready").json()

        assert "release" in body
        assert body["release"]["api_version"] == "v3"


class TestTheIdentityIsActuallyInjected:
    """Settings that nothing populates would report "unknown" forever and pass every test above."""

    def test_the_dockerfile_accepts_and_exports_every_build_arg(self) -> None:
        dockerfile = (_BACKEND / "Dockerfile").read_text(encoding="utf-8")

        for arg in _BUILD_ARGS:
            assert re.search(rf"^ARG {arg}=", dockerfile, re.M), f"Dockerfile declares no {arg}"
            assert f"AURA_{arg}=${{{arg}}}" in dockerfile, f"Dockerfile never exports AURA_{arg}"

    def test_the_args_stay_below_the_expensive_layers(self) -> None:
        """Declared late on purpose: these change every commit, and an ARG invalidates every
        layer after it. Above `pip install` they would make each build recompile the world."""
        dockerfile = (_BACKEND / "Dockerfile").read_text(encoding="utf-8")

        assert dockerfile.index("ARG GIT_SHA=") > dockerfile.index("requirements.lock")

    @pytest.mark.parametrize("compose_path", _COMPOSE_FILES, ids=lambda p: p.name)
    def test_every_service_built_from_the_backend_passes_them(self, compose_path: Path) -> None:
        """All five services share one image; a worker reporting a different commit than the
        gateway is the confusion this is supposed to end."""
        yaml = pytest.importorskip("yaml")
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        building = {
            name: service["build"]
            for name, service in compose["services"].items()
            if isinstance(service.get("build"), dict)
            and "aura_backend" in str(service["build"].get("context", ""))
        }
        assert building, f"{compose_path.name} builds nothing from aura_backend"

        for name, build in building.items():
            args = build.get("args") or {}
            missing = [arg for arg in _BUILD_ARGS if arg not in args]
            assert not missing, f"{compose_path.name}:{name} does not pass {missing}"

    def test_the_deploy_script_pins_the_commit_it_just_checked_out(self) -> None:
        """Not `git rev-parse HEAD` read back later: origin/main can move mid-deploy, and the
        identity has to name the source that produced the image, not the newest commit."""
        script = (_REPO_ROOT / "deploy" / "scripts" / "vps-update.sh").read_text(encoding="utf-8")

        assert 'export AURA_GIT_SHA="$AFTER"' in script

    def test_the_deploy_script_fails_on_a_revision_mismatch(self) -> None:
        """The check whose absence let a 2026-09-13 image serve for two weeks."""
        script = (_REPO_ROOT / "deploy" / "scripts" / "vps-update.sh").read_text(encoding="utf-8")

        assert "/health/version" in script
        assert '"$LIVE_SHA" != "$AFTER"' in script
