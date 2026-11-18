"""SaaS P1-D1: subscriptions as AURA keeps them -- prices, periods, and the lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.domain.billing import (
    DEFAULT_PRICES,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    add_month,
    advance,
    apply_payment,
    load_prices,
    price_for,
)
from aura_core.domain.errors import ConfigurationError, ValidationError

UTC = timezone.utc
T0 = datetime(2026, 1, 31, 10, tzinfo=UTC)
GRACE = timedelta(days=7)


def _payment(**overrides) -> Payment:
    fields = dict(id="pay1", workspace_id="w" * 32, plan_id="pro", seats=1, amount_piastres=15000,
                  method="PAYATFAWRY", purpose="initial", status=PaymentStatus.PAID, provider_ref="F1",
                  reference_number="123", created_at=T0, expires_at=T0 + timedelta(hours=72), paid_at=T0)
    fields.update(overrides)
    return Payment(**fields)


def test_prices() -> None:
    assert DEFAULT_PRICES == {"pro": 15000, "team": 12000}
    assert price_for(DEFAULT_PRICES, "pro", 1) == 15000
    assert price_for(DEFAULT_PRICES, "team", 4) == 48000
    with pytest.raises(ValidationError):
        price_for(DEFAULT_PRICES, "free", 1)
    assert load_prices("") is DEFAULT_PRICES
    assert load_prices(json.dumps({"pro": 9900, "team": 7500}))["team"] == 7500
    for bad in ('{"pro": -1}', '{"pro": "cheap"}', "[1]", "{nope"):
        with pytest.raises(ConfigurationError):
            load_prices(bad)


def test_month_clamping() -> None:
    assert add_month(datetime(2026, 1, 31, tzinfo=UTC)) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_month(datetime(2028, 1, 31, tzinfo=UTC)) == datetime(2028, 2, 29, tzinfo=UTC)
    assert add_month(datetime(2026, 12, 15, 9, tzinfo=UTC)) == datetime(2027, 1, 15, 9, tzinfo=UTC)


def test_activation_starts_a_period() -> None:
    sub = apply_payment(None, _payment(), T0)
    assert sub.status is SubscriptionStatus.ACTIVE and sub.plan_id == "pro"
    assert (sub.period_start, sub.period_end) == (T0, add_month(T0))


def test_early_renewal_extends_from_the_period_end() -> None:
    sub = apply_payment(None, _payment(), T0)
    renewed = apply_payment(sub, _payment(id="pay2", purpose="renewal"), sub.period_end - timedelta(days=2))
    assert renewed.period_end == add_month(sub.period_end)
    assert renewed.period_start == sub.period_end
    assert renewed.status is SubscriptionStatus.ACTIVE


def test_late_payment_in_grace_restarts_from_now() -> None:
    sub = apply_payment(None, _payment(), T0)
    late = sub.period_end + timedelta(days=3)
    graced = advance(advance(sub, late, GRACE), late, GRACE)
    assert graced.status is SubscriptionStatus.GRACE
    renewed = apply_payment(graced, _payment(id="pay3", purpose="renewal"), late)
    assert renewed.status is SubscriptionStatus.ACTIVE and renewed.period_start == late
    assert renewed.grace_until is None


def test_transitions_past_due_grace_expired() -> None:
    sub = apply_payment(None, _payment(), T0)
    after = sub.period_end + timedelta(minutes=1)
    past_due = advance(sub, after, GRACE)
    assert past_due.status is SubscriptionStatus.PAST_DUE
    grace = advance(past_due, after, GRACE)
    assert grace.status is SubscriptionStatus.GRACE and grace.grace_until == sub.period_end + GRACE
    assert advance(grace, sub.period_end + GRACE - timedelta(seconds=1), GRACE).status is SubscriptionStatus.GRACE
    assert advance(grace, sub.period_end + GRACE, GRACE).status is SubscriptionStatus.EXPIRED
    assert advance(sub, sub.period_end - timedelta(days=1), GRACE) == sub  # nothing due yet


def test_canceled_runs_to_period_end_then_expires() -> None:
    sub = apply_payment(None, _payment(), T0)
    from dataclasses import replace

    canceled = replace(sub, status=SubscriptionStatus.CANCELED)
    assert advance(canceled, sub.period_end - timedelta(days=1), GRACE).status is SubscriptionStatus.CANCELED
    assert advance(canceled, sub.period_end, GRACE).status is SubscriptionStatus.EXPIRED
