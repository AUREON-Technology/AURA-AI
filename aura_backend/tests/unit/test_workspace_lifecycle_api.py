"""Workspace start/stop/restart/stats and the paired-desktop status (ADR-041, roadmap row 89).

The real ``WorkspaceManager`` over the real in-memory workspace store and the real (unsandboxed,
temp-directory) ``InMemorySandboxRunner``, driven through the ASGI app. The Docker adapter's new
stats reader is tested against a small stand-in for the daemon's client, because a Docker daemon is
not available to the default suite (the live check is ``tests/load/test_docker_sandbox_live.py``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from aura_core.adapters.coding_fakes import InMemorySandboxRunner  # noqa: E402
from aura_core.adapters.host_agent import HostAgentRegistry, HostAgentSession  # noqa: E402
from aura_core.api import Principal, build_app  # noqa: E402
from aura_core.domain.errors import AuthenticationError  # noqa: E402
from aura_core.domain.identifiers import UserId  # noqa: E402
from aura_core.tools.workspaces import WorkspaceManager  # noqa: E402

ALICE, BOB = "alice", "bob"
AUTH_A = {"Authorization": f"Bearer {ALICE}"}
AUTH_B = {"Authorization": f"Bearer {BOB}"}
WS = "/api/v3/engineering/workspaces"


def _app(container):
    async def authenticator(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or header[7:] not in (ALICE, BOB):
            raise AuthenticationError("unknown principal")
        return Principal(user_id=UserId(header[7:]), email=f"{header[7:]}@example.com")

    return build_app(container, authenticator=authenticator)


def _manager(container, **kw) -> WorkspaceManager:
    defaults = dict(runner=InMemorySandboxRunner(), store=container.workspaces, min_free_memory_mb=0,
                    max_running=2, max_workspaces=4, disk_quota_mb=64)
    defaults.update(kw)
    container.workspace_manager = WorkspaceManager(**defaults)
    return container.workspace_manager


def _open(container, user: str, repo: str):
    return asyncio.run(container.workspace_manager.open(UserId(user), repo)).record


def _status(client, wid: str, headers=AUTH_A) -> str:
    rows = client.get(WS, headers=headers).json()["workspaces"]
    return next(w["status"] for w in rows if w["id"] == wid)


class TestStartStopRestart:
    def test_stop_then_start_changes_state_and_says_so(self, container) -> None:
        _manager(container)
        ws = _open(container, ALICE, "acme/web")
        with TestClient(_app(container)) as client:
            assert _status(client, ws.id.value) == "hot"
            stopped = client.post(f"{WS}/{ws.id.value}/stop", headers=AUTH_A)
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["changed"] is True and stopped.json()["workspace"]["status"] == "warm"
            assert container.workspace_manager._runner.is_paused(ws.id.value)

            again = client.post(f"{WS}/{ws.id.value}/stop", headers=AUTH_A).json()
            assert again["changed"] is False and again["workspace"]["status"] == "warm"

            started = client.post(f"{WS}/{ws.id.value}/start", headers=AUTH_A).json()
            assert started["changed"] is True and started["workspace"]["status"] == "hot"
            assert not container.workspace_manager._runner.is_paused(ws.id.value)
            noop = client.post(f"{WS}/{ws.id.value}/start", headers=AUTH_A).json()
            assert noop["changed"] is False and noop["workspace"]["status"] == "hot"

    def test_restart_stops_and_starts_again(self, container) -> None:
        _manager(container)
        ws = _open(container, ALICE, "acme/web")
        runner = container.workspace_manager._runner
        with TestClient(_app(container)) as client:
            first_opened = runner._opened_at[ws.id.value]
            body = client.post(f"{WS}/{ws.id.value}/restart", headers=AUTH_A).json()
            assert body["action"] == "restart" and body["changed"] is True
            assert body["workspace"]["status"] == "hot"
            assert runner._opened_at[ws.id.value] >= first_opened

    def test_the_files_survive_a_stop_and_start(self, container) -> None:
        _manager(container)
        ws = _open(container, ALICE, "acme/web")
        runner = container.workspace_manager._runner
        (Path(runner._scratch_dirs[ws.id.value]) / "notes.txt").write_text("keep me")
        with TestClient(_app(container)) as client:
            client.post(f"{WS}/{ws.id.value}/stop", headers=AUTH_A)
            client.post(f"{WS}/{ws.id.value}/start", headers=AUTH_A)
        assert (Path(runner._scratch_dirs[ws.id.value]) / "notes.txt").read_text() == "keep me"

    def test_starting_pauses_the_least_recently_used_to_stay_within_the_running_limit(
        self, container
    ) -> None:
        _manager(container, max_running=1)
        first = _open(container, ALICE, "acme/one")
        second = _open(container, ALICE, "acme/two")  # opening two paused one
        with TestClient(_app(container)) as client:
            rows = {w["repo"]: w["status"] for w in client.get(WS, headers=AUTH_A).json()["workspaces"]}
            assert sorted(rows.values()) == ["hot", "warm"]
            cold = first if rows["acme/one"] == "warm" else second
            hot = second if cold is first else first
            body = client.post(f"{WS}/{cold.id.value}/start", headers=AUTH_A).json()
        assert body["workspace"]["status"] == "hot"
        assert body["paused_to_make_room"] == 1 and body["paused_yours"] == [hot.repo]

    def test_making_room_never_names_another_persons_repository(self, container) -> None:
        _manager(container, max_running=1)
        theirs = _open(container, BOB, "bob/secret-project")
        mine = _open(container, ALICE, "acme/web")  # paused Bob's to make room
        assert asyncio.run(container.workspaces.get_by_id(theirs.id)).status == "warm"
        with TestClient(_app(container)) as client:
            client.post(f"{WS}/{mine.id.value}/stop", headers=AUTH_A)
            client.post(f"{WS}/{theirs.id.value}/start", headers=AUTH_B)  # Bob resumes
            response = client.post(f"{WS}/{mine.id.value}/start", headers=AUTH_A)
        body = response.json()
        assert body["paused_to_make_room"] == 1 and body["paused_yours"] == []
        assert "bob" not in response.text.lower()

    def test_no_capacity_is_a_clear_retryable_409_and_nothing_starts(self, container) -> None:
        _manager(container)
        ws = _open(container, ALICE, "acme/web")
        asyncio.run(container.workspace_manager.stop(ws))
        container.workspace_manager._min_free_memory_mb = 10**9  # the host "has no memory"
        with TestClient(_app(container)) as client:
            refused = client.post(f"{WS}/{ws.id.value}/start", headers=AUTH_A)
            assert refused.status_code == 409
            body = refused.json()
            assert body["error"] == "workspace_capacity" and body["retryable"] is True
            assert "memory" in body["message"]
            assert _status(client, ws.id.value) == "warm"
            # A restart refused for capacity leaves the workspace stopped, never half-started.
            again = client.post(f"{WS}/{ws.id.value}/restart", headers=AUTH_A)
            assert again.status_code == 409 and _status(client, ws.id.value) == "warm"

    def test_a_zero_running_limit_is_refused_not_ignored(self, container) -> None:
        _manager(container)
        ws = _open(container, ALICE, "acme/web")
        asyncio.run(container.workspace_manager.stop(ws))
        container.workspace_manager._max_running = 0
        with TestClient(_app(container)) as client:
            refused = client.post(f"{WS}/{ws.id.value}/start", headers=AUTH_A)
        assert refused.status_code == 409 and "running limit" in refused.json()["message"]

    def test_workspaces_not_enabled_is_404(self, container) -> None:
        container.workspace_manager = None
        with TestClient(_app(container)) as client:
            assert client.post(f"{WS}/anything/start", headers=AUTH_A).status_code == 404
            assert client.get(f"{WS}/anything/stats", headers=AUTH_A).status_code == 404

    def test_requires_authentication(self, container) -> None:
        _manager(container)
        with TestClient(_app(container)) as client:
            assert client.post(f"{WS}/x/start").status_code == 401
            assert client.get(f"{WS}/x/stats").status_code == 401


class TestOwnership:
    def test_bob_cannot_start_stop_restart_or_stat_alices_workspace(self, container) -> None:
        _manager(container)
        ws = _open(container, ALICE, "acme/private")
        with TestClient(_app(container)) as client:
            for verb in ("start", "stop", "restart"):
                assert client.post(f"{WS}/{ws.id.value}/{verb}", headers=AUTH_B).status_code == 404, verb
            assert client.get(f"{WS}/{ws.id.value}/stats", headers=AUTH_B).status_code == 404
            assert client.post(f"{WS}/nonexistent/stop", headers=AUTH_B).status_code == 404
            # Alice's workspace was not touched by any of it.
            assert _status(client, ws.id.value) == "hot"
            assert client.get(f"{WS}/{ws.id.value}/stats", headers=AUTH_A).status_code == 200


class TestStats:
    def test_a_running_workspace_reports_state_disk_and_why_cpu_is_missing(self, container) -> None:
        _manager(container)
        ws = _open(container, ALICE, "acme/web")
        runner = container.workspace_manager._runner
        (Path(runner._scratch_dirs[ws.id.value]) / "blob.bin").write_bytes(b"x" * (3 * 1024 * 1024))
        with TestClient(_app(container)) as client:
            body = client.get(f"{WS}/{ws.id.value}/stats", headers=AUTH_A).json()
        assert body["workspace"]["repo"] == "acme/web"
        assert body["state"] == "hot" and body["running"] is True
        assert body["uptime_s"] is not None and body["uptime_s"] >= 0
        assert body["disk"]["used_mb"] == pytest.approx(3.0, abs=0.1)
        assert body["disk"]["quota_mb"] == 64 and body["disk"]["percent"] == pytest.approx(4.7, abs=0.2)
        assert body["disk"]["over_quota"] is False
        # The runner knows no CPU or memory: null, with a reason -- not zeros.
        assert body["cpu"]["percent"] is None and body["memory"]["used_mb"] is None
        assert "CPU or memory" in body["resources"]["reason"]
        assert body["capacity"]["max_running"] == 2 and body["capacity"]["running"] == 1

    def test_over_quota_is_flagged(self, container) -> None:
        _manager(container, disk_quota_mb=2)
        ws = _open(container, ALICE, "acme/web")
        runner = container.workspace_manager._runner
        (Path(runner._scratch_dirs[ws.id.value]) / "blob.bin").write_bytes(b"x" * (3 * 1024 * 1024))
        with TestClient(_app(container)) as client:
            disk = client.get(f"{WS}/{ws.id.value}/stats", headers=AUTH_A).json()["disk"]
        assert disk["over_quota"] is True and disk["percent"] > 100

    def test_reading_the_stats_of_a_stopped_workspace_does_not_start_it(self, container) -> None:
        _manager(container)
        ws = _open(container, ALICE, "acme/web")
        asyncio.run(container.workspace_manager.stop(ws))
        runner = container.workspace_manager._runner
        with TestClient(_app(container)) as client:
            body = client.get(f"{WS}/{ws.id.value}/stats", headers=AUTH_A).json()
            assert body["state"] == "warm" and body["running"] is False and body["uptime_s"] is None
            assert body["cpu"]["percent"] is None and "stopped" in body["resources"]["reason"]
            assert body["resources"]["available"] is False
            assert _status(client, ws.id.value) == "warm"
        assert runner.is_paused(ws.id.value)

    def test_a_runner_that_reports_cpu_and_memory_has_them_passed_through(self, container) -> None:
        manager = _manager(container)
        ws = _open(container, ALICE, "acme/web")

        async def reading(handle):
            return {"started_at": "2026-11-23T00:00:00+00:00", "cpu_percent": 12.345,
                    "memory_mb": 210.5, "memory_limit_mb": 512.0}

        manager._runner.workspace_stats = reading
        with TestClient(_app(container)) as client:
            body = client.get(f"{WS}/{ws.id.value}/stats", headers=AUTH_A).json()
        assert body["cpu"]["percent"] == 12.35
        assert body["memory"] == {"used_mb": 210.5, "limit_mb": 512.0}
        assert body["resources"] == {"available": True, "reason": None}
        assert body["uptime_s"] > 0

    def test_a_runner_with_no_stats_support_says_so(self, container) -> None:
        manager = _manager(container)
        ws = _open(container, ALICE, "acme/web")
        manager._runner.workspace_stats = None  # as if the adapter predates the method
        with TestClient(_app(container)) as client:
            body = client.get(f"{WS}/{ws.id.value}/stats", headers=AUTH_A).json()
        assert body["cpu"]["percent"] is None
        assert body["resources"]["reason"] == "the runner does not report CPU or memory"


class _Daemon:
    """A paired daemon, as in test_remote_desktop_control.py."""

    def __init__(self, user: str, *, answer: bool = True) -> None:
        self.registry = HostAgentRegistry()
        self._answer = answer
        self.session = HostAgentSession(UserId(user), self._send)
        self.methods: list[str] = []

    async def _send(self, frame: dict) -> None:
        self.methods.append(frame["method"])
        if not self._answer:
            self.session.resolve({"type": "error", "id": frame["id"], "message": "unknown method"})
            return
        self.session.resolve({"type": "result", "id": frame["id"], "result": {
            "roots": [{"path": "/home/a/app", "trusted": True, "is_git": True}],
            "remote_control": False}})


class TestHostStatus:
    def test_not_connected(self, container) -> None:
        with TestClient(_app(container)) as client:
            body = client.get("/api/v3/host/status", headers=AUTH_A).json()
        assert body["connected"] is False and body["daemons"] == []
        assert body["controllable_remotely"] is False and "your own computer" in body["note"]

    def test_connected_lists_the_daemon_roots_and_remote_control_flag(self, container) -> None:
        daemon = _Daemon(ALICE)
        container.host_agent_registry = daemon.registry
        with TestClient(_app(container)) as client:
            client.portal.call(daemon.registry.register, daemon.session)
            body = client.get("/api/v3/host/status", headers=AUTH_A).json()
        assert body["connected"] is True and body["controllable_remotely"] is False
        [entry] = body["daemons"]
        assert entry["kind"] == "desktop" and entry["remote_control"] is False and entry["reachable"]
        assert entry["roots"] == [{"path": "/home/a/app", "trusted": True, "is_git": True}]
        assert daemon.methods == ["list_roots"], "status must only ever read"

    def test_it_is_the_callers_own_daemon_only(self, container) -> None:
        daemon = _Daemon(ALICE)
        container.host_agent_registry = daemon.registry
        with TestClient(_app(container)) as client:
            client.portal.call(daemon.registry.register, daemon.session)
            assert client.get("/api/v3/host/status", headers=AUTH_B).json()["connected"] is False

    def test_a_daemon_that_does_not_answer_is_connected_but_unreachable(self, container) -> None:
        daemon = _Daemon(ALICE, answer=False)
        container.host_agent_registry = daemon.registry
        with TestClient(_app(container)) as client:
            client.portal.call(daemon.registry.register, daemon.session)
            body = client.get("/api/v3/host/status", headers=AUTH_A).json()
        assert body["connected"] is True and body["daemons"][0]["reachable"] is False

    def test_there_is_no_route_that_starts_or_stops_a_desktop(self, container) -> None:
        paths = {r.path for r in _app(container).routes if "/host" in getattr(r, "path", "")}
        assert not [p for p in paths if p.rsplit("/", 1)[-1] in ("start", "stop", "restart", "kill")]


# -- the Docker adapter's stats reader, against a stand-in for the daemon's client --------------------


class _Container:
    def __init__(self, *, running: bool, sample: dict[str, Any]) -> None:
        self.attrs = {"State": {"Running": running, "StartedAt": "2026-11-23T01:00:00.123456789Z"}}
        self._sample = sample

    def reload(self) -> None:
        pass

    def stats(self, stream: bool = False) -> dict[str, Any]:
        assert stream is False
        return self._sample


class _Client:
    def __init__(self, containers: list[_Container]) -> None:
        self.containers = self
        self._list = containers
        self.filters: Any = None

    def list(self, all: bool = False, filters: Any = None) -> list[_Container]:
        self.filters = filters
        return self._list


class TestDockerStatsReader:
    def _runner(self, containers):
        from aura_core.adapters.docker_sandbox import DockerSandboxRunner

        runner = DockerSandboxRunner(image="aura-sandbox:latest")
        runner._client = _Client(containers)
        return runner

    def test_cpu_memory_and_start_time_from_one_sample(self) -> None:
        from aura_core.ports.coding import SandboxHandle

        sample = {
            "cpu_stats": {"cpu_usage": {"total_usage": 300_000_000}, "system_cpu_usage": 10_000_000_000,
                          "online_cpus": 4},
            "precpu_stats": {"cpu_usage": {"total_usage": 200_000_000}, "system_cpu_usage": 9_000_000_000},
            "memory_stats": {"usage": 300 * 1024 * 1024, "limit": 512 * 1024 * 1024,
                             "stats": {"inactive_file": 100 * 1024 * 1024}},
        }
        runner = self._runner([_Container(running=True, sample=sample)])
        reading = asyncio.run(runner.workspace_stats(SandboxHandle(task_id="abc")))
        assert reading["cpu_percent"] == pytest.approx(40.0)  # 100M of 1000M system ns x 4 cpus
        assert reading["memory_mb"] == pytest.approx(200.0) and reading["memory_limit_mb"] == 512.0
        assert reading["started_at"].startswith("2026-11-23T01:00:00")
        assert runner._client.filters == {"label": "aura.workspace=abc"}

    def test_a_stopped_or_missing_container_reports_nothing(self) -> None:
        from aura_core.ports.coding import SandboxHandle

        stopped = self._runner([_Container(running=False, sample={})])
        assert asyncio.run(stopped.workspace_stats(SandboxHandle(task_id="abc"))) is None
        assert asyncio.run(self._runner([]).workspace_stats(SandboxHandle(task_id="abc"))) is None

    def test_a_sample_with_no_deltas_leaves_cpu_unknown_rather_than_zero(self) -> None:
        from aura_core.ports.coding import SandboxHandle

        runner = self._runner([_Container(running=True, sample={"cpu_stats": {}, "memory_stats": {}})])
        reading = asyncio.run(runner.workspace_stats(SandboxHandle(task_id="abc")))
        assert reading["cpu_percent"] is None and reading["memory_mb"] is None

    def test_the_reader_never_execs_or_starts_anything(self) -> None:
        import inspect

        from aura_core.adapters.docker_sandbox import DockerSandboxRunner

        source = inspect.getsource(DockerSandboxRunner.workspace_stats)
        for forbidden in ("exec_run", "exec_create", ".start(", ".run(", "open_workspace"):
            assert forbidden not in source
