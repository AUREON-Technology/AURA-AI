"""Reads the Prometheus alert rule groups the control center loads into Grafana Cloud (stage 5).

The rules live in ``deploy/monitoring/alerts/aura.yml`` (the VPS Prometheus reads them there).
The gateway's image is built from ``aura_backend/`` alone, so it carries a byte-identical copy at
``aura_core/control/assets/aura-alerts.yml``; ``tests/unit/test_control_monitoring.py`` fails the
moment the two differ, so there is still one source of truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = ["ALERT_RULES_PATH", "load_alert_groups"]

ALERT_RULES_PATH = Path(__file__).resolve().parents[1] / "control" / "assets" / "aura-alerts.yml"


def load_alert_groups(path: Path = ALERT_RULES_PATH) -> Sequence[Mapping[str, Any]]:
    import yaml

    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    groups = parsed.get("groups") if isinstance(parsed, dict) else None
    return [g for g in groups or [] if isinstance(g, dict) and g.get("name")]
