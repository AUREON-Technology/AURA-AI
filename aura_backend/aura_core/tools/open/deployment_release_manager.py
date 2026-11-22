import math
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._ops_common import (
    CLI_ENV_PASSTHROUGH,
    PathRefused,
    UrlRefused,
    confine,
    open_guarded,
    run_group,
)
from ._process import scrubbed_env

_IMAGE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_ROLLBACK_STRATEGIES = ("blue_green", "canary", "rolling")
_MAX_PROBES = 50
_OUTPUT_TAIL = 20_000


def _docker_env() -> Dict[str, str]:
    """Scrubbed environment plus the few variables the docker CLI needs to find its daemon."""
    return scrubbed_env({k: os.environ[k] for k in CLI_ENV_PASSTHROUGH if k in os.environ})


def _clamp_timeout(value: Any, default: int, maximum: int = 3600) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return float(default)
    if seconds != seconds or seconds <= 0:
        return float(default)
    return min(seconds, float(maximum))


def _run_docker(cmd: List[str], cwd: Path, timeout: float) -> Dict[str, Any]:
    """Run a docker CLI command in its own process group; every failure is a returned dict."""
    try:
        res = run_group(cmd, cwd=str(cwd), timeout_s=timeout, env=_docker_env(), max_output=_OUTPUT_TAIL)
    except FileNotFoundError:
        return {"error": "docker CLI not found."}
    except OSError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if res.timed_out:
        return {"timed_out": True, "stdout": res.stdout.strip(), "stderr": res.stderr.strip()}
    return {
        "exit_code": res.returncode,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }


