"""Subscriptions as AURA keeps them (SaaS P1-D1, spec §5.4). Stdlib only (ADR-001).

AURA owns the subscription: the payment provider (Fawry) only collects each payment. A paid
payment activates or extends the period; a scheduled cycle moves an unpaid subscription through
``past_due`` -> ``grace`` (full access) -> ``expired`` (back to Free, nothing deleted). A canceled
subscription keeps access to the end of what was paid for, then expires without renewing.

Money is integer **piastres** (1 EGP = 100). Prices are data (``AURA_PLAN_PRICES``); the built-in
numbers are placeholders until the owner prices the plans (spec §8.1).
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .errors import ConfigurationError, ValidationError

__all__ = [
    "SubscriptionStatus",
    "Subscription",
    "PaymentStatus",
    "Payment",
    "DEFAULT_PRICES",
    "load_prices",
    "price_for",
    "add_month",
    "apply_payment",
    "advance",
]

#: Plans priced per seat (the rest are flat per workspace).
PER_SEAT_PLANS = frozenset({"team"})
DEFAULT_PRICES: Mapping[str, int] = MappingProxyType({"pro": 15000, "team": 12000})


class SubscriptionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    GRACE = "grace"
    CANCELED = "canceled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Subscription:
    workspace_id: str
    plan_id: str
    seats: int
    status: SubscriptionStatus
    provider: str
    period_start: datetime
    period_end: datetime
    updated_at: datetime
    grace_until: datetime | None = None
    #: Fawry card token for renewals, when the payer saved a card. Never returned by any route.
    card_token: str | None = None
    customer_profile_id: str | None = None


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    FAILED = "failed"
    #: Paid, but not the amount we asked for: activates nothing until a person looks.
    MISMATCH = "mismatch"
    REFUNDED = "refunded"


@dataclass(frozen=True, slots=True)
class Payment:
    #: Our id; sent to Fawry as ``merchantRefNum``, the idempotency key for its notifications.
    id: str
    workspace_id: str
    plan_id: str
    seats: int
    amount_piastres: int
    method: str
    #: ``initial`` or ``renewal``.
    purpose: str
    status: PaymentStatus
    provider_ref: str | None
    reference_number: str | None
    created_at: datetime
    expires_at: datetime | None
    paid_at: datetime | None = None


def load_prices(raw_json: str) -> Mapping[str, int]:
    if not raw_json.strip():
        return DEFAULT_PRICES
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("AURA_PLAN_PRICES is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ConfigurationError("AURA_PLAN_PRICES must be an object of plan -> piastres")
    for plan, piastres in data.items():
        if isinstance(piastres, bool) or not isinstance(piastres, int) or piastres <= 0:
            raise ConfigurationError(f"AURA_PLAN_PRICES[{plan!r}] must be a positive integer (piastres)")
    return MappingProxyType(dict(data))


def price_for(prices: Mapping[str, int], plan_id: str, seats: int) -> int:
    if plan_id not in prices:
        raise ValidationError(f"plan {plan_id!r} cannot be bought", details={"plans": sorted(prices)})
    return prices[plan_id] * max(seats, 1) if plan_id in PER_SEAT_PLANS else prices[plan_id]


def add_month(at: datetime) -> datetime:
    year, month = (at.year + 1, 1) if at.month == 12 else (at.year, at.month + 1)
    return at.replace(year=year, month=month, day=min(at.day, calendar.monthrange(year, month)[1]))


def apply_payment(sub: Subscription | None, payment: Payment, now: datetime) -> Subscription:
    """A paid payment: activate, or extend. Paid early, the new period follows the current one;
    paid after the period ended (past due or in grace), it starts now. The caller applies each
    payment once (``PaymentStore.mark`` from pending is the lock)."""
    if sub is None or sub.status in (SubscriptionStatus.EXPIRED, SubscriptionStatus.PENDING) or sub.period_end <= now:
        start = now
    else:
        start = sub.period_end
    return Subscription(
        workspace_id=payment.workspace_id,
        plan_id=payment.plan_id,
        seats=payment.seats,
        status=SubscriptionStatus.ACTIVE,
        provider=sub.provider if sub is not None else "fawry",
        period_start=start,
        period_end=add_month(start),
        updated_at=now,
        grace_until=None,
        card_token=sub.card_token if sub is not None else None,
        customer_profile_id=sub.customer_profile_id if sub is not None else None,
    )


def advance(sub: Subscription, now: datetime, grace: timedelta) -> Subscription:
    """Move an unpaid subscription along. Returns ``sub`` unchanged when nothing is due."""
    status = sub.status
    if status is SubscriptionStatus.CANCELED and now >= sub.period_end:
        return replace(sub, status=SubscriptionStatus.EXPIRED, updated_at=now)
    if status is SubscriptionStatus.ACTIVE and now >= sub.period_end:
        return replace(sub, status=SubscriptionStatus.PAST_DUE, updated_at=now)
    if status is SubscriptionStatus.PAST_DUE:
        return replace(sub, status=SubscriptionStatus.GRACE, grace_until=sub.period_end + grace, updated_at=now)
    if status is SubscriptionStatus.GRACE and sub.grace_until is not None and now >= sub.grace_until:
        return replace(sub, status=SubscriptionStatus.EXPIRED, updated_at=now)
    return sub
