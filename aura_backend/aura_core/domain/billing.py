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

from .errors import AuraError, ConfigurationError, ValidationError

__all__ = [
    "SubscriptionStatus",
    "Subscription",
    "PaymentStatus",
    "Payment",
    "DEFAULT_PRICES",
    "load_prices",
    "price_for",
    "proration_for",
    "add_month",
    "apply_payment",
    "advance",
    "BillingUnavailableError",
    "StoreBinding",
    "STORE_PROVIDERS",
    "load_store_products",
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
    #: The payment applied last: re-applying it is a no-op, so a payment whose application was
    #: interrupted can be safely re-driven (review D1#4).
    last_payment_id: str | None = None
    #: Fawry card token for renewals, when the payer saved a card. Never returned by any route.
    card_token: str | None = None
    customer_profile_id: str | None = None


class PaymentStatus(str, Enum):
    PENDING = "pending"
    #: Money received, not yet applied to the subscription; the cycle re-drives it (review D1#4).
    RECEIVED = "received"
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
    #: ``None`` once the workspace was erased: the ledger row outlives it (revision 0030, F34).
    workspace_id: str | None
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


#: Statuses whose paid period still runs: an upgrade inside it owes the difference (F42).
_PRORATED = frozenset({SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELED})


def proration_for(prices: Mapping[str, int], sub: Subscription | None, plan_id: str, seats: int,
                  now: datetime) -> int:
    """What an upgrade bought at ``now`` owes for the rest of the period already paid for.

    A payment bought early starts its month when the current one ends (``apply_payment``), but the
    new plan or seats take effect at once -- so without this, the rest of the current period at the
    higher price was free (F42). It is the price difference times the fraction of the period left,
    in whole piastres rounded up. Nothing for a downgrade, for a period that is over or not paid
    (past due, grace, expired), or when the current plan has no price to compare against.
    """
    if sub is None or sub.status not in _PRORATED or sub.plan_id not in prices or now >= sub.period_end:
        return 0
    difference = price_for(prices, plan_id, seats) - price_for(prices, sub.plan_id, sub.seats)
    total = (sub.period_end - sub.period_start) // timedelta(microseconds=1)
    if difference <= 0 or total <= 0:
        return 0
    left = (sub.period_end - max(now, sub.period_start)) // timedelta(microseconds=1)
    return -(-difference * left // total)


def add_month(at: datetime) -> datetime:
    year, month = (at.year + 1, 1) if at.month == 12 else (at.year, at.month + 1)
    return at.replace(year=year, month=month, day=min(at.day, calendar.monthrange(year, month)[1]))


def apply_payment(sub: Subscription | None, payment: Payment, now: datetime) -> Subscription:
    """A paid payment: activate, or extend. Paid early, the new period follows the current one;
    paid after the period ended (past due or in grace), it starts now. The caller applies each
    payment once (``PaymentStore.mark`` from pending is the lock)."""
    if sub is not None and sub.last_payment_id == payment.id:
        return sub
    if sub is None or sub.status in (SubscriptionStatus.EXPIRED, SubscriptionStatus.PENDING) or sub.period_end <= now:
        start = now
    else:
        start = sub.period_end
    return Subscription(
        workspace_id=payment.workspace_id,
        plan_id=payment.plan_id,
        seats=payment.seats,
        status=SubscriptionStatus.ACTIVE,
        # A Fawry payment makes this a Fawry subscription, even one that was a store's: otherwise
        # the cycle would never issue its renewals (it skips store-renewed subscriptions).
        provider="fawry",
        period_start=start,
        period_end=add_month(start),
        updated_at=now,
        grace_until=None,
        last_payment_id=payment.id,
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


class BillingUnavailableError(AuraError):
    """Billing is turned off on this deployment (``AURA_BILLING_ENABLED``)."""

    kind = "billing_unavailable"
    http_status = 503


#: The app stores AURA accepts subscriptions from (SaaS P1-D2).
STORE_PROVIDERS = ("apple", "google")


@dataclass(frozen=True, slots=True)
class StoreBinding:
    """A token AURA issued before an in-app purchase. The app passes it to the store (Apple's
    ``appAccountToken``, Google's ``obfuscatedExternalAccountId``) and every store event names it
    -- the only way a store event is tied to a workspace."""

    token: str
    workspace_id: str
    created_by: str
    created_at: datetime


def load_store_products(raw_json: str, catalogue: Mapping[str, object]) -> dict[str, dict[str, str]]:
    """``AURA_STORE_PRODUCTS``: ``{"apple": {productId: plan}, "google": {productId: plan}}``.
    Only plans for personal workspaces may be sold in the apps (Team is sold on the web)."""
    if not raw_json.strip():
        return {provider: {} for provider in STORE_PROVIDERS}
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("AURA_STORE_PRODUCTS is not valid JSON") from exc
    if not isinstance(data, dict) or set(data) - set(STORE_PROVIDERS):
        raise ConfigurationError(f"AURA_STORE_PRODUCTS keys must be among {STORE_PROVIDERS}")
    products: dict[str, dict[str, str]] = {provider: {} for provider in STORE_PROVIDERS}
    for provider, mapping in data.items():
        if not isinstance(mapping, dict):
            raise ConfigurationError(f"AURA_STORE_PRODUCTS[{provider!r}] must map product ids to plans")
        for product_id, plan_id in mapping.items():
            plan = catalogue.get(plan_id)
            if plan is None:
                raise ConfigurationError(f"store product {product_id!r} names unknown plan {plan_id!r}")
            kinds = {getattr(k, "value", k) for k in getattr(plan, "workspace_kinds", ())}
            if kinds != {"personal"}:
                raise ConfigurationError(f"store product {product_id!r}: only personal plans are sold in the apps")
            products[provider][str(product_id)] = str(plan_id)
    return products
