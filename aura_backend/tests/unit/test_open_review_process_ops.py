"""Review of the process / ops / network tools in ``aura_core/tools/open``.

Real processes, real sockets on 127.0.0.1, real zip/tar/sqlite/PDF files -- nothing under test is
mocked. Every test that starts a child asserts afterwards that no child (or grandchild) is left.
Each bug fixed in the review has a test here that fails on the old behaviour.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import sys
import tarfile
import threading
import time
import tracemalloc
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aura_core.tools.open import _ops_common
from aura_core.tools.open import dev_server_manager as dev_module
from aura_core.tools.open.agent_tool_test_harness import AgentToolTestHarness
from aura_core.tools.open.background_task_manager import BackgroundTaskManager
from aura_core.tools.open.backup_restore_verifier import BackupRestoreVerifier
from aura_core.tools.open.browser_subagent import BrowserSubagent, _route_guard
from aura_core.tools.open.dependency_manager_validator import DependencyManagerValidator
from aura_core.tools.open.deployment_release_manager import DeploymentReleaseManager
from aura_core.tools.open.dev_server_manager import DevServerManager
from aura_core.tools.open.performance_profiler import PerformanceProfiler
from aura_core.tools.open.push_notification_dispatcher import PushNotificationDispatcher
from aura_core.tools.open.remote_session_controller import RemoteSessionController
from aura_core.tools.open import test_coverage_and_mutation_analyzer as cov_module
from aura_core.tools.open.test_coverage_and_mutation_analyzer import (
    TestCoverageAndMutationAnalyzer as CoverageAnalyzer,
)
from tests.unit.test_document_extraction import _pdf

PY = sys.executable
MARKER = "ZZ-LAST-PAGE-MARKER"


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback requests must go straight to the local test server, whatever the host exports."""
    for name in list(os.environ):
        if name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


@pytest.fixture(autouse=True)
def _fresh_port_ownership() -> None:
    """The dev-server manager remembers the ports it allocated for the whole process; start clean."""
    dev_module._OWNED_PORTS.clear()
    yield
    dev_module._OWNED_PORTS.clear()


