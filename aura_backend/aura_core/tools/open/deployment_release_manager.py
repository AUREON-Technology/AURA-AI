import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


class DeploymentReleaseManager:
    """Release and deployment manager for Docker/compose workflows, readiness probing, rollback planning, and risk calculation."""

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

    @classmethod
    def generate_rollback_plan(
        cls,
        service_name: str,
        current_version: str,
        previous_version: str,
        deployment_type: str = "blue_green",
    ) -> Dict[str, Any]:
        """Generate automated rollback instructions and commands based on deployment strategy."""
        steps: List[str] = []

        if deployment_type == "blue_green":
            steps = [
                f"1. Switch traffic routing back to {service_name}:{previous_version} (idle slot).",
                f"2. Run health probes on {service_name}:{previous_version}.",
                f"3. Drain connections from failed {service_name}:{current_version} deployment.",
                "4. Mark rollback complete and alert operations team.",
            ]
        elif deployment_type == "canary":
            steps = [
                "1. Reduce canary traffic weight from current percentage to 0%.",
                f"2. Terminate canary pods running {service_name}:{current_version}.",
                f"3. Confirm 100% traffic flows to stable {service_name}:{previous_version}.",
            ]
        else:  # rolling
            steps = [
                f"1. Issue rolling update back to image {service_name}:{previous_version}.",
                "2. Await pod readiness probes across all replicas.",
                "3. Verify error rates normalize in metrics.",
            ]

        return {
            "service": service_name,
            "strategy": deployment_type,
            "rollback_target": previous_version,
            "rollback_steps": steps,
        }

    @staticmethod
    def check_readiness_probes(
        endpoints: List[str],
        timeout_seconds: float = 3.0,
    ) -> Dict[str, Any]:
        """Verify that deployed microservice readiness probes respond with 200 OK."""
        results: List[Dict[str, Any]] = []
        all_ready = True

        for url in endpoints:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "AURA-Readiness-Probe/1.0"})
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    code = resp.getcode()
                    ready = (200 <= code < 300)
                    if not ready:
                        all_ready = False
                    results.append({"endpoint": url, "ready": ready, "status_code": code})
            except Exception as exc:
                all_ready = False
                results.append({"endpoint": url, "ready": False, "error": str(exc)})

        return {
            "all_healthy": all_ready,
            "total_endpoints": len(endpoints),
            "healthy_count": sum(1 for r in results if r.get("ready")),
            "probe_results": results,
        }

    @staticmethod
    def calculate_deployment_risk(
        changed_files_count: int,
        has_db_migrations: bool,
        rollback_supported: bool,
        test_coverage_pct: float,
    ) -> Dict[str, Any]:
        """Compute an objective risk rating for a prospective production deployment."""
        risk_score = 0
        risk_factors: List[str] = []

        if changed_files_count > 30:
            risk_score += 3
            risk_factors.append(f"Large changeset ({changed_files_count} files modified)")
        elif changed_files_count > 10:
            risk_score += 1

        if has_db_migrations:
            risk_score += 3
            risk_factors.append("Includes schema database migrations (potential breaking change)")

        if not rollback_supported:
            risk_score += 4
            risk_factors.append("No automated rollback target specified")

        if test_coverage_pct < 60.0:
            risk_score += 3
            risk_factors.append(f"Test coverage low ({test_coverage_pct}%)")
        elif test_coverage_pct < 80.0:
            risk_score += 1

        if risk_score >= 7:
            level = "CRITICAL"
        elif risk_score >= 4:
            level = "HIGH"
        elif risk_score >= 2:
            level = "MEDIUM"
        else:
            level = "LOW"

        return {
            "risk_level": level,
            "risk_score": risk_score,
            "can_deploy_automatically": level in ("LOW", "MEDIUM"),
            "risk_factors": risk_factors,
        }
