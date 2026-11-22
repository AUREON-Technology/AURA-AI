"""Railway, through its public GraphQL API and a project token (ADR-037, stage 2).

``RailwayPlatform`` implements ``RailwayApi``. Every call sends the stored project token as
``Project-Access-Token``; the token is read from the vault on each call (``token_source``), never
kept on the instance, and never logged. The project and environment come from the token itself
(``projectToken``), so nothing else has to be configured: a project token reaches exactly one
environment of one project, which is the least privilege Railway offers.

The operations were checked against the live schema's introspection on 2026-11-21:

* ``variableCollectionUpsert`` (``skipDeploys: true``) and ``variableDelete`` change variables
  without starting a deploy; the engine then starts exactly one, so it knows the deployment id to
  watch.
* ``deploymentRedeploy(id, usePreviousImageTag: true)`` returns the new ``Deployment``: the same
  image, the current variables, no rebuild (a rebuild would outlast the five-minute watch).
* ``deploymentRollback(id)`` returns only a boolean, so the new deployment is found by listing.

``InMemoryRailway`` is the same contract with real state, for the default test suite and a local
gateway: services, variables (with ``${{...}}`` references kept unrendered), deployments with
statuses the test moves, and logs.

Vendor messages are never passed through: they can echo the request, and so a variable value.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError, NotFoundError, ValidationError
from ..ports.control_plane import (
    DeploymentHealth,
    DeploymentSummary,
    LogLine,
    RailwayScope,
    ServiceSummary,
)

__all__ = ["RAILWAY_GRAPHQL_URL", "InMemoryRailway", "RailwayPlatform", "deployment_health_of", "railway_scope"]

RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"
_TIMEOUT_S = 20.0

#: Railway's status words, as the reconciler reads them. SUCCESS means the health check passed
#: when the service has one; that is why the engine refuses gateway changes without one.
_STATUS_HEALTH: Mapping[str, DeploymentHealth] = {
    "SUCCESS": DeploymentHealth.HEALTHY,
    "SLEEPING": DeploymentHealth.HEALTHY,
    "FAILED": DeploymentHealth.FAILED,
    "CRASHED": DeploymentHealth.FAILED,
    "REMOVED": DeploymentHealth.SUPERSEDED,
    "REMOVING": DeploymentHealth.SUPERSEDED,
    "SKIPPED": DeploymentHealth.SUPERSEDED,
}


def deployment_health_of(status: str | None) -> DeploymentHealth:
    """BUILDING, DEPLOYING, INITIALIZING, QUEUED, WAITING, NEEDS_APPROVAL: still pending."""
    return _STATUS_HEALTH.get((status or "").upper(), DeploymentHealth.PENDING)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _graphql(
    token: str, query: str, variables: Mapping[str, Any] | None = None, *, transport: Any = None,
) -> Mapping[str, Any]:
    """One request. Raises ``ValidationError`` when Railway refuses the token, and
    ``DependencyUnavailableError`` (no vendor text) for anything else."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=transport) as client:
            response = await client.post(
                RAILWAY_GRAPHQL_URL,
                json={"query": query, "variables": dict(variables or {})},
                headers={"Project-Access-Token": token, "content-type": "application/json"},
            )
    except Exception as exc:
        raise DependencyUnavailableError(
            "Railway could not be reached", dependency="railway", details={"error": type(exc).__name__},
        ) from exc
    if response.status_code in (401, 403):
        raise ValidationError("Railway refused the project token", details={"reason": "railway_token_refused"})
    try:
        body = response.json()
    except ValueError:
        body = None
    errors = (body or {}).get("errors") if isinstance(body, dict) else None
    if errors:
        text = " ".join(str(e.get("message", "")) for e in errors if isinstance(e, dict)).lower()
        # Checked against the live API (2026-11-21): a bad token is "Project Token not found"
        # on projectToken and "Not Authorized" elsewhere.
        if ("not authorized" in text or "unauthori" in text or "invalid token" in text
                or "project token not found" in text):
            raise ValidationError("Railway refused the project token", details={"reason": "railway_token_refused"})
        if "not found" in text:
            raise NotFoundError("Railway has no such service or deployment in this environment")
        raise DependencyUnavailableError(
            "Railway refused the request", dependency="railway",
            details={"status": response.status_code, "errors": len(errors)},
        )
    if response.status_code >= 400 or not isinstance(body, dict):
        raise DependencyUnavailableError(
            "Railway answered with an error", dependency="railway", details={"status": response.status_code},
        )
    return body.get("data") or {}


_SCOPE_QUERY = """
query { projectToken { projectId environmentId project { name } environment { name } } }
"""


