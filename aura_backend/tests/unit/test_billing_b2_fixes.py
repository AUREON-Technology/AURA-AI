"""Billing audit fixes (stream B2): F8 downgrades survive a failure half-way, F41 an Owner transfer
stops the former Owner's card, F42 upgrades are prorated, F58 an unsigned Fawry card token is not
trusted."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.billing import (
    FakePaymentProvider,
    InMemoryBillingLocks,
    InMemoryPaymentStore,
    InMemoryStoreBindingStore,
    InMemoryStoreEventLog,
    InMemorySubscriptionStore,
)
from aura_core.billing import BillingService, StoreBillingService
from aura_core.config.settings import BillingSettings
from aura_core.domain.billing import (
    DEFAULT_PRICES,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    proration_for,
)
from aura_core.domain.tenancy import WorkspaceRole

DAY = 86400
T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.fixture
def provider():
    return FakePaymentProvider()


@pytest.fixture
def billing(container, provider, clock):
    container.plans.clock = clock
    container.tenancy.clock = clock
    container.clock = clock
    return BillingService(
        subscriptions=InMemorySubscriptionStore(), payments=InMemoryPaymentStore(), provider=provider,
        plans=container.plans, tenancy=container.tenancy, clock=clock, audit=container.audit,
        settings=BillingSettings(enabled=True), prices=DEFAULT_PRICES, locks=InMemoryBillingLocks(),
    )


def _fail_free_once(monkeypatch, container, workspace_id: str | None = None) -> dict:
    """The entitlement store refuses the first write that would move a workspace to Free."""
    store = container.plans.entitlements
    real = store.put
    state = {"armed": True, "failed": 0}

    async def put(entitlement):
        if (state["armed"] and entitlement.plan_id == "free"
                and (workspace_id is None or entitlement.workspace_id == workspace_id)):
            state["armed"] = False
            state["failed"] += 1
            raise RuntimeError("entitlement store went away")
        return await real(entitlement)

    monkeypatch.setattr(store, "put", put)
    return state


async def _plan(container, ws) -> str:
    return (await container.plans.entitlement(ws))[0].id


async def _paid_pro(billing, provider, container, user="ann", card_token=None):
    ws = (await container.tenancy.ensure_personal(user)).id
    checkout = await billing.checkout(user, ws, "pro", 1, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000, card_token=card_token))
    return ws, checkout


# -- F8: a refund / expiry that fails half-way is finished by its replay -------------------------


async def test_refund_replayed_after_a_failed_downgrade_ends_on_free(billing, provider, container, monkeypatch) -> None:
    ws, checkout = await _paid_pro(billing, provider, container)
    _fail_free_once(monkeypatch, container, ws)
    refund = provider.notification(checkout["payment_id"], "refunded", 15000)
    with pytest.raises(RuntimeError):
        await billing.handle_notification(refund)
    assert (await billing.payments.get(checkout["payment_id"])).status is PaymentStatus.REFUNDED
    await billing.handle_notification(refund)   # Fawry retries the notification it saw fail
    assert await _plan(container, ws) == "free"
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED


async def test_cycle_repairs_a_refunded_subscription_left_active(billing, provider, container, monkeypatch) -> None:
    ws, checkout = await _paid_pro(billing, provider, container)
    _fail_free_once(monkeypatch, container, ws)
    with pytest.raises(RuntimeError):
        await billing.handle_notification(provider.notification(checkout["payment_id"], "refunded", 15000))
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() - DAY)
    counts = await billing.run_cycle()   # no replay ever came: the cycle finishes it, and renews nothing
    assert counts["renewals"] == 0
    assert await _plan(container, ws) == "free"
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED


async def test_duplicate_refund_does_not_downgrade_a_later_store_subscription(billing, provider, container) -> None:
    ws, checkout = await _paid_pro(billing, provider, container)
    refund = provider.notification(checkout["payment_id"], "refunded", 15000)
    assert await billing.handle_notification(refund) == "refunded"
    sub = await billing.subscriptions.get(ws)
    # The App Store took the workspace over since (it keeps last_payment_id).
    await billing.subscriptions.put(Subscription(
        workspace_id=ws, plan_id="pro", seats=1, status=SubscriptionStatus.ACTIVE, provider="apple",
        period_start=container.clock.now(), period_end=container.clock.now() + timedelta(days=30),
        updated_at=container.clock.now(), last_payment_id=sub.last_payment_id,
    ))
    await container.plans.set_entitlement("billing", ws, "pro", 1, source="subscription")
    assert await billing.handle_notification(refund) == "duplicate"
    assert await _plan(container, ws) == "pro"


async def test_expiry_in_the_cycle_is_redriven_after_a_failed_downgrade(billing, provider, container, monkeypatch) -> None:
    ws, _ = await _paid_pro(billing, provider, container)
    await billing.cancel("ann", ws)
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() + 60)
    _fail_free_once(monkeypatch, container, ws)
    first = await billing.run_cycle()
    assert first["errors"] == 1
    await billing.run_cycle()
    assert await _plan(container, ws) == "free"
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED


async def test_one_failing_workspace_does_not_stop_the_cycle(billing, provider, container, monkeypatch) -> None:
    bad, _ = await _paid_pro(billing, provider, container, user="ann")
    good, _ = await _paid_pro(billing, provider, container, user="bob")
    for ws, user in ((bad, "ann"), (good, "bob")):
        await billing.cancel(user, ws)
    sub = await billing.subscriptions.get(good)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() + 60)
    _fail_free_once(monkeypatch, container, bad)
    counts = await billing.run_cycle()   # does not raise
    assert counts["errors"] == 1 and counts["expired"] == 1
    assert await _plan(container, good) == "free"
    await billing.run_cycle()
    assert await _plan(container, bad) == "free"


@pytest.fixture
def stores(container, clock, billing):
    class Apple:
        def __init__(self) -> None:
            self.events: dict = {}

        async def verify(self, signed):
            return self.events[signed]

    service = StoreBillingService(
        bindings=InMemoryStoreBindingStore(), events=InMemoryStoreEventLog(), subscriptions=billing.subscriptions,
        plans=container.plans, tenancy=container.tenancy, clock=clock, audit=container.audit, locks=billing.locks,
        settings=billing.settings, products={"apple": {"aura.pro": "pro"}, "google": {}}, apple=Apple(),
    )
    return service


def _apple_event(stores, key, kind, token, clock, *, days=30, revoked=False):
    from aura_core.adapters.apple_store import AppleEvent, AppleTransaction

    stores.apple.events[key] = AppleEvent(
        event_id=key, type=kind, subtype=None, bundle_id="b", environment="Sandbox", signed_at=clock.now(),
        transaction=AppleTransaction(token, "aura.pro", "orig-1", "t-1", clock.now() + timedelta(days=days), revoked),
    )
    return key


@pytest.mark.parametrize("kind,revoked", [("EXPIRED", False), ("REFUND", True)])
async def test_store_expiry_or_revoke_replayed_after_a_failed_downgrade_ends_on_free(
        stores, container, clock, monkeypatch, kind, revoked) -> None:
    ws = (await container.tenancy.ensure_personal("ann")).id
    token = await stores.issue_token("ann", ws)
    assert await stores.handle_apple(_apple_event(stores, "n1", "SUBSCRIBED", token, clock)) == "applied"
    clock.advance(31 * DAY)
    key = _apple_event(stores, "n2", kind, token, clock, days=-1 if not revoked else 30, revoked=revoked)
    _fail_free_once(monkeypatch, container, ws)
    with pytest.raises(RuntimeError):
        await stores.handle_apple(key)
    assert await stores.handle_apple(key) == "applied"   # not recorded the first time: the store retries
    assert await _plan(container, ws) == "free"
    assert (await stores.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED


# -- F41: an Owner transfer stops the former Owner's saved card ---------------------------------


async def test_owner_transfer_forgets_the_saved_card(billing, provider, container) -> None:
    container.tenancy.owner_changing = billing.forget_card
    team = await container.tenancy.create_team("own", "T")
    _inv, token = await container.tenancy.invite("own", team.id, "next@example.com", WorkspaceRole.ADMIN)
    await container.tenancy.accept("next", token)
    checkout = await billing.checkout("own", team.id, "team", 2, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 24000, card_token="own-card"))
    assert (await billing.subscriptions.get(team.id)).card_token == "own-card"
    await container.tenancy.transfer("own", team.id, "next")
    assert (await billing.subscriptions.get(team.id)).card_token is None
    assert await container.audit.query(action="billing.card_forgotten")
    sub = await billing.subscriptions.get(team.id)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() - DAY)
    await billing.run_cycle()
    assert [kind for kind, _ in provider.calls].count("card") == 0   # the new Owner pays a reference


# -- F42: an upgrade pays the difference for what is left of the period ------------------------


def _sub(plan, seats, *, start=T0, status=SubscriptionStatus.ACTIVE):
    return Subscription(workspace_id="w", plan_id=plan, seats=seats, status=status, provider="fawry",
                        period_start=start, period_end=start + timedelta(days=30), updated_at=start)


def test_proration_is_the_difference_for_the_remaining_fraction_rounded_up() -> None:
    sub = _sub("team", 3)
    half = T0 + timedelta(days=15)
    assert proration_for(DEFAULT_PRICES, sub, "team", 5, half) == 12000   # 2 seats x 12000 x 1/2
    third = T0 + timedelta(days=20)
    assert proration_for(DEFAULT_PRICES, sub, "team", 4, third) == 4000
    assert proration_for(DEFAULT_PRICES, sub, "team", 4, T0 + timedelta(days=20, seconds=1)) == 4000  # ceil
    assert proration_for(DEFAULT_PRICES, sub, "team", 2, half) == 0        # a downgrade owes nothing
    assert proration_for(DEFAULT_PRICES, sub, "team", 5, T0 + timedelta(days=31)) == 0   # period over
    assert proration_for(DEFAULT_PRICES, None, "team", 5, half) == 0
    assert proration_for(DEFAULT_PRICES, _sub("team", 3, status=SubscriptionStatus.EXPIRED), "team", 5, half) == 0
    assert proration_for(DEFAULT_PRICES, _sub("team", 3, status=SubscriptionStatus.CANCELED), "team", 5, half) == 12000
    assert proration_for(DEFAULT_PRICES, _sub("legacy", 3), "team", 5, half) == 0  # unpriced: nothing to compare


async def test_upgrade_checkout_charges_the_prorated_difference(billing, provider, container) -> None:
    team = await container.tenancy.create_team("own", "T")
    first = await billing.checkout("own", team.id, "team", 3, email=None)
    await billing.handle_notification(provider.notification(first["payment_id"], "paid", 36000))
    sub = await billing.subscriptions.get(team.id)
    container.clock.advance((sub.period_end - sub.period_start).total_seconds() / 2)
    upgrade = await billing.checkout("own", team.id, "team", 5, email=None)
    assert upgrade["proration_piastres"] == 12000
    assert upgrade["amount_piastres"] == 60000 + 12000
    assert await billing.handle_notification(provider.notification(upgrade["payment_id"], "paid", 72000)) == "applied"
    assert (await billing.subscriptions.get(team.id)).seats == 5


# -- F58: a card token Fawry's signature does not cover must be confirmed ----------------------


class _ConfirmingProvider(FakePaymentProvider):
    def __init__(self, known: dict[str, set[str]]) -> None:
        super().__init__()
        self.known = known
        self.asked: list[tuple[str, str]] = []

    async def confirm_card_token(self, customer_profile_id: str, card_token: str) -> bool:
        self.asked.append((customer_profile_id, card_token))
        return card_token in self.known.get(customer_profile_id, set())


async def test_unconfirmed_card_token_is_not_saved(container, clock) -> None:
    container.plans.clock = container.tenancy.clock = container.clock = clock
    ann = (await container.tenancy.ensure_personal("ann")).id
    bob = (await container.tenancy.ensure_personal("bob")).id
    provider = _ConfirmingProvider({ann: {"ann-card"}, bob: {"bob-card"}})
    billing = BillingService(
        subscriptions=InMemorySubscriptionStore(), payments=InMemoryPaymentStore(), provider=provider,
        plans=container.plans, tenancy=container.tenancy, clock=clock, audit=container.audit,
        settings=BillingSettings(enabled=True), prices=DEFAULT_PRICES, locks=InMemoryBillingLocks(),
    )
    checkout = await billing.checkout("ann", ann, "pro", 1, email=None)
    assert await billing.handle_notification(
        provider.notification(checkout["payment_id"], "paid", 15000, card_token="bob-card")) == "applied"
    assert (await billing.subscriptions.get(ann)).card_token is None   # paid, but no foreign card saved
    assert provider.asked == [(ann, "bob-card")]
    checkout = await billing.checkout("bob", bob, "pro", 1, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000, card_token="bob-card"))
    assert (await billing.subscriptions.get(bob)).card_token == "bob-card"   # one Fawry vouches for is kept


async def _orphan(billing, container, user="ann"):
    """A pending payment whose workspace was erased afterwards: revision 0030 keeps the payment
    row with ``workspace_id`` NULL (F34) instead of cascading it away."""
    from dataclasses import replace

    ws = (await container.tenancy.ensure_personal(user)).id
    checkout = await billing.checkout(user, ws, "pro", 1, email=None)
    payment = await billing.payments.get(checkout["payment_id"])
    await billing.payments.put(replace(payment, workspace_id=None))
    return checkout["payment_id"]


async def test_a_notification_for_an_erased_workspace_is_held_for_review(billing, provider, container) -> None:
    payment_id = await _orphan(billing, container)
    outcome = await billing.handle_notification(provider.notification(payment_id, "paid", 15000))
    assert outcome == "needs_review"
    assert (await billing.payments.get(payment_id)).status is PaymentStatus.PENDING


async def test_the_cycle_skips_payments_whose_workspace_was_erased(billing, container) -> None:
    await _orphan(billing, container)
    counts = await billing.run_cycle()
    assert counts.get("errors", 0) == 0
