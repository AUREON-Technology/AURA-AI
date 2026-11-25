"""Choosing the cloud sandbox: off by default, refused when half configured, and wired only when
asked for."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aura_core.adapters.railway_sandbox import RailwaySandboxRunner
from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.errors import ConfigurationError
from aura_core.domain.identifiers import UserId

BACKEND = Path(__file__).resolve().parents[2]
_TOKEN = "t" * 48
_URL = "http://sandbox-broker.railway.internal:8080"
_NAMES = (
    "AURA_SANDBOX_ENABLED", "AURA_SANDBOX_PROVIDER", "AURA_SANDBOX_BROKER_URL",
    "AURA_SANDBOX_BROKER_TOKEN", "AURA_ENGINEER_FOR_CUSTOMERS",
)


@pytest.fixture
def configure(monkeypatch):
    for name in _NAMES:
        monkeypatch.delenv(name, raising=False)

    def _configure(**values):
        for name, value in values.items():
            monkeypatch.setenv(f"AURA_{name}", value)
        return load_settings(refresh=True, validate=False)

    yield _configure
    for name in _NAMES:
        monkeypatch.delenv(name, raising=False)
    load_settings(refresh=True, validate=False)


def _cloud(configure, **extra):
    values = dict(
        SANDBOX_ENABLED="true", SANDBOX_PROVIDER="railway", SANDBOX_BROKER_URL=_URL,
        SANDBOX_BROKER_TOKEN=_TOKEN,
    )
    values.update(extra)
    return configure(**values)


def _problems(settings) -> str:
    try:
        settings.validate()
    except ConfigurationError as exc:
        return " | ".join(exc.details["problems"])
    return ""


class TestSettings:
    def test_the_defaults_change_nothing(self, configure) -> None:
        settings = configure()

        assert settings.sandbox.enabled is False
        assert settings.sandbox.provider == "docker"
        assert settings.sandbox.broker_url == ""
        assert settings.sandbox.broker_token == ""

    def test_an_unknown_provider_is_refused(self, configure) -> None:
        with pytest.raises(ConfigurationError, match="AURA_SANDBOX_PROVIDER"):
            configure(SANDBOX_PROVIDER="kubernetes")

    def test_a_complete_cloud_configuration_has_no_sandbox_problem(self, configure) -> None:
        assert "SANDBOX" not in _problems(_cloud(configure))

    @pytest.mark.parametrize("change, named", [
        (dict(SANDBOX_BROKER_URL=""), "AURA_SANDBOX_BROKER_URL"),
        (dict(SANDBOX_BROKER_TOKEN=""), "AURA_SANDBOX_BROKER_TOKEN"),
        (dict(SANDBOX_BROKER_TOKEN="short"), "AURA_SANDBOX_BROKER_TOKEN"),
    ])
    def test_a_half_configured_cloud_sandbox_is_refused(self, configure, change, named) -> None:
        assert named in _problems(_cloud(configure, **change))

    @pytest.mark.parametrize("url", [
        "http://broker.example.com", "http://203.0.113.7:8080", "ftp://broker.railway.internal",
        "broker.railway.internal:8080", "https://user:pass@broker.example.com",
        "http://railway.internal.example.com",
    ])
    def test_the_broker_secret_is_never_sent_in_the_clear(self, configure, url) -> None:
        assert "AURA_SANDBOX_BROKER_URL" in _problems(_cloud(configure, SANDBOX_BROKER_URL=url))

    @pytest.mark.parametrize("url", [
        "https://broker.example.com", "http://localhost:8080", "http://127.0.0.1:8080",
        "http://sandbox-broker:8080", _URL,
    ])
    def test_private_and_encrypted_addresses_are_accepted(self, configure, url) -> None:
        assert "AURA_SANDBOX_BROKER_URL" not in _problems(
            _cloud(configure, SANDBOX_BROKER_URL=url))

    def test_an_unused_broker_setting_is_not_a_problem(self, configure) -> None:
        settings = configure(SANDBOX_PROVIDER="railway")

        assert "SANDBOX" not in _problems(settings)

    def test_customers_are_still_refused_a_server_sandbox(self, configure) -> None:
        settings = _cloud(configure, ENGINEER_FOR_CUSTOMERS="true")

        assert "AURA_ENGINEER_FOR_CUSTOMERS needs AURA_SANDBOX_ENABLED=false" in _problems(settings)

    def test_the_token_is_not_part_of_the_settings_object(self, configure) -> None:
        assert _TOKEN not in repr(_cloud(configure).sandbox)


class TestContainer:
    def test_nothing_is_built_by_default(self, configure) -> None:
        container = Container(configure())

        assert container.server_sandbox_runner is None
        assert container.workspace_manager is None and container.host_git is None

    def test_the_cloud_runner_is_wired_without_a_docker_daemon(
        self, configure, monkeypatch
    ) -> None:
        # `clone_repository` is offered only where a GitHub account can be resolved.
        monkeypatch.setenv("AURA_GITHUB_ENABLED", "true")
        monkeypatch.setenv("AURA_GITHUB_TOKEN", "ghp_not-a-token-used-only-by-this-test")
        container = Container(_cloud(configure))

        assert isinstance(container.server_sandbox_runner, RailwaySandboxRunner)
        assert container.docker_sandbox_runner is None
        assert container.workspace_manager is not None
        assert container.host_git is not None
        names = set(container.tools.names())
        assert {"clone_repository", "git_push", "open_workspace", "run_command"} <= names

    def test_a_broker_setting_alone_enables_nothing(self, configure) -> None:
        container = Container(configure(
            SANDBOX_PROVIDER="railway", SANDBOX_BROKER_URL=_URL, SANDBOX_BROKER_TOKEN=_TOKEN))

        assert container.server_sandbox_runner is None

    def test_a_missing_broker_leaves_no_server_sandbox(self, configure) -> None:
        container = Container(_cloud(configure, SANDBOX_BROKER_URL=""))

        assert container.server_sandbox_runner is None

    def test_host_git_stays_outside_the_sandbox_whatever_the_docker_opt_out_says(
        self, configure, monkeypatch
    ) -> None:
        monkeypatch.setenv("AURA_SANDBOX_IN_CONTAINER_GIT", "true")

        container = Container(_cloud(configure))

        assert container.host_git is not None

    async def test_the_gateways_own_memory_does_not_refuse_a_cloud_workspace(
        self, configure, monkeypatch
    ) -> None:
        """The sandbox runs elsewhere; a small gateway must still be able to open one."""
        monkeypatch.setattr(
            "aura_core.tools.workspaces.available_memory_mb", lambda *a, **k: 100.0)
        container = Container(_cloud(configure))

        opened = await container.workspace_manager.open(UserId("alice"), "acme/widgets")
        stats = await container.workspace_manager.stats(opened.record)

        assert opened.record.repo == "acme/widgets"
        assert stats["state"] == opened.record.status
        await container.shutdown()

    async def test_cloud_workspaces_are_recorded_as_such(self, configure) -> None:
        container = Container(_cloud(configure))

        opened = await container.workspace_manager.open(UserId("alice"), "acme/widgets")

        assert opened.record.provider == "railway"
        await container.shutdown()


class TestImage:
    def test_the_runtime_image_installs_git(self) -> None:
        dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
        runner_stage = re.split(r"(?im)^FROM \S+ as runner$", dockerfile)[1]
        install = re.search(
            r"apt-get install -y --no-install-recommends(.*?)&&", runner_stage, re.S)

        assert install is not None
        assert "git" in install.group(1).replace("\\", " ").split()
