"""SaaS P1-D1: BillingService -- checkout, notifications, cancel, and the renewal cycle."""

from __future__ import annotations

from datetime import timedelta

import pytest

from aura_core.adapters.billing import FakePaymentProvider, InMemoryBillingLocks, InMemoryPaymentStore, InMemorySubscriptionStore
from aura_core.billing import BillingService
from aura_core.config.settings import BillingSettings
from aura_core.domain.billing import DEFAULT_PRICES, PaymentStatus, SubscriptionStatus
from aura_core.domain.errors import AuthenticationError, AuthorizationError, DependencyUnavailableError, ValidationError
from aura_core.domain.tenancy import WorkspaceRole


@pytest.fixture
def provider():
    return FakePaymentProvider()


@pytest.fixture
def billing(container, provider, clock):
    # One frozen clock for billing, plans and tenancy, so the cycle can be walked through time.
    container.plans.clock = clock
    container.tenancy.clock = clock
    container.clock = clock
    return BillingService(
        subscriptions=InMemorySubscriptionStore(), payments=InMemoryPaymentStore(), provider=provider,
        plans=container.plans, tenancy=container.tenancy, clock=clock, audit=container.audit,
        settings=BillingSettings(enabled=True), prices=DEFAULT_PRICES, locks=InMemoryBillingLocks(),
    )


async def _personal(container, user="ann"):
    return (await container.tenancy.ensure_personal(user)).id


async def _paid_pro(billing, provider, container, user="ann"):
    ws = await _personal(container, user)
    checkout = await billing.checkout(user, ws, "pro", 1, email=f"{user}@example.com")
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000))
    return ws, checkout


async def test_checkout_issues_a_reference(billing, container, provider) -> None:
    ws = await _personal(container)
    checkout = await billing.checkout("ann", ws, "pro", 1, email="ann@example.com")
    assert checkout["amount_piastres"] == 15000 and checkout["reference_number"]
    assert checkout["expires_at"] and provider.calls == [("reference", checkout["payment_id"])]


async def test_only_the_owner_checks_out_and_plans_must_fit(billing, container) -> None:
    team = await container.tenancy.create_team("own", "T")
    _invite, token = await container.tenancy.invite("own", team.id, "m@example.com", WorkspaceRole.ADMIN)
    await container.tenancy.accept("m", token)
    with pytest.raises(AuthorizationError):
        await billing.checkout("m", team.id, "team", 2, email=None)
    with pytest.raises(AuthorizationError):
        await billing.checkout("stranger", team.id, "team", 2, email=None)
    with pytest.raises(ValidationError):
        await billing.checkout("own", team.id, "pro", 1, email=None)        # pro is personal-only
    with pytest.raises(ValidationError):
        await billing.checkout("own", team.id, "team", 1, email=None)       # below min seats
    assert (await billing.checkout("own", team.id, "team", 3, email=None))["amount_piastres"] == 36000


async def test_paid_notification_activates_and_sets_the_plan(billing, provider, container) -> None:
    ws, _ = await _paid_pro(billing, provider, container)
    status = await billing.status(ws)
    assert status["subscription"]["status"] == "active" and status["subscription"]["plan"] == "pro"
    plan, _seats = await container.plans.entitlement(ws)
    assert plan.id == "pro"
    entitlement = await container.plans.entitlements.get(ws)
    assert entitlement.source == "subscription"


async def test_duplicate_notification_extends_once(billing, provider, container) -> None:
    ws, checkout = await _paid_pro(billing, provider, container)
    first = (await billing.subscriptions.get(ws)).period_end
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000))
    assert (await billing.subscriptions.get(ws)).period_end == first


async def test_stale_status_after_paid_changes_nothing(billing, provider, container) -> None:
    ws, checkout = await _paid_pro(billing, provider, container)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "expired", 15000))
    assert (await billing.payments.get(checkout["payment_id"])).status is PaymentStatus.PAID
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.ACTIVE


async def test_amount_mismatch_activates_nothing(billing, provider, container) -> None:
    ws = await _personal(container)
    checkout = await billing.checkout("ann", ws, "pro", 1, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 100))
    assert (await billing.payments.get(checkout["payment_id"])).status is PaymentStatus.MISMATCH
    assert await billing.subscriptions.get(ws) is None
    assert (await container.plans.entitlement(ws))[0].id == "free"
    assert await container.audit.query(action="billing.mismatch")


