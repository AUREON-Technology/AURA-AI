"""Plans: what a workspace may use (SaaS phase 1, spec §5.3). Stdlib only (ADR-001).

A workspace has exactly one plan (plans follow the workspace, not the person). Every limit is data
in a :data:`PlanLimits`; nothing compares a plan's *name*. The built-in numbers are placeholders
until the owner sets prices and limits (spec §8.1); ``AURA_PLAN_CATALOGUE`` replaces the whole
catalogue with JSON of the same shape, no code change.

Usage is counted per workspace per UTC calendar month (``YYYY-MM``) until billing (Plan D) moves
the period to each subscription's own renewal date. Storage is a running total, not monthly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from .errors import AuraError, ConfigurationError
from .tenancy import WorkspaceKind

__all__ = [
    "PlanLimits",
    "Entitlement",
    "DEFAULT_CATALOGUE",
    "DEFAULT_PLAN",
    "STORAGE_PERIOD",
    "PlanLimitError",
    "load_catalogue",
    "monthly_message_limit",
    "period_for",
    "period_resets_at",
]

DEFAULT_PLAN = "free"
#: Storage is a running total; its counter lives under this period, not a month.
STORAGE_PERIOD = "all"
_MIB = 1024 * 1024
_GIB = 1024 * _MIB


@dataclass(frozen=True, slots=True)
class PlanLimits:
    id: str
    messages_per_month: int
    #: True: ``messages_per_month`` is per seat, pooled across the workspace (Team).
    messages_pooled_per_seat: bool
    engineer: bool
    storage_bytes: int
    min_seats: int
    max_seats: int
    #: Which workspaces may hold this plan (Pro is personal, Team is for teams).
    workspace_kinds: frozenset[WorkspaceKind]


@dataclass(frozen=True, slots=True)
class Entitlement:
    workspace_id: str
    plan_id: str
    seats: int
    #: ``manual`` (an operator set it) or ``subscription`` (billing set it, Plan D).
    source: str
    updated_at: datetime
    updated_by: str


_BOTH = frozenset({WorkspaceKind.PERSONAL, WorkspaceKind.TEAM})

DEFAULT_CATALOGUE: Mapping[str, PlanLimits] = MappingProxyType({
    "free": PlanLimits("free", 50, False, False, 25 * _MIB, 1, 3, _BOTH),
    "pro": PlanLimits("pro", 1500, False, True, 1 * _GIB, 1, 1, frozenset({WorkspaceKind.PERSONAL})),
    "team": PlanLimits("team", 1500, True, True, 5 * _GIB, 2, 50, frozenset({WorkspaceKind.TEAM})),
})


class PlanLimitError(AuraError):
    """The workspace's plan does not allow this (402). ``details`` say which limit, how much was
    used and allowed, and when a monthly limit resets, so the client can word it."""

    kind = "plan_limit"
    http_status = 402

    def __init__(
        self, message: str, *, limit: str, plan: str, used: int, allowed: int,
        resets_at: datetime | None = None,
    ) -> None:
        super().__init__(message, details={
            "limit": limit, "plan": plan, "used": used, "allowed": allowed,
            "resets_at": resets_at.isoformat() if resets_at else None,
        })


def monthly_message_limit(plan: PlanLimits, seats: int) -> int:
    return plan.messages_per_month * max(seats, 1) if plan.messages_pooled_per_seat else plan.messages_per_month


def period_for(at: datetime) -> str:
    at = at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return f"{at.year:04d}-{at.month:02d}"


def period_resets_at(period: str) -> datetime:
    year, month = (int(part) for part in period.split("-"))
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _non_negative_int(raw: Any, field: str, plan_id: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ConfigurationError(f"plan {plan_id!r}: {field} must be a non-negative integer")
    return raw


def load_catalogue(raw_json: str) -> Mapping[str, PlanLimits]:
    """``AURA_PLAN_CATALOGUE``: empty means the built-in placeholders."""
    if not raw_json.strip():
        return DEFAULT_CATALOGUE
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("AURA_PLAN_CATALOGUE is not valid JSON") from exc
    if not isinstance(data, dict) or DEFAULT_PLAN not in data:
        raise ConfigurationError(f"AURA_PLAN_CATALOGUE must be an object that defines {DEFAULT_PLAN!r}")
    plans: dict[str, PlanLimits] = {}
    for plan_id, spec in data.items():
        if not isinstance(spec, dict):
            raise ConfigurationError(f"plan {plan_id!r} must be an object")
        try:
            kinds = frozenset(WorkspaceKind(k) for k in spec.get("workspace_kinds", []))
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(f"plan {plan_id!r}: unknown workspace kind") from exc
        if not kinds:
            raise ConfigurationError(f"plan {plan_id!r}: workspace_kinds must not be empty")
        min_seats = _non_negative_int(spec.get("min_seats"), "min_seats", plan_id)
        max_seats = _non_negative_int(spec.get("max_seats"), "max_seats", plan_id)
        if not 1 <= min_seats <= max_seats:
            raise ConfigurationError(f"plan {plan_id!r}: need 1 <= min_seats <= max_seats")
        plans[plan_id] = PlanLimits(
            id=plan_id,
            messages_per_month=_non_negative_int(spec.get("messages_per_month"), "messages_per_month", plan_id),
            messages_pooled_per_seat=bool(spec.get("messages_pooled_per_seat", False)),
            engineer=bool(spec.get("engineer", False)),
            storage_bytes=_non_negative_int(spec.get("storage_bytes"), "storage_bytes", plan_id),
            min_seats=min_seats,
            max_seats=max_seats,
            workspace_kinds=kinds,
        )
    return MappingProxyType(plans)
