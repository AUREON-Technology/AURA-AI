"""SaaS P1-F1: the email customers get -- invites, receipts, renewal reminders, past-due notices."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aura_core.adapters.billing import (
    FakePaymentProvider,
    InMemoryBillingContactStore,
    InMemoryBillingLocks,
    InMemoryPaymentStore,
    InMemorySubscriptionStore,
)
from aura_core.adapters.email import InMemoryEmailSender
from aura_core.billing import BillingService
from aura_core.config.settings import BillingSettings, EmailSettings
from aura_core.domain.billing import DEFAULT_PRICES
from aura_core.domain.tenancy import WorkspaceRole
from aura_core.notifications import Notifier

DAY = 86400


class _Broken:
    async def send(self, message):
        raise OSError("smtp down")


@pytest.fixture
def outbox():
    return InMemoryEmailSender()


@pytest.fixture
def env(container, clock, outbox):
    container.plans.clock = container.tenancy.clock = container.clock = clock
    notifier = Notifier(sender=outbox, settings=EmailSettings(enabled=True, app_base_url="https://app.example"),
                        audit=container.audit, clock=clock)
    container.tenancy.notifier = notifier
    provider = FakePaymentProvider()
    billing = BillingService(
        subscriptions=InMemorySubscriptionStore(), payments=InMemoryPaymentStore(), provider=provider,
        plans=container.plans, tenancy=container.tenancy, clock=clock, audit=container.audit,
        settings=BillingSettings(enabled=True), prices=DEFAULT_PRICES, locks=InMemoryBillingLocks(),
        contacts=InMemoryBillingContactStore(), notifier=notifier,
    )
    return container, billing, provider, notifier


async def _paid(billing, provider, container, user="ann", card=None):
    ws = (await container.tenancy.ensure_personal(user)).id
    checkout = await billing.checkout(user, ws, "pro", 1, email=f"{user}@example.com")
    await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000, card_token=card))
    return ws


async def test_invite_emails_the_invitee(env, outbox) -> None:
    container, *_ = env
    team = await container.tenancy.create_team("own", "Acme")
    _invite, token = await container.tenancy.invite("own", team.id, "bea@example.com", WorkspaceRole.MEMBER,
                                                    inviter_email="own@example.com")
    [message] = outbox.outbox
    assert message.to == "bea@example.com" and token in message.text and "own@example.com" in message.text


async def test_smtp_down_does_not_fail_the_invite_or_the_payment(env) -> None:
    container, billing, provider, notifier = env
    notifier.sender = _Broken()
    team = await container.tenancy.create_team("own", "Acme")
    _invite, token = await container.tenancy.invite("own", team.id, "bea@example.com", WorkspaceRole.MEMBER)
    assert token
    ws = await _paid(billing, provider, container)
    assert (await billing.subscriptions.get(ws)).plan_id == "pro"
    assert sum(e.action == "email.failed" for e in await container.audit.query(limit=200)) == 2


async def test_paid_checkout_emails_a_receipt_to_the_contact(env, outbox) -> None:
    container, billing, provider, _ = env
    await _paid(billing, provider, container)
    receipts = [m for m in outbox.outbox if m.tag == "receipt"]
    assert len(receipts) == 1 and receipts[0].to == "ann@example.com" and "EGP 150.00" in receipts[0].text


async def test_reference_renewal_emails_the_code_and_card_renewal_does_not(env, outbox) -> None:
    container, billing, provider, _ = env
    ref_ws = await _paid(billing, provider, container, "ann")
    await _paid(billing, provider, container, "cal", card="tok")
    sub = await billing.subscriptions.get(ref_ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() - DAY)
    assert (await billing.run_cycle())["renewals"] == 2
    renewals = [m for m in outbox.outbox if m.tag == "renewal_due"]
    assert [m.to for m in renewals] == ["ann@example.com"]


async def test_past_due_emails_once(env, outbox) -> None:
    container, billing, provider, _ = env
    ws = await _paid(billing, provider, container)
    sub = await billing.subscriptions.get(ws)
    container.clock.advance((sub.period_end - container.clock.now()).total_seconds() + 3600)
    await billing.run_cycle()
    container.clock.advance(3600)
    await billing.run_cycle()
    assert [m.to for m in outbox.outbox if m.tag == "past_due"] == ["ann@example.com"]


async def test_no_contact_no_email_and_no_failure(env, outbox) -> None:
    container, billing, provider, _ = env
    ws = (await container.tenancy.ensure_personal("dan")).id
    checkout = await billing.checkout("dan", ws, "pro", 1, email=None)
    assert await billing.handle_notification(provider.notification(checkout["payment_id"], "paid", 15000)) == "applied"
    assert [m for m in outbox.outbox if m.tag == "receipt"] == []
