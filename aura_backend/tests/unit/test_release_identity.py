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
        for key in ("AURA_GIT_SHA", "AURA_GIT_REF", "AURA_BUILD_TIME", "AURA_DEPLOYMENT_ID"):
            monkeypatch.delenv(key, raising=False)

        release = ReleaseSettings.from_env()

        assert release.identified is False
        assert release.revision == "unknown"
        assert release.git_sha == ""

    def test_whitespace_only_injection_does_not_count_as_identified(self, monkeypatch) -> None:
        """`--build-arg GIT_SHA=` and a stray newline both arrive as whitespace, not absence."""
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


class TestProductionRefusesAnAnonymousBuild:
    def test_production_boot_fails_without_a_git_sha(self) -> None:
        problems = _problems(AuraSettings(environment=Environment.PRODUCTION))

        assert _mentions_git_sha(problems)

    def test_staging_is_held_to_the_same_rule(self) -> None:
        """Staging is where a bad deploy should be caught, so it cannot be the lenient one."""
        problems = _problems(AuraSettings(environment=Environment.STAGING))

        assert _mentions_git_sha(problems)

    def test_an_identified_production_build_clears_this_check(self) -> None:
        problems = _problems(
            AuraSettings(
                environment=Environment.PRODUCTION, release=ReleaseSettings(git_sha=_SHA)
            )
        )

        assert not _mentions_git_sha(problems)

    @pytest.mark.parametrize("environment", [Environment.LOCAL, Environment.DEVELOPMENT])
    def test_non_production_needs_no_injected_identity(self, environment: Environment) -> None:
        """A checkout run straight from `pytest`, or a `docker build` with no args, must boot."""
        problems = _problems(AuraSettings(environment=environment))

        assert not _mentions_git_sha(problems)


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
