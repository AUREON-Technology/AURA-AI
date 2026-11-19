import subprocess
from typing import Any, Dict, List, Optional


class DeploymentReleaseManager:
    """Release and deployment helper for Docker/compose workflows and health verification."""

    @staticmethod
    def build_docker_image(
        image_tag: str,
        dockerfile_path: str = "Dockerfile",
        context_dir: str = ".",
        timeout_seconds: int = 180,
    ) -> Dict[str, Any]:
        """Build a Docker image using the local Docker CLI."""
        cmd = ["docker", "build", "-t", image_tag, "-f", dockerfile_path, context_dir]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return {
                "success": proc.returncode == 0,
                "image_tag": image_tag,
                "exit_code": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
        except FileNotFoundError:
            return {"success": False, "image_tag": image_tag, "error": "docker CLI not found."}
        except subprocess.TimeoutExpired:
            return {"success": False, "image_tag": image_tag, "error": "docker build timed out."}

    @staticmethod
    def compose_action(
        action: str,
        compose_file: str = "docker-compose.yml",
        services: Optional[List[str]] = None,
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        """Run a safe docker compose lifecycle action (up, down, ps, config)."""
        allowed_actions = {"up", "down", "ps", "config", "pull"}
        if action not in allowed_actions:
            return {
                "success": False,
                "error": f"Unsupported action '{action}'. Allowed: {sorted(allowed_actions)}",
            }

        cmd = ["docker", "compose", "-f", compose_file, action]
        if action == "up":
            cmd.append("-d")
        if services:
            cmd.extend(services)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return {
                "success": proc.returncode == 0,
                "action": action,
                "exit_code": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
        except FileNotFoundError:
            return {"success": False, "action": action, "error": "docker compose not found."}
        except subprocess.TimeoutExpired:
            return {"success": False, "action": action, "error": f"docker compose {action} timed out."}

    @staticmethod
    def plan_rollout(
        service_name: str,
        target_version: str,
        previous_version: Optional[str] = None,
        health_checks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Produce a structured deployment and rollback plan payload for an agent or CI runner."""
        return {
            "service": service_name,
            "target_version": target_version,
            "previous_version": previous_version,
            "health_checks": health_checks or ["/health"],
            "rollback_supported": previous_version is not None,
        }