async def test_bad_signature_changes_nothing(billing, provider, container) -> None:
    ws = await _personal(container)
    checkout = await billing.checkout("ann", ws, "pro", 1, email=None)
    with pytest.raises(AuthenticationError):
        await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000, sign=False))
    assert (await billing.payments.get(checkout["payment_id"])).status is PaymentStatus.PENDING


async def test_unknown_payment_is_ignored(billing, provider) -> None:
    assert await billing.handle_notification(provider.notification("no-such-payment", "paid", 15000)) == "ignored"


async def test_cancel_runs_to_period_end_then_free_without_renewal(billing, provider, container) -> None:
    ws, _ = await _paid_pro(billing, provider, container)
    await billing.cancel("ann", ws)
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() - 86400)
    await billing.run_cycle()
    assert provider.calls.count(("reference", provider.calls[0][1])) == 1 and len(provider.calls) == 1
    assert (await container.plans.entitlement(ws))[0].id == "pro"
    container.clock.advance(2 * 86400)
    await billing.run_cycle()
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED
    assert (await container.plans.entitlement(ws))[0].id == "free"


async def test_cycle_issues_a_renewal_inside_the_lead(billing, provider, container) -> None:
    ws, _ = await _paid_pro(billing, provider, container)
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() - 2 * 86400)
    counts = await billing.run_cycle()
    assert counts["renewals"] == 1 and provider.calls[-1][0] == "reference"
    assert (await billing.run_cycle())["renewals"] == 0   # not twice


async def test_renewal_charges_a_saved_card(billing, provider, container) -> None:
    ws = await _personal(container)
    checkout = await billing.checkout("ann", ws, "pro", 1, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000, card_token="tok-9"))
    sub = await billing.subscriptions.get(ws)
    assert sub.card_token == "tok-9"
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() - 86400)
    await billing.run_cycle()
    assert provider.calls[-1][0] == "card"
    assert "tok-9" not in repr(await billing.status(ws))


async def test_unpaid_goes_grace_then_free(billing, provider, container) -> None:
    ws, _ = await _paid_pro(billing, provider, container)
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() + 60)
    await billing.run_cycle()
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.GRACE
    assert (await container.plans.entitlement(ws))[0].id == "pro"   # full access in grace
    container.clock.advance(7 * 86400)
    await billing.run_cycle()
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED
    assert (await container.plans.entitlement(ws))[0].id == "free"


async def test_provider_down_during_cycle_keeps_one_renewal_to_ask_about(billing, provider, container) -> None:
    """No answer is not a refusal: the renewal may exist at Fawry, so it stays pending (and is asked
    about by reconcile) rather than being issued again (review D1#1)."""
    ws, _ = await _paid_pro(billing, provider, container)
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() - 86400)
    provider.fail_with = DependencyUnavailableError("fawry down", dependency="fawry")
    counts = await billing.run_cycle()
    assert counts["renewals"] == 0 and counts["errors"] >= 1
    assert len([p for p in await billing.payments.pending(before=None) if p.purpose == "renewal"]) == 1
    provider.fail_with = None
    assert (await billing.run_cycle())["renewals"] == 0
    assert len([p for p in await billing.payments.pending(before=None) if p.purpose == "renewal"]) == 1


async def test_reconcile_applies_a_payment_whose_notification_was_lost(billing, provider, container) -> None:
    from aura_core.ports.billing import ProviderStatus

    ws = await _personal(container)
    checkout = await billing.checkout("ann", ws, "pro", 1, email=None)
    provider.statuses[checkout["payment_id"]] = ProviderStatus(PaymentStatus.PAID, 15000, "FR-9")
    container.clock.advance(15 * 60)
    counts = await billing.run_cycle()
    assert counts["reconciled"] == 1
    assert (await container.plans.entitlement(ws))[0].id == "pro"


# -- SaaS P1-F2b: what the app needs to sell in the stores --------------------------------


async def test_status_names_the_provider(billing, provider, container) -> None:
    ws, _ = await _paid_pro(billing, provider, container)
    assert (await billing.status(ws))["subscription"]["provider"] == "fawry"


async def test_cancel_refuses_a_store_subscription(billing, container, clock) -> None:
    from aura_core.domain.billing import Subscription
    from aura_core.domain.errors import ConflictError

    ws = await _personal(container)
    now = clock.now()
    sub = Subscription(workspace_id=ws, plan_id="pro", seats=1, status=SubscriptionStatus.ACTIVE, provider="apple",
                       period_start=now, period_end=now + timedelta(days=30), updated_at=now)
    await billing.subscriptions.put(sub)
    with pytest.raises(ConflictError, match="App Store or Google Play"):
        await billing.cancel("ann", ws)
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.ACTIVE
