"""Run discovery against an active connector (AP3).

Discovery is only ever run against a connector a Super Admin has approved and activated
(``ConnectorRegistry.open_active``): the approval covered *reaching* the system, and describing
it is the first thing that reach is for. The report is returned, audited by its digest and
counts (never its content) and published as metrics. Persisting it -- as a versioned Project
Twin -- is AP4's job.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..domain.discovery import DiscoveryBudget, DiscoveryReport
from ..domain.errors import AuraError, DependencyUnavailableError, ValidationError
from ..observability.logging import get_logger
from ..ports.connectors import DiscoveryConnector
from ..ports.runtime import AuditEvent, Clock
from .connectors import ConnectorRegistry

__all__ = ["DiscoveryService"]

logger = get_logger("adaptation.discovery")


class DiscoveryService:
    def __init__(self, *, registry: ConnectorRegistry, clock: Clock, audit: Any = None, metrics: Any = None) -> None:
        self._registry = registry
        self._clock = clock
        self._audit = audit
        self._metrics = metrics

    async def discover(self, connector_id: str, *, budget: DiscoveryBudget, by: str) -> DiscoveryReport:
        connector = await self._registry.open_active(connector_id)
        kind = str(connector.descriptor.kind)
        started = self._clock.monotonic()
        outcome = "error"
        try:
            if not isinstance(connector, DiscoveryConnector):
                outcome = "unsupported"
                raise ValidationError(f"{kind} connectors cannot discover yet", details={"kind": kind})
            # The connector enforces the budget itself; this outer bound only catches one that
            # does not (a hung driver), with a margin for connection setup.
            limit = budget.max_seconds + connector.descriptor.timeout_s + 5.0
            try:
                report = await asyncio.wait_for(connector.discover(budget), timeout=limit)
            except asyncio.TimeoutError:
                outcome = "timeout"
                raise DependencyUnavailableError(
                    "discovery did not finish within its budget", dependency="connector",
                    details={"connector": connector_id, "seconds": limit},
                ) from None
            outcome = "truncated" if report.truncated else "ok"
        except AuraError:
            raise
        except Exception as exc:  # a connector bug: report its type, never its message
            logger.warning("discovery raised", extra={"connector": connector_id, "error": type(exc).__name__})
            raise DependencyUnavailableError(
                "discovery failed", dependency="connector", details={"connector": connector_id},
            ) from None
        finally:
            try:
                await connector.aclose()
            except Exception:  # pragma: no cover - defensive
                logger.debug("connector close failed", exc_info=True)
            seconds = self._clock.monotonic() - started
            if self._metrics is not None:
                self._metrics.observe_discovery(connector_id, kind, outcome, seconds)
            await self._record(connector_id, kind, by, outcome, seconds)
        if self._metrics is not None:
            self._metrics.set_discovery_artifacts(connector_id, len(report.artifacts))
        await self._record(connector_id, kind, by, "report", seconds, report)
        return report

    async def _record(self, connector_id: str, kind: str, by: str, outcome: str, seconds: float,
                      report: DiscoveryReport | None = None) -> None:
        if self._audit is None or (report is None and outcome in ("ok", "truncated")):
            return  # a successful run is audited once, with its report
        detail: dict[str, Any] = {"kind": kind, "seconds": round(seconds, 3)}
        if report is not None:
            detail.update(
                digest=report.digest, artifacts=len(report.artifacts), truncated=report.truncated,
                issues=len(report.issues),
            )
            outcome = "truncated" if report.truncated else "ok"
        try:
            await self._audit.record(AuditEvent(
                action="adaptation.connector.discover", actor=by, resource=f"connector:{connector_id}",
                outcome=outcome, detail=detail,
            ))
        except Exception:  # pragma: no cover - defensive
            logger.debug("discovery audit write failed", exc_info=True)
