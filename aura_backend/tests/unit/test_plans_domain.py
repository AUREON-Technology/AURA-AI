"""SaaS P1-C: plans as data -- the catalogue, entitlements, the monthly period and the refusal."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aura_core.domain.errors import ConfigurationError
from aura_core.domain.plans import (
    DEFAULT_CATALOGUE,
    DEFAULT_PLAN,
    PlanLimitError,
    load_catalogue,
    monthly_message_limit,
    period_for,
    period_resets_at,
)
from aura_core.domain.tenancy import WorkspaceKind

MIB = 1024 * 1024


def test_default_catalogue_has_three_plans() -> None:
    assert DEFAULT_PLAN == "free"
    assert set(DEFAULT_CATALOGUE) == {"free", "pro", "team"}
    free, pro, team = (DEFAULT_CATALOGUE[k] for k in ("free", "pro", "team"))
    assert (free.messages_per_month, free.engineer, free.storage_bytes, free.max_seats) == (50, False, 25 * MIB, 3)
    assert (pro.messages_per_month, pro.engineer, pro.max_seats) == (1500, True, 1)
    assert pro.workspace_kinds == frozenset({WorkspaceKind.PERSONAL})
    assert team.workspace_kinds == frozenset({WorkspaceKind.TEAM})
    assert (team.min_seats, team.max_seats, team.messages_pooled_per_seat) == (2, 50, True)
    assert free.workspace_kinds == frozenset({WorkspaceKind.PERSONAL, WorkspaceKind.TEAM})


def test_pooled_team_limit_scales_with_seats() -> None:
    assert monthly_message_limit(DEFAULT_CATALOGUE["team"], 4) == 6000
    assert monthly_message_limit(DEFAULT_CATALOGUE["pro"], 4) == 1500


def _plan(**overrides) -> dict:
    base = {"messages_per_month": 10, "messages_pooled_per_seat": False, "engineer": False,
            "storage_bytes": 1000, "min_seats": 1, "max_seats": 1, "workspace_kinds": ["personal", "team"]}
    base.update(overrides)
    return base


def test_load_catalogue_validates() -> None:
    assert load_catalogue("") is DEFAULT_CATALOGUE
    good = load_catalogue(json.dumps({"free": _plan(), "pro": _plan(engineer=True, workspace_kinds=["personal"])}))
    assert good["pro"].engineer is True and good["free"].messages_per_month == 10
    for bad in (
        {"pro": _plan()},                                   # no free plan
        {"free": _plan(messages_per_month=-1)},
        {"free": _plan(min_seats=3, max_seats=2)},
        {"free": _plan(workspace_kinds=["galaxy"])},
        {"free": _plan(storage_bytes="lots")},
    ):
        with pytest.raises(ConfigurationError):
            load_catalogue(json.dumps(bad))
    with pytest.raises(ConfigurationError):
        load_catalogue("{not json")


def test_period_and_reset_across_year_end() -> None:
    at = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
    assert period_for(at) == "2026-12"
    assert period_resets_at("2026-12") == datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert period_resets_at("2026-02") == datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_plan_limit_error_shape() -> None:
    resets = datetime(2027, 1, 1, tzinfo=timezone.utc)
    error = PlanLimitError("monthly messages used up", limit="messages", plan="free", used=50, allowed=50, resets_at=resets)
    assert error.http_status == 402 and error.kind == "plan_limit"
    body = error.to_dict()
    assert body["details"] == {"limit": "messages", "plan": "free", "used": 50, "allowed": 50,
                               "resets_at": "2027-01-01T00:00:00+00:00"}