class DeploymentReleaseManager:
    """Release and deployment manager for Docker/compose workflows, readiness probing, rollback planning, and risk calculation.

    Safety properties: no shell; every value that reaches the docker command line is validated (an
    image tag, service name or path that begins with ``-`` used to become an option -- a service named
    ``-v`` turned ``compose down`` into "delete my volumes"); paths are resolved to absolute paths and
    optionally confined to ``base_dir``; the child gets a scrubbed environment and its own process
    group; output is bounded; and ``docker compose config`` runs ``--quiet`` because the full
    rendering prints interpolated secrets. Probes only reach http/https on **public** hosts (no opt-out: loopback, private, internal
    names, the platform's own hosts and the gateway's ``/api/v3/`` and ``/health`` paths are refused). The planning methods only produce text: nothing
    they return has been executed.
    """

    @staticmethod
    def build_docker_image(
        image_tag: str,
        dockerfile_path: str = "Dockerfile",
        context_dir: str = ".",
        timeout_seconds: int = 300,
        *,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a Docker image using the local Docker CLI."""
        if not isinstance(image_tag, str) or not _IMAGE_TAG_RE.match(image_tag):
            return {"success": False, "image_tag": image_tag if isinstance(image_tag, str) else None,
                    "error": "image_tag must look like 'name' or 'name:tag' and cannot start with '-'."}
        try:
            context = confine(context_dir, base_dir, kind="dir")
            dockerfile = confine(dockerfile_path, base_dir, kind="file")
        except PathRefused as exc:
            return {"success": False, "image_tag": image_tag, "error": str(exc)}
        if context == Path(context.anchor) or context == Path.home().resolve():
            return {"success": False, "image_tag": image_tag,
                    "error": "Refusing to use the filesystem root or the home directory as a build context."}

        cmd = ["docker", "build", "-t", image_tag, "-f", str(dockerfile), str(context)]
        out = _run_docker(cmd, context, _clamp_timeout(timeout_seconds, 300, 600))
        if "error" in out:
            return {"success": False, "image_tag": image_tag, "error": out["error"]}
        if out.get("timed_out"):
            return {"success": False, "image_tag": image_tag, "error": "docker build timed out.",
                    "stdout": out["stdout"], "stderr": out["stderr"]}
        return {
            "success": out["exit_code"] == 0,
            "image_tag": image_tag,
            "exit_code": out["exit_code"],
            "stdout": out["stdout"],
            "stderr": out["stderr"],
        }

    @staticmethod
    def compose_action(
        action: str,
        compose_file: str = "docker-compose.yml",
        services: Optional[List[str]] = None,
        timeout_seconds: int = 300,
        *,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a safe docker compose lifecycle action (up, down, ps, config, pull).

        ``config`` only validates the file (``--quiet``); its full output prints interpolated
        secrets. ``down`` never removes volumes or images.
        """
        allowed_actions = {"up", "down", "ps", "config", "pull"}
        if not isinstance(action, str) or action not in allowed_actions:
            return {
                "success": False,
                "error": f"Unsupported action {action!r}. Allowed: {sorted(allowed_actions)}",
            }

        if services is not None:
            if not isinstance(services, (list, tuple)) or len(services) > 50 or not all(
                isinstance(s, str) and _SERVICE_RE.match(s) for s in services
            ):
                return {"success": False, "action": action,
                        "error": "services must be a list of service names (letters, digits, '_', '.', '-'; "
                                 "not starting with '-')."}
        try:
            compose = confine(compose_file, base_dir, kind="file")
        except PathRefused as exc:
            return {"success": False, "action": action, "error": str(exc)}

        cmd = ["docker", "compose", "-f", str(compose), action]
        if action == "up":
            cmd.append("-d")
        if action == "config":
            cmd.append("--quiet")
        if services:
            cmd.extend(services)

        out = _run_docker(cmd, compose.parent, _clamp_timeout(timeout_seconds, 300, 600))
        if "error" in out:
            return {"success": False, "action": action,
                    "error": out["error"].replace("docker CLI", "docker compose")}
        if out.get("timed_out"):
            return {"success": False, "action": action, "error": f"docker compose {action} timed out.",
                    "stdout": out["stdout"], "stderr": out["stderr"]}
        return {
            "success": out["exit_code"] == 0,
            "action": action,
            "exit_code": out["exit_code"],
            "stdout": out["stdout"],
            "stderr": out["stderr"],
        }

    @staticmethod
    def plan_rollout(
        service_name: str,
        target_version: str,
        previous_version: Optional[str] = None,
        health_checks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Produce a structured deployment and rollback plan payload for an agent or CI runner.

        Planning only: nothing is deployed. ``rollback_supported`` is True only when a non-blank
        ``previous_version`` is given.
        """
        problems = []
        if not isinstance(service_name, str) or not service_name.strip():
            problems.append("service_name is required")
        if not isinstance(target_version, str) or not target_version.strip():
            problems.append("target_version is required")
        if isinstance(health_checks, str):
            checks = [health_checks]
        elif isinstance(health_checks, (list, tuple)):
            checks = [str(h) for h in health_checks if str(h).strip()]
        else:
            checks = []
        has_previous = isinstance(previous_version, str) and bool(previous_version.strip())
        plan: Dict[str, Any] = {
            "service": service_name,
            "target_version": target_version,
            "previous_version": previous_version,
            "health_checks": checks or ["/health"],
            "rollback_supported": has_previous and not problems,
            "valid": not problems,
            "executed": False,
        }
        if problems:
            plan["error"] = "; ".join(problems) + "."
        return plan

    @classmethod
    def generate_rollback_plan(
        cls,
        service_name: str,
        current_version: str,
        previous_version: str,
        deployment_type: str = "blue_green",
    ) -> Dict[str, Any]:
        """Generate rollback instructions for a deployment strategy (blue_green, canary or rolling).

        Instructions only -- nothing is executed. An unknown strategy or a missing version is an
        error, not a silently different plan.
        """
        strategy = deployment_type.strip().lower() if isinstance(deployment_type, str) else ""
        problems = []
        for label, value in (("service_name", service_name), ("current_version", current_version),
                             ("previous_version", previous_version)):
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{label} is required")
        if strategy not in _ROLLBACK_STRATEGIES:
            problems.append(f"deployment_type must be one of {list(_ROLLBACK_STRATEGIES)}")
        if problems:
            return {
                "service": service_name,
                "strategy": strategy or deployment_type,
                "rollback_target": previous_version,
                "rollback_steps": [],
                "valid": False,
                "executed": False,
                "error": "; ".join(problems) + ".",
            }

        steps: List[str] = []
        if strategy == "blue_green":
            steps = [
                f"1. Switch traffic routing back to {service_name}:{previous_version} (idle slot).",
                f"2. Run health probes on {service_name}:{previous_version}.",
                f"3. Drain connections from failed {service_name}:{current_version} deployment.",
                "4. Mark rollback complete and alert operations team.",
            ]
        elif strategy == "canary":
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
            "strategy": strategy,
            "rollback_target": previous_version,
            "rollback_steps": steps,
            "valid": True,
            "executed": False,
        }

    @staticmethod
    def check_readiness_probes(
        endpoints: List[str],
        timeout_seconds: float = 3.0,
    ) -> Dict[str, Any]:
        """Verify that deployed microservice readiness probes respond with 2xx.

        ``all_healthy`` is True only when at least one endpoint was probed and every one answered.
        An empty list is an error, not a pass.
        """
        if isinstance(endpoints, str):
            endpoints = [endpoints]
        if not isinstance(endpoints, (list, tuple)) or not endpoints:
            return {"all_healthy": False, "total_endpoints": 0, "healthy_count": 0, "probe_results": [],
                    "error": "No endpoints were given, so nothing was checked."}
        if len(endpoints) > _MAX_PROBES:
            return {"all_healthy": False, "total_endpoints": len(endpoints), "healthy_count": 0,
                    "probe_results": [], "error": f"Too many endpoints (limit {_MAX_PROBES})."}

        results: List[Dict[str, Any]] = []
        all_ready = True

        for url in endpoints:
            if not isinstance(url, str):
                all_ready = False
                results.append({"endpoint": None, "ready": False, "error": "Endpoint must be a string URL."})
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "AURA-Readiness-Probe/1.0"})
                with open_guarded(req, timeout=timeout_seconds) as resp:
                    code = resp.getcode()
                    ready = isinstance(code, int) and (200 <= code < 300)
                    if not ready:
                        all_ready = False
                    results.append({"endpoint": url, "ready": ready, "status_code": code})
            except UrlRefused as exc:
                all_ready = False
                results.append({"endpoint": url, "ready": False, "error": f"URL refused: {exc}"})
            except urllib.error.HTTPError as exc:
                all_ready = False
                results.append({"endpoint": url, "ready": False, "status_code": exc.code, "error": str(exc)})
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
        """Compute an objective risk rating for a prospective production deployment.

        Bad input (None, NaN, a negative count, coverage outside 0-100, a non-boolean flag) is never
        scored: NaN coverage used to compare False against both thresholds and rate a deployment LOW
        and auto-deployable. It now returns ``risk_level: "UNKNOWN"`` and
        ``can_deploy_automatically: False``.
        """
        problems = []
        count = changed_files_count
        if isinstance(count, float) and count.is_integer():
            count = int(count)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            problems.append("changed_files_count must be a non-negative integer")
        if not isinstance(has_db_migrations, bool):
            problems.append("has_db_migrations must be true or false")
        if not isinstance(rollback_supported, bool):
            problems.append("rollback_supported must be true or false")
        cov = test_coverage_pct
        if isinstance(cov, bool) or not isinstance(cov, (int, float)) or not math.isfinite(cov) or not (0 <= cov <= 100):
            problems.append("test_coverage_pct must be a number from 0 to 100")
        if problems:
            return {
                "risk_level": "UNKNOWN",
                "risk_score": None,
                "can_deploy_automatically": False,
                "risk_factors": [],
                "valid": False,
                "error": "; ".join(problems) + ".",
            }

        changed_files_count = count
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
            "valid": True,
        }