async def railway_scope(token: str, *, transport: Any = None) -> RailwayScope:
    """What a project token reaches. Also the token check when it is saved."""
    data = await _graphql(token, _SCOPE_QUERY, transport=transport)
    info = data.get("projectToken") or {}
    if not info.get("projectId") or not info.get("environmentId"):
        raise ValidationError(
            "that is not a Railway project token (Project settings -> Tokens)",
            details={"reason": "railway_token_refused"},
        )
    return RailwayScope(
        project_id=str(info["projectId"]), environment_id=str(info["environmentId"]),
        project_name=str((info.get("project") or {}).get("name") or ""),
        environment_name=str((info.get("environment") or {}).get("name") or ""),
    )


_SERVICES_QUERY = """
query($environmentId: String!) {
  environment(id: $environmentId) {
    serviceInstances {
      edges { node {
        serviceId serviceName healthcheckPath
        latestDeployment { id status createdAt }
      } }
    }
  }
}
"""

_VARIABLES_QUERY = """
query($projectId: String!, $environmentId: String!, $serviceId: String, $unrendered: Boolean) {
  variables(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId, unrendered: $unrendered)
}
"""

_UPSERT_MUTATION = """
mutation($input: VariableCollectionUpsertInput!) { variableCollectionUpsert(input: $input) }
"""

_DELETE_MUTATION = """
mutation($input: VariableDeleteInput!) { variableDelete(input: $input) }
"""

_DEPLOYMENTS_QUERY = """
query($input: DeploymentListInput!, $first: Int) {
  deployments(input: $input, first: $first) {
    edges { node { id status createdAt canRollback canRedeploy meta } }
  }
}
"""

_DEPLOYMENT_QUERY = "query($id: String!) { deployment(id: $id) { id status serviceId } }"

_REDEPLOY_MUTATION = """
mutation($id: String!) { deploymentRedeploy(id: $id, usePreviousImageTag: true) { id status } }
"""

_RESTART_MUTATION = "mutation($id: String!) { deploymentRestart(id: $id) }"
_ROLLBACK_MUTATION = "mutation($id: String!) { deploymentRollback(id: $id) }"

_LOG_QUERIES = {
    "deploy": "query($id: String!, $limit: Int) { deploymentLogs(deploymentId: $id, limit: $limit) { timestamp message severity } }",
    "build": "query($id: String!, $limit: Int) { buildLogs(deploymentId: $id, limit: $limit) { timestamp message severity } }",
}


def _deployment_summary(node: Mapping[str, Any]) -> DeploymentSummary:
    meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
    commit = str(meta.get("commitHash") or "")[:12]
    message = str(meta.get("commitMessage") or "").splitlines()[0][:200] if meta.get("commitMessage") else ""
    return DeploymentSummary(
        deployment_id=str(node.get("id") or ""), status=str(node.get("status") or ""),
        created_at=_parse_time(node.get("createdAt")),
        can_rollback=bool(node.get("canRollback")), can_redeploy=bool(node.get("canRedeploy")),
        commit=commit, message=message,
    )


