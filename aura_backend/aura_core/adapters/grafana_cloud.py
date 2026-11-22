"""Grafana Cloud for the control center (ADR-037, stage 5): the stack, its Prometheus, its ruler.

``HttpGrafanaCloud`` implements ``ObservabilityStack`` with the access policy token stored when
Grafana was connected. What the docs establish (read 2026-11-22):

* **The stack.** ``GET https://grafana.com/api/instances/<slug>`` with
  ``Authorization: Bearer <access policy token>`` returns ``slug``, ``url`` (Grafana),
  ``hmInstancePromUrl`` and ``hmInstancePromId``. The slug is the first label of the stack URL
  entered with the token (``https://<slug>.grafana.net``).
* **The hosted Prometheus.** HTTP basic auth, user = the Prometheus instance id, password = the
  access policy token (the documented remote_write credentials). Queries go to
  ``<prom>/api/prom/api/v1/query``: Grafana Cloud serves its Prometheus API under ``/api/prom``.
* **The ruler** takes one YAML rule group per ``POST .../config/v1/rules/<namespace>`` (Mimir's
  HTTP API). Grafana Cloud's prefix for it is not stated in the docs, so the candidates are tried
  in order and the first that accepts the write is kept for this process. A wrong route answers
  404; loading a group is idempotent, so trying again is harmless.

Not done here, because Grafana offers no API for it with this token: the Metrics Endpoint scrape
job (UI or Terraform only) and Synthetic Monitoring checks (they need their own SM token). The
engine shows their exact values to enter once and reads their state through Prometheus.

The token is read from the vault on every call and never logged; vendor messages are never
passed through.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import quote, urlsplit

from ..domain.errors import DependencyUnavailableError, NotFoundError, ValidationError
from ..ports.control_plane import GrafanaStack

__all__ = ["GRAFANA_API", "HttpGrafanaCloud", "InMemoryGrafana", "RULER_PREFIXES", "stack_slug"]

GRAFANA_API = "https://grafana.com/api"
#: Candidate ruler prefixes under the stack's Prometheus URL, most likely first.
RULER_PREFIXES = ("/api/prom/config/v1/rules", "/prometheus/config/v1/rules", "/config/v1/rules")
QUERY_PATH = "/api/prom/api/v1/query"
_TIMEOUT_S = 20.0


def stack_slug(stack_url: str) -> str:
    """``https://<slug>.grafana.net`` -> ``<slug>``; a bare slug is returned as it is."""
    value = (stack_url or "").strip()
    host = urlsplit(value).hostname if "://" in value else value
    slug = (host or "").split(".", 1)[0].lower()
    if not slug or not slug.replace("-", "").replace("_", "").isalnum():
        raise ValidationError("give the Grafana stack URL (https://<stack>.grafana.net)",
                              details={"reason": "grafana_stack_missing"})
    return slug


