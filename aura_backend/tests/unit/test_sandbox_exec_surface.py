"""Command execution must not need the Docker API's exec endpoints.

The deployed gateway does not talk to the Docker daemon directly -- it goes through
``docker-socket-proxy``, whose allowlist is *daemon-wide*. Setting ``EXEC: "1"`` so the engineer
agent could `docker exec` into its workspace would equally let a compromised gateway exec into
``postgres``, ``ollama`` and ``nginx``, which is the reason both compose files set ``EXEC: "0"``.

That makes "the runner never execs" a security property of the deployment, not an implementation
detail -- and one nothing else can catch: the in-memory runner used by every other test runs
subprocesses and never touches the Docker API, and no CI job stands up the socket proxy. So it is
asserted here directly, from both ends, because the pair only works if both halves hold.

`test_sandbox_hardening.py` covers what a container is *launched with*; this file covers how a
command is *dispatched*.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from aura_core.adapters.docker_sandbox import DockerSandboxRunner, _is_read_timeout

_REPO_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE_FILES = (
    _REPO_ROOT / "deploy" / "docker-compose.prod.yml",
    _REPO_ROOT / "deploy" / "docker-compose.dev.yml",
)


class _FakeContainer:
    """Stands in for a docker-py ``Container``. Records what was asked of it.

    A stub of the vendor SDK, not of anything in this codebase: there is no daemon in the default
    test environment, and the behaviour under test is precisely which SDK calls are made.
    """

    def __init__(self, *, status_code: int = 0, stdout: bytes = b"", stderr: bytes = b"",
                 wait_error: Exception | None = None) -> None:
        self._status_code = status_code
        self._stdout = stdout
        self._stderr = stderr
        self._wait_error = wait_error
        self.removed = False
        self.killed = False
        self.wait_timeout: float | None = None

    def wait(self, timeout: float | None = None) -> dict:
        self.wait_timeout = timeout
        if self._wait_error is not None:
            raise self._wait_error
        return {"StatusCode": self._status_code}

    def logs(self, *, stdout: bool = True, stderr: bool = True, tail="all") -> bytes:
        if stdout and not stderr:
            return self._stdout
        if stderr and not stdout:
            return self._stderr
        return self._stdout + self._stderr

    def kill(self) -> None:
        self.killed = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeClient:
    def __init__(self, container: _FakeContainer) -> None:
        self._container = container
        self.run_kwargs: dict | None = None
        self.containers = self

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        return self._container

    def list(self, **kwargs):  # pragma: no cover - only the durable path calls this
        return []


@pytest.fixture
def runner(tmp_path) -> DockerSandboxRunner:
    return DockerSandboxRunner(
        image="aura-sandbox:latest", network="sandbox_egress", host_root=str(tmp_path)
    )


async def _handle(runner: DockerSandboxRunner):
    return await runner.create_task()


class TestNoExecAnywhere:
    def test_the_runner_never_calls_exec_run(self) -> None:
        """The single assertion this whole file exists for. `exec_run` is docker-py's wrapper
        around POST /containers/{id}/exec -- the endpoint the socket proxy refuses."""
        source = inspect.getsource(DockerSandboxRunner)

        assert "exec_run" not in source, (
            "DockerSandboxRunner called exec_run, which docker-socket-proxy refuses with "
            'EXEC: "0". Run the command in its own container instead -- see run()\'s docstring.'
        )

    def test_no_exec_api_call_by_any_other_spelling(self) -> None:
        """`exec_run` is the convenient wrapper, not the only door: `exec_create`/`exec_start`
        on the low-level client reach the same endpoint."""
        source = inspect.getsource(DockerSandboxRunner)

        for forbidden in ("exec_create", "exec_start", "exec_inspect", "exec_resize"):
            assert forbidden not in source, f"{forbidden} also needs EXEC on the socket proxy"

    @pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
    def test_the_socket_proxy_keeps_exec_off(self, compose: Path) -> None:
        """The other half of the pair. The adapter avoiding exec only buys anything while the
        proxy still refuses it -- and dev and prod drifting apart is how that silently stops
        being true in one of them."""
        text = compose.read_text()

        assert re.search(r'^\s*EXEC:\s*"0"\s*$', text, re.MULTILINE), (
            f"{compose.name} no longer sets EXEC: \"0\" on docker-socket-proxy. That flag is "
            "daemon-wide: enabling it lets a compromised gateway exec into postgres and ollama, "
            "not just into a sandbox."
        )


class TestOneShotExecution:
    async def test_a_command_runs_in_its_own_container_over_the_workspace(self, runner) -> None:
        container = _FakeContainer(status_code=0, stdout=b"hello\n")
        runner._client = _FakeClient(container)
        handle = await _handle(runner)

        result = await runner.run(handle, ["echo", "hello"], timeout_s=30)

        assert result.stdout == "hello\n"
        assert result.exit_code == 0
        kwargs = runner._client.run_kwargs
        assert kwargs["command"] == ["echo", "hello"]
        # The bind mount is what makes a fresh container equivalent to exec'ing into a live one.
        assert list(kwargs["volumes"].values()) == [{"bind": "/workspace", "mode": "rw"}]

    async def test_the_container_is_removed_after_the_command(self, runner) -> None:
        """One container per command only stays cheap if each one is actually reclaimed."""
        container = _FakeContainer()
        runner._client = _FakeClient(container)
        handle = await _handle(runner)

        await runner.run(handle, ["true"], timeout_s=30)

        assert container.removed

    async def test_a_failing_command_reports_its_exit_code_and_stderr(self, runner) -> None:
        container = _FakeContainer(status_code=2, stderr=b"boom\n")
        runner._client = _FakeClient(container)
        handle = await _handle(runner)

        result = await runner.run(handle, ["false"], timeout_s=30)

        assert result.exit_code == 2
        assert result.stderr == "boom\n"

    async def test_a_one_shot_command_keeps_the_full_confinement(self, runner) -> None:
        """A command container is where model-chosen code actually executes, so it must not be
        the lifetime that quietly ships with weaker isolation than a workspace."""
        runner._client = _FakeClient(_FakeContainer())
        handle = await _handle(runner)

        await runner.run(handle, ["true"], timeout_s=30)

        kwargs = runner._client.run_kwargs
        assert kwargs["user"] == "10001:10001"
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["security_opt"] == ["no-new-privileges"]
        assert kwargs["read_only"] is True
        assert kwargs["network"] == "sandbox_egress"

    async def test_a_one_shot_container_is_not_labelled_as_a_workspace(self, runner) -> None:
        """It must not answer to `aura.workspace`: reconciliation and the reaper find durable
        workspaces by that label, and a command container appearing in that set would look like
        an orphan to reap or a workspace to adopt."""
        runner._client = _FakeClient(_FakeContainer())
        handle = await _handle(runner)

        await runner.run(handle, ["true"], timeout_s=30)

        labels = runner._client.run_kwargs["labels"]
        assert "aura.workspace" not in labels
        assert labels["aura.sandbox.oneshot"] == handle.task_id


class TestTimeouts:
    async def test_an_overrunning_command_is_killed_and_reported(self, runner) -> None:
        """124 is the shell's timeout convention and what the previous exec path returned; the
        kill is what stops a runaway build holding its memory until the workspace is reaped."""
        class ReadTimeout(Exception):
            pass

        container = _FakeContainer(wait_error=ReadTimeout("read timed out"))
        runner._client = _FakeClient(container)
        handle = await _handle(runner)

        result = await runner.run(handle, ["sleep", "999"], timeout_s=5)

        assert result.exit_code == 124
        assert "timed out" in result.stderr
        assert container.killed
        assert container.removed

    async def test_the_deadline_reaches_the_daemon(self, runner) -> None:
        """Without passing the timeout down, `wait` blocks forever and the outer guard would
        leave the container running behind an abandoned thread."""
        container = _FakeContainer()
        runner._client = _FakeClient(container)
        handle = await _handle(runner)

        await runner.run(handle, ["true"], timeout_s=7)

        assert container.wait_timeout == 7

    async def test_a_real_error_is_not_disguised_as_a_timeout(self, runner) -> None:
        """`_is_read_timeout` matches on class name, so it has to not swallow everything else --
        an image that will not start is a bug to surface, not a slow command."""
        container = _FakeContainer(wait_error=ValueError("no such image"))
        runner._client = _FakeClient(container)
        handle = await _handle(runner)

        with pytest.raises(ValueError, match="no such image"):
            await runner.run(handle, ["true"], timeout_s=5)

        assert container.removed  # still reclaimed on the error path


class TestReadTimeoutDetection:
    def test_it_recognises_a_timeout_by_name(self) -> None:
        class ReadTimeout(Exception):
            pass

        assert _is_read_timeout(ReadTimeout())

    def test_it_looks_through_the_cause_chain(self) -> None:
        """docker-py wraps the underlying requests error inconsistently across versions."""
        class ConnectionError_(Exception):
            pass

        class ReadTimeout(Exception):
            pass

        outer = ConnectionError_()
        outer.__cause__ = ReadTimeout()

        assert _is_read_timeout(outer)

    def test_it_rejects_unrelated_errors(self) -> None:
        assert not _is_read_timeout(ValueError("nope"))

    def test_a_self_referential_cause_chain_terminates(self) -> None:
        """Defensive: a cycle here would hang the turn rather than fail it."""
        first = ValueError("a")
        second = ValueError("b")
        first.__cause__ = second
        second.__cause__ = first

        assert not _is_read_timeout(first)


class TestTheGatewayOnlyReachesTheSandboxBroker:
    """F19. docker-socket-proxy filters endpoints, not request bodies: with CONTAINERS and POST
    on, `POST /containers/create` is allowed whatever it carries, so a gateway that could reach
    the proxy could create a privileged container with `/` bind-mounted. The sandbox broker
    (deploy/sandbox-broker/broker.py, tested in test_sandbox_broker_policy.py) is the server-side
    check, and it only means anything while the gateway cannot go around it."""

    _GATEWAY = {"docker-compose.prod.yml": "gateway", "docker-compose.dev.yml": "dev-gateway"}

    @staticmethod
    def _doc(compose: Path) -> dict:
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load(compose.read_text(encoding="utf-8"))

    @staticmethod
    def _networks(service: dict) -> set[str]:
        networks = service.get("networks") or []
        return set(networks if isinstance(networks, list) else networks.keys())

    @pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
    def test_the_gateway_talks_to_the_broker(self, compose: Path) -> None:
        gateway = self._doc(compose)["services"][self._GATEWAY[compose.name]]
        assert gateway["environment"]["DOCKER_HOST"] == "tcp://sandbox-broker:2375"

    @pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
    def test_only_the_broker_shares_a_network_with_the_socket_proxy(self, compose: Path) -> None:
        services = self._doc(compose)["services"]
        proxy = self._networks(services["docker-socket-proxy"])
        reach = {
            name for name, svc in services.items()
            if name != "docker-socket-proxy" and self._networks(svc) & proxy
        }
        assert reach == {"sandbox-broker"}, f"{compose.name}: {reach} can reach the socket proxy"

    @pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
    def test_the_broker_bridges_the_two(self, compose: Path) -> None:
        services = self._doc(compose)["services"]
        broker = services["sandbox-broker"]
        gateway = services[self._GATEWAY[compose.name]]
        assert self._networks(broker) & self._networks(gateway)
        assert broker["profiles"] == ["sandbox"]
        # Built from the repository, never pulled -- same reason as the sandbox image.
        assert broker["build"]["context"] == "./sandbox-broker"
        assert broker["read_only"] is True
        assert broker["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in broker["security_opt"]

    @pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
    def test_the_broker_enforces_the_gateway_s_own_sandbox_settings(self, compose: Path) -> None:
        """Image, network and workspace root must be the values the adapter sends, or the broker
        refuses every legitimate command; and the root must be mounted read-only at the same
        path, or its symlink check resolves nothing."""
        services = self._doc(compose)["services"]
        broker, gateway = services["sandbox-broker"], services[self._GATEWAY[compose.name]]
        for key in ("AURA_SANDBOX_IMAGE", "AURA_SANDBOX_NETWORK", "AURA_SANDBOX_HOST_ROOT"):
            assert broker["environment"][key] == gateway["environment"][key], key
        root = gateway["environment"]["AURA_SANDBOX_HOST_ROOT"]
        assert f"{root}:{root}:ro" in broker["volumes"]

    @pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
    def test_both_control_networks_are_internal(self, compose: Path) -> None:
        networks = self._doc(compose)["networks"]
        for name in ("sandbox_ctl", "sandbox_docker"):
            assert networks[name].get("internal") is True, f"{compose.name}: {name} is not internal"
