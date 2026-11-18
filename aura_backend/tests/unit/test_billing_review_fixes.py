"""SaaS P1-D1 review regressions: money must never be charged twice, lost, or undone by a race."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from aura_core.adapters.billing import FakePaymentProvider, InMemoryBillingLocks, InMemoryPaymentStore, InMemorySubscriptionStore
from aura_core.billing import BillingService
from aura_core.config.settings import BillingSettings
from aura_core.domain.billing import DEFAULT_PRICES, PaymentStatus, SubscriptionStatus
from aura_core.domain.errors import DependencyUnavailableError, TimeoutError as AuraTimeout
from aura_core.ports.billing import ProviderStatus

DAY = 86400


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


async def _pro_with_card(billing, provider, container, user="ann"):
    ws = (await container.tenancy.ensure_personal(user)).id
    checkout = await billing.checkout(user, ws, "pro", 1, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000, card_token="tok"))
    return ws


async def _to_renewal(billing, container, ws, days_before=1):
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() - days_before * DAY)


async def test_card_charge_timeout_is_not_charged_again(billing, provider, container) -> None:
    """#1: the card may have been charged; the payment stays pending and is asked about, never re-charged."""
    ws = await _pro_with_card(billing, provider, container)
    await _to_renewal(billing, container, ws)
    provider.fail_with = AuraTimeout("Fawry did not answer in time")
    await billing.run_cycle()
    provider.fail_with = None
    await billing.run_cycle()
    assert [kind for kind, _ in provider.calls].count("card") == 1
    pending = [p for p in await billing.payments.pending(before=None) if p.purpose == "renewal"]
    assert len(pending) == 1 and pending[0].method == "CARD"


async def test_definitive_refusal_marks_the_payment_failed(billing, provider, container) -> None:
    ws = await _pro_with_card(billing, provider, container)
    await _to_renewal(billing, container, ws)
    provider.fail_with = DependencyUnavailableError("refused", dependency="fawry", details={"refused": True})
    await billing.run_cycle()
    failed = [p for p in await billing.payments.for_workspace(ws, limit=10) if p.purpose == "renewal"]
    assert failed and failed[0].status is PaymentStatus.FAILED


async def test_payment_during_cycle_is_not_overwritten(billing, provider, container) -> None:
    """#2: a renewal paid while the cycle is busy elsewhere must not be expired by a stale snapshot."""
    early = await _pro_with_card(billing, provider, container, "early")
    late = (await container.tenancy.ensure_personal("late")).id
    checkout = await billing.checkout("late", late, "pro", 1, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000))
    sub = await billing.subscriptions.get(late)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() + 8 * DAY)
    renewal = await billing._issue(late, "pro", 1, 15000, "renewal", email=None, card_token=None)
    gate = asyncio.Event()
    original = provider.charge_card

    async def slow_card(payment, **kwargs):
        await gate.wait()
        return await original(payment, **kwargs)

    provider.charge_card = slow_card
    cycle = asyncio.create_task(billing.run_cycle())
    for _ in range(5):
        await asyncio.sleep(0)
    assert await billing.handle_notification(provider.notification(renewal.id, "paid", 15000)) == "applied"
    gate.set()
    await cycle
    assert (await billing.subscriptions.get(late)).status is SubscriptionStatus.ACTIVE
    assert (await container.plans.entitlement(late))[0].id == "pro"
    assert early


async def test_late_paid_after_expired_is_applied_and_audited(billing, provider, container) -> None:
    """#3: cash paid at the reference's expiry boundary is money received -- never dropped."""
    ws = (await container.tenancy.ensure_personal("bo")).id
    checkout = await billing.checkout("bo", ws, "pro", 1, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "expired", 15000))
    result = await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000))
    assert result == "applied"
    assert (await container.plans.entitlement(ws))[0].id == "pro"
    assert await container.audit.query(action="billing.late_payment")


async def test_refund_after_paid_ends_the_period(billing, provider, container) -> None:
    ws = (await container.tenancy.ensure_personal("cy")).id
    checkout = await billing.checkout("cy", ws, "pro", 1, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000))
    assert await billing.handle_notification(provider.notification(checkout["payment_id"], "refunded", 15000)) == "refunded"
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED
    assert (await container.plans.entitlement(ws))[0].id == "free"
    assert await container.audit.query(action="billing.refund")


