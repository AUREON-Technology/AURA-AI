"""`DockerSandboxRunner` workspace paths must be resolvable by the Docker daemon, not just by us.

These need no daemon: ``create_task`` only creates a directory, and where it creates it is the
whole point. When this adapter runs inside a container (the deployed case -- the gateway reaching
the host daemon through ``docker-socket-proxy``), a bind-mount source is resolved by the daemon
*on the host*. A scratch directory that exists only in the gateway's own filesystem therefore
names a different, empty directory once mounted, and the split is silent: ``write_file`` writes
on one side, ``run_command`` reads the other, and nothing raises.

``tests/load/test_docker_sandbox_live.py`` proves the round trip against a real daemon. What is
checked here is the property that makes it possible -- that a configured ``host_root`` is
actually where workspaces land.
"""

from __future__ import annotations

from pathlib import Path

from aura_core.adapters.docker_sandbox import DockerSandboxRunner


def _runner(**kwargs) -> DockerSandboxRunner:
    return DockerSandboxRunner(image="python:3.12-slim", network="", **kwargs)


async def test_scratch_is_created_under_host_root(tmp_path: Path) -> None:
    root = tmp_path / "sandboxes"
    root.mkdir()
    runner = _runner(host_root=str(root))

    handle = await runner.create_task()

    scratch = runner._scratch(handle)
    assert scratch.is_dir()
    assert scratch.parent == root, (
        "a workspace outside host_root is a path the Docker daemon resolves to a different, "
        "empty directory on the host -- write_file and run_command would stop agreeing"
    )


async def test_host_root_is_created_when_absent(tmp_path: Path) -> None:
    """The compose bind mount creates the host directory, but a fresh host (or a local run
    pointed at a new path) has not; failing to provision here would strand the deployment on an
    error whose cause is a missing directory."""
    root = tmp_path / "not-yet-there" / "sandboxes"
    runner = _runner(host_root=str(root))

    handle = await runner.create_task()

    assert runner._scratch(handle).is_dir()
    assert root.is_dir()


async def test_each_task_gets_its_own_directory(tmp_path: Path) -> None:
    runner = _runner(host_root=str(tmp_path))

    first = await runner.create_task()
    second = await runner.create_task()

    assert runner._scratch(first) != runner._scratch(second)


async def test_falls_back_to_a_temp_directory_without_host_root() -> None:
    """Unset is the local/test case: this process and the daemon share a filesystem, so any
    path already means the same thing to both, and requiring the setting there would be noise."""
    runner = _runner()

    handle = await runner.create_task()

    try:
        assert runner._scratch(handle).is_dir()
    finally:
        await runner.destroy(handle)


async def test_paths_are_still_contained_under_host_root(tmp_path: Path) -> None:
    """host_root changes where the workspace lives, never the containment rule that keeps a
    model-supplied path inside it."""
    runner = _runner(host_root=str(tmp_path))
    handle = await runner.create_task()

    await runner.write_file(handle, "nested/ok.txt", "fine")
    assert (runner._scratch(handle) / "nested" / "ok.txt").read_text() == "fine"

    for escape in ("../escaped.txt", "/etc/passwd", "nested/../../escaped.txt"):
        try:
            await runner.write_file(handle, escape, "nope")
        except Exception:
            continue
        resolved = runner._resolve_within(handle, escape)
        assert runner._scratch(handle).resolve() in resolved.parents
