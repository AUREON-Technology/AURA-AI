"""Grafana Cloud from the control center (ADR-037, stage 5): set up and status.

Engine layer: Grafana is reached only through the ``ObservabilityStack`` port. ``docs/deployment/
observability.md`` lists what used to be done by hand; this is what each becomes:

* **Alert rules** -- "Apply setup" loads every group of ``deploy/monitoring/alerts/aura.yml``
  (one source of truth, packaged with the gateway) into the stack's ruler, namespace ``aura``.
  Each group is reported on its own, so a partial failure says which group.
* **The scrape job** -- Grafana has no API for Metrics Endpoint jobs with this token (UI or
  Terraform only). The page gives its exact values once (job ``aura-gateway``, which the alert
  rules select, the gateway's ``/metrics`` URL, bearer = ``AURA_METRICS_TOKEN``, 60 s) and then
  reads whether it works: ``up{job="aura-gateway"}``.
* **Uptime checks** -- Synthetic Monitoring needs its own token, so the two checks are given as
  exact values too, and their pass rate is read from the probe metrics they write.
* **Firing alerts** -- ``ALERTS{alertstate="firing"}``, which the ruler writes.

A query that cannot run (a token without ``metrics:read``) is reported as such, not as "all
clear".
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence

from ..domain.control import ChangeStatus, Platform
from ..domain.errors import AuraError, NotFoundError
from ..ports.control_plane import ControlStore, ObservabilityStack
from .railway import change_json
from .service import ControlService, _confirm

__all__ = ["MonitoringService", "RULES_NAMESPACE", "SCRAPE_JOB"]

RULES_NAMESPACE = "aura"
#: The job name the alert rules select (``AuraGatewayDown``: ``up{job="aura-gateway"}``).
SCRAPE_JOB = "aura-gateway"
_UPTIME_QUERY = (
    "sum by (job, instance) (rate(probe_all_success_sum[15m]))"
    " / sum by (job, instance) (rate(probe_all_success_count[15m]))"
)


def _number(value: Any) -> float | None:
    return None if not isinstance(value, (int, float)) or math.isnan(value) else float(value)


class MonitoringService:
    def __init__(
        self,
        *,
        control: ControlService,
        store: ControlStore,
        api: ObservabilityStack,
        rules: Callable[[], Sequence[Mapping[str, Any]]],
        metrics_token_set: Callable[[], bool],
    ) -> None:
        self._control = control
        self._store = store
        self._api = api
        self._rules = rules
        self._metrics_token_set = metrics_token_set

    async def _require(self) -> None:
        if await self._store.get_credential(Platform.GRAFANA) is None:
            raise NotFoundError("Grafana Cloud is not connected; connect it in Settings")

    async def _query(self, promql: str, errors: list[str]) -> Sequence[Mapping[str, Any]]:
        try:
            return await self._api.query(promql)
        except AuraError as exc:
            errors.append(exc.message)
            return []

    async def overview(self, *, public_url: str) -> dict[str, Any]:
        await self._require()
        stack = await self._api.stack()
        errors: list[str] = []
        try:
            loaded = set(await self._api.rule_groups(RULES_NAMESPACE))
        except AuraError as exc:
            loaded = set()
            errors.append(exc.message)
        groups = [{"name": str(g["name"]), "rules": len(g.get("rules") or []), "loaded": str(g["name"]) in loaded}
                  for g in self._rules()]
        up = await self._query(f'up{{job="{SCRAPE_JOB}"}}', errors)
        values = [_number(row.get("value")) for row in up]
        scrape_state = "missing" if not up else ("up" if any(v == 1 for v in values) else "down")
        firing = await self._query('ALERTS{alertstate="firing"}', errors)
        probes = await self._query(_UPTIME_QUERY, errors)
        base = public_url.rstrip("/")
        return {
            "stack": {"slug": stack.slug, "grafana_url": stack.grafana_url},
            "rules": {"namespace": RULES_NAMESPACE, "groups": groups,
                      "all_loaded": all(g["loaded"] for g in groups)},
            "scrape": {
                "state": scrape_state,
                "job": SCRAPE_JOB,
                "url": f"{base}/metrics",
                "interval_s": 60,
                "auth": "Bearer: the value of AURA_METRICS_TOKEN on the gateway",
                "metrics_token_set": self._metrics_token_set(),
            },
            "uptime": {
                "checks": [
                    {"name": "AURA liveness", "url": f"{base}/health/live", "frequency_s": 60,
                     "success": "status 200"},
                    {"name": "AURA readiness", "url": f"{base}/health/ready", "frequency_s": 300,
                     "success": 'status 200 and body matches "healthy":\\s*true'},
                ],
                "observed": [
                    {"job": str(row.get("metric", {}).get("job") or ""),
                     "instance": str(row.get("metric", {}).get("instance") or ""),
                     "success_ratio": _number(row.get("value"))}
                    for row in probes
                ],
            },
            "firing": [
                {"alertname": str(row.get("metric", {}).get("alertname") or ""),
                 "severity": str(row.get("metric", {}).get("severity") or "")}
                for row in firing
            ],
            "errors": errors,
        }

    async def apply(self, *, actor: str, confirm: str | None) -> dict[str, Any]:
        """Load every rule group; each one's result is reported, and a partial apply is FAILED."""
        await self._require()
        _confirm(Platform.GRAFANA.value, confirm)
        await self._control.check_write_cap(actor)
        results = []
        for group in self._rules():
            try:
                await self._api.load_rule_group(RULES_NAMESPACE, group)
                results.append({"group": str(group["name"]), "ok": True, "error": ""})
            except AuraError as exc:
                results.append({"group": str(group["name"]), "ok": False,
                                "error": exc.message})
        failed = [r["group"] for r in results if not r["ok"]]
        record = await self._control.journal(
            actor=actor, platform=Platform.GRAFANA, target=RULES_NAMESPACE, action="grafana_apply",
            detail={"service": "Grafana Cloud", "results": results},
            status=ChangeStatus.FAILED if failed else ChangeStatus.RECORDED,
            reason=f"not loaded: {', '.join(failed)}" if failed else "",
        )
        return {"results": results, "change": change_json(record)}
