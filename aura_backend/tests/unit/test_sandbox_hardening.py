"""The confinement a workspace container is launched with.

A durable workspace runs shell commands a model chose, against a repository an attacker may
control, for up to three days. Every flag asserted here is one that silently stops applying if
someone edits `_container_kwargs` without meaning to -- a dropped `user` or `cap_drop` does not
fail anything, it just quietly gives the container more than it needs, which is exactly the class
of change that should not pass unnoticed.

These read the launch arguments rather than starting anything, so no daemon is needed. That the
kernel then honours them is Docker's job and is exercised in `tests/load/test_docker_sandbox_live.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aura_core.adapters.docker_sandbox import DockerSandboxRunner


@pytest.fixture
def kwargs(tmp_path: Path) -> dict:
    runner = DockerSandboxRunner(image="aura-sandbox:latest", network="sandbox_egress")
    return runner._container_kwargs(tmp_path)


def test_runs_as_an_unprivileged_user(kwargs) -> None:
    """Root in the container is root on the bind-mounted host directory."""
    assert kwargs["user"] == "10001:10001", (
        "must match the uid created in deploy/sandbox/Dockerfile and chowned by "
        "deploy/scripts/bootstrap-host.sh, or the workspace cannot write to its own checkout"
    )


def test_drops_all_capabilities(kwargs) -> None:
    assert kwargs["cap_drop"] == ["ALL"]


def test_forbids_privilege_escalation(kwargs) -> None:
    assert "no-new-privileges" in kwargs["security_opt"]


def test_root_filesystem_is_read_only(kwargs) -> None:
    """/workspace is the only durable writable path, which is also why the reaper has exactly
    one directory to remove."""
    assert kwargs["read_only"] is True


def test_writable_scratch_is_tmpfs_and_non_executable(kwargs) -> None:
    tmp = kwargs["tmpfs"]["/tmp"]
    assert "noexec" in tmp and "nosuid" in tmp and "nodev" in tmp
    assert "size=" in tmp, "an unbounded tmpfs is host memory a workspace can take at will"


def test_home_is_writable_because_git_requires_it(kwargs) -> None:
    """git refuses to run with nowhere to write its config, and the rootfs is read-only."""
    assert "/home/aura" in kwargs["tmpfs"]
    # A tmpfs is root-owned unless told otherwise, and over a mount point that exists in the image
    # (/home/aura does, mode 700) it takes that mode: HOME was then unwritable for the sandbox user.
    home = kwargs["tmpfs"]["/home/aura"]
    assert "uid=10001" in home and "gid=10001" in home, home


def test_process_and_file_limits_are_set(kwargs) -> None:
    assert kwargs["pids_limit"] == 512, "a fork bomb should hit a limit, not the host"
    names = {u.name for u in kwargs["ulimits"]}
    assert names == {"nofile", "nproc"}


def test_memory_limit_also_bounds_swap(kwargs) -> None:
    """Without memswap_limit, a container over its memory limit swaps rather than failing --
    which on this host means competing for disk with Postgres instead of hitting a ceiling."""
    assert kwargs["memswap_limit"] == kwargs["mem_limit"]


def test_the_workspace_is_the_only_bind_mount(kwargs, tmp_path) -> None:
    assert list(kwargs["volumes"]) == [str(tmp_path)]
    assert kwargs["volumes"][str(tmp_path)]["bind"] == "/workspace"


class TestEgress:
    def test_no_network_at_all_when_none_is_configured(self, tmp_path) -> None:
        runner = DockerSandboxRunner(image="aura-sandbox:latest", network="")
        kwargs = runner._container_kwargs(tmp_path)

        assert kwargs["network_disabled"] is True
        assert "network" not in kwargs

    def test_proxy_variables_are_set_when_a_proxy_is_configured(self, tmp_path) -> None:
        """The `sandbox_egress` network is internal, so these are not a suggestion -- they are
        the only route out, and a tool that ignored them would simply fail to connect."""
        runner = DockerSandboxRunner(
            image="aura-sandbox:latest",
            network="sandbox_egress",
            proxy_url="http://sandbox-proxy:8888",
        )
        env = runner._container_kwargs(tmp_path)["environment"]

        assert env["HTTPS_PROXY"] == "http://sandbox-proxy:8888"
        # Lowercase too: curl and git read those, uppercase-only would leak straight past.
        assert env["https_proxy"] == "http://sandbox-proxy:8888"

    def test_no_proxy_variables_without_a_proxy(self, tmp_path) -> None:
        runner = DockerSandboxRunner(image="aura-sandbox:latest", network="sandbox_egress")

        assert "environment" not in runner._container_kwargs(tmp_path)


def test_durable_and_ephemeral_share_one_launch_path(tmp_path) -> None:
    """The confinement above must not be something a durable workspace can be missing: it lives
    far longer than a per-turn sandbox and so deserves it more, not less. One builder is what
    keeps the two from drifting apart."""
    import inspect

    source = inspect.getsource(DockerSandboxRunner)
    assert source.count("_container_kwargs(") >= 3, (
        "open_workspace and run must both build their launch arguments through "
        "_container_kwargs, or one lifetime will quietly lose the hardening"
    )
