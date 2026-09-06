"""Load-test proof that DockerSandboxRunner gives real container isolation, real resource-limit
enforcement, and a real full lifecycle -- none of which InMemorySandboxRunner (a real host
subprocess, no container) can structurally prove.

Requires a reachable Docker daemon (`docker_available()`, aura_core/adapters/docker_sandbox.py).
Pulls `python:3.12-slim` (or $AURA_SANDBOX_TEST_IMAGE) once if not already cached, and skips
cleanly if the pull cannot complete -- some environments have no network egress at all.

    pytest tests/load/test_docker_sandbox_live.py -m load -s
"""

from __future__ import annotations

import os
import socket
import time

import pytest

from aura_core.adapters.docker_sandbox import DockerSandboxRunner, docker_available

pytestmark = pytest.mark.load

_IMAGE = os.environ.get("AURA_SANDBOX_TEST_IMAGE", "python:3.12-slim")


@pytest.fixture(scope="module")
def docker_client():
    if not docker_available():
        pytest.skip("docker sandbox live test not runnable: no reachable Docker daemon")
    import docker

    client = docker.from_env()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="module", autouse=True)
def _ensure_image(docker_client) -> None:
    try:
        docker_client.images.get(_IMAGE)
    except Exception:
        try:
            docker_client.images.pull(_IMAGE)
        except Exception as exc:
            pytest.skip(f"image {_IMAGE!r} not cached and could not be pulled: {exc}")


@pytest.fixture
def runner() -> DockerSandboxRunner:
    # network="" -> DockerSandboxRunner sets network_disabled=True; the isolation tests below
    # depend on that. Deliberately not wired through SandboxSettings.network
    # ("sandbox_egress"), which only exists inside the production compose stack.
    return DockerSandboxRunner(image=_IMAGE, network="", memory_limit="256m", cpu_limit=0.5)


class TestLifecycle:
    async def test_create_write_run_read_destroy_round_trip(self, runner, docker_client) -> None:
        handle = await runner.create_task()
        try:
            await runner.write_file(handle, "hello.txt", "hi from the sandbox")
            result = await runner.run(handle, ["cat", "hello.txt"], timeout_s=15.0)
            assert result.exit_code == 0
            assert result.stdout.strip() == "hi from the sandbox"
            assert await runner.read_file(handle, "hello.txt") == "hi from the sandbox"

            # No public accessor for the container/scratch-dir -- reaching into these private
            # dicts is deliberate here: this file tests exactly this adapter, and it's a read,
            # never a mutation.
            container_id = runner._containers[handle.task_id].id
            scratch_dir = runner._scratch_dirs[handle.task_id]
        finally:
            await runner.destroy(handle)

        import docker as docker_pkg

        with pytest.raises(docker_pkg.errors.NotFound):
            docker_client.containers.get(container_id)
        assert not scratch_dir.exists()


class TestIsolation:
    async def test_cannot_see_host_filesystem_outside_the_bind_mount(
        self, runner, tmp_path
    ) -> None:
        """A file that exists on the host, outside the scratch dir bind-mounted at /workspace,
        must be invisible from inside the container -- proving real container isolation, not
        the test process's own filesystem."""
        host_only = tmp_path / "host-only-marker.txt"
        host_only.write_text("host-only marker\n")

        handle = await runner.create_task()
        try:
            result = await runner.run(handle, ["test", "-e", str(host_only)], timeout_s=10.0)
            assert result.exit_code != 0
        finally:
            await runner.destroy(handle)

    async def test_run_executes_inside_a_real_container_not_the_test_process(
        self, runner
    ) -> None:
        handle = await runner.create_task()
        try:
            result = await runner.run(handle, ["hostname"], timeout_s=10.0)
            assert result.exit_code == 0
            assert result.stdout.strip() != socket.gethostname()
        finally:
            await runner.destroy(handle)

    async def test_network_disabled_blocks_egress(self, runner) -> None:
        handle = await runner.create_task()
        try:
            result = await runner.run(
                handle,
                [
                    "python3", "-c",
                    "import socket; socket.create_connection(('8.8.8.8', 53), timeout=3)",
                ],
                timeout_s=10.0,
            )
            assert result.exit_code != 0
        finally:
            await runner.destroy(handle)


class TestResourceLimits:
    async def test_memory_limit_kills_the_container_not_the_host(self) -> None:
        limited = DockerSandboxRunner(image=_IMAGE, network="", memory_limit="64m", cpu_limit=0.5)
        handle = await limited.create_task()
        started = time.monotonic()
        try:
            # Touch every page so the allocation is actually committed, not just reserved -- a
            # bare `bytearray(300 * 1024 * 1024)` can be lazily-mapped and never hit the limit.
            result = await limited.run(
                handle,
                [
                    "python3", "-c",
                    "b = bytearray(300 * 1024 * 1024)\n"
                    "for i in range(0, len(b), 4096): b[i] = 1",
                ],
                timeout_s=30.0,
            )
            # OOM-killed by the kernel (137) or a MemoryError surfaced by the interpreter (1),
            # depending on kernel/cgroup version -- assert non-zero, not the exact code, so this
            # isn't coupled to a specific kernel's OOM-killer behaviour.
            assert result.exit_code != 0
        finally:
            await limited.destroy(handle)
        # The test process itself is still responsive right after -- that IS the "doesn't crash
        # the host" proof; a hung/crashed host would have made this assertion never execute.
        assert time.monotonic() - started < 35.0

    async def test_cpu_limit_does_not_hang_the_host(self, runner) -> None:
        handle = await runner.create_task()
        try:
            result = await runner.run(handle, ["python3", "-c", "while True: pass"], timeout_s=5.0)
            assert result.exit_code == 124
            assert "timed out" in result.stderr
        finally:
            await runner.destroy(handle)