async def test_apply_failure_is_redriven_once_by_reconcile(billing, provider, container, monkeypatch) -> None:
    """#4: money received but not applied (crash, DB error) is re-applied later -- exactly once."""
    ws = (await container.tenancy.ensure_personal("di")).id
    checkout = await billing.checkout("di", ws, "pro", 1, email=None)
    original = container.plans.set_entitlement
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database went away")
        return await original(*args, **kwargs)

    monkeypatch.setattr(container.plans, "set_entitlement", flaky)
    with pytest.raises(RuntimeError):
        await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000))
    assert (await billing.payments.get(checkout["payment_id"])).status is PaymentStatus.RECEIVED
    first_end = (await billing.subscriptions.get(ws)).period_end
    await billing.run_cycle()
    await billing.run_cycle()
    assert (await billing.payments.get(checkout["payment_id"])).status is PaymentStatus.PAID
    assert (await billing.subscriptions.get(ws)).period_end == first_end      # extended once, not twice
    assert (await container.plans.entitlement(ws))[0].id == "pro"


async def test_reconcile_without_an_amount_waits(billing, provider, container) -> None:
    """#5: a status answer without an amount is not a mismatch."""
    ws = (await container.tenancy.ensure_personal("ed")).id
    checkout = await billing.checkout("ed", ws, "pro", 1, email=None)
    provider.statuses[checkout["payment_id"]] = ProviderStatus(PaymentStatus.PAID, None, "FR")
    container.clock.advance(15 * 60)
    await billing.run_cycle()
    assert (await billing.payments.get(checkout["payment_id"])).status is PaymentStatus.PENDING


async def test_concurrent_cycles_issue_one_renewal(billing, provider, container) -> None:
    """#6: the cron and the manual route at once -- one renewal, not two."""
    ws = await _pro_with_card(billing, provider, container)
    await _to_renewal(billing, container, ws)
    original = provider.charge_card

    async def yielding(payment, **kwargs):
        await asyncio.sleep(0)
        return await original(payment, **kwargs)

    provider.charge_card = yielding
    results = await asyncio.gather(billing.run_cycle(), billing.run_cycle())
    assert [kind for kind, _ in provider.calls].count("card") == 1
    assert any(r.get("skipped") for r in results)


async def test_checkout_voids_a_pending_renewal(billing, provider, container) -> None:
    """#7: a pending renewal at the old size cannot later roll an upgrade back."""
    team = await container.tenancy.create_team("own", "T")
    await container.plans.set_plan("sa", team.id, "team", 3)
    first = await billing.checkout("own", team.id, "team", 3, email=None)
    await billing.handle_notification(provider.notification(first["payment_id"], "paid", 36000))
    await _to_renewal(billing, container, team.id, days_before=2)
    await billing.run_cycle()
    stale = [p for p in await billing.payments.pending(before=None) if p.purpose == "renewal"][0]
    upgrade = await billing.checkout("own", team.id, "team", 10, email=None)
    assert (await billing.payments.get(stale.id)).status is PaymentStatus.FAILED
    await billing.handle_notification(provider.notification(upgrade["payment_id"], "paid", upgrade["amount_piastres"]))
    assert await billing.handle_notification(provider.notification(stale.id, "paid", 36000)) == "needs_review"
    assert (await billing.subscriptions.get(team.id)).seats == 10


async def test_expiry_leaves_a_manual_plan_alone(billing, provider, container) -> None:
    """#8: an operator's later hand-set plan survives the subscription's expiry."""
    ws = (await container.tenancy.ensure_personal("fi")).id
    checkout = await billing.checkout("fi", ws, "pro", 1, email=None)
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000))
    await billing.cancel("fi", ws)
    await container.plans.set_plan("sa", ws, "pro", 1)
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() + 60)
    await billing.run_cycle()
    assert (await billing.subscriptions.get(ws)).status is SubscriptionStatus.EXPIRED
    assert (await container.plans.entitlement(ws))[0].id == "pro"


async def test_fake_provider_secret_is_not_a_public_constant() -> None:
    assert FakePaymentProvider()._secret != FakePaymentProvider()._secret


def test_fake_provider_is_refused_outside_a_local_run() -> None:
    from aura_core.config.settings import AuraSettings, Environment
    from aura_core.container import Container
    from aura_core.domain.errors import ConfigurationError

    settings = AuraSettings.from_env()
    settings = replace(settings, environment=Environment.DEVELOPMENT, billing=BillingSettings(enabled=True))
    with pytest.raises(ConfigurationError):
        Container(settings)
