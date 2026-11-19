import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class DevServerManager:
    """Manager for starting, health-checking, inspecting, and stopping local development servers."""

    _processes: Dict[int, subprocess.Popen] = {}

    @classmethod
    def start_server(
        cls,
        command: str,
        cwd: Optional[str] = None,
        wait_seconds: float = 1.0,
    ) -> Dict[str, Any]:
        """Start a development server process in the background and track its PID."""
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if wait_seconds > 0:
                time.sleep(wait_seconds)

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

            cls._processes[proc.pid] = proc
            return {
                "started": True,
                "pid": proc.pid,
                "command": command,
                "cwd": cwd,
            }
        except Exception as exc:
            return {
                "started": False,
                "error": f"{type(exc).__name__}: {exc}",
                "command": command,
            }

    @classmethod
    def check_process_status(cls, pid: int) -> Dict[str, Any]:
        """Check whether a tracked server process is still running."""
        proc = cls._processes.get(pid)
        if proc is None:
            return {"pid": pid, "tracked": False, "running": False}

        code = proc.poll()
        return {
            "pid": pid,
            "tracked": True,
            "running": code is None,
            "exit_code": code,
        }

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

    @classmethod
    def stop_server(cls, pid: int, timeout_seconds: float = 5.0) -> Dict[str, Any]:
        """Terminate a tracked server process cleanly, escalating to kill if needed."""
        proc = cls._processes.get(pid)
        if proc is None:
            return {"stopped": False, "pid": pid, "error": "PID not found in tracked processes."}

        if proc.poll() is not None:
            cls._processes.pop(pid, None)
            return {"stopped": True, "pid": pid, "already_exited": True, "exit_code": proc.returncode}

        proc.terminate()
        try:
            code = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait(timeout=2)

        cls._processes.pop(pid, None)
        return {"stopped": True, "pid": pid, "exit_code": code}