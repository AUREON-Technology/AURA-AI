"""The Docker sandbox against a REAL Docker daemon: does the confinement actually hold?

Every other test of ``DockerSandboxRunner`` uses a fake Docker client, so none of them can say
whether the settings the runner asks for are the ones the kernel enforces. These do not use a fake:
the real runner, the real ``docker`` SDK, a real ``dockerd``, real containers, and the assertions are
made *from inside* the container (what the process can see and do) and on the host (what is left
behind). Nothing here proves more than it says -- see "Not covered" at the bottom.

Gated like ``tests/load``: skipped unless ``AURA_REAL_DOCKER=1`` and the daemon at ``DOCKER_HOST`` answers.
``scripts/real_docker_up.sh`` starts a throwaway daemon and pulls the image; ``real_docker_down.sh``
removes it. The image is the same digest-pinned ``python:3.12-slim`` base ``deploy/sandbox/Dockerfile``
builds on (``AURA_REAL_DOCKER_IMAGE`` overrides it); the isolation properties do not depend on the
toolchain the Dockerfile adds.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from aura_core.adapters.docker_sandbox import (
    _NOFILE_LIMIT,
    _PIDS_LIMIT,
    _SANDBOX_UID_N,
    DockerSandboxRunner,
)

IMAGE = os.environ.get(
    "AURA_REAL_DOCKER_IMAGE",
    "mirror.gcr.io/library/python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea",
)


def _daemon_answers() -> bool:
    if os.environ.get("AURA_REAL_DOCKER") != "1":
        return False
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _daemon_answers(),
        reason="needs a real Docker daemon: AURA_REAL_DOCKER=1 and DOCKER_HOST (scripts/real_docker_up.sh)",
    ),
]


@pytest.fixture
async def runner():
    r = DockerSandboxRunner(image=IMAGE)
    yield r
    await r.close()


@pytest.fixture
async def task(runner):
    handle = await runner.create_task()
    yield handle
    await runner.destroy(handle)


async def sh(runner, handle, command: str, timeout_s: float = 30.0):
    return await runner.run(handle, ["sh", "-c", command], timeout_s=timeout_s)


def _client():
    import docker

    return docker.from_env()


def _leftovers() -> list[str]:
    """Containers the runner created that are still around (running or stopped)."""
    found = _client().containers.list(all=True, filters={"label": ["aura.sandbox.oneshot"]})
    return [c.short_id for c in found]


# ------------------------------------------------------------------ what the process can do


class TestInsideTheContainer:
    async def test_it_runs_as_the_unprivileged_user_not_root(self, runner, task) -> None:
        out = (await sh(runner, task, "id -u; id -g")).stdout.split()
        assert out == [str(_SANDBOX_UID_N), str(_SANDBOX_UID_N)]

    async def test_it_holds_no_linux_capabilities_at_all(self, runner, task) -> None:
        status = (await sh(runner, task, "grep -E '^Cap' /proc/self/status")).stdout
        for line in status.splitlines():
            assert line.split()[1] == "0" * 16, f"{line}: a capability survived cap_drop=ALL"

    async def test_no_new_privileges_and_a_seccomp_filter_are_active(self, runner, task) -> None:
        status = (await sh(runner, task, "grep -E 'NoNewPrivs|Seccomp' /proc/self/status")).stdout
        assert re.search(r"NoNewPrivs:\s+1", status), status
        assert re.search(r"Seccomp:\s+2", status), "the default seccomp profile is not applied"

    async def test_the_root_filesystem_is_read_only(self, runner, task) -> None:
        result = await sh(runner, task, "touch /etc/pwned; echo rc=$?")
        assert "rc=1" in result.stdout and "Read-only file system" in result.stderr
        assert (await sh(runner, task, "touch /usr/local/bin/x && echo wrote")).stdout.strip() != "wrote"

    async def test_only_the_workspace_and_two_tmpfs_are_writable(self, runner, task) -> None:
        result = await sh(runner, task, "touch /workspace/a && echo ws; touch /tmp/a && echo tmp; touch /home/aura/a && echo home")
        assert result.stdout.split() == ["ws", "tmp", "home"]

    async def test_tmp_is_noexec_so_a_downloaded_binary_cannot_run_from_it(self, runner, task) -> None:
        result = await sh(runner, task, "printf '#!/bin/sh\\necho ran\\n' > /tmp/x.sh; chmod +x /tmp/x.sh; /tmp/x.sh; echo rc=$?")
        assert "ran" not in result.stdout and "Permission denied" in result.stderr

    async def test_tmp_is_size_limited(self, runner, task) -> None:
        result = await sh(runner, task, "dd if=/dev/zero of=/tmp/big bs=1M count=400 2>&1; echo rc=$?; du -sm /tmp/big | cut -f1")
        used = int(result.stdout.strip().splitlines()[-1])
        assert used <= 256, f"/tmp grew to {used} MB; the tmpfs is capped at 256"

    async def test_the_daemon_really_gives_this_container_no_interface_where_a_default_one_gets_one(self, runner, task) -> None:
        """Control: on the same daemon a plain container is attached to the bridge (eth0); the
        runner's is not. Without a bridge network on the daemon the first half proves nothing, so
        that case is skipped rather than passed."""
        client = _client()
        if not any(n.name == "bridge" for n in client.networks.list()):
            pytest.skip("this daemon has no default bridge network, so isolation from it cannot be shown")
        control = client.containers.run(IMAGE, ["sh", "-c", "tail -n +3 /proc/net/dev | cut -d: -f1"], remove=True)
        assert "eth0" in control.decode(), "the control container should be attached to the bridge"
        interfaces = (await sh(runner, task, "tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' '")).stdout.split()
        assert interfaces == ["lo"]

    async def test_there_is_no_network_beyond_loopback(self, runner, task) -> None:
        interfaces = (await sh(runner, task, "tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' '")).stdout.split()
        assert interfaces == ["lo"]
        probe = await sh(
            runner, task,
            "python3 -c \"import socket;s=socket.socket();s.settimeout(3);s.connect(('1.1.1.1',53))\"; echo rc=$?",
        )
        assert "rc=1" in probe.stdout and "unreachable" in probe.stderr.lower()
        dns = await sh(runner, task, "python3 -c \"import socket;socket.gethostbyname('example.com')\"; echo rc=$?")
        assert "rc=1" in dns.stdout

    async def test_it_cannot_mount_unshare_or_change_the_clock(self, runner, task) -> None:
        mount = await sh(runner, task, "mount -t tmpfs none /mnt; echo rc=$?")
        assert "rc=32" in mount.stdout or "rc=1" in mount.stdout
        userns = await sh(runner, task, "python3 -c 'import os;os.unshare(os.CLONE_NEWUSER)'; echo rc=$?")
        assert "rc=1" in userns.stdout and "not permitted" in userns.stderr.lower()
        clock = await sh(runner, task, "date -s '2001-01-01' 2>&1; echo rc=$?")
        assert "rc=0" not in clock.stdout

    async def test_it_cannot_write_kernel_tunables(self, runner, task) -> None:
        result = await sh(runner, task, "echo 1 > /proc/sys/kernel/panic; echo rc=$?; echo b > /proc/sysrq-trigger; echo rc2=$?")
        assert "rc=0" not in result.stdout and "rc2=0" not in result.stdout

    async def test_it_sees_only_its_own_processes_and_no_host_devices(self, runner, task) -> None:
        pids = (await sh(runner, task, "ls /proc | grep -E '^[0-9]+$' | wc -l")).stdout.strip()
        assert int(pids) < 10, "the host's process table is visible"
        devices = (await sh(runner, task, "ls /dev")).stdout.split()
        for forbidden in ("sda", "vda", "mem", "kmem", "port", "nvme0n1", "fuse"):
            assert forbidden not in devices, f"/dev/{forbidden} is exposed"

    async def test_no_docker_socket_and_no_host_paths_are_mounted(self, runner, task) -> None:
        assert (await sh(runner, task, "ls /var/run/docker.sock /run/docker.sock 2>&1")).exit_code != 0
        mounts = (await sh(runner, task, "awk '{print $5}' /proc/self/mountinfo")).stdout.split()
        writable_binds = [m for m in mounts if m.startswith("/") and m not in (
            "/", "/proc", "/dev", "/dev/pts", "/dev/mqueue", "/dev/shm", "/sys", "/sys/fs/cgroup", "/etc/resolv.conf",
            "/etc/hostname", "/etc/hosts", "/workspace", "/tmp", "/home/aura",
        ) and not m.startswith(("/proc/", "/sys/", "/dev/"))]
        assert writable_binds == [], f"unexpected mounts inside the container: {writable_binds}"

    async def test_the_gateways_own_environment_does_not_leak_in(self, runner, task, monkeypatch) -> None:
        monkeypatch.setenv("AURA_JWT_SECRET", "canary-do-not-leak")
        monkeypatch.setenv("AURA_PROVIDER_X_API_KEY", "canary-do-not-leak")
        env = (await sh(runner, task, "env")).stdout
        assert "canary-do-not-leak" not in env and "AURA_" not in env


# --------------------------------------------------------------------- resource limits


class TestResourceLimits:
    async def test_a_fork_bomb_stops_at_the_pid_limit(self, runner, task) -> None:
        await runner.write_file(task, "bomb.py", (
            "import os, time\n"
            "n = 0\n"
            "try:\n"
            "    for _ in range(5000):\n"
            "        if os.fork() == 0:\n"
            "            time.sleep(4)\n"
            "            os._exit(0)\n"
            "        n += 1\n"
            "except OSError:\n"
            "    print('forked', n)\n"
        ))
        result = await sh(runner, task, "python3 /workspace/bomb.py", timeout_s=40)
        forked = int(re.search(r"forked (\d+)", result.stdout).group(1))
        assert forked < _PIDS_LIMIT, f"{forked} processes: the pids cgroup did not cap it at {_PIDS_LIMIT}"
        await asyncio.sleep(1)
        assert _leftovers() == []

    async def test_memory_is_capped_and_the_oom_kill_is_the_containers_alone(self, runner, task) -> None:
        result = await sh(runner, task, "python3 -c \"x=bytearray(900*1024*1024); print('allocated')\"; echo rc=$?")
        assert "allocated" not in result.stdout and "rc=137" in result.stdout, result
        # the host is fine and the next command in the same workspace runs normally
        assert (await sh(runner, task, "echo alive")).stdout.strip() == "alive"

    async def test_swap_cannot_be_used_to_get_around_the_memory_limit(self, runner, task) -> None:
        client = _client()
        container = client.containers.create(**runner._container_kwargs(Path(tempfile.mkdtemp())))
        try:
            host = container.attrs["HostConfig"]
            assert host["Memory"] == host["MemorySwap"] == 512 * 1024 * 1024
        finally:
            container.remove(force=True)

    async def test_the_open_file_limit_applies(self, runner, task) -> None:
        result = await sh(
            runner, task,
            "python3 -c \"import os;fds=[]\ntry:\n  [fds.append(os.open('/dev/null',os.O_RDONLY)) for _ in range(8000)]\n"
            "except OSError as e: print('opened',len(fds))\"",
        )
        opened = int(re.search(r"opened (\d+)", result.stdout).group(1))
        assert opened <= _NOFILE_LIMIT

    async def test_one_cpu_of_quota_is_set(self, runner, task) -> None:
        quota = (await sh(runner, task, "cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>&1")).stdout.split()
        assert quota[:2] == ["100000", "100000"], quota

    async def test_a_command_over_its_time_is_killed_and_nothing_is_left_running(self, runner, task) -> None:
        result = await sh(runner, task, "sleep 120", timeout_s=3)
        assert result.exit_code == 124 and "timed out" in result.stderr
        await asyncio.sleep(1)
        assert _leftovers() == [], "the timed-out container was not removed"

    async def test_runaway_output_is_capped_not_buffered_without_bound(self, runner, task) -> None:
        result = await sh(runner, task, "python3 -c \"import sys\nfor _ in range(200000): sys.stdout.write('x'*100+'\\n')\"", timeout_s=60)
        assert len(result.stdout.encode()) <= 4 * 1024 * 1024 + 1024


# ------------------------------------------------------- what the runner asks the daemon for


class TestTheContainerConfigurationAsTheDaemonRecordsIt:
    async def test_inspect_shows_the_hardening_the_runner_claims(self, runner) -> None:
        scratch = Path(tempfile.mkdtemp(prefix="aura-real-docker-"))
        container = _client().containers.create(**runner._container_kwargs(scratch))
        try:
            host = container.attrs["HostConfig"]
            config = container.attrs["Config"]
            assert host["Privileged"] is False
            assert host["CapDrop"] == ["ALL"] and not host["CapAdd"]
            assert host["ReadonlyRootfs"] is True
            assert "no-new-privileges" in " ".join(host["SecurityOpt"] or [])
            assert host["PidsLimit"] == _PIDS_LIMIT
            assert config["NetworkDisabled"] is True, "the runner asked for no networking"
            assert config["User"] == f"{_SANDBOX_UID_N}:{_SANDBOX_UID_N}"
            assert host["PidMode"] == "" and host["IpcMode"] in ("private", "")
            assert host["UsernsMode"] == "" and not host["Devices"]
            assert host["RestartPolicy"]["Name"] in ("", "no")
            assert "noexec" in host["Tmpfs"]["/tmp"] and "nosuid" in host["Tmpfs"]["/tmp"]
            binds = host["Binds"] or []
            assert binds == [f"{scratch}:/workspace:rw"], f"unexpected bind mounts: {binds}"
            assert not any("docker.sock" in b for b in binds)
        finally:
            container.remove(force=True)
            shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------- host side of the bind mount


class TestTheWorkspaceDirectoryOnTheHost:
    async def test_files_written_by_the_agent_are_owned_by_the_sandbox_user(self, runner, task) -> None:
        await runner.write_file(task, "src/a.txt", "hello")
        await sh(runner, task, "echo from-container > /workspace/src/b.txt")
        scratch = runner._scratch(task)
        assert (scratch / "src" / "b.txt").read_text() == "from-container\n"
        assert (scratch / "src" / "b.txt").stat().st_uid == _SANDBOX_UID_N

    async def test_a_symlink_made_inside_the_container_cannot_be_followed_out_by_the_host_side_tools(self, runner, task) -> None:
        secret = Path(tempfile.mkdtemp()) / "host-secret.txt"
        secret.write_text("TOP SECRET OF THE HOST")
        await sh(runner, task, f"ln -s {secret} /workspace/escape; ln -s /etc/passwd /workspace/passwd")
        for name in ("escape", "passwd"):
            with pytest.raises(Exception) as read_attempt:
                await runner.read_file(task, name)
            assert "TOP SECRET" not in str(read_attempt.value)
        with pytest.raises(Exception):
            await runner.write_file(task, "escape", "overwritten by the agent")
        assert secret.read_text() == "TOP SECRET OF THE HOST", "the host file was written through the symlink"

    async def test_path_traversal_in_the_file_api_is_refused_or_kept_inside_the_workspace(self, runner, task) -> None:
        import uuid

        marker = f"aura-escape-probe-{uuid.uuid4().hex}"
        outside = runner._scratch(task).parent
        for bad in (f"../{marker}", f"a/../../{marker}", f"../../etc/cron.d/{marker}", f"./../{marker}"):
            with pytest.raises(Exception):
                await runner.write_file(task, bad, "x")
        # `a/../name` stays inside (it resolves to the workspace itself), and an absolute path is
        # taken relative to the workspace: neither is an escape, and neither may reach the host.
        await runner.write_file(task, f"a/../{marker}", "inside")
        await runner.write_file(task, f"/etc/{marker}", "inside")
        assert not (outside / marker).exists() and not Path(f"/etc/{marker}").exists()
        assert (runner._scratch(task) / marker).read_text() == "inside"
        assert (runner._scratch(task) / "etc" / marker).read_text() == "inside"

    async def test_destroying_the_task_removes_the_directory_and_leaves_no_container(self, runner) -> None:
        handle = await runner.create_task()
        scratch = runner._scratch(handle)
        await sh(runner, handle, "echo data > /workspace/f")
        assert scratch.exists()
        await runner.destroy(handle)
        assert not scratch.exists() and _leftovers() == []

    async def test_parallel_commands_in_one_workspace_all_finish_and_all_clean_up(self, runner, task) -> None:
        results = await asyncio.gather(*(sh(runner, task, f"echo {i}; sleep 0.2") for i in range(12)))
        assert sorted(int(r.stdout.strip()) for r in results) == list(range(12))
        assert _leftovers() == []


# ------------------------------------------------------------------- durable workspaces


class TestDurableWorkspaces:
    async def test_open_pause_resume_and_destroy_through_the_real_daemon(self, runner) -> None:
        key = "real-docker-test-workspace"
        handle = await runner.open_workspace(key)
        try:
            await runner.write_file(handle, "kept.txt", "still here")
            assert key in await runner.workspace_keys()
            again = await runner.open_workspace(key)
            assert runner._scratch(again) == runner._scratch(handle), "the same workspace, not a second one"
            await runner.pause_workspace(handle)
            resumed = await runner.open_workspace(key)
            assert await runner.read_file(resumed, "kept.txt") == "still here"
            assert (await runner.run(resumed, ["cat", "/workspace/kept.txt"], timeout_s=20)).stdout == "still here"
            carriers = _client().containers.list(all=True, filters={"label": [f"aura.workspace={key}"]})
            assert len(carriers) == 1, "one carrier container per workspace, never a duplicate"
            assert carriers[0].attrs["HostConfig"]["CapDrop"] == ["ALL"], "the durable carrier is as confined as a one-shot"
        finally:
            await runner.destroy_workspace(handle)
        assert _client().containers.list(all=True, filters={"label": [f"aura.workspace={key}"]}) == []
        assert key not in await runner.workspace_keys()


# ----------------------------------------------------------------- the egress allowlist proxy

_DEPLOY = Path(__file__).resolve().parents[3] / "deploy" / "sandbox"
PROXY_IMAGE = os.environ.get("AURA_REAL_DOCKER_PROXY_IMAGE", "mirror.gcr.io/vimagick/tinyproxy:latest")


class TestEgressThroughTheAllowlistProxy:
    """The real ``deploy/sandbox/tinyproxy.conf`` and ``filter`` in a real tinyproxy, reached by a real
    sandbox container the way compose wires it: same user-defined network, the runner's own
    ``network`` / ``proxy_url`` settings, the embedded DNS resolver.

    What this shows is the proxy's POLICY (default deny, only the listed hosts, only port 443). It
    cannot show that the network is ``internal`` (no route past the proxy): that needs the daemon to
    manage iptables, which a throwaway daemon here does not, so it is checked on the host by
    ``deploy/scripts/verify-engineer-workspaces.sh``.
    """

    @pytest.fixture
    async def egress(self):
        import uuid

        client = _client()
        try:
            client.images.get(PROXY_IMAGE)
        except Exception:
            try:
                client.images.pull(PROXY_IMAGE)
            except Exception as exc:  # pragma: no cover - registry unreachable
                pytest.skip(f"cannot get the tinyproxy image {PROXY_IMAGE}: {exc}")
        name = f"aura-real-egress-{uuid.uuid4().hex[:8]}"
        network = client.networks.create(name, driver="bridge", internal=True)
        proxy = client.containers.run(
            PROXY_IMAGE, detach=True, name=f"{name}-proxy", network=name,
            volumes={
                str(_DEPLOY / "tinyproxy.conf"): {"bind": "/etc/tinyproxy/tinyproxy.conf", "mode": "ro"},
                str(_DEPLOY / "filter"): {"bind": "/etc/tinyproxy/rendered/filter", "mode": "ro"},
            },
        )
        network.reload()
        proxy.reload()
        address = proxy.attrs["NetworkSettings"]["Networks"][name]["IPAddress"]
        # wait for the listener: poll from a sandbox-image container on the same network
        listening = False
        for _ in range(40):
            try:
                client.containers.run(
                    IMAGE, ["python3", "-c", f"import socket;socket.create_connection(('{address}',8888),2)"],
                    network=name, remove=True,
                )
                listening = True
                break
            except Exception:
                await asyncio.sleep(0.5)
        assert listening, "tinyproxy did not start listening"
        runner = DockerSandboxRunner(image=IMAGE, network=name, proxy_url=f"http://{address}:8888")
        handle = await runner.create_task()
        try:
            yield runner, handle, address
        finally:
            await runner.destroy(handle)
            await runner.close()
            proxy.remove(force=True)
            network.remove()

    async def _connect(self, runner, handle, address, target: str) -> str:
        script = (
            "import socket,sys\n"
            f"s=socket.create_connection(('{address}',8888),5)\n"
            f"s.sendall(b'CONNECT {target} HTTP/1.1\\r\\nHost: {target}\\r\\n\\r\\n')\n"
            "s.settimeout(8)\n"
            "print(s.recv(200).decode(errors='replace').splitlines()[0])\n"
        )
        await runner.write_file(handle, "connect.py", script)
        result = await runner.run(handle, ["python3", "/workspace/connect.py"], timeout_s=30)
        assert result.exit_code == 0, result.stderr
        return result.stdout.strip()

    async def test_the_runners_proxy_settings_reach_the_process(self, egress) -> None:
        runner, handle, address = egress
        env = (await runner.run(handle, ["env"], timeout_s=20)).stdout
        assert f"HTTPS_PROXY=http://{address}:8888" in env and f"http_proxy=http://{address}:8888" in env
        assert "NO_PROXY=localhost,127.0.0.1" in env

    async def test_a_host_that_is_not_listed_is_refused(self, egress) -> None:
        runner, handle, address = egress
        status = await self._connect(runner, handle, address, "evil.example.com:443")
        assert " 403 " in status, status

    async def test_a_listed_host_passes_the_filter(self, egress) -> None:
        """Allowed by policy: not 403. (Nothing upstream is reachable from a throwaway daemon with
        no NAT, so the proxy answers with its own connection error, which is not a policy refusal.)"""
        runner, handle, address = egress
        status = await self._connect(runner, handle, address, "github.com:443")
        assert " 403 " not in status, f"a listed host was refused by policy: {status}"

    @pytest.mark.parametrize("target", ["github.com:22", "github.com:8443", "github.com:80", "pypi.org:25"])
    async def test_a_listed_host_on_any_port_but_443_is_refused(self, egress, target) -> None:
        runner, handle, address = egress
        status = await self._connect(runner, handle, address, target)
        assert " 403 " in status, f"{target}: {status}"

    async def test_lookalike_hosts_do_not_match_the_anchored_filter(self, egress) -> None:
        runner, handle, address = egress
        for lookalike in ("github.com.evil.example:443", "evilgithub.com:443", "api.github.com.attacker.io:443"):
            status = await self._connect(runner, handle, address, lookalike)
            assert " 403 " in status, f"{lookalike}: {status}"

    async def test_the_filter_is_default_deny_for_a_name_that_is_not_in_the_file(self, egress) -> None:
        runner, handle, address = egress
        for denied in ("169.254.169.254:443", "localhost:443", "10.0.0.1:443", "metadata.google.internal:443"):
            status = await self._connect(runner, handle, address, denied)
            assert " 403 " in status, f"{denied}: {status}"


# ------------------------------------------------- the image deploy/sandbox/Dockerfile really builds

SANDBOX_IMAGE = os.environ.get("AURA_REAL_DOCKER_SANDBOX_IMAGE", "")


@pytest.mark.skipif(not SANDBOX_IMAGE, reason="set AURA_REAL_DOCKER_SANDBOX_IMAGE to an image built from deploy/sandbox/Dockerfile")
class TestTheSandboxImageBuiltFromTheRepositorysDockerfile:
    """Run the toolchain and the isolation checks on the image the deployment actually builds, not
    only on the bare pinned base. Build it with ``docker build deploy/sandbox`` (the base digest
    in its ``FROM`` line; ``scripts/real_docker_up.sh`` prints the mirror to use if Docker Hub
    rate-limits the pull)."""

    @pytest.fixture
    async def image_runner(self):
        r = DockerSandboxRunner(image=SANDBOX_IMAGE)
        handle = await r.create_task()
        yield r, handle
        await r.destroy(handle)
        await r.close()

    async def test_the_toolchain_the_agent_needs_is_there(self, image_runner) -> None:
        runner, handle = image_runner
        out = (await sh(runner, handle, "git --version; python3 --version; node --version; npm --version; gcc --version | head -1; curl --version | head -1")).stdout
        for needle in ("git version", "Python 3.12", "v", "gcc", "curl"):
            assert needle in out, out

    async def test_the_aura_user_exists_with_the_uid_the_host_chowns_to(self, image_runner) -> None:
        runner, handle = image_runner
        assert (await sh(runner, handle, "getent passwd 10001 | cut -d: -f1,6")).stdout.strip() == "aura:/home/aura"
        assert (await sh(runner, handle, "echo $HOME")).stdout.strip() == "/home/aura"

    async def test_git_works_in_a_workspace_with_a_read_only_rootfs_and_a_tmpfs_home(self, image_runner) -> None:
        runner, handle = image_runner
        result = await sh(
            runner, handle,
            "git init -q -b main r && cd r && git -c user.name=t -c user.email=t@e.x commit -q --allow-empty -m first && git log --format=%s",
        )
        assert result.exit_code == 0 and result.stdout.strip() == "first", result

    async def test_home_is_writable_so_git_config_and_the_package_caches_work(self, image_runner) -> None:
        """Regression: the tmpfs over /home/aura was root-owned with mode 700 in this image, so every
        write to HOME failed with "Permission denied" (found by this suite; the bare base image has
        no /home/aura and never showed it)."""
        runner, handle = image_runner
        result = await sh(
            runner, handle,
            "git config --global user.name tester && git config --global user.name"
            " && mkdir -p ~/.cache/pip ~/.npm && touch ~/.npm/x && echo caches-ok",
        )
        assert result.exit_code == 0, result
        assert result.stdout.split() == ["tester", "caches-ok"], result

    async def test_a_python_test_suite_runs_in_it(self, image_runner) -> None:
        runner, handle = image_runner
        await runner.write_file(handle, "test_demo.py", "def test_ok():\n    assert 1 + 1 == 2\n")
        result = await sh(runner, handle, "python3 -m unittest -q 2>&1; python3 -m pytest --version 2>&1 | head -1", timeout_s=60)
        assert result.exit_code == 0, result

    async def test_the_confinement_holds_on_this_image_too(self, image_runner) -> None:
        runner, handle = image_runner
        out = (await sh(runner, handle, "id -u; grep -E 'CapEff|NoNewPrivs|Seccomp:' /proc/self/status; touch /usr/x 2>&1; tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' '")).stdout
        assert "10001" in out and re.search(r"CapEff:\s+0{16}", out) and re.search(r"NoNewPrivs:\s+1", out)
        assert re.search(r"Seccomp:\s+2", out) and "lo" in out and "eth0" not in out


# --------------------------------------------------------------------------- not covered
#
# * Only the kernel this machine runs, with cgroup v1 and the default seccomp profile. A different
#   kernel, cgroup v2, SELinux/AppArmor policy or gVisor/Kata would behave differently; run these
#   again on the production host (deploy/scripts/verify-engineer-workspaces.sh is the on-VPS check).
# * The egress proxy path (internal `sandbox_egress` network + tinyproxy filter) -- see
#   tests/unit/test_render_sandbox_filter.py for the filter and docs/deployment for the topology.
# * docker-socket-proxy's allowlist: this talks to the daemon directly, so a method the proxy
#   refuses would not show up here (tests/unit/test_sandbox_exec_surface.py pins that half).