class RailwayPlatform:
    """``token_source`` returns the stored project token (``ControlService.token``).
    ``transport`` is for contract tests (``httpx.MockTransport``); production passes none."""

    def __init__(
        self, *, token_source: Callable[[], Awaitable[str]], poll_s: float = 2.0, transport: Any = None,
    ) -> None:
        self._token_source = token_source
        self._poll_s = poll_s
        self._transport = transport

    async def _call(self, query: str, variables: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        return await _graphql(await self._token_source(), query, variables, transport=self._transport)

    async def scope(self) -> RailwayScope:
        return await railway_scope(await self._token_source(), transport=self._transport)

    async def services(self) -> Sequence[ServiceSummary]:
        scope = await self.scope()
        data = await self._call(_SERVICES_QUERY, {"environmentId": scope.environment_id})
        edges = (((data.get("environment") or {}).get("serviceInstances") or {}).get("edges")) or []
        out = []
        for edge in edges:
            node = (edge or {}).get("node") or {}
            latest = node.get("latestDeployment") or {}
            out.append(ServiceSummary(
                service_id=str(node.get("serviceId") or ""), name=str(node.get("serviceName") or ""),
                latest_deployment_id=latest.get("id"), latest_status=latest.get("status"),
                latest_at=_parse_time(latest.get("createdAt")),
                healthcheck_path=str(node.get("healthcheckPath") or ""),
            ))
        return sorted(out, key=lambda s: s.name.lower())

    async def variables(self, service_id: str, *, rendered: bool = True) -> Mapping[str, str]:
        scope = await self.scope()
        data = await self._call(_VARIABLES_QUERY, {
            "projectId": scope.project_id, "environmentId": scope.environment_id,
            "serviceId": service_id, "unrendered": not rendered,
        })
        raw = data.get("variables") or {}
        return {str(k): "" if v is None else str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    async def set_variables(self, service_id: str, values: Mapping[str, str | None]) -> None:
        scope = await self.scope()
        base = {"projectId": scope.project_id, "environmentId": scope.environment_id, "serviceId": service_id}
        upserts = {k: v for k, v in values.items() if v is not None}
        if upserts:
            await self._call(_UPSERT_MUTATION, {"input": {**base, "variables": upserts, "skipDeploys": True}})
        for name in (k for k, v in values.items() if v is None):
            await self._call(_DELETE_MUTATION, {"input": {**base, "name": name}})

    async def restore_variables(self, target: str, values: Mapping[str, str | None]) -> None:
        await self.set_variables(target, values)

    async def deployments(self, service_id: str, *, limit: int = 20) -> Sequence[DeploymentSummary]:
        scope = await self.scope()
        data = await self._call(_DEPLOYMENTS_QUERY, {
            "input": {"projectId": scope.project_id, "environmentId": scope.environment_id, "serviceId": service_id},
            "first": max(1, min(limit, 50)),
        })
        edges = ((data.get("deployments") or {}).get("edges")) or []
        return [_deployment_summary((e or {}).get("node") or {}) for e in edges]

    async def _latest(self, service_id: str) -> DeploymentSummary | None:
        found = await self.deployments(service_id, limit=1)
        return found[0] if found else None

    async def redeploy(self, service_id: str) -> str:
        latest = await self._latest(service_id)
        if latest is None:
            raise NotFoundError("the service has never been deployed; deploy it once from Railway")
        data = await self._call(_REDEPLOY_MUTATION, {"id": latest.deployment_id})
        new_id = ((data.get("deploymentRedeploy") or {}).get("id"))
        if not new_id:
            raise DependencyUnavailableError("Railway did not return the new deployment", dependency="railway")
        return str(new_id)

    async def restart(self, deployment_id: str) -> None:
        await self._call(_RESTART_MUTATION, {"id": deployment_id})

    async def rollback(self, target: str, to_deployment_id: str) -> str:
        before = await self._latest(target)
        await self._call(_ROLLBACK_MUTATION, {"id": to_deployment_id})
        # deploymentRollback answers only true/false: the new deployment is the next one listed.
        for _ in range(10):
            latest = await self._latest(target)
            if latest is not None and (before is None or latest.deployment_id != before.deployment_id):
                return latest.deployment_id
            await asyncio.sleep(self._poll_s)
        raise DependencyUnavailableError(
            "Railway accepted the rollback but no new deployment appeared yet", dependency="railway",
        )

    async def deployment_health(self, target: str, deployment_id: str) -> DeploymentHealth:
        data = await self._call(_DEPLOYMENT_QUERY, {"id": deployment_id})
        return deployment_health_of((data.get("deployment") or {}).get("status"))

    async def logs(self, deployment_id: str, *, kind: str = "deploy", limit: int = 200) -> Sequence[LogLine]:
        query = _LOG_QUERIES.get(kind)
        if query is None:
            raise ValidationError("logs are 'deploy' or 'build'")
        data = await self._call(query, {"id": deployment_id, "limit": max(1, min(limit, 1000))})
        rows = data.get("deploymentLogs" if kind == "deploy" else "buildLogs") or []
        return [
            LogLine(timestamp=str(r.get("timestamp") or ""), message=str(r.get("message") or ""),
                    severity=str(r.get("severity") or ""))
            for r in rows if isinstance(r, dict)
        ]


_REFERENCE = re.compile(r"\$\{\{[^}]*\}\}")


class InMemoryRailway:
    """Railway with real state: services, raw variables, deployments and logs.

    A test moves a deployment's status with ``set_status``. ``redeploy`` and ``rollback`` create
    a new deployment in ``DEPLOYING``, as Railway's do. ``${{...}}`` references are kept raw and
    "rendered" to ``rendered-<ref>``, so code that must restore a reference can be checked.
    """

    def __init__(self, scope: RailwayScope | None = None) -> None:
        self.scope_value = scope or RailwayScope("proj-1", "env-1", "aura", "production")
        self.service_names: dict[str, str] = {}
        self.healthcheck: dict[str, str] = {}
        self.raw: dict[str, dict[str, str]] = {}
        self.history: dict[str, list[DeploymentSummary]] = {}
        self.log_lines: dict[str, list[LogLine]] = {}
        self.restarts: list[str] = []
        self.rollbacks: list[tuple[str, str]] = []
        self.fail_rollback = False
        self._ids = itertools.count(1)

    # -- test set-up -------------------------------------------------------------------------
    def add_service(self, service_id: str, name: str, variables: Mapping[str, str] | None = None,
                    *, healthcheck_path: str = "/health/live", status: str = "SUCCESS") -> str:
        self.service_names[service_id] = name
        self.healthcheck[service_id] = healthcheck_path
        self.raw[service_id] = dict(variables or {})
        return self._new_deployment(service_id, status)

    def _new_deployment(self, service_id: str, status: str) -> str:
        deployment_id = f"dep-{next(self._ids)}"
        self.history.setdefault(service_id, []).insert(0, DeploymentSummary(
            deployment_id=deployment_id, status=status, created_at=datetime.now(timezone.utc),
            can_rollback=True, can_redeploy=True, commit="abc123", message="a commit",
        ))
        return deployment_id

    def set_status(self, deployment_id: str, status: str) -> None:
        for service_id, items in self.history.items():
            for i, item in enumerate(items):
                if item.deployment_id == deployment_id:
                    items[i] = dataclasses.replace(item, status=status)
                    return
        raise NotFoundError(f"no deployment {deployment_id!r}")

    def _find(self, deployment_id: str) -> tuple[str, DeploymentSummary]:
        for service_id, items in self.history.items():
            for item in items:
                if item.deployment_id == deployment_id:
                    return service_id, item
        raise NotFoundError("Railway has no such service or deployment in this environment")

    def _service(self, service_id: str) -> None:
        if service_id not in self.service_names:
            raise NotFoundError("Railway has no such service or deployment in this environment")

    # -- RailwayApi --------------------------------------------------------------------------
    async def scope(self) -> RailwayScope:
        return self.scope_value

    async def services(self) -> Sequence[ServiceSummary]:
        out = []
        for service_id, name in self.service_names.items():
            latest = (self.history.get(service_id) or [None])[0]
            out.append(ServiceSummary(
                service_id=service_id, name=name,
                latest_deployment_id=latest.deployment_id if latest else None,
                latest_status=latest.status if latest else None,
                latest_at=latest.created_at if latest else None,
                healthcheck_path=self.healthcheck.get(service_id, ""),
            ))
        return sorted(out, key=lambda s: s.name.lower())

    async def variables(self, service_id: str, *, rendered: bool = True) -> Mapping[str, str]:
        self._service(service_id)
        raw = dict(self.raw[service_id])
        if not rendered:
            return raw
        return {k: _REFERENCE.sub(lambda m: f"rendered-{m.group(0)[3:-2]}", v) for k, v in raw.items()}

    async def set_variables(self, service_id: str, values: Mapping[str, str | None]) -> None:
        self._service(service_id)
        for name, value in values.items():
            if value is None:
                self.raw[service_id].pop(name, None)
            else:
                self.raw[service_id][name] = value

    async def restore_variables(self, target: str, values: Mapping[str, str | None]) -> None:
        await self.set_variables(target, values)

    async def deployments(self, service_id: str, *, limit: int = 20) -> Sequence[DeploymentSummary]:
        self._service(service_id)
        return list(self.history.get(service_id, []))[:limit]

    async def redeploy(self, service_id: str) -> str:
        self._service(service_id)
        if not self.history.get(service_id):
            raise NotFoundError("the service has never been deployed; deploy it once from Railway")
        return self._new_deployment(service_id, "DEPLOYING")

    async def restart(self, deployment_id: str) -> None:
        self._find(deployment_id)
        self.restarts.append(deployment_id)

    async def rollback(self, target: str, to_deployment_id: str) -> str:
        if self.fail_rollback:
            raise DependencyUnavailableError("Railway refused the request", dependency="railway")
        self._find(to_deployment_id)
        self.rollbacks.append((target, to_deployment_id))
        return self._new_deployment(target, "DEPLOYING")

    async def deployment_health(self, target: str, deployment_id: str) -> DeploymentHealth:
        return deployment_health_of(self._find(deployment_id)[1].status)

    async def logs(self, deployment_id: str, *, kind: str = "deploy", limit: int = 200) -> Sequence[LogLine]:
        if kind not in _LOG_QUERIES:
            raise ValidationError("logs are 'deploy' or 'build'")
        self._find(deployment_id)
        return list(self.log_lines.get(f"{kind}:{deployment_id}", []))[-limit:]