def _alive(pid: int) -> bool:
    """True if ``pid`` is running (a zombie awaiting reaping does not count)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _wait_for(predicate, seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _long_pdf(pages: int = 45, last_text: str = MARKER) -> bytes:
    spec = [{"text": [f"page {i} filler line"]} for i in range(1, pages)]
    spec.append({"text": [last_text]})
    return _pdf(spec)


class _Handler(BaseHTTPRequestHandler):
    received: list = []

    def log_message(self, *args) -> None:  # noqa: D401 - silence
        pass

    def do_GET(self) -> None:
        if self.path == "/ok":
            self.send_response(200)
        elif self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "/ok")
        elif self.path == "/to-metadata":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
        else:
            self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).received.append((self.path, body))
        if self.path.startswith("/moved"):
            self.send_response(302)
            self.send_header("Location", "/hook-final")
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture()
def http_server():
    _Handler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(5)
    assert not thread.is_alive()


class _SocketCounter:
    """Counts every attempt to resolve a name or open a connection; raises as a black hole would."""

    def __init__(self) -> None:
        self.calls: list = []
        self.allow_loopback = False

    @property
    def total(self) -> int:
        return len(self.calls)


@pytest.fixture()
def no_sockets(monkeypatch: pytest.MonkeyPatch) -> _SocketCounter:
    """Instrument DNS and connect so a test can prove a refusal happened *before* any network use."""
    import socket

    counter = _SocketCounter()
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host, *args, **kwargs):
        if counter.allow_loopback and host in ("127.0.0.1", "localhost"):
            return real_getaddrinfo(host, *args, **kwargs)
        counter.calls.append(("getaddrinfo", host))
        raise socket.gaierror("blackholed by the test")

    def _is_loopback(address) -> bool:
        return isinstance(address, tuple) and bool(address) and address[0] in ("127.0.0.1", "localhost")

    def connect(self, address):
        if counter.allow_loopback and _is_loopback(address):
            return real_connect(self, address)
        counter.calls.append(("connect", address))
        raise OSError("blackholed by the test")

    def connect_ex(self, address):
        if counter.allow_loopback and _is_loopback(address):
            return real_connect_ex(self, address)
        counter.calls.append(("connect_ex", address))
        return 111

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    return counter


# ======================================================================================
# BackgroundTaskManager
# ======================================================================================


class TestBackgroundTaskManager:
    def test_secrets_in_the_gateway_environment_do_not_reach_the_child(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_JWT_SECRET", "super-secret-value")
        mgr = BackgroundTaskManager()
        started = mgr.start_task("t", [PY, "-c", "import os;print(sorted(k for k in os.environ if 'AURA' in k))"])
        assert started["started"] is True
        done = mgr.wait_for_task("t", 20)
        assert done["completed"] is True
        assert "AURA_JWT_SECRET" not in done["stdout"]
        assert "super-secret" not in json.dumps(done)
        mgr.terminate_all()

    def test_explicit_env_is_passed_to_the_child(self) -> None:
        mgr = BackgroundTaskManager()
        mgr.start_task("t", [PY, "-c", "import os;print(os.environ['FOO_FLAG'])"], env={"FOO_FLAG": "yes"})
        assert mgr.wait_for_task("t", 20)["stdout"] == "yes"
        mgr.terminate_all()

    def test_a_chatty_task_does_not_deadlock_on_a_full_pipe(self) -> None:
        # 3 MB to stdout: an undrained PIPE blocks the child at ~64 KiB and wait_for_task timed out.
        mgr = BackgroundTaskManager()
        mgr.start_task("t", [PY, "-c", "import sys;sys.stdout.write('x'*3_000_000 + 'END')"])
        done = mgr.wait_for_task("t", 20)
        assert done["completed"] is True and done["exit_code"] == 0
        assert done["stdout"].endswith("END")
        assert len(done["stdout"]) < 200_000  # a bounded tail, not 3 MB
        mgr.terminate_all()

    def test_invalid_utf8_output_is_a_result_not_an_exception(self) -> None:
        mgr = BackgroundTaskManager()
        mgr.start_task("t", [PY, "-c", "import sys;sys.stdout.buffer.write(b'ok \\xff\\xfe done')"])
        assert "ok" in mgr.wait_for_task("t", 20)["stdout"]
        mgr.terminate_all()

    def test_terminating_a_task_kills_the_children_it_spawned(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "child.pid"
        script = (
            "import subprocess,sys,time;"
            "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
            f"open({str(pid_file)!r},'w').write(str(c.pid));time.sleep(120)"
        )
        mgr = BackgroundTaskManager()
        started = mgr.start_task("t", [PY, "-c", script])
        assert _wait_for(pid_file.exists)
        assert _wait_for(lambda: pid_file.read_text() != "")
        child = int(pid_file.read_text())
        assert _alive(child) and _alive(started["pid"])
        result = mgr.terminate_task("t")
        assert result["terminated"] is True
        assert _wait_for(lambda: not _alive(child)), "the grandchild survived terminate_task"
        assert not _alive(started["pid"])
        assert mgr.get_status("t")["found"] is False

    def test_a_running_task_id_cannot_be_reused(self) -> None:
        mgr = BackgroundTaskManager()
        first = mgr.start_task("t", [PY, "-c", "import time;time.sleep(60)"])
        second = mgr.start_task("t", [PY, "-c", "print(1)"])
        assert second["started"] is False and "still running" in second["error"]
        assert mgr.get_status("t")["pid"] == first["pid"]  # the original is still the tracked one
        mgr.terminate_all()
        assert not _alive(first["pid"])

    def test_bad_input_is_a_result(self, tmp_path: Path) -> None:
        mgr = BackgroundTaskManager()
        assert mgr.start_task("t", None)["started"] is False
        assert mgr.start_task("t", "echo 'unterminated")["started"] is False
        assert mgr.start_task("t", [])["started"] is False
        assert mgr.start_task("t", [PY, 5])["started"] is False
        assert mgr.start_task("", "echo hi")["started"] is False
        assert mgr.start_task("t", [PY, "a\x00b"])["started"] is False
        assert mgr.start_task("t", ["definitely-not-a-binary-xyz"])["started"] is False
        assert mgr.start_task("t", [PY, "-c", "1"], cwd=str(tmp_path / "nope"))["started"] is False
        assert mgr.send_task_input("t", "x")["delivered"] is False
        assert mgr.terminate_task("t")["terminated"] is False
        assert mgr.list_tasks() == []

    def test_cwd_and_executable_confinement(self, tmp_path: Path) -> None:
        mgr = BackgroundTaskManager()
        inside = tmp_path / "in"
        inside.mkdir()
        outside = tmp_path / "out"
        outside.mkdir()
        refused = mgr.start_task("a", [PY, "-c", "1"], cwd=str(outside), base_dir=str(inside))
        assert refused["started"] is False and "outside" in refused["error"]
        refused = mgr.start_task("b", [PY, "-c", "1"], allowed_executables=["git"])
        assert refused["started"] is False and "allowed" in refused["error"]
        ok = mgr.start_task("c", [PY, "-c", "import os;print(os.getcwd())"], cwd=str(inside), base_dir=str(inside))
        assert ok["started"] is True
        assert mgr.wait_for_task("c", 20)["stdout"] == str(inside.resolve())
        mgr.terminate_all()

    def test_stdin_round_trip_and_unicode(self) -> None:
        mgr = BackgroundTaskManager()
        mgr.start_task("t", [PY, "-c", "import sys;sys.stdout.reconfigure(encoding='utf-8');"
                                       "print(sys.stdin.readline().strip()[::-1])"])
        assert mgr.send_task_input("t", "مرحبا")["delivered"] is True
        assert mgr.send_task_input("t", None)["delivered"] is False
        assert mgr.wait_for_task("t", 20)["stdout"] == "ابحرم"
        mgr.terminate_all()


# ======================================================================================
# DevServerManager
# ======================================================================================


class TestDevServerManager:
    @pytest.mark.parametrize(
        "command",
        [
            "python -c 'print(1)'",
            "python3 -Sc 'print(1)'",
            "python -m pip install requests",
            "node -e 'process.exit()'",
            "node --eval 1",
            "npx cowsay hi",
            "npm exec evil",
            "npm install",
            "yarn dlx evil",
            "rm -rf /",
        ],
    )
    def test_code_execution_forms_are_refused(self, command: str, tmp_path: Path) -> None:
        mgr = DevServerManager()
        res = mgr.start_server(command, cwd=str(tmp_path), wait_seconds=0)
        assert res["started"] is False and "rejected" in res["error"]
        assert mgr._processes == {}

    def test_an_executable_with_an_allowed_basename_elsewhere_is_refused(self, tmp_path: Path) -> None:
        fake = tmp_path / "python"
        marker = tmp_path / "ran"
        fake.write_text(f"#!/bin/sh\ntouch {marker}\n")
        fake.chmod(0o755)
        res = DevServerManager().start_server(f"{fake} -m http.server", cwd=str(tmp_path), wait_seconds=0)
        assert res["started"] is False
        time.sleep(0.2)
        assert not marker.exists(), "the look-alike binary was executed"

    def test_http_server_lifecycle_health_and_group_stop(self, tmp_path: Path, monkeypatch) -> None:
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "python3").symlink_to(PY)
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
        (tmp_path / "index.html").write_text("hi")
        mgr = DevServerManager()
        port = DevServerManager.find_free_port(18500)
        res = mgr.start_server(f"python3 -m http.server {port} --bind 127.0.0.1", cwd=str(tmp_path),
                               wait_seconds=0.5, port=port)
        assert res["started"] is True, res
        pid = res["pid"]
        try:
            assert DevServerManager.wait_for_port_ready("127.0.0.1", port, 10) is True
            health = DevServerManager.check_http_health(f"http://127.0.0.1:{port}/index.html")
            assert health["healthy"] is True and health["status_code"] == 200
            assert mgr.check_process_status(pid)["running"] is True
            assert "GET /index.html" in _wait_output(mgr, pid)
        finally:
            stopped = mgr.stop_server(pid)
        assert stopped["stopped"] is True
        assert not _alive(pid)
        # a stopped server's port is no longer one the manager will connect to
        assert DevServerManager.wait_for_port_ready("127.0.0.1", port, 0.5) is False
        assert mgr.check_process_status(pid)["tracked"] is False

    def test_restart_of_an_untracked_pid_starts_nothing(self, tmp_path: Path) -> None:
        mgr = DevServerManager()
        res = mgr.restart_server(999999, "python3 -m http.server 0", cwd=str(tmp_path))
        assert res["stopped"] is False and res["new_server"]["started"] is False
        assert mgr._processes == {}

    def test_health_check_only_talks_to_ports_this_module_allocated(self, no_sockets) -> None:
        check = DevServerManager.check_http_health
        for url in ("file:///etc/hostname", "http://169.254.169.254/", "http://127.0.0.1:8000/",
                    "http://127.0.0.1:1/", "http://127.0.0.1/", "http://localhost:80/", "http://gateway:8000/",
                    "http://10.0.0.1:9000/", "https://example.com/", "http://0.0.0.0:8000/", None, 5, "",
                    "http://127.0.0.1:99999/", "http://user:pw@127.0.0.1:8000/"):
            res = check(url)
            assert res["healthy"] is False and res["status_code"] is None, url
        assert no_sockets.total == 0, no_sockets.calls

    def test_health_check_on_an_owned_port_refuses_gateway_paths_and_never_follows_redirects(
        self, no_sockets
    ) -> None:
        port = DevServerManager.find_free_port(18700)
        server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            no_sockets.allow_loopback = True
            base = f"http://127.0.0.1:{port}"
            assert DevServerManager.check_http_health(f"{base}/ok")["healthy"] is True
            moved = DevServerManager.check_http_health(f"{base}/redir")
            assert moved["healthy"] is False and moved["status_code"] == 302  # reported, not followed
            before = no_sockets.total
            for path in ("/api/v3/chat", "/health", "/health/ready", "/x/../api/v3/chat", "/%61pi/v3/x"):
                res = DevServerManager.check_http_health(f"{base}{path}")
                assert res["healthy"] is False and "refused" in res["error"], path
            assert no_sockets.total == before, "a refused path opened a socket"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
        assert not thread.is_alive()

    def test_port_helpers_survive_garbage_and_refuse_foreign_ports(self, no_sockets) -> None:
        assert 1 <= DevServerManager.find_free_port(70000, 5) <= 65535
        assert 1 <= DevServerManager.find_free_port(None, None) <= 65535
        dev_module._OWNED_PORTS.clear()  # those two allocations made 8000-ish ports "owned"; forget them
        before = no_sockets.total
        wait = DevServerManager.wait_for_port_ready
        assert wait("127.0.0.1", "nope", 0.1) is False
        assert wait("127.0.0.1", 99999, 0.1) is False
        assert wait("127.0.0.1", 8000, 0.1) is False  # the gateway's usual port: never allocated here
        assert wait("10.255.255.1", 80, 0.1) is False
        assert wait("gateway", 8000, 0.1) is False
        assert wait("localhost", True, 0.1) is False
        assert no_sockets.total == before, "a refused probe opened a socket"

    def test_start_server_refuses_foreign_ports_and_credential_env(self, tmp_path: Path) -> None:
        mgr = DevServerManager()
        try:
            # port 7 (echo) is never handed out by find_free_port, whatever other tests allocated
            res = mgr.start_server("python3 -m http.server 7", cwd=str(tmp_path), port=7)
            assert res["started"] is False and "find_free_port" in res["error"]
            res = mgr.start_server("python3 -m http.server 0", cwd=str(tmp_path), env={"AURA_JWT_SECRET": "x"})
            assert res["started"] is False and "credentials" in res["error"]
            assert mgr._processes == {}
        finally:
            mgr.stop_all()

def _wait_output(mgr: DevServerManager, pid: int) -> str:
    _wait_for(lambda: "GET" in mgr.read_output(pid).get("stderr", "") or "GET" in mgr.read_output(pid).get("stdout", ""), 5)
    out = mgr.read_output(pid)
    return out.get("stdout", "") + out.get("stderr", "")


# ======================================================================================
# BrowserSubagent  (Playwright is not installed here: the guards are what can be tested)
# ======================================================================================


class _Route:
    def __init__(self, url: str) -> None:
        self.request = type("Req", (), {"url": url})()
        self.verdict = None

    def abort(self) -> None:
        self.verdict = "abort"

    def continue_(self) -> None:
        self.verdict = "continue"


class TestBrowserSubagent:
    @pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "http://169.254.169.254/",
                                     "http://user:pw@example.com/", "", None, "ftp://example.com/"])
    def test_urls_outside_the_policy_are_refused_before_anything_launches(self, url) -> None:
        assert BrowserSubagent.run_actions(url)["executed"] is False
        assert "refused" in BrowserSubagent.run_actions(url)["error"]
        assert BrowserSubagent.extract_page_content(url)["success"] is False
        assert BrowserSubagent.check_url_accessibility(url)["accessible"] is False
        assert BrowserSubagent.run_script_in_page(url, "1")["success"] is False

    def test_loopback_is_never_reachable(self, http_server, no_sockets) -> None:
        # there is no allow_local switch any more: a local server is exactly what must stay unreachable
        assert BrowserSubagent.check_url_accessibility(f"{http_server}/ok")["accessible"] is False
        assert BrowserSubagent.extract_page_content(f"{http_server}/ok")["success"] is False
        assert _Handler.received == [] and no_sockets.total == 0
        import inspect
        for method in (BrowserSubagent.run_actions, BrowserSubagent.extract_page_content,
                       BrowserSubagent.navigate_and_screenshot, BrowserSubagent.run_script_in_page,
                       BrowserSubagent.check_url_accessibility):
            assert not any("allow" in name for name in inspect.signature(method).parameters)

    def test_screenshot_path_must_be_an_image_inside_base_dir(self, tmp_path: Path) -> None:
        victim = tmp_path / ".bashrc"
        victim.write_text("keep me")
        res = BrowserSubagent.navigate_and_screenshot("https://example.com", str(victim))
        assert res["success"] is False and ".png" in res["error"]
        assert victim.read_text() == "keep me"
        res = BrowserSubagent.run_actions("https://example.com", screenshot_path=str(victim))
        assert res["executed"] is False and victim.read_text() == "keep me"
        outside = tmp_path.parent / "shot.png"
        res = BrowserSubagent.navigate_and_screenshot("https://example.com", str(outside), base_dir=str(tmp_path))
        assert res["success"] is False and "outside" in res["error"]

    def test_without_playwright_there_is_no_fake_success(self) -> None:
        if BrowserSubagent.check_playwright_availability()["available"]:
            pytest.skip("Playwright is installed here")
        res = BrowserSubagent.run_actions("https://example.com", [{"type": "click", "selector": "#x"}])
        assert res["executed"] is False and "Playwright" in res["error"]
        assert BrowserSubagent.run_actions("https://example.com", ["not a dict"])["executed"] is False
        assert BrowserSubagent.run_actions("https://example.com", [{}] * 99)["executed"] is False

    def test_the_route_guard_aborts_internal_requests_and_lets_public_ones_through(self, http_server, no_sockets) -> None:
        guard = _route_guard()
        for url in ("http://169.254.169.254/latest/", f"{http_server}/ok", "file:///etc/passwd",
                    "http://gateway:8000/api/v3/chat", "https://aureon-ai.tech/", "https://example.com/health"):
            route = _Route(url)
            guard(route)
            assert route.verdict == "abort", url
        route = _Route("https://93.184.216.34/app.js")  # a public IP literal: no DNS needed to decide
        guard(route)
        assert route.verdict == "continue"
        assert no_sockets.total == 0

# ======================================================================================
# DeploymentReleaseManager
# ======================================================================================


class TestDeploymentReleaseManager:
    def test_a_service_named_like_an_option_is_refused(self, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}\n")
        for bad in (["-v"], ["--rmi", "all"], "web", ["a b"], [None]):
            res = DeploymentReleaseManager.compose_action("down", str(compose), services=bad)
            assert res["success"] is False and "services must be" in res["error"], bad

    def test_bad_actions_files_and_tags_are_results(self, tmp_path: Path) -> None:
        assert DeploymentReleaseManager.compose_action(None)["success"] is False
        assert DeploymentReleaseManager.compose_action("exec")["success"] is False
        missing = DeploymentReleaseManager.compose_action("ps", str(tmp_path / "none.yml"))
        assert missing["success"] is False and "not found" in missing["error"]
        for tag in ("-oops", "", None, "a b", "x" * 400, "--network=host"):
            assert DeploymentReleaseManager.build_docker_image(tag)["success"] is False, tag
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM scratch\n")
        res = DeploymentReleaseManager.build_docker_image("ok:1", str(dockerfile), "/")
        assert res["success"] is False and "root" in res["error"]
        res = DeploymentReleaseManager.build_docker_image("ok:1", str(dockerfile), str(tmp_path), base_dir=str(tmp_path / "x"))
        assert res["success"] is False

    def test_a_context_that_starts_with_a_dash_cannot_become_an_option(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "--network=host").mkdir()
        (tmp_path / "Dockerfile").write_text("FROM scratch\n")
        res = DeploymentReleaseManager.build_docker_image("ok:1", "Dockerfile", "--network=host")
        # either docker is absent (clear error) or the context was passed as an absolute path and docker
        # ran it; in neither case was an option injected.
        assert res["success"] is False or res.get("exit_code") == 0
        if "error" in res:
            assert "docker" in res["error"].lower()

    def test_readiness_probes(self, http_server, no_sockets) -> None:
        empty = DeploymentReleaseManager.check_readiness_probes([])
        assert empty["all_healthy"] is False and "nothing was checked" in empty["error"]
        assert DeploymentReleaseManager.check_readiness_probes(None)["all_healthy"] is False
        # a local service is no longer probeable: this is the gateway-reachability fence
        local = DeploymentReleaseManager.check_readiness_probes([f"{http_server}/ok", "http://gateway:8000/"])
        assert local["all_healthy"] is False and local["healthy_count"] == 0
        assert all("refused" in r["error"] for r in local["probe_results"])
        bad = DeploymentReleaseManager.check_readiness_probes(["file:///etc/passwd", "http://169.254.169.254/", 5])
        assert bad["all_healthy"] is False and bad["healthy_count"] == 0
        assert DeploymentReleaseManager.check_readiness_probes(["https://93.184.216.34/ok"] * 60)["all_healthy"] is False
        assert _Handler.received == [] and no_sockets.total == 0

    @pytest.mark.parametrize("coverage", [math.nan, None, -5, 250, "90", math.inf])
    def test_unscorable_risk_input_is_never_auto_deployable(self, coverage) -> None:
        res = DeploymentReleaseManager.calculate_deployment_risk(1, False, True, coverage)
        assert res["can_deploy_automatically"] is False and res["risk_level"] == "UNKNOWN" and "error" in res

    def test_risk_other_bad_inputs_and_unchanged_scoring(self) -> None:
        assert DeploymentReleaseManager.calculate_deployment_risk(-1, False, True, 90)["valid"] is False
        assert DeploymentReleaseManager.calculate_deployment_risk(1, "no", True, 90)["valid"] is False
        assert DeploymentReleaseManager.calculate_deployment_risk(None, False, True, 90)["valid"] is False
        assert DeploymentReleaseManager.calculate_deployment_risk(3, False, True, 90.0)["risk_level"] == "LOW"
        high = DeploymentReleaseManager.calculate_deployment_risk(40, True, False, 10)
        assert high["risk_level"] == "CRITICAL" and high["can_deploy_automatically"] is False

    def test_rollback_plans_are_honest(self) -> None:
        assert DeploymentReleaseManager.plan_rollout("api", "2", "")["rollback_supported"] is False
        assert DeploymentReleaseManager.plan_rollout("api", "2", "  ")["rollback_supported"] is False
        assert DeploymentReleaseManager.plan_rollout("api", "2", "1")["rollback_supported"] is True
        assert DeploymentReleaseManager.plan_rollout("", "2", "1")["valid"] is False
        assert DeploymentReleaseManager.plan_rollout("api", "2", None, "/h")["health_checks"] == ["/h"]
        plan = DeploymentReleaseManager.generate_rollback_plan("api", "2", "1", "canary")
        assert plan["valid"] is True and plan["rollback_steps"] and plan["executed"] is False
        bogus = DeploymentReleaseManager.generate_rollback_plan("api", "2", "1", "yolo")
        assert bogus["valid"] is False and bogus["rollback_steps"] == []  # used to be silently "rolling"
        assert DeploymentReleaseManager.generate_rollback_plan("api", "2", "", "rolling")["valid"] is False
        assert DeploymentReleaseManager.generate_rollback_plan("api", "2", "1", None)["valid"] is False


# ======================================================================================
# RemoteSessionController
# ======================================================================================


class TestRemoteSessionController:
    def test_a_terminated_session_is_not_resurrected(self) -> None:
        ctrl = RemoteSessionController()
        ctrl.get_or_create_session("s")
        ctrl.save_session_checkpoint("s", {"step": 1})
        assert ctrl.terminate_session("s")["updated"] is True
        for action in ("resume", "pause", "cancel", "redirect"):
            res = ctrl.apply_action("s", action, redirect_target="x")
            assert res["updated"] is False and "final" in res["error"]
        assert ctrl.restore_session_checkpoint("s")["restored"] is False
        assert ctrl.save_session_checkpoint("s", {})["saved"] is False
        assert ctrl.get_or_create_session("s")["state"] == "terminated"

    def test_unknown_sessions_and_bad_input_are_results_not_phantoms(self) -> None:
        ctrl = RemoteSessionController()
        assert ctrl.apply_action("typo", "pause")["updated"] is False
        assert ctrl.save_session_checkpoint("typo", {})["saved"] is False
        assert ctrl.list_active_sessions() == []
        ctrl.get_or_create_session("s")
        assert ctrl.apply_action("s", None)["updated"] is False
        assert ctrl.apply_action("s", 5)["updated"] is False
        assert ctrl.apply_action("s", "pause", reason=5)["updated"] is False
        assert ctrl.apply_action(None, "pause")["updated"] is False
        assert ctrl.apply_action("s", "redirect")["updated"] is False  # no target: not a silent no-op
        assert ctrl.save_session_checkpoint("s", "not a dict")["saved"] is False
        assert ctrl.list_active_sessions(5) == []
        with pytest.raises(ValueError):
            ctrl.get_or_create_session("")
        with pytest.raises(ValueError):
            ctrl.get_or_create_session(None)

    def test_checkpoint_flag_is_true_only_when_a_checkpoint_exists(self) -> None:
        ctrl = RemoteSessionController()
        assert ctrl.get_or_create_session("s")["checkpoint_saved"] is False
        res = ctrl.apply_action("s", "pause")
        assert res["checkpoint_saved"] is False and "does not signal" in res["note"]
        ctrl.save_session_checkpoint("s", {"a": 1})
        assert ctrl.apply_action("s", "resume")["checkpoint_saved"] is True

    def test_snapshots_are_copied_in_and_out(self) -> None:
        ctrl = RemoteSessionController()
        ctrl.get_or_create_session("s")
        data = {"steps": [1, 2]}
        ctrl.save_session_checkpoint("s", data)
        data["steps"].append(3)
        restored = ctrl.restore_session_checkpoint("s")
        assert restored["snapshot_data"] == {"steps": [1, 2]}
        restored["snapshot_data"]["steps"].clear()
        assert ctrl.restore_session_checkpoint("s")["snapshot_data"] == {"steps": [1, 2]}
        record = ctrl.get_or_create_session("s")
        record["state"] = "hacked"
        assert ctrl.get_or_create_session("s")["state"] == "running"

    def test_limits(self) -> None:
        ctrl = RemoteSessionController()
        ctrl.get_or_create_session("s")
        assert ctrl.save_session_checkpoint("s", {"blob": "x" * 2_000_000})["saved"] is False
        ids = {ctrl.save_session_checkpoint("s", {"i": i})["checkpoint_id"] for i in range(105)}
        assert len(ids) == 105  # ids stay unique after old checkpoints are dropped
        assert ctrl.list_active_sessions()[0]["checkpoints_stored"] == 100
        assert ctrl.restore_session_checkpoint("s", "nope")["restored"] is False


# ======================================================================================
# PushNotificationDispatcher
# ======================================================================================


class TestPushNotificationDispatcher:
    def test_without_a_destination_nothing_is_reported_delivered(self) -> None:
        res = PushNotificationDispatcher.dispatch("t", "m")
        assert res["dispatched"] is False and res["status"] == "not_sent" and res["payload"]["title"] == "t"

    def test_hostile_destinations_are_refused(self, http_server) -> None:
        for url in ("file:///etc/passwd", "http://169.254.169.254/", f"{http_server}/hook", "gopher://x", "",
                    "https://user:pw@example.com/x", "http://gateway:8000/api/v3/chat"):
            res = PushNotificationDispatcher.dispatch("t", "m", webhook_url=url)
            assert res["dispatched"] is False, url
        assert _Handler.received == []  # the loopback server was never contacted

    def test_the_webhook_secret_is_never_echoed(self, no_sockets) -> None:
        url = "http://gateway:8000/hook/SECRET-TOKEN-123"  # refused, and the refusal must not leak it
        res = PushNotificationDispatcher.dispatch("t", "m", webhook_url=url)
        assert res["dispatched"] is False
        assert "SECRET-TOKEN-123" not in json.dumps(res)
        res = PushNotificationDispatcher.dispatch_to_webhook(
            "https://hooks.example.com/services/SECRET-TOKEN-123", {"x": object()})
        assert "SECRET-TOKEN-123" not in json.dumps(res) and res["url"] == "https://hooks.example.com/..."
        assert no_sockets.total == 0

    def test_a_redirect_is_never_followed(self) -> None:
        import urllib.error
        import urllib.request
        handler = _ops_common._GuardedRedirect(None)
        req = urllib.request.Request("https://93.184.216.34/hook/TOKEN-9", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            handler.redirect_request(req, None, 302, "Found", {}, "https://93.184.216.35/elsewhere")
        assert "TOKEN-9" not in str(caught.value) and "not followed" in str(caught.value)

    def test_bad_inputs_never_raise(self, no_sockets) -> None:
        build = PushNotificationDispatcher.build_payload
        assert build(None, None, None, None)["priority"] == "normal"
        assert build("t" * 10_000, "m")["title"] == "t" * 256
        assert PushNotificationDispatcher.format_slack_block_kit(None, None, None, None)["blocks"]
        assert PushNotificationDispatcher.format_discord_embed(None, None, None, None)["embeds"]
        assert PushNotificationDispatcher.format_ntfy_headers(None, None, "nope")["Priority"] == "3"
        hook = "https://93.184.216.34/h"
        res = PushNotificationDispatcher.dispatch_to_webhook(hook, {"x": object()})
        assert res["success"] is False and "JSON" in res["error"]
        res = PushNotificationDispatcher.dispatch_to_webhook(hook, {}, {"X": "a\r\nInjected: 1"})
        assert res["success"] is False and "line breaks" in res["error"]
        res = PushNotificationDispatcher.dispatch_to_webhook(hook, {}, "not a dict")
        assert res["success"] is False
        res = PushNotificationDispatcher.dispatch_to_webhook(hook, {"big": "x" * 300_000})
        assert res["success"] is False and "too large" in res["error"]
        assert no_sockets.total == 0

    def test_slack_text_cannot_ping_the_channel_and_the_button_style_is_valid(self) -> None:
        blocks = PushNotificationDispatcher.format_slack_block_kit(
            "T", "<!channel> & <@U123>", "ci`x", "NORMAL", action_url="https://example.com/run/1"
        )["blocks"]
        text = blocks[1]["text"]["text"]
        assert "<!channel>" not in text and "&lt;!channel&gt;" in text and "&amp;" in text
        button = blocks[2]["elements"][0]
        assert "style" not in button  # "default" is not a Slack style and can reject the message
        urgent = PushNotificationDispatcher.format_slack_block_kit("T", "m", priority="urgent", action_url="http://x.io")
        assert urgent["blocks"][2]["elements"][0]["style"] == "primary"
        js = PushNotificationDispatcher.format_slack_block_kit("T", "m", action_url="javascript:alert(1)")
        assert len(js["blocks"]) == 2

    def test_discord_mentions_are_disabled_and_ntfy_headers_are_valid_for_any_language(self) -> None:
        embed = PushNotificationDispatcher.format_discord_embed("T", "@everyone hi")
        assert embed["allowed_mentions"] == {"parse": []}
        for title in ("مرحبا 🚀", "line1\r\nInjected: yes", "plain"):
            headers = PushNotificationDispatcher.format_ntfy_headers(title, "urgent", ["a,b", "c\nd"])
            for key, value in headers.items():
                value.encode("latin-1")  # would raise on the old output
                assert "\n" not in value and "\r" not in value, key
        assert PushNotificationDispatcher.format_ntfy_headers("مرحبا")["Title"].startswith("=?UTF-8?B?")


# ======================================================================================
# BackupRestoreVerifier
# ======================================================================================


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _tar(path: Path, members: dict[str, bytes], mode: str = "w:gz") -> Path:
    with tarfile.open(path, mode) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


class TestBackupRestoreVerifierPdf:
    def test_a_long_pdf_backup_is_hashed_and_read_in_full(self, tmp_path: Path) -> None:
        data = _long_pdf(45)
        pdf = tmp_path / "backup.pdf"
        pdf.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        assert BackupRestoreVerifier.compute_sha256(str(pdf))["sha256"] == digest
        assert BackupRestoreVerifier.verify_checksum(str(pdf), digest)["verified"] is True
        res = BackupRestoreVerifier.verify_backup(str(pdf), digest)
        assert res["recovery_ready"] is True
        assert res["document"]["pages"] == 45 and res["document"]["pages_read"] == 45
        assert res["document"]["complete"] is True
        assert "pdf_full_read" in res["checks_performed"] and "checksum" in res["checks_performed"]

    def test_restore_drill_of_a_pdf_copies_every_byte_and_reads_every_page(self, tmp_path: Path) -> None:
        data = _long_pdf(45)
        pdf = tmp_path / "backup.pdf"
        pdf.write_bytes(data)
        target = tmp_path / "restore"
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(pdf), str(target))
        assert res["restored"] is True and res["bytes_identical"] is True
        assert res["input"]["pages"] == 45 and res["input"]["pages_read"] == 45
        assert (target / "backup.pdf").read_bytes() == data
        auto = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(pdf))
        assert auto["restored"] is True and auto["temp_target_dir_removed"] is True
        assert not Path(auto["temp_target_dir"]).exists()

    def test_scanned_pages_are_named_and_unproven_bytes_are_not_ready(self, tmp_path: Path) -> None:
        data = _pdf([{"text": ["one"]}, {"image": True}, {"text": [MARKER]}])
        pdf = tmp_path / "mixed.pdf"
        pdf.write_bytes(data)
        res = BackupRestoreVerifier.verify_backup(str(pdf))
        assert res["document"]["unreadable_pages"] == [2] and res["warnings"]
        assert res["recovery_ready"] is False  # page 2 is unverified and no checksum proves the bytes
        proven = BackupRestoreVerifier.verify_backup(str(pdf), hashlib.sha256(data).hexdigest())
        assert proven["recovery_ready"] is True and proven["warnings"]

    def test_a_pdf_is_not_an_sql_dump_or_an_archive_and_says_it_was_read(self, tmp_path: Path) -> None:
        pdf = tmp_path / "dump.sql"  # even with an .sql name the bytes decide
        pdf.write_bytes(_long_pdf(45))
        sql = BackupRestoreVerifier.verify_sql_dump_integrity(str(pdf))
        assert sql["valid"] is False and "45 page(s) read in full" in sql["error"]
        assert sql["input"]["pages"] == 45
        arc = BackupRestoreVerifier.verify_archive_integrity(str(pdf))
        assert arc["valid_archive"] is False and "45 page(s)" in arc["error"]

    def test_corrupt_and_encrypted_pdfs_are_results(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf at all")
        assert BackupRestoreVerifier.verify_backup(str(bad))["recovery_ready"] is False
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(bad))
        assert res["restored"] is False


class TestBackupRestoreVerifier:
    def test_paths_and_checksums(self, tmp_path: Path) -> None:
        f = tmp_path / "a.bin"
        f.write_bytes(b"abc")
        digest = hashlib.sha256(b"abc").hexdigest()
        assert BackupRestoreVerifier.compute_sha256(str(tmp_path))["exists"] is False  # a directory
        assert BackupRestoreVerifier.compute_sha256(str(tmp_path / "missing"))["exists"] is False
        assert BackupRestoreVerifier.compute_sha256("a\x00b")["exists"] is False
        assert BackupRestoreVerifier.compute_sha256(None)["exists"] is False
        assert BackupRestoreVerifier.verify_checksum(str(f), None)["verified"] is False  # used to raise
        assert BackupRestoreVerifier.verify_checksum(str(f), "abc")["verified"] is False
        assert BackupRestoreVerifier.verify_checksum(str(f), "sha256:" + digest.upper())["verified"] is True
        out = tmp_path.parent / "outside.bin"
        out.write_bytes(b"x")
        res = BackupRestoreVerifier.compute_sha256(str(out), base_dir=str(tmp_path))
        assert res["exists"] is False and "outside" in res["error"]
        res = BackupRestoreVerifier.compute_sha256(str(tmp_path / ".." / "outside.bin"), base_dir=str(tmp_path))
        assert res["exists"] is False

    def test_a_file_that_merely_exists_is_not_recovery_ready(self, tmp_path: Path) -> None:
        junk = tmp_path / "db.bak"
        junk.write_bytes(b"\x00\x01junk")
        assert BackupRestoreVerifier.verify_backup(str(junk))["recovery_ready"] is False  # used to be True
        empty = tmp_path / "empty.bak"
        empty.write_bytes(b"")
        assert BackupRestoreVerifier.verify_backup(str(empty), hashlib.sha256(b"").hexdigest())["recovery_ready"] is False
        good = BackupRestoreVerifier.verify_backup(str(junk), hashlib.sha256(b"\x00\x01junk").hexdigest())
        assert good["recovery_ready"] is True and good["checks_performed"] == ["checksum"]
        wrong = BackupRestoreVerifier.verify_backup(str(junk), "0" * 64)
        assert wrong["recovery_ready"] is False and wrong["reasons"]
        assert BackupRestoreVerifier.verify_backup(str(tmp_path))["recovery_ready"] is False

    def test_archive_integrity(self, tmp_path: Path) -> None:
        z = _zip(tmp_path / "ok.zip", {"a.txt": b"hello", "d/b.txt": b"world"})
        res = BackupRestoreVerifier.verify_archive_integrity(str(z))
        assert res["valid_archive"] is True and res["entries_count"] == 2 and res["safe_to_extract"] is True
        t = _tar(tmp_path / "ok.tar.gz", {"a.txt": b"hello"})
        assert BackupRestoreVerifier.verify_archive_integrity(str(t))["valid_archive"] is True
        # a truncated gzip stream is caught because every member is actually read
        cut = tmp_path / "cut.tar.gz"
        cut.write_bytes(t.read_bytes()[: len(t.read_bytes()) // 2])
        assert BackupRestoreVerifier.verify_archive_integrity(str(cut))["valid_archive"] is False
        # a corrupt member inside an otherwise well-formed zip
        raw = bytearray(z.read_bytes())
        raw[raw.index(b"hello")] ^= 0xFF
        broken = tmp_path / "broken.zip"
        broken.write_bytes(bytes(raw))
        assert BackupRestoreVerifier.verify_archive_integrity(str(broken))["valid_archive"] is False
        empty = _zip(tmp_path / "empty.zip", {})
        res = BackupRestoreVerifier.verify_archive_integrity(str(empty))
        assert res["valid_archive"] is False and "no entries" in res["error"]  # used to be valid
        import gzip
        gz = tmp_path / "dump.sql.gz"
        gz.write_bytes(gzip.compress(b"CREATE TABLE t(a);"))
        assert BackupRestoreVerifier.verify_archive_integrity(str(gz))["format"] == "gzip"
        junk = tmp_path / "x.zip"
        junk.write_bytes(b"nope")
        assert BackupRestoreVerifier.verify_archive_integrity(str(junk))["valid_archive"] is False
        bomb = BackupRestoreVerifier.verify_archive_integrity(str(z), max_uncompressed_bytes=3)
        assert bomb["valid_archive"] is False and "refusing" in bomb["error"]

    def test_unsafe_archives_are_flagged_and_not_recovery_ready(self, tmp_path: Path) -> None:
        z = _zip(tmp_path / "evil.zip", {"../escape.txt": b"x", "ok.txt": b"y"})
        res = BackupRestoreVerifier.verify_archive_integrity(str(z))
        assert res["valid_archive"] is True and res["safe_to_extract"] is False and res["unsafe_count"] == 1
        assert BackupRestoreVerifier.verify_backup(str(z))["recovery_ready"] is False

    def test_zip_slip_cannot_write_outside_the_target(self, tmp_path: Path) -> None:
        z = _zip(tmp_path / "evil.zip", {"../escape.txt": b"x", "/abs-escape.txt": b"x", "ok.txt": b"y"})
        target = tmp_path / "t" / "restore"
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(z), str(target))
        assert res["restored"] is False and res["skipped_count"] == 2
        assert not (tmp_path / "t" / "escape.txt").exists() and not Path("/abs-escape.txt").exists()
        assert (target / "ok.txt").read_bytes() == b"y"

    def test_a_tar_symlink_cannot_be_used_to_write_through(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        tar_path = tmp_path / "evil.tar"
        with tarfile.open(tar_path, "w") as tf:
            link = tarfile.TarInfo("link")
            link.type = tarfile.SYMTYPE
            link.linkname = str(outside)
            tf.addfile(link)
            data = b"pwned"
            payload = tarfile.TarInfo("link/pwn.txt")
            payload.size = len(data)
            tf.addfile(payload, io.BytesIO(data))
            fine = tarfile.TarInfo("fine.txt")
            fine.size = 2
            tf.addfile(fine, io.BytesIO(b"ok"))
        target = tmp_path / "restore"
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(tar_path), str(target))
        assert res["restored"] is False and res["skipped_count"] >= 1
        assert list(outside.iterdir()) == []
        assert not (target / "link").is_symlink()  # the link itself was never recreated

    def test_the_drill_never_restores_over_existing_files(self, tmp_path: Path) -> None:
        z = _zip(tmp_path / "b.zip", {"keep.txt": b"NEW"})
        target = tmp_path / "live"
        target.mkdir()
        (target / "keep.txt").write_text("PRECIOUS")
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(z), str(target))
        assert res["restored"] is False and "not empty" in res["error"]
        assert (target / "keep.txt").read_text() == "PRECIOUS"
        file_target = tmp_path / "afile"
        file_target.write_text("x")
        assert BackupRestoreVerifier.simulate_restore_to_temp_dir(str(z), str(file_target))["restored"] is False
        confined = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(z), str(tmp_path / "e"), base_dir=str(tmp_path / "zzz"))
        assert confined["restored"] is False

    def test_names_containing_two_dots_are_restored_and_an_unsupported_format_is_not_success(self, tmp_path: Path) -> None:
        z = _zip(tmp_path / "b.zip", {"report..final.txt": b"ok", "empty/": b""})
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(z))  # used to skip it silently
        assert res["restored"] is True and res["recovered_items_count"] == 2
        junk = tmp_path / "x.bin"
        junk.write_bytes(b"whatever")
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(junk))
        assert res["restored"] is False and "Unsupported" in res["error"]  # used to be True with 0 items
        only_dirs = _zip(tmp_path / "d.zip", {"d/": b""})
        assert BackupRestoreVerifier.simulate_restore_to_temp_dir(str(only_dirs))["restored"] is False

    def test_sql_replay_cannot_write_files_or_run_forever(self, tmp_path: Path) -> None:
        good = tmp_path / "good.sql"
        good.write_text("BEGIN TRANSACTION;\nCREATE TABLE t(a INTEGER);\nINSERT INTO t VALUES (1);\nCOMMIT;\n")
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(good))
        assert res["restored"] is True and res["engine"] == "sqlite" and res["tables_created"] == 1
        victim = tmp_path / "written-by-attach.db"
        attach = tmp_path / "attach.sql"
        attach.write_text(f"ATTACH DATABASE '{victim}' AS x;\nCREATE TABLE x.t(a);\n")
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(attach))
        assert res["restored"] is False and not victim.exists()
        vacuum = tmp_path / "vacuum.sql"
        target_file = tmp_path / "vacuumed.db"
        vacuum.write_text(f"CREATE TABLE t(a);\nVACUUM INTO '{target_file}';\n")
        assert BackupRestoreVerifier.simulate_restore_to_temp_dir(str(vacuum))["restored"] is False
        assert not target_file.exists()
        loop = tmp_path / "loop.sql"
        loop.write_text("WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM r) SELECT count(*) FROM r;\n")
        started = time.monotonic()
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(loop), timeout_seconds=1)
        assert res["restored"] is False and "did not finish" in res["error"]
        assert time.monotonic() - started < 10
        pg = tmp_path / "pg.sql"
        pg.write_text("SET statement_timeout = 0;\nCREATE TABLE public.t (id integer);\n")
        res = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(pg))
        assert res["restored"] is False and "PostgreSQL" in res["error"]

    def test_sql_dump_validation(self, tmp_path: Path) -> None:
        ok = tmp_path / "ok.sql"
        ok.write_text("-- dump\nCREATE TABLE users (id int);\nINSERT INTO users VALUES (1);\nCOMMIT;\n-- Dump completed\n")
        res = BackupRestoreVerifier.verify_sql_dump_integrity(str(ok))
        assert res["valid"] is True and res["tables_found"] == ["users"] and res["has_transaction_commit"] is True
        cut = tmp_path / "cut.sql"
        cut.write_text("CREATE TABLE users (id int);\nINSERT INTO users VALUES (1),\n(2")
        res = BackupRestoreVerifier.verify_sql_dump_integrity(str(cut))
        assert res["valid"] is False and res["truncation_suspected"] is True  # used to be valid
        assert BackupRestoreVerifier.verify_sql_dump_integrity(str(_empty(tmp_path)))["valid"] is False
        binary = tmp_path / "custom.dump"
        binary.write_bytes(b"PGDMP\x00\x01\x02binary")
        assert "binary" in BackupRestoreVerifier.verify_sql_dump_integrity(str(binary))["error"]
        nothing = tmp_path / "comments.sql"
        nothing.write_text("-- only a comment\n")
        assert BackupRestoreVerifier.verify_sql_dump_integrity(str(nothing))["valid"] is False

    def test_a_large_dump_is_streamed_not_loaded(self, tmp_path: Path) -> None:
        big = tmp_path / "big.sql"
        with big.open("w") as handle:
            handle.write("CREATE TABLE t (a int);\n")
            for _ in range(200_000):
                handle.write("INSERT INTO t VALUES (1);\n")
        res = BackupRestoreVerifier.verify_sql_dump_integrity(str(big))
        assert res["valid"] is True and res["size_bytes"] > 5_000_000

    def test_the_report_does_not_claim_rto_for_an_integrity_check(self, tmp_path: Path) -> None:
        f = tmp_path / "a.bin"
        f.write_bytes(b"abc")
        integrity = BackupRestoreVerifier.verify_backup(str(f), hashlib.sha256(b"abc").hexdigest())
        report = BackupRestoreVerifier.generate_restore_drill_report(integrity)
        assert "RTO VERIFIED" not in report and "NO RESTORE PERFORMED" in report
        z = _zip(tmp_path / "b.zip", {"a": b"1"})
        drill = BackupRestoreVerifier.simulate_restore_to_temp_dir(str(z))
        assert "RTO VERIFIED" in BackupRestoreVerifier.generate_restore_drill_report(drill)
        failed = BackupRestoreVerifier.generate_restore_drill_report({"restored": False, "error": "x```y"})
        assert "FAILED" in failed and failed.count("```") == 2  # the fence cannot be broken out of
        assert "FAILED" in BackupRestoreVerifier.generate_restore_drill_report(None)
        assert "FAILED" in BackupRestoreVerifier.generate_restore_drill_report({"recovery_ready": "False"})


def _empty(tmp_path: Path) -> Path:
    p = tmp_path / "empty.sql"
    p.write_bytes(b"")
    return p


# ======================================================================================
# DependencyManagerValidator
# ======================================================================================


class TestDependencyManagerValidator:
    def test_a_long_requirements_pdf_is_read_to_its_last_page(self) -> None:
        spec = [{"text": [f"package{i}==1.{i}"]} for i in range(1, 45)]
        spec.append({"text": ["zzlastpkg"]})  # unpinned, on page 45
        data = _pdf(spec)
        res = DependencyManagerValidator.check_version_pinning(data)
        assert res["input"]["pages"] == 45 and res["input"]["complete"] is True
        assert res["exact_pinned_count"] == 44
        assert [p["name"] for p in res["unpinned_packages"]] == ["zzlastpkg"]
        assert res["is_valid_lock"] is False
        assert [p["name"] for p in DependencyManagerValidator.parse_requirements_text(data)["packages"]][-1] == "zzlastpkg"
        full = DependencyManagerValidator.validate_requirements_full(data)
        assert full["valid"] is False and full["input"]["pages"] == 45

    def test_a_pdf_with_unread_pages_is_never_a_valid_lock(self) -> None:
        data = _pdf([{"text": ["flask==2.0.0"]}, {"image": True}])
        res = DependencyManagerValidator.check_version_pinning(data)
        assert res["is_valid_lock"] is False and res["warnings"]
        assert DependencyManagerValidator.validate_requirements_full(data)["valid"] is False
        assert DependencyManagerValidator.validate_requirements_full(data)["input"]["unreadable_pages"] == [2]

    def test_a_package_json_in_a_pdf_is_read_and_a_binary_is_refused(self) -> None:
        data = _pdf([{"text": ['{"name": "x", "dependencies": {"left-pad": "*"}}']}])
        res = DependencyManagerValidator.validate_package_json(data)
        assert res["valid"] is False and res["input"]["pages"] == 1
        binary = DependencyManagerValidator.parse_requirements_text(b"\x00\x01\x02\x03")
        assert "binary" in binary["error"]

    def test_false_all_clears_are_gone(self) -> None:
        check = DependencyManagerValidator.check_version_pinning
        assert check("")["is_valid_lock"] is False  # nothing verified
        assert check("# only a comment\n")["is_valid_lock"] is False
        res = check("requests==\nflask==2.0\n")
        assert res["is_valid_lock"] is False and res["invalid_lines"][0]["reason"] == "operator without a version"
        assert check("flask==2.*\n")["ranged_packages"]  # a prefix match is not an exact pin
        assert check("flask==2.0\n")["is_valid_lock"] is True
        assert DependencyManagerValidator.detect_duplicate_or_conflicting_specs(None)["has_conflicts"] is None

    def test_option_lines_are_directives_not_packages(self) -> None:
        text = "-i https://pypi.example/simple\n--hash=sha256:abc\n-r base.txt\nflask==2.0 \\\n    --hash=sha256:def\n" \
               "https://x.test/pkg.whl\n./local\n"
        parsed = DependencyManagerValidator.parse_requirements_text(text)
        assert [p["name"] for p in parsed["packages"]] == ["flask"]
        assert len(parsed["vcs_references"]) == 2 and not parsed["invalid_lines"]
        res = DependencyManagerValidator.check_version_pinning("flask==2.0\n-r base.txt\n")
        assert res["includes_unexamined"] == ["-r base.txt"] and res["warnings"]

    def test_wrong_types_are_results(self) -> None:
        for bad in (None, 5, ["a"], {}):
            assert "error" in DependencyManagerValidator.parse_requirements_text(bad)
            assert DependencyManagerValidator.check_version_pinning(bad)["is_valid_lock"] is False
            assert DependencyManagerValidator.validate_package_json(bad)["valid"] is False
            assert DependencyManagerValidator.validate_requirements_full(bad)["valid"] is False
        assert "JSON object" in DependencyManagerValidator.validate_package_json("[]")["error"]
        assert DependencyManagerValidator.validate_package_json("{")["valid"] is False
        assert DependencyManagerValidator.validate_package_json("[" * 100000)["valid"] is False
        ok = DependencyManagerValidator.validate_package_json('{"name":"x","dependencies":{"a":"1.0.0"}}')
        assert ok["valid"] is True and ok["dependencies_checked"] == 1
        ranges = DependencyManagerValidator.validate_package_json(
            '{"optionalDependencies":{"a":"1 || 2","b":"1.0.0 - 2.0.0"}}')
        assert len(ranges["ranged"]) == 2
        arabic = DependencyManagerValidator.check_version_pinning("مكتبة==1.0\nflask==2.0\n")
        assert len(arabic["invalid_lines"]) == 1

    def test_lockfile_presence(self, tmp_path: Path) -> None:
        assert DependencyManagerValidator.check_lockfile_presence(None)["exists"] is False
        assert DependencyManagerValidator.check_lockfile_presence(str(tmp_path / "missing"))["exists"] is False
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(_long_pdf(45))
        res = DependencyManagerValidator.check_lockfile_presence(str(pdf))
        assert res["exists"] is False and "PDF" in res["error"] and res["input"]["pages"] == 45
        empty = DependencyManagerValidator.check_lockfile_presence(str(tmp_path))
        assert empty["exists"] is True and any("No dependency manifest" in w for w in empty["warnings"])
        (tmp_path / "requirements.txt").write_text("flask==2.0\n")
        (tmp_path / "requirements.lock").write_text("")
        res = DependencyManagerValidator.check_lockfile_presence(str(tmp_path))
        assert any("empty" in w for w in res["warnings"])
        res = DependencyManagerValidator.check_lockfile_presence(str(tmp_path), base_dir=str(tmp_path / "other"))
        assert res["exists"] is False and "outside" in res["error"]

    def test_pip_check_runs_bounded_and_is_a_result(self) -> None:
        res = DependencyManagerValidator.run_pip_check(timeout_seconds=60)
        assert "available" in res and ("healthy" in res)


# ======================================================================================
# PerformanceProfiler
# ======================================================================================


class TestPerformanceProfiler:
    def test_executions_are_reported_and_single_run_runs_once(self) -> None:
        calls: list[int] = []
        two = PerformanceProfiler.profile_callable(lambda: calls.append(1))
        assert two["executions"] == 2 and len(calls) == 2
        calls.clear()
        one = PerformanceProfiler.profile_callable(lambda: calls.append(1), single_run=True)
        assert one["executions"] == 1 and len(calls) == 1 and one["memory_measured"] is True
        calls.clear()
        none = PerformanceProfiler.profile_callable(lambda: calls.append(1), measure_memory=False)
        assert none["executions"] == 1 and none["memory_measured"] is False and len(calls) == 1
        assert not tracemalloc.is_tracing()

    def test_a_failing_second_run_is_reported_not_swallowed(self) -> None:
        state = {"n": 0}

        def flaky() -> int:
            state["n"] += 1
            if state["n"] > 1:
                raise RuntimeError("second run broke")
            return 1

        res = PerformanceProfiler.profile_callable(flaky)
        assert res["success"] is True and res["memory_measured"] is False
        assert "second run broke" in res["memory_error"]
        assert not tracemalloc.is_tracing()

    def test_a_coroutine_works_from_inside_a_running_event_loop(self) -> None:
        async def work(x: int) -> int:
            await asyncio.sleep(0.01)
            return x * 2

        async def agent_turn() -> dict:
            return PerformanceProfiler.profile_callable(work, 21)  # used to fail: "loop is already running"

        res = asyncio.run(agent_turn())
        assert res["success"] is True and res["result"] == 42, res
        assert PerformanceProfiler.benchmark_callable(work, 1, iterations=2)["success"] is True

    def test_bad_arguments_are_results(self) -> None:
        assert PerformanceProfiler.benchmark_callable(lambda: 1, iterations=None)["iterations"] == 5
        assert PerformanceProfiler.benchmark_callable(lambda: 1, iterations="x")["success"] is True
        assert PerformanceProfiler.benchmark_callable(lambda: 1, iterations=10**9)["iterations"] == 10_000
        assert PerformanceProfiler.benchmark_callable(None)["success"] is False
        for budget in (None, math.nan, -1, "5", True):
            res = PerformanceProfiler.check_latency_budget(lambda: 1, max_allowed_ms=budget)
            assert res["within_budget"] is False and res["success"] is False
        assert PerformanceProfiler.check_latency_budget(lambda: 1, max_allowed_ms=1000)["within_budget"] is True
        slow = PerformanceProfiler.check_latency_budget(lambda: time.sleep(0.05), max_allowed_ms=1)
        assert slow["within_budget"] is False and slow["success"] is True
        top = PerformanceProfiler.profile_cprofile_top_functions(lambda: sum(range(1000)), top_n=None)
        assert top["success"] is True and top["top_n"] == 10
        boom = PerformanceProfiler.profile_cprofile_top_functions(lambda: 1 / 0)
        assert boom["success"] is False and "ZeroDivisionError" in boom["error"]


# ======================================================================================
# TestCoverageAndMutationAnalyzer
# ======================================================================================

_PROJECT = {
    "mod.py": "def flag():\n    a = True\n    b = False\n    c = True\n    return a and not b and c\n",
    "test_mod.py": "from mod import flag\n\ndef test_flag():\n    assert flag() is True\n",
}


def _project(tmp_path: Path, files: dict | None = None) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    for name, text in (files or _PROJECT).items():
        (root / name).write_text(text)
    return root


class TestCoverageAnalyzer:
    def test_impossible_coverage_numbers_never_pass(self) -> None:
        calc = CoverageAnalyzer.calculate_coverage_ratio
        assert calc(10, 5)["passed_threshold"] is False and calc(10, 5)["valid"] is False  # was 200% "passed"
        for args in ((None, 5), (-1, 5), (math.nan, 5), (1, math.inf), ("1", 5)):
            assert calc(*args)["passed_threshold"] is False
        assert calc(0, 0)["passed_threshold"] is False
        assert calc(9, 10)["coverage_percentage"] == 90.0 and calc(9, 10)["passed_threshold"] is True
        assert calc(9, 10, threshold=math.nan)["valid"] is False

    def test_a_pdf_is_not_a_coverage_report_and_says_it_was_read(self, tmp_path: Path) -> None:
        pdf = tmp_path / "coverage.json"  # the bytes decide, not the name
        pdf.write_bytes(_long_pdf(45))
        res = CoverageAnalyzer.parse_coverage_json(str(pdf))
        assert res["loaded"] is False and "45 page(s), read in full" in res["error"]
        assert res["input"]["pages"] == 45

    def test_coverage_json_edge_cases(self, tmp_path: Path) -> None:
        parse = CoverageAnalyzer.parse_coverage_json
        assert parse(str(tmp_path))["loaded"] is False  # a directory used to raise
        assert parse(None)["loaded"] is False
        assert parse(str(tmp_path / "missing.json"))["loaded"] is False
        for text in ("[]", "{}", "not json", '{"totals": 5}', "\x00\x01"):
            f = tmp_path / "c.json"
            f.write_bytes(text.encode())
            assert parse(str(f))["loaded"] is False, text
        f = tmp_path / "good.json"
        f.write_text(json.dumps({"totals": {"percent_covered": 87.456, "covered_lines": 5, "num_statements": 6},
                                 "files": {"a.py": {"summary": {"percent_covered": None}, "missing_lines": [3]},
                                           "b.py": 7}}))
        res = parse(str(f))
        assert res["loaded"] is True and res["percent_covered"] == 87.46
        assert res["files"][0]["percent_covered"] == 0.0 and len(res["files"]) == 1
        assert parse(str(f), base_dir=str(tmp_path / "x"))["loaded"] is False

    def test_pytest_arguments_cannot_become_options_and_a_missing_dir_is_named(self, tmp_path: Path) -> None:
        run = CoverageAnalyzer.run_pytest_with_coverage
        for bad in ("-p", "--co", "-c evil.ini", "", None, "a\x00b"):
            res = run(bad, working_dir=str(tmp_path))
            assert res["passed"] is False and "test_target" in res["error"], bad
        assert "source_package" in run("t.py", source_package="--x", working_dir=str(tmp_path))["error"]
        res = run("t.py", working_dir=str(tmp_path / "nope"))
        assert "not found" in res["error"] and "coverage CLI" not in res["error"]  # used to blame coverage
        res = run("t.py", working_dir=str(tmp_path), base_dir=str(tmp_path / "elsewhere"))
        assert res["passed"] is False and "outside" in res["error"]

    def test_threshold_zero_does_not_pass_a_run_that_executed_nothing(self, tmp_path: Path) -> None:
        res = CoverageAnalyzer.run_pytest_with_coverage(
            "does_not_exist.py", working_dir=str(tmp_path), threshold=0, timeout_seconds=60
        )
        assert res["available"] is True and res["tests_passed"] is False
        assert res["passed_threshold"] is False and res["passed"] is False  # used to be True

    def test_real_coverage_run(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        res = CoverageAnalyzer.run_pytest_with_coverage(
            "test_mod.py", source_package="mod", working_dir=str(root), threshold=50, timeout_seconds=60
        )
        assert res["tests_passed"] is True and res["report"]["loaded"] is True, res
        assert res["passed"] is True and res["coverage_percentage"] >= 50

    def test_mutation_refuses_pdf_directory_garbage_and_escapes(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        evaluate = CoverageAnalyzer.evaluate_mutations_against_tests
        (root / "doc.py").write_bytes(_long_pdf(45))
        res = evaluate("doc.py", str(root))
        assert res["evaluated"] is False and "PDF (45 page(s), read in full)" in res["error"]
        assert evaluate(".", str(root))["evaluated"] is False  # a directory used to raise
        (root / "sub").mkdir()
        assert evaluate("sub", str(root))["evaluated"] is False
        (root / "bin.py").write_bytes(b"\x00\x01\x02")
        assert evaluate("bin.py", str(root))["evaluated"] is False  # was a ValueError-prone parse
        (root / "bad.py").write_text("def (:\n")
        assert "not valid Python" in evaluate("bad.py", str(root))["error"]
        assert "escapes" in evaluate("../x.py", str(root))["error"]
        assert "escapes" in evaluate("/etc/passwd", str(root))["error"]
        assert evaluate(None, str(root))["evaluated"] is False
        assert evaluate("mod.py", None)["evaluated"] is False
        assert evaluate("mod.py", str(root), base_dir=str(tmp_path / "other"))["evaluated"] is False
        link = root / "link.py"
        outside = tmp_path / "outside.py"
        outside.write_text("X = True\n")
        link.symlink_to(outside)
        assert "escapes" in evaluate("link.py", str(root))["error"]

    def test_mutation_is_sampled_honestly_and_never_touches_the_original(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        before = {p.name: p.read_text() for p in root.iterdir()}
        res = CoverageAnalyzer.evaluate_mutations_against_tests(
            "mod.py", str(root), timeout_seconds=30, max_mutants=2
        )
        assert res["evaluated"] is True and res["mutants_total"] == 2 and res["mutants_candidates"] == 3
        assert res["mutants_not_tested"] == 1 and res["sampled"] is True
        assert res["mutants_killed"] >= 1
        assert {p.name: p.read_text() for p in root.iterdir() if p.is_file()} == before

    def test_a_hanging_baseline_is_killed_with_its_children(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "child.pid"
        root = _project(tmp_path, {
            "mod.py": "FLAG = True\n",
            "test_hang.py": (
                "import subprocess, sys, time\n"
                "def test_hang():\n"
                "    c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                f"    open({str(pid_file)!r}, 'w').write(str(c.pid))\n"
                "    time.sleep(120)\n"
            ),
        })
        res = CoverageAnalyzer.evaluate_mutations_against_tests("mod.py", str(root), timeout_seconds=3)
        assert res["evaluated"] is False and "timed out" in res["error"]
        assert pid_file.exists()
        child = int(pid_file.read_text())
        assert _wait_for(lambda: not _alive(child)), "the test's own child outlived the timeout"

    def test_a_huge_working_dir_is_refused_instead_of_copied(self, tmp_path: Path, monkeypatch) -> None:
        root = _project(tmp_path)
        for i in range(6):
            (root / f"extra{i}.txt").write_text("x")
        monkeypatch.setattr(cov_module, "_MAX_COPY_FILES", 4)
        res = CoverageAnalyzer.evaluate_mutations_against_tests("mod.py", str(root))
        assert res["evaluated"] is False and "too large to copy" in res["error"]

    def test_mutation_generation_survives_hostile_source(self) -> None:
        gen = CoverageAnalyzer.generate_boolean_mutations
        assert gen(None) == [] and gen(5) == [] and gen("a\x00b") == [] and gen("x = (" * 5000) == []
        assert gen(b"\x00\x01") == []
        assert gen(_long_pdf(45)) == []
        assert len(gen("x = True\ny = False  # مرحبا 🚀\n".encode())) == 2
        assert len(gen("x = 'مرحبا'; y = True\n")) == 1


# ======================================================================================
# AgentToolTestHarness
# ======================================================================================


class TestAgentToolTestHarness:
    def test_an_empty_suite_is_not_a_pass(self) -> None:
        for cases in ([], None, "nope"):
            summary = AgentToolTestHarness.run_suite(cases)
            assert summary["all_passed"] is False and summary["total_cases"] == 0
        assert "NO TESTS RUN" in AgentToolTestHarness.generate_tool_test_report(AgentToolTestHarness.run_suite([]))
        assert AgentToolTestHarness.run_suite([{"callable": lambda: 1}])["all_passed"] is True
        assert AgentToolTestHarness.run_suite(["not a dict"])["all_passed"] is False

    def test_the_timeout_is_real_for_sync_and_async_tools(self) -> None:
        release = threading.Event()

        def hangs() -> int:
            release.wait(30)
            return 1

        started = time.monotonic()
        res = AgentToolTestHarness.run_tool_case("hang", hangs, timeout_seconds=0.3)
        assert time.monotonic() - started < 5
        assert res["passed"] is False and res["stage"] == "timeout" and res["still_running"] is True
        release.set()

        async def forever() -> int:
            await asyncio.sleep(60)
            return 1

        res = AgentToolTestHarness.run_tool_case("async-hang", forever, timeout_seconds=0.3)
        assert res["stage"] == "timeout" and res["passed"] is False and res["still_running"] is False

    def test_tools_that_raise_baseexception_or_return_junk_do_not_escape(self) -> None:
        def exits() -> None:
            raise SystemExit(3)

        res = AgentToolTestHarness.run_tool_case("exit", exits)
        assert res["passed"] is False and "SystemExit" in res["error"]

        async def cancelled() -> None:
            raise asyncio.CancelledError()

        assert AgentToolTestHarness.run_tool_case("cancel", cancelled)["passed"] is False
        res = AgentToolTestHarness.run_tool_case("junk", lambda: object())
        assert res["passed"] is True and isinstance(res["output_preview"], str)
        json.dumps(res)  # JSON-safe
        big = AgentToolTestHarness.run_tool_case("big", lambda: {"k": "x" * 100_000})
        assert len(json.dumps(big["output_preview"])) < 5000
        assert AgentToolTestHarness.run_tool_case("a", lambda **k: 1, payload=[1])["passed"] is False
        assert AgentToolTestHarness.run_tool_case("a", lambda **k: 1, payload={1: 2})["passed"] is False
        assert AgentToolTestHarness.run_tool_case("a", None)["passed"] is False
        bad_type = AgentToolTestHarness.run_tool_case("a", lambda: 1, expected_return_type="int")
        assert bad_type["passed"] is False

    def test_a_coroutine_is_actually_run_even_inside_a_running_loop(self) -> None:
        async def tool(x: int) -> int:
            await asyncio.sleep(0.01)
            return x + 1

        async def inside() -> dict:
            return AgentToolTestHarness.run_tool_case("t", tool, {"x": 1})

        res = asyncio.run(inside())
        assert res["passed"] is True and res["output_preview"] == 2

    def test_fuzzing_nothing_is_not_robustness(self) -> None:
        empty = AgentToolTestHarness.run_fuzz_tests(lambda **k: 1, {})
        assert empty["resilient"] is False and "nothing to fuzz" in empty["error"]
        assert AgentToolTestHarness.run_fuzz_tests(lambda **k: 1, {"a": 1}, iterations=0)["resilient"] is False
        assert AgentToolTestHarness.run_fuzz_tests(None, {"a": 1})["resilient"] is False
        robust = AgentToolTestHarness.run_fuzz_tests(lambda a: {"error": "bad"} if a is None else {"ok": 1}, {"a": 1}, 20)
        assert robust["resilient"] is True and robust["total_fuzz_trials"] == 20

        def crashes(a) -> None:
            raise RuntimeError("boom")

        assert AgentToolTestHarness.run_fuzz_tests(crashes, {"a": 1}, 5)["resilient"] is False
        assert AgentToolTestHarness.run_fuzz_tests(lambda a: 1, {"a": 1}, iterations="x")["total_fuzz_trials"] == 10

    def test_report_and_schema_validation_cannot_be_broken_by_content(self) -> None:
        summary = AgentToolTestHarness.run_suite([
            {"tool_name": "evil|name\nsecond", "callable": lambda: (_ for _ in ()).throw(ValueError("a|b\nc"))}
        ])
        report = AgentToolTestHarness.generate_tool_test_report(summary)
        rows = [line for line in report.splitlines() if line.startswith("| `")]
        assert len(rows) == 1 and rows[0].count("|") == 6 + 2  # 5 columns + the two escaped pipes
        assert "FAILED" in AgentToolTestHarness.generate_tool_test_report(None)
        assert AgentToolTestHarness.validate_tool_schema(None)["valid"] is False
        assert AgentToolTestHarness.validate_tool_schema([])["valid"] is False
        good = {"name": "t", "description": "does a useful thing", "parameters": {"type": "object"}}
        assert AgentToolTestHarness.validate_tool_schema(good)["valid"] is True
        assert AgentToolTestHarness.validate_payload_keys([], ["a"])["valid"] is False


# ======================================================================================
# The shared guards
# ======================================================================================


class TestOpsCommon:
    def test_url_policy(self) -> None:
        for bad in ("http://127.0.0.1/", "http://[::1]/", "http://10.0.0.1/", "http://192.168.1.1/",
                    "http://169.254.169.254/", "http://0.0.0.0/", "http://[::ffff:127.0.0.1]/",
                    "http://metadata.google.internal/", "http://100.64.0.1/", "http://a b/", "http://x\r\ny/"):
            with pytest.raises(_ops_common.UrlRefused):
                _ops_common.check_url(bad)
        _ops_common.check_url("https://93.184.216.34/x")  # a public IP literal passes, with no DNS lookup

    def test_redact_url_hides_the_secret_path(self) -> None:
        shown = _ops_common.redact_url("https://hooks.slack.com/services/T0/B0/SECRET?token=1")
        assert shown == "https://hooks.slack.com/..." and "SECRET" not in shown

    def test_run_group_kills_the_whole_group_on_timeout(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "pid"
        script = (
            "import subprocess,sys,time;"
            "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
            f"open({str(pid_file)!r},'w').write(str(c.pid));time.sleep(120)"
        )
        res = _ops_common.run_group([PY, "-c", script], cwd=None, timeout_s=2, env=None)
        assert res.timed_out is True and res.returncode is None
        child = int(pid_file.read_text())
        assert _wait_for(lambda: not _alive(child))

    def test_run_group_bounds_output(self) -> None:
        res = _ops_common.run_group([PY, "-c", "print('x'*2_000_000 + 'END')"], cwd=None, timeout_s=30, env=None,
                                    max_output=1000)
        assert res.stdout.rstrip().endswith("END") and len(res.stdout) < 2000 and "omitted" in res.stdout


# ======================================================================================
# The tools can never reach the AURA gateway (or anything internal)
# ======================================================================================

#: Every one of these must be refused from the URL text alone: no DNS query, no connection.
_FORBIDDEN_URLS = [
    # loopback, private, link-local, CGNAT, unique-local, metadata, unspecified
    "http://127.0.0.1/", "http://127.0.0.1:8000/", "http://127.1.2.3/x", "http://localhost/", "http://localhost:8000/",
    "http://LOCALHOST./", "http://[::1]/", "http://[::1]:8000/", "http://[::ffff:127.0.0.1]/",
    "http://10.0.0.5/", "http://172.16.0.1/", "http://172.31.255.254/", "http://192.168.0.1/",
    "http://169.254.169.254/latest/meta-data/", "http://[fe80::1]/", "http://100.64.0.1/", "http://100.127.255.1/",
    "http://[fd00::1]/", "http://[fc00::1]/", "http://[fd00:ec2::254]/", "http://0.0.0.0/", "http://[::]/",
    "http://224.0.0.1/", "http://240.0.0.1/",
    # internal names: docker/compose services, Railway private network, intranet suffixes
    "http://gateway/", "http://gateway:8000/", "http://nginx/", "http://nginx:8080/", "http://postgres/",
    "http://redis:6379/", "http://ollama:11434/", "http://qdrant/", "http://aura-gateway/",
    "http://svc.internal/", "http://gateway.railway.internal:8000/", "http://metadata.google.internal/",
    "http://printer.local/", "http://app.localhost/", "http://a.b.localhost:3000/", "http://host.lan/",
    "http://db.svc.cluster.local/", "http://2130706433/", "http://0x7f.0.0.1/", "http://127.1/",
    # the platform's own hosts, subdomains included
    "https://aureon-ai.tech/", "https://aureon-ai.tech/api/v3/chat", "https://console.aureon-ai.tech/",
    "https://api.aureon-ai.tech/", "https://deep.sub.aureon-ai.tech/x", "https://AUREON-AI.TECH./",
    # the gateway's API and health paths on ANY host, however spelled
    "https://example.com/api/v3/chat", "https://example.com/api/v3", "https://example.com/health",
    "https://example.com/health/ready", "https://example.com/healthz", "https://example.com/x/../api/v3/chat",
    "https://example.com/%61pi/v3/chat", "https://example.com/%2561pi/%2576%2533/chat",
    "https://example.com//API//v3/x", "https://example.com/api;x=1/v3/chat", "https://example.com/prefix/api/v3/chat",
    "https://example.com/%48ealth",
    # not http(s) / malformed
    "file:///etc/passwd", "ftp://example.com/", "gopher://example.com/", "javascript:alert(1)", "data:text/plain,x",
    "ws://example.com/", "//example.com/", "example.com", "", "http://", "http://user:pw@example.com/",
    "http://exa mple.com/", "http://example.com/\r\nX: y", "http://example.com:99999/",
]


def _entry_points(tmp_path: Path):
    """Every network-capable public method in the group, as ``(name, callable(url))``."""
    shot = str(tmp_path / "shot.png")
    return [
        ("browser.check_url_accessibility", lambda u: BrowserSubagent.check_url_accessibility(u)),
        ("browser.run_actions", lambda u: BrowserSubagent.run_actions(u)),
        ("browser.navigate_and_screenshot", lambda u: BrowserSubagent.navigate_and_screenshot(u, shot)),
        ("browser.extract_page_content", lambda u: BrowserSubagent.extract_page_content(u)),
        ("browser.run_script_in_page", lambda u: BrowserSubagent.run_script_in_page(u, "1")),
        ("deploy.check_readiness_probes", lambda u: DeploymentReleaseManager.check_readiness_probes([u])),
        ("push.dispatch", lambda u: PushNotificationDispatcher.dispatch("t", "m", webhook_url=u)),
        ("push.dispatch_to_webhook", lambda u: PushNotificationDispatcher.dispatch_to_webhook(u, {"a": 1})),
        ("dev.check_http_health", lambda u: DevServerManager.check_http_health(u)),
    ]


def _refused(result: dict) -> bool:
    """The entry point reported failure and did not report success under any of the success keys."""
    success_keys = ("accessible", "executed", "success", "all_healthy", "dispatched", "healthy")
    present = [k for k in success_keys if k in result]
    return bool(present) and all(result[k] is False for k in present)


class TestNoToolCanReachTheGatewayOrAnythingInternal:
    def test_every_forbidden_url_is_refused_by_check_url_with_no_dns_and_no_connection(self, no_sockets) -> None:
        for url in _FORBIDDEN_URLS:
            with pytest.raises(_ops_common.UrlRefused):
                _ops_common.check_url(url)
        assert no_sockets.total == 0, no_sockets.calls

    def test_every_network_entry_point_refuses_every_forbidden_url_before_any_socket(
        self, tmp_path: Path, no_sockets
    ) -> None:
        points = _entry_points(tmp_path)
        for name, call in points:
            for url in _FORBIDDEN_URLS:
                result = call(url)
                assert _refused(result), (name, url, result)
        assert no_sockets.total == 0, f"a refused URL caused network use: {no_sockets.calls[:5]}"
        assert not (tmp_path / "shot.png").exists()

    @pytest.mark.parametrize("env_name", ["AURA_PUBLIC_URL", "AURA_PUBLIC_BASE_URL", "AURA_BASE_URL",
                                          "PUBLIC_BASE_URL", "RAILWAY_PUBLIC_DOMAIN", "RAILWAY_PRIVATE_DOMAIN"])
    def test_the_platform_hosts_named_in_the_environment_are_refused(self, env_name, monkeypatch, tmp_path,
                                                                     no_sockets) -> None:
        value = "my-gateway.up.example-cloud.dev" if env_name.startswith("RAILWAY") \
            else "https://my-gateway.up.example-cloud.dev:8443/base"
        monkeypatch.setenv(env_name, value)
        for url in ("https://my-gateway.up.example-cloud.dev/", "https://x.my-gateway.up.example-cloud.dev/a",
                    "http://MY-GATEWAY.up.example-cloud.dev:9/"):
            with pytest.raises(_ops_common.UrlRefused, match="platform"):
                _ops_common.check_url(url)
            for name, call in _entry_points(tmp_path):
                assert _refused(call(url)), (name, url)
        assert no_sockets.total == 0

    def test_a_comma_separated_environment_value_is_honoured(self, monkeypatch, no_sockets) -> None:
        monkeypatch.setenv("AURA_BASE_URL", "https://a.example-one.dev, https://b.example-two.dev/x")
        for url in ("https://a.example-one.dev/", "https://b.example-two.dev/"):
            with pytest.raises(_ops_common.UrlRefused):
                _ops_common.check_url(url)
        assert no_sockets.total == 0

    def test_a_public_ip_literal_is_allowed_without_any_lookup(self, no_sockets) -> None:
        for url in ("https://93.184.216.34/x", "http://8.8.8.8:8080/a?b=1", "https://[2606:4700:4700::1111]/"):
            assert _ops_common.check_url(url) == url
        assert no_sockets.total == 0

    def test_a_public_looking_name_that_resolves_to_an_internal_address_is_refused(self, monkeypatch) -> None:
        import socket as socket_module

        answers = {
            "sneaky.example.org": [(socket_module.AF_INET, 1, 6, "", ("10.0.0.7", 443))],
            "loop.example.org": [(socket_module.AF_INET, 1, 6, "", ("127.0.0.1", 443))],
            "meta.example.org": [(socket_module.AF_INET, 1, 6, "", ("169.254.169.254", 443))],
            "mixed.example.org": [(socket_module.AF_INET, 1, 6, "", ("93.184.216.34", 443)),
                                  (socket_module.AF_INET6, 1, 6, "", ("::1", 443, 0, 0))],
            "mapped.example.org": [(socket_module.AF_INET6, 1, 6, "", ("::ffff:10.0.0.1", 443, 0, 0))],
            "fine.example.org": [(socket_module.AF_INET, 1, 6, "", ("93.184.216.34", 443))],
        }
        monkeypatch.setattr(socket_module, "getaddrinfo",
                            lambda host, *a, **k: answers.get(host) or (_ for _ in ()).throw(socket_module.gaierror()))
        for host in ("sneaky", "loop", "meta", "mixed", "mapped", "nonexistent"):
            with pytest.raises(_ops_common.UrlRefused):
                _ops_common.check_url(f"https://{host}.example.org/")
        assert _ops_common.check_url("https://fine.example.org/x") == "https://fine.example.org/x"

    def test_every_redirect_hop_is_checked_again(self) -> None:
        import urllib.error
        import urllib.request

        handler = _ops_common._GuardedRedirect(_ops_common.check_url)
        req = urllib.request.Request("https://93.184.216.34/start")
        for target in ("http://127.0.0.1:8000/", "http://gateway:8000/api/v3/chat", "https://aureon-ai.tech/",
                       "http://169.254.169.254/", "/api/v3/chat", "/health", "file:///etc/passwd",
                       "https://console.aureon-ai.tech/", "http://printer.local/"):
            with pytest.raises(urllib.error.HTTPError, match="redirect refused"):
                handler.redirect_request(req, None, 302, "Found", {}, target)
        assert handler.redirect_request(req, None, 302, "Found", {}, "https://93.184.216.35/ok") is not None
        # the no-follow variant (webhooks, owned-port health probes) refuses even a harmless target
        strict = _ops_common._GuardedRedirect(None)
        with pytest.raises(urllib.error.HTTPError, match="not followed"):
            strict.redirect_request(req, None, 302, "Found", {}, "https://93.184.216.35/ok")

    def test_dev_server_never_connects_to_a_caller_supplied_host_or_port(self, no_sockets) -> None:
        owned = DevServerManager.find_free_port(18900)
        before = no_sockets.total
        for url in (f"http://10.0.0.1:{owned}/", f"http://gateway:{owned}/", f"http://127.0.0.1:{owned + 1}/",
                    "http://127.0.0.1:8000/", f"https://127.0.0.1:{owned}/", f"http://[::1]:{owned}/",
                    f"http://127.0.0.2:{owned}/", f"http://localhost.evil.com:{owned}/"):
            assert DevServerManager.check_http_health(url)["healthy"] is False, url
        for host, port in (("127.0.0.1", owned + 1), ("10.0.0.1", owned), ("gateway", owned), ("::1", owned),
                           ("127.0.0.2", owned), ("aureon-ai.tech", 443)):
            assert DevServerManager.wait_for_port_ready(host, port, 0.1) is False, (host, port)
        assert no_sockets.total == before, no_sockets.calls[before:]

    def test_the_signatures_have_no_opt_out_switches(self) -> None:
        import inspect

        for cls in (BrowserSubagent, DeploymentReleaseManager, PushNotificationDispatcher, DevServerManager):
            for name, member in inspect.getmembers(cls, callable):
                if name.startswith("_"):
                    continue
                params = inspect.signature(member).parameters
                assert not [p for p in params if p.startswith(("allow_", "inherit_", "unsafe", "insecure"))], (cls, name)

    def test_no_tool_in_the_group_imports_the_platform_core(self) -> None:
        import ast

        forbidden = ("api", "container", "adapters", "config", "auth", "kernel")
        files = ["_ops_common", "background_task_manager", "dev_server_manager", "browser_subagent",
                 "deployment_release_manager", "remote_session_controller", "push_notification_dispatcher",
                 "backup_restore_verifier", "dependency_manager_validator", "performance_profiler",
                 "test_coverage_and_mutation_analyzer", "agent_tool_test_harness"]
        folder = Path(_ops_common.__file__).parent
        for stem in files:
            tree = ast.parse((folder / f"{stem}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    # inside aura_core/tools/open: level 1 is this package, 2 is tools, 3 is aura_core
                    names = [("aura_core." + base) if node.level >= 3 else
                             ("aura_core.tools." + base) if node.level == 2 else
                             ("aura_core.tools.open." + base) if node.level == 1 else base]
                    names += [f"{names[0]}.{a.name}" for a in node.names]
                for name in names:
                    parts = name.split(".")
                    assert "aura_service" not in name, (stem, name)
                    assert not (parts[0] == "aura_core" and len(parts) > 1 and parts[1] in forbidden), (stem, name)
                    assert not (parts[0] == "aura_core" and len(parts) > 2 and parts[1] == "tools"
                                and parts[2] in forbidden), (stem, name)

    def test_children_never_inherit_gateway_credentials_and_cannot_be_handed_them(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("AURA_JWT_SECRET", "jwt-secret-value")
        monkeypatch.setenv("AURA_PROVIDER_HOSTED1_API_KEY", "sk-hosted-value")
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw@db/x")
        code = "import os;print(sorted(k for k in os.environ if 'AURA' in k or 'KEY' in k or 'DATABASE' in k))"
        mgr = BackgroundTaskManager()
        mgr.start_task("t", [PY, "-c", code])
        assert mgr.wait_for_task("t", 20)["stdout"] == "[]"
        mgr.terminate_all()
        res = _ops_common.run_group([PY, "-c", code], cwd=None, timeout_s=20)  # env=None means scrubbed
        assert res.stdout.strip() == "[]"
        for bad in ({"AURA_JWT_SECRET": "x"}, {"OPENAI_API_KEY": "x"}, {"GH_TOKEN": "x"}, {"DATABASE_URL": "x"},
                    {"db_password": "x"}, {"AURA_BASE_URL": "x"}):
            started = mgr.start_task("e", [PY, "-c", "1"], env=bad)
            assert started["started"] is False and "credentials" in started["error"], bad
            assert DevServerManager().start_server("python3 -m http.server 0", cwd=str(tmp_path), env=bad)["started"] is False
        assert mgr.list_tasks() == []
        assert _ops_common.forbidden_env_names({"FOO": "1", "AURA_X": "1", "MY_TOKEN": "1"}) == ["AURA_X", "MY_TOKEN"]
        # the child environments used by the process helpers
        assert set(_ops_common.scrubbed_env()) <= {"PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "TEMP",
                                                   "TMP", "TMPDIR", "LANG", "LC_ALL", "PYTHONIOENCODING",
                                                   "PYTHONDONTWRITEBYTECODE"}


class TestTimeBudgets:
    def test_defaults_are_generous_and_bounded(self) -> None:
        import inspect

        assert _ops_common.DEFAULT_READ_BUDGET_S == 300.0 and _ops_common.MAX_BUDGET_S == 600.0
        assert _ops_common.clamp_budget(None) == 300.0 and _ops_common.clamp_budget(9999) == 600.0
        assert _ops_common.clamp_budget(-1) == 300.0 and _ops_common.clamp_budget(math.nan) == 300.0
        assert _ops_common.clamp_budget(45) == 45.0
        for method in (BackupRestoreVerifier.compute_sha256, BackupRestoreVerifier.verify_checksum,
                       BackupRestoreVerifier.verify_archive_integrity, BackupRestoreVerifier.verify_sql_dump_integrity,
                       BackupRestoreVerifier.simulate_restore_to_temp_dir, BackupRestoreVerifier.verify_backup):
            assert inspect.signature(method).parameters["timeout_seconds"].default == 300.0, method
        for method in (DependencyManagerValidator.parse_requirements_text, DependencyManagerValidator.check_version_pinning,
                       DependencyManagerValidator.validate_package_json, DependencyManagerValidator.validate_requirements_full,
                       CoverageAnalyzer.parse_coverage_json):
            assert inspect.signature(method).parameters["read_timeout_seconds"].default == 300.0, method
        assert inspect.signature(CoverageAnalyzer.evaluate_mutations_against_tests).parameters["timeout_seconds"].default >= 120
        assert inspect.signature(DeploymentReleaseManager.build_docker_image).parameters["timeout_seconds"].default == 300

    def test_a_budget_overrun_is_an_error_never_a_partial_verdict(self, tmp_path: Path) -> None:
        big = tmp_path / "big.sql"
        with big.open("w") as handle:
            handle.write("CREATE TABLE t (a int);\n")
            for _ in range(300_000):
                handle.write("INSERT INTO t VALUES (1);\n")
        res = BackupRestoreVerifier.verify_sql_dump_integrity(str(big), timeout_seconds=0.001)
        assert res["valid"] is False and "time budget" in res["error"]
        assert BackupRestoreVerifier.verify_sql_dump_integrity(str(big))["valid"] is True  # default budget is ample
        data = tmp_path / "data.bin"
        data.write_bytes(os.urandom(5_000_000))
        res = BackupRestoreVerifier.compute_sha256(str(data), timeout_seconds=0.000001)
        assert "sha256" not in res and "time budget" in res["error"]
        z = _zip(tmp_path / "z.zip", {"a.bin": os.urandom(3_000_000)})
        res = BackupRestoreVerifier.verify_archive_integrity(str(z), timeout_seconds=0.000001)
        assert res["valid_archive"] is False and "time budget" in res["error"]
        assert BackupRestoreVerifier.verify_archive_integrity(str(z))["valid_archive"] is True

    def test_a_long_pdf_is_read_completely_with_the_default_budget_and_fails_loudly_without_one(self) -> None:
        data = _long_pdf(45)
        res = DependencyManagerValidator.check_version_pinning(data)  # default 300 s: all 45 pages
        assert res["input"]["pages"] == 45 and res["input"]["pages_read"] == 45
        starved = DependencyManagerValidator.check_version_pinning(data, read_timeout_seconds=0.000001)
        assert "time budget" in starved["error"] and starved["is_valid_lock"] is False
        assert "input" not in starved  # no half-read document is reported on

    def test_run_with_budget_returns_values_and_reraises_errors(self) -> None:
        assert _ops_common.run_with_budget(lambda: 7, 5) == 7
        with pytest.raises(ValueError, match="boom"):
            _ops_common.run_with_budget(lambda: (_ for _ in ()).throw(ValueError("boom")), 5)
        release = threading.Event()
        with pytest.raises(_ops_common.BudgetExceeded):
            _ops_common.run_with_budget(lambda: release.wait(30), 0.2, "Waiting")
        release.set()
