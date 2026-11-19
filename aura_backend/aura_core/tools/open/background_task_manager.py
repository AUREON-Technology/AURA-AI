import shlex
import subprocess
from typing import Any, Dict, List, Optional, Union


class BackgroundTaskManager:
    """Manager for starting, polling, and terminating background subprocess tasks safely."""

    def __init__(self) -> None:
        self._tasks: Dict[str, subprocess.Popen] = {}

    def start_task(
        self,
        task_id: str,
        command: Union[str, List[str]],
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start a background command without shell=True and register it under task_id."""
        if not task_id:
            return {"started": False, "error": "task_id is required."}

        args = shlex.split(command) if isinstance(command, str) else list(command)
        if not args:
            return {"started": False, "task_id": task_id, "error": "Command cannot be empty."}

        try:
            proc = subprocess.Popen(
                args,
                shell=False,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._tasks[task_id] = proc
            return {
                "started": True,
                "task_id": task_id,
                "pid": proc.pid,
                "command": args,
            }
        except Exception as exc:
            return {
                "started": False,
                "task_id": task_id,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def get_status(self, task_id: str) -> Dict[str, Any]:
        """Return current running state and exit code for a registered background task."""
        proc = self._tasks.get(task_id)
        if proc is None:
            return {"task_id": task_id, "found": False, "running": False, "status": "unknown"}

        code = proc.poll()
        return {
            "task_id": task_id,
            "found": True,
            "pid": proc.pid,
            "running": code is None,
            "exit_code": code,
            "status": "running" if code is None else "completed",
        }

    def terminate_task(self, task_id: str, timeout_seconds: float = 5.0) -> Dict[str, Any]:
        """Terminate a background task cleanly and clean up its pipes."""
        proc = self._tasks.get(task_id)
        if proc is None:
            return {"terminated": False, "task_id": task_id, "error": "Task ID not found."}

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

        stdout_text = ""
        stderr_text = ""
        try:
            out, err = proc.communicate(timeout=1)
            stdout_text = out or ""
            stderr_text = err or ""
        except Exception:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        self._tasks.pop(task_id, None)
        return {
            "terminated": True,
            "task_id": task_id,
            "exit_code": proc.returncode,
            "stdout": stdout_text.strip(),
            "stderr": stderr_text.strip(),
        }