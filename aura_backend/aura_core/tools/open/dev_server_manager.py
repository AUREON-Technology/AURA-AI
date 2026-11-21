"""Deferred (ADR-036) -- deliberately NOT registered as a tool. Long-running processes, a
process-global PID table and ``shell=True`` cannot be made safe in the gateway. The home for this
is a ``dev_server.*`` RPC in ``aura_hostd`` (not built; needs its own change and Host Daemon CI).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import shlex
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Allowlist of permitted executable names for start_server.
# Only these binaries may be launched; arbitrary shell strings are rejected.
# Add entries here as new dev-server runtimes are approved.
# ---------------------------------------------------------------------------
_ALLOWED_EXECUTABLES: frozenset = frozenset({
    # Python / ASGI / WSGI runners
    "uvicorn",
    "gunicorn",
    "python",
    "python3",
    "python3.11",
    "python3.12",
    "python3.13",
    # Node / JS runtimes
    "node",
    "npm",
    "npx",
    "pnpm",
    "yarn",
    # Vite / Next / common front-end dev servers (called through node)
    "vite",
    "next",
    # Other common runners
    "flask",
    "fastapi",
    "daphne",
    "hypercorn",
    "granian",
    # Django management
    "django-admin",
    "manage.py",
})


def _parse_command(command: str) -> Optional[List[str]]:
    """Split a command string safely (no shell) and validate its executable.

    Returns the token list on success, or None if the executable is not in the
    allowlist.  The function never spawns a shell, so shell metacharacters in
    the remaining arguments are inert.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    if not tokens:
        return None

    # Strip any leading path components so "python3" matches "/usr/bin/python3"
    import os
    executable = os.path.basename(tokens[0])
    if executable not in _ALLOWED_EXECUTABLES:
        return None

    return tokens


class DevServerManager:
    """Manager for starting, finding free ports, health-checking, restarting, and stopping local development servers.

    Security fixes (v2):
    - start_server no longer uses shell=True; the command is split with shlex
      and the executable is checked against _ALLOWED_EXECUTABLES before Popen.
    - Processes are stored in an instance-level dict (not a class-level shared
      one) so isolated callers cannot interfere with each other's processes.
    - wait_seconds is applied with a non-blocking poll loop instead of a bare
      time.sleep that would block the entire caller thread for the full duration.
    - cwd defaults to the temp directory when not supplied, constraining the
      server's working directory.
    """

    def __init__(self) -> None:
        # Instance-level registry – avoids cross-caller state leakage.
        self._processes: Dict[int, subprocess.Popen] = {}

    # Keep a backward-compatible class-level fallback for callers that use the
    # class directly (e.g. DevServerManager.start_server(...)).
    _class_processes: Dict[int, subprocess.Popen] = {}

    def _registry(self) -> Dict[int, subprocess.Popen]:
        return self._processes

    @classmethod
    def _class_registry(cls) -> Dict[int, subprocess.Popen]:
        return cls._class_processes

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def start_server(
        self,
        command: str,
        cwd: Optional[str] = None,
        wait_seconds: float = 1.0,
    ) -> Dict[str, Any]:
        """Start a development server process in the background and track its PID.

        ``command`` must begin with an executable in the _ALLOWED_EXECUTABLES
        allowlist.  Shell metacharacters are not interpreted – the string is
        split with shlex and passed directly to the OS.

        ``wait_seconds`` is consumed as a non-blocking poll loop so the calling
        thread is not fully suspended for the whole duration.
        """
        tokens = _parse_command(command)
        if tokens is None:
            return {
                "started": False,
                "error": (
                    f"Command rejected: executable not in allowlist or command is malformed. "
                    f"Permitted executables: {sorted(_ALLOWED_EXECUTABLES)}"
                ),
                "command": command,
            }

        import tempfile
        effective_cwd = cwd or tempfile.gettempdir()

        try:
            proc = subprocess.Popen(
                tokens,
                shell=False,          # never shell=True
                cwd=effective_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as exc:
            return {
                "started": False,
                "error": f"{type(exc).__name__}: {exc}",
                "command": command,
            }

        # Non-blocking wait: poll in short increments rather than sleeping all
        # at once, so that a process that exits immediately is noticed quickly.
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.05)

        poll = proc.poll()
        if poll is not None:
            stdout_out, stderr_err = proc.communicate(timeout=2)
            return {
                "started": False,
                "pid": proc.pid,
                "exit_code": poll,
                "stdout": (stdout_out or "").strip(),
                "stderr": (stderr_err or "").strip(),
            }

        self._processes[proc.pid] = proc
        return {
            "started": True,
            "pid": proc.pid,
            "command": command,
            "cwd": effective_cwd,
        }

    def check_process_status(self, pid: int) -> Dict[str, Any]:
        """Check whether a tracked server process is still running."""
        proc = self._processes.get(pid)
        if proc is None:
            return {"pid": pid, "tracked": False, "running": False}

        code = proc.poll()
        return {
            "pid": pid,
            "tracked": True,
            "running": code is None,
            "exit_code": code,
        }

    def stop_server(self, pid: int, timeout_seconds: float = 5.0) -> Dict[str, Any]:
        """Terminate a tracked server process cleanly, escalating to kill if needed."""
        proc = self._processes.get(pid)
        if proc is None:
            return {"stopped": False, "pid": pid, "error": "PID not found in tracked processes."}

        if proc.poll() is not None:
            self._processes.pop(pid, None)
            return {"stopped": True, "pid": pid, "already_exited": True, "exit_code": proc.returncode}

        proc.terminate()
        try:
            code = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait(timeout=2)

        self._processes.pop(pid, None)
        return {"stopped": True, "pid": pid, "exit_code": code}

    def restart_server(self, pid: int, command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
        """Gracefully stop existing process and boot replacement server."""
        stop_res = self.stop_server(pid)
        start_res = self.start_server(command, cwd=cwd)
        return {
            "previous_pid": pid,
            "stopped": stop_res.get("stopped", False),
            "new_server": start_res,
        }

    # ------------------------------------------------------------------
    # Network utilities (stateless – fine as staticmethods)
    # ------------------------------------------------------------------

    @staticmethod
    def check_http_health(url: str, timeout_seconds: float = 3.0) -> Dict[str, Any]:
        """Perform a lightweight HTTP GET health probe against a running server URL."""
        try:
            with urllib.request.urlopen(url, timeout=timeout_seconds) as resp:
                status_code = resp.getcode()
                return {
                    "healthy": 200 <= status_code < 400,
                    "url": url,
                    "status_code": status_code,
                }
        except urllib.error.HTTPError as exc:
            return {
                "healthy": False,
                "url": url,
                "status_code": exc.code,
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "healthy": False,
                "url": url,
                "status_code": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    @staticmethod
    def find_free_port(start_port: int = 8000, max_tries: int = 50) -> int:
        """Find an unallocated TCP port on localhost."""
        for port in range(start_port, start_port + max_tries):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        # Fallback OS ephemeral port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    @staticmethod
    def wait_for_port_ready(
        host: str = "127.0.0.1",
        port: int = 8000,
        timeout_seconds: float = 10.0,
    ) -> bool:
        """Poll until a network port is listening or timeout expires."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                res = s.connect_ex((host, port))
                if res == 0:
                    return True
            time.sleep(0.2)
        return False