async def grafana_stack(token: str, slug: str, *, transport: Any = None) -> GrafanaStack:
    """The stack a token reaches. Also the token check when Grafana is connected."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=transport) as client:
            response = await client.get(f"{GRAFANA_API}/instances/{quote(slug, safe='')}",
                                        headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:
        raise DependencyUnavailableError(
            "Grafana Cloud could not be reached", dependency="grafana", details={"error": type(exc).__name__},
        ) from exc
    if response.status_code in (401, 403):
        raise ValidationError("Grafana Cloud refused the token (it needs stacks:read)",
                              details={"reason": "grafana_refused"})
    if response.status_code == 404:
        raise ValidationError(f"Grafana Cloud has no stack {slug!r} for this token", details={"reason": "grafana_stack_missing"})
    if response.status_code >= 400:
        raise DependencyUnavailableError("Grafana Cloud answered with an error", dependency="grafana",
                                         details={"status": response.status_code})
    body = response.json() if response.content else {}
    prom_url, prom_id = str(body.get("hmInstancePromUrl") or ""), str(body.get("hmInstancePromId") or "")
    if not prom_url or not prom_id:
        raise DependencyUnavailableError("the stack has no hosted Prometheus", dependency="grafana")
    return GrafanaStack(slug=str(body.get("slug") or slug), grafana_url=str(body.get("url") or ""),
                        prometheus_url=prom_url.rstrip("/"), prometheus_id=prom_id)


class HttpGrafanaCloud:
    """``token_source`` returns the stored access policy token; ``stack_source`` the stack URL."""

    def __init__(self, *, token_source: Callable[[], Awaitable[str]], stack_source: Callable[[], Awaitable[str]],
                 transport: Any = None) -> None:
        self._token_source = token_source
        self._stack_source = stack_source
        self._transport = transport
        self._ruler_prefix: str | None = None

    async def stack(self) -> GrafanaStack:
        return await grafana_stack(await self._token_source(), stack_slug(await self._stack_source()),
                                   transport=self._transport)

    async def _prom(self, method: str, path: str, **kwargs: Any) -> Any:
        import httpx

        stack = await self.stack()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=self._transport) as client:
                return await client.request(method, f"{stack.prometheus_url}{path}",
                                            auth=(stack.prometheus_id, await self._token_source()), **kwargs)
        except Exception as exc:
            raise DependencyUnavailableError(
                "Grafana Cloud's Prometheus could not be reached", dependency="grafana",
                details={"error": type(exc).__name__},
            ) from exc

    @staticmethod
    def _refused(response: Any, need: str) -> None:
        if response.status_code in (401, 403):
            raise ValidationError(f"Grafana Cloud refused the token (it needs {need})",
                                  details={"reason": "grafana_refused"})

    async def load_rule_group(self, namespace: str, group: Mapping[str, Any]) -> None:
        import yaml

        body = yaml.safe_dump(dict(group), sort_keys=False)
        prefixes = (self._ruler_prefix,) if self._ruler_prefix else RULER_PREFIXES
        for prefix in prefixes:
            response = await self._prom("POST", f"{prefix}/{quote(namespace, safe='')}", content=body,
                                        headers={"Content-Type": "application/yaml"})
            self._refused(response, "rules:write")
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                raise DependencyUnavailableError(
                    "the ruler refused the rule group", dependency="grafana",
                    details={"status": response.status_code, "group": str(group.get("name") or "")},
                )
            self._ruler_prefix = prefix
            return
        raise DependencyUnavailableError("Grafana Cloud's ruler was not found at any known path", dependency="grafana")

    async def rule_groups(self, namespace: str) -> Sequence[str]:
        import yaml

        prefixes = (self._ruler_prefix,) if self._ruler_prefix else RULER_PREFIXES
        for prefix in prefixes:
            response = await self._prom("GET", f"{prefix}/{quote(namespace, safe='')}")
            self._refused(response, "rules:read")
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                raise DependencyUnavailableError("the ruler answered with an error", dependency="grafana",
                                                 details={"status": response.status_code})
            self._ruler_prefix = prefix
            parsed = yaml.safe_load(response.text) or {}
            groups = parsed.get(namespace, parsed.get("groups", [])) if isinstance(parsed, dict) else []
            return [str(g.get("name")) for g in groups or [] if isinstance(g, dict) and g.get("name")]
        return []  # no route answered: nothing is loaded that this token can see

    async def query(self, promql: str) -> Sequence[Mapping[str, Any]]:
        response = await self._prom("GET", QUERY_PATH, params={"query": promql})
        self._refused(response, "metrics:read")
        if response.status_code == 404:
            raise NotFoundError("Grafana Cloud's query API was not found")
        if response.status_code >= 400:
            raise DependencyUnavailableError("the query failed", dependency="grafana",
                                             details={"status": response.status_code})
        data = (response.json() or {}).get("data") or {}
        out = []
        for row in data.get("result") or []:
            try:
                value = float((row.get("value") or [0, "nan"])[1])
            except (TypeError, ValueError):
                value = float("nan")
            out.append({"metric": dict(row.get("metric") or {}), "value": value})
        return out


class InMemoryGrafana:
    """A stack with real state: rule groups per namespace, and query answers a test sets."""

    def __init__(self, stack: GrafanaStack | None = None) -> None:
        self.stack_value = stack or GrafanaStack("aurastack", "https://aurastack.grafana.net",
                                                 "https://prometheus-prod-01.grafana.net", "123456")
        self.groups: dict[str, dict[str, dict[str, Any]]] = {}
        self.answers: dict[str, list[dict[str, Any]]] = {}
        self.fail_group: str | None = None

    async def stack(self) -> GrafanaStack:
        return self.stack_value

    async def load_rule_group(self, namespace: str, group: Mapping[str, Any]) -> None:
        if group.get("name") == self.fail_group:
            raise DependencyUnavailableError("the ruler refused the rule group", dependency="grafana")
        self.groups.setdefault(namespace, {})[str(group["name"])] = dict(group)

    async def rule_groups(self, namespace: str) -> Sequence[str]:
        return sorted(self.groups.get(namespace, {}))

    async def query(self, promql: str) -> Sequence[Mapping[str, Any]]:
        for key, rows in self.answers.items():
            if key in promql:
                return rows
        return []
